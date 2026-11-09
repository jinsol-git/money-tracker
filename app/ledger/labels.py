"""장부의 내부 값을 화면에 보여 줄 한글 이름."""

from __future__ import annotations

NATURE_LABELS = {
    "expense": "지출",
    "income": "수입",
    "refund": "환불·정산",
    "internal": "내 계좌 이동",
    "funding": "페이 충전·카드대금",
    "asset_move": "저축·투자",
    "debt_move": "대출",
    "undetermined": "미확정",
    "ignored": "제외",
}
CHANNEL_LABELS = {"card": "카드", "account": "계좌", "pay": "페이", "other": "기타"}
# 연결한 대출 납입을 나눈 조각. 금리로 어림한 조각은 다음 내보내기가 오면 다시 계산한다.
LOAN_INTEREST_REASONS = frozenset({"loan_interest", "loan_interest_estimate"})
LOAN_PRINCIPAL_REASONS = frozenset({"loan_principal", "loan_principal_estimate"})
LOAN_ESTIMATE_REASONS = frozenset({"loan_interest_estimate", "loan_principal_estimate"})
LOAN_REASONS = LOAN_INTEREST_REASONS | LOAN_PRINCIPAL_REASONS
INSTRUMENT_KIND_LABELS = {
    "credit_card": "신용카드",
    "check_card": "체크카드",
    "account": "입출금 계좌",
    "savings": "저축 계좌",
    "pay_usage": "간편결제",
    "pay_balance": "페이 머니",
    "cash": "현금",
    "unknown": "알 수 없음",
}
REAL_ESTATE_GROUP = "부동산"  # 뱅크샐러드 재무현황의 항목 이름
PENSION_GROUP = "연금 자산"  # 뱅크샐러드 재무현황의 항목 이름
PHYSICAL_ASSET_GROUPS = frozenset({REAL_ESTATE_GROUP, "동산", "기타 실물 자산"})  # 뱅크샐러드 재무현황의 실물 자산 항목
MANUAL_KIND_LABELS = {"car": "자동차", "real_estate": "부동산", "other": "기타 자산"}
TENURE_LABELS = {"own": "매매 (내 집)", "jeonse": "전세 보증금", "wolse": "월세 보증금"}
TENURE_CHOICES = {"own": "매매", "jeonse": "전세", "wolse": "전월세"}  # 입력 화면의 버튼
OTHER_TYPE_LABELS = {"cash": "현금", "physical": "실물", "coin": "코인", "fund": "펀드", "stock": "주식", "pension": "연금"}
FINANCIAL_OTHER_TYPES = frozenset(OTHER_TYPE_LABELS) - {"physical"}  # 금융 탭에 함께 보이는 직접 입력 자산
CAR_CLASS_LABELS = {
    "small": "소형 (경차, 소형 세단, 소형 SUV)",
    "compact": "준중형 (아반떼, K3, 준중형 SUV 등)",
    "mid": "중형 (쏘나타, K5, 쏘렌토 등)",
    "large": "대형 (그랜저, G80, 팰리세이드 등)",
}
CAR_ORIGIN_LABELS = {"domestic": "국산", "imported": "수입"}
RECURRING_KIND_LABELS = {
    "regular": "정기",
    "ended": "끝난 정기",
    "short": "단기",
    "irregular": "불규칙",
}
SPREAD_LABELS = {"same": "금액 일정", "similar": "금액 비슷", "varied": "금액 들쭉날쭉"}
REASON_LABELS = {
    "zero_amount": "금액이 0원인 거래라 합계에서 뺐습니다.",
    "foreign_currency": "외화 거래라 원화 금액을 알 수 없어 합계에 넣지 않았습니다.",
    "source_income": "뱅크샐러드가 수입으로 표시한 거래입니다.",
    "own_name": "설정한 내 이름으로 오간 돈이라 내 계좌 이동으로 봅니다.",
    "charge_pair": "같은 금액의 페이 충전 입금과 짝이 맞아, 페이로 쓸 때만 지출로 셉니다.",
    "charge_unpaired": "충전 입금을 찾지 못한 페이 충전입니다. 페이로 쓴 기록이 보이면 페이 충전, 아니면 지출로 정해 주세요.",
    "transfer_pair": "같은 금액의 입금과 짝이 맞아 내 계좌 사이의 이동으로 봤습니다.",
    "investment_out": "뱅크샐러드가 증권·투자로 분류한 출금입니다.",
    "positive_spend": "지출 항목으로 들어온 돈이라 취소나 환불로 봤습니다.",
    "source_spend": "뱅크샐러드가 지출로 표시한 거래입니다.",
    "own_transfer": "뱅크샐러드가 내 계좌 이체로 표시한 거래입니다.",
    "saving_or_investment": "뱅크샐러드가 저축이나 투자로 표시한 이체입니다.",
    "cash_move": "뱅크샐러드가 현금 이체로 표시한 거래입니다.",
    "unknown_transfer": "어디로 간 돈인지 알 수 없는 이체입니다.",
    "one_won": "들어온 1원이라 계좌 인증으로 보고 금융수입에 넣었습니다.",
    "small_deposit": "1,000원 미만으로 들어온 출처 모를 돈이라 계좌 인증이나 이자로 보고 금융수입에 넣었습니다.",
    "named_charge": "통장에서 페이로 보낸 돈이라 페이 충전으로 봤습니다. 페이로 쓸 때 지출로 셉니다.",
    "named_pay": "페이에서 통장으로 옮긴 돈이라 내 계좌 이동으로 봤습니다.",
    "returned_pair": "같은 상대와 같은 금액을 며칠 안에 주고받아 둘 다 합계에서 뺐습니다.",
    "cancel_pair": "취소된 거래라 원래 거래와 함께 합계에서 뺐습니다.",
    "bill_unknown_card": "어느 카드의 대금인지 알 수 없습니다.",
    "bill_unlinked": "사용 내역이 장부에 없는 카드의 대금이라 지출로 셉니다.",
    "bill_reversal": "카드 대금이 취소되어 들어온 돈입니다.",
    "bill_policy_ignore": "이 카드의 대금은 세지 않도록 설정되어 있습니다.",
    "bill_policy_count": "이 카드의 대금은 지출로 세도록 설정되어 있습니다.",
    "bill_linked": "사용 내역이 장부에 있는 카드의 대금이라 이중 계산을 막으려고 뺐습니다.",
    "undo": "변경을 되돌린 값입니다.",
    "loan_interest": "연결한 대출의 이자입니다. 두 잔액 기록 사이의 납입액과 잔액 감소로 구했습니다.",
    "loan_interest_estimate": "연결한 대출의 이자입니다. 잔액과 금리로 어림했고, 다음 내보내기가 오면 다시 계산합니다.",
    "loan_principal": "연결한 대출의 원금입니다. 납입액에서 이자를 뺀 금액입니다.",
    "loan_principal_estimate": "연결한 대출의 원금입니다. 납입액에서 어림한 이자를 뺀 금액이라 다음 내보내기가 오면 다시 계산합니다.",
}


# 자산 묶음 위젯의 네 묶음. 적혀 있지 않은 항목(저축성, 투자성, 신탁, 보험, 직접 넣은 코인·펀드·주식)은 저축·투자다.
ASSET_BUCKETS = {"cash": "현금성", "grow": "저축·투자", "lock": "연금", "real": "실물·보증금"}
BUCKET_OF_GROUP = {
    "자유입출금 자산": "cash",
    "현금 자산": "cash",
    "전자금융 자산": "cash",
    "현금 (직접 입력)": "cash",
    "연금 자산": "lock",
    "연금 (직접 입력)": "lock",
    "부동산": "real",
    "동산": "real",
    "기타 실물 자산": "real",
    "자동차 (직접 입력)": "real",
    "부동산 (직접 입력)": "real",
    "보증금 (직접 입력)": "real",
    "실물 (직접 입력)": "real",
}


def manual_group_label(kind: str, tenure: str | None, other_type: str | None = None) -> str:
    """차트의 항목 이름. 뱅크샐러드의 같은 이름 항목과 섞이지 않게 구분한다."""
    if kind == "car":
        return "자동차 (직접 입력)"
    if kind == "real_estate":
        return "부동산 (직접 입력)" if tenure == "own" else "보증금 (직접 입력)"
    return f"{OTHER_TYPE_LABELS.get(other_type or '', '기타')} (직접 입력)"


def split_part(reason: str | None) -> str:
    """나눈 거래의 조각 이름."""
    if reason in LOAN_INTEREST_REASONS:
        return "이자"
    if reason in LOAN_PRINCIPAL_REASONS:
        return "원금"
    return ""


def reason_label(reason: str | None) -> str:
    if not reason:
        return ""
    if reason.startswith("rule:"):
        return "규칙으로 정한 값입니다."
    if reason.startswith("change_set:"):
        return "직접 바꾼 값입니다."
    if reason.startswith("named:"):
        return f"이름에 '{reason.removeprefix('named:')}'이(가) 들어 있어 그에 맞게 분류했습니다."
    return REASON_LABELS.get(reason, reason)
