"""직접 입력하는 자산: 자동차, 부동산, 기타. 값은 날짜별로 쌓여 순자산과 차트에 함께 잡힌다."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_db, now_of, render
from app.format import kst_today
from app.ledger import depreciation, labels, reports
from app.ledger.models import (
    CAR_CLASSES,
    CAR_ORIGINS,
    MANUAL_ASSET_KINDS,
    OTHER_ASSET_TYPES,
    TENURES,
    ManualAsset,
    ManualAssetValue,
)
from app.ledger.payment_links import LINKS_KEY, links_of, links_value
from app.ledger.reclassify import reclassify
from app.ops import state

router = APIRouter()

MAX_AMOUNT = 10_000_000_000_000  # 1경 미만이면 충분하고, bigint 범위 오류를 막는다
MAX_NAME_LENGTH = 60
MAX_MEMO_LENGTH = 300
MESSAGES = {
    "created": "자산을 추가했습니다.",
    "value_saved": "금액을 저장했습니다.",
    "depreciated": "감가상각을 반영했습니다.",
    "edited": "정보를 고쳤습니다.",
    "value_deleted": "기록을 지웠습니다.",
    "sold": "이날부터 자산에서 뺍니다.",
    "unsold": "다시 자산에 넣었습니다.",
    "deleted": "자산을 지웠습니다.",
}
ERRORS = {
    "name": "이름을 입력해 주세요.",
    "amount": "금액을 숫자로 입력해 주세요.",
    "date": "날짜를 확인해 주세요. 앞으로의 날짜는 넣을 수 없습니다.",
    "car": "차급, 제조국, 최초 등록 연월을 확인해 주세요.",
    "tenure": "타입을 골라 주세요.",
    "other_type": "종류를 골라 주세요.",
    "last_value": "기록이 하나뿐이면 지울 수 없습니다. 자산 전체를 지우려면 아래의 자산 지우기를 눌러 주세요.",
}


def _see_other(path: str, **params: str) -> RedirectResponse:
    return RedirectResponse(f"{path}?{urlencode(params)}" if params else path, status_code=303)


def parse_amount(value: str) -> int | None:
    """쉼표, 공백, '원'을 허용한다. 음수는 받지 않는다."""
    cleaned = re.sub(r"[,\s원]", "", value or "")
    if not cleaned.isascii() or not cleaned.isdigit() or len(cleaned) > 14:
        return None
    amount = int(cleaned)
    return amount if amount < MAX_AMOUNT else None


def parse_day(value: str, today: date) -> date | None:
    """연월만 받으면 그달 1일로 본다. 앞으로의 날짜는 받지 않는다."""
    text = (value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}", text):
        text += "-01"
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None
    return day if day <= today else None


def _asset_or_404(db: Session, asset_id: int) -> ManualAsset:
    asset = db.get(ManualAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404)
    return asset


def _set_value(db: Session, asset: ManualAsset, day: date, amount: int, source: str) -> None:
    """하루에 값은 하나다. 같은 날 다시 넣으면 덮어쓴다."""
    existing = db.scalar(
        select(ManualAssetValue).where(ManualAssetValue.asset_id == asset.id, ManualAssetValue.as_of == day)
    )
    if existing is not None:
        existing.amount = amount
        existing.source = source
    else:
        asset.values.append(ManualAssetValue(as_of=day, amount=amount, source=source))
    db.flush()
    db.expire(asset, ["values"])


def _depreciation_of(asset: ManualAsset, today: date) -> dict[str, object] | None:
    if asset.kind != "car" or asset.base_price is None:
        return None
    rate = depreciation.BASE_RATES[(asset.car_class, asset.car_origin)]
    year = depreciation.age_in_years(asset.first_registered, today)
    return {
        "value": depreciation.depreciated_value(
            asset.base_price, asset.car_class, asset.car_origin, asset.first_registered, asset.base_date, today
        ),
        "rate": rate,
        "year": year,
        "factor": depreciation.age_factor(year),
    }


def _clean_memo(value: str) -> str | None:
    return value.strip()[:MAX_MEMO_LENGTH] or None


def _set_tenure(asset: ManualAsset, tenure: str, monthly_rent: str) -> str | None:
    """부동산 타입과 월세를 넣는다. 월세는 전월세에만 있다. 잘못된 값이면 오류 이름을 돌려준다."""
    if tenure not in TENURES:
        return "tenure"
    rent = parse_amount(monthly_rent) if tenure == "wolse" and monthly_rent.strip() else 0
    if rent is None:
        return "amount"
    asset.tenure, asset.monthly_rent = tenure, rent or None
    return None


def _form_context() -> dict[str, object]:
    return {
        "kind_labels": labels.MANUAL_KIND_LABELS,
        "tenure_labels": labels.TENURE_LABELS,
        "tenure_choices": labels.TENURE_CHOICES,
        "other_type_labels": labels.OTHER_TYPE_LABELS,
        "car_class_labels": labels.CAR_CLASS_LABELS,
        "car_origin_labels": labels.CAR_ORIGIN_LABELS,
    }


@router.get("/assets/manual/new", response_class=HTMLResponse)
def new_asset(request: Request, kind: str = "car", error: str | None = None):
    return render(
        request,
        "manual_asset_new.html",
        kind=kind if kind in MANUAL_ASSET_KINDS else "car",
        error=ERRORS.get(error or ""),
        today=kst_today(now_of(request)),
        **_form_context(),
    )


@router.post("/assets/manual")
def create_asset(
    request: Request,
    db: Session = Depends(get_db),
    kind: str = Form("car"),
    name: str = Form(""),
    amount: str = Form(""),
    acquired_on: str = Form(""),
    tenure: str = Form(""),
    monthly_rent: str = Form(""),
    other_type: str = Form(""),
    memo: str = Form(""),
    car_class: str = Form(""),
    car_origin: str = Form(""),
    first_registered: str = Form(""),
    base_price: str = Form(""),
    base_date: str = Form(""),
    depreciate: str = Form(""),
    confirmed: str = Form(""),
    market_value: str = Form(""),
):
    now = now_of(request)
    today = kst_today(now)
    kind = kind if kind in MANUAL_ASSET_KINDS else "other"
    back = "/assets/manual/new"
    name = name.strip()[:MAX_NAME_LENGTH]
    if not name:
        return _see_other(back, kind=kind, error="name")

    asset = ManualAsset(kind=kind, name=name, acquired_on=today)
    source = "user"
    value = parse_amount(amount)
    market = None  # 매매로 산 부동산의 지금 시세. 비우면 구매가로 본다
    if kind == "car":
        registered = parse_day(first_registered, today)
        price = parse_amount(base_price)
        if car_class not in CAR_CLASSES or car_origin not in CAR_ORIGINS or registered is None:
            return _see_other(back, kind=kind, error="car")
        if price is None:
            return _see_other(back, kind=kind, error="amount")
        priced_on = parse_day(base_date, today) if base_date.strip() else registered
        if priced_on is None or priced_on < registered:
            return _see_other(back, kind=kind, error="date")
        asset.car_class, asset.car_origin = car_class, car_origin
        asset.first_registered, asset.base_price, asset.base_date = registered, price, priced_on
        asset.acquired_on = priced_on
        if depreciate:
            # 계산 결과를 보여 주고, 확인을 받은 뒤에만 저장한다. 금액은 매번 서버에서 다시 계산한다.
            value = depreciation.depreciated_value(price, car_class, car_origin, registered, priced_on, today)
            source = "depreciation"
            if not confirmed:
                return render(
                    request,
                    "manual_asset_confirm.html",
                    calculated=value,
                    fields={
                        "kind": kind,
                        "name": name,
                        "car_class": car_class,
                        "car_origin": car_origin,
                        "first_registered": first_registered,
                        "base_price": price,
                        "base_date": base_date,
                    },
                    price=price,
                    priced_on=priced_on,
                    rate=depreciation.BASE_RATES[(car_class, car_origin)],
                    year=depreciation.age_in_years(registered, today),
                    **_form_context(),
                )
        elif not amount.strip():
            value = price  # 감가상각을 고르지 않으면 산 가격으로 시작한다
    elif kind == "other":
        if other_type not in OTHER_ASSET_TYPES:
            return _see_other(back, kind=kind, error="other_type")
        asset.other_type = other_type
        asset.memo = _clean_memo(memo)
    else:
        error = _set_tenure(asset, tenure, monthly_rent)
        if error:
            return _see_other(back, kind=kind, error=error)
        if acquired_on.strip():
            held_from = parse_day(acquired_on, today)
            if held_from is None:
                return _see_other(back, kind=kind, error="date")
            asset.acquired_on = held_from
        if asset.tenure == "own" and market_value.strip():
            market = parse_amount(market_value)
            if market is None:
                return _see_other(back, kind=kind, error="amount")
    if value is None:
        return _see_other(back, kind=kind, error="amount")
    if kind == "real_estate" and asset.tenure == "own":
        asset.base_price, asset.base_date = value, asset.acquired_on  # 구매가. 시세를 따로 넣어도 남겨 둔다

    db.add(asset)
    db.flush()
    if market is not None and asset.acquired_on < today:
        _set_value(db, asset, asset.acquired_on, value, source)  # 산 날의 값은 구매가다
    _set_value(db, asset, today, value if market is None else market, source)
    state.record(db, now, "manual_asset_created", kind=kind)
    db.commit()
    if kind == "other":
        # 이름과 금액만 넣는 자산이라 상세 화면을 거치지 않고 추가한 카드로 돌아간다.
        return RedirectResponse("/?msg=manual_added#widget-physical", status_code=303)
    return _see_other(f"/assets/manual/{asset.id}", msg="created")


@router.get("/assets/manual/{asset_id}", response_class=HTMLResponse)
def asset_detail(
    request: Request,
    asset_id: int,
    db: Session = Depends(get_db),
    msg: str | None = None,
    error: str | None = None,
):
    asset = _asset_or_404(db, asset_id)
    today = kst_today(now_of(request))
    return render(
        request,
        "manual_asset.html",
        asset=asset,
        today=today,
        current=reports.manual_value_on(asset, today),
        latest=asset.values[-1] if asset.values else None,
        history=list(reversed(asset.values)),
        depreciation=_depreciation_of(asset, today),
        message=MESSAGES.get(msg or ""),
        error=ERRORS.get(error or ""),
        **_form_context(),
    )


@router.post("/assets/manual/{asset_id}/value")
def save_value(
    request: Request,
    asset_id: int,
    db: Session = Depends(get_db),
    amount: str = Form(""),
    as_of: str = Form(""),
):
    now = now_of(request)
    today = kst_today(now)
    asset = _asset_or_404(db, asset_id)
    value = parse_amount(amount)
    if value is None:
        return _see_other(f"/assets/manual/{asset_id}", error="amount")
    day = parse_day(as_of, today) if as_of.strip() else today
    if day is None:
        return _see_other(f"/assets/manual/{asset_id}", error="date")
    _set_value(db, asset, day, value, "user")
    state.record(db, now, "manual_asset_value_saved")
    db.commit()
    return _see_other(f"/assets/manual/{asset_id}", msg="value_saved")


@router.get("/assets/manual/{asset_id}/depreciate", response_class=HTMLResponse)
def confirm_depreciation(request: Request, asset_id: int, db: Session = Depends(get_db)):
    """바뀔 금액을 먼저 보여 준다. 여기서 확인을 눌러야 저장된다."""
    today = kst_today(now_of(request))
    asset = _asset_or_404(db, asset_id)
    result = _depreciation_of(asset, today)
    if result is None:
        raise HTTPException(status_code=404)
    current = reports.manual_value_on(asset, today)
    return render(
        request,
        "manual_asset_depreciate.html",
        asset=asset,
        current=current,
        change=result["value"] - current,
        depreciation=result,
    )


@router.post("/assets/manual/{asset_id}/depreciate")
def apply_depreciation(request: Request, asset_id: int, db: Session = Depends(get_db)):
    now = now_of(request)
    today = kst_today(now)
    asset = _asset_or_404(db, asset_id)
    result = _depreciation_of(asset, today)
    if result is None:
        raise HTTPException(status_code=404)
    _set_value(db, asset, today, result["value"], "depreciation")
    state.record(db, now, "manual_asset_depreciated")
    db.commit()
    return _see_other(f"/assets/manual/{asset_id}", msg="depreciated")


@router.post("/assets/manual/{asset_id}/edit")
def edit_asset(
    request: Request,
    asset_id: int,
    db: Session = Depends(get_db),
    name: str = Form(""),
    tenure: str = Form(""),
    monthly_rent: str = Form(""),
    acquired_on: str = Form(""),
    other_type: str = Form(""),
    memo: str = Form(""),
    car_class: str = Form(""),
    car_origin: str = Form(""),
    first_registered: str = Form(""),
    base_price: str = Form(""),
    base_date: str = Form(""),
):
    now = now_of(request)
    today = kst_today(now)
    asset = _asset_or_404(db, asset_id)
    back = f"/assets/manual/{asset_id}"
    name = name.strip()[:MAX_NAME_LENGTH]
    if not name:
        return _see_other(back, error="name")
    if asset.kind == "car":
        registered = parse_day(first_registered, today)
        price = parse_amount(base_price)
        if car_class not in CAR_CLASSES or car_origin not in CAR_ORIGINS or registered is None:
            return _see_other(back, error="car")
        if price is None:
            return _see_other(back, error="amount")
        priced_on = parse_day(base_date, today) if base_date.strip() else registered
        if priced_on is None or priced_on < registered:
            return _see_other(back, error="date")
        asset.car_class, asset.car_origin = car_class, car_origin
        asset.first_registered, asset.base_price, asset.base_date = registered, price, priced_on
    elif asset.kind == "real_estate":
        held_from = parse_day(acquired_on, today) if acquired_on.strip() else asset.acquired_on
        if held_from is None or (asset.sold_on is not None and held_from > asset.sold_on):
            return _see_other(back, error="date")
        error = _set_tenure(asset, tenure, monthly_rent)
        if error:
            return _see_other(back, error=error)
        if asset.tenure == "own" and base_price.strip():
            price = parse_amount(base_price)
            if price is None:
                return _see_other(back, error="amount")
            asset.base_price = price
        asset.acquired_on = held_from
        if asset.base_price is not None:
            asset.base_date = held_from
    else:
        if other_type not in OTHER_ASSET_TYPES:
            return _see_other(back, error="other_type")
        asset.other_type = other_type
        asset.memo = _clean_memo(memo)
    asset.name = name
    state.record(db, now, "manual_asset_edited")
    db.commit()
    return _see_other(back, msg="edited")


@router.post("/assets/manual/{asset_id}/values/{value_id}/delete")
def delete_value(request: Request, asset_id: int, value_id: int, db: Session = Depends(get_db)):
    asset = _asset_or_404(db, asset_id)
    value = db.get(ManualAssetValue, value_id)
    if value is None or value.asset_id != asset.id:
        raise HTTPException(status_code=404)
    if len(asset.values) <= 1:
        return _see_other(f"/assets/manual/{asset_id}", error="last_value")
    asset.values.remove(value)
    state.record(db, now_of(request), "manual_asset_value_deleted")
    db.commit()
    return _see_other(f"/assets/manual/{asset_id}", msg="value_deleted")


@router.post("/assets/manual/{asset_id}/sold")
def mark_sold(request: Request, asset_id: int, db: Session = Depends(get_db), sold_on: str = Form("")):
    now = now_of(request)
    asset = _asset_or_404(db, asset_id)
    if not sold_on.strip():
        asset.sold_on = None
        state.record(db, now, "manual_asset_unsold")
        db.commit()
        return _see_other(f"/assets/manual/{asset_id}", msg="unsold")
    day = parse_day(sold_on, kst_today(now))
    if day is None or day < asset.acquired_on:
        return _see_other(f"/assets/manual/{asset_id}", error="date")
    asset.sold_on = day
    state.record(db, now, "manual_asset_sold")
    db.commit()
    return _see_other(f"/assets/manual/{asset_id}", msg="sold")


def _forget_links(db: Session, now: datetime, key: str) -> None:
    """지운 자산(부동산이나 연금)에 이어 둔 연결을 푼다. 대출에도 이은 납입은 대출 연결만 남긴다."""
    links = links_of(db)
    if not any(key in (link.asset, link.pension) for link in links):
        return
    kept = [
        replace(link, asset=None if link.asset == key else link.asset, pension=None if link.pension == key else link.pension)
        for link in links
    ]
    state.set_state(db, now, LINKS_KEY, links_value(link for link in kept if link.loan or link.asset or link.pension))
    reclassify(db)  # 대출 이자의 카테고리가 주거비에서 바뀐다


@router.post("/assets/manual/{asset_id}/delete")
def delete_asset(request: Request, asset_id: int, db: Session = Depends(get_db)):
    """잘못 넣은 자산을 없앤다. 판 자산은 지우지 말고 처분으로 남겨야 지난 그래프가 맞는다."""
    now = now_of(request)
    db.delete(_asset_or_404(db, asset_id))
    _forget_links(db, now, f"manual:{asset_id}")
    state.record(db, now, "manual_asset_deleted")
    db.commit()
    return _see_other("/", msg="manual_deleted")
