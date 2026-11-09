"""장부 스키마.

한 거래를 세 축으로 나눠 다룬다.
- 성격(nature): 합계에 들어가는지 정한다.
- 비목(category): 어디로 집계할지 정한다.
- 채널(channel): 결제수단에서 파생한다. report_lines 뷰에서 계산하며 저장하지 않는다.

뱅크샐러드가 준 값은 Txn에, 앱이 판단한 값은 TxnSplit에 둔다. 다시 가져와도
Txn만 갱신되므로 사용자가 수정한 분류는 덮어쓰이지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NATURES = (
    "expense",  # 지출
    "income",  # 수입
    "refund",  # 환불·정산 (지출에서 차감)
    "internal",  # 내부 이체
    "funding",  # 자금 행: 페이 충전, 연동된 카드의 대금
    "asset_move",  # 자산 이동: 저축, 투자
    "debt_move",  # 부채 이동: 대출 실행, 원금 상환
    "undetermined",  # 미확정: 합계에 넣지 않고 옆에 표시
    "ignored",  # 0원 행 등
)
INSTRUMENT_KINDS = (
    "credit_card",
    "check_card",
    "account",
    "savings",
    "pay_usage",
    "pay_balance",
    "cash",
    "unknown",
)
LINK_KINDS = ("charge", "internal_transfer", "refund")
MANUAL_ASSET_KINDS = ("car", "real_estate", "other")
TENURES = ("own", "jeonse", "wolse")
CAR_CLASSES = ("small", "compact", "mid", "large")
CAR_ORIGINS = ("domestic", "imported")
OTHER_ASSET_TYPES = ("cash", "physical", "coin", "fund", "stock", "pension")


def _in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


class Base(DeclarativeBase):
    pass


class ImportBatch(Base):
    __tablename__ = "import_batch"
    __table_args__ = (
        CheckConstraint(_in("status", ("applied", "quarantined", "resolved")), name="ck_import_batch_status"),
        Index(
            "uq_import_batch_applied_sha",
            "file_sha256",
            unique=True,
            postgresql_where=text("status = 'applied'"),
        ),
        Index(
            "uq_import_batch_applied_message",
            "message_id",
            unique=True,
            postgresql_where=text("status = 'applied' AND message_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(Text)
    file_sha256: Mapped[str] = mapped_column(Text)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    raw_zip: Mapped[bytes | None] = mapped_column(LargeBinary)  # 격리된 파일만 보관한다
    parser_version: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Instrument(Base):
    __tablename__ = "instrument"
    __table_args__ = (
        CheckConstraint(_in("kind", INSTRUMENT_KINDS), name="ck_instrument_kind"),
        CheckConstraint(_in("kind_source", ("auto", "user")), name="ck_instrument_kind_source"),
        CheckConstraint(_in("bill_policy", ("auto", "count", "ignore")), name="ck_instrument_bill_policy"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_name: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, server_default="unknown")
    kind_source: Mapped[str] = mapped_column(Text, server_default="auto")
    is_virtual: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))  # 대금으로만 확인된 카드
    linked_from: Mapped[date | None] = mapped_column(Date)  # 사용자가 직접 지정한 연동 시작일
    bill_policy: Mapped[str] = mapped_column(Text, server_default="auto")
    first_seen: Mapped[date | None] = mapped_column(Date)
    last_seen: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Category(Base):
    __tablename__ = "category"
    __table_args__ = (CheckConstraint(_in("kind", ("expense", "income")), name="ck_category_kind"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"))
    sort_order: Mapped[int] = mapped_column(Integer, server_default=text("100"))
    is_system: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class CategoryMap(Base):
    """뱅크샐러드 분류를 앱 비목으로 옮기는 표. source_minor가 NULL이면 대분류 전체에 적용한다."""

    __tablename__ = "category_map"
    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_major",
            "source_minor",
            name="uq_category_map_source",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_type: Mapped[str] = mapped_column(Text)
    source_major: Mapped[str] = mapped_column(Text)
    source_minor: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"))


class Rule(Base):
    __tablename__ = "rule"
    __table_args__ = (
        CheckConstraint("set_nature IS NOT NULL OR set_category_id IS NOT NULL", name="ck_rule_has_action"),
        CheckConstraint("set_nature IS NULL OR " + _in("set_nature", NATURES), name="ck_rule_set_nature"),
        CheckConstraint(_in("created_from", ("user", "chat", "memory")), name="ck_rule_created_from"),
        CheckConstraint("match_direction IS NULL OR match_direction IN ('out', 'in')", name="ck_rule_match_direction"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, server_default=text("100"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    match_description_equals: Mapped[str | None] = mapped_column(Text)
    match_description_contains: Mapped[str | None] = mapped_column(Text)
    match_source_type: Mapped[str | None] = mapped_column(Text)
    match_source_major: Mapped[str | None] = mapped_column(Text)
    match_source_minor: Mapped[str | None] = mapped_column(Text)
    match_instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instrument.id"))
    match_amount_min: Mapped[int | None] = mapped_column(BigInteger)
    match_amount_max: Mapped[int | None] = mapped_column(BigInteger)
    match_direction: Mapped[str | None] = mapped_column(Text)  # 월세를 내는 상대에게 보증금을 돌려받는 경우를 가른다
    set_nature: Mapped[str | None] = mapped_column(Text)
    set_category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"))
    created_from: Mapped[str] = mapped_column(Text, server_default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Txn(Base):
    """뱅크샐러드가 준 값. 가져오기만 이 테이블을 수정한다."""

    __tablename__ = "txn"
    __table_args__ = (
        UniqueConstraint("key_base", "occurrence", name="uq_txn_key"),
        CheckConstraint(_in("status", ("active", "missing")), name="ck_txn_status"),
        Index("ix_txn_occurred_date", "occurred_date"),
        Index("ix_txn_instrument", "instrument_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key_base: Mapped[str] = mapped_column(Text)
    occurrence: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))  # KST 기준
    occurred_date: Mapped[date] = mapped_column(Date)
    source_type: Mapped[str] = mapped_column(Text)
    source_major: Mapped[str] = mapped_column(Text)
    source_minor: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    source_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(Text)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.id"))
    memo: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    first_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id"))
    last_seen_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id"))
    missing_since_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batch.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    instrument: Mapped[Instrument] = relationship()
    splits: Mapped[list["TxnSplit"]] = relationship(back_populates="txn", cascade="all, delete-orphan")


class TxnSplit(Base):
    """앱이 판단한 값. 집계의 단위이며 거래당 기본 1건이고, 원금·이자처럼 섞인 행은 나눈다."""

    __tablename__ = "txn_split"
    __table_args__ = (
        UniqueConstraint("txn_id", "seq", name="uq_txn_split_seq"),
        CheckConstraint(_in("nature", NATURES), name="ck_txn_split_nature"),
        CheckConstraint(_in("nature_source", ("auto", "rule", "user")), name="ck_txn_split_nature_source"),
        CheckConstraint(
            _in("category_source", ("auto", "map", "rule", "user")), name="ck_txn_split_category_source"
        ),
        Index("ix_txn_split_txn", "txn_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    txn_id: Mapped[int] = mapped_column(ForeignKey("txn.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    amount: Mapped[int] = mapped_column(BigInteger)
    nature: Mapped[str] = mapped_column(Text, server_default="undetermined")
    nature_source: Mapped[str] = mapped_column(Text, server_default="auto")
    nature_reason: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"))
    category_source: Mapped[str] = mapped_column(Text, server_default="auto")
    channel_instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instrument.id"))  # 대금으로 잡은 카드 등

    txn: Mapped[Txn] = relationship(back_populates="splits")


class TxnLink(Base):
    __tablename__ = "txn_link"
    __table_args__ = (
        UniqueConstraint("kind", "from_txn_id", "to_txn_id", name="uq_txn_link"),
        CheckConstraint(_in("kind", LINK_KINDS), name="ck_txn_link_kind"),
        CheckConstraint(_in("source", ("auto", "user")), name="ck_txn_link_source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    from_txn_id: Mapped[int] = mapped_column(ForeignKey("txn.id", ondelete="CASCADE"))
    to_txn_id: Mapped[int] = mapped_column(ForeignKey("txn.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(Text, server_default="auto")


class RawRow(Base):
    """처음 보였거나 소스 값이 바뀐 행의 사본. 변경 이력 확인과 파서 오류 복구에 쓴다."""

    __tablename__ = "raw_row"
    __table_args__ = (Index("ix_raw_row_batch", "batch_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id"))
    txn_id: Mapped[int] = mapped_column(ForeignKey("txn.id", ondelete="CASCADE"))
    row_number: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ChangeSet(Base):
    """되돌리기 단위. 챗 제안을 적용한 것도, 화면에서 일괄 수정한 것도 한 건으로 남는다."""

    __tablename__ = "change_set"
    __table_args__ = (CheckConstraint(_in("origin", ("user", "chat", "undo")), name="ck_change_set_origin"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    origin: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Assignment(Base):
    """분류 변경 이력. 추가만 하고 수정하지 않는다."""

    __tablename__ = "assignment"
    __table_args__ = (
        CheckConstraint(_in("field", ("nature", "category")), name="ck_assignment_field"),
        Index("ix_assignment_change_set", "change_set_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    change_set_id: Mapped[int] = mapped_column(ForeignKey("change_set.id"))
    split_id: Mapped[int] = mapped_column(ForeignKey("txn_split.id", ondelete="CASCADE"))
    field: Mapped[str] = mapped_column(Text)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    old_source: Mapped[str] = mapped_column(Text)
    new_source: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssetSnapshot(Base):
    """내보낸 시점의 자산과 부채. 하루에 한 건만 두며, 합계는 직접 계산한 값이다."""

    __tablename__ = "asset_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id"))
    as_of: Mapped[date] = mapped_column(Date, unique=True)
    total_assets: Mapped[int] = mapped_column(BigInteger)
    total_liabilities: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items: Mapped[list["AssetItem"]] = relationship(cascade="all, delete-orphan")
    details: Mapped[list["AssetDetail"]] = relationship(cascade="all, delete-orphan")


class AssetItem(Base):
    __tablename__ = "asset_item"
    __table_args__ = (
        CheckConstraint(_in("side", ("asset", "liability")), name="ck_asset_item_side"),
        Index("ix_asset_item_snapshot", "snapshot_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("asset_snapshot.id", ondelete="CASCADE"))
    side: Mapped[str] = mapped_column(Text)
    group_label: Mapped[str] = mapped_column(Text)
    product_name: Mapped[str] = mapped_column(Text)
    amount: Mapped[int] = mapped_column(BigInteger)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instrument.id"))


class ManualAsset(Base):
    """뱅크샐러드에 없는 자산. 값은 날짜별로 ManualAssetValue에 쌓는다."""

    __tablename__ = "manual_asset"
    __table_args__ = (
        CheckConstraint(_in("kind", MANUAL_ASSET_KINDS), name="ck_manual_asset_kind"),
        CheckConstraint("tenure IS NULL OR " + _in("tenure", TENURES), name="ck_manual_asset_tenure"),
        CheckConstraint("car_class IS NULL OR " + _in("car_class", CAR_CLASSES), name="ck_manual_asset_car_class"),
        CheckConstraint("car_origin IS NULL OR " + _in("car_origin", CAR_ORIGINS), name="ck_manual_asset_car_origin"),
        CheckConstraint("other_type IS NULL OR " + _in("other_type", OTHER_ASSET_TYPES), name="ck_manual_asset_other_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    tenure: Mapped[str | None] = mapped_column(Text)  # 부동산: 매매, 전세, 월세
    monthly_rent: Mapped[int | None] = mapped_column(BigInteger)  # 전월세의 월세. 나가는 돈이라 자산에는 넣지 않는다
    other_type: Mapped[str | None] = mapped_column(Text)  # 기타 자산: 현금, 실물, 코인, 펀드, 주식, 연금
    memo: Mapped[str | None] = mapped_column(Text)
    car_class: Mapped[str | None] = mapped_column(Text)
    car_origin: Mapped[str | None] = mapped_column(Text)
    first_registered: Mapped[date | None] = mapped_column(Date)  # 차의 나이는 이 날짜부터 센다
    base_price: Mapped[int | None] = mapped_column(BigInteger)  # 감가 계산의 출발 금액
    base_date: Mapped[date | None] = mapped_column(Date)  # 출발 금액의 시점. 중고차면 산 날이다
    acquired_on: Mapped[date] = mapped_column(Date)  # 이날부터 자산에 넣는다
    sold_on: Mapped[date | None] = mapped_column(Date)  # 이날부터 자산에서 뺀다
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    values: Mapped[list["ManualAssetValue"]] = relationship(
        cascade="all, delete-orphan", order_by="ManualAssetValue.as_of"
    )


class ManualAssetValue(Base):
    __tablename__ = "manual_asset_value"
    __table_args__ = (
        UniqueConstraint("asset_id", "as_of", name="uq_manual_asset_value_day"),
        CheckConstraint(_in("source", ("user", "depreciation")), name="ck_manual_asset_value_source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("manual_asset.id", ondelete="CASCADE"))
    as_of: Mapped[date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(Text, server_default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssetDetail(Base):
    """보험·투자·대출 현황 행. 항목이 구역마다 달라 그대로 JSON으로 둔다."""

    __tablename__ = "asset_detail"
    __table_args__ = (
        CheckConstraint(_in("section", ("insurance", "investment", "loan")), name="ck_asset_detail_section"),
        Index("ix_asset_detail_snapshot", "snapshot_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("asset_snapshot.id", ondelete="CASCADE"))
    section: Mapped[str] = mapped_column(Text)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB)
