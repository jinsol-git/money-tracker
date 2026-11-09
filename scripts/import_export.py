#!/usr/bin/env python3
"""내보내기 ZIP을 로컬 장부에 가져오고 요약을 출력한다.

    ~/.local/bin/uv run python scripts/import_export.py <ZIP 경로>

DATABASE_URL이 없으면 private/pgdata의 임베디드 PostgreSQL을 쓴다(개발 전용).
기본 출력은 건수와 비율뿐이다. 금액까지 보려면 --amounts를 붙인다.
비밀번호는 터미널에서 입력받는다.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import make_engine  # noqa: E402
from app.ingest.importer import import_export  # noqa: E402
from app.ledger import reports  # noqa: E402
from app.ledger.models import Instrument  # noqa: E402


def local_database_url() -> str:
    import pgserver

    data_dir = PROJECT_ROOT / "private" / "pgdata"
    data_dir.mkdir(parents=True, exist_ok=True)
    data_dir.parent.chmod(0o700)
    return pgserver.get_server(data_dir, cleanup_mode="stop").get_uri()


def percent(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "-"


def print_report(session: Session, show_amounts: bool) -> None:
    months = reports.monthly_summary(session)
    spend = sum(m.spend_total for m in months)

    def money(value: int) -> str:
        return f"{value:,}원" if show_amounts else percent(value, spend)

    basis = "금액" if show_amounts else "실지출 대비 비율"
    print(f"\n[기간 합계, {basis}]")
    income = sum(m.income for m in months)
    print(f"  수입: {money(income)}")
    print(f"  실지출: {money(spend)}")
    print(f"    카드: {money(sum(m.spend_card for m in months))}")
    print(f"    계좌: {money(sum(m.spend_account for m in months))}")
    print(f"    페이: {money(sum(m.spend_pay for m in months))}")
    print(f"    기타: {money(sum(m.spend_other for m in months))}")
    print(f"  미확정 입금: {money(sum(m.undetermined_in for m in months))}")
    print(f"  미확정 출금: {money(sum(m.undetermined_out for m in months))}")

    print(f"\n[비목별 실지출, {basis}]")
    for line in reports.category_breakdown(session):
        print(f"  {line.category_name}: {money(line.amount)} ({line.rows}건)")

    print(f"\n[월별 실지출, {basis}]")
    for month in months:
        print(f"  {month.month}: {money(month.spend_total)}")

    print("\n[계좌 추적: 출금이 많은 결제수단]")
    instruments = session.query(Instrument).filter(Instrument.kind.in_(("account", "savings"))).all()
    traces = [(i, reports.account_trace(session, i.id)) for i in instruments]
    for instrument, trace in sorted(traces, key=lambda pair: -pair[1].total_out)[:5]:
        if not trace.total_out:
            continue
        rate = "-" if trace.trace_rate is None else f"{trace.trace_rate * 100:.1f}%"
        print(f"  {instrument.display_name}: 추적률 {rate}, 미추적 {trace.untracked_rows}건")

    print("\n[결제수단 종류]")
    for instrument in session.query(Instrument).order_by(Instrument.kind, Instrument.source_name):
        print(f"  {instrument.kind}: {instrument.display_name}")

    print("\n[확인이 필요한 항목]")
    print(f"  미확정: {len(reports.review_undetermined(session, limit=10000))}건")
    print(f"  수입이 아닌 것으로 처리한 입금: {len(reports.review_large_inflows(session, limit=10000))}건")
    print(f"  미분류: {len(reports.review_uncategorized(session, limit=10000))}건")
    for gap in reports.instrument_gaps(session):
        print(f"  오래된 결제수단: {gap.name}, 마지막 거래 후 {gap.days_behind}일")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip", type=Path)
    parser.add_argument("--amounts", action="store_true", help="금액을 그대로 출력한다")
    args = parser.parse_args()

    if not args.zip.is_file():
        print("오류: 파일을 찾을 수 없습니다.", file=sys.stderr)
        return 2

    url = os.environ.get("DATABASE_URL") or local_database_url()
    os.environ["DATABASE_URL"] = url
    os.chdir(PROJECT_ROOT)
    command.upgrade(Config("alembic.ini"), "head")

    password = getpass.getpass("내보내기 비밀번호 (입력해도 화면에 표시되지 않습니다): ")
    engine = make_engine(url)
    with Session(engine) as session:
        result = import_export(session, args.zip.read_bytes(), [password], source="cli")
        session.commit()
        print(f"\n가져오기 결과: {result.status}")
        if result.reason:
            print(f"사유: {result.reason}")
        for name, value in result.counts.items():
            print(f"  {name}: {value}")
        if result.status != "quarantined":
            print_report(session, args.amounts)
    return 0 if result.status != "quarantined" else 1


if __name__ == "__main__":
    sys.exit(main())
