"""내보내기 파일 하나를 장부에 반영한다. 호출한 쪽이 트랜잭션을 커밋한다.

처리 순서: 중복 확인, 복호화, 파싱, 합계 대조, 차이 계산, 반영, 재분류, 자산 스냅샷.
문제가 있는 파일은 장부에 손대지 않고 격리 기록만 남긴다.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.ingest.archive import ExportRejected, WrongPassword, open_export
from app.ingest.parser import PARSER_VERSION, FormatChanged, ParsedExport, SourceRow, parse_workbook
from app.ingest.reconcile import (
    Diff,
    ReconciliationFailed,
    StoredTxn,
    SuspiciousDisappearance,
    compute_diff,
    verify_summary,
)
from app.ledger.classify import infer_instrument_kind
from app.ledger.models import (
    AssetDetail,
    AssetItem,
    AssetSnapshot,
    ImportBatch,
    Instrument,
    RawRow,
    Txn,
    TxnSplit,
)
from app.ledger.reclassify import reclassify

QUARANTINE_RETENTION = timedelta(days=30)
MAX_QUARANTINED_FILES = 20


@dataclass(frozen=True)
class ImportResult:
    status: str  # applied, duplicate, quarantined
    batch_id: int | None
    reason: str | None = None
    counts: dict[str, int] = field(default_factory=dict)


def _quarantine(
    session: Session,
    data: bytes,
    sha: str,
    *,
    source: str,
    message_id: str | None,
    reason: str,
    keep_file: bool,
    summary: dict | None = None,
) -> ImportResult:
    # 같은 파일을 반복해서 받아도 행이 늘지 않게 기존 격리 기록을 갱신한다
    existing = session.scalar(
        select(ImportBatch).where(ImportBatch.status == "quarantined", ImportBatch.file_sha256 == sha).limit(1)
    )
    if existing is not None:
        existing.reason = reason
        existing.summary = summary or {}
        session.flush()
        return ImportResult("quarantined", existing.id, reason)

    # 무료 DB를 격리 파일로 채우지 못하게 보관 개수에 상한을 둔다. 기록은 남긴다.
    if keep_file:
        stored = session.scalar(
            select(func.count()).select_from(ImportBatch).where(ImportBatch.raw_zip.is_not(None))
        )
        if stored >= MAX_QUARANTINED_FILES:
            keep_file = False
            summary = {**(summary or {}), "file_dropped": True}

    batch = ImportBatch(
        source=source,
        message_id=message_id,
        file_sha256=sha,
        status="quarantined",
        reason=reason,
        summary=summary or {},
        raw_zip=data if keep_file else None,
        parser_version=PARSER_VERSION,
    )
    session.add(batch)
    session.flush()
    return ImportResult("quarantined", batch.id, reason)


def _load_stored(session: Session) -> list[StoredTxn]:
    touched = set(
        session.scalars(
            select(TxnSplit.txn_id).where(
                or_(TxnSplit.nature_source == "user", TxnSplit.category_source == "user")
            )
        )
    )
    rows = session.execute(select(Txn, Instrument.source_name).join(Instrument, Txn.instrument_id == Instrument.id))
    return [
        StoredTxn(
            id=txn.id,
            key_base=txn.key_base,
            occurrence=txn.occurrence,
            occurred_at=txn.occurred_at,
            type=txn.source_type,
            major=txn.source_major,
            minor=txn.source_minor,
            description=txn.description,
            amount=txn.source_amount,
            currency=txn.currency,
            instrument=name,
            memo=txn.memo,
            status=txn.status,
            user_touched=txn.id in touched,
        )
        for txn, name in rows
    ]


def _sync_instruments(session: Session, parsed: ParsedExport) -> dict[str, Instrument]:
    existing = {i.source_name: i for i in session.scalars(select(Instrument))}
    groups = {line.product: line.group for line in parsed.assets if line.side == "asset"}

    types: dict[str, set[str]] = defaultdict(set)
    dates: dict[str, list[date]] = defaultdict(list)
    for row in parsed.rows:
        types[row.instrument].add(row.type)
        dates[row.instrument].append(row.occurred_at.date())

    for name, used_types in types.items():
        instrument = existing.get(name)
        kind = infer_instrument_kind(name, groups.get(name), used_types)
        if instrument is None:
            instrument = Instrument(source_name=name, display_name=name, kind=kind)
            session.add(instrument)
            existing[name] = instrument
        elif instrument.kind_source == "auto" and kind != "unknown":
            instrument.kind = kind
        low, high = min(dates[name]), max(dates[name])
        instrument.first_seen = min(instrument.first_seen or low, low)
        instrument.last_seen = max(instrument.last_seen or high, high)
    session.flush()
    return existing


def _payload(row: SourceRow) -> dict:
    return {
        "occurred_at": row.occurred_at.isoformat(timespec="seconds"),
        "type": row.type,
        "major": row.major,
        "minor": row.minor,
        "description": row.description,
        "amount": format(row.amount.normalize(), "f"),
        "currency": row.currency,
        "instrument": row.instrument,
        "memo": row.memo,
    }


def _apply_diff(session: Session, diff: Diff, batch: ImportBatch, instruments: dict[str, Instrument]) -> None:
    for item in diff.new:
        row = item.row
        txn = Txn(
            key_base=item.key_base,
            occurrence=item.occurrence,
            occurred_at=row.occurred_at,
            occurred_date=row.occurred_at.date(),
            source_type=row.type,
            source_major=row.major,
            source_minor=row.minor,
            description=row.description,
            source_amount=row.amount,
            currency=row.currency,
            instrument_id=instruments[row.instrument].id,
            memo=row.memo,
            status="active",
            first_batch_id=batch.id,
            last_seen_batch_id=batch.id,
        )
        txn.splits.append(TxnSplit(seq=1, amount=int(row.amount) if row.currency == "KRW" else 0))
        session.add(txn)
        session.flush()
        session.add(RawRow(batch_id=batch.id, txn_id=txn.id, row_number=row.row_number, payload=_payload(row)))

    if diff.is_stale:
        session.flush()
        return

    ids = [m.stored.id for m in diff.matched]
    txns = {t.id: t for t in session.scalars(select(Txn).where(Txn.id.in_(ids)))} if ids else {}
    for match in diff.matched:
        txn = txns[match.stored.id]
        row = match.row
        txn.last_seen_batch_id = batch.id
        txn.status = "active"
        txn.missing_since_batch_id = None
        if match.changed:
            txn.key_base = match.new_key_base
            txn.occurrence = match.new_occurrence
            txn.occurred_at = row.occurred_at
            txn.occurred_date = row.occurred_at.date()
            txn.source_type = row.type
            txn.source_major = row.major
            txn.source_minor = row.minor
            txn.description = row.description
            txn.memo = row.memo
            session.add(RawRow(batch_id=batch.id, txn_id=txn.id, row_number=row.row_number, payload=_payload(row)))

    gone = [item.id for item in diff.missing]
    kept = [item.id for item in diff.missing_kept]
    for txn in session.scalars(select(Txn).where(Txn.id.in_(gone + kept))):
        txn.missing_since_batch_id = batch.id
        if txn.id in gone:
            txn.status = "missing"
    session.flush()


def _write_snapshot(
    session: Session,
    parsed: ParsedExport,
    batch: ImportBatch,
    as_of: date,
    instruments: dict[str, Instrument],
) -> None:
    session.execute(delete(AssetSnapshot).where(AssetSnapshot.as_of == as_of))
    snapshot = AssetSnapshot(
        batch_id=batch.id,
        as_of=as_of,
        total_assets=sum(line.amount for line in parsed.assets if line.side == "asset"),
        total_liabilities=sum(line.amount for line in parsed.assets if line.side == "liability"),
    )
    for line in parsed.assets:
        instrument = instruments.get(line.product)
        snapshot.items.append(
            AssetItem(
                side=line.side,
                group_label=line.group,
                product_name=line.product,
                amount=line.amount,
                instrument_id=instrument.id if instrument else None,
            )
        )
    for detail in parsed.details:
        snapshot.details.append(AssetDetail(section=detail.section, fields=detail.fields))
    session.add(snapshot)
    session.flush()


def reprocess_batch(
    session: Session, batch: ImportBatch, passwords: Sequence[str], *, today: date | None = None
) -> bool:
    """격리된 파일을 다시 처리한다. 반영되면 True. 다시 실패하면 같은 격리 행의 사유가 갱신된다."""
    result = import_export(
        session, batch.raw_zip, passwords, source=batch.source, message_id=batch.message_id, today=today
    )
    if result.status in {"applied", "duplicate"}:
        batch.status = "resolved"
        batch.summary = {**batch.summary, "resolved_by": result.batch_id}
        batch.raw_zip = None
        session.flush()
        return True
    return False


def dismiss_batch(session: Session, batch: ImportBatch) -> None:
    """처리할 수 없는 격리 기록을 사용자가 닫는다. 경고가 영구히 남지 않게 하기 위해서다."""
    batch.status = "resolved"
    batch.summary = {**batch.summary, "dismissed": True}
    batch.raw_zip = None
    session.flush()


def reprocess_wrong_password(session: Session, passwords: Sequence[str], *, today: date | None = None) -> int:
    """비밀번호를 저장한 뒤 대기 중이던 파일을 처리한다. 오래된 것부터 반영한다."""
    waiting = session.scalars(
        select(ImportBatch)
        .where(
            ImportBatch.status == "quarantined",
            ImportBatch.reason == "wrong_password",
            ImportBatch.raw_zip.is_not(None),
        )
        .order_by(ImportBatch.id)
    ).all()
    return sum(reprocess_batch(session, batch, passwords, today=today) for batch in waiting)


def purge_expired_quarantine(session: Session, now: datetime) -> int:
    """격리 파일은 약한 암호로만 보호되므로 기간이 지나면 지운다. 기록은 남긴다."""
    expired = session.scalars(
        select(ImportBatch).where(
            ImportBatch.raw_zip.is_not(None),
            ImportBatch.created_at < now - QUARANTINE_RETENTION,
        )
    ).all()
    for batch in expired:
        batch.raw_zip = None
    session.flush()
    return len(expired)


def import_export(
    session: Session,
    data: bytes,
    passwords: Sequence[str],
    *,
    source: str = "cli",
    message_id: str | None = None,
    today: date | None = None,
) -> ImportResult:
    sha = hashlib.sha256(data).hexdigest()
    duplicate_filter = [ImportBatch.file_sha256 == sha]
    if message_id:
        duplicate_filter.append(ImportBatch.message_id == message_id)
    duplicate = session.scalar(
        select(ImportBatch).where(ImportBatch.status == "applied", or_(*duplicate_filter)).limit(1)
    )
    if duplicate is not None:
        return ImportResult("duplicate", duplicate.id)

    context = {"source": source, "message_id": message_id}
    try:
        opened = open_export(data, passwords)
    except ExportRejected as exc:
        return _quarantine(session, data, sha, reason=f"rejected:{exc.code}", keep_file=False, **context)
    except WrongPassword:
        return _quarantine(session, data, sha, reason="wrong_password", keep_file=True, **context)

    try:
        parsed = parse_workbook(opened.xlsx)
        check = verify_summary(parsed)
    except FormatChanged as exc:
        return _quarantine(
            session, data, sha, reason="format_changed", keep_file=True, summary={"detail": str(exc)}, **context
        )
    except ReconciliationFailed as exc:
        return _quarantine(
            session,
            data,
            sha,
            reason="reconciliation_failed",
            keep_file=True,
            summary={"mismatched_cells": len(exc.mismatches)},
            **context,
        )

    if not parsed.rows:
        return _quarantine(session, data, sha, reason="format_changed", keep_file=True, **context)
    dates = [row.occurred_at.date() for row in parsed.rows]
    period = opened.period or (min(dates), max(dates))
    # 먼 미래 날짜를 단 파일 하나로 이후 파일이 전부 '오래된 파일'이 되는 일을 막는다. KST와 UTC 차이로 하루는 허용한다.
    latest_allowed = (today or date.today()) + timedelta(days=1)
    if period[1] < max(dates) or period[1] > latest_allowed:
        return _quarantine(
            session,
            data,
            sha,
            reason="format_changed",
            keep_file=True,
            summary={"detail": "파일 기간이 내역과 맞지 않습니다."},
            **context,
        )
    latest = session.scalar(select(func.max(ImportBatch.period_end)).where(ImportBatch.status == "applied"))
    is_stale = latest is not None and period[1] < latest

    try:
        diff = compute_diff(_load_stored(session), parsed.rows, period, is_stale=is_stale)
    except SuspiciousDisappearance as exc:
        return _quarantine(
            session,
            data,
            sha,
            reason="suspicious_disappearance",
            keep_file=True,
            summary={"detail": str(exc)},
            **context,
        )

    counts = {
        "rows": len(parsed.rows),
        "new": len(diff.new),
        "updated": len(diff.updated),
        "restored": len(diff.restored),
        "missing": len(diff.missing),
        "missing_kept": len(diff.missing_kept),
        "summary_cells_checked": check.cells_checked,
        "summary_cells_skipped": check.cells_skipped,
    }
    batch = ImportBatch(
        source=source,
        message_id=message_id,
        file_sha256=sha,
        period_start=period[0],
        period_end=period[1],
        status="applied",
        summary=counts,
        parser_version=PARSER_VERSION,
    )
    session.add(batch)
    session.flush()

    instruments = _sync_instruments(session, parsed)
    _apply_diff(session, diff, batch, instruments)
    _write_snapshot(session, parsed, batch, period[1], instruments)
    reclassify(session)  # 대출 납입의 이자는 방금 들어온 잔액으로 다시 구한다
    return ImportResult("applied", batch.id, counts=counts)
