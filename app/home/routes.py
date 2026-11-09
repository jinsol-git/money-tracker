"""홈: 달을 옮겨 가며 보는 요약 카드와, 넣고 뺄 수 있는 카드들."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import chart_widgets
from app.deps import get_db, now_of, render
from app.format import kst_today, link, month_end, next_month, previous_month, valid_month
from app.home import overview, widgets
from app.ledger import reports
from app.ops import state

router = APIRouter()

MESSAGES = {"manual_added": "자산을 추가했습니다.", "manual_deleted": "자산을 지웠습니다."}


def _empty_month(month: str) -> reports.MonthSummary:
    return reports.MonthSummary(month, 0, 0, 0, 0, 0, 0, 0, 0)


def _partial_note(month: str, as_of: date | None) -> str | None:
    if as_of is None or as_of.strftime("%Y-%m") != month or as_of == month_end(month):
        return None
    return f"{as_of.month}월 {as_of.day}일까지"


def _bridge_rows(bridge: reports.SpendBridge) -> list[tuple[str, int]]:
    rows = [
        ("페이 충전 (쓸 때 이미 지출로 잡힘)", -bridge.charges_removed),
        ("저축·투자·대출 이동", -bridge.moves_removed),
        ("내 계좌 사이 이동", -bridge.internal_removed),
        ("지출이 아니라고 정했거나 아직 정하지 않은 거래", -bridge.other_removed),
        ("사용 내역 없는 카드의 대금", bridge.unlinked_bills_added),
        ("지출로 정한 이체", bridge.other_added),
    ]
    return [row for row in rows if row[1]]


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: Session = Depends(get_db),
    month: str | None = None,
    edit: str | None = None,
    msg: str | None = None,
):
    now = now_of(request)
    today = kst_today(now)
    months = {item.month: item for item in reports.monthly_summary(db)}
    this_month = today.strftime("%Y-%m")
    first = min(months, default=this_month)
    latest = max(months, default=this_month)
    last = max(latest, this_month)
    wanted = valid_month(month)
    selected = wanted if wanted and first <= wanted <= last else latest
    earlier = previous_month(selected)
    later = next_month(selected)

    # 처음 열리는 달과 이번 달은 오늘의 자산을 보여 준다. 방금 넣은 자동차가 안 보이는 일이 없도록 하기 위해서다.
    as_of = today if selected in (latest, this_month) else min(month_end(selected), today)
    summary = months.get(selected) or _empty_month(selected)
    layout = widgets.load_layout(db)
    editing = "1" if edit else None
    status = reports.data_status(db)
    counts = reports.review_counts(db)
    bridge = next((item for item in reports.spend_bridge(db) if item.month == selected), None)
    holdings = widgets.holding_cards(db, as_of)
    net_worth = widgets.net_worth_card(db, as_of, month_end(earlier))
    pending = counts.undetermined + counts.uncategorized
    build_for = {"db": db, "month": selected, "as_of": as_of, "previous_end": month_end(earlier), "summary": summary}
    cards = widgets.build(**build_for, visible=layout.visible, holdings=holdings, net_worth=net_worth)
    # 위젯 화면은 넣지 않은 홈 카드도 그림으로 보여 주므로 편집할 때만 모두 만든다.
    previews = widgets.build(**build_for, visible=list(widgets.WIDGETS), holdings=holdings, net_worth=net_worth) if editing else {}
    return render(
        request,
        "home.html",
        review_badge=pending,
        warnings=state.system_warnings(db, now),
        message=MESSAGES.get(msg or ""),
        has_data=bool(months),
        selected=selected,
        summary=summary,
        previous=months.get(earlier),
        previous_link=link("/", month=earlier, edit=editing) if earlier >= first else None,
        next_link=link("/", month=later, edit=editing) if later <= last else None,
        partial=_partial_note(selected, status.data_as_of),
        month_calendar=overview.calendar(db, selected, min(status.data_as_of or today, today)),
        book=overview.book(
            db,
            month=selected,
            summary=summary,
            as_of=as_of,
            holdings=holdings,
            net_worth=net_worth,
            visible=layout.visible,
        ),
        trend=overview.month_bars(months, selected=selected, first=first, last=last, editing=editing),
        chart_link=link("/chart", tab="category", month=selected),
        bridge=bridge,
        bridge_rows=_bridge_rows(bridge) if bridge else [],
        editing=bool(editing),
        edit_link=link("/", month=wanted, edit="1"),
        done_link=link("/", month=wanted),
        layout=layout,
        cards=cards,
        previews=previews,
        gallery=widgets.gallery() if editing else [],
        titles=widgets.TITLES,
        chart_widgets=chart_widgets.WIDGETS,
        empty_text=widgets.EMPTY_TEXT,
        add_links=widgets.ADD_LINKS,
    )


@router.post("/home/widgets")
def change_widgets(
    request: Request,
    db: Session = Depends(get_db),
    key: str = Form(""),
    action: str = Form(""),
    month: str = Form(""),
    keys: list[str] = Form(default=[]),
):
    if action == "set":  # 위젯 화면에서 고른 순서. 저장하면 편집을 마친다
        if any(item not in widgets.TITLES for item in keys) or len(set(keys)) != len(keys):
            raise HTTPException(status_code=404)
        widgets.set_layout(db, now_of(request), keys)
        db.commit()
        return RedirectResponse(link("/", month=valid_month(month)), status_code=303)
    if key not in widgets.TITLES or action not in widgets.LAYOUT_ACTIONS:
        raise HTTPException(status_code=404)
    widgets.change_layout(db, now_of(request), key, action)
    db.commit()
    return RedirectResponse(link("/", month=valid_month(month), edit="1"), status_code=303)
