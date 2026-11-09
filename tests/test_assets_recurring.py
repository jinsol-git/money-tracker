"""직접 입력한 자산과 감가상각, 반복 거래 판별과 규칙."""

from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

from app.ingest.importer import import_export
from app.ledger import depreciation, reports
from app.ledger.classify import RowView, RuleView
from app.ledger.models import Category, ManualAsset, ManualAssetValue, Rule, Txn, TxnSplit
from app.ledger.payment_links import LINKS_KEY, links_of
from app.ops import state
from app.ledger.recurring import Line, detect, target_key
from app.assets.routes import parse_amount, parse_day
from app.ledger.reclassify import own_names_of, parse_own_names
from fixtures import make_export as fx

PASSWORD = "0000"
BANK = "입출금통장"
CARD = "어떤 삼성카드"
ASSETS = [fx.AssetRow("자유입출금 자산", BANK, 1_000_000)]
TODAY = date(2026, 11, 5)  # web_support.FrozenClock의 한국 날짜


def load(session, rows, inner_name="2026-01-01~2026-10-31.xlsx", assets=ASSETS):
    data = fx.zip_aes(fx.build_xlsx(rows=rows, assets=assets, liabilities=[]), PASSWORD, inner_name)
    result = import_export(session, data, [PASSWORD])
    assert result.status == "applied", result
    return result


@pytest.fixture
def client(signed_in):
    return signed_in[0]


def add_car(client, **fields):
    defaults = {
        "kind": "car",
        "name": "쏘나타",
        "car_class": "mid",
        "car_origin": "domestic",
        "first_registered": "2023-11",
        "base_price": "30000000",
        "amount": "",
    }
    return client.post("/assets/manual", form_page="/assets/manual/new", **{**defaults, **fields})


def add_home(client, **fields):
    defaults = {"kind": "real_estate", "name": "우리 집", "tenure": "own", "amount": "500000000"}
    return client.post("/assets/manual", form_page="/assets/manual/new", **{**defaults, **fields})


def add_other(client, **fields):
    defaults = {"kind": "other", "other_type": "coin", "name": "비트코인", "amount": "3000000", "memo": "거래소 지갑"}
    return client.post("/assets/manual", form_page="/assets/manual/new?kind=other", **{**defaults, **fields})


def add_lease(client, **fields):
    defaults = {"tenure": "wolse", "name": "오피스텔", "amount": "50000000", "monthly_rent": "550000"}
    return add_home(client, **{**defaults, **fields})


def only_asset(session) -> ManualAsset:
    return session.scalars(select(ManualAsset)).one()


# --- 감가상각 공식


def test_depreciation_follows_the_formula_year_by_year():
    start = date(2020, 1, 1)
    # 국산 중형 r=0.125. 1년 차 1.65배, 2~3년 차 1배, 4~5년 차 1.2배, 6년 차부터 0.8배
    expected = 30_000_000.0
    for year, factor in enumerate((1.65, 1.0, 1.0, 1.2, 1.2, 0.8, 0.8), start=1):
        expected *= 1 - 0.125 * factor
        value = depreciation.depreciated_value(30_000_000, "mid", "domestic", start, start, start.replace(year=2020 + year))
        assert value == pytest.approx(expected, abs=500), year


def test_first_year_drop_matches_the_table():
    start = date(2020, 1, 1)
    assert depreciation.depreciated_value(30_000_000, "mid", "domestic", start, start, date(2021, 1, 1)) == 23_812_000


@pytest.mark.parametrize(
    ("car_class", "origin", "low", "high"),
    [
        ("small", "domestic", 0.10, 0.11),
        ("small", "imported", 0.14, 0.15),
        ("compact", "domestic", 0.11, 0.12),
        ("compact", "imported", 0.15, 0.16),
        ("mid", "domestic", 0.12, 0.13),
        ("mid", "imported", 0.16, 0.17),
        ("large", "domestic", 0.14, 0.15),
        ("large", "imported", 0.18, 0.20),
    ],
)
def test_rates_stay_inside_the_requested_ranges(car_class, origin, low, high):
    assert low <= depreciation.BASE_RATES[(car_class, origin)] <= high


def test_imported_and_larger_cars_lose_value_faster():
    start = date(2020, 1, 1)
    target = date(2025, 1, 1)
    value = lambda c, o: depreciation.depreciated_value(30_000_000, c, o, start, start, target)  # noqa: E731
    assert value("mid", "imported") < value("mid", "domestic")
    assert value("large", "domestic") < value("mid", "domestic") < value("small", "domestic")


def test_part_of_a_year_counts_only_that_part():
    start = date(2020, 1, 1)
    half = depreciation.depreciated_value(30_000_000, "mid", "domestic", start, start, date(2020, 7, 1))
    full = depreciation.depreciated_value(30_000_000, "mid", "domestic", start, start, date(2021, 1, 1))
    assert full < half < 30_000_000
    assert 30_000_000 - half == pytest.approx((30_000_000 - full) * 182 / 366, abs=2000)


def test_used_car_skips_the_years_before_it_was_bought():
    registered = date(2018, 1, 1)
    bought = date(2023, 1, 1)
    value = depreciation.depreciated_value(15_000_000, "mid", "domestic", registered, bought, date(2024, 1, 1))
    # 산 뒤의 첫해는 차의 6년 차라 0.8배다
    assert value == pytest.approx(15_000_000 * (1 - 0.125 * 0.8), abs=500)


def test_value_does_not_change_before_the_base_date():
    assert depreciation.depreciated_value(1_000_000, "mid", "domestic", date(2020, 1, 1), date(2022, 1, 1), date(2021, 1, 1)) == 1_000_000


def test_leap_day_registration_is_handled():
    assert depreciation.depreciated_value(10_000_000, "mid", "domestic", date(2020, 2, 29), date(2020, 2, 29), date(2023, 3, 1)) > 0
    assert depreciation.age_in_years(date(2020, 2, 29), date(2021, 3, 1)) == 2


def test_age_counts_the_first_year_as_one():
    assert depreciation.age_in_years(date(2025, 1, 1), date(2025, 6, 1)) == 1
    assert depreciation.age_in_years(date(2025, 1, 1), date(2026, 1, 1)) == 2


# --- 입력값


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1000", 1000),
        ("30,000,000", 30_000_000),
        (" 1 000 원", 1000),
        ("0", 0),
        ("", None),
        ("abc", None),
        ("-100", None),
        ("١٢٣", None),
        ("1.5", None),
        ("99999999999999999999", None),
    ],
)
def test_amount_parsing(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-03", date(2026, 3, 1)),
        ("2026-03-15", date(2026, 3, 15)),
        ("2026-13", None),
        ("nope", None),
        ("", None),
        ("2099-01-01", None),
    ],
)
def test_day_parsing(text, expected):
    assert parse_day(text, TODAY) == expected


# --- 자산 추가


def calculated_car_value() -> int:
    return depreciation.depreciated_value(30_000_000, "mid", "domestic", date(2023, 11, 1), date(2023, 11, 1), TODAY)


def test_checking_depreciation_asks_for_confirmation_first(client, session):
    response = add_car(client, depreciate="1")
    assert response.status_code == 200
    assert "감가상각 확인" in response.text
    assert f"{calculated_car_value():,}원" in response.text
    assert "이 금액으로 추가" in response.text
    assert 'name="confirmed" value="1"' in response.text
    assert session.scalars(select(ManualAsset)).all() == []


def test_confirming_saves_the_calculated_value(client, session):
    response = add_car(client, depreciate="1", confirmed="1")
    assert response.status_code == 303
    car = only_asset(session)
    assert response.headers["location"] == f"/assets/manual/{car.id}?msg=created"
    assert (car.car_class, car.car_origin, car.base_price) == ("mid", "domestic", 30_000_000)
    value = car.values[0]
    assert (value.amount, value.source, value.as_of) == (calculated_car_value(), "depreciation", TODAY)


def test_confirmation_recalculates_on_the_server(client, session):
    add_car(client, depreciate="1", confirmed="1", amount="1")
    assert only_asset(session).values[0].amount == calculated_car_value()


def test_confirmation_page_carries_every_field_to_the_next_step(client, session):
    page = add_car(client, depreciate="1", base_date="2024-03", base_price="28,000,000").text
    for name, value in (
        ("name", "쏘나타"),
        ("car_class", "mid"),
        ("car_origin", "domestic"),
        ("first_registered", "2023-11"),
        ("base_price", "28000000"),
        ("base_date", "2024-03"),
    ):
        assert f'name="{name}" value="{value}"' in page, name


def test_confirmation_page_can_switch_to_a_typed_amount(client, session):
    page = add_car(client, depreciate="1").text
    assert 'name="amount"' in page
    add_car(client, amount="21000000")
    value = only_asset(session).values[0]
    assert (value.amount, value.source) == (21_000_000, "user")


def test_car_can_start_with_a_typed_value(client, session):
    add_car(client, amount="20,000,000")
    value = only_asset(session).values[0]
    assert (value.amount, value.source) == (20_000_000, "user")


def test_car_starts_at_its_price_without_the_depreciation_option(client, session):
    add_car(client)
    value = only_asset(session).values[0]
    assert (value.amount, value.source) == (30_000_000, "user")


def test_car_form_only_asks_for_the_depreciation_choice(client):
    html = client.client.get("/assets/manual/new?kind=car").text
    assert "신차면 비워 두세요" not in html
    assert "지금 금액" not in html
    assert 'name="amount"' not in html
    assert '<input type="checkbox" name="depreciate" value="1"> 감가상각 반영하기</label>' in html


def test_add_form_offers_the_depreciation_checkbox_for_cars_only(client):
    assert 'name="depreciate"' in client.client.get("/assets/manual/new?kind=car").text
    assert 'name="depreciate"' not in client.client.get("/assets/manual/new?kind=real_estate").text


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"name": "  "}, "name"),
        ({"car_class": "nope"}, "car"),
        ({"car_origin": "nope"}, "car"),
        ({"first_registered": "abc"}, "car"),
        ({"first_registered": "2099-01"}, "car"),
        ({"base_price": "abc"}, "amount"),
        ({"base_date": "2020-01"}, "date"),  # 등록 전
        ({"amount": "abc"}, "amount"),
    ],
)
def test_bad_car_fields_are_rejected(client, session, fields, error):
    assert f"error={error}" in add_car(client, **fields).headers["location"]
    assert session.scalars(select(ManualAsset)).all() == []


@pytest.mark.parametrize("tenure", ["own", "jeonse", "wolse"])
def test_real_estate_tenures(client, session, tenure):
    add_home(client, tenure=tenure)
    assert only_asset(session).tenure == tenure


def test_real_estate_needs_a_tenure_and_amount(client, session):
    assert "error=tenure" in add_home(client, tenure="nope").headers["location"]
    assert "error=amount" in add_home(client, amount="").headers["location"]
    assert "error=date" in add_home(client, acquired_on="2099-01-01").headers["location"]
    assert session.scalars(select(ManualAsset)).all() == []


def test_new_form_shows_fields_for_each_kind(client):
    assert 'name="car_class"' in client.client.get("/assets/manual/new?kind=car").text
    assert 'name="tenure"' in client.client.get("/assets/manual/new?kind=real_estate").text
    other = client.client.get("/assets/manual/new?kind=other").text
    assert 'name="tenure"' not in other and 'name="car_class"' not in other
    assert 'name="car_class"' in client.client.get("/assets/manual/new?kind=nope").text


def test_real_estate_form_asks_for_a_type_under_the_name(client):
    html = client.client.get("/assets/manual/new?kind=real_estate").text
    assert html.index('name="name"') < html.index("<legend>타입</legend>") < html.index('name="amount"')
    choices = re.findall(r'name="tenure" value="(\w+)"( checked)?><span>([^<]+)</span>', html)
    assert choices == [("own", " checked", "매매"), ("jeonse", "", "전세"), ("wolse", "", "전월세")]
    assert '<label for="amount"><span class="for-own">구매가</span><span class="for-lease">보증금</span></label>' in html
    assert '<label for="market_value" class="for-own">현재 시세 (선택)</label>' in html
    assert 'name="market_value" inputmode="numeric" placeholder' in html and "required" not in html.split('name="market_value"')[1].split(">")[0]
    assert '<label for="monthly_rent" class="for-wolse">월세</label>' in html
    assert "전월세는 보증금" not in html


def test_asset_form_has_three_tabs(client):
    html = client.client.get("/assets/manual/new").text
    assert re.findall(r'href="/assets/manual/new\?kind=(\w+)"', html) == ["car", "real_estate", "other"]


def values_of(asset: ManualAsset) -> list[tuple[date, int]]:
    return [(value.as_of, value.amount) for value in asset.values]


def test_home_without_a_market_value_is_worth_its_purchase_price(client, session):
    add_home(client, acquired_on="2020-03-01")
    home = only_asset(session)
    assert values_of(home) == [(TODAY, 500_000_000)]
    assert (home.base_price, home.base_date) == (500_000_000, date(2020, 3, 1))
    assert reports.manual_value_on(home, date(2021, 1, 1)) == 500_000_000


def test_market_value_is_saved_next_to_the_purchase_price(client, session):
    add_home(client, acquired_on="2020-03-01", market_value="650,000,000")
    home = only_asset(session)
    assert values_of(home) == [(date(2020, 3, 1), 500_000_000), (TODAY, 650_000_000)]
    html = client.client.get(f"/assets/manual/{home.id}").text
    assert "현재 시세" in html and "650,000,000원" in html and "구매가 500,000,000원" in html


def test_market_value_without_a_date_keeps_the_purchase_price_aside(client, session):
    add_home(client, market_value="650000000")
    home = only_asset(session)
    assert values_of(home) == [(TODAY, 650_000_000)]
    assert home.base_price == 500_000_000


def test_bad_market_value_is_rejected(client, session):
    assert "error=amount" in add_home(client, market_value="abc").headers["location"]
    assert session.scalars(select(ManualAsset)).all() == []


def test_lease_has_no_market_value(client, session):
    add_lease(client, market_value="999")
    lease = only_asset(session)
    assert [value.amount for value in lease.values] == [50_000_000] and lease.base_price is None


def test_saved_asset_does_not_ask_for_the_amount_again(client, session):
    location = add_home(client).headers["location"]
    html = client.client.get(location).text
    assert "자산을 추가했습니다." in html
    start = html.index("<summary>시세 바꾸기</summary>")
    assert start < html.index('action="/assets/manual/') and "<details class=\"fix\">" in html[start - 40 : start]
    assert "<summary>자산 지우기</summary>" in html
    assert html.index("팔았거나 계약이 끝났나요") < html.index("<summary>자산 지우기</summary>")


def test_purchase_price_can_be_fixed_later(client, session):
    add_home(client, acquired_on="2020-03-01")
    home = only_asset(session)
    page = f"/assets/manual/{home.id}"
    assert 'name="base_price" inputmode="numeric" placeholder="예: 500000000" value="500000000"' in client.client.get(page).text
    assert "msg=edited" in client.post(f"{page}/edit", form_page=page, name=home.name, tenure="own", base_price="480,000,000").headers["location"]
    session.expire_all()
    assert only_asset(session).base_price == 480_000_000
    assert "error=amount" in client.post(f"{page}/edit", form_page=page, name=home.name, tenure="own", base_price="abc").headers["location"]


def test_wolse_keeps_the_deposit_and_the_rent(client, session):
    assert "/assets/manual/" in add_lease(client, monthly_rent="550,000").headers["location"]
    lease = only_asset(session)
    assert (lease.kind, lease.tenure, lease.monthly_rent) == ("real_estate", "wolse", 550_000)
    assert lease.values[0].amount == 50_000_000


@pytest.mark.parametrize("tenure", ["own", "jeonse"])
def test_rent_is_kept_only_for_wolse(client, session, tenure):
    add_lease(client, tenure=tenure, monthly_rent="숫자 아님")
    home = only_asset(session)
    assert (home.tenure, home.monthly_rent) == (tenure, None)


def test_wolse_can_be_saved_before_the_rent_is_known(client, session):
    add_lease(client, monthly_rent="")
    lease = only_asset(session)
    assert (lease.tenure, lease.monthly_rent) == ("wolse", None)


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"monthly_rent": "abc"}, "amount"),
        ({"amount": ""}, "amount"),
        ({"name": " "}, "name"),
        ({"acquired_on": "2099-01-01"}, "date"),
    ],
)
def test_bad_lease_fields_return_to_the_real_estate_form(client, session, fields, error):
    location = add_lease(client, **fields).headers["location"]
    assert "kind=real_estate" in location and f"error={error}" in location
    assert session.scalars(select(ManualAsset)).all() == []


def test_rent_is_not_an_asset(client, session):
    add_lease(client)
    assert [point.total_assets for point in reports.net_worth_series(session)] == [50_000_000]


def test_home_card_shows_the_rent(client, session):
    add_lease(client)
    from app.home.widgets import holding_cards

    assert holding_cards(session, TODAY)["real_estate"].sections[0].rows[0].hint == "월세 보증금 · 월세 550,000원"
    assert 'href="/assets/manual/new?kind=real_estate">부동산 추가</a>' in client.client.get("/").text


def test_edit_form_uses_the_same_type_buttons(client, session):
    add_lease(client)
    html = client.client.get(f"/assets/manual/{only_asset(session).id}").text
    assert "<legend>타입</legend>" in html
    assert re.findall(r'name="tenure" value="(\w+)" checked', html) == ["wolse"]
    assert 'class="for-wolse" name="monthly_rent"' in html and 'value="550000"' in html


def test_rent_can_be_added_to_a_lease_registered_earlier(client, session):
    add_home(client, tenure="wolse")
    lease = only_asset(session)
    page = f"/assets/manual/{lease.id}"
    assert 'name="monthly_rent"' in client.client.get(page).text
    client.post(f"{page}/edit", form_page=page, name=lease.name, tenure="wolse", monthly_rent="600,000")
    session.expire_all()
    assert only_asset(session).monthly_rent == 600_000
    assert "월세 600,000원" in client.client.get(page).text


def test_edited_rent_follows_the_type(client, session):
    add_lease(client, monthly_rent="")
    lease = only_asset(session)
    page = f"/assets/manual/{lease.id}"

    def saved(**fields):
        client.post(f"{page}/edit", form_page=page, name=lease.name, **fields)
        session.expire_all()
        current = only_asset(session)
        return current.tenure, current.monthly_rent

    assert saved(tenure="wolse", monthly_rent="300000") == ("wolse", 300_000)
    assert saved(tenure="jeonse", monthly_rent="300000") == ("jeonse", None)
    assert saved(tenure="wolse", monthly_rent="") == ("wolse", None)
    assert "error=amount" in client.post(
        f"{page}/edit", form_page=page, name=lease.name, tenure="wolse", monthly_rent="abc"
    ).headers["location"]
    assert saved(tenure="own", monthly_rent="300000") == ("own", None)


def test_held_date_can_be_fixed_later(client, session):
    load(session, [fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -1000, CARD)])
    add_lease(client)
    lease = only_asset(session)
    page = f"/assets/manual/{lease.id}"
    assert reports.net_worth_series(session)[0].total_assets == 1_000_000
    assert f'name="acquired_on" type="date" max="{TODAY}" value="{TODAY}"' in client.client.get(page).text

    def edited(**fields):
        response = client.post(f"{page}/edit", form_page=page, name=lease.name, tenure="wolse", **fields)
        session.expire_all()
        return response.headers["location"]

    assert "msg=edited" in edited(acquired_on="2026-01-01")
    assert only_asset(session).acquired_on == date(2026, 1, 1)
    assert reports.net_worth_series(session)[0].total_assets == 51_000_000
    assert "msg=edited" in edited()  # 비우면 그대로 둔다
    assert only_asset(session).acquired_on == date(2026, 1, 1)
    assert "error=date" in edited(acquired_on="2099-01-01")
    client.post(f"{page}/sold", form_page=page, sold_on="2026-10-01")
    assert "error=date" in edited(acquired_on="2026-10-02")
    assert only_asset(session).acquired_on == date(2026, 1, 1)


def test_amount_fields_are_read_aloud_next_to_their_label(client, session):
    add_car(client)
    pages = [
        "/assets/manual/new?kind=car",
        "/assets/manual/new?kind=real_estate",
        "/assets/manual/new?kind=other",
        f"/assets/manual/{only_asset(session).id}",
    ]
    for page in pages:
        html = client.client.get(page).text
        assert " data-amount" in html, page
        assert '<script src="/js/amount-reading.js" defer></script>' in html, page
    confirm = add_car(client, depreciate="1").text
    assert " data-amount" in confirm and '<script src="/js/amount-reading.js" defer></script>' in confirm
    assert client.client.get("/js/amount-reading.js").status_code == 200


def test_names_are_escaped(client, session):
    add_home(client, name='<script>alert("x")</script>')
    for path in ("/", f"/assets/manual/{only_asset(session).id}"):
        html = client.client.get(path).text
        assert '<script>alert("x")</script>' not in html
        assert "&lt;script&gt;" in html


# --- 자산에 반영


def test_manual_asset_joins_net_worth_and_keeps_the_past(client, session):
    load(session, [fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -1000, CARD)])
    add_home(client)
    points = reports.net_worth_series(session)
    assert [(point.as_of, point.total_assets) for point in points] == [
        (date(2026, 10, 31), 1_000_000),  # 산 날짜를 안 넣었으니 지난 기록은 그대로다
        (TODAY, 501_000_000),
    ]
    assert "501,000,000원" in client.client.get("/").text


def test_acquired_date_reaches_past_records(client, session):
    load(session, [fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -1000, CARD)])
    add_home(client, acquired_on="2026-01-01")
    points = reports.net_worth_series(session)
    assert points[0].as_of == date(2026, 10, 31) and points[0].total_assets == 501_000_000


def test_manual_asset_works_without_any_export(client, session):
    add_home(client)
    points = reports.net_worth_series(session)
    assert [(point.as_of, point.total_assets) for point in points] == [(TODAY, 500_000_000)]
    html = client.client.get("/").text
    assert "500,000,000원" in html
    assert "등록한 부동산이 없습니다" not in html
    assert f'href="/assets/manual/{only_asset(session).id}"' in html


def test_manual_asset_appears_in_the_chart_data(client, session):
    load(session, [fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -1000, CARD)])
    add_car(client, amount="20000000")
    add_home(client, tenure="jeonse", amount="100000000")
    data = client.client.get("/chart/data").json()
    keys = {group["key"]: group["latest"] for group in data["asset_groups"]}
    assert keys["asset:자동차 (직접 입력)"] == 20_000_000
    assert keys["asset:보증금 (직접 입력)"] == 100_000_000
    assert data["asset_range"]["last"] == str(TODAY)
    last = data["snapshots"][-1]["groups"]
    assert last["asset:자유입출금 자산"] == 1_000_000  # 내보내기가 없는 날은 그 전 기록을 이어 쓴다


def test_history_adds_up_to_net_worth_with_manual_assets(client, session):
    load(session, [fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -1000, CARD)])
    add_car(client, amount="20000000")
    add_home(client, acquired_on="2026-01-01")
    lines = reports.asset_history(session)
    for point in reports.net_worth_series(session):
        same = [line for line in lines if line.as_of == point.as_of]
        assert sum(line.amount for line in same if line.side == "asset") == point.total_assets


def test_overlap_with_banksalad_is_flagged(client, session):
    assets = [*ASSETS, fx.AssetRow("부동산", "아파트", 300_000_000)]
    load(session, [fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -1000, CARD)], assets=assets)
    assert "두 번 계산됩니다" not in client.client.get("/").text
    add_home(client)
    assert "두 번 계산됩니다" in client.client.get("/").text


def test_home_cards_follow_acquisition_and_sale(client, session):
    load(session, [fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -1000, CARD)])
    add_home(client, acquired_on="2026-06-01")
    home = only_asset(session)
    assert "등록한 부동산이 없습니다" in client.client.get("/?month=2026-05").text  # 아직 갖고 있지 않은 달
    assert "500,000,000원" in client.client.get("/").text
    page = f"/assets/manual/{home.id}"
    client.post(f"{page}/sold", form_page=page, sold_on="2026-10-01")
    html = client.client.get("/").text
    assert "처분함" in html
    assert "500,000,000원" not in html
    assert "500,000,000원" in client.client.get("/?month=2026-09").text  # 판 달 전에는 그대로 있다


def test_manual_asset_goes_to_the_card_of_its_kind(client, session):
    add_car(client, amount="20000000")
    add_home(client, tenure="jeonse", amount="100000000")
    add_other(client, other_type="physical", name="금 10돈", amount="5000000")
    from app.home.widgets import holding_cards

    cards = holding_cards(session, TODAY)
    assert (cards["car"].total, cards["real_estate"].total, cards["physical"].total) == (20_000_000, 100_000_000, 5_000_000)
    assert cards["real_estate"].sections[0].rows[0].hint == "전세 보증금"


# --- 기타 자산: 현금, 실물, 코인, 펀드, 주식, 연금


def physical_card(html: str) -> str:
    return re.search(r'id="widget-physical">(.*?)</section>', html, re.S).group(1)


def test_other_form_asks_for_type_name_amount_and_memo(client):
    html = client.client.get("/assets/manual/new?kind=other").text
    assert re.findall(r'name="other_type" value="(\w+)"', html) == ["cash", "physical", "coin", "fund", "stock", "pension"]
    assert re.findall(r"<span>(\S+)</span></label>", html) == ["현금", "실물", "코인", "펀드", "주식", "연금"]
    for field in ('name="name"', 'name="amount"', 'name="memo"'):
        assert field in html
    assert ">완료</button>" in html
    assert 'name="acquired_on"' not in html


def test_home_card_opens_the_other_form(client):
    assert 'href="/assets/manual/new?kind=other"' in physical_card(client.client.get("/").text)


def test_adding_other_asset_returns_to_its_card(client, session):
    response = add_other(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/?msg=manual_added#widget-physical"
    asset = only_asset(session)
    assert (asset.kind, asset.other_type, asset.name, asset.memo) == ("other", "coin", "비트코인", "거래소 지갑")
    assert asset.acquired_on == TODAY
    assert reports.manual_value_on(asset, TODAY) == 3_000_000
    html = client.client.get(response.headers["location"]).text
    assert "자산을 추가했습니다." in html
    card = physical_card(html)
    for text in ("코인", "비트코인", "거래소 지갑", "3,000,000원"):
        assert text in card
    assert f'href="/assets/manual/{asset.id}"' in card


def test_other_assets_are_grouped_by_type_on_the_card(client, session):
    add_other(client, other_type="cash", name="비상금", amount="100000", memo="")
    add_other(client, other_type="stock", name="삼성전자", amount="2000000", memo="")
    add_other(client, other_type="stock", name="네이버", amount="1000000", memo="")
    from app.home.widgets import holding_cards

    card = holding_cards(session, TODAY)["physical"]
    assert [(section.title, section.total) for section in card.sections] == [("주식", 3_000_000), ("현금", 100_000)]
    assert card.total == 3_100_000
    assert reports.net_worth_on(session, TODAY).total_assets == 3_100_000


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"other_type": ""}, "other_type"),
        ({"other_type": "nope"}, "other_type"),
        ({"name": "  "}, "name"),
        ({"amount": "abc"}, "amount"),
        ({"amount": ""}, "amount"),
    ],
)
def test_other_asset_needs_type_name_and_amount(client, session, fields, error):
    response = add_other(client, **fields)
    assert response.headers["location"] == f"/assets/manual/new?kind=other&error={error}"
    assert session.scalars(select(ManualAsset)).all() == []
    assert client.client.get(response.headers["location"]).text.count('class="notice error"') == 1


def test_memo_is_optional(client, session):
    add_other(client, memo="   ")
    assert only_asset(session).memo is None


def test_long_memo_is_cut(client, session):
    add_other(client, memo="가" * 400)
    assert len(only_asset(session).memo) == 300


def test_memo_is_escaped(client, session):
    attack = '<script>alert("x")</script>'
    add_other(client, memo=attack)
    for path in ("/", f"/assets/manual/{only_asset(session).id}"):
        html = client.client.get(path).text
        assert attack not in html
        assert "&lt;script&gt;" in html


def test_other_asset_type_and_memo_can_be_changed(client, session):
    add_other(client)
    asset = only_asset(session)
    page = f"/assets/manual/{asset.id}"
    html = client.client.get(page).text
    assert "거래소 지갑" in html and "코인" in html
    response = client.post(f"{page}/edit", form_page=page, name="삼성전자", other_type="stock", memo="새 메모")
    assert "msg=edited" in response.headers["location"]
    assert (asset.name, asset.other_type, asset.memo) == ("삼성전자", "stock", "새 메모")
    assert "error=other_type" in client.post(f"{page}/edit", form_page=page, name="x", other_type="nope").headers["location"]
    assert asset.other_type == "stock"
    client.post(f"{page}/edit", form_page=page, name="삼성전자", other_type="stock", memo="")
    assert asset.memo is None


def test_other_types_become_their_own_chart_groups(client, session):
    add_other(client)
    add_other(client, other_type="physical", name="금 10돈", amount="5000000", memo="")
    response = client.client.get("/chart/data")
    keys = {group["key"]: group["latest"] for group in response.json()["asset_groups"]}
    assert keys == {"asset:코인 (직접 입력)": 3_000_000, "asset:실물 (직접 입력)": 5_000_000}
    assert "거래소 지갑" not in response.text  # 메모는 차트로 보내지 않는다


def test_finance_history_includes_financial_types_but_not_physical(client, session):
    load(session, [fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -1000, CARD)])
    add_other(client)
    add_other(client, other_type="physical", name="금 10돈", amount="5000000")
    add_other(client, other_type="pension", name="개인연금", amount="700000")
    add_other(client, other_type="pension", name="개인연금", amount="300000")  # 같은 이름은 더한다

    def on(day: date) -> dict[tuple[str, str], int]:
        return {(line.group_label, line.product_name): line.amount for line in reports.finance_history(session) if line.as_of == day}

    assert on(date(2026, 10, 31)) == {("자유입출금 자산", BANK): 1_000_000}  # 그날에는 아직 없던 자산이다
    assert on(TODAY) == {
        ("자유입출금 자산", BANK): 1_000_000,
        ("코인 (직접 입력)", "비트코인"): 3_000_000,
        ("연금 (직접 입력)", "개인연금"): 1_000_000,
    }


def test_investment_history_carries_over_to_days_with_only_manual_changes(client, session):
    load(session, [fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -1000, CARD)])
    add_other(client)
    points = reports.investment_history(session)
    assert [point.as_of for point in points] == [date(2026, 10, 31), TODAY]
    assert points[1].valuation == points[0].valuation


# --- 금액 고치기와 감가상각 반영


def test_depreciation_button_saves_the_calculated_value(client, session):
    add_car(client, amount="29000000")
    car = only_asset(session)
    page = client.client.get(f"/assets/manual/{car.id}").text
    assert f'href="/assets/manual/{car.id}/depreciate"' in page
    assert "29,000,000원" in page  # 누르기 전에는 바뀌지 않는다
    expected = calculated_car_value()

    # 확인 화면은 바뀔 금액만 보여 주고 저장하지 않는다
    confirm_page = client.client.get(f"/assets/manual/{car.id}/depreciate").text
    assert "확인, 반영하기" in confirm_page
    assert f"{expected:,}원" in confirm_page
    assert f"{expected - 29_000_000:+,}원" in confirm_page
    session.expire_all()
    assert only_asset(session).values[-1].amount == 29_000_000

    response = client.post(f"/assets/manual/{car.id}/depreciate", form_page=f"/assets/manual/{car.id}/depreciate")
    assert "msg=depreciated" in response.headers["location"]
    session.expire_all()
    value = only_asset(session).values[-1]
    assert (value.amount, value.source) == (expected, "depreciation")
    assert f"{expected:,}원" in client.client.get("/").text


def test_depreciation_is_offered_only_for_cars(client, session):
    add_home(client)
    home = only_asset(session)
    assert "감가상각 반영하기" not in client.client.get(f"/assets/manual/{home.id}").text
    assert client.client.get(f"/assets/manual/{home.id}/depreciate").status_code == 404
    assert client.post(f"/assets/manual/{home.id}/depreciate", form_page=f"/assets/manual/{home.id}").status_code == 404


def test_real_estate_value_can_be_updated(client, session):
    add_home(client)
    home = only_asset(session)
    page = f"/assets/manual/{home.id}"
    assert "msg=value_saved" in client.post(f"{page}/value", form_page=page, amount="550,000,000", as_of="2026-10-01").headers["location"]
    session.expire_all()
    assert [(value.as_of, value.amount) for value in only_asset(session).values] == [
        (date(2026, 10, 1), 550_000_000),
        (TODAY, 500_000_000),
    ]


def test_same_day_overwrites(client, session):
    add_home(client)
    home = only_asset(session)
    page = f"/assets/manual/{home.id}"
    client.post(f"{page}/value", form_page=page, amount="520000000")
    session.expire_all()
    assert [value.amount for value in only_asset(session).values] == [520_000_000]


def test_bad_values_are_rejected(client, session):
    add_home(client)
    home = only_asset(session)
    page = f"/assets/manual/{home.id}"
    assert "error=amount" in client.post(f"{page}/value", form_page=page, amount="abc").headers["location"]
    assert "error=date" in client.post(f"{page}/value", form_page=page, amount="1", as_of="2099-01-01").headers["location"]


def test_value_history_can_be_trimmed_but_not_emptied(client, session):
    add_home(client)
    home = only_asset(session)
    page = f"/assets/manual/{home.id}"
    first = home.values[0].id
    assert "error=last_value" in client.post(f"{page}/values/{first}/delete", form_page=page).headers["location"]
    client.post(f"{page}/value", form_page=page, amount="550000000", as_of="2026-10-01")
    assert "msg=value_deleted" in client.post(f"{page}/values/{first}/delete", form_page=page).headers["location"]
    session.expire_all()
    assert [value.amount for value in only_asset(session).values] == [550_000_000]


def test_value_of_another_asset_cannot_be_deleted(client, session):
    add_home(client)
    add_home(client, name="두 번째")
    first, second = session.scalars(select(ManualAsset).order_by(ManualAsset.id)).all()
    page = f"/assets/manual/{first.id}"
    assert client.post(f"{page}/values/{second.values[0].id}/delete", form_page=page).status_code == 404


def test_asset_details_can_be_edited(client, session):
    add_car(client, amount="20000000")
    car = only_asset(session)
    page = f"/assets/manual/{car.id}"
    response = client.post(
        f"{page}/edit",
        form_page=page,
        name="그랜저",
        car_class="large",
        car_origin="imported",
        first_registered="2022-01",
        base_price="40000000",
        base_date="2022-01",
    )
    assert "msg=edited" in response.headers["location"]
    session.expire_all()
    car = only_asset(session)
    assert (car.name, car.car_class, car.car_origin, car.base_price) == ("그랜저", "large", "imported", 40_000_000)
    assert "error=name" in client.post(f"{page}/edit", form_page=page, name=" ").headers["location"]


# --- 처분과 삭제


def test_sold_asset_leaves_net_worth_but_keeps_its_past(client, session):
    load(session, [fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -1000, CARD)])
    add_home(client, acquired_on="2026-01-01")
    home = only_asset(session)
    page = f"/assets/manual/{home.id}"
    assert "msg=sold" in client.post(f"{page}/sold", form_page=page, sold_on="2026-11-01").headers["location"]
    points = {point.as_of: point.total_assets for point in reports.net_worth_series(session)}
    assert points[date(2026, 10, 31)] == 501_000_000
    assert points[date(2026, 11, 1)] == 1_000_000
    assert points[TODAY] == 1_000_000
    assert "msg=unsold" in client.post(f"{page}/sold", form_page=page, sold_on="").headers["location"]
    assert reports.net_worth_series(session)[-1].total_assets == 501_000_000


def test_sale_before_acquisition_is_rejected(client, session):
    add_home(client, acquired_on="2026-06-01")
    home = only_asset(session)
    page = f"/assets/manual/{home.id}"
    assert "error=date" in client.post(f"{page}/sold", form_page=page, sold_on="2026-01-01").headers["location"]


def test_deleting_removes_the_asset_and_its_values(client, session):
    add_home(client)
    home = only_asset(session)
    page = f"/assets/manual/{home.id}"
    response = client.post(f"{page}/delete", form_page=page)
    assert response.headers["location"] == "/?msg=manual_deleted"
    assert session.scalars(select(ManualAsset)).all() == []
    assert session.scalars(select(ManualAssetValue)).all() == []
    assert "자산을 지웠습니다." in client.client.get(response.headers["location"]).text
    assert client.client.get(page).status_code == 404


def test_deleting_a_home_unlinks_its_payments(client, session, clock):
    add_lease(client)
    home = only_asset(session)
    links = [
        {"key": "집주인", "name": "집주인", "loan": None, "asset": f"manual:{home.id}"},
        {"key": "대출이자", "name": "대출이자", "loan": "liability:장기대출:전세자금대출", "asset": f"manual:{home.id}"},
    ]
    state.set_state(session, clock(), LINKS_KEY, {"links": links})
    page = f"/assets/manual/{home.id}"
    assert client.post(f"{page}/delete", form_page=page).headers["location"] == "/?msg=manual_deleted"
    assert [(link.key, link.loan, link.asset) for link in links_of(session)] == [("대출이자", "liability:장기대출:전세자금대출", None)]


def test_changes_need_a_csrf_token(client, session):
    add_home(client)
    home = only_asset(session)
    assert client.client.post("/assets/manual", data={"kind": "other"}).status_code == 403
    assert client.client.post(f"/assets/manual/{home.id}/delete").status_code == 403
    assert client.client.post(f"/assets/manual/{home.id}/value", data={"amount": "1"}).status_code == 403


def test_pages_have_no_inline_code(client, session):
    add_car(client, amount="20000000")
    for path in ("/", "/assets/manual/new", f"/assets/manual/{only_asset(session).id}", "/recurring"):
        html = client.client.get(path).text
        assert not re.search(r"<script(?![^>]*\ssrc=)", html) and " style=" not in html, path


# --- 반복 거래 판별


def line(day: date, description="상대", amount=-500_000, nature="undetermined", reason="unknown_transfer", key=None, name=None):
    return Line(day, description, amount, nature, reason, key, name, "통장")


def monthly(times: int, last: date = date(2026, 10, 25), **fields) -> list[Line]:
    days = []
    year, month = last.year, last.month
    for _ in range(times):
        days.append(date(year, month, last.day))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return [line(day, **fields) for day in reversed(days)]


AS_OF = date(2026, 10, 30)


def test_monthly_transfer_is_regular():
    group = detect(monthly(6), AS_OF)[0]
    assert (group.kind, group.period, group.typical_day, group.times) == ("regular", "매달", 25, 6)
    assert group.tentative is False
    assert group.typical_amount == 500_000
    assert group.spread == "same"
    assert group.total == 3_000_000
    assert group.needs_review is True


def test_a_skipped_month_is_still_regular():
    lines = [line(date(2026, month, 25)) for month in (2, 3, 4, 5, 7, 8, 9, 10)]
    assert detect(lines, AS_OF)[0].kind == "regular"


def test_payment_days_can_shift_a_little():
    lines = [line(date(2026, month, day)) for month, day in ((5, 25), (6, 26), (7, 24), (8, 25), (9, 28), (10, 25))]
    group = detect(lines, AS_OF)[0]
    assert (group.kind, group.period) == ("regular", "매달")


def test_weekly_and_yearly_periods_are_found():
    weekly = [line(date(2026, 10, 29) - timedelta(weeks=week)) for week in range(8)]
    assert detect(weekly, AS_OF)[0].period == "매주"
    yearly = [line(date(year, 10, 1)) for year in (2024, 2025, 2026)]
    assert detect(yearly, AS_OF)[0].period == "매년"


def test_stopped_payments_are_marked_as_ended():
    group = detect(monthly(6, last=date(2026, 3, 25)), AS_OF)[0]
    assert group.kind == "ended"


def test_burst_of_transfers_is_short_term():
    lines = [line(date(2026, 3, day)) for day in (1, 5, 11, 20)]
    group = detect(lines, AS_OF)[0]
    assert (group.kind, group.period) == ("short", None)


def test_evenly_spaced_burst_is_still_short_term():
    # 며칠 간격으로 세 번 보낸 돈은 간격이 일정해도 정기 결제가 아니다
    lines = [line(date(2026, 9, day)) for day in (3, 9, 17)]
    group = detect(lines, AS_OF)[0]
    assert (group.kind, group.period) == ("short", None)


def test_uneven_gaps_are_irregular():
    lines = [line(day) for day in (date(2026, 1, 3), date(2026, 1, 20), date(2026, 4, 2), date(2026, 8, 30))]
    assert detect(lines, AS_OF)[0].kind == "irregular"


def test_two_monthly_transfers_are_only_tentative():
    lines = monthly(2)
    group = detect(lines, AS_OF)[0]
    assert (group.kind, group.tentative) == ("regular", True)


def test_a_single_transaction_is_not_a_group():
    assert detect([line(date(2026, 1, 1))], AS_OF) == []


def test_same_day_transactions_count_once():
    lines = [line(date(2026, 1, 1)), line(date(2026, 1, 1)), line(date(2026, 2, 1))]
    group = detect(lines, AS_OF)[0]
    assert group.times == 2 and group.rows == 3
    assert group.high == 1_000_000 and group.low == 500_000
    assert group.total == 1_500_000


def test_sending_and_receiving_are_separate():
    lines = [*monthly(4), *monthly(4, amount=10_000, nature="income", reason="source_income", last=date(2026, 10, 20))]
    groups = detect(lines, AS_OF)
    assert {group.direction for group in groups} == {"out", "in"}


def test_money_explained_elsewhere_is_left_out():
    assert detect(monthly(4, nature="internal", reason="own_transfer"), AS_OF) == []
    assert detect(monthly(4, nature="funding", reason="bill_linked"), AS_OF) == []
    assert detect(monthly(4, nature="funding", reason="charge_pair"), AS_OF) == []
    assert detect(monthly(4, nature="ignored", reason="zero_amount"), AS_OF) == []
    assert detect(monthly(4, amount=0), AS_OF) == []


def test_unknown_money_comes_first():
    known = monthly(6, description="큰 지출", amount=-9_000_000, nature="expense", reason="source_spend", key="food", name="식비")
    unknown = monthly(6, description="모르는 상대", amount=-1000)
    groups = detect([*known, *unknown], AS_OF)
    assert [group.description for group in groups] == ["모르는 상대", "큰 지출"]


@pytest.mark.parametrize(
    ("amounts", "expected"),
    [
        ((500_000, 500_000, 500_000), "same"),
        ((100_000, 120_000, 110_000), "similar"),
        ((100_000, 300_000, 900_000), "varied"),
    ],
)
def test_amount_spread(amounts, expected):
    lines = [line(date(2026, 8 + index, 25), amount=-amount) for index, amount in enumerate(amounts)]
    assert detect(lines, AS_OF)[0].spread == expected


@pytest.mark.parametrize(
    ("description", "nature", "category"),
    [
        ("3월 월세", "expense", "housing"),
        ("아파트관리비", "expense", "housing"),
        ("수학학원", "expense", "education"),
        ("계모임", "expense", "dues"),
        ("테니스 동호회", "expense", "dues"),
        ("결혼 축의금", "expense", "dues"),
        ("도시가스", "expense", "finance"),
        ("전기요금", "expense", "finance"),
        ("휴대폰 요금", "expense", "telecom"),
        ("지역화폐 충전", "funding", None),
        ("국민은행 대출이자", "expense", "finance"),
        ("신한 원리금", "expense", "finance"),
        ("주담대 상환", "expense", "finance"),
        ("전세자금대출", "expense", "housing"),  # 전세라는 말이 먼저다
        ("이자카야", "expense", None),  # 이자라는 말로는 대출로 보지 않는다
    ],
)
def test_keywords_suggest_a_route(description, nature, category):
    suggestion = detect(monthly(4, description=description), AS_OF)[0].suggestion
    assert (suggestion.nature, suggestion.category_key) == (nature, category)


def test_far_larger_amount_stays_out_of_the_group():
    deposit = date(2026, 3, 2)
    group = detect([*monthly(6), line(deposit, amount=-50_000_000)], AS_OF)[0]
    assert group.outliers == {deposit}
    assert (group.times, group.high, group.total) == (6, 500_000, 3_000_000)


def test_amount_up_to_five_times_the_usual_stays_in_the_group():
    group = detect([*monthly(6), line(date(2026, 3, 2), amount=-2_500_000)], AS_OF)[0]
    assert group.outliers == frozenset()
    assert group.times == 7


def test_decided_amount_stays_in_the_group_however_large():
    decided = {"nature": "expense", "reason": "rule"}
    group = detect([*monthly(6, **decided), line(date(2026, 3, 2), amount=-50_000_000, **decided)], AS_OF)[0]
    assert group.outliers == frozenset()
    assert group.times == 7


def test_two_days_with_a_far_larger_amount_are_not_a_group():
    assert detect([line(date(2026, 9, 25)), line(date(2026, 10, 25), amount=-50_000_000)], AS_OF) == []


def test_transfer_close_to_the_registered_rent_is_suggested_as_housing():
    suggestion = detect(monthly(4, description="홍길동", amount=-550_000), AS_OF, rents=[600_000])[0].suggestion
    assert (suggestion.nature, suggestion.category_key) == ("expense", "housing")


def test_transfer_far_from_the_registered_rent_is_not_housing():
    suggestion = detect(monthly(4, description="홍길동", amount=-100_000), AS_OF, rents=[600_000])[0].suggestion
    assert suggestion.category_key != "housing"


def test_unpaired_charges_are_suggested_as_charges():
    assert detect(monthly(4, description="네이버페이", reason="charge_unpaired"), AS_OF)[0].suggestion.nature == "funding"


def test_regular_unknown_transfer_is_suggested_as_spending():
    suggestion = detect(monthly(4, description="홍길동"), AS_OF)[0].suggestion
    assert suggestion.nature == "expense"


def test_rule_can_be_limited_to_a_direction():
    from datetime import datetime

    def row(amount: int) -> RowView:
        return RowView(1, datetime(2026, 1, 1), "이체", "이체", "미분류", "상대", amount, "KRW", 1, "account")

    rule = RuleView(id=1, direction="out", set_nature="expense")
    assert rule.matches(row(-100)) and not rule.matches(row(100))
    incoming = RuleView(id=2, direction="in", set_nature="income")
    assert incoming.matches(row(100)) and not incoming.matches(row(-100))
    assert RuleView(id=3, set_nature="expense").matches(row(100))


# --- 규칙으로 반영


LEDGER = [
    fx.tx(f"2026-{month:02d}-25 10:00:00", "이체", "이체", "미분류", "집주인", -500_000, BANK) for month in range(5, 11)
] + [
    fx.tx(f"2026-{month:02d}-10 10:00:00", "지출", "식비", "한식", "단골식당", -20_000, CARD) for month in range(5, 11)
] + [
    fx.tx("2026-07-01 10:00:00", "이체", "이체", "미분류", "집주인", 5_000_000, BANK),
]


def save_rule(client, **fields):
    defaults = {"description": "집주인", "direction": "out", "nature": "expense"}
    return client.post("/recurring/rules", form_page="/recurring", **{**defaults, **fields})


def splits_of(session, description: str, amount: int | None = None) -> list[TxnSplit]:
    query = select(TxnSplit).join(Txn).where(Txn.description == description)
    if amount is not None:
        query = query.where(TxnSplit.amount == amount)
    return session.scalars(query).all()


def test_recurring_page_lists_groups(client, session):
    load(session, LEDGER)
    html = client.client.get("/recurring").text
    assert "집주인" in html
    assert "매달 25일쯤" in html
    assert "분류 필요" in html
    assert "500,000원" in html
    assert "단골식당" in html
    assert client.client.get("/recurring?view=nope").status_code == 200
    assert "여기에 해당하는 거래가 없습니다" in client.client.get("/recurring?view=short").text


# --- 내 이름


def save_own_names(client, names):
    return client.post("/settings/own-names", form_page="/settings", names=names)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("홍길동", ["홍길동"]),
        (" 홍길동 , 홍*동 ", ["홍길동", "홍*동"]),
        ("홍길동, 홍길동", ["홍길동"]),
        ("홍, ,  ", []),
        ("", []),
        ("가나, 다라, 마바, 사아, 자차, 카타", ["가나", "다라", "마바", "사아", "자차"]),
    ],
)
def test_own_names_are_cleaned_up(text, expected):
    assert parse_own_names(text) == expected


def test_saving_my_name_moves_past_transfers_at_once(client, session):
    rows = [
        fx.tx(f"2026-{month:02d}-05 10:00:00", "이체", "이체", "미분류", "홍길동", -300_000, BANK) for month in range(8, 11)
    ] + [fx.tx("2026-09-07 10:00:00", "이체", "이체", "미분류", "홍길동", 100_000, BANK)]
    load(session, rows)
    assert {item.nature for item in splits_of(session, "홍길동")} == {"undetermined"}
    assert "msg=own_names_saved" in save_own_names(client, "홍길동").headers["location"]
    session.expire_all()
    assert {(item.nature, item.nature_reason) for item in splits_of(session, "홍길동")} == {("internal", "own_name")}
    assert "홍길동" not in client.client.get(REVIEW).text
    assert 'value="홍길동"' in client.client.get("/settings").text


def test_my_name_applies_to_future_imports(client, session):
    rows = [fx.tx("2026-09-05 10:00:00", "이체", "이체", "미분류", "밥값", -1000, BANK)]
    load(session, rows)
    save_own_names(client, "홍길동")
    newer = [*rows, fx.tx("2026-10-05 10:00:00", "이체", "이체", "미분류", "홍길동", -300_000, BANK)]
    load(session, newer, inner_name="2026-01-01~2026-11-02.xlsx")
    assert splits_of(session, "홍길동")[0].nature == "internal"


def test_clearing_my_name_returns_transfers_to_review(client, session):
    load(session, [fx.tx("2026-09-05 10:00:00", "이체", "이체", "미분류", "홍길동", -300_000, BANK)])
    save_own_names(client, "홍길동")
    save_own_names(client, "")
    session.expire_all()
    assert splits_of(session, "홍길동")[0].nature == "undetermined"
    assert 'name="names"' in client.client.get("/settings").text


def test_my_name_does_not_touch_what_i_changed_myself(client, session):
    load(session, [fx.tx("2026-09-05 10:00:00", "이체", "이체", "미분류", "홍길동", -300_000, BANK)])
    split = splits_of(session, "홍길동")[0]
    client.post(f"/splits/{split.id}/change", form_page="/review", nature="expense")
    save_own_names(client, "홍길동")
    session.expire_all()
    assert splits_of(session, "홍길동")[0].nature == "expense"


def test_my_name_is_escaped_and_needs_a_csrf_token(client, session):
    save_own_names(client, '<script>"x"</script>')
    html = client.client.get("/settings").text
    assert "<script>\"x\"" not in html
    assert client.client.post("/settings/own-names", data={"names": "홍길동"}).status_code in {400, 403}
    assert own_names_of(session) == ['<script>"x"</script>']


def test_review_links_to_recurring(client):
    assert 'href="/recurring"' in client.client.get("/review").text


def test_rule_moves_past_transactions_into_spending(client, session):
    load(session, LEDGER)
    housing = session.scalar(select(Category.id).where(Category.key == "housing"))
    before = reports.monthly_summary(session)[-1].spend_total
    response = save_rule(client, category=str(housing))
    assert "msg=rule_saved" in response.headers["location"]
    assert reports.monthly_summary(session)[-1].spend_total == before + 500_000
    for split in splits_of(session, "집주인", -500_000):
        assert (split.nature, split.category_id, split.nature_source) == ("expense", housing, "rule")
    assert "규칙 적용 중" in client.client.get("/recurring").text


def test_rule_keeps_money_going_the_other_way_apart(client, session):
    load(session, LEDGER)
    save_rule(client)
    assert splits_of(session, "집주인", 5_000_000)[0].nature == "undetermined"


DEPOSIT = fx.tx("2026-03-02 10:00:00", "이체", "이체", "미분류", "집주인", -50_000_000, BANK)


def test_rule_leaves_a_deposit_to_the_same_target_alone(client, session):
    load(session, [*LEDGER, DEPOSIT])
    save_rule(client)
    rule = session.scalars(select(Rule)).one()
    assert (rule.match_amount_min, rule.match_amount_max) == (None, 2_500_000)
    assert splits_of(session, "집주인", -50_000_000)[0].nature == "undetermined"
    assert splits_of(session, "집주인", -500_000)[0].nature == "expense"


def test_rule_to_my_own_account_has_no_cap(client, session):
    load(session, [*LEDGER, DEPOSIT])
    save_rule(client, nature="internal")
    assert session.scalars(select(Rule)).one().match_amount_max is None
    assert splits_of(session, "집주인", -50_000_000)[0].nature == "internal"


def test_rule_for_money_coming_in_has_no_cap(client, session):
    rows = [fx.tx(f"2026-{month:02d}-25 10:00:00", "이체", "이체", "미분류", "세입자", 500_000, BANK) for month in range(5, 11)]
    load(session, rows)
    save_rule(client, description="세입자", direction="in", nature="income")
    assert session.scalars(select(Rule)).one().match_amount_max is None


def test_rule_applies_to_future_imports(client, session):
    load(session, LEDGER)
    save_rule(client)
    newer = [*LEDGER, fx.tx("2026-11-01 10:00:00", "이체", "이체", "미분류", "집주인", -500_000, BANK)]
    load(session, newer, inner_name="2026-01-01~2026-11-02.xlsx")
    assert {split.nature for split in splits_of(session, "집주인", -500_000)} == {"expense"}
    assert len(splits_of(session, "집주인", -500_000)) == 7


def test_new_category_is_created_and_reused(client, session):
    load(session, LEDGER)
    save_rule(client, new_category="모임·회비")
    category = session.scalars(select(Category).where(Category.name == "모임·회비")).one()
    assert (category.kind, category.is_system) == ("expense", False)
    assert splits_of(session, "집주인", -500_000)[0].category_id == category.id
    save_rule(client, description="단골식당", new_category="모임·회비", nature="expense")
    assert len(session.scalars(select(Category).where(Category.name == "모임·회비")).all()) == 1
    assert "모임·회비" in client.client.get("/transactions").text
    breakdown = {line.category_name for line in reports.category_breakdown(session)}
    assert "모임·회비" in breakdown


def test_similar_amount_option_limits_the_rule(client, session):
    rows = [*LEDGER, fx.tx("2026-08-10 10:00:00", "이체", "이체", "미분류", "집주인", -50_000, BANK)]
    load(session, rows)
    save_rule(client, similar_only="1")
    rule = session.scalars(select(Rule)).one()
    assert (rule.match_amount_min, rule.match_amount_max) == (400_000, 600_000)
    assert splits_of(session, "집주인", -50_000)[0].nature == "undetermined"
    assert splits_of(session, "집주인", -500_000)[0].nature == "expense"


def test_saving_again_updates_the_same_rule(client, session):
    load(session, LEDGER)
    save_rule(client)
    save_rule(client, nature="internal")
    rule = session.scalars(select(Rule)).one()
    assert rule.set_nature == "internal"
    assert splits_of(session, "집주인", -500_000)[0].nature == "internal"


def test_disabling_returns_to_automatic_classification(client, session):
    load(session, LEDGER)
    save_rule(client)
    rule = session.scalars(select(Rule)).one()
    assert "msg=rule_disabled" in client.post(f"/recurring/rules/{rule.id}/disable", form_page="/recurring").headers["location"]
    assert splits_of(session, "집주인", -500_000)[0].nature == "undetermined"
    assert "적용 중인 규칙" not in client.client.get("/recurring").text
    save_rule(client)
    assert len(session.scalars(select(Rule)).all()) == 1
    assert session.scalars(select(Rule)).one().enabled is True


def test_user_edit_of_one_transaction_beats_the_rule(client, session):
    load(session, LEDGER)
    split = splits_of(session, "집주인", -500_000)[0]
    client.post(f"/splits/{split.id}/change", form_page="/review", nature="internal")
    save_rule(client)
    assert split.nature == "internal"
    others = [item for item in splits_of(session, "집주인", -500_000) if item.id != split.id]
    assert {item.nature for item in others} == {"expense"}


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"description": "없는 상대"}, "group"),
        ({"direction": "in", "description": "단골식당"}, "group"),
        ({"nature": "nope"}, "nature"),
        ({"category": "999999"}, "category"),
        ({"nature": "internal", "new_category": "이름"}, "new_category"),
    ],
)
def test_bad_rules_are_rejected(client, session, fields, error):
    load(session, LEDGER)
    assert f"error={error}" in save_rule(client, **fields).headers["location"]
    assert session.scalars(select(Rule)).all() == []


def test_category_must_fit_the_nature(client, session):
    load(session, LEDGER)
    salary = session.scalar(select(Category.id).where(Category.key == "salary"))
    assert "error=category" in save_rule(client, category=str(salary)).headers["location"]
    assert "error=category" in save_rule(client, nature="internal", category=str(salary)).headers["location"]


def test_saving_nothing_new_creates_no_rule(client, session):
    load(session, LEDGER)
    food = session.scalar(select(Category.id).where(Category.key == "food"))
    response = save_rule(client, description="단골식당", nature="expense", category=str(food))
    assert "error=empty" in response.headers["location"]
    assert session.scalars(select(Rule)).all() == []


def test_rule_routes_need_a_csrf_token(client, session):
    load(session, LEDGER)
    assert client.client.post("/recurring/rules", data={"description": "집주인"}).status_code == 403
    assert client.client.post("/recurring/rules/1/disable").status_code == 403
    assert client.post("/recurring/rules/999999/disable", form_page="/recurring").status_code == 404


def test_descriptions_are_escaped(client, session):
    attack = '<script>alert("x")</script>'
    rows = [fx.tx(f"2026-{month:02d}-25 10:00:00", "이체", "이체", "미분류", attack, -1000, BANK) for month in range(5, 11)]
    load(session, rows)
    html = client.client.get("/recurring").text
    assert attack not in html
    assert "&lt;script&gt;" in html


# --- 같은 상대 판별


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("10월 월세", "11월 월세"),
        ("월세 10월분", "월세 11월분"),
        ("2026.10 관리비", "2026.11 관리비"),
        ("관리비(10월)", "관리비 11월"),
        ("Netflix", "NETFLIX"),
        ("수학학원 1", "수학학원 2"),
    ],
)
def test_dates_and_symbols_do_not_change_the_target(left, right):
    assert target_key(left) == target_key(right)


@pytest.mark.parametrize(("left", "right"), [("집주인", "관리비"), ("월세", "월"), ("12345", "67890")])
def test_different_targets_stay_apart(left, right):
    assert target_key(left) != target_key(right)


def test_months_in_the_text_do_not_split_a_group():
    lines = [line(date(2026, month, 25), description=f"{month}월 월세") for month in range(7, 11)]
    groups = detect(lines, AS_OF)
    assert len(groups) == 1
    assert (groups[0].kind, groups[0].times, groups[0].undetermined) == ("regular", 4, 4)
    assert (groups[0].suggestion.nature, groups[0].suggestion.category_key) == ("expense", "housing")
    assert groups[0].description == "월세"  # 달이 들어간 내용 하나가 묶음의 이름이 되지 않는다


def test_group_with_one_text_keeps_it_as_is():
    lines = [line(date(2026, month, 25), description="10호 관리비") for month in range(7, 11)]
    assert detect(lines, AS_OF)[0].description == "10호 관리비"


def test_regular_money_coming_in_is_suggested_as_income():
    lines = [line(date(2026, month, 25), description="홍길동", amount=300_000) for month in range(7, 11)]
    assert detect(lines, AS_OF)[0].suggestion.nature == "income"


def test_short_groups_get_no_route():
    days = (date(2026, 9, 3), date(2026, 9, 9), date(2026, 9, 17))
    group = detect([line(day, description="친구 회식비") for day in days], AS_OF)[0]
    assert group.kind == "short"
    assert group.suggestion.nature == "undetermined"


# --- 검토 화면의 반복 거래


REVIEW = "/review?queue=undetermined"


def apply_from_review(client, **fields):
    defaults = {"description": "집주인", "direction": "out", "nature": "expense", "back": REVIEW}
    return client.post("/recurring/rules", form_page="/review", **{**defaults, **fields})


def line_count(html: str) -> int:
    return html.count('<div class="line-main">')


def test_undetermined_queue_groups_repeated_targets(client, session):
    load(session, LEDGER)
    html = client.client.get(REVIEW).text
    assert "<h2>반복 거래" in html
    assert "<h2>한 번만 있는 거래</h2>" in html
    assert "집주인" in html and "매달 25일쯤" in html and "6회" in html
    assert '<span class="pill ok">정기</span>' in html
    assert f'name="back" value="{REVIEW}"' in html
    assert line_count(html) == 2  # 묶음 하나와, 한 번만 받은 돈 하나


def test_deposit_is_decided_on_its_own_in_the_queue(client, session):
    load(session, [*LEDGER, DEPOSIT])
    html = client.client.get(REVIEW).text
    assert line_count(html) == 3  # 묶음 하나, 보증금 하나, 한 번만 받은 돈 하나
    assert "6회" in html
    assert "-50,000,000원" in html


def test_registered_rent_preselects_housing_in_the_queue(client, session):
    rows = [fx.tx(f"2026-{month:02d}-25 10:00:00", "이체", "이체", "미분류", "홍길동", -550_000, BANK) for month in range(7, 11)]
    load(session, rows)
    add_lease(client)
    housing = session.scalar(select(Category.id).where(Category.key == "housing"))
    assert f'<option value="{housing}" selected>' in client.client.get(REVIEW).text


def test_group_form_is_ready_to_apply(client, session):
    rows = [fx.tx(f"2026-{month:02d}-25 10:00:00", "이체", "이체", "미분류", "아파트 월세", -500_000, BANK) for month in range(7, 11)]
    load(session, rows)
    housing = session.scalar(select(Category.id).where(Category.key == "housing"))
    html = client.client.get(REVIEW).text
    assert '<option value="expense" selected>' in html
    assert f'<option value="{housing}" selected>' in html
    assert '<details class="fix">\n    <summary>분류 정하기' not in html  # 펼치지 않아도 바로 적용할 수 있다


def test_groups_come_in_order_of_regularity(client, session):
    rows = [
        *LEDGER,
        *[fx.tx(f"2026-09-{day:02d} 20:00:00", "이체", "이체", "미분류", "친구 회식비", -9_000_000, BANK) for day in (3, 9, 17)],
    ]
    load(session, rows)
    html = client.client.get(REVIEW).text
    assert '<span class="pill plain">단기</span>' in html
    assert html.index("집주인") < html.index("친구 회식비")  # 금액이 커도 정기가 먼저다


def test_two_monthly_hits_show_as_a_candidate(client, session):
    rows = [fx.tx(f"2026-{month:02d}-25 10:00:00", "이체", "이체", "미분류", "새 상대", -100_000, BANK) for month in (9, 10)]
    load(session, rows)
    assert '<span class="pill ok">정기 후보</span>' in client.client.get(REVIEW).text


def test_a_target_seen_once_stays_in_the_plain_list(client, session):
    load(session, [fx.tx("2026-10-25 10:00:00", "이체", "이체", "미분류", "처음 보는 상대", -100_000, BANK)])
    html = client.client.get(REVIEW).text
    assert "<h2>반복 거래" not in html
    assert "처음 보는 상대" in html


def test_uncategorized_queue_groups_too(client, session):
    rows = [fx.tx(f"2026-{month:02d}-25 10:00:00", "지출", "기타", "미분류", "모르는 가게", -10_000, CARD) for month in range(7, 11)]
    load(session, rows)
    html = client.client.get("/review?queue=uncategorized").text
    assert "<h2>반복 거래" in html and "모르는 가게" in html
    assert "<h2>반복 거래" not in client.client.get("/review?queue=largest").text


def test_applying_from_review_moves_every_line_and_stays_there(client, session):
    load(session, LEDGER)
    housing = session.scalar(select(Category.id).where(Category.key == "housing"))
    before = reports.monthly_summary(session)[-1].spend_total
    response = apply_from_review(client, category=str(housing))
    assert response.headers["location"] == f"{REVIEW}&msg=rule_saved"
    html = client.client.get(response.headers["location"]).text
    assert "저장했습니다" in html
    assert "매달 25일쯤" not in html  # 다 옮겨서 더는 검토할 묶음이 아니다
    assert reports.monthly_summary(session)[-1].spend_total == before + 500_000
    for split in splits_of(session, "집주인", -500_000):
        assert (split.nature, split.category_id, split.nature_source) == ("expense", housing, "rule")


def test_rule_follows_the_target_when_the_text_changes(client, session):
    rows = [fx.tx(f"2026-{month:02d}-25 10:00:00", "이체", "이체", "미분류", f"{month}월 월세", -500_000, BANK) for month in range(7, 11)]
    load(session, rows)
    assert "error" not in apply_from_review(client, description="8월 월세").headers["location"]
    newer = [*rows, fx.tx("2026-11-01 10:00:00", "이체", "이체", "미분류", "11월 월세", -500_000, BANK)]
    load(session, newer, inner_name="2026-01-01~2026-11-02.xlsx")
    assert splits_of(session, "11월 월세")[0].nature == "expense"
    assert len(session.scalars(select(Rule)).all()) == 1
    apply_from_review(client, description="9월 월세", nature="internal")  # 같은 상대면 규칙을 새로 만들지 않고 고친다
    assert [rule.set_nature for rule in session.scalars(select(Rule))] == ["internal"]


def test_error_from_review_is_shown_on_review(client, session):
    load(session, LEDGER)
    response = apply_from_review(client, nature="nope")
    assert response.headers["location"] == f"{REVIEW}&error=nature"
    assert "종류를 골라 주세요." in client.client.get(response.headers["location"]).text


def test_leaving_it_undetermined_saves_nothing(client, session):
    load(session, LEDGER)
    assert "error=empty" in apply_from_review(client, nature="undetermined").headers["location"]
    assert session.scalars(select(Rule)).all() == []


@pytest.mark.parametrize("back", ["https://evil.example/review", "//evil.example/recurring", "/settings", "javascript:alert(1)", ""])
def test_rule_back_never_leaves_these_screens(client, session, back):
    load(session, LEDGER)
    parts = urlsplit(apply_from_review(client, back=back).headers["location"])
    assert not parts.scheme and not parts.netloc
    assert parts.path in {"/recurring", "/review"}


# --- 거래 하나에서 같은 상대 전체로


def new_landlord(session):
    rows = [fx.tx("2026-10-25 10:00:00", "이체", "이체", "미분류", "새 집주인", -700_000, BANK)]
    load(session, rows)
    return rows, splits_of(session, "새 집주인")[0]


def test_one_line_can_route_its_target(client, session):
    rows, split = new_landlord(session)
    housing = session.scalar(select(Category.id).where(Category.key == "housing"))
    response = client.post(
        f"/splits/{split.id}/change", form_page="/review", nature="expense", category=str(housing), remember="1", back=REVIEW
    )
    assert response.headers["location"] == f"{REVIEW}&msg=rule_saved"
    assert (split.nature, split.category_id, split.nature_source) == ("expense", housing, "rule")
    rule = session.scalars(select(Rule)).one()
    assert (rule.match_description_equals, rule.match_direction, rule.created_from) == ("새 집주인", "out", "user")
    newer = [*rows, fx.tx("2026-11-01 10:00:00", "이체", "이체", "미분류", "새 집주인", -700_000, BANK)]
    load(session, newer, inner_name="2026-01-01~2026-11-02.xlsx")
    assert {item.nature for item in splits_of(session, "새 집주인")} == {"expense"}


def test_one_line_rule_leaves_a_far_larger_amount_alone(client, session):
    rows, split = new_landlord(session)
    client.post(f"/splits/{split.id}/change", form_page="/review", nature="expense", remember="1", back=REVIEW)
    assert session.scalars(select(Rule)).one().match_amount_max == 3_500_000
    newer = [*rows, fx.tx("2026-11-01 10:00:00", "이체", "이체", "미분류", "새 집주인", -50_000_000, BANK)]
    load(session, newer, inner_name="2026-01-01~2026-11-02.xlsx")
    assert splits_of(session, "새 집주인", -50_000_000)[0].nature == "undetermined"


def test_one_line_rule_to_my_own_account_has_no_cap(client, session):
    _, split = new_landlord(session)
    client.post(f"/splits/{split.id}/change", form_page="/review", nature="internal", remember="1", back=REVIEW)
    assert session.scalars(select(Rule)).one().match_amount_max is None


def test_without_the_checkbox_only_that_line_changes(client, session):
    _, split = new_landlord(session)
    client.post(f"/splits/{split.id}/change", form_page="/review", nature="expense")
    assert split.nature_source == "user"
    assert session.scalars(select(Rule)).all() == []


@pytest.mark.parametrize(
    ("nature", "category_key", "error"),
    [("expense", "salary", "category_mismatch"), ("internal", "food", "category_not_allowed")],
)
def test_one_line_rule_checks_the_category(client, session, nature, category_key, error):
    _, split = new_landlord(session)
    category = session.scalar(select(Category.id).where(Category.key == category_key))
    response = client.post(f"/splits/{split.id}/change", form_page="/review", nature=nature, category=str(category), remember="1")
    assert f"error={error}" in response.headers["location"]
    assert session.scalars(select(Rule)).all() == []
    assert split.nature == "undetermined"


def test_line_form_offers_the_checkbox(client, session):
    new_landlord(session)
    assert 'name="remember"' in client.client.get(REVIEW).text


# --- 분류 폼의 카테고리 목록


DEPOSIT_DAYS = ("2026-02-21", "2026-02-22", "2026-04-09")


def one_won_deposits():
    """계좌를 인증할 때 받은 1원. 뱅크샐러드는 투자로 보내지만 금융수입이다."""
    return [fx.tx(f"{day} 10:00:00", "이체", "투자", "미분류", "삼성", 1, BANK) for day in DEPOSIT_DAYS]


def investment_deposits():
    """뱅크샐러드가 투자로 보낸 입금. 저축·투자에는 카테고리가 없다."""
    return [fx.tx(f"{day} 10:00:00", "이체", "투자", "미분류", "삼성", 100_000, BANK) for day in DEPOSIT_DAYS]


def category_select_of(html: str, description: str) -> str:
    start = html.index(f'name="description" value="{description}"')
    begin = html.index('<select name="category"', start)
    return html[begin : html.index("</select>", begin)]


def test_kind_without_categories_locks_the_list(client, session):
    load(session, investment_deposits())
    select_html = category_select_of(client.client.get("/recurring?view=irregular").text, "삼성")
    assert select_html.split(">")[0].endswith(" disabled")
    assert '<option value="">카테고리 없음</option>' in select_html
    assert 'data-kind="expense" hidden disabled' in select_html and 'data-kind="income" hidden disabled' in select_html


def test_one_won_deposit_is_financial_income_on_its_own(client, session):
    load(session, one_won_deposits())
    income = session.scalar(select(Category.id).where(Category.key == "financial_income"))
    splits = session.scalars(select(TxnSplit)).all()
    assert {(split.nature, split.category_id, split.nature_reason) for split in splits} == {("income", income, "one_won")}
    fields = {"description": "삼성", "direction": "in", "category": str(income)}
    assert "error=category" in client.post("/recurring/rules", form_page="/recurring", nature="asset_move", **fields).headers["location"]
    assert "error=empty" in client.post("/recurring/rules", form_page="/recurring", nature="income", **fields).headers["location"]


def test_auto_choice_shows_the_category_it_will_use(client, session):
    rows = [fx.tx(f"2026-{month:02d}-25 10:00:00", "수입", "금융수입", "미분류", "예금 이자", 1200, BANK) for month in range(5, 10)]
    load(session, rows)
    select_html = category_select_of(client.client.get("/recurring").text, "예금 이자")
    assert 'data-auto-income="금융수입"' in select_html and 'data-auto-expense="미분류 지출"' in select_html
    assert '<option value="">금융수입</option>' in select_html  # 들어갈 카테고리 이름 그대로다
    assert 'data-kind="income">' in select_html and 'data-kind="expense" hidden disabled' in select_html
    assert '<option value="internal">내 계좌 이동</option>' in select_html


PEOPLE = [
    fx.tx("2026-03-02 10:00:00", "이체", "이체", "미분류", "김철수", -70_000, BANK),
    fx.tx("2026-03-03 10:00:00", "이체", "미분류", "미분류", "이영희", 50_000, BANK),
]


def test_review_offers_buttons_for_the_direction(client, session):
    load(session, PEOPLE)
    html = client.client.get("/review").text
    assert 'name="choice" value="expense:dues"' in html  # 보낸 돈
    for key in ("education", "apparel", "entertainment", "beauty"):  # 사람에게 보낸 돈으로도 자주 쓰는 카테고리
        assert f'name="choice" value="expense:{key}"' in html
    assert 'name="choice" value="income:allowance"' in html  # 받은 돈
    assert 'name="choice" value="asset_move"' in html  # 보증금처럼 돌려받을 돈


def test_one_click_classifies_a_single_transaction(client, session):
    load(session, PEOPLE)
    split = next(split for split in session.scalars(select(TxnSplit)) if split.nature == "undetermined" and split.amount < 0)
    response = client.post(f"/splits/{split.id}/change", form_page="/review", choice="expense:dues")
    assert "changed=" in response.headers["location"]
    session.expire_all()
    dues = session.scalar(select(Category.id).where(Category.key == "dues"))
    assert (session.get(TxnSplit, split.id).nature, session.get(TxnSplit, split.id).category_id) == ("expense", dues)
    assert "error=unknown_category" in client.post(f"/splits/{split.id}/change", form_page="/review", choice="expense:nope").headers["location"]


def test_one_click_saves_a_group_rule(client, session):
    load(session, investment_deposits())
    fields = {"description": "삼성", "direction": "in", "nature": "asset_move", "new_category": "무시될 이름"}
    assert "msg=rule_saved" in client.post("/recurring/rules", form_page="/recurring", choice="income:other_income", **fields).headers["location"]
    other = session.scalar(select(Category.id).where(Category.key == "other_income"))
    assert {(split.nature, split.category_id) for split in session.scalars(select(TxnSplit))} == {("income", other)}
    assert session.scalar(select(Category.id).where(Category.name == "무시될 이름")) is None  # 버튼이 새 카테고리 칸보다 먼저다


def test_internal_can_be_picked_from_the_category_list(client, session):
    load(session, one_won_deposits())
    fields = {"description": "삼성", "direction": "in", "nature": "income", "category": "internal"}
    assert "msg=rule_saved" in client.post("/recurring/rules", form_page="/recurring", **fields).headers["location"]
    assert {split.nature for split in session.scalars(select(TxnSplit))} == {"internal"}


def test_line_form_keeps_to_its_kind_and_can_move_to_internal(client, session):
    load(session, [fx.tx("2026-03-02 10:00:00", "지출", "식비", "한식", "밥집", -9000, CARD)])
    html = client.client.get("/transactions").text
    select_html = category_select_of(html.replace("/splits/", 'name="description" value="밥집" /splits/', 1), "밥집")
    split = session.scalars(select(TxnSplit)).one()
    name = session.get(Category, split.category_id).name
    assert f'data-current="{name}" data-current-kind="expense"' in select_html
    assert f'<option value="">그대로 두기 ({name})</option>' in select_html
    assert 'data-kind="income" hidden disabled' in select_html
    response = client.post(f"/splits/{split.id}/change", form_page="/transactions", nature="expense", category="internal")
    assert "changed=" in response.headers["location"]
    session.expire_all()
    assert session.get(TxnSplit, split.id).nature == "internal"


@pytest.mark.parametrize("page", ["/recurring", "/review", "/transactions"])
def test_category_forms_load_their_script(client, page):
    html = client.client.get(page).text
    assert '<script src="/js/category-choices.js" defer></script>' in html
    # 적용을 누르면 그 자리에서 저장하고, 화면을 새로 그려도 같은 항목을 찾을 수 있다
    assert '<script src="/js/review-forms.js" defer></script>' in html
    assert html.count('class="fix-form" data-inline') == html.count('class="fix-form"')
    assert html.count("data-item=") >= html.count('action="/recurring/rules"')


def test_category_from_the_bank_follows_the_kind(client, session):
    rows = [fx.tx(f"2026-{month:02d}-25 10:00:00", "수입", "금융수입", "미분류", "예금 이자", 1200, BANK) for month in range(5, 10)]
    load(session, rows)
    client.post("/recurring/rules", form_page="/recurring", description="예금 이자", direction="in", nature="refund")
    unknown = session.scalar(select(Category.id).where(Category.key == "uncategorized_expense"))
    # 환불·정산은 지출 쪽이라 뱅크샐러드의 수입 카테고리(금융수입)를 붙이지 않는다.
    assert {(split.nature, split.category_id) for split in session.scalars(select(TxnSplit))} == {("refund", unknown)}
