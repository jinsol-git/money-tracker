"""차트 위젯: 어떤 위젯이 어디에 들어가는지, 테마와 배치, 위젯이 그리는 숫자가 맞는지 본다."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app import chart_widgets
from app.home import widgets as home_widgets
from app.ingest.importer import import_export
from app.ledger import labels, reports
from app.ledger.models import AssetSnapshot, ManualAsset, ManualAssetValue
from app.ops import state
from fixtures import make_export as fx
from test_pages import BANK, CARD, LEDGER, PASSWORD, chart_data, client, load, needs_node, run_chart_js  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
TABS = ("category", "flow", "assets", "finance")


def change(client, **fields):
    return client.post("/chart/widgets", form_page="/chart", **fields)


def layout_of(client, tab: str) -> dict:
    return chart_data(client)["widgets"]["layouts"][tab]


def keys_of(layout: dict) -> list[str]:
    return [item["key"] for item in layout["items"]]


def figures(client_or_data, **scenario) -> dict:
    data = client_or_data if isinstance(client_or_data, dict) else chart_data(client_or_data)
    return run_chart_js(data, figures=[scenario])["figures"][0]


# --- 위젯 목록과 테마


def test_every_widget_has_a_drawing_and_no_drawing_is_orphaned():
    script = (ROOT / "public" / "js" / "chart-widgets.js").read_text(encoding="utf-8")
    table = script[script.index("const WIDGET_DRAW = {") :]
    table = table[: table.index("};")]
    assert set(re.findall(r'^\s+"([\w.]+)":', table, re.M)) == set(chart_widgets.WIDGETS)


def test_every_tab_has_three_themes_of_its_own_widgets():
    assert set(chart_widgets.THEMES) == set(TABS)
    for tab, themes in chart_widgets.THEMES.items():
        assert set(themes) == set(chart_widgets.THEME_NAMES)
        for items in themes.values():
            for key, span, span_m in items:
                assert chart_widgets.WIDGETS[key].tab == tab
                assert 1 <= span <= chart_widgets.FULL_WIDTH and 1 <= span_m <= chart_widgets.FULL_WIDTH


def test_each_tab_can_also_add_the_shape_chart():
    themed = {key for themes in chart_widgets.THEMES.values() for items in themes.values() for key, _, _ in items}
    assert set(chart_widgets.WIDGETS) - themed == {f"{tab}.free" for tab in TABS}


def test_tabs_start_with_the_chosen_themes(client):
    layouts = chart_data(client)["widgets"]["layouts"]
    assert {tab: layout["theme"] for tab, layout in layouts.items()} == {"category": "C", "flow": "A", "assets": "A", "finance": "B"}
    assert keys_of(layouts["category"]) == ["category.matrix", "category.daily", "category.weekday"]
    assert keys_of(layouts["flow"]) == ["flow.pair", "flow.cumulative", "flow.sankey", "flow.payment"]
    assert keys_of(layouts["assets"]) == ["assets.balance", "assets.trend", "assets.waterfall", "assets.bucket"]
    assert keys_of(layouts["finance"]) == ["finance.list", "finance.detail", "finance.info"]


# --- 배치 바꾸기


def test_theme_replaces_the_whole_layout(client):
    change(client, tab="category", action="add", key="category.places")
    response = change(client, tab="category", action="theme", theme="A")
    assert response.status_code == 200
    layout = response.json()["layout"]
    assert layout["theme"] == "A"
    assert keys_of(layout) == [key for key, _, _ in chart_widgets.THEMES["category"]["A"]]
    assert layout["items"][0] == {"key": "category.treemap", "span": 5, "span_m": 5}
    assert layout_of(client, "category") == layout


def test_add_move_and_remove_one_widget(client):
    assert keys_of(change(client, tab="flow", action="add", key="flow.daily").json()["layout"])[-1] == "flow.daily"
    assert keys_of(change(client, tab="flow", action="add", key="flow.daily").json()["layout"]).count("flow.daily") == 1
    moved = keys_of(change(client, tab="flow", action="up", key="flow.daily").json()["layout"])
    assert moved[-2:] == ["flow.daily", "flow.payment"]
    first = keys_of(change(client, tab="flow", action="up", key="flow.pair").json()["layout"])
    assert first[0] == "flow.pair"  # 맨 위는 더 올라가지 않는다
    removed = change(client, tab="flow", action="remove", key="flow.daily").json()["layout"]
    assert "flow.daily" not in keys_of(removed)
    assert removed["theme"] == "A"  # 하나를 빼도 테마는 그대로다
    other_tab = layout_of(client, "assets")
    assert keys_of(other_tab)[0] == "assets.balance"


def test_picked_widgets_are_placed_in_the_clicked_order(client):
    picked = ["flow.daily", "flow.pair", "flow.net"]
    layout = change(client, tab="flow", action="set", theme="A", keys=picked).json()["layout"]
    assert keys_of(layout) == picked and layout["theme"] == "A"
    spans = {item["key"]: (item["span"], item["span_m"]) for item in layout["items"]}
    assert spans["flow.pair"] == (7, 12)  # A 테마의 칸 수
    assert spans["flow.daily"] == (chart_widgets.WIDGETS["flow.daily"].span, chart_widgets.WIDGETS["flow.daily"].span_m)
    assert layout_of(client, "flow") == layout
    assert keys_of(change(client, tab="flow", action="set", theme="C").json()["layout"]) == []  # 모두 빼도 된다


def test_widgets_page_knows_each_theme(client):
    themes = chart_data(client)["widgets"]["theme_items"]
    assert themes["category"]["A"] == [key for key, _, _ in chart_widgets.THEMES["category"]["A"]]
    assert set(themes) == set(TABS)


@pytest.mark.parametrize(
    "fields",
    [
        {"tab": "flow", "action": "set", "theme": "A", "keys": ["flow.pair", "category.daily"]},  # 다른 탭의 위젯
        {"tab": "flow", "action": "set", "theme": "A", "keys": ["flow.pair", "flow.pair"]},  # 같은 위젯 두 번
        {"tab": "flow", "action": "set", "theme": "Z", "keys": ["flow.pair"]},
        {"tab": "category", "action": "add", "key": "flow.pair"},  # 탭 화면에는 그 탭의 위젯만
        {"tab": "category", "action": "add", "key": "nope"},
        {"tab": "category", "action": "nope", "key": "category.daily"},
        {"tab": "nope", "action": "add", "key": "category.daily"},
        {"tab": "category", "action": "theme", "theme": "Z"},
    ],
)
def test_layout_refuses_what_does_not_belong(client, fields):
    assert change(client, **fields).status_code == 404


def test_layout_change_needs_a_csrf_token(client):
    response = client.client.post("/chart/widgets", data={"tab": "flow", "action": "theme", "theme": "B"})
    assert response.status_code == 403
    assert layout_of(client, "flow")["theme"] == "A"


def test_saved_layout_is_cleaned_before_use(client, session, clock):
    state.set_state(
        session,
        clock(),
        chart_widgets.LAYOUT_KEY,
        {
            "category": {
                "theme": "A",
                "items": [
                    {"key": "category.daily", "span": 99, "span_m": "x"},
                    {"key": "category.daily", "span": 6},
                    {"key": "flow.pair", "span": 6},
                    {"key": "nope"},
                    "broken",
                ],
            },
            "flow": {"theme": "Z", "items": []},
            "assets": "broken",
        },
    )
    layouts = chart_widgets.load_layouts(session)
    assert layouts["category"]["items"] == [{"key": "category.daily", "span": 6, "span_m": 12}]
    assert layouts["flow"]["theme"] == "A"
    assert layouts["assets"]["theme"] == "A"


@needs_node
def test_rows_are_filled_so_no_gap_remains():
    items = [{"span": 7}, {"span": 5}, {"span": 4}, {"span": 3}, {"span": 8}, {"span": 6}]
    result = run_chart_js(fills=[[items, "span"], [[{"span": 12}], "span"], [[], "span"]])["fills"]
    assert result[0] == [7, 5, 4, 8, 12, 12]
    assert result[1] == [12] and result[2] == []


# --- 홈


def test_home_keeps_its_cards_until_a_chart_widget_is_added(client):
    html = client.client.get("/?month=2026-02").text
    assert "data-home-chart" not in html


def test_home_offers_every_widget_grouped_by_tab(client):
    html = client.client.get("/?edit=1").text
    page = html[html.index("data-home-widget-page") :]
    assert re.findall(r'<h3 class="wp-section">([^<]+)</h3>', page) == ["홈", *chart_widgets.TABS.values()]
    for key in [*home_widgets.WIDGETS, *chart_widgets.WIDGETS]:
        assert f'data-wp-key="{key}"' in page


def test_chart_widget_goes_into_the_home_and_comes_out(client, session):
    page = "/?edit=1"
    assert client.post("/home/widgets", form_page=page, key="flow.pair", action="show", month="2026-02").status_code == 303
    assert client.post("/home/widgets", form_page=page, key="assets.trend", action="show", month="2026-02").status_code == 303
    layout = home_widgets.load_layout(session)
    assert "flow.pair" in layout.spend and "assets.trend" in layout.assets
    html = client.client.get("/?month=2026-02").text
    assert 'data-home-chart="flow.pair"' in html and 'data-home-chart="assets.trend"' in html
    assert 'href="/chart?tab=flow&amp;month=2026-02"' in html
    assert " style=" not in html
    client.post("/home/widgets", form_page="/?edit=1", key="flow.pair", action="hide", month="2026-02")
    assert 'data-home-chart="flow.pair"' not in client.client.get("/?month=2026-02").text


def test_home_refuses_a_widget_that_does_not_exist(client):
    assert client.post("/home/widgets", form_page="/?edit=1", key="flow.nope", action="show").status_code == 404


# --- 서버가 보내는 재료


def test_chart_page_has_no_inline_setup_and_passes_the_token(client):
    html = client.client.get("/chart?tab=finance").text
    assert re.search(r'data-csrf="[^"]+"', html)
    assert " style=" not in html


def test_finance_details_come_from_the_latest_export(client, session):
    details = reports.finance_details(session)
    assert details["investment"][0]["name"] == "OO전자"
    assert (details["investment"][0]["principal"], details["investment"][0]["valuation"]) == (100, 120)
    assert details["investment"][0]["history"] == [{"date": "2026-02-27", "principal": 100, "valuation": 120}]
    assert details["loan"][0] == {
        "name": "비밀대출상품",
        "org": "OO은행",
        "type": "장기대출",
        "principal": 200,
        "balance": 100,
        "rate": 4.5,
        "start": "2026-02-01",
        "end": "2031-02-01",
    }
    assert details["insurance"][0]["paid"] == 1000
    assert details["insurance"][0]["end"] == "2087-07-01"
    assert chart_data(client)["finance_details"]["loan"][0]["name"] == "비밀대출상품"


def test_finance_details_read_text_and_skip_what_they_cannot(session):
    assert reports._detail_day("2026.03.14") == "2026-03-14"
    assert reports._detail_day("2026-13-40") is None
    assert reports._detail_day(None) is None
    assert reports._detail_amount("1,234원") == 1234
    assert reports._detail_amount("없음") is None
    assert reports._detail_amount(True) is None
    assert reports._detail_number("3.85%") == 3.85


def test_finance_details_keep_history_across_exports(client, session):
    load(session, inner_name="2026-01-01~2026-03-05.xlsx")
    details = reports.finance_details(session)
    assert [point["date"] for point in details["investment"][0]["history"]] == ["2026-02-27", "2026-03-05"]


def test_asset_groups_know_their_bucket(client):
    data = chart_data(client)
    assert {group["name"]: group["bucket"] for group in data["asset_groups"] if group["side"] == "asset"} == {"자유입출금 자산": "cash"}
    assert [bucket["key"] for bucket in data["asset_buckets"]] == list(labels.ASSET_BUCKETS)


def test_every_physical_or_manual_group_has_a_bucket():
    for group in labels.PHYSICAL_ASSET_GROUPS:
        assert labels.BUCKET_OF_GROUP[group] == "real"
    for kind, tenure, other in (("car", None, None), ("real_estate", "own", None), ("real_estate", "jeonse", None), ("other", None, "physical")):
        assert labels.BUCKET_OF_GROUP[labels.manual_group_label(kind, tenure, other)] == "real"
    assert labels.BUCKET_OF_GROUP[labels.manual_group_label("other", None, "cash")] == "cash"
    assert labels.BUCKET_OF_GROUP[labels.manual_group_label("other", None, "pension")] == "lock"


def test_asset_products_show_exports_and_manual_assets(client, session):
    car = ManualAsset(
        kind="car",
        name="쏘나타",
        car_class="mid",
        car_origin="domestic",
        first_registered=date(2023, 3, 1),
        base_price=32_000_000,
        base_date=date(2023, 3, 1),
        acquired_on=date(2023, 3, 1),
        memo="비밀 메모",
    )
    car.values.append(ManualAssetValue(as_of=date(2026, 1, 1), amount=19_000_000, source="user"))
    session.add(car)
    session.flush()
    data = chart_data(client)
    products = data["asset_products"]
    assert {item["name"] for item in products["asset:자유입출금 자산"]} == {BANK, "다른통장"}
    entry = products["asset:자동차 (직접 입력)"][0]
    assert (entry["name"], entry["amount"], entry["href"]) == ("쏘나타", 19_000_000, f"/assets/manual/{car.id}")
    assert entry["facts"][0] == {"label": "차종", "value": "중형 국산", "note": "쏘나타"}
    assert entry["facts"][2]["value"] == "3,200만원"
    assert "비밀 메모" not in client.client.get("/chart/data").text


def test_finance_items_know_their_account(client, session):
    item = next(item for item in chart_data(client)["finance_items"] if item["name"] == BANK)
    line = next(line for line in reports.asset_items_on_latest(session) if line.product_name == BANK)
    assert item["instrument"] == line.instrument_id and item["instrument"]


# --- 자주 쓴 곳과 계좌 사용처


def places(client, **params) -> list[dict]:
    response = client.client.get("/chart/places", params=params)
    assert response.status_code == 200
    return response.json()["places"]


def test_places_are_the_biggest_spends_of_the_month(client, session):
    result = places(client, month="2026-02")
    assert result[0]["amount"] >= result[-1]["amount"]
    by_name = {place["name"]: place for place in result}
    assert by_name["밥집"] == {"name": "밥집", "amount": 25_000, "rows": 2}  # 환불을 뺀 금액이다
    assert sum(place["amount"] for place in result) <= reports.monthly_summary(session)[-1].spend_total
    assert "회사" not in by_name  # 수입은 넣지 않는다
    assert "어떤사람" not in by_name  # 미확정도 넣지 않는다


def test_places_follow_the_same_conditions(client):
    assert [place["name"] for place in places(client, month="2026-02", category="food")] == ["밥집"]
    assert places(client, month="2026-02", channel="pay")[0]["name"] == "쇼핑몰"
    assert places(client, month="2026-02", channel="nope") == places(client, month="2026-02")
    assert places(client, month="2026-01")[0]["name"] == "지난달밥집"
    assert places(client, month="2025-01") == []


def test_places_follow_the_chosen_days(client):
    assert places(client, start="2026-02-01", end="2026-02-28") == places(client, month="2026-02")
    assert places(client, start="2026-02-28", end="2026-02-01") == places(client, month="2026-02")  # 앞뒤가 바뀌어도 된다
    assert [place["name"] for place in places(client, start="2026-02-05", end="2026-02-05")] == ["쇼핑몰"]
    assert places(client, start="2026-02-02", end="2026-02-02")[0] == {"name": "밥집", "amount": 30_000, "rows": 1}  # 환불은 다음 날이다


def test_places_need_a_month(client):
    assert client.client.get("/chart/places").status_code == 404
    assert client.client.get("/chart/places", params={"month": "nope"}).status_code == 404
    assert client.client.get("/chart/places", params={"start": "2026-02-01"}).status_code == 404  # 끝날도 있어야 한다
    assert client.client.get("/chart/places", params={"start": "nope", "end": "2026-02-01"}).status_code == 404


def test_widget_addresses_need_a_login(browser):
    assert browser.client.get("/chart/places", params={"month": "2026-02"}).status_code == 401
    assert browser.client.get("/chart/account/1").status_code == 401
    assert browser.client.post("/chart/widgets", data={"tab": "flow", "action": "theme", "theme": "B"}).status_code == 401


def test_places_can_ask_for_lines_without_a_category(client):
    assert places(client, month="2026-02", category="none") == []  # 이 장부의 지출은 모두 카테고리가 있다
    both = places(client, month="2026-02", category=["none", "food"])
    assert [place["name"] for place in both] == ["밥집"]


def test_account_use_is_the_same_as_the_account_screen(client, session):
    line = next(line for line in reports.asset_items_on_latest(session) if line.product_name == BANK)
    response = client.client.get(f"/chart/account/{line.instrument_id}", params={"month": "2026-02"})
    assert response.status_code == 200
    rows = {row["name"]: row["amount"] for row in response.json()["rows"]}
    trace = reports.account_trace(session, line.instrument_id, "2026-02", "2026-02")
    assert rows["내 계좌 이동"] == trace.own_transfer
    assert sum(rows.values()) == trace.total_out
    assert client.client.get("/chart/account/999999").status_code == 404


# --- 위젯의 숫자


@needs_node
def test_figures_add_up_to_the_monthly_summary(client, session):
    result = figures(client, tab="flow")["flow"]
    summary = {item.month: item for item in reports.monthly_summary(session)}
    assert result["axis"]["selected"] == "2026-02"
    assert result["spend"]["2026-02"] == summary["2026-02"].spend_total
    assert result["income"]["2026-02"] == summary["2026-02"].income
    assert sum(result["daily"]) == summary["2026-02"].spend_total
    assert result["running"][-1] == sum(item.income - item.spend_total for item in summary.values())


@needs_node
def test_month_window_follows_the_chosen_month_and_span(client):
    data = chart_data(client)
    assert figures(data, tab="category", month="2026-01")["flow"]["axis"]["selected"] == "2026-01"
    assert figures(data, tab="category", month="1999-01")["flow"]["axis"]["selected"] == "2026-02"
    axis = figures(data, tab="category", month="2026-01", range="1m")["flow"]["axis"]
    assert axis["months"] == ["2026-01"]  # 기간 밖의 달을 고르면 그 달에서 끝나는 기간으로 옮겨 간다
    assert (axis["previous"], axis["next"]) == (None, "2026-02")


@needs_node
def test_bucket_address_still_picks_the_month(client):
    assert figures(client, tab="flow", bucket="2026-01")["flow"]["axis"]["selected"] == "2026-01"


@needs_node
def test_address_keeps_the_widget_choices():
    query = "tab=flow&month=2026-02&span=6&record=2026-02-27&edit=1"
    result = run_chart_js(queries=[query, "month=nope&span=7&record=nope"])["queries"]
    assert result[0]["query"] == query
    assert (result[0]["month"], result[0]["span"], result[0]["record"], result[0]["editing"]) == ("2026-02", 6, "2026-02-27", True)
    assert (result[1]["month"], result[1]["span"], result[1]["record"], result[1]["editing"]) == (None, 12, None, False)
    assert result[1]["query"] == ""


@needs_node
def test_units_split_the_same_money_into_days_weeks_and_years(client):
    data = chart_data(client)
    month = figures(data, tab="flow")["flow"]
    week = figures(data, tab="flow", unit="week")["flow"]
    year = figures(data, tab="flow", unit="year")["flow"]
    day = figures(data, tab="flow", unit="day")["flow"]
    assert [item["axis"]["unit"] for item in (month, week, year, day)] == ["month", "week", "year", "day"]
    assert month["axis"]["months"] == month["axis"]["all"] and week["axis"]["months"] == week["axis"]["all"]
    for unit in (week, year):
        assert sum(unit["spend"].values()) == sum(month["spend"].values())
        assert sum(unit["income"].values()) == sum(month["income"].values())
    assert year["axis"]["months"] == sorted({key[:4] for key in month["axis"]["all"]})
    assert all(len(key) == 10 for key in week["axis"]["months"] + day["axis"]["months"])
    assert len(day["axis"]["months"]) <= 31 and day["axis"]["selected"] == data["flow_range"]["last"]


@needs_node
def test_changing_the_unit_keeps_the_chosen_time(client):
    data = chart_data(client)
    assert figures(data, tab="flow", unit="week", month="2026-01")["flow"]["axis"]["selected"] == "2026-01-26"  # 1월 31일이 든 주
    assert figures(data, tab="flow", unit="year", month="2026-01")["flow"]["axis"]["selected"] == "2026"
    assert figures(data, tab="flow", unit="month", month="2026-01-26")["flow"]["axis"]["selected"] == "2026-01"


@needs_node
def test_widgets_inside_a_period_follow_the_unit(client):
    data = chart_data(client)
    week = figures(data, tab="category", unit="week", month="2026-01-26")["flow"]
    assert week["month"] == "2026-01" and len(week["curve"]) == 7 and len(week["bucketDaily"]) == 7  # 그 주의 7일이다
    assert len(figures(data, tab="category", month="2026-02")["flow"]["curve"]) == 28
    day = figures(data, tab="category", unit="day", month="2026-02-05")["flow"]
    assert len(day["curve"]) == 28 and day["month"] == "2026-02"  # 하루 안에는 시간이 없어 그날이 든 달을 본다
    year = figures(data, tab="category", unit="year")["flow"]
    assert year["month"] == data["flow_range"]["last"][:7] and len(year["curve"]) == 365 and len(year["bucketDaily"]) == 365


@needs_node
def test_assets_keep_the_last_record_of_each_unit(client):
    data = chart_data(client)
    every = figures(data, tab="assets")["assets"]["days"]
    monthly = figures(data, tab="assets", unit="month")["assets"]["days"]
    assert monthly == [max(day for day in every if day[:7] == month) for month in sorted({day[:7] for day in every})]
    records = figures(data, tab="finance")["finance"]["days"]
    yearly = figures(data, tab="finance", unit="year")["finance"]["days"]
    assert yearly == [max(day for day in records if day[:4] == year) for year in sorted({day[:4] for day in records})]


@needs_node
def test_finance_groups_can_be_chosen_as_a_whole(client):
    data = chart_data(client)
    finance = figures(data, tab="finance")["finance"]
    group = finance["groups"][0]
    assert group["kind"] == "group" and len(group["values"]) == len(finance["days"])
    assert group["value"] == sum(item["value"] for item in group["items"])
    assert figures(data, tab="finance", focus=group["key"])["finance"]["found"] == "group"
    assert finance["debtGroup"]["value"] == finance["debt"]
    assert figures(data, tab="finance", focus="group:debts")["finance"]["found"] == "group"


@needs_node
def test_ranges_set_how_far_back_the_widgets_look(client):
    data = chart_data(client)
    last = data["flow_range"]["last"]
    assert figures(data, tab="flow", range="1m")["flow"]["axis"]["months"] == [last[:7]]  # 1년이 기본인 월간에 1개월을 고르면 한 달이다
    days = figures(data, tab="flow", unit="day", range="1m")["flow"]["axis"]["months"]
    assert days[-1] == last and 28 <= len(days) <= 31
    year = figures(data, tab="flow", unit="month", range="ytd")["flow"]["axis"]["months"]
    assert year[0] == f"{last[:4]}-01" and year[-1] == last[:7]
    assert figures(data, tab="flow", unit="year")["flow"]["axis"]["range"] == "max"  # 기간을 고르지 않으면 단위에 맞는 기간이다
    assert figures(data, tab="flow", span=6)["flow"]["axis"]["range"] == "6m"  # 예전 주소의 6개월


@needs_node
def test_holdings_keep_the_records_inside_the_range(client):
    data = chart_data(client)
    every = figures(data, tab="assets")["assets"]["days"]
    recent = figures(data, tab="assets", range="1m")["assets"]["days"]
    assert recent and set(recent) <= set(every) and recent[-1] == every[-1]
    assert all(day > f"{int(every[-1][:4]) - 1}{every[-1][4:]}" for day in recent)


@needs_node
def test_summary_names_the_chosen_bucket():
    names = [["month", "2026-10", "2026"], ["month", "2025-12", "2026"], ["week", "2026-11-02", "2026"], ["day", "2026-11-08", "2026"], ["year", "2026", "2026"]]
    assert run_chart_js(names=names)["names"] == ["10월", "2025년 12월", "11월 2일 주", "11월 8일", "2026년"]


@needs_node
def test_address_keeps_the_unit():
    result = run_chart_js(queries=["month=2026-01-26&unit=week&range=5y", "unit=hour&month=2026-02-30&range=2d", "month=2026"])["queries"]
    assert (result[0]["unit"], result[0]["month"]) == ("week", "2026-01-26")
    assert result[0]["query"] == "month=2026-01-26&unit=week&range=5y"
    assert (result[1]["unit"], result[1]["month"]) == (None, None)
    assert result[2]["month"] == "2026"


@needs_node
def test_matrix_colors_only_what_differs_from_its_own_average():
    result = run_chart_js(heats=[[100, 100], [103, 100], [150, 100], [50, 100], [200, 100], [0, 100], [5, 0]])["heats"]
    assert result[0]["percent"] == 0 and result[1]["percent"] == 0  # 고정비는 무채색이다
    assert result[2]["above"] is True and 10 <= result[2]["percent"] <= 62
    assert result[3]["above"] is False and result[3]["percent"] > 10
    assert result[4]["percent"] == 62  # 아무리 튀어도 글자가 읽히는 진하기까지만
    assert result[5]["percent"] == 0 and result[6]["percent"] == 0


@needs_node
def test_matrix_writes_cells_in_ten_thousands():
    assert run_chart_js(cells=[0, 320_000, 9_000, 2_586, -40_000, 120_000_000])["cells"] == ["", "32", "0.9", "0.3", "-4", "1.2억"]


@needs_node
def test_matrix_rows_are_sorted_and_skip_empty_categories(client):
    rows = figures(client, tab="category")["flow"]["matrix"]
    totals = [row["total"] for row in rows]
    assert totals == sorted(totals, reverse=True)
    assert all(any(cell["value"] for cell in row["cells"]) for row in rows)
    food = next(row for row in rows if row["key"] == "food")
    assert [cell["value"] for cell in food["cells"]] == [11_000, 25_000]


@needs_node
def test_focus_narrows_daily_and_weekday_but_not_the_rank(client):
    data = chart_data(client)
    everything = figures(data, tab="category")["flow"]
    food = figures(data, tab="category", focus="food")["flow"]
    assert sum(food["daily"]) == 25_000
    assert sum(everything["daily"]) == 95_000
    assert food["rank"] == everything["rank"]


@needs_node
def test_filters_narrow_every_flow_figure(client):
    result = figures(client, tab="category", channels=["pay"])["flow"]
    assert result["spend"]["2026-02"] == 20_000
    assert [row["key"] for row in result["rank"]] == ["shopping"]
    assert [item["name"] for item in result["payment"]["instruments"]] == ["네이버페이 간편결제"]


@needs_node
def test_cumulative_compares_with_the_average_of_the_range(signed_in, session):
    browser, _, _ = signed_in
    months = [(2025, month) for month in range(1, 13)] + [(2026, month) for month in range(1, 7)]
    rows = [fx.tx(f"{year}-{month:02d}-10 10:00:00", "지출", "식비", "한식", f"밥집{year}{month}", -1_000, CARD) for year, month in months]
    load(session, rows=rows, inner_name="2025-01-01~2026-06-30.xlsx")
    data = chart_data(browser)
    flow = figures(data, tab="flow")["flow"]
    assert flow["cumulativeAverageOf"] == 11  # 1년 안의 지난 달들
    average = flow["cumulativeAverage"]
    assert (average[8], average[9], average[-1]) == (0, 1_000, 1_000)  # 짧은 달은 마지막 합계를 잇는다
    assert figures(data, tab="flow", range="1m")["flow"]["cumulativeAverage"] is None  # 견줄 지난 달이 없다


@needs_node
def test_cumulative_stops_at_the_last_exported_day(client):
    curve = figures(client, tab="flow")["flow"]["curve"]
    assert len(curve) == 28  # 2026년 2월
    last = max(index for index, value in enumerate(curve) if value is not None)
    assert last == 26  # 2월 27일까지 내보냈다
    assert curve[last] == 95_000 and curve[27] is None


@needs_node
def test_weekday_average_counts_only_days_with_records(client):
    data = chart_data(client)
    result = figures(data, tab="flow", month="2026-02")["flow"]["weekday"]
    assert len(result) == 7 and result[1] > 0  # 2월 2일은 월요일이다


@needs_node
def test_changes_compare_with_the_month_before(client):
    data = chart_data(client)
    changes = figures(data, tab="category")["flow"]["changes"]
    food = next(item for item in changes if item["key"] == "food")
    assert (food["value"], food["change"]) == (25_000, 14_000)
    assert changes == sorted(changes, key=lambda item: -item["change"])
    assert figures(data, tab="category", month="2026-01")["flow"]["changes"] == []


@needs_node
def test_focused_rest_category_gets_out_of_the_rest_group():
    categories = [{"key": f"c{index}", "name": str(index), "kind": "spend", "total": 100 - index} for index in range(9)]
    data = {
        "today": "2026-02-28",
        "flow_range": {"first": "2026-02-01", "last": "2026-02-28"},
        "channels": [{"key": "card", "name": "카드"}],
        "categories": categories,
        "instruments": [{"id": 1, "name": "카드"}],
        "lines": [["2026-02-02", "spend", "card", 1, category["key"], 1000] for category in categories],
        "snapshots": [],
        "finance_snapshots": [],
        "asset_groups": [],
        "asset_buckets": [],
        "finance_items": [],
        "finance_groups": [],
        "finance_investments": {},
        "finance_details": {"investment": [], "loan": [], "insurance": []},
    }
    plain = figures(data, tab="category")["flow"]["series"]
    assert plain[-1] == {"key": "rest", "slot": "rest"}
    picked = figures(data, tab="category", focus="c8")["flow"]["series"]
    assert {"key": "c8", "slot": "leftover"} in picked


def load_records(session) -> None:
    """2월 27일과 3월 5일에 내보낸 자산 기록. 입출금이 늘고 대출이 줄었다."""
    load(session)
    rows = [row for row in LEDGER]
    sheet = fx.build_xlsx(
        rows=rows,
        assets=[fx.AssetRow("자유입출금 자산", BANK, 1_400_000), fx.AssetRow("연금 자산", "퇴직연금", 300_000), fx.AssetRow("부동산", "우리집", 2_000_000)],
        liabilities=[fx.AssetRow("장기대출", "대출상품", 150_000)],
    )
    result = import_export(session, fx.zip_aes(sheet, PASSWORD, "2026-01-01~2026-03-05.xlsx"), [PASSWORD])
    assert result.status == "applied", result


@needs_node
def test_waterfall_explains_the_change_in_net_worth(signed_in, session):
    browser, _, _ = signed_in
    load_records(session)
    assets = figures(browser, tab="assets")["assets"]
    assert assets["days"] == ["2026-02-27", "2026-03-05"]
    assert (assets["rec"], assets["base"]) == (1, 0)
    change_in_net = assets["net"][1] - assets["net"][0]
    assert sum(step["value"] for step in assets["steps"]) == change_in_net
    steps = {step["short"]: step["value"] for step in assets["steps"]}
    assert steps["부채"] == 50_000  # 부채가 줄면 순자산에는 늘어난 것이다
    assert [abs(step["value"]) for step in assets["steps"]] == sorted((abs(step["value"]) for step in assets["steps"]), reverse=True)


@needs_node
def test_changes_count_from_the_first_record_of_the_range(signed_in, session):
    browser, _, _ = signed_in
    load_records(session)
    data = chart_data(browser)
    later = {**data["snapshots"][-1], "date": "2026-03-20"}
    later["groups"] = {**later["groups"], next(iter(later["groups"])): next(iter(later["groups"].values())) + 70_000}
    data["snapshots"] = [*data["snapshots"], later]
    assets = figures(data, tab="assets")["assets"]
    assert assets["days"] == ["2026-02-27", "2026-03-05", "2026-03-20"]
    assert (assets["rec"], assets["base"]) == (2, 0)  # 직전 기록이 아니라 기간의 첫 기록과 견준다
    assert sum(step["value"] for step in assets["steps"]) == assets["net"][2] - assets["net"][0]
    assert figures(data, tab="assets", range="1m")["assets"]["days"][0] == "2026-02-27"


@needs_node
def test_buckets_add_up_to_assets(signed_in, session):
    browser, _, _ = signed_in
    load_records(session)
    assets = figures(browser, tab="assets")["assets"]
    buckets = {bucket["key"]: bucket["value"] for bucket in assets["buckets"]}
    assert sum(buckets.values()) == assets["assets"][1]
    assert (buckets["cash"], buckets["lock"], buckets["real"]) == (1_400_000, 300_000, 2_000_000)


@needs_node
def test_chosen_record_moves_the_figures(signed_in, session):
    browser, _, _ = signed_in
    load_records(session)
    data = chart_data(browser)
    earlier = figures(data, tab="assets", record="2026-02-27")["assets"]
    assert (earlier["rec"], earlier["base"], earlier["steps"]) == (0, None, [])
    assert figures(data, tab="assets", record="1999-01-01")["assets"]["rec"] == 1


@needs_node
def test_filters_narrow_the_assets(signed_in, session):
    browser, _, _ = signed_in
    load_records(session)
    assets = figures(browser, tab="assets", groups=["asset:연금 자산"])["assets"]
    assert [group["key"] for group in assets["groups"]] == ["asset:연금 자산"]
    assert assets["debts"] == [] and assets["net"][1] == 300_000


@needs_node
def test_average_spend_leaves_out_partial_months(client):
    # 1월은 10일부터, 2월은 27일까지라 온전한 달이 없다. 그때는 있는 달을 모두 쓴다.
    assert figures(client, tab="assets")["averageSpend"] == round((11_000 + 95_000) / 2)


@needs_node
def test_finance_list_links_products_to_their_details(client, session):
    assets = [fx.AssetRow("자유입출금 자산", BANK, 1_000_000), fx.AssetRow("투자성 자산", "OO 전자", 120)]
    sheet = fx.build_xlsx(rows=LEDGER, assets=assets, liabilities=[fx.AssetRow("장기대출", "다른이름", 100)])
    result = import_export(session, fx.zip_aes(sheet, PASSWORD, "2026-01-01~2026-03-05.xlsx"), [PASSWORD])
    assert result.status == "applied", result
    finance = figures(client, tab="finance")["finance"]
    products = {item["name"]: item for group in finance["groups"] for item in group["items"]}
    assert products["OO 전자"]["kind"] == "invest"  # 띄어쓰기가 달라도 같은 상품이다
    assert products["OO 전자"]["investment"]["principal"] == 100
    assert products[BANK]["kind"] == "account"
    assert finance["debts"][0]["loan"]["name"] == "비밀대출상품"  # 이름이 달라도 잔액이 같으면 같은 대출이다
    assert finance["profit"] == 20
    assert finance["net"] == finance["assets"] - finance["debt"]
    assert finance["netValues"][finance["rec"]] == finance["net"]


@needs_node
def test_finance_focus_finds_every_kind(client):
    data = chart_data(client)
    bank = next(item for item in data["finance_items"] if item["name"] == BANK)
    debt = next(item for item in data["finance_items"] if item["side"] == "liability")
    assert figures(data, tab="finance", focus=bank["key"])["finance"]["found"] == "account"
    assert figures(data, tab="finance", focus=debt["key"])["finance"]["found"] == "loan"
    assert figures(data, tab="finance", focus="insurance:OO보험")["finance"]["found"] == "insurance"
    assert figures(data, tab="finance", focus="nope")["finance"]["found"] is None


@needs_node
def test_maturity_and_timeline_use_only_rows_with_dates(client):
    data = chart_data(client)
    data["today"] = "2026-11-07"
    finance = figures(data, tab="finance")["finance"]
    assert [item["name"] for item in finance["maturity"]["items"]] == ["비밀대출상품"]
    assert finance["maturity"]["to"] == "2031-12-31"
    names = [row.get("group") or row["name"] for row in finance["timeline"]]
    assert names == ["대출", "비밀대출상품", "보험", "OO보험"]  # 가입일이 없는 투자 상품은 그릴 수 없다


@needs_node
def test_details_link_by_name_then_part_of_name_then_amount():
    items = [{"key": "a", "name": "미국 ETF", "latest": 10}, {"key": "b", "name": "청년 적금 2호", "latest": 20}, {"key": "c", "name": "다른 것", "latest": 30}]
    rows = [{"name": "청년 적금", "amount": 1}, {"name": "미국ETF", "amount": 2}, {"name": "전혀 다름", "amount": 30}, {"name": "남는 것", "amount": 99}]
    links = dict(run_chart_js(links=[{"items": items, "rows": rows}])["links"][0])
    assert links == {"a": "미국ETF", "b": "청년 적금", "c": "전혀 다름"}


def test_snapshots_are_only_read(client, session):
    before = len(list(session.scalars(select(AssetSnapshot))))
    chart_data(client)
    client.client.get("/chart/places", params={"month": "2026-02"})
    assert len(list(session.scalars(select(AssetSnapshot)))) == before
