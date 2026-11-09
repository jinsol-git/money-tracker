"""사용자의 분류 변경과 되돌리기. 화면 버튼과 챗 제안 적용이 모두 이 경로를 쓴다."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger.models import NATURES, Assignment, Category, ChangeSet, TxnSplit
from app.ledger.reclassify import reclassify

CHANGE_ERRORS = {
    "empty": "바꿀 값이 없습니다.",
    "unknown_nature": "알 수 없는 종류입니다.",
    "unknown_category": "알 수 없는 카테고리입니다.",
    "unknown_split": "찾을 수 없는 거래가 있습니다.",
    "unknown_change": "찾을 수 없는 변경입니다.",
    "already_undone": "이미 되돌린 변경입니다.",
    "category_not_allowed": "이 종류의 거래에는 카테고리를 정할 수 없습니다.",
    "category_mismatch": "종류에 맞는 카테고리를 함께 골라 주세요.",
}
CATEGORY_KIND_OF_NATURE = {"expense": "expense", "refund": "expense", "income": "income"}


class ChangeError(Exception):
    def __init__(self, code: str):
        super().__init__(CHANGE_ERRORS[code])
        self.code = code


def _check_category(session: Session, split: TxnSplit, nature: str | None, category: Category | None) -> None:
    """지출이 수입 비목으로 집계되는 일을 막는다. 사용자가 고른 비목은 자동 분류가 고쳐 주지 않는다."""
    kind = CATEGORY_KIND_OF_NATURE.get(nature or split.nature)
    if category is not None and kind is None:
        raise ChangeError("category_not_allowed")
    kept = category
    if kept is None and split.category_source == "user" and split.category_id is not None:
        kept = session.get(Category, split.category_id)
    if kind is not None and kept is not None and kept.kind != kind:
        raise ChangeError("category_mismatch")


def apply_change(
    session: Session,
    split_ids: Iterable[int],
    *,
    nature: str | None = None,
    category_id: int | None = None,
    origin: str = "user",
    summary: str = "",
) -> ChangeSet:
    if nature is None and category_id is None:
        raise ChangeError("empty")
    if nature is not None and nature not in NATURES:
        raise ChangeError("unknown_nature")
    category = session.get(Category, category_id) if category_id is not None else None
    if category_id is not None and category is None:
        raise ChangeError("unknown_category")

    ids = list(split_ids)
    splits = session.scalars(select(TxnSplit).where(TxnSplit.id.in_(ids))).all()
    if len(splits) != len(set(ids)):
        raise ChangeError("unknown_split")
    for split in splits:
        _check_category(session, split, nature, category)

    change_set = ChangeSet(origin=origin, summary=summary)
    session.add(change_set)
    session.flush()

    for split in splits:
        if nature is not None and split.nature != nature:
            session.add(
                Assignment(
                    change_set_id=change_set.id,
                    split_id=split.id,
                    field="nature",
                    old_value=split.nature,
                    new_value=nature,
                    old_source=split.nature_source,
                    new_source="user",
                )
            )
            split.nature = nature
            split.nature_source = "user"
            split.nature_reason = f"change_set:{change_set.id}"
            split.channel_instrument_id = None
        if category_id is not None and split.category_id != category_id:
            session.add(
                Assignment(
                    change_set_id=change_set.id,
                    split_id=split.id,
                    field="category",
                    old_value=str(split.category_id) if split.category_id is not None else None,
                    new_value=str(category_id),
                    old_source=split.category_source,
                    new_source="user",
                )
            )
            split.category_id = category_id
            split.category_source = "user"
    reclassify(session)
    return change_set


def undo_change_set(session: Session, change_set_id: int) -> ChangeSet:
    """그 변경 이후 같은 값을 다시 바꾼 거래는 나중 변경을 존중해 건너뛴다."""
    change_set = session.get(ChangeSet, change_set_id)
    if change_set is None:
        raise ChangeError("unknown_change")
    if change_set.undone_at is not None:
        raise ChangeError("already_undone")

    assignments = session.scalars(
        select(Assignment).where(Assignment.change_set_id == change_set_id).order_by(Assignment.id.desc())
    ).all()
    undo = ChangeSet(origin="undo", summary=f"변경 {change_set_id} 되돌리기")
    session.add(undo)
    session.flush()

    for assignment in assignments:
        split = session.get(TxnSplit, assignment.split_id)
        if split is None:
            continue
        current = split.nature if assignment.field == "nature" else split.category_id
        current_text = str(current) if current is not None else None
        if current_text != assignment.new_value:
            continue
        session.add(
            Assignment(
                change_set_id=undo.id,
                split_id=split.id,
                field=assignment.field,
                old_value=assignment.new_value,
                new_value=assignment.old_value,
                old_source=assignment.new_source,
                new_source=assignment.old_source,
            )
        )
        if assignment.field == "nature":
            split.nature = assignment.old_value
            split.nature_source = assignment.old_source
            split.nature_reason = "undo"
        else:
            split.category_id = int(assignment.old_value) if assignment.old_value is not None else None
            split.category_source = assignment.old_source

    change_set.undone_at = datetime.now(UTC)
    reclassify(session)
    return change_set


def recent_changes(session: Session, limit: int = 10) -> list[ChangeSet]:
    """아직 되돌릴 수 있는 변경. 되돌리기 자체는 목록에 넣지 않는다."""
    return list(
        session.scalars(
            select(ChangeSet)
            .where(ChangeSet.origin != "undo", ChangeSet.undone_at.is_(None))
            .order_by(ChangeSet.id.desc())
            .limit(limit)
        )
    )
