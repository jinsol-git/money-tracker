"""가져오기와 집계를 DB까지 포함해 검증한다. 답을 아는 합성 장부를 쓴다."""

from __future__ import annotations

import re
from datetime import date

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, create_engine, make_url, select, text

import app.ops_models  # noqa: F401 - 메타데이터에 운영 테이블을 등록한다
from app.db import normalize_url
from app.ingest.importer import import_export
from app.ledger import reports
from app.ledger.changes import ChangeError, apply_change, undo_change_set
from app.ledger.models import (
    AssetSnapshot,
    Base,
    Category,
    ChangeSet,
    ImportBatch,
    Instrument,
    RawRow,
    Rule,
    Txn,
    TxnLink,
    TxnSplit,
)
from app.ledger.reclassify import reclassify
from fixtures import make_export as fx

PASSWORD = "0000"
BANK = "입출금통장"
OTHER_BANK = "다른통장"
MONEY = "네이버페이 머니"
PAY = "네이버페이 간편결제"
CARD = "어떤 삼성카드"
CASH = "현금"
ASSETS = [
    fx.AssetRow("자유입출금 자산", BANK, 1_000_000),
    fx.AssetRow("자유입출금 자산", OTHER_BANK, 500_000),
    fx.AssetRow("현금 자산", CASH, 10_000),
]
LIABILITIES = [fx.AssetRow("장기대출", "대출상품", 200_000)]

# 2월: 소비 카드 30,000 + 페이 20,000 + 계좌 10,000 - 취소 5,000 = 55,000
LEDGER = [
    fx.tx("2026-02-01 09:00:00", "수입", "급여", "미분류", "회사", 1_000_000, BANK),
    fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -30_000, CARD),
    fx.tx("2026-02-03 10:00:00", "지출", "식비", "한식", "밥집", 5_000, CARD),
    # 충전: 통장 쪽이 지출로 잡히지만 이중 집계다
    fx.tx("2026-02-04 10:00:00", "지출", "온라인쇼핑", "결제/충전", "네이버페이 충전", -20_000, BANK),
    fx.tx("2026-02-04 10:00:00", "이체", "내계좌이체", "미분류", "충전", 20_000, MONEY),
    fx.tx("2026-02-05 10:00:00", "지출", "온라인쇼핑", "인터넷쇼핑", "쇼핑몰", -20_000, PAY),
    fx.tx("2026-02-06 10:00:00", "지출", "금융", "보험", "보험사", -10_000, BANK),
    # 카드대금: 사용 내역이 있는 카드라 제외
    fx.tx("2026-02-25 10:00:00", "이체", "카드대금", "미분류", "삼성카드 결제대금", -30_000, BANK),
    # 미연동 카드 대금은 지출로 잡는다 (별도 계산)
    fx.tx("2026-02-26 10:00:00", "이체", "카드대금", "미분류", "현대카드", -40_000, BANK),
    # 내 계좌 사이 이동
    fx.tx("2026-02-27 10:00:00", "이체", "내계좌이체", "미분류", "이동", -100_000, BANK),
    fx.tx("2026-02-27 10:00:00", "이체", "내계좌이체", "미분류", "이동", 100_000, OTHER_BANK),
    # 정체를 모르는 출금
    fx.tx("2026-02-20 10:00:00", "이체", "이체", "미분류", "어떤사람", -70_000, BANK),
    # 저축에서 들어온 큰 돈
    fx.tx("2026-02-21 10:00:00", "이체", "저축", "미분류", "적금 해지", 300_000, BANK),
    # 이자가 0원인 행
    fx.tx("2026-02-22 10:00:00", "수입", "금융수입", "미분류", "이자", 0, OTHER_BANK),
]


def export_file(rows=LEDGER, *, inner_name="2026-01-15~2026-02-28.xlsx", assets=ASSETS, tamper=None) -> bytes:
    return fx.zip_aes(
        fx.build_xlsx(rows=rows, assets=assets, liabilities=LIABILITIES, tamper=tamper), PASSWORD, inner_name
    )


def load(session, *args, **kwargs):
    result = import_export(session, export_file(*args, **kwargs), [PASSWORD])
    assert result.status == "applied", result
    return result


def split_of(session, description: str, amount: int | None = None) -> TxnSplit:
    query = select(TxnSplit).join(Txn).where(Txn.description == description)
    if amount is not None:
        query = query.where(TxnSplit.amount == amount)
    return session.scalars(query).one()


def category_id(session, key: str) -> int:
    return session.scalar(select(Category.id).where(Category.key == key))


def instrument_id(session, name: str) -> int:
    return session.scalar(select(Instrument.id).where(Instrument.source_name == name))


# --- 스키마


def test_migration_matches_models(session):
    context = MigrationContext.configure(session.connection())
    assert compare_metadata(context, Base.metadata) == []


def test_check_constraints_match_models(session):
    # Alembic 자동 비교는 CHECK 제약을 보지 않아 모델과 DB가 어긋나도 알 수 없다.
    in_database = dict(
        session.execute(
            text("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE contype = 'c'")
        ).all()
    )
    checked = 0
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if not isinstance(constraint, CheckConstraint):
                continue
            definition = in_database.get(constraint.name)
            assert definition is not None, f"DB에 없는 제약: {constraint.name}"
            for literal in re.findall(r"'([^']+)'", str(constraint.sqltext)):
                assert f"'{literal}'" in definition, f"{constraint.name}: {literal}"
            checked += 1
    assert checked >= 10


def test_migrations_can_be_reversed(database_url):
    admin = create_engine(normalize_url(database_url), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text("DROP DATABASE IF EXISTS downgrade_check"))
        connection.execute(text("CREATE DATABASE downgrade_check"))
    target = create_engine(make_url(normalize_url(database_url)).set(database="downgrade_check"))
    config = Config("alembic.ini")
    with target.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")
    target.dispose()
    with admin.connect() as connection:
        connection.execute(text("DROP DATABASE downgrade_check"))
    admin.dispose()


def test_default_categories_follow_the_chosen_order(session):
    expenses = session.scalars(select(Category.name).where(Category.kind == "expense").order_by(Category.sort_order)).all()
    assert expenses[:15] == [
        "식비",
        "주거비",
        "의류",
        "생활비",
        "쇼핑",
        "엔터테인먼트",
        "교육비",
        "보험",
        "통신",
        "교통",
        "의료·건강",
        "경조사·회비",
        "세금·공과금",
        "뷰티·미용",
        "반려동물",
    ]
    # 목록에 없던 카테고리도 지우지 않고 뒤에 둔다. 합치면 되돌릴 수 없기 때문이다.
    assert expenses[15:] == ["여행", "자동차", "구독료", "도서", "카드대금(상세 없음)", "미분류 지출"]


def test_taxonomy_migration_moves_only_automatic_choices(database_url):
    admin = create_engine(normalize_url(database_url), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text("DROP DATABASE IF EXISTS taxonomy_check"))
        connection.execute(text("CREATE DATABASE taxonomy_check"))
    target = create_engine(make_url(normalize_url(database_url)).set(database="taxonomy_check"))
    config = Config("alembic.ini")

    def key_of(connection, description: str) -> tuple[str, str]:
        row = connection.execute(
            text(
                "SELECT c.key, s.category_source FROM txn_split s JOIN txn t ON t.id = s.txn_id "
                "JOIN category c ON c.id = s.category_id WHERE t.description = :description"
            ),
            {"description": description},
        ).one()
        return row.key, row.category_source

    with target.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0003")
        batch = connection.execute(
            text("INSERT INTO import_batch (source, file_sha256, status, parser_version) VALUES ('t', 'x', 'applied', '1') RETURNING id")
        ).scalar_one()
        instrument = connection.execute(
            text("INSERT INTO instrument (source_name, display_name) VALUES ('카드', '카드') RETURNING id")
        ).scalar_one()
        rows = [
            ("옷가게", "패션/쇼핑", "shopping", "map"),
            ("직접 고른 옷가게", "패션/쇼핑", "food", "user"),
            ("직접 쇼핑으로 둔 옷가게", "패션/쇼핑", "shopping", "user"),
            ("동물병원", "반려동물", "uncategorized_expense", "auto"),
            ("축의금", "경조/선물", "uncategorized_expense", "auto"),
            ("마트", "온라인쇼핑", "shopping", "map"),
        ]
        for index, (description, major, category, source) in enumerate(rows):
            txn = connection.execute(
                text(
                    "INSERT INTO txn (key_base, occurrence, occurred_at, occurred_date, source_type, source_major, source_minor, "
                    "description, source_amount, currency, instrument_id, first_batch_id, last_seen_batch_id) "
                    "VALUES (:key, 1, '2026-01-01 10:00:00', '2026-01-01', '지출', :major, '미분류', :description, -1000, 'KRW', "
                    ":instrument, :batch, :batch) RETURNING id"
                ),
                {"key": f"k{index}", "major": major, "description": description, "instrument": instrument, "batch": batch},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO txn_split (txn_id, amount, nature, category_id, category_source) "
                    "SELECT :txn, -1000, 'expense', id, :source FROM category WHERE key = :category"
                ),
                {"txn": txn, "source": source, "category": category},
            )

        command.upgrade(config, "head")
        assert key_of(connection, "옷가게") == ("apparel", "map")
        assert key_of(connection, "직접 고른 옷가게") == ("food", "user")
        assert key_of(connection, "직접 쇼핑으로 둔 옷가게") == ("shopping", "user")
        assert key_of(connection, "동물병원") == ("pet", "map")
        assert key_of(connection, "축의금") == ("dues", "map")
        assert key_of(connection, "마트") == ("shopping", "map")

        command.downgrade(config, "0003")
        assert key_of(connection, "옷가게") == ("shopping", "map")
        assert key_of(connection, "동물병원")[0] == "uncategorized_expense"
        names = dict(connection.execute(text("SELECT key, name FROM category")).all())
        assert names["housing"] == "주거" and names["finance"] == "금융·세금"
        assert "apparel" not in names
    target.dispose()
    with admin.connect() as connection:
        connection.execute(text("DROP DATABASE taxonomy_check"))
    admin.dispose()


def test_new_categories_are_used_on_import(session):
    rows = [
        fx.tx("2026-02-02 10:00:00", "지출", "패션/쇼핑", "의류", "옷가게", -1_000, CARD),
        fx.tx("2026-02-03 10:00:00", "지출", "반려동물", "병원", "동물병원", -2_000, CARD),
        fx.tx("2026-02-04 10:00:00", "지출", "경조/선물", "축의금", "결혼식", -3_000, CARD),
    ]
    load(session, rows=rows)
    lines = {line.category_key: line.amount for line in reports.category_breakdown(session)}
    assert lines == {"apparel": 1_000, "pet": 2_000, "dues": 3_000}


# --- 집계


def test_month_totals_avoid_double_counting(session):
    load(session)
    summary = reports.monthly_summary(session)
    assert [m.month for m in summary] == ["2026-02"]
    month = summary[0]

    assert month.income == 1_000_000
    # 카드 25,000(취소 차감) + 페이 20,000 + 계좌 10,000 + 미연동 카드 대금 40,000
    assert month.spend_total == 95_000
    assert month.spend_card == 65_000
    assert month.spend_pay == 20_000
    assert month.spend_account == 10_000
    assert month.spend_other == 0
    assert month.undetermined_out == 70_000
    assert month.undetermined_in == 0


def test_total_equals_sum_of_channels(session):
    load(session)
    for month in reports.monthly_summary(session):
        assert month.spend_total == month.spend_card + month.spend_account + month.spend_pay + month.spend_other


def _assert_bridge_closes(bridge: reports.SpendBridge) -> None:
    expected = (
        bridge.banksalad_total
        - bridge.charges_removed
        - bridge.moves_removed
        - bridge.internal_removed
        - bridge.other_removed
        + bridge.unlinked_bills_added
        + bridge.other_added
    )
    assert expected == bridge.our_total


def test_difference_from_banksalad_total_is_explained(session):
    load(session)
    bridge = reports.spend_bridge(session)[0]
    assert bridge.banksalad_total == 55_000 + 20_000  # 뱅크샐러드 합계에는 충전이 들어 있다
    assert bridge.charges_removed == 20_000
    assert bridge.unlinked_bills_added == 40_000
    assert bridge.our_total == 95_000
    _assert_bridge_closes(bridge)


def test_bridge_still_closes_after_user_changes(session):
    load(session)
    # 지출을 지출이 아니라고 지정하고, 이체를 지출로 지정한다
    apply_change(session, [split_of(session, "보험사").id], nature="internal")
    apply_change(session, [split_of(session, "어떤사람").id], nature="expense")
    bridge = reports.spend_bridge(session)[0]
    assert bridge.internal_removed == 10_000
    assert bridge.other_added == 70_000
    _assert_bridge_closes(bridge)
    assert reports.monthly_summary(session)[0].spend_total == bridge.our_total


def test_category_breakdown_uses_requested_categories(session):
    load(session)
    lines = {line.category_name: line.amount for line in reports.category_breakdown(session)}
    assert lines == {
        "카드대금(상세 없음)": 40_000,
        "식비": 25_000,
        "쇼핑": 20_000,
        "보험": 10_000,
    }
    card_only = {line.category_name: line.amount for line in reports.category_breakdown(session, channel="card")}
    assert card_only == {"카드대금(상세 없음)": 40_000, "식비": 25_000}


def test_refund_stays_in_its_own_month(session):
    rows = [
        fx.tx("2026-01-20 10:00:00", "지출", "식비", "한식", "밥집", -10_000, CARD),
        fx.tx("2026-02-10 10:00:00", "지출", "식비", "한식", "밥집", 4_000, CARD),
    ]
    load(session, rows=rows)
    by_month = {m.month: m.spend_total for m in reports.monthly_summary(session)}
    assert by_month == {"2026-01": 10_000, "2026-02": -4_000}


def test_links_are_recorded(session):
    load(session)
    kinds = {link.kind for link in session.scalars(select(TxnLink))}
    assert kinds == {"charge", "internal_transfer"}  # 장부의 취소는 부분 취소라 원거래와 잇지 않는다


def test_full_refund_is_linked_to_its_origin(session):
    rows = [
        fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -9_000, CARD),
        fx.tx("2026-02-03 10:00:00", "지출", "식비", "한식", "밥집", 9_000, CARD),
    ]
    load(session, rows=rows)
    assert [link.kind for link in session.scalars(select(TxnLink))] == ["refund"]


def test_virtual_card_is_registered_for_bill_only_company(session):
    load(session)
    virtual = session.scalars(select(Instrument).where(Instrument.is_virtual.is_(True))).one()
    assert virtual.source_name == "현대카드 (대금으로만 확인)"
    assert virtual.kind == "credit_card"


def test_instrument_kinds_are_inferred_from_balance_sheet(session):
    load(session)
    kinds = {i.source_name: i.kind for i in session.scalars(select(Instrument))}
    assert kinds[BANK] == "account"
    assert kinds[MONEY] == "pay_balance"
    assert kinds[PAY] == "pay_usage"
    assert kinds[CARD] == "credit_card"


def test_bill_policy_can_remove_unlinked_bill_from_spend(session):
    load(session)
    card = session.scalars(select(Instrument).where(Instrument.is_virtual.is_(True))).one()
    card.bill_policy = "ignore"
    reclassify(session)
    assert reports.monthly_summary(session)[0].spend_total == 55_000


# --- 계좌 추적


def test_account_trace_explains_outflows(session):
    load(session)
    trace = reports.account_trace(session, instrument_id(session, BANK))

    assert trace.total_out == 270_000
    assert trace.direct == {"insurance": 10_000}
    assert trace.pay_charges == 20_000
    assert trace.card_bills == 30_000
    assert trace.own_transfer == 100_000
    # 미추적 = 정체 모를 출금 70,000 + 상세 없는 카드대금 40,000
    assert trace.untracked == 110_000
    assert trace.untracked_rows == 2
    assert trace.trace_rate == pytest.approx(1 - 110_000 / 170_000)


def test_trace_rate_improves_when_user_labels_a_transfer(session):
    load(session)
    bank = instrument_id(session, BANK)
    before = reports.account_trace(session, bank).trace_rate
    split = split_of(session, "어떤사람")
    apply_change(session, [split.id], nature="expense", category_id=category_id(session, "housing"))
    assert reports.account_trace(session, bank).trace_rate > before


# --- 자산


def test_net_worth_is_computed_not_read_from_formula_cells(session):
    load(session)
    points = reports.net_worth_series(session)
    assert len(points) == 1
    assert points[0].as_of == date(2026, 2, 28)
    assert points[0].total_assets == 1_510_000
    assert points[0].total_liabilities == 200_000
    assert points[0].net_worth == 1_310_000


def test_each_export_adds_a_point_and_same_day_replaces(session):
    load(session)
    richer = [fx.AssetRow("자유입출금 자산", BANK, 2_000_000), *ASSETS[1:]]
    load(session, inner_name="2026-01-20~2026-03-05.xlsx", assets=richer)
    points = reports.net_worth_series(session)
    assert [p.as_of for p in points] == [date(2026, 2, 28), date(2026, 3, 5)]

    richest = [fx.AssetRow("자유입출금 자산", BANK, 3_000_000), *ASSETS[1:]]
    load(session, inner_name="2026-01-21~2026-03-05.xlsx", assets=richest)
    points = reports.net_worth_series(session)
    assert len(points) == 2
    assert points[-1].total_assets == 3_510_000


def test_account_balance_history_links_to_instrument(session):
    load(session)
    assert reports.instrument_balances(session, instrument_id(session, BANK)) == [(date(2026, 2, 28), 1_000_000)]


def test_detail_sections_are_stored(session):
    load(session)
    snapshot = session.scalars(select(AssetSnapshot)).one()
    assert {detail.section for detail in snapshot.details} == {"insurance", "investment", "loan"}


# --- 반복 가져오기


def test_same_file_twice_changes_nothing(session):
    data = export_file()
    first = import_export(session, data, [PASSWORD])
    before = session.scalars(select(Txn.id).order_by(Txn.id)).all()
    second = import_export(session, data, [PASSWORD])
    assert second.status == "duplicate"
    assert second.batch_id == first.batch_id
    assert session.scalars(select(Txn.id).order_by(Txn.id)).all() == before


def test_reexport_with_same_content_adds_no_rows(session):
    load(session)
    # 파일 해시가 달라지도록 내보낸 기간만 바꾼다
    result = load(session, inner_name="2026-01-15~2026-03-01.xlsx")
    assert result.counts["new"] == 0
    assert result.counts["updated"] == 0
    assert result.counts["missing"] == 0


def test_overlapping_file_adds_only_new_rows(session):
    load(session)
    newer = [*LEDGER, fx.tx("2026-03-01 10:00:00", "지출", "식비", "한식", "새밥집", -7_000, CARD)]
    result = load(session, rows=newer, inner_name="2026-01-15~2026-03-02.xlsx")
    assert result.counts["new"] == 1
    assert result.counts["rows"] == len(LEDGER) + 1
    assert session.scalars(select(RawRow).where(RawRow.batch_id == result.batch_id)).all()[0].payload["description"] == "새밥집"


def test_identical_rows_are_kept_separately(session):
    same = fx.tx("2026-02-02 10:00:00", "지출", "교통", "대중교통", "지하철", -1_500, CARD)
    rows = [same, list(same), fx.tx("2026-02-03 10:00:00", "지출", "식비", "한식", "밥집", -10, CARD)]
    load(session, rows=rows)
    assert len(session.scalars(select(Txn).where(Txn.description == "지하철")).all()) == 2

    # 다음 파일에 한 건이 더 생기면 그만큼만 늘어난다
    more = [*rows, list(same)]
    result = load(session, rows=more, inner_name="2026-01-15~2026-03-02.xlsx")
    assert result.counts["new"] == 1
    assert len(session.scalars(select(Txn).where(Txn.description == "지하철")).all()) == 3


def test_user_change_survives_reimport(session):
    load(session)
    split = split_of(session, "어떤사람")
    housing = category_id(session, "housing")
    apply_change(session, [split.id], nature="expense", category_id=housing, summary="월세로 지정")

    load(session, inner_name="2026-01-15~2026-03-01.xlsx")
    session.refresh(split)
    assert split.nature == "expense"
    assert split.nature_source == "user"
    assert split.category_id == housing


def test_source_category_change_updates_without_new_row(session):
    load(session)
    changed = [list(row) for row in LEDGER]
    for row in changed:
        if row[5] == "보험사":
            row[3], row[4] = "금융", "세금/과태료"
    result = load(session, rows=changed, inner_name="2026-01-15~2026-03-01.xlsx")
    assert result.counts["new"] == 0
    assert result.counts["updated"] == 1
    assert split_of(session, "보험사").category_id == category_id(session, "finance")


def test_edited_description_keeps_the_same_transaction(session):
    load(session)
    original_id = split_of(session, "보험사").txn_id
    changed = [list(row) for row in LEDGER]
    for row in changed:
        if row[5] == "보험사":
            row[5] = "보험사 자동이체"
    result = load(session, rows=changed, inner_name="2026-01-15~2026-03-01.xlsx")
    assert result.counts["new"] == 0
    assert result.counts["updated"] == 1
    assert split_of(session, "보험사 자동이체").txn_id == original_id

    # 키가 바뀐 뒤에도 그다음 파일과 정상적으로 맞아야 한다
    again = load(session, rows=changed, inner_name="2026-01-15~2026-03-02.xlsx")
    assert again.counts["new"] == 0
    assert again.counts["updated"] == 0


def test_row_removed_inside_period_leaves_totals_and_goes_to_missing(session):
    load(session)
    without = [row for row in LEDGER if row[5] != "보험사"]
    result = load(session, rows=without, inner_name="2026-01-15~2026-03-01.xlsx")
    assert result.counts["missing"] == 1

    txn = session.scalars(select(Txn).where(Txn.description == "보험사")).one()
    assert txn.status == "missing"
    assert txn.missing_since_batch_id == result.batch_id
    assert reports.monthly_summary(session)[0].spend_account == 0


def test_removed_row_returns_with_same_id(session):
    load(session)
    original_id = split_of(session, "보험사").txn_id
    without = [row for row in LEDGER if row[5] != "보험사"]
    load(session, rows=without, inner_name="2026-01-15~2026-03-01.xlsx")
    result = load(session, inner_name="2026-01-15~2026-03-02.xlsx")

    assert result.counts["restored"] == 1
    assert result.counts["new"] == 0
    txn = session.get(Txn, original_id)
    assert txn.status == "active"
    assert txn.missing_since_batch_id is None


def test_removed_row_with_user_edit_stays_in_totals(session):
    load(session)
    split = split_of(session, "보험사")
    apply_change(session, [split.id], category_id=category_id(session, "medical"))
    without = [row for row in LEDGER if row[5] != "보험사"]
    result = load(session, rows=without, inner_name="2026-01-15~2026-03-01.xlsx")

    assert result.counts["missing"] == 0
    assert result.counts["missing_kept"] == 1
    txn = session.get(Txn, split.txn_id)
    assert txn.status == "active"
    assert txn.missing_since_batch_id == result.batch_id


def test_rows_outside_the_file_period_are_preserved(session):
    older = [fx.tx("2025-03-01 10:00:00", "지출", "식비", "한식", "오래된밥집", -1_000, CARD), *LEDGER]
    load(session, rows=older, inner_name="2025-03-01~2026-02-28.xlsx")
    # 1년이 지나 다음 파일에는 오래된 행이 없다. 이 DB가 유일한 사본이다.
    result = load(session, inner_name="2025-04-01~2026-03-05.xlsx")
    assert result.counts["missing"] == 0
    assert session.scalars(select(Txn).where(Txn.description == "오래된밥집")).one().status == "active"


def test_boundary_days_are_not_judged_missing(session):
    load(session)
    without = [row for row in LEDGER if row[5] != "보험사"]
    # 2026-02-06이 파일의 시작일이면 잘렸을 수 있어 부재로 보지 않는다
    result = load(session, rows=without, inner_name="2026-02-06~2026-03-01.xlsx")
    assert result.counts["missing"] == 0


def test_older_file_only_adds_rows(session):
    load(session, inner_name="2026-01-15~2026-03-01.xlsx")
    without = [row for row in LEDGER if row[5] != "보험사"]
    result = load(session, rows=without, inner_name="2026-01-15~2026-02-28.xlsx")
    assert result.counts["missing"] == 0
    assert session.scalars(select(Txn).where(Txn.description == "보험사")).one().status == "active"


def test_older_file_does_not_revert_newer_values_or_revive_rows(session):
    # 격리됐던 옛 파일이 나중에 반영되어도 최신 상태를 되돌리면 안 된다
    changed = [list(row) for row in LEDGER if row[5] != "밥집"]
    for row in changed:
        if row[5] == "보험사":
            row[3], row[4] = "금융", "세금/과태료"
    load(session, rows=changed, inner_name="2026-01-15~2026-03-05.xlsx")
    load(session, rows=[r for r in changed if r[5] != "쇼핑몰"], inner_name="2026-01-15~2026-03-06.xlsx")
    assert session.scalars(select(Txn).where(Txn.description == "쇼핑몰")).one().status == "missing"

    result = load(session, inner_name="2026-01-15~2026-02-28.xlsx")  # 옛날 값이 든 오래된 파일
    assert result.counts["updated"] == 0
    assert result.counts["restored"] == 0
    assert split_of(session, "보험사").category_id == category_id(session, "finance")
    assert session.scalars(select(Txn).where(Txn.description == "쇼핑몰")).one().status == "missing"
    assert result.counts["new"] == 2  # 없던 행만 더해진다
    assert len(session.scalars(select(Txn).where(Txn.description == "밥집")).all()) == 2


def test_different_transaction_is_not_merged_into_a_vanished_one(session):
    # 같은 날·같은 금액·같은 결제수단이어도 시간과 내용이 모두 다르면 다른 거래다
    original = [fx.tx("2026-02-10 10:00:00", "지출", "식비", "한식", "가게A", -5_000, CARD)]
    other = [fx.tx("2026-02-10 18:30:00", "지출", "식비", "한식", "가게B", -5_000, CARD)]
    load(session, rows=original)
    split = split_of(session, "가게A")
    apply_change(session, [split.id], category_id=category_id(session, "travel"))

    result = load(session, rows=other, inner_name="2026-01-15~2026-03-01.xlsx")
    assert result.counts["new"] == 1
    assert split_of(session, "가게B").category_id != category_id(session, "travel")


def test_mass_disappearance_quarantines_the_file(session):
    many = [
        fx.tx(f"2026-02-{day:02d} 10:{minute:02d}:00", "지출", "식비", "한식", f"가게{day}-{minute}", -100, CARD)
        for day in range(2, 8)
        for minute in range(5)
    ]
    load(session, rows=[*LEDGER, *many])
    before = reports.monthly_summary(session)
    result = import_export(session, export_file(inner_name="2026-01-15~2026-03-01.xlsx"), [PASSWORD])
    assert result.status == "quarantined"
    assert result.reason == "suspicious_disappearance"
    assert reports.monthly_summary(session) == before


# --- 격리


def test_wrong_password_keeps_file_for_retry(session):
    data = export_file()
    result = import_export(session, data, ["1111"])
    assert result.status == "quarantined"
    assert result.reason == "wrong_password"
    assert session.get(ImportBatch, result.batch_id).raw_zip == data
    assert session.scalars(select(Txn)).all() == []


def test_same_file_can_be_applied_after_quarantine(session):
    data = export_file()
    assert import_export(session, data, ["1111"]).status == "quarantined"
    assert import_export(session, data, [PASSWORD]).status == "applied"


def test_unencrypted_file_is_rejected_without_keeping_it(session):
    data = fx.zip_plain(fx.build_xlsx(rows=LEDGER))
    result = import_export(session, data, [PASSWORD])
    assert result.reason == "rejected:unencrypted"
    assert session.get(ImportBatch, result.batch_id).raw_zip is None


def test_tampered_summary_quarantines_the_file(session):
    def tamper(workbook):
        for row in workbook["뱅샐현황"].iter_rows():
            if row[1].value == "식비":
                row[4].value = 123

    result = import_export(session, export_file(tamper=tamper), [PASSWORD])
    assert result.status == "quarantined"
    assert result.reason == "reconciliation_failed"
    assert session.scalars(select(Txn)).all() == []


def test_format_change_quarantines_the_file(session):
    def tamper(workbook):
        workbook["가계부 내역"]["G1"] = "금액(원)"

    result = import_export(session, export_file(tamper=tamper), [PASSWORD])
    assert result.reason == "format_changed"
    assert session.scalars(select(Txn)).all() == []


def test_message_id_prevents_reprocessing(session):
    data = export_file()
    first = import_export(session, data, [PASSWORD], source="mail", message_id="m1")
    other = export_file(inner_name="2026-01-15~2026-03-01.xlsx")
    second = import_export(session, other, [PASSWORD], source="mail", message_id="m1")
    assert second.status == "duplicate"
    assert second.batch_id == first.batch_id


# --- 사용자 변경과 규칙


def test_change_and_undo_restore_previous_values(session):
    load(session)
    split = split_of(session, "밥집", -30_000)
    original_category = split.category_id
    change = apply_change(session, [split.id], category_id=category_id(session, "travel"), summary="여행 경비")
    assert split.category_source == "user"

    undo_change_set(session, change.id)
    assert split.category_id == original_category
    assert split.category_source == "map"
    assert session.get(ChangeSet, change.id).undone_at is not None


def test_undo_skips_values_changed_again_later(session):
    load(session)
    split = split_of(session, "밥집", -30_000)
    first = apply_change(session, [split.id], category_id=category_id(session, "travel"))
    medical = category_id(session, "medical")
    apply_change(session, [split.id], category_id=medical)

    undo_change_set(session, first.id)
    assert split.category_id == medical


def test_double_undo_is_rejected(session):
    load(session)
    split = split_of(session, "밥집", -30_000)
    change = apply_change(session, [split.id], category_id=category_id(session, "travel"))
    undo_change_set(session, change.id)
    with pytest.raises(ChangeError):
        undo_change_set(session, change.id)


def test_invalid_changes_are_rejected(session):
    load(session)
    split = split_of(session, "밥집", -30_000)
    with pytest.raises(ChangeError):
        apply_change(session, [split.id])
    with pytest.raises(ChangeError):
        apply_change(session, [split.id], nature="없는성격")
    with pytest.raises(ChangeError):
        apply_change(session, [split.id], category_id=999_999)
    with pytest.raises(ChangeError):
        apply_change(session, [999_999], category_id=category_id(session, "travel"))


def test_nature_change_gets_a_category(session):
    load(session)
    split = split_of(session, "어떤사람")
    apply_change(session, [split.id], nature="expense")
    assert split.category_id == category_id(session, "uncategorized_expense")


def test_rule_applies_on_reclassify_and_future_imports(session):
    load(session)
    rule = Rule(
        name="월세",
        match_description_contains="어떤사람",
        set_nature="expense",
        set_category_id=category_id(session, "housing"),
    )
    session.add(rule)
    reclassify(session)
    split = split_of(session, "어떤사람")
    assert split.nature == "expense"
    assert split.nature_source == "rule"
    assert split.category_source == "rule"

    newer = [*LEDGER, fx.tx("2026-03-01 10:00:00", "이체", "이체", "미분류", "어떤사람", -70_000, BANK)]
    load(session, rows=newer, inner_name="2026-01-15~2026-03-02.xlsx")
    categories = {s.category_id for s in session.scalars(select(TxnSplit).join(Txn).where(Txn.description == "어떤사람"))}
    assert categories == {category_id(session, "housing")}


def test_user_change_beats_rule(session):
    load(session)
    split = split_of(session, "어떤사람")
    travel = category_id(session, "travel")
    apply_change(session, [split.id], nature="expense", category_id=travel)
    session.add(
        Rule(
            name="월세",
            match_description_contains="어떤사람",
            set_nature="internal",
            set_category_id=category_id(session, "housing"),
        )
    )
    reclassify(session)
    assert (split.nature, split.category_id) == ("expense", travel)


def test_disabling_a_rule_returns_to_default(session):
    load(session)
    rule = Rule(name="월세", match_description_contains="어떤사람", set_nature="expense")
    session.add(rule)
    reclassify(session)
    rule.enabled = False
    reclassify(session)
    assert split_of(session, "어떤사람").nature == "undetermined"


def test_reclassify_is_idempotent(session):
    load(session)
    assert reclassify(session) == 0


# --- 검토함


def test_review_queues_sort_by_amount(session):
    load(session)
    undetermined = reports.review_undetermined(session)
    assert [item.description for item in undetermined] == ["어떤사람"]

    inflows = reports.review_large_inflows(session)
    assert [item.description for item in inflows] == ["적금 해지"]

    largest = [item.amount for item in reports.review_largest_spend(session)]
    assert largest == sorted(largest, key=abs, reverse=True)
    assert largest[0] == -40_000


def test_data_status_reports_as_of_and_quarantine(session):
    load(session)
    import_export(session, export_file(inner_name="2026-01-15~2026-03-05.xlsx"), ["1111"])
    status = reports.data_status(session)
    assert status.data_as_of == date(2026, 2, 28)
    assert status.quarantined == 1
