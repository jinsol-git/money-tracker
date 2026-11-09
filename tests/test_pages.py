"""홈과 차트 화면. 화면의 숫자가 집계와 같은지, 고치기와 되돌리기가 합계까지 바꾸는지 본다."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

from app import charts
from app.format import compact_won
from app.home import widgets
from app.ingest.importer import import_export
from app.ledger import labels, reports
from app.ledger.models import Category, ChangeSet, Instrument, Txn, TxnSplit
from app.ops import state
from fixtures import make_export as fx

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "chart_page_harness.js"
SCRIPTS = tuple(
    ROOT / "public" / "js" / name
    for name in (
        "chart-data.js",
        "widget-data.js",
        "chart-marks.js",
        "chart-widgets.js",
        "chart-page.js",
        "dashboard.js",
        "shell.js",
        "signature.js",
        "amount-reading.js",
        "category-choices.js",
        "review-forms.js",
        "home-widgets.js",
    )
)
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\ssrc=)")
FLOW = {"tab": "flow", "view": "trend"}
PASSWORD = "0000"
BANK = "입출금통장"
OTHER_BANK = "다른통장"
MONEY = "네이버페이 머니"
PAY = "네이버페이 간편결제"
CARD = "어떤 삼성카드"
ASSETS = [fx.AssetRow("자유입출금 자산", BANK, 1_000_000), fx.AssetRow("자유입출금 자산", OTHER_BANK, 500_000)]
LIABILITIES = [fx.AssetRow("장기대출", "대출상품", 200_000)]
LEDGER = [
    fx.tx("2026-01-10 10:00:00", "지출", "식비", "한식", "지난달밥집", -11_000, CARD),
    fx.tx("2026-02-01 09:00:00", "수입", "급여", "미분류", "회사", 1_000_000, BANK),
    fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -30_000, CARD),
    fx.tx("2026-02-03 10:00:00", "지출", "식비", "한식", "밥집", 5_000, CARD),
    fx.tx("2026-02-04 10:00:00", "지출", "온라인쇼핑", "결제/충전", "네이버페이 충전", -20_000, BANK),
    fx.tx("2026-02-04 10:00:00", "이체", "내계좌이체", "미분류", "충전", 20_000, MONEY),
    fx.tx("2026-02-05 10:00:00", "지출", "온라인쇼핑", "인터넷쇼핑", "쇼핑몰", -20_000, PAY),
    fx.tx("2026-02-06 10:00:00", "지출", "금융", "보험", "보험사", -10_000, BANK),
    fx.tx("2026-02-25 10:00:00", "이체", "카드대금", "미분류", "삼성카드 결제대금", -30_000, BANK),
    fx.tx("2026-02-26 10:00:00", "이체", "카드대금", "미분류", "현대카드", -40_000, BANK),
    fx.tx("2026-02-27 10:00:00", "이체", "내계좌이체", "미분류", "이동", -100_000, BANK),
    fx.tx("2026-02-27 10:00:00", "이체", "내계좌이체", "미분류", "이동", 100_000, OTHER_BANK),
    fx.tx("2026-02-20 10:00:00", "이체", "이체", "미분류", "어떤사람", -70_000, BANK),
    fx.tx("2026-02-21 10:00:00", "이체", "저축", "미분류", "적금 해지", 300_000, BANK),
]
PAGES = ("/", "/transactions", "/review")


def load(session, rows=LEDGER, inner_name="2026-01-01~2026-02-27.xlsx", assets=ASSETS):
    data = fx.zip_aes(fx.build_xlsx(rows=rows, assets=assets, liabilities=LIABILITIES), PASSWORD, inner_name)
    result = import_export(session, data, [PASSWORD])
    assert result.status == "applied", result
    return result


@pytest.fixture
def client(signed_in, session):
    browser, _, _ = signed_in
    load(session)
    return browser


def split_of(session, description: str) -> TxnSplit:
    return session.scalars(select(TxnSplit).join(Txn).where(Txn.description == description)).one()


def category_id(session, key: str) -> int:
    return session.scalar(select(Category.id).where(Category.key == key))


def instrument_id(session, name: str) -> int:
    return session.scalar(select(Instrument.id).where(Instrument.source_name == name))


def result_sum(html: str) -> tuple[str, str]:
    match = re.search(r'<p class="result-sum"><span>([\d,]+)건</span> <span class="num">합계 ([+\-\d,]+)원', html)
    assert match, "합계 줄이 없습니다"
    return match.group(1), match.group(2)


# --- 데이터가 없을 때


def test_every_page_works_before_the_first_import(signed_in):
    browser, _, _ = signed_in
    for path in (*PAGES, "/chart"):
        assert browser.client.get(path).status_code == 200, path
    assert "아직 볼 기록이 없습니다" in browser.client.get("/").text
    assert "조건에 맞는 거래가 없습니다" in browser.client.get("/transactions").text
    assert "확인할 거래가 없습니다" in browser.client.get("/review").text


# --- 홈


def widget(html: str, key: str) -> str:
    match = re.search(rf'<section class="card widget" id="widget-{key}">(.*?)</section>', html, re.S)
    assert match, f"{key} 카드가 없습니다"
    return match.group(1)


def test_home_shows_latest_month_from_reports(client, session):
    month = reports.monthly_summary(session)[-1]
    assert month.month == "2026-02" and month.spend_total == 95_000
    html = client.client.get("/").text
    assert "<h1>2026년 2월</h1>" in html
    assert '<p class="hero">95,000원</p>' in html
    assert "1,000,000원" in html  # 수입
    assert "+905,000원" in html  # 수입 - 지출
    assert "+84,000원" in html  # 전달보다
    assert "미확정 금액" not in html


def test_home_moves_between_months(client):
    html = client.client.get("/?month=2026-01").text
    assert "<h1>2026년 1월</h1>" in html
    assert '<p class="hero">11,000원</p>' in html
    assert 'href="/?month=2026-02"' in html
    assert 'aria-label="이전 달"' not in html  # 데이터가 시작한 달
    assert "전달보다" not in html
    february = client.client.get("/?month=2026-02").text
    assert 'href="/?month=2026-01"' in february and 'href="/?month=2026-03"' in february


def test_home_opens_a_month_without_records(client):
    html = client.client.get("/?month=2026-05").text
    assert "<h1>2026년 5월</h1>" in html
    assert '<p class="hero">0원</p>' in html
    assert "이 달의 지출이 없습니다" in widget(html, "category")


def test_home_stops_at_the_current_month(client):
    html = client.client.get("/?month=2026-11").text  # 오늘은 2026년 11월 5일이다
    assert "<h1>2026년 11월</h1>" in html
    assert 'aria-label="다음 달"' not in html


def test_home_falls_back_to_latest_for_a_bad_month(client):
    for value in ("2099-01", "2025-12", "abc", "2026-13", ""):
        assert "<h1>2026년 2월</h1>" in client.client.get("/", params={"month": value}).text


def test_current_screen_is_marked_in_the_menu(client):
    screens = (
        ("/", "홈"),
        ("/chart", "카테고리"),
        ("/chart?tab=category", "카테고리"),
        ("/chart?tab=flow", "수입·지출"),
        ("/chart?tab=assets", "자산"),
        ("/chart?tab=finance", "금융"),
        ("/chart?tab=nope", "카테고리"),
        ("/transactions", "기록"),
        ("/review", "검토"),
        ("/settings", "설정"),
    )
    for path, label in screens:
        top = re.search(r'<header class="topbar">.*?</header>', client.client.get(path).text, re.S).group(0)
        marked = re.findall(r'aria-current="page"[^>]*>\s*(?:<svg.*?</svg>\s*<span>)?([^<]+)<', top, re.S)
        assert marked == [label], path


def test_old_addresses_lead_to_the_new_screens(client):
    spending = client.client.get("/spending?month=2026-01")
    assert (spending.status_code, spending.headers["location"]) == (303, "/?month=2026-01")
    assert client.client.get("/spending?month=abc").headers["location"] == "/"
    assets = client.client.get("/assets")
    assert (assets.status_code, assets.headers["location"]) == (303, "/chart?tab=assets")


def test_home_flags_a_month_that_is_not_finished(client):
    assert "2월 27일까지" in client.client.get("/").text


def test_transactions_to_review_show_in_the_menu_not_as_a_notice(client):
    html = client.client.get("/").text
    assert "확인이 필요한 거래" not in html and "검토하기" not in html
    assert re.findall(r'<sup class="cnt"[^>]*>(\d+)</sup>', html) == ["1", "1"]


def test_bridge_explains_the_difference_from_banksalad(client):
    html = client.client.get("/?month=2026-02").text
    assert "뱅크샐러드 합계와 비교" in html
    assert "75,000원" in html  # 뱅크샐러드 합계
    assert "-20,000원" in html  # 페이 충전
    assert "+40,000원" in html  # 대금으로만 보이는 카드


@pytest.mark.parametrize("month", ["2026-01", "2026-02"])
def test_category_card_rows_open_a_list_with_the_same_total(client, session, month):
    card = widget(client.client.get("/", params={"month": month}).text, "category")
    lines = reports.category_breakdown(session, month, month)
    links = re.findall(r'<a href="(/transactions\?[^"]*category=[^"]*)">', card)
    assert lines and len(links) == len(lines)
    for link, line in zip(links, lines):
        count, amount = result_sum(client.client.get(link.replace("&amp;", "&")).text)
        assert int(amount.replace(",", "")) == -line.amount
        assert int(count.replace(",", "")) == line.rows


def test_category_card_shows_shares_with_the_chart_colors(client, session):
    card = widget(client.client.get("/?month=2026-02").text, "category")
    assert "42%" in card  # 카드대금 40,000원 / 95,000원
    assert 'class="series-slot-1"' in card
    assert widgets.category_slots(session)["card_bill_unknown"] == "slot-1"
    assert 'href="/chart?tab=category&amp;month=2026-02"' in card


def test_payment_card_rows_add_up_and_open_the_same_total(client, session):
    lines = reports.instrument_breakdown(session, "2026-02")
    assert sum(line.amount for line in lines) == 95_000
    card = widget(client.client.get("/?month=2026-02").text, "payment")
    links = re.findall(r'<a href="(/transactions\?month=[^"]*instrument=[^"]*)">', card)
    assert len(links) == len(lines)
    for link, line in zip(links, lines):
        assert int(result_sum(client.client.get(link.replace("&amp;", "&")).text)[1].replace(",", "")) == -line.amount


def test_payment_card_has_no_lists_of_instruments(client):
    card = widget(client.client.get("/").text, "payment")
    assert 'href="/chart?tab=flow&amp;month=2026-02"' in card
    assert "모든 결제 수단" not in card
    assert "오래 거래가 없는 결제 수단" not in card


def test_screens_have_no_table_views(client):
    for path in ("/", "/?month=2026-02", "/chart"):
        assert "표로 보기" not in client.client.get(path).text


def test_cards_card_counts_cards_known_only_by_their_bills(client):
    card = widget(client.client.get("/?month=2026-02").text, "cards")
    assert "65,000원" in card
    assert CARD in card
    assert "사용 내역 없는 카드 대금" in card
    link = re.search(r'<a href="(/transactions\?[^"]*channel=card[^"]*)">', card).group(1)
    assert result_sum(client.client.get(link.replace("&amp;", "&")).text) == ("1", "-40,000")


def test_home_shows_net_worth_and_items(client, session):
    html = client.client.get("/").text
    worth = widget(html, "net_worth")
    assert "1,300,000원" in worth and "1,500,000원" in worth and "200,000원" in worth
    accounts = widget(html, "accounts")
    assert "1,500,000원" in accounts
    assert f'href="/accounts/{instrument_id(session, BANK)}"' in accounts
    finance = widget(html, "finance")
    assert "대출상품" in finance
    assert "OO증권" in finance and "OO생명" in finance and "OO은행" in finance


def test_empty_cards_stay_on_the_home(client):
    html = client.client.get("/").text
    for key, kind, text in (
        ("car", "car", "등록한 자동차가 없습니다"),
        ("real_estate", "real_estate", "등록한 부동산이 없습니다"),
        ("physical", "other", "등록한 자산이 없습니다"),
    ):
        assert text in widget(html, key)
        assert f'href="/assets/manual/new?kind={kind}"' in widget(html, key)
    assert "연금 기록이 없습니다" in widget(html, "pension")
    assert "페이머니 기록이 없습니다" in widget(html, "pay")


def test_each_asset_group_goes_to_its_card(signed_in, session):
    assets = [
        fx.AssetRow("자유입출금 자산", BANK, 1),
        fx.AssetRow("현금 자산", "현금", 2),
        fx.AssetRow("전자금융 자산", MONEY, 3),
        fx.AssetRow("저축성 자산", "적금", 4),
        fx.AssetRow("투자성 자산", "펀드", 5),
        fx.AssetRow("연금 자산", "연금보험", 6),
        fx.AssetRow("부동산", "아파트", 7),
        fx.AssetRow("동산", "차", 8),
        fx.AssetRow("기타 실물 자산", "금", 9),
    ]
    load(session, inner_name="2026-01-01~2026-03-01.xlsx", assets=assets)
    cards = widgets.holding_cards(session, date(2026, 3, 1))
    assert {key: card.total for key, card in cards.items()} == {
        "accounts": 3,
        "pay": 3,
        "real_estate": 7,
        "car": 8,
        "pension": 6,
        "finance": 9,
        "physical": 9,
    }
    assert cards["finance"].debt_total == 200_000


def test_holding_cards_add_up_to_the_net_worth(client, session):
    client.post("/assets/manual", form_page="/assets/manual/new", kind="other", other_type="physical", name="금 10돈", amount="5000000")
    client.post("/assets/manual", form_page="/assets/manual/new", kind="real_estate", name="집", tenure="jeonse", amount="100000000")
    today = date(2026, 11, 5)
    point = reports.net_worth_on(session, today)
    cards = widgets.holding_cards(session, today)
    assert sum(card.total for card in cards.values()) == point.total_assets == 106_500_000
    assert cards["finance"].debt_total == point.total_liabilities == 200_000


def test_home_past_month_uses_the_record_of_that_month(signed_in, session):
    browser, _, _ = signed_in
    february = [fx.tx("2026-02-10 10:00:00", "지출", "식비", "한식", "밥집", -1_000, CARD)]
    march = [*february, fx.tx("2026-03-10 10:00:00", "지출", "식비", "한식", "밥집", -2_000, CARD)]
    load(session, rows=february, inner_name="2026-02-01~2026-02-27.xlsx", assets=[fx.AssetRow("자유입출금 자산", BANK, 1_000_000)])
    load(session, rows=march, inner_name="2026-02-01~2026-03-20.xlsx", assets=[fx.AssetRow("자유입출금 자산", BANK, 2_000_000)])
    assert "1,000,000원" in widget(browser.client.get("/?month=2026-02").text, "accounts")
    latest = browser.client.get("/").text
    assert "<h1>2026년 3월</h1>" in latest
    assert "2,000,000원" in widget(latest, "accounts")
    assert "+1,000,000원" in widget(latest, "net_worth")


# --- 홈 카드 편집


def card_order(client, path: str = "/") -> list[str]:
    return re.findall(r'id="widget-(\w+)"', client.client.get(path).text)


def change_card(client, key: str, action: str, **fields):
    return client.post("/home/widgets", form_page="/?edit=1", key=key, action=action, **fields)


def test_every_card_shows_by_default_in_the_set_order(client):
    assert card_order(client) == list(widgets.WIDGETS)
    assert list(widgets.WIDGETS.values()) == [
        "카테고리 비중",
        "결제 수단 비중",
        "카드",
        "순자산",
        "계좌·현금",
        "페이머니",
        "부동산",
        "자동차",
        "연금",
        "금융",
        "기타 실물자산",
    ]


def test_edit_buttons_appear_only_in_edit_mode(client):
    assert 'action="/home/widgets"' not in client.client.get("/").text
    assert 'action="/home/widgets"' in client.client.get("/?edit=1").text


def test_cards_can_be_taken_out_and_put_back(client):
    response = change_card(client, "car", "hide", month="2026-02")
    assert response.status_code == 303
    assert response.headers["location"] == "/?month=2026-02&edit=1"
    assert "car" not in card_order(client)
    edit = client.client.get(response.headers["location"]).text
    assert 'data-wp-key="car"' in edit  # 위젯 화면에는 뺀 카드도 그림으로 있다
    assert "car" not in re.search(r'data-visible="([^"]*)"', edit).group(1).split(",")
    change_card(client, "car", "show")
    assert card_order(client)[-1] == "car"  # 다시 넣으면 맨 뒤에 붙는다


def test_cards_can_be_moved(client):
    change_card(client, "payment", "up")
    assert card_order(client)[:2] == ["payment", "category"]
    change_card(client, "payment", "up")  # 맨 위에서는 더 올라가지 않는다
    assert card_order(client)[:2] == ["payment", "category"]
    change_card(client, "payment", "down")
    assert card_order(client)[:2] == ["category", "payment"]
    change_card(client, "physical", "down")  # 맨 아래에서는 더 내려가지 않는다
    assert card_order(client)[-1] == "physical"


def test_a_taken_out_card_is_not_calculated(client, session):
    change_card(client, "category", "hide")
    layout = widgets.load_layout(session)
    assert "category" in layout.hidden
    built = widgets.build(
        session,
        month="2026-02",
        as_of=date(2026, 2, 28),
        previous_end=date(2026, 1, 31),
        summary=reports.monthly_summary(session)[-1],
        visible=layout.visible,
    )
    assert "category" not in built and "payment" in built


def test_layout_ignores_unknown_cards_and_adds_new_ones(client, session, clock):
    state.set_state(session, clock(), "home_widgets", {"order": ["nope", "car"], "hidden": ["nope", "pay"]})
    layout = widgets.load_layout(session)
    assert layout.visible[0] == "car"
    assert [key for key in layout.hidden if key in widgets.WIDGETS] == ["pay"]
    assert set(layout.visible) | set(layout.hidden) == set(widgets.TITLES)


def test_card_changes_need_a_csrf_token_and_a_known_card(client):
    assert client.client.post("/home/widgets", data={"key": "car", "action": "hide"}).status_code == 403
    assert change_card(client, "nope", "hide").status_code == 404
    assert change_card(client, "car", "nope").status_code == 404
    assert card_order(client) == list(widgets.WIDGETS)


def test_widget_page_places_cards_in_the_clicked_order(client):
    picked = ["net_worth", "category", "flow.pair", "pension"]
    response = client.post("/home/widgets", form_page="/?edit=1", action="set", keys=picked, month="2026-02")
    assert response.status_code == 303 and response.headers["location"] == "/?month=2026-02"  # 적용하면 편집을 마친다
    html = client.client.get("/?month=2026-02").text
    assert card_order(client, "/?month=2026-02") == ["category", "net_worth", "pension"]  # 지출 묶음, 자산 묶음 순서다
    assert 'data-home-chart="flow.pair"' in html and 'id="widget-payment"' not in html
    edit = client.client.get("/?month=2026-02&edit=1").text
    assert re.search(r'data-visible="([^"]*)"', edit).group(1).split(",") == picked
    assert "id=\"widget-" not in edit.split("data-home-widget-page", 1)[1]  # 그림은 화면의 카드와 id가 겹치지 않는다
    for bad in (["flow.nope"], ["car", "car"]):
        assert client.post("/home/widgets", form_page="/?edit=1", action="set", keys=bad).status_code == 404


def test_month_is_kept_while_editing(client):
    html = client.client.get("/?month=2026-02&edit=1").text
    assert 'href="/?month=2026-01&amp;edit=1"' in html
    assert 'href="/?month=2026-02"' in html  # 편집 끝내기
    assert '<input type="hidden" name="month" value="2026-02">' in html




# --- 거래 내역


def test_transactions_filters_combine(client, session):
    bank = instrument_id(session, BANK)
    assert result_sum(client.client.get("/transactions?q=밥집").text) == ("3", "-36,000")
    assert result_sum(client.client.get("/transactions?q=밥집&month=2026-02").text) == ("2", "-25,000")
    assert result_sum(client.client.get("/transactions?nature=undetermined").text) == ("1", "-70,000")
    assert result_sum(client.client.get(f"/transactions?instrument={bank}&nature=spend").text) == ("2", "-50,000")


def test_total_is_hidden_until_a_filter_gives_it_meaning(client):
    assert "합계" not in client.client.get("/transactions").text
    assert "합계 " in client.client.get("/transactions?month=2026-02").text


def test_search_treats_wildcards_as_text(client):
    assert result_sum(client.client.get("/transactions", params={"q": "%"}).text)[0] == "0"
    assert result_sum(client.client.get("/transactions", params={"q": "_"}).text)[0] == "0"


@pytest.mark.parametrize(
    "query",
    [
        "month=abc",
        "page=abc",
        "page=0",
        "page=-1",
        "instrument=abc",
        "instrument=99999999999999999999999",
        "nature=nope",
        "category=nope",
        "channel=nope",
    ],
)
def test_bad_filter_values_do_not_break_the_page(client, query):
    assert client.client.get(f"/transactions?{query}").status_code == 200


def test_paging_keeps_the_total_of_all_pages(client, session):
    rows = [fx.tx(f"2026-02-0{1 + i % 9} 11:{i % 60:02d}:{i // 60:02d}", "지출", "식비", "한식", f"가게{i}", -100, CARD) for i in range(120)]
    load(session, rows=[*LEDGER, *rows], inner_name="2026-01-01~2026-02-28.xlsx")
    first = client.client.get("/transactions?q=가게").text
    assert result_sum(first) == ("120", "-12,000")
    assert first.count('<div class="line-main">') == 50
    assert "1 / 3" in first
    assert "page=2" in first
    last = client.client.get("/transactions?q=가게&page=3").text
    assert result_sum(last) == ("120", "-12,000")
    assert last.count('<div class="line-main">') == 20
    assert 'href="/transactions?q=%EA%B0%80%EA%B2%8C&amp;page=2"' in last


# --- 검토와 고치기


def test_review_queues_show_their_items(client):
    undetermined = client.client.get("/review").text
    assert "어떤사람" in undetermined
    assert "어디로 간 돈인지 알 수 없는 이체입니다." in undetermined
    assert "이체 › 이체 › 미분류" in undetermined
    assert "적금 해지" in client.client.get("/review?queue=inflows").text
    assert "현대카드" in client.client.get("/review?queue=largest").text
    assert client.client.get("/review?queue=nope").status_code == 200


def test_fixing_a_transaction_moves_the_totals_and_undo_restores_them(client, session):
    split = split_of(session, "어떤사람")
    housing = category_id(session, "housing")
    response = client.post(
        f"/splits/{split.id}/change", form_page="/review", nature="expense", category=str(housing), back="/review?queue=undetermined"
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/review?queue=undetermined&changed=")

    html = client.client.get(location).text
    assert "변경했습니다." in html
    assert "어떤사람: 종류 미확정 → 지출, 카테고리 없음 → 주거비" in html
    assert "확인할 거래가 없습니다" in html
    assert '<p class="hero">165,000원</p>' in client.client.get("/").text

    change_id = int(location.rsplit("=", 1)[1])
    undone = client.post(f"/changes/{change_id}/undo", form_page="/review", back="/review")
    assert "msg=undone" in undone.headers["location"]
    assert "변경을 되돌렸습니다." in client.client.get(undone.headers["location"]).text
    assert '<p class="hero">95,000원</p>' in client.client.get("/").text
    assert split_of(session, "어떤사람").nature == "undetermined"


def test_changing_only_the_nature_finds_a_category(client, session):
    split = split_of(session, "어떤사람")
    client.post(f"/splits/{split.id}/change", form_page="/review", nature="expense", category="")
    assert split_of(session, "어떤사람").category_id == category_id(session, "uncategorized_expense")
    assert "어떤사람" in client.client.get("/review?queue=uncategorized").text


def test_saving_without_changes_creates_nothing(client, session):
    split = split_of(session, "보험사")
    response = client.post(f"/splits/{split.id}/change", form_page="/review", nature=split.nature, category="")
    assert "msg=unchanged" in response.headers["location"]
    assert session.scalars(select(ChangeSet)).all() == []


@pytest.mark.parametrize(
    ("description", "fields", "error"),
    [
        ("보험사", {"category": "salary"}, "category_mismatch"),  # 지출에 수입 비목
        ("회사", {"category": "food"}, "category_mismatch"),  # 수입에 지출 비목
        ("이동", {"category": "food"}, "category_not_allowed"),  # 내 계좌 이동에는 비목이 없다
    ],
)
def test_category_must_fit_the_nature(client, session, description, fields, error):
    split = session.scalars(select(TxnSplit).join(Txn).where(Txn.description == description)).first()
    before = (split.nature, split.category_id)
    response = client.post(
        f"/splits/{split.id}/change",
        form_page="/review",
        category=str(category_id(session, fields["category"])),
    )
    assert f"error={error}" in response.headers["location"]
    assert (split.nature, split.category_id) == before
    assert client.client.get(response.headers["location"]).text.count('class="notice error"') == 1


def test_user_category_blocks_a_nature_it_does_not_fit(client, session):
    split = split_of(session, "보험사")
    client.post(f"/splits/{split.id}/change", form_page="/review", category=str(category_id(session, "medical")))
    response = client.post(f"/splits/{split.id}/change", form_page="/review", nature="income")
    assert "error=category_mismatch" in response.headers["location"]
    fixed = client.post(
        f"/splits/{split.id}/change", form_page="/review", nature="income", category=str(category_id(session, "other_income"))
    )
    assert "changed=" in fixed.headers["location"]


def test_unknown_values_are_rejected(client, session):
    split = split_of(session, "보험사")
    assert "error=unknown_nature" in client.post(f"/splits/{split.id}/change", form_page="/review", nature="nope").headers["location"]
    assert "error=unknown_category" in client.post(f"/splits/{split.id}/change", form_page="/review", category="999999").headers["location"]
    assert client.post("/splits/999999/change", form_page="/review", nature="income").status_code == 404
    assert "error=unknown_change" in client.post("/changes/999999/undo", form_page="/review").headers["location"]


def test_undoing_twice_reports_it(client, session):
    split = split_of(session, "보험사")
    location = client.post(f"/splits/{split.id}/change", form_page="/review", nature="internal").headers["location"]
    change_id = int(location.rsplit("=", 1)[1])
    client.post(f"/changes/{change_id}/undo", form_page="/review")
    assert "error=already_undone" in client.post(f"/changes/{change_id}/undo", form_page="/review").headers["location"]


def test_undone_change_leaves_the_recent_list_and_the_notice(client, session):
    split = split_of(session, "보험사")
    location = client.post(f"/splits/{split.id}/change", form_page="/review", nature="internal").headers["location"]
    assert "최근 변경" in client.client.get("/review").text
    change_id = int(location.rsplit("=", 1)[1])
    client.post(f"/changes/{change_id}/undo", form_page="/review")
    assert "최근 변경" not in client.client.get("/review").text
    assert "변경했습니다." not in client.client.get(location).text


def test_changes_need_a_csrf_token(client, session):
    split = split_of(session, "보험사")
    assert client.client.post(f"/splits/{split.id}/change", data={"nature": "internal"}).status_code == 403
    assert client.client.post("/changes/1/undo").status_code == 403
    assert split.nature == "expense"


@pytest.mark.parametrize(
    "back",
    [
        "https://evil.example/review",
        "//evil.example/review",
        "/\\evil.example",
        "javascript:alert(1)",
        "/settings",
        "",
    ],
)
def test_back_never_leaves_the_review_screens(client, session, back):
    split = split_of(session, "보험사")
    location = client.post(f"/splits/{split.id}/change", form_page="/review", nature=split.nature, back=back).headers["location"]
    parts = urlsplit(location)
    assert not parts.scheme and not parts.netloc
    assert parts.path in {"/review", "/transactions"}


def test_back_keeps_the_filters_of_the_list(client, session):
    split = split_of(session, "보험사")
    location = client.post(
        f"/splits/{split.id}/change", form_page="/review", nature=split.nature, back="/transactions?q=x&page=2&msg=old"
    ).headers["location"]
    assert location == "/transactions?q=x&page=2&msg=unchanged"


# --- 자산과 계좌


def test_second_export_draws_the_trend(client, session):
    assert '<polyline class="line"' not in client.client.get("/").text
    load(session, inner_name="2026-01-01~2026-03-05.xlsx", assets=[fx.AssetRow("자유입출금 자산", BANK, 2_000_000)])
    assert '<polyline class="line"' in widget(client.client.get("/").text, "net_worth")




def test_account_page_explains_where_money_went(client, session):
    bank = instrument_id(session, BANK)
    html = client.client.get(f"/accounts/{bank}").text
    trace = reports.account_trace(session, bank)
    assert f'<p class="hero">{trace.total_out:,}원</p>' in html
    assert "카드대금" in html and "페이 충전" in html and "어디로 갔는지 모르는 돈" in html
    assert "1,000,000원" in html  # 잔액
    assert f"{trace.trace_rate:.0%}" in html


def test_account_page_bars_add_up_to_the_total(client, session):
    from app.pages import _trace_bars

    trace = reports.account_trace(session, instrument_id(session, BANK))
    assert sum(bar.amount for bar in _trace_bars(trace)) == trace.total_out


def test_account_page_scopes_to_a_month_and_404s_unknown(client, session):
    bank = instrument_id(session, BANK)
    assert "이 기간에 나간 돈이 없습니다" in client.client.get(f"/accounts/{bank}?month=2026-01").text
    assert client.client.get("/accounts/999999").status_code == 404


# --- 차트


def chart_data(client) -> dict:
    response = client.client.get("/chart/data")
    assert response.status_code == 200
    return response.json()


def run_chart_js(data: dict | None = None, scenarios: list[dict] | None = None, **extra) -> dict:
    payload = {"data": data, "scenarios": scenarios or [], **extra}
    done = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def one_scenario(data: dict, **scenario) -> dict:
    return run_chart_js(data, [scenario])["scenarios"][0]


def rows_of(result: dict) -> dict[str, dict]:
    return {row["key"]: row["values"] for row in result["rows"]}


def load_snapshots(session) -> None:
    """2월 27일, 3월 5일, 3월 20일에 내보낸 자산 기록을 만든다."""
    load(session)
    for inner, amount in (("2026-01-01~2026-03-05.xlsx", 2_000_000), ("2026-01-01~2026-03-20.xlsx", 1_500_000)):
        load(session, inner_name=inner, assets=[fx.AssetRow("자유입출금 자산", BANK, amount), ASSETS[1]])


needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node가 없습니다")


def test_chart_page_loads_only_its_own_scripts(client):
    response = client.client.get("/chart")
    assert response.status_code == 200
    assert '<script src="/js/chart-data.js" defer></script>' in response.text
    assert '<script src="/js/chart-page.js" defer></script>' in response.text
    assert response.text.index("chart-data.js") < response.text.index("chart-page.js")
    assert re.findall(r'<script src="([^"]+)" defer></script>', response.text) == [
        "/js/signature.js",
        "/js/chart-data.js",
        "/js/widget-data.js",
        "/js/chart-marks.js",
        "/js/chart-widgets.js",
        "/js/chart-page.js",
    ]
    assert response.text.count("<script") == 7  # 그리기 전에 밝기를 정하는 shell.js는 defer가 없다
    assert " style=" not in response.text
    assert client.client.get("/js/chart-data.js").status_code == 200
    assert client.client.get("/js/chart-page.js").status_code == 200


@pytest.mark.parametrize("script", SCRIPTS)
def test_chart_scripts_never_build_html_from_strings(script):
    source = script.read_text(encoding="utf-8")
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert 'setAttribute("style"' not in source


def test_calculations_stay_out_of_the_drawing_script():
    assert "document" not in (ROOT / "public" / "js" / "chart-data.js").read_text(encoding="utf-8")




def test_chart_data_needs_a_login(browser):
    assert browser.client.get("/chart/data").status_code == 401


def test_chart_data_is_not_cached(client):
    assert client.client.get("/chart/data").headers["cache-control"] == "no-store"


def test_chart_data_before_the_first_import(signed_in):
    browser, _, _ = signed_in
    data = browser.client.get("/chart/data").json()
    assert data["flow_range"] is None and data["asset_range"] is None and data["finance_range"] is None
    assert data["lines"] == [] and data["snapshots"] == [] and data["finance_snapshots"] == []


def test_chart_data_never_contains_transaction_text(client):
    text = client.client.get("/chart/data").text
    for description in ("밥집", "어떤사람", "쇼핑몰"):
        assert description not in text


def test_chart_lines_add_up_to_the_reports(client, session):
    lines = reports.chart_lines(session)
    for month in reports.monthly_summary(session):
        of_month = [line for line in lines if line.day.strftime("%Y-%m") == month.month]
        assert sum(line.amount for line in of_month if line.kind == "income") == month.income
        assert sum(line.amount for line in of_month if line.kind == "spend") == month.spend_total
        for channel in labels.CHANNEL_LABELS:
            spent = sum(line.amount for line in of_month if line.kind == "spend" and line.channel == channel)
            assert spent == getattr(month, f"spend_{channel}"), channel


def test_undetermined_is_kept_out_of_the_lines(client, session):
    total = sum(line.amount for line in reports.chart_lines(session) if line.kind == "spend")
    assert total == sum(month.spend_total for month in reports.monthly_summary(session))


def test_asset_history_adds_up_to_the_snapshots(client, session):
    load_snapshots(session)
    history = reports.asset_history(session)
    for point in reports.net_worth_series(session):
        lines = [line for line in history if line.as_of == point.as_of]
        assert sum(line.amount for line in lines if line.side == "asset") == point.total_assets
        assert sum(line.amount for line in lines if line.side == "liability") == point.total_liabilities


def test_chart_options_keep_a_fixed_order(client):
    data = chart_data(client)
    kinds = [item["kind"] for item in data["categories"]]
    assert kinds == sorted(kinds, key=lambda kind: kind != "spend")
    spending = [item["total"] for item in data["categories"] if item["kind"] == "spend"]
    assert spending == sorted(spending, reverse=True)
    assert [item["key"] for item in data["channels"]] == list(labels.CHANNEL_LABELS)


def test_flow_range_reaches_the_export_date(signed_in, session):
    browser, _, _ = signed_in
    load(session, inner_name="2026-01-01~2026-03-02.xlsx")
    data = browser.client.get("/chart/data").json()
    assert data["flow_range"] == {"first": "2026-01-10", "last": "2026-03-02"}


@needs_node
def test_browser_monthly_totals_match_the_reports(client, session):
    data = chart_data(client)
    months = reports.monthly_summary(session)
    scenarios = [{**FLOW, "scale": "all"}] + [{**FLOW, "scale": "all", "channels": [channel]} for channel in labels.CHANNEL_LABELS]
    results = run_chart_js(data, scenarios)["scenarios"]
    everything = rows_of(results[0])
    for month in months:
        assert everything[month.month] == {"income": month.income, "spend": month.spend_total}
    for channel, result in zip(labels.CHANNEL_LABELS, results[1:]):
        by_month = rows_of(result)
        for month in months:
            assert by_month[month.month]["spend"] == getattr(month, f"spend_{channel}"), channel


@needs_node
def test_browser_daily_view_shows_each_day(client):
    rows = rows_of(one_scenario(chart_data(client), **FLOW, scale="day"))
    assert len(rows) == 30
    assert list(rows)[-1] == "2026-02-27"
    assert rows["2026-02-02"] == {"income": 0, "spend": 30_000}
    assert rows["2026-02-03"] == {"income": 0, "spend": -5_000}  # 환불은 그 날의 지출을 줄인다
    assert rows["2026-02-01"] == {"income": 1_000_000, "spend": 0}
    assert rows["2026-02-07"] == {"income": 0, "spend": 0}  # 거래가 없는 날도 칸이 있다


@needs_node
def test_browser_weekly_view_starts_on_monday(client):
    rows = rows_of(one_scenario(chart_data(client), **FLOW, scale="week"))
    assert rows["2026-01-26"]["income"] == 1_000_000  # 2월 1일은 일요일이라 앞 주에 들어간다
    assert rows["2026-02-02"]["spend"] == 55_000
    assert rows["2026-02-23"]["spend"] == 40_000  # 미연동 카드 대금


@needs_node
def test_browser_yearly_and_all_views_cover_the_whole_range(client, session):
    data = chart_data(client)
    months = reports.monthly_summary(session)
    year = one_scenario(data, **FLOW, scale="year")
    assert rows_of(year) == {
        "2026": {"income": sum(m.income for m in months), "spend": sum(m.spend_total for m in months)}
    }
    assert list(rows_of(one_scenario(data, **FLOW, scale="all"))) == [m.month for m in months]


def test_bucket_windows_cross_month_and_year_boundaries():
    if not shutil.which("node"):
        pytest.skip("node가 없습니다")
    result = run_chart_js(
        buckets=[
            ["day", "2025-12-30", "2026-01-02", 0],
            ["day", "2026-02-27", "2026-03-02", 2],
            ["week", "2025-12-31", "2026-01-12", 0],
            ["month", "2025-11-15", "2026-02-01", 0],
            ["month", "2025-01-15", "2026-02-01", 12],
            ["year", "2024-06-01", "2026-01-01", 0],
        ]
    )["buckets"]
    assert result[0] == ["2025-12-30", "2025-12-31", "2026-01-01", "2026-01-02"]
    assert result[1] == ["2026-03-01", "2026-03-02"]
    assert result[2] == ["2025-12-29", "2026-01-05", "2026-01-12"]
    assert result[3] == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert len(result[4]) == 12 and result[4][-1] == "2026-02"
    assert result[5] == ["2024", "2025", "2026"]


@needs_node
def test_browser_category_filter_matches_the_breakdown(client, session):
    data = chart_data(client)
    lines = reports.category_breakdown(session, "2026-02", "2026-02")
    scenarios = [{"tab": "flow", "bucket": "2026-02", "categories": [line.category_key]} for line in lines]
    for line, result in zip(lines, run_chart_js(data, scenarios)["scenarios"]):
        assert result["summary"]["spend"] == line.amount, line.category_key
        assert result["summary"]["income"] == 0


@needs_node
def test_browser_filters_add_several_choices_together(client, session):
    february = next(month for month in reports.monthly_summary(session) if month.month == "2026-02")
    result = one_scenario(chart_data(client), tab="flow", bucket="2026-02", channels=["card", "pay"])
    assert result["summary"]["spend"] == february.spend_card + february.spend_pay


@needs_node
def test_browser_instrument_filter_matches_the_transaction_list(client, session):
    bank = instrument_id(session, BANK)
    result = one_scenario(chart_data(client), tab="flow", scale="all", instruments=[bank])
    listed = reports.list_lines(session, instrument_id=bank, natures=("expense", "refund"))
    assert result["summary"]["spend"] == -listed.amount
    assert result["summary"]["income"] == 1_000_000


@needs_node
def test_browser_buttons_show_what_clicking_them_would_show(client, session):
    data = chart_data(client)
    results = run_chart_js(data, [{"tab": "flow", "bucket": "2026-02"}, {"tab": "flow", "bucket": "2026-02", "channels": ["card"]}])["scenarios"]
    february = next(month for month in reports.monthly_summary(session) if month.month == "2026-02")
    # 자기 조건은 빼고 계산하므로 카드를 골라도 다른 결제 방식의 금액이 그대로 보인다
    assert results[1]["facets"]["channels"]["pay"] == february.spend_pay
    # 다른 조건은 적용되므로 카드를 고르면 카테고리 금액은 카드 사용분만 남는다
    assert results[0]["facets"]["categories"]["shopping"] == 20_000
    assert "shopping" not in results[1]["facets"]["categories"]


@needs_node
def test_category_tab_buttons_leave_out_income(client):
    facets = one_scenario(chart_data(client))["facets"]
    assert "salary" not in facets["categories"]
    assert facets["categories"]["food"] == 25_000
    assert facets["channels"]["card"] == 65_000


@needs_node
def test_browser_cumulative_ends_at_the_total_of_the_period(client):
    data = chart_data(client)
    result = one_scenario(data, **FLOW, scale="all", form="cumulative")
    plain = one_scenario(data, **FLOW, scale="all")
    rows = list(rows_of(result).values())
    assert rows[-1] == {"income": plain["summary"]["income"], "spend": plain["summary"]["spend"]}
    assert rows[0]["spend"] <= rows[-1]["spend"]


@needs_node
def test_browser_difference_is_income_minus_spend(client, session):
    rows = rows_of(one_scenario(chart_data(client), **FLOW, scale="all", form="difference"))
    for month in reports.monthly_summary(session):
        assert rows[month.month]["difference"] == month.income - month.spend_total


@needs_node
def test_browser_stack_adds_up_to_the_spend(client, session):
    result = one_scenario(chart_data(client), **FLOW, scale="all", form="stack")
    rows = rows_of(result)
    for month in reports.monthly_summary(session):
        assert sum(rows[month.month].values()) == month.spend_total
    assert {item["key"] for item in result["legend"]} == {"card_bill_unknown", "food", "shopping", "insurance"}


@needs_node
def test_browser_stack_shows_income_when_asked(client):
    result = one_scenario(chart_data(client), **FLOW, scale="all", form="stack", flowKind="income")
    assert rows_of(result)["2026-02"]["salary"] == 1_000_000
    assert result["series"] == ["salary"]


MANY_MAJORS = ["식비", "교통", "자동차", "교육/학습", "의료/건강", "뷰티/미용", "여행/숙박", "패션/쇼핑", "생활"]


def load_many_categories(session) -> None:
    rows = [
        fx.tx(f"2026-02-{index + 1:02d} 10:00:00", "지출", major, "미분류", f"가게{index}", -(1000 * (index + 1)), CARD)
        for index, major in enumerate(MANY_MAJORS)
    ]
    load(session, rows=rows, inner_name="2026-02-01~2026-02-28.xlsx")


@needs_node
def test_browser_stack_folds_small_categories_into_rest(signed_in, session):
    browser, _, _ = signed_in
    load_many_categories(session)
    result = one_scenario(browser.client.get("/chart/data").json(), **FLOW, scale="all", form="stack")
    assert len(result["series"]) == 8
    assert result["series"][-1] == "rest"
    assert rows_of(result)["2026-02"]["rest"] == 1000 + 2000  # 가장 작은 두 카테고리
    assert sum(rows_of(result)["2026-02"].values()) == sum(1000 * (index + 1) for index in range(9))


@needs_node
def test_browser_stack_keeps_colors_when_filtered(client):
    data = chart_data(client)
    everything = one_scenario(data, **FLOW, scale="all", form="stack")
    only_pay = one_scenario(data, **FLOW, scale="all", form="stack", channels=["pay"])
    assert only_pay["series"] == ["shopping"]
    assert "shopping" in everything["series"]


@needs_node
def test_browser_average_skips_partial_periods(signed_in, session):
    browser, _, _ = signed_in
    rows = [
        fx.tx(f"2026-{month:02d}-10 10:00:00", "지출", "식비", "한식", f"밥집{month}", -amount, CARD)
        for month, amount in ((1, 1_000), (2, 20_000), (3, 40_000), (4, 3_000))
    ]
    load(session, rows=rows, inner_name="2026-01-10~2026-04-10.xlsx")
    data = browser.client.get("/chart/data").json()
    assert one_scenario(data, tab="flow", scale="month")["summary"]["average"] == 30_000  # 2월과 3월만의 평균


@needs_node
@pytest.mark.parametrize(
    ("scale", "bucket", "expected"),
    [("day", "2026-02-02", "-30,000"), ("week", "2026-02-02", "-55,000"), ("month", "2026-02", "-95,000")],
)
def test_browser_link_opens_the_same_total_in_the_list(client, scale, bucket, expected):
    result = one_scenario(chart_data(client), **FLOW, scale=scale, bucket=bucket)
    assert result_sum(client.client.get(result["link"]).text)[1] == expected


@needs_node
def test_browser_link_keeps_the_filters(client):
    result = one_scenario(chart_data(client), **FLOW, bucket="2026-02", categories=["food"], channels=["card"])
    assert result_sum(client.client.get(result["link"]).text)[1] == f"-{result['summary']['spend']:,}"


@needs_node
def test_browser_link_is_offered_only_when_the_list_can_show_it(client):
    results = run_chart_js(
        chart_data(client),
        [
            FLOW,
            {**FLOW, "bucket": "2026-02", "channels": ["card", "pay"]},
            {**FLOW, "bucket": "2026-02", "categories": ["salary"]},
            {"tab": "assets", "bucket": "2026-02"},
        ],
    )["scenarios"]
    assert results[0]["link"] is None  # 구간을 고르지 않았다
    assert results[1]["link"] is None  # 거래 내역은 조건마다 값을 하나만 받는다
    assert "nature=income" in results[2]["link"]
    assert results[3]["link"] is None  # 자산 기록은 거래가 아니다


@needs_node
def test_share_link_opens_the_same_total(client):
    data = chart_data(client)
    assert result_sum(client.client.get(one_scenario(data)["link"]).text)[1] == "-95,000"
    focused = one_scenario(data, focus="food")["link"]
    assert result_sum(client.client.get(focused).text)[1] == "-25,000"
    income = one_scenario(data, tab="flow", form="donut", flowKind="income")["link"]
    assert result_sum(client.client.get(income).text)[1].endswith("1,000,000")
    assert one_scenario(data, focus="rest")["link"] is None  # 여러 카테고리를 묶은 칸은 한 번에 열 수 없다


# --- 기간


@needs_node
def test_share_view_steps_through_periods(client):
    data = chart_data(client)
    latest = one_scenario(data)["period"]
    assert (latest["selected"], latest["previous"], latest["next"]) == ("2026-02", "2026-01", None)
    assert (latest["scopeFrom"], latest["scopeTo"]) == ("2026-02-01", "2026-02-28")
    earlier = one_scenario(data, bucket="2026-01")
    assert (earlier["period"]["previous"], earlier["period"]["next"]) == (None, "2026-02")
    assert earlier["total"] == 11_000
    assert one_scenario(data, bucket="1999-01")["period"]["selected"] == "2026-02"


@needs_node
def test_whole_period_has_no_single_bucket(client):
    result = one_scenario(chart_data(client), scale="all")
    assert result["period"]["selected"] is None
    assert result["total"] == 95_000 + 11_000


@needs_node
def test_custom_period_limits_every_number(client):
    data = chart_data(client)
    result = one_scenario(data, scale="custom", start="2026-02-02", end="2026-02-08")
    assert result["period"]["unit"] == "day" and len(result["period"]["keys"]) == 7
    assert result["total"] == 55_000
    assert result_sum(client.client.get(result["link"]).text)[1] == "-55,000"
    trend = one_scenario(data, view="trend", scale="custom", start="2026-02-02", end="2026-02-08", form="stack")
    assert sum(sum(values.values()) for values in rows_of(trend).values()) == 55_000
    assert one_scenario(data, tab="flow", scale="custom", start="2026-02-02", end="2026-02-08")["summary"]["spend"] == 55_000


@needs_node
def test_custom_period_cuts_buckets_at_its_edges(client):
    # 2월 2일은 월요일이다. 2월 3일부터 고르면 그 주의 2월 2일 거래는 들어가지 않는다.
    data = chart_data(client)
    result = one_scenario(data, **FLOW, scale="custom", start="2026-02-03", end="2026-03-20", form="bars")
    assert result["period"]["unit"] == "day" or result["period"]["unit"] == "week"
    picked = one_scenario(data, tab="flow", scale="custom", start="2026-02-03", end="2026-02-20")
    assert picked["summary"]["spend"] == -5_000 + 20_000 + 10_000


@needs_node
def test_custom_period_stays_inside_the_data(client):
    data = chart_data(client)
    wide = one_scenario(data, scale="custom", start="2000-01-01", end="2099-01-01")["period"]
    assert (wide["from"], wide["to"]) == ("2026-01-10", "2026-02-27")
    backwards = one_scenario(data, scale="custom", start="2026-02-08", end="2026-02-02")["period"]
    assert (backwards["from"], backwards["to"]) == ("2026-02-02", "2026-02-08")
    unset = one_scenario(data, scale="custom")["period"]
    assert (unset["from"], unset["to"]) == ("2026-01-10", "2026-02-27")


def blank_chart_data(first: str, last: str) -> dict:
    return {
        "flow_range": {"first": first, "last": last},
        "channels": [],
        "categories": [],
        "instruments": [],
        "lines": [],
        "asset_range": None,
        "snapshots": [],
        "asset_groups": [],
        "finance_range": None,
        "finance_snapshots": [],
        "finance_items": [],
        "finance_groups": [],
        "finance_investments": {},
    }


@needs_node
@pytest.mark.parametrize(
    ("end", "unit"),
    [("2026-02-14", "day"), ("2026-02-15", "week"), ("2026-07-19", "week"), ("2026-07-20", "month")],
)
def test_custom_period_picks_a_readable_unit(end, unit):
    data = blank_chart_data("2026-01-01", "2026-12-31")
    assert one_scenario(data, scale="custom", start="2026-01-01", end=end)["period"]["unit"] == unit


@needs_node
def test_older_bucket_moves_the_trend_window(signed_in, session):
    browser, _, _ = signed_in
    rows = [
        fx.tx(f"{year}-{month:02d}-10 10:00:00", "지출", "식비", "한식", f"밥집{year}{month}", -1_000, CARD)
        for year, month in [(2025, m) for m in range(1, 13)] + [(2026, m) for m in range(1, 7)]
    ]
    load(session, rows=rows, inner_name="2025-01-01~2026-06-30.xlsx")
    data = browser.client.get("/chart/data").json()
    keys = one_scenario(data, **FLOW, scale="month", bucket="2025-02")["period"]["keys"]
    assert keys[0] == "2025-02" and len(keys) == 12
    assert one_scenario(data, **FLOW, scale="month")["period"]["keys"][-1] == "2026-06"


@needs_node
def test_address_keeps_a_custom_period():
    query = "tab=flow&scale=custom&start=2026-02-02&end=2026-02-08&kind=income"
    result = run_chart_js(queries=[query, "scale=custom&start=abc&end=2026-02-30"])["queries"]
    assert result[0]["query"] == query
    assert (result[0]["start"], result[0]["end"], result[0]["kind"]) == ("2026-02-02", "2026-02-08", "income")
    assert (result[1]["start"], result[1]["end"]) == (None, None)


@needs_node
def test_empty_ledger_gives_empty_models():
    data = blank_chart_data("2026-01-01", "2026-01-02")
    data["flow_range"] = None
    results = run_chart_js(
        data,
        [
            {},
            {"view": "trend"},
            {"tab": "flow"},
            {**FLOW},
            {"tab": "assets"},
            {"tab": "assets", "view": "trend"},
            {"tab": "finance", "form": "sankey"},
            {"scale": "custom"},
        ],
    )["scenarios"]
    for result in results:
        assert result["rows"] == [] and result["items"] == [] and result["sankey"] is None and result["link"] is None


# --- 카테고리 탭


@needs_node
def test_category_share_matches_the_breakdown(client, session):
    result = one_scenario(chart_data(client))
    lines = reports.category_breakdown(session, "2026-02", "2026-02")
    assert {item["key"]: item["value"] for item in result["items"]} == {line.category_key: line.amount for line in lines}
    assert sum(item["share"] for item in result["items"]) == pytest.approx(1)
    assert result["total"] == 95_000
    values = [item["value"] for item in result["items"]]
    assert values == sorted(values, reverse=True)


@needs_node
def test_share_leaves_out_categories_with_more_refunds(signed_in, session):
    browser, _, _ = signed_in
    rows = [
        fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -10_000, CARD),
        fx.tx("2026-02-03 10:00:00", "지출", "교통", "택시", "환불받은 택시", 3_000, CARD),
    ]
    load(session, rows=rows, inner_name="2026-02-01~2026-02-28.xlsx")
    result = one_scenario(browser.client.get("/chart/data").json())
    assert [item["key"] for item in result["items"]] == ["food"]
    assert result["summary"]["spend"] == 7_000  # 합계에는 환불이 그대로 들어간다


@needs_node
def test_colors_stay_with_the_category(client, session):
    data = chart_data(client)
    february = {item["key"]: item["slot"] for item in one_scenario(data)["items"]}
    january = {item["key"]: item["slot"] for item in one_scenario(data, bucket="2026-01")["items"]}
    only_pay = {item["key"]: item["slot"] for item in one_scenario(data, channels=["pay"])["items"]}
    assert january["food"] == february["food"]
    assert only_pay["shopping"] == february["shopping"]
    assert widgets.category_slots(session)["food"] == february["food"]  # 홈 카드와 같은 색이다


@needs_node
def test_category_summary_names_the_largest_and_compares(client):
    data = chart_data(client)
    summary = one_scenario(data)["summary"]
    assert summary["spend"] == 95_000
    assert summary["top"] == {"name": "카드대금(상세 없음)", "share": pytest.approx(40_000 / 95_000)}
    assert summary["previous"] == 11_000
    assert one_scenario(data, bucket="2026-01")["summary"]["previous"] is None
    assert one_scenario(data, view="trend")["summary"]["previous"] is None


@needs_node
@pytest.mark.parametrize("form", ["line", "stack"])
def test_category_trend_adds_up_to_the_spend(client, session, form):
    result = one_scenario(chart_data(client), view="trend", scale="all", form=form)
    rows = rows_of(result)
    for month in reports.monthly_summary(session):
        assert sum(rows[month.month].values()) == month.spend_total
    assert set(result["series"]) == {"card_bill_unknown", "food", "shopping", "insurance"}


@needs_node
def test_legend_can_hide_a_series(client):
    data = chart_data(client)
    scenario = {"view": "trend", "scale": "all", "form": "stack"}
    assert one_scenario(data, **scenario)["domain"]["high"] == 95_000
    without = one_scenario(data, **scenario, hidden=["card_bill_unknown"])
    assert "card_bill_unknown" not in without["series"]
    assert without["domain"]["high"] == 55_000  # 숨긴 항목만큼 축도 줄어든다
    pressed = {item["key"]: item["pressed"] for item in without["legend"]}
    assert pressed["card_bill_unknown"] is False and pressed["food"] is True


@needs_node
def test_hiding_everything_still_draws_the_chart(client):
    keys = ["card_bill_unknown", "food", "shopping", "insurance"]
    result = one_scenario(chart_data(client), view="trend", scale="all", hidden=keys)
    assert set(result["series"]) == set(keys)


# --- 원형, 트리맵, 생키


def flows_of(sankey: dict) -> dict[str, int]:
    """이름표 없이 합을 비교한다. 들어온 값은 양수, 나간 값은 음수로 더해 가운데는 0이 된다."""
    balance: dict[str, int] = {}
    for link in sankey["links"]:
        balance[link["source"]] = balance.get(link["source"], 0) - link["value"]
        balance[link["target"]] = balance.get(link["target"], 0) + link["value"]
    return balance


def assert_sankey_balances(sankey: dict) -> None:
    balance = flows_of(sankey)
    columns = {node["column"] for node in sankey["nodes"]}
    first, last = min(columns), max(columns)
    keys = {node["key"]: node["column"] for node in sankey["nodes"]}
    assert set(balance) == set(keys)
    assert sum(value for key, value in balance.items() if keys[key] == first) == -sum(
        value for key, value in balance.items() if keys[key] == last
    )
    for key, value in balance.items():
        if first < keys[key] < last:
            assert value == 0, key


@needs_node
def test_category_sankey_goes_from_payment_method_to_category(client):
    result = one_scenario(chart_data(client), form="sankey")
    sankey = result["sankey"]
    assert_sankey_balances(sankey)
    balance = flows_of(sankey)
    assert balance["channel:card"] == -65_000
    assert balance["channel:pay"] == -20_000
    assert balance["channel:account"] == -10_000
    assert balance["category:food"] == 25_000
    assert sum(link["value"] for link in sankey["links"]) == result["total"] == 95_000


@needs_node
def test_flow_sankey_shows_what_is_left(client):
    sankey = one_scenario(chart_data(client), tab="flow")["sankey"]
    assert_sankey_balances(sankey)
    balance = flows_of(sankey)
    names = {node["key"]: node["name"] for node in sankey["nodes"]}
    assert names["center"] == "수입"
    assert balance["in:salary"] == -1_000_000
    assert balance["out:leftover"] == 905_000
    assert balance["out:food"] == 25_000


@needs_node
def test_flow_sankey_shows_what_was_short(client):
    sankey = one_scenario(chart_data(client), tab="flow", bucket="2026-01")["sankey"]
    assert_sankey_balances(sankey)
    balance = flows_of(sankey)
    names = {node["key"]: node["name"] for node in sankey["nodes"]}
    assert names["center"] == "수입"
    assert names["in:shortfall"] == "모자란 금액"
    assert balance["in:shortfall"] == -11_000
    assert "out:leftover" not in balance


@needs_node
def test_flow_sankey_with_a_filter_shows_only_spending(client):
    result = one_scenario(chart_data(client), tab="flow", channels=["card"])
    sankey = result["sankey"]
    assert_sankey_balances(sankey)
    assert "out:leftover" not in flows_of(sankey)
    assert sum(link["value"] for link in sankey["links"]) == 65_000


@needs_node
def test_flow_sankey_leaves_out_categories_with_more_refunds(signed_in, session):
    browser, _, _ = signed_in
    rows = [
        fx.tx("2026-02-01 09:00:00", "수입", "급여", "미분류", "회사", 100_000, BANK),
        fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -10_000, CARD),
        fx.tx("2026-02-03 10:00:00", "지출", "교통", "택시", "환불받은 택시", 3_000, CARD),
    ]
    load(session, rows=rows, inner_name="2026-02-01~2026-02-28.xlsx")
    result = one_scenario(browser.client.get("/chart/data").json(), tab="flow")
    assert_sankey_balances(result["sankey"])
    assert flows_of(result["sankey"])["out:leftover"] == 90_000


@needs_node
def test_sankey_layout_is_proportional_and_stays_in_the_box(client):
    results = run_chart_js(chart_data(client), [{"form": "sankey"}, {"tab": "flow"}, {"tab": "assets", "form": "sankey"}])
    width, height = results["sizes"]["width"], results["sizes"]["height"]
    for result in results["scenarios"]:
        layout = result["shapes"]["sankey"]
        nodes = {node["key"]: node for node in layout["nodes"]}
        for node in layout["nodes"]:
            assert 0 <= node["x0"] < node["x1"] <= width
            assert 0 <= node["y0"] and node["y1"] <= height + 0.01
            assert node["y1"] - node["y0"] == pytest.approx(node["value"] * layout["scale"])
        for link in layout["links"]:
            source, target = nodes[link["source"]], nodes[link["target"]]
            assert source["y0"] - 0.01 <= link["sy"] - link["width"] / 2
            assert link["sy"] + link["width"] / 2 <= source["y1"] + 0.01
            assert target["y0"] - 0.01 <= link["ty"] - link["width"] / 2
            assert link["ty"] + link["width"] / 2 <= target["y1"] + 0.01
            assert link["width"] == pytest.approx(link["value"] * layout["scale"])


@needs_node
def test_many_categories_keep_the_sankey_readable(signed_in, session):
    browser, _, _ = signed_in
    load_many_categories(session)
    sankey = one_scenario(browser.client.get("/chart/data").json(), form="sankey")["sankey"]
    assert_sankey_balances(sankey)
    categories = [node for node in sankey["nodes"] if node["column"] == 1]
    assert len(categories) == 8 and categories[-1]["key"] == "category:rest"


@needs_node
def test_treemap_fills_the_box_in_proportion(signed_in, session):
    browser, _, _ = signed_in
    load_many_categories(session)
    results = run_chart_js(browser.client.get("/chart/data").json(), [{"form": "treemap"}])
    width, height = results["sizes"]["width"], results["sizes"]["height"]
    result = results["scenarios"][0]
    cells = result["shapes"]["treemap"]
    assert len(cells) == 9
    assert sum(cell["width"] * cell["height"] for cell in cells) == pytest.approx(width * height)
    for cell in cells:
        assert cell["x"] >= -0.01 and cell["x"] + cell["width"] <= width + 0.01
        assert cell["y"] >= -0.01 and cell["y"] + cell["height"] <= height + 0.01
        assert cell["width"] * cell["height"] == pytest.approx(width * height * cell["share"])
    for index, one in enumerate(cells):
        for other in cells[index + 1 :]:
            apart = (
                one["x"] + one["width"] <= other["x"] + 0.01
                or other["x"] + other["width"] <= one["x"] + 0.01
                or one["y"] + one["height"] <= other["y"] + 0.01
                or other["y"] + other["height"] <= one["y"] + 0.01
            )
            assert apart, (one["key"], other["key"])


@needs_node
def test_treemap_handles_uneven_values():
    values = [1_000_000, 500_000, 3, 2, 1]
    results = run_chart_js(treemaps=[[{"key": str(value), "value": value} for value in values]])
    cells = results["treemaps"][0]
    width, height = results["sizes"]["width"], results["sizes"]["height"]
    assert len(cells) == len(values)
    assert sum(cell["width"] * cell["height"] for cell in cells) == pytest.approx(width * height)


@needs_node
def test_donut_folds_small_categories_but_keeps_picked_ones(signed_in, session):
    browser, _, _ = signed_in
    load_many_categories(session)
    data = browser.client.get("/chart/data").json()
    slices = one_scenario(data)["shapes"]["donut"]
    assert len(slices) == 8 and slices[-1]["key"] == "rest"
    assert sum(item["end"] - item["start"] for item in slices) == pytest.approx(2 * math.pi)
    assert slices[-1]["value"] == 1000 + 2000
    picked = one_scenario(data, categories=["food", "transport"])["shapes"]["donut"]
    assert {item["key"] for item in picked} == {"food", "transport"}


@needs_node
def test_donut_with_one_item_is_a_full_ring(client):
    slices = one_scenario(chart_data(client), bucket="2026-01")["shapes"]["donut"]
    assert len(slices) == 1
    assert slices[0]["path"].count("A") == 4  # 호 하나로는 원이 닫히지 않아 둘로 나눈다


@needs_node
def test_small_slices_have_no_label_inside(signed_in, session):
    browser, _, _ = signed_in
    rows = [
        fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "큰집", -94_000, CARD),
        fx.tx("2026-02-03 10:00:00", "지출", "교통", "택시", "택시", -6_000, CARD),
    ]
    load(session, rows=rows, inner_name="2026-02-01~2026-02-28.xlsx")
    slices = one_scenario(browser.client.get("/chart/data").json())["shapes"]["donut"]
    assert [item["showLabel"] for item in slices] == [True, False]


# --- 수입·지출 탭


@needs_node
def test_flow_share_follows_the_kind(client):
    data = chart_data(client)
    spend = one_scenario(data, tab="flow", form="donut")
    assert spend["caption"] == "지출" and spend["total"] == 95_000
    income = one_scenario(data, tab="flow", form="treemap", flowKind="income")
    assert income["caption"] == "수입" and income["total"] == 1_000_000
    assert [item["key"] for item in income["items"]] == ["salary"]


@needs_node
def test_flow_tab_starts_with_both_and_can_show_one(client):
    data = chart_data(client)
    both = one_scenario(data, **FLOW, scale="all")
    assert both["series"] == ["income", "spend"]
    assert [item["key"] for item in both["legend"]] == ["income", "spend"]
    income = one_scenario(data, **FLOW, scale="all", flowKind="income")
    assert income["series"] == ["income"] and income["legend"] == []
    assert rows_of(income)["2026-02"] == {"income": 1_000_000}
    spend = one_scenario(data, **FLOW, scale="all", flowKind="spend")
    assert spend["series"] == ["spend"]
    assert rows_of(spend)["2026-02"] == {"spend": 95_000}
    running = one_scenario(data, **FLOW, scale="all", flowKind="spend", form="cumulative")
    assert list(rows_of(running).values())[-1] == {"spend": 106_000}


@needs_node
def test_flow_summary_for_one_side(client):
    data = chart_data(client)
    income = one_scenario(data, tab="flow", flowKind="income")["summary"]
    assert income["income"] == 1_000_000
    assert income["top"] == {"name": "급여", "share": 1}
    assert income["previous"] == 0
    spend = one_scenario(data, tab="flow", flowKind="spend")["summary"]
    assert spend["spend"] == 95_000
    assert spend["top"]["name"] == "카드대금(상세 없음)"
    assert spend["previous"] == 11_000


@needs_node
def test_flow_buttons_follow_the_shown_side(client):
    data = chart_data(client)
    income = one_scenario(data, tab="flow", flowKind="income", bucket="2026-02")["facets"]
    assert income["channels"] == {"account": 1_000_000}
    assert set(income["categories"]) == {"salary"}
    spend = one_scenario(data, tab="flow", flowKind="spend", bucket="2026-02")["facets"]
    assert spend["channels"]["card"] == 65_000
    assert "salary" not in spend["categories"]
    both = one_scenario(data, tab="flow", bucket="2026-02")["facets"]
    assert both["channels"]["account"] == 10_000  # 둘 다 볼 때 결제 방식의 금액은 지출이다
    assert {"salary", "food"} <= set(both["categories"])


@needs_node
def test_flow_sankey_for_one_side_joins_payment_method_and_category(client):
    data = chart_data(client)
    income = one_scenario(data, tab="flow", flowKind="income")["sankey"]
    assert_sankey_balances(income)
    balance = flows_of(income)
    assert balance["channel:account"] == -1_000_000
    assert balance["category:salary"] == 1_000_000
    spend = one_scenario(data, tab="flow", flowKind="spend")["sankey"]
    assert_sankey_balances(spend)
    assert sum(link["value"] for link in spend["links"]) == 95_000
    assert "out:leftover" not in flows_of(spend)


@needs_node
def test_flow_link_follows_the_shown_side(client):
    data = chart_data(client)
    income = one_scenario(data, tab="flow", flowKind="income")["link"]
    assert "nature=income" in income
    assert result_sum(client.client.get(income).text)[1].endswith("1,000,000")
    assert "nature=spend" in one_scenario(data, tab="flow", flowKind="spend")["link"]


def test_flow_forms_and_sides_always_fit_together():
    if not shutil.which("node"):
        pytest.skip("node가 없습니다")
    results = run_chart_js(
        choices=[
            {"state": {"tab": "flow"}, "form": "donut"},
            {"state": {"tab": "flow", "view": "trend"}, "form": "stack"},
            {"state": {"tab": "flow", "view": "trend", "flowKind": "income"}, "form": "difference"},
            {"state": {"tab": "flow", "flowKind": "spend", "forms": {"flow": {"share": "donut", "trend": "stack"}}}, "kind": "all"},
            {"state": {"tab": "flow", "forms": {"flow": {"trend": "difference"}}}, "kind": "income"},
            {"state": {"tab": "flow"}, "kind": "spend"},
            {"state": {"tab": "flow", "flowKind": "income"}, "form": "treemap"},
        ]
    )["choices"]
    assert results == [
        {"kind": "spend", "share": "donut", "trend": "bars"},  # 원형은 한쪽만 그릴 수 있다
        {"kind": "spend", "share": "sankey", "trend": "stack"},
        {"kind": "all", "share": "sankey", "trend": "difference"},  # 차이는 둘 다 있어야 한다
        {"kind": "all", "share": "sankey", "trend": "bars"},
        {"kind": "income", "share": "sankey", "trend": "bars"},
        {"kind": "spend", "share": "sankey", "trend": "bars"},
        {"kind": "income", "share": "treemap", "trend": "bars"},  # 고른 쪽은 그대로 둔다
    ]


@needs_node
def test_address_keeps_the_shown_side():
    result = run_chart_js(
        queries=[
            "tab=flow&kind=income",
            "tab=flow&form=donut",
            "tab=flow&form=donut&kind=income",
            "tab=flow&view=trend&form=difference&kind=income",
            "tab=flow&kind=nope",
        ]
    )["queries"]
    assert result[0]["query"] == "tab=flow&kind=income"
    assert (result[1]["form"], result[1]["kind"]) == ("donut", "spend")
    assert result[1]["query"] == "tab=flow&form=donut&kind=spend"
    assert result[2]["query"] == "tab=flow&form=donut&kind=income"
    assert (result[3]["form"], result[3]["kind"]) == ("difference", "all")
    assert result[4]["query"] == "tab=flow"


# --- 자산 탭


ASSET_TREND = {"tab": "assets", "view": "trend"}
FINANCE_TREND = {"tab": "finance", "view": "trend"}


@needs_node
def test_asset_view_uses_the_last_record_of_each_period(client, session):
    load_snapshots(session)
    data = chart_data(client)
    points = {str(point.as_of): point for point in reports.net_worth_series(session)}
    monthly = rows_of(one_scenario(data, **ASSET_TREND, scale="month", hidden=[]))
    assert monthly["2026-02"]["net"] == points["2026-02-27"].net_worth
    assert monthly["2026-03"]["net"] == points["2026-03-20"].net_worth  # 3월 5일이 아니라 마지막 기록
    assert monthly["2026-03"]["assets"] == points["2026-03-20"].total_assets
    assert monthly["2026-03"]["liabilities"] == points["2026-03-20"].total_liabilities


@needs_node
def test_asset_view_leaves_days_without_a_record_empty(client, session):
    load_snapshots(session)
    rows = rows_of(one_scenario(chart_data(client), **ASSET_TREND, scale="day"))
    assert list(rows)[-1] == "2026-03-20"
    assert rows["2026-03-05"]["net"] is not None
    assert rows["2026-03-06"]["net"] is None  # 0원이 아니라 기록이 없는 것이다


@needs_node
def test_asset_change_compares_with_the_previous_record(client, session):
    load_snapshots(session)
    data = chart_data(client)
    points = reports.net_worth_series(session)
    daily = rows_of(one_scenario(data, **ASSET_TREND, scale="day", form="change"))
    assert daily["2026-02-27"]["change"] is None  # 첫 기록은 비교할 대상이 없다
    assert daily["2026-03-05"]["change"] == points[1].net_worth - points[0].net_worth
    assert daily["2026-03-20"]["change"] == points[2].net_worth - points[1].net_worth
    assert daily["2026-03-06"]["change"] is None


@needs_node
def test_asset_change_reaches_a_record_before_the_window(client, session):
    load_snapshots(session)
    points = reports.net_worth_series(session)
    rows = rows_of(one_scenario(chart_data(client), **ASSET_TREND, scale="week", form="change"))
    assert rows["2026-03-16"]["change"] == points[2].net_worth - points[1].net_worth


@needs_node
def test_asset_summary_shows_the_change_of_the_period(client, session):
    load_snapshots(session)
    points = reports.net_worth_series(session)
    data = chart_data(client)
    whole = one_scenario(data, **ASSET_TREND, scale="all")["summary"]
    assert whole["net"] == points[-1].net_worth
    assert whole["change"] == points[-1].net_worth - points[0].net_worth
    picked = one_scenario(data, **ASSET_TREND, scale="day", bucket="2026-03-20")["summary"]
    assert picked["net"] == points[2].net_worth
    assert picked["change"] == points[2].net_worth - points[1].net_worth


@needs_node
def test_asset_change_works_within_a_single_period(client, session):
    load_snapshots(session)
    points = reports.net_worth_series(session)
    data = chart_data(client)
    yearly = one_scenario(data, **ASSET_TREND, scale="year")
    assert yearly["summary"]["change"] == points[-1].net_worth - points[0].net_worth
    bars = one_scenario(data, **ASSET_TREND, scale="year", form="change")
    assert rows_of(bars)["2026"]["change"] == points[-1].net_worth - points[0].net_worth


@needs_node
def test_asset_period_change_starts_at_the_first_record_of_the_window(client, session):
    load_snapshots(session)
    points = reports.net_worth_series(session)
    data = chart_data(client)
    monthly = one_scenario(data, **ASSET_TREND, scale="month")
    assert monthly["summary"]["change"] == points[-1].net_worth - points[0].net_worth
    picked = one_scenario(data, **ASSET_TREND, scale="month", bucket="2026-03")
    assert picked["summary"]["change"] == points[-1].net_worth - points[0].net_worth  # 2월의 마지막 기록과 비교


@needs_node
def test_single_asset_record_has_no_change_yet(client):
    summary = one_scenario(chart_data(client), tab="assets", scale="all")["summary"]
    assert summary["net"] == 1_300_000
    assert summary["change"] is None


@needs_node
def test_asset_group_filter_and_stack_add_up(client, session):
    load_snapshots(session)
    data = chart_data(client)
    group = "asset:자유입출금 자산"
    filtered = rows_of(one_scenario(data, **ASSET_TREND, scale="all", groups=["liability:장기대출"]))
    assert filtered["2026-03"]["net"] == -200_000
    stacked = one_scenario(data, **ASSET_TREND, scale="all", form="stack")
    point = reports.net_worth_series(session)[-1]
    assert sum(rows_of(stacked)["2026-03"].values()) == point.total_assets
    assert one_scenario(data, tab="assets", scale="all")["facets"]["groups"][group] == point.total_assets


@needs_node
def test_asset_share_shows_each_group_and_its_change(client, session):
    load_snapshots(session)
    result = one_scenario(chart_data(client), tab="assets", scale="all")
    point = reports.net_worth_series(session)[-1]
    assert result["caption"] == "자산"
    assert result["asOf"] == "2026-03-20"
    assert result["total"] == point.total_assets
    item = result["items"][0]
    assert (item["key"], item["value"], item["change"]) == ("asset:자유입출금 자산", 2_000_000, 500_000)
    assert sum(entry["share"] for entry in result["items"]) == pytest.approx(1)


@needs_node
def test_asset_share_on_a_day_without_a_record_shows_the_last_one(client, session):
    load_snapshots(session)
    result = one_scenario(chart_data(client), tab="assets", scale="day", bucket="2026-03-10")
    assert result["asOf"] == "2026-03-05"
    assert result["summary"]["net"] == 2_300_000
    assert result["summary"]["change"] is None


@needs_node
def test_share_of_debts_when_only_debts_are_picked(client):
    result = one_scenario(chart_data(client), tab="assets", groups=["liability:장기대출"])
    assert result["caption"] == "부채"
    assert result["total"] == 200_000


@needs_node
def test_asset_sankey_balances_assets_with_net_worth_and_debts(client):
    sankey = one_scenario(chart_data(client), tab="assets", form="sankey")["sankey"]
    assert_sankey_balances(sankey)
    balance = flows_of(sankey)
    assert balance["in:asset:자유입출금 자산"] == -1_500_000
    assert balance["out:net"] == 1_300_000
    assert balance["out:liability:장기대출"] == 200_000


@needs_node
def test_asset_sankey_shows_a_shortfall_when_debts_are_larger(signed_in, session):
    browser, _, _ = signed_in
    load(session, assets=[fx.AssetRow("자유입출금 자산", BANK, 50_000)])
    sankey = one_scenario(browser.client.get("/chart/data").json(), tab="assets", form="sankey")["sankey"]
    assert_sankey_balances(sankey)
    balance = flows_of(sankey)
    assert balance["in:shortfall"] == -150_000
    assert "out:net" not in balance


# --- 금융 탭


def load_finance_records(session) -> None:
    """2월 27일, 3월 5일에 내보낸 기록. 입출금, 펀드, 대출이 있고 부동산은 금융이 아니다."""
    def assets(bank: int, fund: int) -> list:
        return [
            fx.AssetRow("자유입출금 자산", BANK, bank),
            fx.AssetRow("투자성 자산", "어떤 펀드", fund),
            fx.AssetRow("부동산", "아파트", 300_000_000),
        ]

    load(session, assets=assets(1_000_000, 5_000_000))
    load(session, inner_name="2026-01-01~2026-03-05.xlsx", assets=assets(1_200_000, 4_500_000))


def test_finance_history_leaves_out_physical_assets(client, session):
    load_finance_records(session)
    products = {line.product_name for line in reports.finance_history(session)}
    assert products == {BANK, "어떤 펀드", "대출상품"}


def test_finance_history_leaves_out_manual_physical_assets(client, session):
    load_finance_records(session)
    client.post("/assets/manual", form_page="/assets/manual/new", kind="other", other_type="physical", name="금 10돈", amount="5000000")
    assert "금 10돈" not in {line.product_name for line in reports.finance_history(session)}
    assert "금 10돈" not in client.client.get("/chart/data").json()["finance_items"]


def test_investment_history_adds_up_principal_and_valuation(client, session):
    load_finance_records(session)
    # 합성 파일의 투자 현황은 원금 100원, 평가금액 120원이다
    assert [(p.principal, p.valuation) for p in reports.investment_history(session)] == [(100, 120), (100, 120)]


def test_investment_history_skips_values_that_are_not_numbers(client, session):
    from app.ledger.models import AssetDetail, AssetSnapshot

    load_finance_records(session)
    snapshot = session.scalars(select(AssetSnapshot)).first()
    session.add(AssetDetail(snapshot_id=snapshot.id, section="investment", fields={"투자원금": "비공개", "평가금액": 50}))
    session.flush()
    assert reports.investment_history(session)[0].valuation == 120


def test_home_finance_card_links_to_the_finance_tab(client):
    assert 'href="/chart?tab=finance"' in widget(client.client.get("/").text, "finance")


@needs_node
def test_finance_tab_shows_net_of_financial_products_only(client, session):
    load_finance_records(session)
    rows = rows_of(one_scenario(chart_data(client), **FINANCE_TREND, scale="all", hidden=[]))
    assert rows["2026-02"] == {"net": 5_800_000, "assets": 1_000_000 + 5_000_000, "liabilities": 200_000}
    assert rows["2026-03"]["assets"] == 1_200_000 + 4_500_000
    assert rows["2026-03"]["net"] == 5_700_000 - 200_000


@needs_node
def test_finance_change_shows_plus_and_minus(client, session):
    load_finance_records(session)
    rows = rows_of(one_scenario(chart_data(client), **FINANCE_TREND, scale="day", form="change"))
    assert rows["2026-02-27"]["change"] is None
    assert rows["2026-03-05"]["change"] == -300_000  # 입출금 +200,000, 펀드 -500,000


@needs_node
def test_finance_group_and_product_filters(client, session):
    load_finance_records(session)
    data = chart_data(client)
    fund = "asset:투자성 자산:어떤 펀드"
    by_group = one_scenario(data, **FINANCE_TREND, scale="day", financeGroups=["asset:투자성 자산"], form="change")
    assert rows_of(by_group)["2026-03-05"]["change"] == -500_000
    by_product = one_scenario(data, **FINANCE_TREND, scale="all", products=[fund])
    assert rows_of(by_product)["2026-03"]["net"] == 4_500_000
    loan = one_scenario(data, **FINANCE_TREND, scale="all", financeGroups=["liability:장기대출"])
    assert rows_of(loan)["2026-03"]["net"] == -200_000


@needs_node
def test_finance_buttons_show_amount_and_change(client, session):
    load_finance_records(session)
    result = one_scenario(chart_data(client), tab="finance", scale="all")
    fund = "asset:투자성 자산:어떤 펀드"
    assert result["facets"]["products"][fund] == 4_500_000
    assert result["changes"]["products"][fund] == -500_000
    assert result["labels"]["products"][fund] == "450만 -50만"
    assert result["changes"]["financeGroups"]["asset:자유입출금 자산"] == 200_000
    assert result["labels"]["financeGroups"]["liability:장기대출"] == "20만"  # 그대로면 증감을 적지 않는다


@needs_node
def test_finance_summary_has_period_change_and_profit(client, session):
    load_finance_records(session)
    summary = one_scenario(chart_data(client), tab="finance", scale="all")["summary"]
    assert summary["net"] == 5_500_000
    assert summary["change"] == -300_000
    assert summary["profit"] == 20


@needs_node
def test_finance_profit_form_shows_gain_per_period(client, session):
    load_finance_records(session)
    rows = rows_of(one_scenario(chart_data(client), **FINANCE_TREND, scale="day", form="profit"))
    assert rows["2026-03-05"]["profit"] == 20
    assert rows["2026-03-04"]["profit"] is None  # 기록이 없는 날은 빈칸이다


@needs_node
def test_finance_stack_groups_products(client, session):
    load_finance_records(session)
    result = one_scenario(chart_data(client), **FINANCE_TREND, scale="all", form="stack")
    assert rows_of(result)["2026-03"] == {"asset:자유입출금 자산": 1_200_000, "asset:투자성 자산": 4_500_000}


@needs_node
def test_holding_colors_stay_the_same_across_forms_and_filters(client, session):
    load_finance_records(session)
    data = chart_data(client)
    share = {item["key"]: item["slot"] for item in one_scenario(data, tab="finance", scale="all")["items"]}
    stack = {item["key"]: item["slot"] for item in one_scenario(data, **FINANCE_TREND, scale="all", form="stack")["legend"]}
    assert stack == share
    group = "asset:투자성 자산"
    filtered = one_scenario(data, **FINANCE_TREND, scale="all", form="stack", financeGroups=[group])["legend"]
    assert [(item["key"], item["slot"]) for item in filtered] == [(group, share[group])]


@needs_node
def test_stack_of_debts_when_only_debts_are_picked(client, session):
    load_finance_records(session)
    loan = "liability:장기대출"
    result = one_scenario(chart_data(client), **FINANCE_TREND, scale="all", form="stack", financeGroups=[loan])
    assert rows_of(result)["2026-03"][loan] == 200_000


@needs_node
def test_finance_tab_counts_coins_entered_by_hand(client, session):
    load_finance_records(session)
    client.post("/assets/manual", form_page="/assets/manual/new", kind="other", other_type="coin", name="비트코인", amount="3000000")
    client.post("/assets/manual", form_page="/assets/manual/new", kind="other", other_type="physical", name="금 10돈", amount="5000000")
    data = chart_data(client)
    result = one_scenario(data, tab="finance", scale="all")
    coin = "asset:코인 (직접 입력)"
    assert next(item for item in result["items"] if item["key"] == coin)["value"] == 3_000_000
    assert "asset:실물 (직접 입력)" not in {item["key"] for item in result["items"]}
    assert result["summary"]["net"] == 5_500_000 + 3_000_000
    assert result["summary"]["profit"] == 20  # 직접 입력한 날에도 그 전 내보내기의 투자 손익이 이어진다
    assert result["facets"]["products"][f"{coin}:비트코인"] == 3_000_000
    assert data["finance_range"]["last"] == "2026-11-05"


@needs_node
def test_finance_share_leaves_out_physical_assets(client, session):
    load_finance_records(session)
    result = one_scenario(chart_data(client), tab="finance", scale="all")
    assert {item["key"] for item in result["items"]} == {"asset:자유입출금 자산", "asset:투자성 자산"}
    assert result["total"] == 5_700_000
    assert result["caption"] == "금융 자산"


@needs_node
def test_finance_treemap_splits_groups_into_products(client, session):
    load(session, inner_name="2026-01-01~2026-03-05.xlsx")  # 입출금 항목에 통장이 둘이다
    results = run_chart_js(chart_data(client), [{"tab": "finance", "scale": "all", "form": "treemap"}])
    result = results["scenarios"][0]
    group = "asset:자유입출금 자산"
    item = next(entry for entry in result["items"] if entry["key"] == group)
    assert [child["value"] for child in item["children"]] == [1_000_000, 500_000]
    cells = [cell for cell in result["shapes"]["treemap"] if cell["group"] == group]
    frame = next(cell for cell in cells if not cell["leaf"])
    leaves = [cell for cell in cells if cell["leaf"]]
    assert len(leaves) == 2
    inside = frame["width"] * (frame["height"] - frame["header"])
    assert sum(cell["width"] * cell["height"] for cell in leaves) == pytest.approx(inside)
    assert leaves[0]["width"] * leaves[0]["height"] == pytest.approx(inside * 2 / 3)
    assert {cell["slot"] for cell in cells} == {frame["slot"]}
    assert sum(cell["share"] for cell in leaves) == pytest.approx(item["share"])


@needs_node
def test_asset_tab_buttons_also_show_change(client, session):
    load_snapshots(session)
    result = one_scenario(chart_data(client), tab="assets", scale="all")
    assert result["changes"]["groups"]["asset:자유입출금 자산"] == 1_500_000 + 500_000 - 1_500_000


@needs_node
def test_chart_without_any_value_is_left_empty(client):
    data = chart_data(client)
    assert one_scenario(data, **FINANCE_TREND, scale="all")["filled"] is True
    data["finance_investments"] = {}
    assert one_scenario(data, **FINANCE_TREND, scale="all", form="profit")["filled"] is False


def test_screens_do_not_narrate_the_data(client):
    home = client.client.get("/").text
    for sentence in ("미확정 금액", "두 번 세는 돈", "카드를 옮기거나", "연동이 끊겨서"):
        assert sentence not in home
    script = (ROOT / "public" / "js" / "chart-page.js").read_text(encoding="utf-8")
    for sentence in ("데이터입니다", "기록 기준입니다", "금융 상품만", "합계에 넣지", "비중에서 뺐습니다"):
        assert sentence not in script


@needs_node
def test_browser_formats_match_the_server():
    values = [0, 9_500, 10_000, 1_234_000, 12_000_000, 100_000_000, 150_000_000, 285_000_000, 286_549_000, -2_500_000]
    highs = [0, 1, 950_000, 1_000_000, 3_000_000, 23_456_789]
    result = run_chart_js(compact=values, scales=[[0, high] for high in highs])
    assert result["compact"] == [compact_won(value) for value in values]
    for high, scale in zip(highs, result["scales"]):
        assert scale["ticks"][1] - scale["ticks"][0] == charts.nice_step(high)
        assert scale["top"] >= high
        assert scale["bottom"] == 0


@needs_node
def test_address_keeps_the_choices():
    query = "tab=assets&view=trend&scale=day&form=change&bucket=2026-02-03&channel=card&category=food&instrument=7&group=x"
    result = run_chart_js(queries=[query])["queries"][0]
    assert result["query"] == query
    assert (result["tab"], result["view"], result["scale"], result["form"]) == ("assets", "trend", "day", "change")
    assert result["instruments"] == [7]


@needs_node
def test_address_keeps_conditions_chosen_all_at_once():
    query = "tab=flow&channel=card&all=category&all=instrument"
    assert run_chart_js(queries=[query])["queries"][0]["query"] == query
    assert run_chart_js(queries=["all=nope"])["queries"][0]["query"] == ""


@needs_node
def test_values_written_on_the_chart_are_short():
    result = run_chart_js(directLabels=[313_880_000, -123_456_789, 14_900_000, 2_586, 0])["directLabels"]
    assert result == ["3.14억", "-1.23억", "1,490만", "2,586", "0"]


@needs_node
def test_address_keeps_the_large_view():
    result = run_chart_js(queries=["tab=flow&size=large", "size=nope"])["queries"]
    assert result[0]["query"] == "tab=flow&size=large"
    assert result[1]["query"] == ""


def test_chart_shape_widget_keeps_the_large_view_button():
    script = (ROOT / "public" / "js" / "chart-page.js").read_text(encoding="utf-8")
    assert '"widget-link chart-large"' in script and "크게 보기" in script


def test_chart_conditions_are_shown_without_opening_anything(client):
    html = client.client.get("/chart").text
    assert "<details" not in html
    assert html.index("data-controls") < html.index("data-filters") < html.index("data-widgets")
    script = (ROOT / "public" / "js" / "chart-page.js").read_text(encoding="utf-8")
    assert "조건으로 나눠" not in script


def chart_title(data, **scenario):
    return one_scenario(data, **scenario)["title"]


def test_chart_titles_name_what_is_shown(client):
    data = chart_data(client)
    bucket = {"bucket": "2026-02"}
    assert chart_title(data, **bucket) == "2026년 2월 카테고리 비중"
    assert chart_title(data, tab="flow", **bucket) == "2026년 2월 수입·지출 흐름"
    assert chart_title(data, tab="flow", flowKind="spend", form="donut", **bucket) == "2026년 2월 지출 비중"
    assert chart_title(data, tab="assets", **bucket) == "2026년 2월 자산 비중"
    assert chart_title(data, view="trend") == "월별 카테고리"
    assert chart_title(data, view="trend", scale="day") == "일별 카테고리"
    assert chart_title(data, **FLOW) == "월별 수입·지출"
    assert chart_title(data, **FLOW, flowKind="income") == "월별 수입"
    assert chart_title(data, **FLOW, form="difference") == "월별 수입 - 지출"
    assert chart_title(data, **FLOW, form="cumulative", flowKind="spend") == "누적 지출"
    assert chart_title(data, tab="assets", view="trend", form="change") == "월별 자산 증감"
    assert chart_title(data, tab="finance", view="trend", form="profit") == "월별 평가 손익"


@needs_node
def test_choosing_every_shown_item_counts_as_all():
    result = run_chart_js(
        choosing=[
            {"keys": ["a"], "shown": ["a", "b"]},
            {"keys": ["a", "b"], "shown": ["a", "b"]},
            {"keys": [], "shown": ["a", "b"]},
            {"keys": ["b"], "shown": ["a", "b"], "all": True},
            {"keys": [], "shown": []},
        ]
    )["choosing"]
    assert result == [
        {"before": [], "set": ["a"], "all": False},
        {"before": [], "set": [], "all": True},
        {"before": [], "set": [], "all": False},
        {"before": ["a", "b"], "set": ["b"], "all": False},
        {"before": [], "set": [], "all": False},
    ]


@needs_node
def test_amount_is_read_in_korean_units():
    readings = run_chart_js(readings=[1000, 31000, "12,400,000", 0, 1234567890, "50000000원", "abc", ""])["readings"]
    assert readings == ["1000원", "3만 1000원", "1240만", "0원", "12억 3456만 7890원", "5000만", "", ""]


def test_records_screen_has_the_new_name(client):
    html = client.client.get("/transactions").text
    assert "<title>기록 · 가계부</title>" in html
    assert "<h1>기록</h1>" in html


@needs_node
def test_finance_address_is_kept():
    query = "tab=finance&view=trend&form=profit&fgroup=a&product=b"
    assert run_chart_js(queries=[query])["queries"][0]["query"] == query


@needs_node
def test_address_ignores_unknown_choices_and_omits_defaults():
    result = run_chart_js(
        queries=[
            "tab=nope&view=nope&scale=nope&form=nope",
            "tab=category&view=share&scale=month&form=donut",
            "tab=assets&view=trend&form=difference",
            "form=bars",
        ]
    )["queries"]
    assert result[0]["query"] == ""
    assert (result[0]["tab"], result[0]["view"], result[0]["scale"], result[0]["form"]) == ("category", "share", "month", "donut")
    assert result[1]["query"] == ""
    assert result[2]["form"] == "line"  # 차이는 수입·지출 탭에만 있다
    assert result[2]["query"] == "tab=assets&view=trend"
    assert result[3]["form"] == "donut"  # 막대는 비중의 모양이 아니다


@needs_node
def test_axis_covers_negative_values():
    scale = run_chart_js(scales=[[-250_000, 900_000]])["scales"][0]
    assert scale["bottom"] <= -250_000 and scale["top"] >= 900_000
    assert 0 in scale["ticks"]




# --- 거래 내역의 날짜 범위


def test_transactions_can_be_limited_to_dates(client):
    html = client.client.get("/transactions?start=2026-02-02&end=2026-02-08&nature=spend").text
    assert result_sum(html) == ("4", "-55,000")
    assert '<input type="date" name="start" value="2026-02-02">' in html
    assert '<input type="date" name="end" value="2026-02-08">' in html


def test_bad_dates_are_ignored(client):
    for query in ("start=abc", "end=2026-13-01", "start=2026-02-30"):
        html = client.client.get(f"/transactions?{query}").text
        assert 'name="start" value=""' in html and 'name="end" value=""' in html


def test_date_pickers_are_always_on_the_list(client):
    html = client.client.get("/transactions").text
    assert '<input type="date" name="start" value="">' in html
    assert '<input type="date" name="end" value="">' in html
    assert 'name="month"' not in html


def test_reversed_dates_are_put_in_order(client):
    html = client.client.get("/transactions?start=2026-02-08&end=2026-02-02&nature=spend").text
    assert result_sum(html) == ("4", "-55,000")
    assert '<input type="date" name="start" value="2026-02-02">' in html


def test_one_sided_date_range_works(client):
    assert result_sum(client.client.get("/transactions?start=2026-02-27&nature=internal").text)[0] == "2"
    assert result_sum(client.client.get("/transactions?end=2026-01-31").text) == ("1", "-11,000")


def test_month_address_becomes_a_date_range(client):
    html = client.client.get("/transactions?month=2026-02&q=밥집").text
    assert result_sum(html) == ("2", "-25,000")
    assert '<input type="date" name="start" value="2026-02-01">' in html
    assert '<input type="date" name="end" value="2026-02-28">' in html
    picked = client.client.get("/transactions?month=2026-02&start=2026-02-03&q=밥집").text
    assert result_sum(picked) == ("1", "+5,000")  # 직접 고른 날짜가 먼저다


def test_date_presets_keep_the_other_conditions(client):
    html = client.client.get("/transactions", params={"q": "밥집"}).text  # 오늘은 2026년 11월 5일이다
    assert 'href="/transactions?q=%EB%B0%A5%EC%A7%91&amp;start=2026-11-01&amp;end=2026-11-30"' in html
    assert 'href="/transactions?q=%EB%B0%A5%EC%A7%91&amp;start=2026-10-01&amp;end=2026-10-31"' in html
    assert "aria-current=\"true\"" not in html
    active = client.client.get("/transactions?start=2026-10-01&end=2026-10-31").text
    assert 'end=2026-10-31" aria-current="true">지난 달<' in active


def test_paging_keeps_the_dates(client, session):
    rows = [fx.tx(f"2026-02-0{1 + i % 9} 11:{i % 60:02d}:{i // 60:02d}", "지출", "식비", "한식", f"가게{i}", -100, CARD) for i in range(60)]
    load(session, rows=[*LEDGER, *rows], inner_name="2026-01-01~2026-02-28.xlsx")
    html = client.client.get("/transactions?start=2026-02-01&end=2026-02-28").text
    assert "start=2026-02-01&amp;end=2026-02-28&amp;page=2" in html


# --- 보안


def test_pages_with_data_have_no_inline_code(client, session):
    bank = instrument_id(session, BANK)
    for path in (*PAGES, "/review?queue=largest", f"/accounts/{bank}"):
        html = client.client.get(path).text
        assert not INLINE_SCRIPT.search(html), path
        assert "<style" not in html, path
        assert " style=" not in html, path
        assert " onclick=" not in html, path


def test_transaction_text_is_escaped(client, session):
    attack = '<script>alert("x")</script>'
    load(session, rows=[*LEDGER, fx.tx("2026-02-28 10:00:00", "지출", "식비", "한식", attack, -1, CARD)], inner_name="2026-01-01~2026-02-28.xlsx")
    for path in ("/transactions", "/transactions?q=" + "%3Cscript%3E"):
        html = client.client.get(path).text
        assert attack not in html
        assert "&lt;script&gt;" in html


def test_expired_session_sends_a_browser_form_to_login(client, clock, session):
    split = split_of(session, "보험사")
    token = client.csrf("/review")
    clock.advance(hours=13)
    response = client.client.post(
        f"/splits/{split.id}/change",
        data={"csrf_token": token, "nature": "internal"},
        headers={"accept": "text/html,application/xhtml+xml"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert split.nature == "expense"


def test_expired_session_still_answers_json_to_api_clients(client, clock):
    token = client.csrf("/review")
    clock.advance(hours=13)
    response = client.client.post("/splits/1/change", data={"csrf_token": token, "nature": "internal"})
    assert response.status_code == 401
    assert response.json() == {"detail": "로그인이 필요합니다."}


# --- 하단 서명. 라이선스 조건이라 실수로 바뀌면 여기서 멈춘다.


SIGNATURE_LINKS = [
    ("https://jinsolkim-archive.vercel.app/ko", "블로그"),
    ("https://www.instagram.com/handler_jsk/", "Instagram"),
    ("mailto:kr.jsk.edu@gmail.com", "이메일"),
    ("https://www.linkedin.com/in/jinsol-kim-3ab154372/", "LinkedIn"),
    ("https://x.com/Handler_JSK", "X"),
    ("https://github.com/jinsol-git", "GitHub"),
]


def signature_of(html: str) -> str:
    match = re.search(r'<footer class="signature" data-signature>(.*?)</footer>', html, re.S)
    assert match, "하단 서명이 없습니다"
    return match.group(1)


@pytest.mark.parametrize("path", ["/", "/chart", "/transactions", "/review", "/recurring", "/settings", "/assets/manual/new"])
def test_signature_is_on_every_screen(client, path):
    footer = signature_of(client.client.get(path).text)
    assert re.findall(r'<a href="([^"]+)"[^>]*aria-label="([^"]+)"', footer) == SIGNATURE_LINKS
    assert "© JINSOL KIM" in footer


def test_signature_mark_is_a_button_that_opens_the_links(client):
    footer = signature_of(client.client.get("/").text)
    assert '<ul class="signature-links" id="signature-links">' in footer
    assert '<button class="signature-mark" type="button" aria-expanded="false" aria-controls="signature-links">' in footer
    assert footer.index("signature-links") < footer.index("signature-mark")  # 링크는 버튼의 왼쪽으로 밀려 나온다
    assert client.client.get("/js/signature.js").status_code == 200


def test_signature_is_on_the_login_screen(browser):
    assert "© JINSOL KIM" in signature_of(browser.client.get("/auth/login").text)


def test_signature_links_do_not_expose_this_page_to_the_opened_site(client):
    footer = signature_of(client.client.get("/").text)
    outside = re.findall(r'<a href="(https://[^"]+)"([^>]*)>', footer)
    assert len(outside) == 5
    for _, attributes in outside:
        assert 'target="_blank"' in attributes and 'rel="noopener noreferrer"' in attributes


def test_signature_has_no_inline_code():
    source = (ROOT / "app" / "templates" / "_footer.html").read_text(encoding="utf-8")
    for code in ("<script", "<style", " style=", " onclick="):
        assert code not in source


def test_font_is_served_from_this_site_with_its_license(client, browser):
    fonts = ROOT / "public" / "fonts"
    assert (fonts / "PretendardVariable.woff2").read_bytes()[:4] == b"wOF2"
    assert "SIL Open Font License" in (fonts / "LICENSE-Pretendard.txt").read_text(encoding="utf-8")
    css = (ROOT / "public" / "css" / "app.css").read_text(encoding="utf-8")
    assert 'url("/fonts/PretendardVariable.woff2")' in css
    assert "http://" not in css and "https://" not in css  # 다른 주소의 글꼴과 스타일은 CSP가 막는다
    page = client.client.get("/")
    assert "font-src 'self'" in page.headers["content-security-policy"]
    assert '<link rel="preload" href="/fonts/PretendardVariable.woff2" as="font" type="font/woff2" crossorigin>' in page.text
    font = client.client.get("/fonts/PretendardVariable.woff2")
    assert font.status_code == 200 and font.content[:4] == b"wOF2"
    assert font.headers.get("cache-control") != "no-store"  # 금융 데이터가 아니므로 기기에 남겨도 된다


def test_font_does_not_need_a_login(browser):
    assert browser.client.get("/fonts/PretendardVariable.woff2").status_code == 200  # 로그인 화면도 같은 글꼴을 쓴다


def test_license_forbids_changing_the_signature():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "app/templates/_footer.html" in license_text
    assert "© JINSOL KIM" in license_text
    assert "바꾸면 안 됩니다" in license_text
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## 라이선스" in readme
    assert "[LICENSE](LICENSE)" in readme
    assert "© JINSOL KIM" in readme


# --- 표기와 차트


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0"),
        (9_500, "9,500"),
        (10_000, "1만"),
        (1_234_000, "123만"),
        (12_000_000, "1,200만"),
        (100_000_000, "1억"),
        (150_000_000, "1.5억"),
        (285_000_000, "2.85억"),
        (286_549_000, "2.8655억"),
        (-2_500_000, "-250만"),
    ],
)
def test_compact_won(value, expected):
    assert compact_won(value) == expected


def test_every_classifier_reason_has_a_label():
    source = (Path(__file__).parent.parent / "app" / "ledger" / "classify.py").read_text(encoding="utf-8")
    reasons = set(re.findall(r'return "\w+", "(\w+)", ', source))
    assert len(reasons) >= 15
    assert reasons <= set(labels.REASON_LABELS)


def test_reason_label_handles_generated_reasons():
    assert labels.reason_label(None) == ""
    assert labels.reason_label("rule:3") == "규칙으로 정한 값입니다."
    assert labels.reason_label("change_set:9") == "직접 바꾼 값입니다."


def test_line_needs_two_points():
    assert charts.line_chart([]) is None
    assert charts.line_chart([(date(2026, 1, 1), 100)]) is None


def test_line_spaces_points_by_date_and_stays_in_the_plot():
    start = date(2026, 1, 1)
    chart = charts.line_chart([(start, 1_000), (start + timedelta(days=3), -2_000), (start + timedelta(days=12), 5_000)])
    assert [point.x for point in chart.points] == [0, 25, 100]
    assert all(charts.PLOT_TOP <= point.y <= chart.baseline for point in chart.points)
    assert chart.points[1].y > chart.points[0].y > chart.points[2].y
    assert chart.ticks[0].value <= -2_000 and chart.ticks[-1].value >= 5_000
    assert chart.last_value == "5,000"


def test_flat_line_has_a_range():
    chart = charts.line_chart([(date(2026, 1, 1), 500), (date(2026, 1, 2), 500)])
    assert chart.points[0].y == chart.points[1].y
    assert chart.ticks[-1].value > chart.ticks[0].value


def test_stack_splits_the_whole_and_keeps_negative_amounts():
    segments = charts.stack([("card", "카드", 75), ("account", "계좌", 25), ("pay", "페이", -10), ("other", "기타", 0)])
    assert [segment.width for segment in segments] == [75, 25, 0, 0]
    assert [segment.x for segment in segments] == [0, 75, 100, 100]
    assert segments[2].value == -10


def test_empty_stack_has_no_shares():
    assert all(segment.share is None and segment.width == 0 for segment in charts.stack([("card", "카드", 0)]))


# --- 대시보드 디자인: 바, 공지, 달력, 장부


def header_of(html: str) -> str:
    return re.search(r'<header class="topbar">.*?</header>', html, re.S).group(0)


def dock_of(html: str) -> str:
    return re.search(r'<nav class="dock glass".*?</nav>', html, re.S).group(0)


def book_of(html: str) -> str:
    return re.search(r'<section class="card book" id="book">(.*?)</section>', html, re.S).group(1)


def amount_of(text: str) -> int:
    return int(text.replace(",", ""))


def test_phone_bar_has_five_places_and_marks_the_current_one(client):
    for path, label in (
        ("/", "홈"),
        ("/chart?tab=flow", "차트"),
        ("/chart?tab=finance", "차트"),
        ("/transactions", "기록"),
        ("/review", "검토"),
        ("/settings", "설정"),
    ):
        dock = dock_of(client.client.get(path).text)
        assert re.findall(r"<span>([^<]+)</span>", dock) == ["홈", "차트", "기록", "검토", "설정"]
        assert re.findall(r'aria-current="page">\s*<svg.*?</svg>\s*<span>([^<]+)</span>', dock, re.S) == [label], path


def test_chart_page_keeps_the_four_tabs_for_phones(client):
    html = client.client.get("/chart?tab=assets").text
    tabs = re.search(r'<nav class="domain-tabs".*?</nav>', html, re.S).group(0)
    assert re.findall(r'data-chart-tab="(\w+)"', tabs) == ["category", "flow", "assets", "finance"]
    assert re.findall(r'data-chart-tab="(\w+)" aria-current="page"', tabs) == ["assets"]
    # 넓은 화면의 메뉴와 휴대폰의 탭이 같은 표시로 눌려야 다시 불러오지 않고 탭이 바뀐다.
    assert len(re.findall(r'data-chart-tab="assets"', html)) == 2


def test_day_and_night_switch_sits_left_of_settings(client):
    html = client.client.get("/").text
    top = header_of(html)
    assert top.index("data-theme-toggle") < top.index('href="/settings"') < top.index('action="/auth/logout"')
    assert "theme-toggle" not in dock_of(html)
    head = html.split("</head>")[0]
    # 그리기 전에 밝기를 정해야 하므로 스타일보다 먼저, defer 없이 불러온다.
    assert '<script src="/js/shell.js"></script>' in head
    assert head.index("/js/shell.js") < head.index("/css/app.css")


def test_login_screen_has_no_menu(browser):
    html = browser.client.get("/auth/login").text
    assert "data-theme-toggle" not in html
    assert 'class="dock' not in html
    assert "has-dock" not in html
    assert "/js/shell.js" in html  # 고른 밝기는 로그인 화면에도 적용된다


def test_menu_counts_transactions_waiting_for_review(client):
    for path in ("/", "/chart", "/transactions", "/settings"):
        html = client.client.get(path).text
        assert re.findall(r'<sup class="cnt"[^>]*>(\d+)</sup>', html) == ["1", "1"], path  # 위 메뉴와 아래 바


def test_menu_shows_no_count_when_nothing_waits(signed_in):
    browser, _, _ = signed_in
    assert 'class="cnt"' not in browser.client.get("/").text


def test_current_device_can_log_out_from_settings(client):
    html = client.client.get("/settings").text
    devices = re.search(r"<h2>로그인한 기기</h2>(.*?)</ul>", html, re.S).group(1)
    assert 'action="/auth/logout"' in devices  # 휴대폰의 위 바에는 로그아웃이 없다


def test_home_notices_are_capsules(signed_in):
    browser, _, _ = signed_in
    capsule = re.search(r'<aside class="capsule tone-attention" data-warning>.*?</aside>', browser.client.get("/").text, re.S).group(0)
    assert '<span class="capsule-tag">시작 전</span>' in capsule
    assert "아직 가져온 데이터가 없습니다" in capsule


def test_a_stopped_signal_is_told_with_the_error_tone(browser, signed_in, session, clock):
    from app.ops.state import system_warnings

    state.set_state(session, clock() - timedelta(days=9), "ingest_heartbeat")
    warning = next(item for item in system_warnings(session, clock()) if "9일째" in item.text)
    assert (warning.tag, warning.tone) == ("멈춤", "error")


def test_finished_message_is_a_capsule(client):
    html = client.client.get("/?msg=manual_added").text
    assert '<span class="capsule-tag">완료</span>' in html and "자산을 추가했습니다." in html


def test_home_offers_calendar_and_paint_tabs(client):
    html = client.client.get("/?month=2026-02").text
    assert re.findall(r'<label for="dash-(\w+)">(\S+)</label>', html) == [("calendar", "달력"), ("paint", "물감")]
    assert re.search(r'id="dash-calendar"[^>]* checked', html)  # 스크립트 없이도 탭이 바뀐다
    assert 'data-month="2026-02"' in html
    scripts = re.findall(r'<script src="/js/([\w-]+)\.js" defer>', html)
    assert scripts[-6:] == ["chart-data", "widget-data", "chart-marks", "chart-widgets", "chart-page", "dashboard"]
    assert " style=" not in html  # CSP가 막는다


def test_home_without_data_loads_no_dashboard(signed_in):
    browser, _, _ = signed_in
    html = browser.client.get("/").text
    assert "dashboard.js" not in html and "data-dashboard" not in html and 'id="book"' not in html


def calendar_of(html: str) -> str:
    return re.search(r'<div class="cal" .*?<p class="cal-legend">', html, re.S).group(0)


def test_calendar_days_add_up_and_open_the_same_total(client, session):
    html = client.client.get("/?month=2026-02").text
    february = reports.monthly_summary(session)[-1]
    flows = reports.daily_flow(session, "2026-02")
    assert sum(flow.spend for flow in flows.values()) == february.spend_total == 95_000
    assert sum(flow.income for flow in flows.values()) == february.income == 1_000_000
    for day, flow in flows.items():
        for nature, amount in (("spend", -flow.spend), ("income", flow.income)):
            if amount:
                link = f"/transactions?start={day}&end={day}&nature={nature}"
                assert f'href="{link.replace("&", "&amp;")}"' in html
                assert amount_of(result_sum(client.client.get(link).text)[1]) == amount


def test_calendar_marks_income_and_spending_with_dots_only(client):
    html = client.client.get("/?month=2026-02").text
    calendar = calendar_of(html)
    income_day = re.search(r'data-day="2026-02-01".*?</a>', calendar, re.S).group(0)
    spend_day = re.search(r'data-day="2026-02-02".*?</a>', calendar, re.S).group(0)
    assert "dot-income" in income_day and "dot-spend" not in income_day and "수입 1,000,000원" in income_day
    assert "dot-spend" in spend_day and "dot-income" not in spend_day and "지출 30,000원" in spend_day
    assert "<small>" not in calendar  # 금액은 날짜를 누르면 나온다
    assert '<i class="dot dot-income"></i>수입</span><span><i class="dot dot-spend"></i>지출</span></p>' in html


def test_calendar_separates_days_without_transactions_from_days_without_records(client):
    calendar = calendar_of(client.client.get("/?month=2026-02").text)  # 2월 27일까지 받았고 2026년 2월은 28일까지다
    assert '<span class="cal-day is-none" title="2월 7일 토요일 거래 없음">' in calendar
    assert '<span class="cal-day is-unknown" title="2월 28일 토요일 기록 없음">' in calendar


def test_calendar_starts_on_sunday(client):
    february = calendar_of(client.client.get("/?month=2026-02").text)
    assert re.findall(r'<div class="cal-weekday">(\S)</div>', february) == list("일월화수목금토")
    assert february.count("cal-blank") == 0  # 2026년 2월은 일요일에 시작해 토요일에 끝난다
    assert calendar_of(client.client.get("/?month=2026-05").text).count("cal-blank") == 11  # 금요일에 시작해 일요일에 끝난다


def test_day_detail_lists_income_and_spending_and_opens_on_the_latest_day(client):
    html = client.client.get("/?month=2026-02").text
    assert re.findall(r'<div class="day-detail" data-day-detail="([\d-]+)">', html) == ["2026-02-26"]
    assert len(re.findall(r'<div class="day-detail" data-day-detail="[\d-]+" hidden>', html)) >= 3
    assert 'data-day="2026-02-26" aria-label="2월 26일 목요일 지출 40,000원" aria-current="date"' in html
    spend = re.search(r'data-day-detail="2026-02-02".*?</ul>', html, re.S).group(0)
    assert "밥집" in spend and "30,000원" in spend and CARD in spend
    income = re.search(r'data-day-detail="2026-02-01".*?</ul>', html, re.S).group(0)
    assert "회사" in income and "+1,000,000원" in income


def test_calendar_of_a_month_without_records_has_no_selected_day(client):
    html = client.client.get("/?month=2026-05").text
    assert "data-day=" not in html and "data-day-detail" not in html
    assert "이 달의 거래가 없습니다." in re.search(r'<div class="day-box".*?</div>', html, re.S).group(0)


def test_book_totals_match_the_summary_and_the_net_worth(client, session):
    book = book_of(client.client.get("/?month=2026-02").text)
    february = reports.monthly_summary(session)[-1]
    totals = [amount_of(value) for value in re.findall(r'book-total">.*?book-amount">([\d,\-]+)<', book, re.S)]
    point = reports.net_worth_on(session, date(2026, 11, 5))
    assert totals == [february.income, february.spend_total, point.total_assets, point.total_liabilities]
    ends = dict(re.findall(r'<p class="book-end"><span>([^<]+)</span><strong>([\d,\-]+)</strong>', book))
    assert amount_of(ends["수입 - 지출"]) == february.income - february.spend_total == 905_000
    assert amount_of(ends["순자산"]) == point.net_worth


def test_book_rows_add_up_and_open_the_same_total(client, session):
    book = book_of(client.client.get("/?month=2026-02").text)
    rows = re.findall(r'<a class="book-row" href="(/transactions[^"]+)">.*?book-amount">([\d,\-]+)<', book, re.S)
    assert {link.split("nature=")[1].split("&")[0] for link, _ in rows} == {"income", "spend"}
    assert sum(amount_of(amount) for link, amount in rows if "nature=income" in link) == 1_000_000
    assert sum(amount_of(amount) for link, amount in rows if "nature=spend" in link) == 95_000
    for link, amount in rows:
        assert abs(int(result_sum(client.client.get(link.replace("&amp;", "&")).text)[1].replace(",", ""))) == amount_of(amount)


def test_book_lists_holdings_by_the_home_cards_and_links_to_them(client):
    book = book_of(client.client.get("/?month=2026-02").text)
    assert '<a class="book-row" href="#widget-accounts">' in book
    assert "계좌·현금" in book
    change_card(client, "accounts", "hide")
    hidden = book_of(client.client.get("/?month=2026-02").text)
    assert "#widget-accounts" not in hidden and "계좌·현금" in hidden  # 카드를 빼도 장부의 합계는 그대로다


def test_book_has_no_change_line_without_an_earlier_record(client):
    assert "전달보다" not in book_of(client.client.get("/?month=2026-01").text)


def test_month_bars_move_between_months_and_keep_the_selected_one_in_view(client, session):
    html = client.client.get("/?month=2026-01").text
    bars = re.search(r'<nav class="month-bars".*?</nav>', html, re.S).group(0)
    assert re.findall(r'href="/\?month=([\d-]+)"', bars) == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11"]
    assert re.findall(r'href="/\?month=([\d-]+)"[^>]*aria-current="true"', bars) == ["2026-01"]
    assert 'aria-label="2026년 2월 95,000원"' in bars


def test_month_bars_keep_edit_mode(client):
    bars = re.search(r'<nav class="month-bars".*?</nav>', client.client.get("/?edit=1").text, re.S).group(0)
    assert 'href="/?month=2026-01&amp;edit=1"' in bars


def test_address_keeps_the_one_focused_item():
    result = run_chart_js(queries=["bucket=2026-02&focus=food", "view=trend&focus=food", "bucket=2026-02"])["queries"]
    assert result[0]["query"] == "bucket=2026-02&focus=food"
    assert result[0]["focus"] == "food"
    assert result[1]["query"] == "view=trend&focus=food"
    assert result[2]["query"] == "bucket=2026-02" and result[2]["focus"] is None


def test_focus_is_kept_only_while_the_item_is_drawn(client):
    data = chart_data(client)
    food = "food" if any(item["key"] == "food" for item in data["categories"]) else data["categories"][0]["key"]
    result = run_chart_js(
        data,
        scenarios=[
            {"view": "share", "bucket": "2026-02", "focus": food},
            {"view": "share", "bucket": "2026-02", "focus": "nope"},
            {"view": "share", "bucket": "2026-02", "focus": "rest"},
            {"view": "trend", "scale": "month", "focus": food},
            {"view": "trend", "scale": "month", "focus": "nope"},
        ],
    )["scenarios"]
    assert [item["focusKept"] for item in result] == [True, False, True, True, False]
    assert result[3]["focusedSeries"] == [food]


def test_home_keeps_similar_cards_together(client):
    html = client.client.get("/?month=2026-02").text
    marks = (
        'id="widget-category"',
        "data-dash-multiples",
        'id="widget-payment"',
        'id="widget-cards"',
        "data-dash-difference",
        'id="book"',
        'id="widget-net_worth"',
        'id="widget-accounts"',
        'id="widget-physical"',
    )
    places = [html.index(mark) for mark in marks]
    assert places == sorted(places)
    assert re.findall(r'<div class="group-title">\s*<h2>(\S+)</h2>', html) == ["지출", "자산"]


def test_cards_move_only_inside_their_group(client):
    change_card(client, "net_worth", "up")  # 자산 묶음의 맨 위
    change_card(client, "cards", "down")  # 지출 묶음의 맨 아래
    assert card_order(client) == list(widgets.WIDGETS)
    change_card(client, "cards", "up")
    assert card_order(client)[:3] == ["category", "cards", "payment"]


def test_a_group_without_cards_keeps_its_charts_and_the_edit_button(client):
    for key in widgets.SPEND_WIDGETS:
        change_card(client, key, "hide")
    html = client.client.get("/?month=2026-02").text
    assert "data-dash-multiples" in html and "data-dash-difference" in html
    assert ">위젯 편집</a>" in html


def test_home_links_to_charts_say_more(client):
    html = client.client.get("/?month=2026-02").text
    assert set(re.findall(r'class="widget-link" href="[^"]*">([^<]+)<', html)) == {"더 보기"}
    assert len(re.findall(r'class="widget-link"', html)) >= 9


def test_home_uses_plain_words(client):
    html = client.client.get("/?month=2026-02").text
    for phrase in ("달마다", "어디에 썼나", "들어온 돈", "나간 돈", "가진 것", "갚을 것", "남은 돈", "안 쓴 날", "지출 없는 날"):
        assert phrase not in html, phrase


def test_brand_is_plain_text_not_a_glass_button(client):
    assert '<a class="brand" href="/">가계부</a>' in client.client.get("/").text
