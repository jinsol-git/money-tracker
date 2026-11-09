/* 홈의 대시보드: 달력과 물감 탭. 계산과 그리기는 차트 화면의 것을 그대로 써서 숫자와 색이 같다.
   항목 하나를 누르면 그 항목만 색을 둔 차트 화면으로 간다. */
"use strict";

(() => {
  const TAB_KEY = "money-tracker-dashboard-tab";
  const STRIP_LABEL_ROOM = 14; // 칸 안에 글자를 넣을 때 남겨 둘 여백
  const MULTIPLES = 6;

  const root = document.querySelector("[data-dashboard]");
  if (!root) return;
  const month = root.dataset.month;
  const page = document.querySelector(".home");
  const part = (name) => page.querySelector(`[data-dash-${name}]`);

  function rememberTab() {
    try {
      const saved = window.localStorage.getItem(TAB_KEY);
      if (saved === "paint") document.getElementById("dash-paint").checked = true;
    } catch {
      return; // 저장소를 막은 창은 기본 탭으로 연다.
    }
    for (const radio of page.querySelectorAll(".dash-radio")) {
      radio.addEventListener("change", () => {
        try {
          window.localStorage.setItem(TAB_KEY, radio.value);
        } catch {
          /* 저장하지 못해도 탭은 바뀐다. */
        }
      });
    }
  }

  function chartHref(overrides, form) {
    const state = newState(overrides);
    if (form) state.forms[state.tab][state.view] = form;
    const query = stateToQuery(state);
    return query ? `/chart?${query}` : "/chart";
  }

  /* 차트 화면의 그리기 함수가 쓰는 것만 갖춘 작은 화면. */
  function miniView(data, canvas, overrides, form, handlers) {
    const tooltip = htmlNode("div", "chart-tooltip", canvas);
    tooltip.hidden = true;
    const empty = htmlNode("p", "chart-empty", canvas, "이 달의 기록이 없습니다.");
    empty.hidden = true;
    const state = newState(overrides);
    if (form) state.forms[state.tab][state.view] = form;
    const view = { data, canvas, tooltip, empty, state, chart: null, ...handlers };
    view.update = () => {
      view.period = resolvePeriod(data, state);
      view.model = buildModel(data, state, view.period);
      if (view.legend) drawLegend(view);
      drawChart(view);
    };
    view.update();
    new ResizeObserver(() => drawChart(view)).observe(canvas);
    document.fonts?.addEventListener("loadingdone", () => drawChart(view));
    return view;
  }

  /* 고른 달의 비중을 한 줄로 본다. 칸에 들어갈 때만 비중을 적고, 이름은 아래 범례에 있다. */
  function drawStrip(view, holder, legend) {
    const items = foldRest(view.model.items);
    holder.replaceChildren(view.tooltip);
    legend.replaceChildren();
    part("total").textContent = view.model.total ? won(view.model.total) : "";
    if (!items.length) htmlNode("span", "hint", holder, "이 달의 지출이 없습니다.");
    holder.setAttribute("aria-label", items.map((item) => `${item.name} ${percentText(item.share)}`).join(", "));
    for (const item of items) {
      const block = htmlNode("a", `strip-block series-${item.slot}`, holder);
      block.href = chartHref({ month, focus: item.key });
      block.style.flexGrow = String(item.value);
      block.setAttribute("aria-label", shareDescription(item));
      const label = htmlNode("span", "", block, percentText(item.share));
      block.addEventListener("pointerenter", (event) => {
        if (event.pointerType === "mouse") showShareTooltip(view, item, { x: block.offsetLeft + block.offsetWidth / 2, y: 0 });
      });
      block.addEventListener("pointerleave", () => hideTooltip(view));
      block.addEventListener("focus", () => showShareTooltip(view, item, { x: block.offsetLeft + block.offsetWidth / 2, y: 0 }));
      block.addEventListener("blur", () => hideTooltip(view));
      block.label = label;
      const key = htmlNode("span", "legend-item", legend);
      htmlNode("span", `swatch series-${item.slot}`, key);
      htmlNode("span", "", key, item.name);
    }
    fitStripLabels(holder);
  }

  function fitStripLabels(holder) {
    for (const block of holder.querySelectorAll(".strip-block")) {
      block.label.hidden = false;
      if (block.label.offsetWidth + STRIP_LABEL_ROOM > block.clientWidth) block.label.hidden = true;
    }
  }

  /* 항목마다 같은 눈금 없이 12개월의 모양만 비교한다. */
  function drawMultiples(holder, model) {
    holder.replaceChildren();
    const shown = model.series
      .filter((item) => item.key !== REST)
      .map((item) => ({ item, total: sumOf(model.rows, (row) => row.values[item.key]) }))
      .sort((a, b) => b.total - a.total)
      .slice(0, MULTIPLES);
    const at = model.rows.findIndex((row) => row.key === month);
    for (const { item } of shown) {
      const values = model.rows.map((row) => row.values[item.key]);
      const largest = Math.max(...values, 1);
      const cell = htmlNode("a", "multiple", holder);
      cell.href = chartHref({ month: at >= 0 ? month : null, focus: item.key });
      const head = htmlNode("span", "multiple-head", cell);
      htmlNode("span", "", head, item.name);
      const current = at >= 0 ? values[at] : values[values.length - 1];
      htmlNode("span", "num", head, compactWon(current));
      const change = at > 0 ? values[at] - values[at - 1] : null;
      htmlNode("small", "hint multiple-note", cell, change === null ? "" : `전달보다 ${change > 0 ? "+" : ""}${compactWon(change)}`);
      const bars = htmlNode("span", "multiple-bars", cell);
      model.rows.forEach((row, index) => {
        const bar = htmlNode("i", index === at ? "is-now" : "", bars);
        bar.style.height = `${Math.max(3, (Math.max(values[index], 0) / largest) * 100)}%`;
        bar.title = `${bucketTitle(model.unit, row.key)} ${won(values[index])}`;
      });
    }
  }

  function draw(data) {
    const range = data.flow_range;
    const inRange = Boolean(range) && month >= range.first.slice(0, 7) && month <= range.last.slice(0, 7);
    const bucket = inRange ? month : null;

    // 고른 달을 보여 주는 칸. 기록이 없는 달이면 다른 달을 대신 그리지 않는다.
    const monthOnly = (canvas, overrides, form, handlers) => {
      if (inRange) return miniView(data, canvas, overrides, form, handlers);
      htmlNode("p", "chart-empty", canvas, "이 달의 기록이 없습니다.");
      return null;
    };

    const stripHolder = part("strip");
    const stripView = {
      data,
      canvas: stripHolder,
      tooltip: htmlNode("div", "chart-tooltip", stripHolder),
      state: newState({ bucket }),
    };
    stripView.tooltip.hidden = true;
    if (inRange) {
      stripView.period = resolvePeriod(data, stripView.state);
      stripView.model = buildModel(data, stripView.state, stripView.period);
      drawStrip(stripView, stripHolder, part("strip-legend"));
      new ResizeObserver(() => fitStripLabels(stripHolder)).observe(stripHolder);
      document.fonts?.addEventListener("loadingdone", () => fitStripLabels(stripHolder));
    } else {
      htmlNode("span", "hint", stripHolder, "이 달의 지출이 없습니다.");
    }

    const stack = miniView(data, part("stack"), { view: "trend", bucket }, "stack", {
      legend: part("stack-legend"),
      toggleBucket: (key) => window.location.assign(chartHref({ month: key })),
    });
    // 작은 차트는 범례에서 끈 항목과 상관없이 모든 값을 보여 준다.
    const everything = buildModel(data, { ...stack.state, hidden: new Set() }, stack.period);
    drawMultiples(part("multiples"), everything);

    miniView(data, part("difference"), { tab: "flow", view: "trend", bucket }, "difference", {
      toggleBucket: (key) => window.location.assign(chartHref({ tab: "flow", month: key })),
    });
    monthOnly(part("treemap"), { bucket }, "treemap", {
      toggleFocus: (key) => window.location.assign(chartHref({ month, focus: key })),
    });
    monthOnly(part("sankey"), { tab: "flow", bucket }, "sankey", {});
    drawHomeWidgets(data, month);
    drawHomePreviews(data, month);
  }

  /* 날짜를 누르면 그날의 거래로 바꾼다. 다른 달의 다른 날로 가지 않으므로 주소는 그대로 둔다. */
  function bindCalendar() {
    const details = new Map([...page.querySelectorAll("[data-day-detail]")].map((node) => [node.dataset.dayDetail, node]));
    const days = [...page.querySelectorAll("[data-day]")];
    for (const day of days) {
      day.addEventListener("click", (event) => {
        if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        const detail = details.get(day.dataset.day);
        if (!detail) return;
        event.preventDefault();
        for (const other of days) other.removeAttribute("aria-current");
        day.setAttribute("aria-current", "date");
        for (const node of details.values()) node.hidden = node !== detail;
        detail.scrollIntoView({ block: "nearest" });
      });
    }
  }

  rememberTab();
  bindCalendar();
  fetch("/chart/data", { headers: { Accept: "application/json" } })
    .then((response) => {
      if (!response.ok) throw new Error(String(response.status));
      return response.json();
    })
    .then((raw) => draw(normalize(raw)))
    .catch(() => {
      for (const canvas of page.querySelectorAll(".dash-canvas")) {
        htmlNode("p", "chart-empty", canvas, "차트를 불러오지 못했습니다. 새로고침해 주세요.");
      }
    });
})();
