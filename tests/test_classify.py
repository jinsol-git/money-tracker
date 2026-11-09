"""성격 판단 규칙. 실제 파일에서 확인한 행 표현을 그대로 시나리오로 쓴다."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.ledger.classify import (
    CardView,
    CategoryLookup,
    RowView,
    RuleView,
    classify_rows,
    company_of,
    detect_pairs,
    infer_instrument_kind,
)

ACCOUNT, MONEY, CASH, CARD, OTHER_ACCOUNT, PAY = 1, 2, 3, 4, 5, 6
KINDS = {
    ACCOUNT: "account",
    MONEY: "pay_balance",
    CASH: "cash",
    CARD: "credit_card",
    OTHER_ACCOUNT: "account",
    PAY: "pay_usage",
}
LOOKUP = CategoryLookup(
    map={
        ("지출", "식비", None): 10,
        ("지출", "온라인쇼핑", None): 20,
        ("지출", "온라인쇼핑", "서비스구독"): 21,
        ("수입", "급여", None): 30,
        ("수입", "금융수입", None): 31,
    },
    uncategorized_expense=90,
    uncategorized_income=91,
    card_bill_unknown=92,
)
BASE = datetime(2026, 5, 10, 12, 0, 0)
_next_id = iter(range(1, 100000))


def row(
    transaction_type: str,
    major: str,
    amount: int,
    instrument: int = ACCOUNT,
    *,
    minor: str = "미분류",
    description: str = "내용",
    seconds: int = 0,
    when: datetime = BASE,
    currency: str = "KRW",
) -> RowView:
    return RowView(
        id=next(_next_id),
        when=when + timedelta(seconds=seconds),
        type=transaction_type,
        major=major,
        minor=minor,
        description=description,
        amount=amount,
        currency=currency,
        instrument_id=instrument,
        instrument_kind=KINDS[instrument],
    )


def classify(rows, cards=(), rules=()):
    return classify_rows(rows, cards, rules, LOOKUP)


def test_pay_charge_typed_as_spend_is_excluded():
    # 실제 파일: 통장 출금은 지출, 머니 입금은 이체/내계좌이체, 같은 금액, 대부분 같은 초
    out = row("지출", "온라인쇼핑", -50000, ACCOUNT, minor="결제/충전", description="네이버페이 충전")
    incoming = row("이체", "내계좌이체", 50000, MONEY, description="충전")
    usage = row("지출", "식비", -50000, PAY, seconds=3600, description="밥집")
    decisions = classify([out, incoming, usage])

    assert decisions[out.id].nature == "funding"
    assert decisions[out.id].nature_reason == "charge_pair"
    assert decisions[out.id].category_id is None
    assert decisions[incoming.id].nature == "internal"
    assert decisions[usage.id].nature == "expense"


def test_pay_charge_without_its_other_half_waits_for_a_decision():
    # 페이로 쓴 돈이 지출로 잡히므로 충전까지 지출로 세면 두 번 계산될 수 있다
    out = row("지출", "온라인쇼핑", -50000, ACCOUNT, minor="결제/충전", description="네이버페이 충전")
    decision = classify([out])[out.id]
    assert (decision.nature, decision.nature_reason) == ("undetermined", "charge_unpaired")


def test_charging_a_car_is_still_spending():
    out = row("지출", "자동차", -30000, CARD, minor="전기차충전", description="충전소")
    assert classify([out])[out.id].nature == "expense"


OWN = ("홍길동", "홍*동")


def own_classified(row_view):
    return classify_rows([row_view], [], [], LOOKUP, own_names=OWN)[row_view.id]


@pytest.mark.parametrize("amount", [-300_000, 300_000])
@pytest.mark.parametrize("description", ["홍길동", "홍 길동", "홍길동(신한)", "이체 홍*동"])
def test_transfer_under_my_name_is_my_own_money(amount, description):
    decision = own_classified(row("이체", "이체", amount, ACCOUNT, description=description))
    assert (decision.nature, decision.nature_reason) == ("internal", "own_name")


def test_transfer_under_another_name_still_waits():
    decision = own_classified(row("이체", "이체", -300_000, ACCOUNT, description="김철수"))
    assert (decision.nature, decision.nature_reason) == ("undetermined", "unknown_transfer")


def test_money_marked_as_income_under_my_name_is_my_own_money():
    assert own_classified(row("수입", "기타수입", 300_000, ACCOUNT, description="홍길동")).nature == "internal"
    assert own_classified(row("수입", "용돈", 300_000, ACCOUNT, description="홍길동")).nature == "internal"


@pytest.mark.parametrize(
    ("kind", "major", "amount", "expected"),
    [
        ("수입", "급여", 3_000_000, "income"),
        ("수입", "금융수입", 500, "income"),
        ("지출", "식비", -10_000, "expense"),
        ("이체", "저축", -100_000, "asset_move"),
        ("이체", "투자", -100_000, "asset_move"),
    ],
)
def test_my_name_does_not_change_what_the_file_already_says(kind, major, amount, expected):
    assert own_classified(row(kind, major, amount, ACCOUNT, description="홍길동")).nature == expected


def test_rule_beats_my_name():
    out = row("이체", "이체", -300_000, ACCOUNT, description="홍길동")
    rule = RuleView(id=1, description_equals="홍길동", direction="out", set_nature="expense")
    assert classify_rows([out], [], [rule], LOOKUP, own_names=OWN)[out.id].nature == "expense"


def test_no_names_means_no_change():
    out = row("이체", "이체", -300_000, ACCOUNT, description="홍길동")
    assert classify([out])[out.id].nature == "undetermined"


def test_charge_pair_allows_a_short_delay_but_not_a_long_one():
    near_out = row("지출", "온라인쇼핑", -1000, ACCOUNT)
    near_in = row("이체", "내계좌이체", 1000, MONEY, seconds=120)
    far_out = row("지출", "온라인쇼핑", -2000, ACCOUNT)
    far_in = row("이체", "내계좌이체", 2000, MONEY, seconds=601)
    decisions = classify([near_out, near_in, far_out, far_in])

    assert decisions[near_out.id].nature == "funding"
    assert decisions[far_out.id].nature == "expense"


def test_ambiguous_pairs_are_not_linked():
    out_a = row("지출", "온라인쇼핑", -1000, ACCOUNT, seconds=10)
    out_b = row("지출", "온라인쇼핑", -1000, OTHER_ACCOUNT, seconds=20)
    incoming = row("이체", "내계좌이체", 1000, MONEY, seconds=30)
    decisions = classify([out_a, out_b, incoming])

    assert decisions[out_a.id].nature == "expense"
    assert decisions[out_b.id].nature == "expense"


def test_same_second_wins_over_nearby_candidate():
    exact_out = row("지출", "온라인쇼핑", -1000, ACCOUNT, seconds=0)
    nearby_out = row("지출", "온라인쇼핑", -1000, OTHER_ACCOUNT, seconds=40)
    incoming = row("이체", "내계좌이체", 1000, MONEY, seconds=0)
    decisions = classify([exact_out, nearby_out, incoming])

    assert decisions[exact_out.id].nature == "funding"
    assert decisions[nearby_out.id].nature == "expense"


def test_cash_placeholder_row_is_internal():
    # 실제 파일: 출처 모를 충전은 결제수단 '현금'의 음수 행으로 나온다
    out = row("이체", "내계좌이체", -30000, CASH)
    incoming = row("이체", "내계좌이체", 30000, MONEY)
    decisions = classify([out, incoming])
    assert decisions[out.id].nature == "internal"
    assert decisions[incoming.id].nature == "internal"


@pytest.mark.parametrize(
    ("description", "amount", "nature", "word", "category"),
    [
        ("쿠팡", -30_000, "expense", "쿠팡", 20),
        ("쿠팡이츠", 10_000, "refund", "쿠팡이츠", 10),  # 쿠팡보다 쿠팡이츠를 먼저 본다
        ("업비트 입금", -20_000, "asset_move", "업비트", None),
        ("OO피아노", -100_000, "expense", "피아노", 90),
        ("홍길동(OO로또)", -5_000, "expense", "로또", 90),
        ("OO손해보험", 300_000, "refund", "손해보험", 90),
    ],
)
def test_names_tell_what_a_transfer_was(description, amount, nature, word, category):
    item = row("이체", "이체", amount, ACCOUNT, description=description)
    decision = classify([item])[item.id]
    assert (decision.nature, decision.nature_reason, decision.category_id) == (nature, f"named:{word}", category)


def test_card_rows_keep_what_the_file_says():
    item = row("지출", "식비", -9_000, CARD, description="쿠팡")
    assert classify([item])[item.id].category_id == 10  # 이름보다 뱅크샐러드 분류가 먼저다


def test_money_sent_to_a_linked_pay_is_a_charge():
    out = row("이체", "이체", -1_000_000, ACCOUNT, description="카카오페이")
    back = row("이체", "이체", 300_000, ACCOUNT, description="카카오페이", seconds=86_400 * 20)
    decisions = classify_rows([out, back], [], [], LOOKUP, pays=("카카오페이",))
    assert (decisions[out.id].nature, decisions[out.id].nature_reason) == ("funding", "named_charge")
    assert (decisions[back.id].nature, decisions[back.id].nature_reason) == ("internal", "named_pay")
    assert classify([out])[out.id].nature == "undetermined"  # 머니가 연결되지 않은 페이는 이름만으로 정하지 않는다


def test_one_won_is_an_account_check():
    item = row("이체", "저축", 1, ACCOUNT, description="우리")
    decision = classify([item])[item.id]
    assert (decision.nature, decision.nature_reason, decision.category_id) == ("income", "one_won", 31)


@pytest.mark.parametrize(
    ("amount", "nature"),
    [(94, "income"), (999, "income"), (1_000, "undetermined"), (-94, "undetermined")],
)
def test_small_unknown_deposits_are_financial_income(amount, nature):
    item = row("이체", "이체", amount, ACCOUNT, description="입금 내역")
    decision = classify([item])[item.id]
    assert decision.nature == nature
    if nature == "income":
        assert (decision.nature_reason, decision.category_id) == ("small_deposit", 31)


def test_small_savings_stay_savings():
    item = row("이체", "저축", 340, ACCOUNT, description="잔돈 모으기")
    assert classify([item])[item.id].nature == "asset_move"  # 1원이 아닌 소액은 뱅크샐러드 분류를 따른다


def test_money_sent_and_returned_is_left_out():
    sent = row("이체", "이체", -230_000, ACCOUNT, description="김철수")
    back = row("이체", "이체", 230_000, ACCOUNT, description="김철수", seconds=86_400)
    later = row("이체", "이체", -50_000, ACCOUNT, description="김철수", seconds=86_400 * 30)
    later_back = row("이체", "이체", 50_000, ACCOUNT, description="김철수", seconds=86_400 * 40)
    decisions = classify([sent, back, later, later_back])
    assert decisions[sent.id].nature == decisions[back.id].nature == "ignored"
    assert decisions[sent.id].nature_reason == "returned_pair"
    assert decisions[later.id].nature == decisions[later_back.id].nature == "undetermined"  # 열흘 떨어지면 따로 정한다


def test_cancelled_transfer_leaves_with_its_original():
    received = row("이체", "이체", 100_000, MONEY, description="이영희")
    cancel = row("이체", "이체", -100_000, MONEY, description="송금 취소 내역", seconds=3_600)
    decisions = classify([received, cancel])
    assert decisions[received.id].nature_reason == decisions[cancel.id].nature_reason == "cancel_pair"
    assert decisions[received.id].nature == "ignored"


def test_rule_wins_over_a_returned_pair():
    sent = row("이체", "이체", -230_000, ACCOUNT, description="김철수")
    back = row("이체", "이체", 230_000, ACCOUNT, description="김철수", seconds=86_400)
    rule = RuleView(id=1, description_equals="김철수", direction="out", set_nature="expense")
    decisions = classify([sent, back], rules=[rule])
    assert decisions[sent.id].nature == "expense"
    assert decisions[back.id].nature == "undetermined"


def test_transfer_pair_marks_generic_transfer_internal():
    out = row("이체", "이체", -700000, ACCOUNT, description="내 다른 계좌로")
    incoming = row("이체", "내계좌이체", 700000, OTHER_ACCOUNT, seconds=30)
    assert classify([out, incoming])[out.id].nature == "internal"


def test_unpaired_generic_transfer_is_undetermined():
    out = row("이체", "이체", -700000, ACCOUNT, description="어떤 사람")
    incoming = row("이체", "이체", 200000, ACCOUNT, description="어떤 입금", seconds=9999)
    decisions = classify([out, incoming])
    assert decisions[out.id].nature == "undetermined"
    assert decisions[incoming.id].nature == "undetermined"
    assert decisions[out.id].category_id is None


def test_linked_card_bill_is_funding():
    card = CardView(CARD, "삼성", first_use=date(2026, 3, 1))
    bill = row("이체", "카드대금", -300000, ACCOUNT, description="삼성카드 결제대금")
    decision = classify([bill], [card])[bill.id]
    assert decision.nature == "funding"
    assert decision.nature_reason == "bill_linked"


def test_bill_before_card_was_linked_counts_as_spend():
    # 실제 파일: BC는 10월부터 사용 내역이 있지만 대금은 그 전부터 나간다
    card = CardView(CARD, "비씨", first_use=date(2026, 10, 1))
    bill = row("이체", "카드대금", -300000, ACCOUNT, description="비씨카드 대금")
    decision = classify([bill], [card])[bill.id]
    assert decision.nature == "expense"
    assert decision.nature_reason == "bill_unlinked"
    assert decision.category_id == LOOKUP.card_bill_unknown
    assert decision.channel_instrument_id == CARD


def test_bill_of_card_without_usage_counts_as_spend():
    card = CardView(CARD, "현대", first_use=None, is_virtual=True)
    bill = row("이체", "카드대금", -300000, ACCOUNT, description="현대카드")
    decision = classify([bill], [card])[bill.id]
    assert decision.nature == "expense"
    assert decision.channel_instrument_id == CARD


@pytest.mark.parametrize(
    ("policy", "nature"),
    [("ignore", "funding"), ("count", "expense")],
)
def test_bill_policy_overrides_inference(policy, nature):
    # 페이에 등록해 둔 카드라면 대금을 세면 이중 집계라 사용자가 끌 수 있어야 한다
    card = CardView(CARD, "현대", first_use=None, is_virtual=True, bill_policy=policy)
    bill = row("이체", "카드대금", -300000, ACCOUNT, description="현대카드")
    assert classify([bill], [card])[bill.id].nature == nature


def test_bill_with_unknown_company_is_undetermined():
    bill = row("이체", "카드대금", -300000, ACCOUNT, description="어딘가 카드")
    assert classify([bill])[bill.id].nature == "undetermined"


def test_positive_spend_is_refund_in_same_category():
    origin = row("지출", "식비", -9000, CARD, description="밥집")
    refund = row("지출", "식비", 9000, CARD, description="밥집", seconds=3600)
    decisions = classify([origin, refund])
    assert decisions[refund.id].nature == "refund"
    assert decisions[refund.id].category_id == decisions[origin.id].category_id == 10


def test_refund_pairs_with_latest_earlier_spend():
    old = row("지출", "식비", -9000, CARD, description="밥집", seconds=0)
    recent = row("지출", "식비", -9000, CARD, description="밥집", seconds=100)
    refund = row("지출", "식비", 9000, CARD, description="밥집", seconds=200)
    assert detect_pairs([old, recent, refund]).refund == [(recent.id, refund.id)]


@pytest.mark.parametrize(
    ("major", "minor", "amount", "nature", "reason"),
    [
        ("저축", "미분류", 100000, "asset_move", "saving_or_investment"),
        ("투자", "미분류", 100000, "asset_move", "saving_or_investment"),
        ("현금", "미분류", -50000, "internal", "cash_move"),
        ("내계좌이체", "미분류", 5000, "internal", "own_transfer"),
        ("미분류", "미분류", 5000, "undetermined", "unknown_transfer"),
    ],
)
def test_transfer_major_rules(major, minor, amount, nature, reason):
    item = row("이체", major, amount, ACCOUNT, minor=minor, seconds=abs(amount))
    decision = classify([item])[item.id]
    assert (decision.nature, decision.nature_reason) == (nature, reason)


def test_investment_typed_as_spend_is_asset_move():
    item = row("지출", "금융", -1000000, ACCOUNT, minor="증권/투자")
    assert classify([item])[item.id].nature == "asset_move"


def test_zero_rows_are_ignored():
    for transaction_type, major in (("수입", "금융수입"), ("이체", "저축")):
        item = row(transaction_type, major, 0, ACCOUNT)
        decision = classify([item])[item.id]
        assert decision.nature == "ignored"
        assert decision.category_id is None


def test_foreign_currency_is_undetermined_until_converted():
    item = row("지출", "문화/여가", 0, PAY, currency="USD")
    decision = classify([item])[item.id]
    assert (decision.nature, decision.nature_reason) == ("undetermined", "foreign_currency")


def test_income_and_category_mapping():
    salary = row("수입", "급여", 3000000, ACCOUNT)
    unknown_income = row("수입", "상금", 100, ACCOUNT)
    subscription = row("지출", "온라인쇼핑", -100, CARD, minor="서비스구독")
    shopping = row("지출", "온라인쇼핑", -100, CARD, minor="인터넷쇼핑")
    unknown_spend = row("지출", "새분류", -100, CARD)
    decisions = classify([salary, unknown_income, subscription, shopping, unknown_spend])

    assert decisions[salary.id].category_id == 30
    assert decisions[unknown_income.id].category_id == LOOKUP.uncategorized_income
    assert decisions[subscription.id].category_id == 21  # 소분류가 대분류보다 우선
    assert decisions[shopping.id].category_id == 20
    assert decisions[unknown_spend.id].category_id == LOOKUP.uncategorized_expense
    assert decisions[salary.id].category_source == "map"


def test_rules_override_defaults_in_priority_order():
    item = row("이체", "이체", -850000, ACCOUNT, description="월세 집주인")
    rules = [
        RuleView(id=2, priority=50, description_contains="월세", set_nature="expense", set_category_id=10),
        RuleView(id=1, priority=100, description_contains="집주인", set_nature="internal"),
    ]
    decision = classify([item], rules=rules)[item.id]
    assert decision.nature == "expense"
    assert decision.nature_source == "rule"
    assert decision.category_id == 10
    assert decision.category_source == "rule"


def test_rule_conditions_all_apply():
    item = row("이체", "이체", -850000, ACCOUNT, description="월세")
    assert RuleView(id=1, description_equals="월세", set_nature="expense").matches(item)
    assert not RuleView(id=1, description_equals="월", set_nature="expense").matches(item)
    assert RuleView(id=1, amount_min=850000, amount_max=850000, set_nature="expense").matches(item)
    assert not RuleView(id=1, amount_min=900000, set_nature="expense").matches(item)
    assert not RuleView(id=1, instrument_id=OTHER_ACCOUNT, set_nature="expense").matches(item)
    assert not RuleView(id=1, source_type="지출", set_nature="expense").matches(item)


def test_rule_does_not_revive_zero_rows():
    item = row("수입", "금융수입", 0, ACCOUNT)
    rule = RuleView(id=1, source_type="수입", set_nature="income")
    assert classify([item], rules=[rule])[item.id].nature == "ignored"


@pytest.mark.parametrize(
    ("name", "group", "types", "kind"),
    [
        ("입출금통장", "자유입출금 자산", {"이체"}, "account"),
        ("청약", "저축성 자산", {"이체"}, "savings"),
        ("현금", "현금 자산", {"이체"}, "cash"),
        ("네이버페이 간편결제(머니)", None, {"지출"}, "pay_usage"),
        ("토스 간편결제", None, {"지출"}, "pay_usage"),
        ("카카오페이 머니", None, {"이체", "지출"}, "pay_balance"),
        ("어떤 삼성카드", None, {"지출"}, "credit_card"),
        ("어떤 체크카드", None, {"지출"}, "check_card"),
        ("BC 바로 스페셜", None, {"지출"}, "credit_card"),
        ("세이프박스", None, {"이체"}, "account"),
        ("이상한 이름", None, {"이체"}, "unknown"),
    ],
)
def test_instrument_kind_inference(name, group, types, kind):
    assert infer_instrument_kind(name, group, types) == kind


def test_card_company_detection():
    assert company_of("BC 바로 스페셜") == "비씨"
    assert company_of("비씨카드") == "비씨"
    assert company_of("어떤 삼성카드") == "삼성"
    assert company_of("현대카드 M") == "현대"
    assert company_of("어딘가") is None
