"""정기 납입을 대출과 부동산에 잇고, 납입액과 잔액으로 금리를 거꾸로 구한다."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.ingest.importer import import_export
from app.ledger import reports
from app.ledger.changes import apply_change
from app.ledger.models import ManualAsset, ManualAssetValue, Rule, Txn, TxnSplit  # noqa: F401
from app.ledger.payment_links import (
    LINKS_KEY,
    AssetOption,
    LoanOption,
    Part,
    Payment,
    guess_link,
    implied_rate,
    links_of,
    loan_parts,
)
from app.ops import state
from fixtures import make_export as fx
from test_pages import BANK, PASSWORD, chart_data, needs_node, run_chart_js

LOAN = "liability:장기대출:전세자금대출"
OTHER_LOAN = "liability:신용대출:직장인 신용대출"
RENT_ASSET_NAME = "우리 집"
PAYMENT = "국민은행 대출이자"
LEDGER = [
    fx.tx(f"2026-{month:02d}-25 10:00:00", "이체", "이체", "미분류", f"{month}월 {PAYMENT}", -12_500, BANK) for month in range(5, 11)
] + [
    fx.tx(f"2026-{month:02d}-05 10:00:00", "이체", "이체", "미분류", "집주인", -500_000, BANK) for month in range(5, 11)
] + [
    fx.tx(f"2026-{month:02d}-10 10:00:00", "수입", "기타수입", "미분류", "되돌려받음", 3_000, BANK) for month in range(5, 11)
]


def export(session, day: date, debts: list[fx.AssetRow], rows=None, assets=None, rates=None) -> None:
    """rates는 대출 현황 표에 적을 상품별 금리다."""
    subset = [row for row in (LEDGER if rows is None else rows) if row[0].date() <= day]
    orgs = {"전세자금대출": "국민은행", "직장인 신용대출": "신한은행"}
    tables = [fx.loan_row(debt.group, orgs[debt.product], debt.product, debt.amount, (rates or {}).get(debt.product)) for debt in debts]
    sheet = fx.build_xlsx(
        rows=subset,
        assets=assets if assets is not None else [fx.AssetRow("자유입출금 자산", BANK, 1_000_000)],
        liabilities=debts,
        loans=[*fx.LOANS, *tables],
    )
    result = import_export(session, fx.zip_aes(sheet, PASSWORD, f"2026-01-01~{day}.xlsx"), [PASSWORD])
    assert result.status == "applied", result


@pytest.fixture
def client(signed_in, session):
    """잔액 300만원인 대출의 기록이 두 번 있고, 그 사이에 이자를 다섯 번 냈다."""
    browser, _, _ = signed_in
    debts = [fx.AssetRow("장기대출", "전세자금대출", 3_000_000), fx.AssetRow("신용대출", "직장인 신용대출", 500_000)]
    export(session, date(2026, 5, 31), debts)
    export(session, date(2026, 10, 31), debts)
    return browser


def link(client, description=PAYMENT, **fields):
    return client.post("/recurring/links", form_page="/recurring", description=description, **fields)


def loan_payments(client) -> dict:
    return chart_data(client)["loan_payments"]


# --- 금리 계산


def monthly(start: date, count: int, amount: int) -> dict[date, int]:
    return {start + timedelta(days=30 * index): amount for index in range(count)}


def test_interest_only_loan_gives_the_rate_directly():
    balances = [(date(2025, 10, 1), 120_000_000), (date(2026, 10, 1), 120_000_000)]
    result = implied_rate(balances, monthly(date(2025, 10, 10), 12, 385_000))
    assert result.rate == 3.85
    assert result.per_payment == 385_000
    assert result.count == 12


def test_repaid_principal_is_taken_out_of_the_payments():
    balances = [(date(2026, 7, 1), 52_000_000), (date(2026, 10, 1), 50_000_000)]
    payments = {date(2026, 7, 25): 800_000, date(2026, 8, 25): 800_000, date(2026, 9, 25): 800_000}
    result = implied_rate(balances, payments)
    assert result.rate == 3.14  # 이자 40만원을 세 번에 나눠 평균 잔액 5,100만원으로 나눈다
    assert result.per_payment == 133_333


def test_export_days_that_miss_the_payment_day_do_not_shake_the_rate():
    # 71일 사이에 납입이 세 번 들어온다. 날짜로 나누면 금리가 3할쯤 높게 나온다.
    balances = [(date(2026, 8, 20), 120_000_000), (date(2026, 10, 30), 120_000_000)]
    payments = {date(2026, 8, 25): 385_000, date(2026, 9, 25): 385_000, date(2026, 10, 25): 385_000}
    assert implied_rate(balances, payments).rate == 3.85


def test_two_records_are_needed():
    payments = monthly(date(2026, 1, 1), 3, 100_000)
    assert implied_rate([], payments).reason == "records"
    assert implied_rate([(date(2026, 3, 1), 1_000_000)], payments).reason == "records"


def test_payments_are_needed():
    balances = [(date(2026, 1, 1), 1_000_000), (date(2026, 3, 1), 1_000_000)]
    assert implied_rate(balances, {}).reason == "payments"
    assert implied_rate(balances, {date(2025, 1, 1): 5_000}).reason == "payments"
    assert implied_rate(balances, {date(2026, 4, 1): 5_000}).reason == "records"  # 납입 뒤의 잔액 기록을 기다린다


def test_new_borrowing_stops_the_window():
    balances = [
        (date(2026, 1, 1), 1_000_000),
        (date(2026, 2, 1), 500_000),
        (date(2026, 3, 1), 3_000_000),  # 새로 빌렸다
        (date(2026, 5, 1), 3_000_000),
    ]
    payments = {date(2026, 1, 20): 600_000, date(2026, 3, 20): 12_500, date(2026, 4, 20): 12_500}
    result = implied_rate(balances, payments)
    assert (result.rate, result.count) == (5.0, 2)


def test_balance_fall_before_the_account_was_connected_is_ignored():
    balances = [
        (date(2026, 1, 1), 3_000_000),
        (date(2026, 2, 1), 2_000_000),  # 이때까지의 납입 기록이 없다
        (date(2026, 5, 1), 2_000_000),
    ]
    payments = {date(2026, 3, 1): 10_000, date(2026, 4, 1): 10_000, date(2026, 5, 1): 10_000}
    assert implied_rate(balances, payments).rate == 6.0


def test_only_the_recent_year_counts():
    balances = [(date(2024, 1, 1), 2_000_000), (date(2026, 1, 1), 1_000_000), (date(2026, 7, 1), 1_000_000)]
    payments = {date(2024, 6, 1): 500_000, date(2026, 3, 1): 5_000, date(2026, 5, 1): 5_000}
    assert implied_rate(balances, payments).count == 2


def test_quarterly_payments_count_four_times_a_year():
    balances = [(date(2025, 1, 1), 1_000_000), (date(2026, 1, 1), 1_000_000)]
    payments = {date(2025, 3, 1): 10_000, date(2025, 6, 1): 10_000, date(2025, 9, 1): 10_000, date(2025, 12, 1): 10_000}
    assert implied_rate(balances, payments).rate == 4.0


def test_money_that_does_not_fit_gives_no_rate():
    balances = [(date(2026, 1, 1), 1_000_000), (date(2026, 4, 1), 1_000_000)]
    assert implied_rate(balances, monthly(date(2026, 1, 15), 2, 500_000)).reason == "range"  # 다른 돈이 섞였다
    fallen = [(date(2026, 1, 1), 1_000_000), (date(2026, 4, 1), 500_000)]
    assert implied_rate(fallen, monthly(date(2026, 1, 15), 2, 10_000)).reason == "range"  # 다른 이름으로 갚았다


# --- 연결하기


def test_link_form_appears_for_money_going_out_only(client):
    html = client.client.get("/recurring").text
    assert 'action="/recurring/links"' in html
    assert f'value="{LOAN}"' in html
    assert html.count('action="/recurring/links"') == 2  # 대출이자와 집주인. 받은 돈 묶음에는 없다
    assert "대출에 연결" in html  # 부동산이나 연금이 없으면 고를 수 있는 것만 적는다
    review = client.client.get("/review?queue=undetermined").text
    assert 'action="/recurring/links"' in review


def test_saving_a_link_shows_it_and_gives_the_rate(client, session):
    response = link(client, loan=LOAN)
    assert response.status_code == 303 and "msg=link_split" in response.headers["location"]
    saved = links_of(session)
    assert [(item.loan, item.asset) for item in saved] == [(LOAN, None)]
    assert "전세자금대출에 연결" in client.client.get("/recurring").text
    assert "이자는 지출, 원금은 대출로 나눴습니다." in client.client.get(response.headers["location"]).text
    entry = loan_payments(client)[LOAN]
    assert entry["reason"] is None
    assert entry["implied"]["rate"] == 5.0  # 12,500원 × 12 ÷ 300만원
    assert entry["implied"]["count"] == 5
    assert entry["payments"] == [{"name": PAYMENT, "typical": 12_500, "times": 6, "kind": None}]
    # 잔액이 그대로인 대출이라 납입이 모두 이자다. 첫 기록보다 앞선 5월은 금리로 어림한다.
    assert entry["months"][0] == {"month": "2026-05", "interest": 12_500, "principal": 0, "exact": False}
    assert entry["months"][-1] == {"month": "2026-10", "interest": 12_500, "principal": 0, "exact": True}


def spend_of(session, month: str) -> int:
    return next(item for item in reports.monthly_summary(session) if item.month == month).spend_total


def test_one_click_links_and_classifies_a_group_still_undecided(client, session):
    before = spend_of(session, "2026-10")
    link(client, loan=LOAN)
    assert spend_of(session, "2026-10") == before + 12_500
    html = client.client.get("/recurring").text
    start = html.index(f"<strong>{PAYMENT}</strong>")
    assert "분류 필요" not in html[start : html.index("</p>", start)]
    rule = session.scalars(select(Rule).where(Rule.match_description_equals == PAYMENT)).one()
    assert (rule.set_nature, rule.match_amount_max) == ("expense", 62_500)  # 같은 곳으로 간 큰돈은 걸리지 않는다


def test_link_splits_a_group_decided_before(client, session):
    client.post("/recurring/rules", form_page="/recurring", description=PAYMENT, direction="out", nature="debt_move")
    before = spend_of(session, "2026-10")
    response = link(client, loan=LOAN)
    assert "msg=link_split" in response.headers["location"]
    assert spend_of(session, "2026-10") == before + 12_500  # 잔액이 그대로인 대출이라 납입이 모두 이자다
    rule = session.scalars(select(Rule).where(Rule.match_description_equals == PAYMENT)).one()
    assert rule.set_nature == "debt_move"  # 규칙은 그대로 두고, 금리를 모르는 납입에만 쓰인다


# --- 납입을 이자와 원금으로 나누기

AMORTIZING = [
    fx.tx(f"2026-{month:02d}-25 10:00:00", "이체", "이체", "미분류", PAYMENT, -112_500, BANK) for month in range(5, 11)
]


def amortizing(session, records: list[tuple[date, int]], rates=None) -> None:
    for day, balance in records:
        export(session, day, [fx.AssetRow("장기대출", "전세자금대출", balance)], rows=AMORTIZING, rates=rates)


def splits_on(session, day: date) -> list[tuple[int, str, str]]:
    rows = session.execute(
        select(TxnSplit.amount, TxnSplit.nature, TxnSplit.nature_reason)
        .join(Txn, Txn.id == TxnSplit.txn_id)
        .where(Txn.occurred_date == day, Txn.source_amount < 0, Txn.description.contains("대출이자"))
        .order_by(TxnSplit.seq)
    )
    return [tuple(row) for row in rows]


def test_each_payment_is_split_into_interest_and_principal(signed_in, session):
    browser, _, _ = signed_in
    amortizing(session, [(date(2026, 6, 30), 3_000_000), (date(2026, 10, 31), 2_600_000)])
    before = spend_of(session, "2026-10")
    response = link(browser, loan=LOAN)
    assert "msg=link_split" in response.headers["location"]
    assert spend_of(session, "2026-10") == before + 12_500  # 원금 10만원은 지출에 들어가지 않는다
    assert splits_on(session, date(2026, 10, 25)) == [(-12_500, "expense", "loan_interest"), (-100_000, "debt_move", "loan_principal")]
    # 첫 기록보다 앞선 납입은 금리 5.36%로 어림한다. 갚은 원금만큼 그때 잔액이 더 커서 이자도 조금 더 크다.
    assert splits_on(session, date(2026, 5, 25)) == [
        (-14_279, "expense", "loan_interest_estimate"),
        (-98_221, "debt_move", "loan_principal_estimate"),
    ]
    months = loan_payments(browser)[LOAN]["months"]
    assert [(item["month"], item["interest"], item["exact"]) for item in months] == [
        ("2026-05", 14_279, False),
        ("2026-06", 13_841, False),
        ("2026-07", 12_500, True),
        ("2026-08", 12_500, True),
        ("2026-09", 12_500, True),
        ("2026-10", 12_500, True),
    ]
    html = browser.client.get("/recurring").text
    start = html.index(f"<strong>{PAYMENT}</strong>")
    assert "이자는 지출, 원금은 대출" in html[start : html.index("</small>", start)]
    lines = browser.client.get("/transactions", params={"q": PAYMENT}).text
    assert "지출 · 세금·공과금 · 이자" in lines and "대출 · 원금" in lines


def test_new_export_splits_the_payments_without_another_click(signed_in, session):
    browser, _, _ = signed_in
    amortizing(session, [(date(2026, 6, 30), 3_000_000)])
    link(browser, loan=LOAN)
    assert len(splits_on(session, date(2026, 6, 25))) == 1  # 기록이 하나이고 표에 금리가 없어 아직 나누지 못한다
    amortizing(session, [(date(2026, 10, 31), 2_600_000)])
    assert splits_on(session, date(2026, 10, 25)) == [(-12_500, "expense", "loan_interest"), (-100_000, "debt_move", "loan_principal")]


def test_unlinking_puts_each_payment_back_in_one_piece(signed_in, session):
    browser, _, _ = signed_in
    amortizing(session, [(date(2026, 6, 30), 3_000_000), (date(2026, 10, 31), 2_600_000)])
    link(browser, loan=LOAN)
    before = spend_of(session, "2026-10")
    assert "msg=link_removed" in link(browser).headers["location"]
    assert [(amount, nature) for amount, nature, _ in splits_on(session, date(2026, 10, 25))] == [(-112_500, "expense")]
    assert spend_of(session, "2026-10") == before + 100_000  # 연결할 때 정한 지출 규칙을 다시 따른다


def test_payment_changed_by_hand_is_left_alone(signed_in, session):
    browser, _, _ = signed_in
    amortizing(session, [(date(2026, 6, 30), 3_000_000), (date(2026, 10, 31), 2_600_000)])
    link(browser, loan=LOAN)
    interest = session.scalars(
        select(TxnSplit.id).join(Txn, Txn.id == TxnSplit.txn_id).where(Txn.occurred_date == date(2026, 10, 25), TxnSplit.nature == "expense")
    ).one()
    apply_change(session, [interest], nature="debt_move")
    link(browser)
    assert splits_on(session, date(2026, 10, 25))[1] == (-100_000, "debt_move", "loan_principal")  # 직접 고친 거래는 나눈 그대로다
    assert len(splits_on(session, date(2026, 9, 25))) == 1


def test_one_record_splits_by_the_table_rate(signed_in, session):
    browser, _, _ = signed_in
    amortizing(session, [(date(2026, 10, 31), 3_000_000)], rates={"전세자금대출": 4.0})
    link(browser, loan=LOAN)
    # 잔액 기록이 하나라 납입액으로는 금리를 못 구해 표의 4.0%로 어림한다. 10월 납입 전 잔액은 약 310만원이다.
    assert splits_on(session, date(2026, 10, 25)) == [
        (-10_341, "expense", "loan_interest_estimate"),
        (-102_159, "debt_move", "loan_principal_estimate"),
    ]


def test_without_any_rate_the_payment_stays_whole(signed_in, session):
    browser, _, _ = signed_in
    amortizing(session, [(date(2026, 10, 31), 3_000_000)])
    assert "msg=link_spend" in link(browser, loan=LOAN).headers["location"]
    assert [nature for _, nature, _ in splits_on(session, date(2026, 10, 25))] == ["expense"]
    assert loan_payments(browser)[LOAN]["months"] == []


def test_interest_between_two_records_is_shared_by_the_payments_in_between():
    balances = [(date(2026, 1, 31), 1_000_000), (date(2026, 3, 31), 900_000)]
    payments = [Payment(1, date(2026, 2, 25), 55_000), Payment(2, date(2026, 3, 25), 55_000)]
    assert loan_parts(balances, payments, None) == {1: Part(5_000, True), 2: Part(5_000, True)}  # 11만원 - 10만원


def test_share_never_goes_over_a_payment():
    balances = [(date(2026, 1, 31), 1_000_000), (date(2026, 3, 31), 814_000)]
    payments = [Payment(1, date(2026, 2, 25), 100_000), Payment(2, date(2026, 3, 1), 1_000), Payment(3, date(2026, 3, 25), 100_000)]
    assert loan_parts(balances, payments, None) == {1: Part(7_000, True), 2: Part(1_000, True), 3: Part(7_000, True)}


def test_payments_outside_the_records_are_estimated_from_the_rate():
    balances = [(date(2026, 3, 31), 1_200_000)]
    payments = [Payment(1, date(2026, 2, 25), 50_000), Payment(2, date(2026, 4, 25), 50_000)]
    # 기록 뒤의 납입은 기록의 잔액 120만원으로, 앞의 납입은 그 납입 전 잔액 약 124만원으로 어림한다.
    assert loan_parts(balances, payments, 5.0) == {1: Part(5_187, False), 2: Part(5_000, False)}
    assert loan_parts(balances, payments, None) == {}  # 금리를 모르면 나누지 않는다


def test_interest_only_loan_estimates_the_whole_payment_as_interest():
    balances = [(date(2026, 3, 31), 3_000_000)]
    payments = [Payment(1, date(2026, 1, 25), 12_500), Payment(2, date(2026, 2, 25), 12_500)]
    assert loan_parts(balances, payments, 5.0) == {1: Part(12_500, False), 2: Part(12_500, False)}


def test_window_far_from_the_rate_is_estimated():
    # 내보낸 날 잔액에 그날 납입이 아직 반영되지 않아 앞 구간은 이자가 크고 뒤 구간은 작게 나온다.
    balances = [(date(2026, 1, 25), 300_000_000), (date(2026, 2, 25), 300_000_000), (date(2026, 3, 25), 299_000_000)]
    payments = [Payment(1, date(2026, 2, 25), 1_500_000), Payment(2, date(2026, 3, 25), 1_500_000)]
    assert loan_parts(balances, payments, 4.0) == {1: Part(1_000_000, False), 2: Part(1_000_000, False)}


def test_new_borrowing_is_estimated_from_the_balance_before():
    balances = [(date(2026, 1, 31), 1_000_000), (date(2026, 2, 28), 3_000_000)]
    assert loan_parts(balances, [Payment(1, date(2026, 2, 25), 10_000)], 6.0) == {1: Part(5_000, False)}


def test_empty_link_removes_it(client, session):
    link(client, loan=LOAN)
    response = link(client)
    assert "msg=link_removed" in response.headers["location"]
    assert links_of(session) == []
    assert loan_payments(client) == {}


def test_saving_again_replaces_the_link(client, session):
    link(client, loan=LOAN)
    link(client, loan=OTHER_LOAN)
    assert [item.loan for item in links_of(session)] == [OTHER_LOAN]


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"description": PAYMENT, "loan": "liability:없는 항목:없는 대출"}, "error=link"),
        ({"description": PAYMENT, "asset": "manual:999999"}, "error=link"),
        ({"description": PAYMENT, "asset": "asset:자유입출금 자산:" + BANK}, "error=link"),
        ({"description": "없는 상대", "loan": LOAN}, "error=group"),
        ({"description": "되돌려받음", "loan": LOAN}, "error=group"),  # 받은 돈은 납입이 아니다
    ],
)
def test_link_refuses_what_does_not_belong(client, session, fields, error):
    response = client.post("/recurring/links", form_page="/recurring", **fields)
    assert error in response.headers["location"]
    assert links_of(session) == []


def test_link_needs_a_csrf_token_and_goes_back_only_to_known_pages(client):
    assert client.client.post("/recurring/links", data={"description": PAYMENT, "loan": LOAN}).status_code == 403
    response = link(client, loan=LOAN, back="https://example.com/")
    assert response.headers["location"].startswith("/recurring")
    from_review = link(client, loan=LOAN, back="/review?queue=undetermined")
    assert from_review.headers["location"].startswith("/review")


def test_saved_links_ignore_a_loan_that_is_gone(client, session, clock):
    state.set_state(session, clock(), LINKS_KEY, {"links": [{"key": "대출이자", "name": PAYMENT, "loan": "liability:옛날:대출", "asset": None}]})
    entry = loan_payments(client)["liability:옛날:대출"]
    assert entry["implied"] is None and entry["reason"] == "records"
    html = client.client.get("/recurring").text
    assert "에 연결" in html  # 사라진 대출이어도 화면은 멈추지 않는다


def test_rate_waits_for_a_second_record(signed_in, session):
    browser, _, _ = signed_in
    export(session, date(2026, 10, 31), [fx.AssetRow("장기대출", "전세자금대출", 3_000_000)])
    link(browser, loan=LOAN)
    entry = loan_payments(browser)[LOAN]
    assert entry["implied"] is None and entry["reason"] == "records"


def test_several_payments_on_one_loan_are_added(client, session):
    link(client, loan=LOAN)
    link(client, description="집주인", loan=LOAN)
    entry = loan_payments(client)[LOAN]
    assert [item["name"] for item in entry["payments"]] == [PAYMENT, "집주인"]
    assert entry["implied"] is None and entry["reason"] == "range"  # 금리가 30%를 넘으면 다른 돈이 섞인 것이다
    # 이자와 원금을 따로 내는 경우가 많아 나누지 않고 묶음마다 분류를 정하게 둔다.
    assert entry["months"] == []
    assert len(splits_on(session, date(2026, 10, 25))) == 1


# --- 분류를 먼저 골라 두기


def selected_nature(html: str, description: str) -> str:
    start = html.index(f'name="description" value="{description}"')
    form = html[start : html.index("</form>", start)]
    return form[form.index('name="nature"') :].split(" selected>")[0].rsplit('value="', 1)[1].split('"')[0]


def selected_category(html: str, description: str) -> str | None:
    start = html.index(f'name="description" value="{description}"')
    form = html[start : html.index("</form>", start)]
    begin = form.index('name="category"')
    found = re.search(r" selected>([^<]+)<", form[begin : form.index("</select>", begin)])
    return found.group(1) if found else None


def test_loan_link_keeps_interest_in_spending(client):
    link(client, loan=LOAN)
    html = client.client.get("/recurring").text
    assert selected_nature(html, PAYMENT) == "expense"
    assert selected_category(html, PAYMENT) == "세금·공과금"  # 뱅크샐러드가 지출/금융으로 표시한 납입과 같은 곳이다


def test_housing_link_preselects_housing(client, session):
    asset = rented_home(session)
    link(client, loan=LOAN, asset=f"manual:{asset.id}")
    link(client, description="집주인", asset=f"manual:{asset.id}")
    html = client.client.get("/recurring").text
    assert selected_nature(html, PAYMENT) == "expense"
    assert selected_category(html, PAYMENT) == "주거비"
    assert selected_nature(html, "집주인") == "expense"
    assert selected_category(html, "집주인") == "주거비"
    assert "매달 5일쯤" in html


def test_link_to_a_home_keeps_a_kind_already_decided(client, session):
    response = client.post(
        "/recurring/rules", form_page="/recurring", description=PAYMENT, direction="out", nature="debt_move"
    )
    assert "msg=rule_saved" in response.headers["location"]
    asset = rented_home(session)
    link(client, asset=f"manual:{asset.id}")  # 부동산에만 이으면 납입을 나누지 않는다
    assert selected_nature(client.client.get("/recurring").text, PAYMENT) == "debt_move"


# --- 부동산


def rented_home(session) -> ManualAsset:
    asset = ManualAsset(kind="real_estate", name=RENT_ASSET_NAME, tenure="jeonse", acquired_on=date(2025, 1, 1))
    asset.values.append(ManualAssetValue(as_of=date(2025, 1, 1), amount=250_000_000, source="user"))
    session.add(asset)
    session.flush()
    return asset


def test_home_shows_its_loan_and_payment(client, session):
    asset = rented_home(session)
    link(client, loan=LOAN, asset=f"manual:{asset.id}")
    products = chart_data(client)["asset_products"]["asset:보증금 (직접 입력)"][0]
    facts = {fact["label"]: fact for fact in products["facts"]}
    assert facts["연결된 대출"]["value"] == "전세자금대출"
    assert facts["연결된 대출"]["note"] == "잔액 300만원"
    assert facts["연결된 납입"] == {"label": "연결된 납입", "value": PAYMENT, "note": "12,500원 · 6회"}
    assert f"{RENT_ASSET_NAME}" in client.client.get("/recurring").text


def test_sold_home_cannot_be_linked(client, session):
    asset = rented_home(session)
    asset.sold_on = date(2026, 1, 1)
    session.flush()
    response = link(client, asset=f"manual:{asset.id}")
    assert "error=link" in response.headers["location"]


def test_exported_real_estate_can_be_linked(signed_in, session):
    browser, _, _ = signed_in
    assets = [fx.AssetRow("자유입출금 자산", BANK, 1_000_000), fx.AssetRow("부동산", "아파트", 300_000_000)]
    debts = [fx.AssetRow("장기대출", "전세자금대출", 3_000_000)]
    export(session, date(2026, 10, 31), debts, assets=assets)
    response = link(browser, loan=LOAN, asset="asset:부동산:아파트")
    assert "error" not in response.headers["location"]
    entry = chart_data(browser)["asset_products"]["asset:부동산"][0]
    assert entry["hint"] == f"연결된 대출 전세자금대출 · 연결된 납입 {PAYMENT}"


# --- 연금


PENSION = "asset:연금 자산:개인형 IRP"
PENSION_ROWS = [
    fx.tx(f"2026-{month:02d}-25 10:00:00", "이체", "이체", "미분류", "개인형 IRP 납입", -300_000, BANK) for month in range(5, 11)
] + [
    fx.tx(f"2026-{month:02d}-27 10:00:00", "이체", "이체", "미분류", "개인형 IRP 연금", 50_000, BANK) for month in range(5, 11)
]


@pytest.fixture
def pension_client(signed_in, session):
    """연금 잔액 기록이 두 번 있고, 그 사이에 넣은 돈과 받은 돈이 있다."""
    browser, _, _ = signed_in
    for day, balance in ((date(2026, 5, 31), 5_000_000), (date(2026, 10, 31), 6_800_000)):
        assets = [fx.AssetRow("자유입출금 자산", BANK, 1_000_000), fx.AssetRow("연금 자산", "개인형 IRP", balance)]
        export(session, day, [], rows=PENSION_ROWS, assets=assets)
    return browser


def test_money_in_and_out_can_be_linked_to_a_pension(pension_client, session):
    html = pension_client.client.get("/recurring").text
    assert html.count('action="/recurring/links"') == 2  # 넣는 돈과 받는 돈 모두 연금에 이을 수 있다
    assert "개인형 IRP 납입 같음" in html and "개인형 IRP 수령 같음" in html
    out = link(pension_client, description="개인형 IRP 납입", pension=PENSION)
    received = link(pension_client, description="개인형 IRP 연금", direction="in", pension=PENSION)
    assert "msg=link_saving" in out.headers["location"] and "msg=link_saving" in received.headers["location"]
    assert sorted((item.direction, item.pension) for item in links_of(session)) == [("in", PENSION), ("out", PENSION)]
    # 내 계좌와 연금 사이를 오간 돈이라 지출이나 수입이 아니다.
    assert {split.nature for split in session.scalars(select(TxnSplit))} == {"asset_move"}


def test_pension_shows_what_went_in_came_out_and_grew(pension_client):
    link(pension_client, description="개인형 IRP 납입", pension=PENSION)
    link(pension_client, description="개인형 IRP 연금", direction="in", pension=PENSION)
    entry = chart_data(pension_client)["pension_links"][PENSION]
    assert (entry["contributed"], entry["received"]) == (1_800_000, 300_000)
    # 잔액은 180만원 늘었고 그사이 150만원을 넣고 25만원을 받았다.
    assert entry["growth"] == {"from": "2026-05-31", "to": "2026-10-31", "amount": 550_000}
    assert [item["direction"] for item in entry["payments"]] == ["out", "in"]
    hint = chart_data(pension_client)["asset_products"]["asset:연금 자산"][0]["hint"]
    assert hint == "연결된 납입 개인형 IRP 납입 · 연결된 수령 개인형 IRP 연금"


@pytest.mark.parametrize(
    "fields",
    [
        {"description": "개인형 IRP 연금", "direction": "in", "loan": LOAN},  # 받은 돈은 연금에만 잇는다
        {"description": "개인형 IRP 납입", "pension": PENSION, "asset": "asset:부동산:아파트"},
        {"description": "개인형 IRP 납입", "pension": "asset:연금 자산:없는 연금"},
    ],
)
def test_pension_link_refuses_what_does_not_fit(pension_client, session, fields):
    assert "error=link" in link(pension_client, **fields).headers["location"]
    assert links_of(session) == []


def test_money_in_has_no_link_form_without_a_pension(client):
    html = client.client.get("/recurring").text
    start = html.index("<strong>되돌려받음</strong>")
    assert 'action="/recurring/links"' not in html[start : html.index("</li>", start)]


# --- 어디에 내는 돈인지 짐작하기


LOANS = [LoanOption("loan:home", "전세자금대출", "국민은행"), LoanOption("loan:credit", "직장인 신용대출", "신한은행")]
HOMES = [AssetOption("manual:1", "우리 집")]


@pytest.mark.parametrize(
    ("description", "loan"),
    [
        ("전세 자금 대출", "loan:home"),  # 상품 이름이 그대로 있다
        ("국민은행 대출이자", "loan:home"),  # 금융사와 대출이라는 말
        ("국민은행 이자", "loan:home"),  # 금융사가 있으면 이자라는 말도 믿는다
        ("신한은행 원리금", "loan:credit"),
        ("신한 상환", "loan:credit"),
        ("이자카야", None),  # 이자라는 말만으로는 짐작하지 않는다
        ("대출", None),  # 대출이 둘이면 어느 쪽인지 모른다
        ("국민은행", None),  # 금융사만으로는 예금으로 보낸 돈일 수 있다
        ("집주인", None),
    ],
)
def test_loan_is_guessed_from_the_description(description, loan):
    assert guess_link(description, 10_000, LOANS, []).loan == loan


def test_english_part_of_the_bank_name_is_not_needed():
    loans = [LoanOption("loan:kb", "주택자금", "KB국민은행"), LoanOption("loan:nh", "신용", "NH농협은행")]
    assert guess_link("국민은행 대출이자", 10_000, loans, []).loan == "loan:kb"
    assert guess_link("농협 원리금", 10_000, loans, []).loan == "loan:nh"


def test_single_loan_is_guessed_from_the_word_alone():
    assert guess_link("대출 납입", 10_000, LOANS[:1], []).loan == "loan:home"


def test_home_is_guessed_from_rent_or_a_housing_loan():
    rented = [AssetOption("manual:1", "우리 집", 500_000), AssetOption("manual:2", "다른 집")]
    assert guess_link("집주인", 550_000, [], rented).asset == "manual:1"
    assert guess_link("집주인", 100_000, [], rented).found is False
    assert guess_link("국민은행 대출이자", 10_000, LOANS, HOMES).asset == "manual:1"  # 전세자금대출은 주택 대출이다
    assert guess_link("신한은행 대출이자", 10_000, LOANS, HOMES).asset is None
    assert guess_link("국민은행 대출이자", 10_000, LOANS, rented).asset is None  # 집이 둘이면 어느 쪽인지 모른다


def test_guess_opens_the_form_with_the_loan_chosen_but_does_not_link(client, session):
    html = client.client.get("/recurring").text
    start = html.index(f"<strong>{PAYMENT}</strong>")
    item = html[start : html.index("</li>", html.index('action="/recurring/links"', start))]
    assert "전세자금대출 납입 같음" in item
    assert '<details class="fix" open>' in item
    assert f'<option value="{LOAN}" selected>' in item
    assert "연결하고 분류" in item
    assert links_of(session) == []


def connect_section(client, loan: str) -> str:
    html = client.client.get("/recurring", params={"loan": loan}).text
    return html[html.index("에 이을 납입") : html.index('aria-label="반복 거래 종류"')]


def test_loan_page_lists_payments_and_returns_to_the_loan(client, session):
    top = connect_section(client, LOAN)
    assert top.index(PAYMENT) < top.index("집주인")  # 내용이 맞는 것이 먼저다
    assert top.count("button primary") == 1  # 짐작이 맞는 묶음만 먼저 권한다
    assert "되돌려받음" not in top  # 받은 돈은 납입이 아니다
    response = client.post(
        "/recurring/links",
        form_page="/recurring",
        description=PAYMENT,
        loan=LOAN,
        back=f"/chart?tab=finance&focus={LOAN}",
    )
    location = response.headers["location"]
    assert location.startswith("/chart?") and "tab=finance" in location
    assert [item.loan for item in links_of(session)] == [LOAN]
    assert PAYMENT not in connect_section(client, OTHER_LOAN)  # 대출에 이은 납입은 뺀다


def test_loan_page_leaves_out_money_paid_for_something_else(signed_in, session):
    browser, _, _ = signed_in
    others = [
        fx.tx(f"2026-{month:02d}-{day:02d} 10:00:00", "이체", "이체", "미분류", name, -amount, BANK)
        for month in range(5, 11)
        for day, name, amount in ((6, "적금 자동이체", 900_000), (7, "수학학원", 800_000), (8, "보험료", 700_000))
    ]
    export(session, date(2026, 10, 31), [fx.AssetRow("장기대출", "전세자금대출", 3_000_000)], rows=[*LEDGER, *others])
    top = connect_section(browser, LOAN)
    assert PAYMENT in top and "집주인" in top
    assert not any(name in top for name in ("적금 자동이체", "수학학원", "보험료"))


def test_loan_page_keeps_a_home_already_linked(client, session):
    asset = rented_home(session)
    home = f"manual:{asset.id}"
    link(client, description="집주인", asset=home)
    top = connect_section(client, LOAN)
    start = top.index('value="집주인"')
    assert f'name="asset" value="{home}"' in top[start : top.index("</form>", start)]


def test_unknown_loan_shows_the_plain_page(client):
    assert "에 이을 납입" not in client.client.get("/recurring", params={"loan": "nope"}).text


# --- 화면에 보여 줄 금리


@needs_node
def test_table_rate_comes_first_and_payments_fill_the_gap():
    result = run_chart_js(rates=[[3.85, None], [None, 3.9], [None, None], [3.85, 3.9], [0.0385, 3.9], [0.5, 0.52]])["rates"]
    assert result[0] == {"value": 3.85, "source": "table"}
    assert result[1] == {"value": 3.9, "source": "payments"}
    assert result[2] == {"value": None, "source": None}
    assert result[3] == {"value": 3.85, "source": "table"}
    assert result[4] == {"value": 3.85, "source": "table"}  # 0.0385로 들어와도 납입액과 비교해 알아낸다
    assert result[5] == {"value": 0.5, "source": "table"}  # 정말 0.5%면 그대로다


@needs_node
def test_finance_loans_carry_the_rate_and_the_payments(client):
    link(client, loan=LOAN)
    finance = run_chart_js(chart_data(client), figures=[{"tab": "finance"}])["figures"][0]["finance"]
    loans = {item["key"]: item["loan"] for item in finance["debts"]}
    linked = loans[LOAN]
    assert linked["rate"] == 5.0 and linked["rateSource"] == "payments"
    assert linked["payments"][0]["name"] == PAYMENT
    assert linked["balance"] == 3_000_000
    other = loans[OTHER_LOAN]
    assert other["rate"] is None and other["payments"] == []
    # 현황 표의 대출은 재무현황과 이름이 달라 따로 보인다.
    assert "비밀대출상품" in [loan["name"] for loan in finance["loans"]]
