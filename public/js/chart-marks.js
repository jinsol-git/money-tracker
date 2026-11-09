/* 위젯의 그림. 폭과 높이를 받아 SVG를 그린다. 모양은 design-proposals/charts.html과 같다.
   밖에서 온 글자는 모두 textContent로 넣는다. CSP가 style 속성을 막으므로 색은 SVG 속성과 CSSOM으로 준다. */
"use strict";

const MARK_MARGIN = { t: 24, r: 4, b: 22, l: 40 };
const WIDE_BAR = 24;
const MARK_BAR_SHARE = 0.62;
const THIN_BAND = 30; // 칸이 이보다 좁으면 눈금 글자 사이가 이만큼 되도록 걸러 적는다
const COMPACT_WIDTH = 420;
const PAIR_LOWER = 74; // 수입 - 지출 막대가 들어가는 아래 칸의 높이
const GANTT_ROW = 34;
const GANTT_HEAD = 26;
const GANTT_GROUP = 24;
const REST_FILL = "color-mix(in srgb, var(--text) 42%, var(--surface))";

function signedLabel(value) {
  return `${value > 0 ? "+" : ""}${labelWon(value)}`;
}

function dotDate(day) {
  return day.replaceAll("-", ".");
}

/* --- 색 */

function slotFill(slot) {
  if (slot === REST) return "var(--bar-muted)";
  if (slot === "leftover") return "var(--accent)";
  if (slot === "muted") return "color-mix(in srgb, var(--text) 45%, var(--surface))";
  return `var(--${slot})`;
}

/* 칸을 채우는 색. 회색 항목도 글자를 얹을 수 있게 조금 더 진하다. */
function solidFill(slot) {
  return slot === REST ? REST_FILL : slotFill(slot);
}

function slotInk(slot) {
  if (slot === REST) return "var(--surface)";
  if (slot === "leftover") return "var(--accent-text)";
  return `var(--on-${slot})`;
}

/* --- 말풍선: 화면에 하나만 두고 커서를 따라간다. */

let tipBox = null;
let tipsInstalled = false;
let tipShownAt = 0;
let touchedMark = null; // 손가락으로 누른 칸. 다시 그린 뒤에도 그 칸의 값을 보여 준다

function setTip(node, title, rows) {
  node.tipInfo = { title, rows };
  node.setAttribute("data-tip", "");
}

function hideTip() {
  if (tipBox) tipBox.hidden = true;
}

function showTip(info, x, y) {
  if (!tipBox) {
    tipBox = htmlNode("div", "tip", document.body);
    tipBox.hidden = true;
  }
  tipBox.replaceChildren();
  htmlNode("b", "", tipBox, info.title);
  for (const row of info.rows) {
    const line = htmlNode("div", "", tipBox);
    if (row.fill) htmlNode("i", "", line).style.background = row.fill;
    htmlNode("span", "", line, row.name);
    htmlNode("em", "", line, row.value);
  }
  tipBox.hidden = false;
  const width = tipBox.offsetWidth;
  const height = tipBox.offsetHeight;
  let left = x + 14;
  let top = y + 14;
  if (left + width > window.innerWidth - 8) left = x - width - 14;
  if (top + height > window.innerHeight - 8) top = y - height - 14;
  tipBox.style.left = `${Math.max(8, left)}px`;
  tipBox.style.top = `${Math.max(8, top)}px`;
  tipShownAt = performance.now();
}

function nearestPoint(points, x) {
  return points.reduce((best, point) => (Math.abs(point.x - x) < Math.abs(best.x - x) ? point : best));
}

/* 선 차트는 커서와 가장 가까운 점을, 나머지는 커서 아래 표식의 값을 보여 준다. */
function tipFromPointer(event) {
  const target = event.target;
  if (!target.closest) return;
  const host = target.closest(".c");
  if (host?.points?.length) {
    const box = host.getBoundingClientRect();
    const point = nearestPoint(host.points, event.clientX - box.left);
    const cross = host.querySelector(".cross");
    if (cross) {
      cross.setAttribute("x1", point.x);
      cross.setAttribute("x2", point.x);
      cross.setAttribute("opacity", 1);
    }
    showTip(point.tip, event.clientX, event.clientY);
    return;
  }
  const mark = target.closest("[data-tip]");
  if (mark?.tipInfo) showTip(mark.tipInfo, event.clientX, event.clientY);
  else hideTip();
}

/* 휴대폰은 칸이 좁아 금액을 말풍선으로만 볼 수 있다. 눌러서 화면을 다시 그렸으면 그 칸의 값을 다시 띄운다. */
function restoreTouchedTip(root) {
  const mark = touchedMark;
  touchedMark = null;
  if (!mark) return;
  const node = [...root.querySelectorAll("[data-mark]")].find((item) => item.dataset.mark === mark);
  if (!node?.tipInfo) return;
  const box = node.getBoundingClientRect();
  showTip(node.tipInfo, box.left + box.width / 2, box.top);
}

function installTips() {
  if (tipsInstalled) return;
  tipsInstalled = true;
  document.addEventListener("pointermove", (event) => {
    if (event.pointerType === "mouse") tipFromPointer(event);
  });
  document.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "mouse") return;
    touchedMark = event.target.closest?.("[data-mark]")?.dataset.mark || null;
    tipFromPointer(event);
  });
  document.addEventListener("pointerout", (event) => {
    if (event.pointerType !== "mouse") return;
    const host = event.target.closest?.(".c");
    if (host && !host.contains(event.relatedTarget)) host.querySelector(".cross")?.setAttribute("opacity", 0);
    if (!event.relatedTarget) hideTip();
  });
  document.addEventListener("focusin", (event) => {
    const mark = event.target.closest?.("[data-tip]");
    if (!mark?.tipInfo || !mark.matches(":focus-visible")) return;
    const box = mark.getBoundingClientRect();
    showTip(mark.tipInfo, box.right, box.top);
  });
  document.addEventListener("focusout", hideTip);
  document.addEventListener(
    "scroll",
    () => {
      // 다시 그린 뒤 자리를 되돌리는 스크롤은 사용자가 움직인 것이 아니다.
      if (performance.now() - tipShownAt > 300) hideTip();
    },
    { passive: true },
  );
}

/* --- 그릴 자리 */

const figureObserver = new ResizeObserver((entries) => entries.forEach((entry) => drawFigure(entry.target)));

function drawFigure(host, force) {
  const width = Math.floor(host.clientWidth);
  if (!width || (!force && host.drawnWidth === width)) return;
  host.drawnWidth = width;
  host.replaceChildren();
  host.points = null;
  host.drawChart(host, width, host.chartHeight);
}

/* 폭이 바뀌면 다시 그린다. 위젯을 다시 놓을 때 resetFigures로 지난 자리를 잊는다. */
function figure(parent, height, drawChart) {
  const host = htmlNode("div", "c", parent);
  host.style.height = `${height}px`;
  host.chartHeight = height;
  host.drawChart = drawChart;
  figureObserver.observe(host);
  drawFigure(host, true);
  return host;
}

function resetFigures() {
  figureObserver.disconnect();
}

function openChart(host, width, height, label) {
  return svgNode("svg", { class: "fc", width, height, role: "img", "aria-label": label }, host);
}

function markText(parent, x, y, text, attributes = {}) {
  const node = svgNode("text", { x, y, ...attributes }, parent);
  node.textContent = text;
  return node;
}

/* --- 눈금 */

function chartScale(min, max, count = 4) {
  const raw = (max - min || 1) / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const ratio = raw / power;
  const step = (ratio <= 1 ? 1 : ratio <= 2 ? 2 : ratio <= 2.5 ? 2.5 : ratio <= 5 ? 5 : 10) * power;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let tick = lo; tick <= hi + step / 2; tick += step) ticks.push(tick);
  return { lo, hi: hi === lo ? lo + step : hi, ticks };
}

function barUpPath(x, y, width, height, radius = 4) {
  if (height <= 0) return "";
  const corner = Math.min(radius, height, width / 2);
  return `M${x} ${y + height}V${y + corner}Q${x} ${y} ${x + corner} ${y}H${x + width - corner}Q${x + width} ${y} ${x + width} ${y + corner}V${y + height}Z`;
}

function barDownPath(x, y, width, height, radius = 4) {
  if (height <= 0) return "";
  const corner = Math.min(radius, height, width / 2);
  return `M${x} ${y}V${y + height - corner}Q${x} ${y + height} ${x + corner} ${y + height}H${x + width - corner}Q${x + width} ${y + height} ${x + width} ${y + height - corner}V${y}Z`;
}

function yAxisMarks(svg, width, yOf, ticks, left, right, baseline = 0, absolute = false) {
  for (const tick of ticks) {
    svgNode("line", { class: tick === baseline ? "axis" : "grid", x1: left, x2: width - right, y1: yOf(tick), y2: yOf(tick) }, svg);
    markText(svg, left - 8, yOf(tick), tick ? compactWon(absolute ? Math.abs(tick) : tick) : "0", { dy: ".32em", "text-anchor": "end" });
  }
}

/* 마지막 칸부터 세어 일정한 간격으로 적는다. 일간처럼 칸이 많으면 더 걸러 글자가 겹치지 않게 한다. */
function showsTick(band, index, count, on) {
  if (band >= THIN_BAND || on) return true;
  return (count - 1 - index) % Math.ceil(THIN_BAND / band) === 0;
}

/* 칸 하나. 누르면 pick이 불린다. */
function bandMark(svg, { on, mark, pick, title, rows }) {
  const group = svgNode("g", { class: `band${on ? " on" : ""}${pick ? "" : " is-static"}` }, svg);
  const text = `${title}, ${rows.map((row) => `${row.name} ${row.value}`).join(", ")}`;
  if (pick) {
    group.setAttribute("tabindex", 0);
    group.setAttribute("role", "button");
    group.setAttribute("aria-pressed", on ? "true" : "false");
    group.setAttribute("aria-label", text);
    if (mark) group.setAttribute("data-mark", mark);
    onActivate(group, pick);
  }
  setTip(group, title, rows);
  return group;
}

function referenceLine(svg, left, right, y, text) {
  svgNode("line", { class: "ref", x1: left, x2: right, y1: y, y2: y, "pointer-events": "none" }, svg);
  markText(svg, left + 4, y - 5, text, { class: "ref-t" });
}

/* --- 달마다 */

/* 달마다 막대 하나. 고른 달만 색을 준다. */
function monthColumns(host, width, height, { months, unit = "month", values, selected, average, fill = "var(--accent)", name = "금액", pick }) {
  const svg = openChart(host, width, height, "월별");
  const m = MARK_MARGIN;
  const plot = height - m.t - m.b;
  const band = (width - m.l - m.r) / values.length;
  const barWidth = Math.min(WIDE_BAR, band * MARK_BAR_SHARE);
  const { hi, ticks } = chartScale(0, Math.max(1, ...values), width < COMPACT_WIDTH ? 3 : 4);
  const yOf = (value) => m.t + plot - (Math.max(value, 0) / hi) * plot;
  yAxisMarks(svg, width, yOf, ticks, m.l, m.r);
  values.forEach((value, index) => {
    const month = months[index];
    const on = month === selected;
    const x = m.l + band * index;
    const rows = [{ name, value: won(value) }];
    if (average !== null && average !== undefined) rows.push({ name: "평균과 차이", value: signedWon(value - average) });
    const group = bandMark(svg, { on, mark: `month:${month}`, pick: pick && (() => pick(month)), title: bucketTitle(unit, month), rows });
    svgNode("rect", { class: "hit", x, y: m.t - 4, width: band, height: plot + 4, rx: 4 }, group);
    svgNode("path", { d: barUpPath(x + (band - barWidth) / 2, yOf(value), barWidth, m.t + plot - yOf(value)), fill: on ? fill : "var(--bar-muted)" }, group);
    if (showsTick(band, index, values.length, on)) markText(group, x + band / 2, height - 6, bucketTick(unit, month), { "text-anchor": "middle" });
    if (on) markText(group, x + band / 2, yOf(value) - 7, labelWon(value), { class: "val", "text-anchor": "middle" });
  });
  if (average !== null && average !== undefined) referenceLine(svg, m.l, width - m.r, yOf(average), `평균 ${labelWon(average)}`);
}

/* 누적 막대. 항목을 고르면 그 항목만 색을 두고 바닥으로 내린다. */
function stackColumns(host, width, height, { months, unit = "month", series, selected, focus, average, pick }) {
  const svg = openChart(host, width, height, "월별 카테고리");
  const m = MARK_MARGIN;
  const plot = height - m.t - m.b;
  const band = (width - m.l - m.r) / months.length;
  const barWidth = Math.min(WIDE_BAR, band * MARK_BAR_SHARE);
  const ordered = focus ? [...series.filter((item) => item.key === focus), ...series.filter((item) => item.key !== focus)] : series;
  const totals = months.map((month) => sumOf(series, (item) => Math.max(item.valueOf(month), 0)));
  const { hi, ticks } = chartScale(0, Math.max(1, ...totals), width < COMPACT_WIDTH ? 3 : 4);
  const yOf = (value) => m.t + plot - (value / hi) * plot;
  yAxisMarks(svg, width, yOf, ticks, m.l, m.r);
  months.forEach((month, index) => {
    const on = month === selected;
    const x = m.l + band * index;
    const barX = x + (band - barWidth) / 2;
    const rows = ordered.map((item) => ({ item, value: item.valueOf(month) })).filter((row) => row.value > 0);
    const group = bandMark(svg, {
      on,
      mark: `month:${month}`,
      pick: pick && (() => pick(month)),
      title: bucketTitle(unit, month),
      rows: [...rows.map((row) => ({ fill: slotFill(row.item.slot), name: row.item.name, value: won(row.value) })), { name: "합계", value: won(totals[index]) }],
    });
    svgNode("rect", { class: "hit", x, y: m.t - 4, width: band, height: plot + 4, rx: 4 }, group);
    let top = 0;
    rows.forEach((row, order) => {
      const y = yOf(top + row.value);
      const size = yOf(top) - y;
      const color = focus && row.item.key !== focus ? "var(--bar-muted)" : slotFill(row.item.slot);
      const last = order === rows.length - 1;
      svgNode("path", { fill: color, d: last ? barUpPath(barX, y, barWidth, size) : `M${barX} ${y + 1.5}H${barX + barWidth}V${y + size}H${barX}Z` }, group);
      top += row.value;
    });
    if (showsTick(band, index, months.length, on)) markText(group, x + band / 2, height - 6, bucketTick(unit, month), { "text-anchor": "middle" });
    if (on) markText(group, x + band / 2, yOf(totals[index]) - 7, labelWon(totals[index]), { class: "val", "text-anchor": "middle" });
  });
  if (average !== null && average !== undefined) referenceLine(svg, m.l, width - m.r, yOf(average), `평균 ${labelWon(average)}`);
}

/* 수입과 지출을 나란히. 아래 칸은 같은 달의 수입 - 지출이다. */
function pairColumns(host, width, height, { months, unit = "month", income, spend, selected, pick }) {
  const svg = openChart(host, width, height, "월별 수입과 지출");
  const m = { t: 24, r: 4, b: 22 + PAIR_LOWER, l: 40 };
  const plot = height - m.t - m.b;
  const band = (width - m.l - m.r) / months.length;
  const barWidth = Math.min(14, band * 0.32);
  const { hi, ticks } = chartScale(0, Math.max(1, ...income, ...spend), 3);
  const yOf = (value) => m.t + plot - (Math.max(value, 0) / hi) * plot;
  const differences = income.map((value, index) => value - spend[index]);
  const largest = Math.max(1, ...differences.map(Math.abs));
  const lower = height - 22 - PAIR_LOWER / 2 + 6;
  const scale = (PAIR_LOWER / 2 - 12) / largest;
  yAxisMarks(svg, width, yOf, ticks, m.l, m.r);
  svgNode("line", { class: "axis", x1: m.l, x2: width - m.r, y1: lower, y2: lower }, svg);
  markText(svg, m.l - 8, lower, "0", { dy: ".32em", "text-anchor": "end" });
  months.forEach((month, index) => {
    const on = month === selected;
    const x = m.l + band * index;
    const center = x + band / 2;
    const difference = differences[index];
    const group = bandMark(svg, {
      on,
      mark: `month:${month}`,
      pick: pick && (() => pick(month)),
      title: bucketTitle(unit, month),
      rows: [
        { fill: "var(--slot-1)", name: "수입", value: won(income[index]) },
        { fill: "var(--slot-2)", name: "지출", value: won(spend[index]) },
        { name: "수입 - 지출", value: signedWon(difference) },
      ],
    });
    svgNode("rect", { class: "hit", x, y: m.t - 4, width: band, height: height - m.t - 18, rx: 4 }, group);
    const bars = svgNode("g", { opacity: on || !selected ? 1 : 0.42 }, group);
    svgNode("path", { d: barUpPath(center - barWidth - 1, yOf(income[index]), barWidth, m.t + plot - yOf(income[index])), fill: "var(--slot-1)" }, bars);
    svgNode("path", { d: barUpPath(center + 1, yOf(spend[index]), barWidth, m.t + plot - yOf(spend[index])), fill: "var(--slot-2)" }, bars);
    const size = Math.abs(difference) * scale;
    if (difference >= 0) {
      svgNode("path", { d: barUpPath(center - barWidth / 1.4, lower - size, barWidth * 1.4, size, 3), fill: on ? "var(--accent)" : "var(--bar-muted)" }, group);
    } else {
      svgNode("path", { d: barDownPath(center - barWidth / 1.4, lower, barWidth * 1.4, size, 3), fill: on ? "var(--slot-2)" : "color-mix(in srgb, var(--slot-2) 55%, var(--surface))" }, group);
    }
    if (showsTick(band, index, months.length, on)) markText(group, center, height - 6, bucketTick(unit, month), { "text-anchor": "middle" });
    if (on) {
      markText(group, center, Math.min(yOf(income[index]), yOf(spend[index])) - 7, labelWon(income[index]), { class: "val", "text-anchor": "middle" });
      markText(group, center, difference >= 0 ? lower - size - 5 : lower + size + 14, signedLabel(difference), { class: "val", "text-anchor": "middle" });
    }
  });
}

/* 위로 수입, 아래로 지출. */
function mirrorColumns(host, width, height, { months, unit = "month", up, down, selected, pick }) {
  const svg = openChart(host, width, height, "수입과 지출");
  const m = { t: 22, r: 4, b: 22, l: 44 };
  const plot = height - m.t - m.b;
  const band = (width - m.l - m.r) / months.length;
  const barWidth = Math.min(26, band * 0.56);
  const { hi } = chartScale(0, Math.max(1, ...up, ...down), 3);
  const zero = m.t + plot / 2;
  const scale = plot / 2 / hi;
  for (const tick of [hi, hi / 2, -hi / 2, -hi]) {
    const y = zero - tick * scale;
    svgNode("line", { class: "grid", x1: m.l, x2: width - m.r, y1: y, y2: y }, svg);
    markText(svg, m.l - 8, y, compactWon(Math.abs(tick)), { dy: ".32em", "text-anchor": "end" });
  }
  months.forEach((month, index) => {
    const on = month === selected;
    const x = m.l + band * index;
    const barX = x + (band - barWidth) / 2;
    const upSize = Math.max(up[index], 0) * scale;
    const downSize = Math.max(down[index], 0) * scale;
    const group = bandMark(svg, {
      on,
      mark: `month:${month}`,
      pick: pick && (() => pick(month)),
      title: bucketTitle(unit, month),
      rows: [
        { fill: "var(--slot-1)", name: "수입", value: won(up[index]) },
        { fill: "var(--slot-2)", name: "지출", value: won(down[index]) },
        { name: "수입 - 지출", value: signedWon(up[index] - down[index]) },
      ],
    });
    svgNode("rect", { class: "hit", x, y: m.t - 2, width: band, height: plot + 4, rx: 4 }, group);
    const bars = svgNode("g", { opacity: on ? 1 : 0.5 }, group);
    svgNode("path", { d: barUpPath(barX, zero - upSize - 1, barWidth, upSize), fill: "var(--slot-1)" }, bars);
    svgNode("path", { d: barDownPath(barX, zero + 1, barWidth, downSize), fill: "var(--slot-2)" }, bars);
    if (showsTick(band, index, months.length, on)) markText(group, x + band / 2, height - 6, bucketTick(unit, month), { "text-anchor": "middle" });
    if (on) {
      markText(group, x + band / 2, zero - upSize - 7, labelWon(up[index]), { class: "val", "text-anchor": "middle" });
      markText(group, x + band / 2, zero + downSize + 15, labelWon(down[index]), { class: "val", "text-anchor": "middle" });
    }
  });
  svgNode("line", { class: "axis", x1: m.l, x2: width - m.r, y1: zero, y2: zero, "pointer-events": "none" }, svg);
}

/* 날짜마다 얇은 막대. 가장 큰 날만 색을 준다. labels와 xTicks가 있으면 말풍선과 눈금에 그 말을 쓴다. */
function dayColumns(host, width, height, { values, title, fill = "var(--accent)", labels = null, xTicks = null, name = "일별 지출" }) {
  const svg = openChart(host, width, height, name);
  const m = { t: 18, r: 2, b: 20, l: 36 };
  const plot = height - m.t - m.b;
  const count = values.length;
  const band = (width - m.l - m.r) / count;
  const barWidth = Math.max(2, Math.min(14, band * 0.66));
  const { hi, ticks } = chartScale(0, Math.max(1, ...values), 2);
  const yOf = (value) => m.t + plot - (Math.max(value, 0) / hi) * plot;
  yAxisMarks(svg, width, yOf, ticks, m.l, m.r);
  const peak = values.indexOf(Math.max(...values));
  values.forEach((value, index) => {
    const x = m.l + band * index;
    const group = bandMark(svg, { title: labels ? labels[index] : `${title} ${index + 1}일`, rows: [{ name: "지출", value: won(value) }] });
    svgNode("rect", { class: "hit", x, y: m.t, width: band, height: plot }, group);
    if (value > 0) {
      svgNode("path", { d: barUpPath(x + (band - barWidth) / 2, yOf(value), barWidth, m.t + plot - yOf(value), 2), fill: index === peak ? fill : "var(--bar-muted)" }, group);
    }
    if (index === peak && value > 0) {
      markText(group, Math.min(width - 24, Math.max(m.l + 18, x + band / 2)), yOf(value) - 5, labelWon(value), { class: "val", "text-anchor": "middle" });
    }
    const tick = xTicks ? xTicks[index] : [0, 9, 19, count - 1].includes(index) ? `${index + 1}일` : null;
    if (tick) markText(group, x + band / 2, height - 5, tick, { "text-anchor": "middle" });
  });
}

/* 요일별 막대. 월요일부터 놓고 가장 큰 요일만 색을 준다. */
function weekdayColumns(host, width, height, { values }) {
  const svg = openChart(host, width, height, "요일별 평균 지출");
  const m = { t: 22, r: 2, b: 22, l: 2 };
  const plot = height - m.t - m.b;
  const band = (width - m.l - m.r) / 7;
  const barWidth = Math.min(WIDE_BAR, band * MARK_BAR_SHARE);
  const hi = Math.max(1, ...values);
  const yOf = (value) => m.t + plot - (Math.max(value, 0) / hi) * plot;
  const peak = values.indexOf(hi);
  svgNode("line", { class: "axis", x1: m.l, x2: width - m.r, y1: m.t + plot, y2: m.t + plot }, svg);
  [1, 2, 3, 4, 5, 6, 0].forEach((weekday, index) => {
    const value = values[weekday];
    const x = m.l + band * index;
    const on = weekday === peak;
    const group = bandMark(svg, { on, title: `${WEEKDAYS[weekday]}요일`, rows: [{ name: "평균", value: won(value) }] });
    svgNode("rect", { class: "hit", x, y: m.t - 4, width: band, height: plot + 4, rx: 4 }, group);
    svgNode("path", { d: barUpPath(x + (band - barWidth) / 2, yOf(value), barWidth, m.t + plot - yOf(value)), fill: on ? "var(--accent)" : "var(--bar-muted)" }, group);
    markText(group, x + band / 2, height - 6, WEEKDAYS[weekday], { "text-anchor": "middle" });
    if (on) markText(group, x + band / 2, yOf(value) - 7, labelWon(value), { class: "val", "text-anchor": "middle" });
  });
}

/* --- 선 */

function lineMargins(series, inside) {
  return { t: 22, r: !inside && series.some((item) => item.label) ? 72 : 10, b: 22, l: 44 };
}

/* 날짜 간격대로 놓을 때 쓰는 위치. 기록이 하나면 가운데다. */
function datePositions(days) {
  const first = parseDay(days[0]);
  const last = parseDay(days[days.length - 1]);
  return days.map((day) => (last > first ? (parseDay(day) - first) / (last - first) : 0.5));
}

let washCount = 0; // 그라데이션마다 다른 id가 있어야 차트끼리 섞이지 않는다

/* 선 아래 면. 선 색에서 시작해 바닥으로 갈수록 옅어진다. */
function washFill(svg, color) {
  const id = `wash-${++washCount}`;
  const gradient = svgNode("linearGradient", { id, x1: 0, y1: 0, x2: 0, y2: 1 }, svgNode("defs", {}, svg));
  svgNode("stop", { offset: "0%", "stop-color": color, "stop-opacity": 0.26 }, gradient);
  svgNode("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0.02 }, gradient);
  return `url(#${id})`;
}

/* 기록을 고르는 선 차트. 누르면 가장 가까운 기록을, 화살표 키로 앞뒤 기록을 고른다. */
function pickableLine(svg, { count, at, labels, name, xOf, pick }) {
  svg.classList.add("is-pick");
  svg.dataset.mark = `pick:${name}`;
  svg.setAttribute("tabindex", 0);
  svg.setAttribute("role", "slider");
  svg.setAttribute("aria-label", `${name} 기록 고르기`);
  svg.setAttribute("aria-valuemin", 0);
  svg.setAttribute("aria-valuemax", count - 1);
  svg.setAttribute("aria-valuenow", at);
  svg.setAttribute("aria-valuetext", labels[at]);
  const choose = (index) => {
    if (index >= 0 && index < count && index !== at) pick(index);
  };
  svg.addEventListener("click", (event) => {
    const x = event.clientX - svg.getBoundingClientRect().left;
    let best = 0;
    for (let index = 1; index < count; index += 1) {
      if (Math.abs(xOf(index) - x) < Math.abs(xOf(best) - x)) best = index;
    }
    choose(best);
  });
  svg.addEventListener("keydown", (event) => {
    const target = { ArrowLeft: at - 1, ArrowRight: at + 1, Home: 0, End: count - 1 }[event.key];
    if (target === undefined) return;
    event.preventDefault();
    choose(target);
  });
}

/* 끝값은 직접 적고 나머지는 말풍선으로 본다. selected는 고른 기록이고, 마지막이 아니면 점선으로 표시한다. */
function lineChart(host, width, height, { series, labels, pos, name = "차트", dots = false, zero = false, tickCount = 3, xLabels = null, between = null, inside = false, selected = null, pick = null }) {
  const svg = openChart(host, width, height, name);
  const m = lineMargins(series, inside);
  const plot = height - m.t - m.b;
  const all = series.flatMap((item) => item.values.filter((value) => value !== null && value !== undefined));
  if (!all.length) return;
  const low = zero ? Math.min(0, ...all) : Math.min(...all);
  const high = Math.max(...all);
  const pad = zero ? 0 : (high - low || Math.abs(high) * 0.02 || 1) * 0.08;
  const { lo, hi, ticks } = chartScale(low - pad, high + pad, tickCount);
  const yOf = (value) => m.t + plot - ((value - lo) / (hi - lo)) * plot;
  const count = labels.length;
  const xOf = (index) => m.l + (width - m.l - m.r) * (pos ? pos[index] : count > 1 ? index / (count - 1) : 0.5);
  for (const tick of ticks) {
    svgNode("line", { class: tick === 0 || tick === lo ? "axis" : "grid", x1: m.l, x2: width - m.r, y1: yOf(tick), y2: yOf(tick) }, svg);
    markText(svg, m.l - 8, yOf(tick), compactWon(tick), { dy: ".32em", "text-anchor": "end" });
  }
  if (between) {
    const [upper, lower, fill] = between;
    const pairs = upper.map((value, index) => [index, value, lower[index]]).filter(([, a, b]) => a !== null && b !== null);
    const top = pairs.map(([index, a]) => `${xOf(index)},${yOf(a)}`).join(" ");
    const bottom = pairs.map(([index, , b]) => `${xOf(index)},${yOf(b)}`).reverse().join(" ");
    if (pairs.length > 1) svgNode("polygon", { points: `${top} ${bottom}`, fill, opacity: 0.16 }, svg);
  }
  for (const item of series) {
    const points = item.values.map((value, index) => (value === null || value === undefined ? null : [xOf(index), yOf(value)])).filter(Boolean);
    if (!points.length) continue;
    const line = points.map((point) => point.join(",")).join(" ");
    if (item.wash && points.length > 1) {
      svgNode("polygon", { points: `${points[0][0]},${m.t + plot} ${line} ${points[points.length - 1][0]},${m.t + plot}`, fill: washFill(svg, item.color) }, svg);
    }
    const polyline = svgNode("polyline", { class: "ln", points: line, stroke: item.color, "stroke-width": item.width || 2 }, svg);
    if (item.dash) polyline.setAttribute("stroke-dasharray", "5 4");
    if (dots) {
      for (const point of points) svgNode("circle", { cx: point[0], cy: point[1], r: item.dotR || 3, fill: item.color, stroke: "var(--surface)", "stroke-width": 2 }, svg);
    }
    if (item.label) {
      const end = points[points.length - 1];
      svgNode("circle", { cx: end[0], cy: end[1], r: 4, fill: item.color, stroke: "var(--surface)", "stroke-width": 2 }, svg);
      if (inside) markText(svg, Math.min(end[0], width - m.r), end[1] - 9, item.label, { class: "val", "text-anchor": "end" });
    }
  }
  if (!inside) {
    const ends = series
      .filter((item) => item.label)
      .map((item) => {
        const index = item.values.map((value, at) => (value === null || value === undefined ? -1 : at)).filter((at) => at >= 0).pop();
        return index === undefined ? null : { label: item.label, x: xOf(index), y: yOf(item.values[index]) };
      })
      .filter(Boolean)
      .sort((a, b) => a.y - b.y);
    ends.forEach((end, index) => {
      if (index && end.y - ends[index - 1].y < 15) end.y = ends[index - 1].y + 15;
    });
    for (const end of ends) markText(svg, end.x + 9, end.y, end.label, { class: "val", dy: ".32em" });
  }
  if (selected !== null && selected < count - 1) {
    svgNode("line", { class: "pick-line", x1: xOf(selected), x2: xOf(selected), y1: m.t, y2: m.t + plot }, svg);
    for (const item of series) {
      const value = item.values[selected];
      if (value !== null && value !== undefined) svgNode("circle", { cx: xOf(selected), cy: yOf(value), r: 5, fill: item.color, stroke: "var(--surface)", "stroke-width": 2 }, svg);
    }
  }
  if (pick && count > 1) pickableLine(svg, { count, at: selected ?? count - 1, labels, name, xOf, pick });
  const axisLabels = xLabels || (count > 1 ? [[0, labels[0]], [count - 1, labels[count - 1]]] : [[0, labels[0]]]);
  axisLabels.forEach(([index, text], order) => {
    const single = axisLabels.length === 1;
    markText(svg, xOf(index), height - 5, text, { "text-anchor": single ? "middle" : order === 0 ? "start" : order === axisLabels.length - 1 ? "end" : "middle" });
  });
  svgNode("line", { class: "cross", x1: 0, x2: 0, y1: m.t, y2: m.t + plot, opacity: 0 }, svg);
  host.points = labels.map((label, index) => ({
    x: xOf(index),
    tip: {
      title: label,
      rows: series
        .filter((item) => !item.hideTip && item.values[index] !== null && item.values[index] !== undefined)
        .map((item) => ({ fill: item.color, name: item.name, value: won(item.values[index]) })),
    },
  }));
}

/* --- 비중 */

function cellText(group, className, x, y, room, text, color) {
  const node = markText(group, x, y, text, { class: className });
  node.style.fill = color;
  if (node.getComputedTextLength() <= room) return true;
  node.remove();
  return false;
}

/* 칸을 고르면 나머지는 흐려진다. */
function cellMark(svg, cell, { focus, pick, fill, ink, name, value, title, rows, radius = 5 }) {
  const dim = focus && focus !== cell.item.key;
  const group = svgNode("g", { class: dim ? "dim" : "" }, svg);
  if (pick) {
    group.setAttribute("tabindex", 0);
    group.setAttribute("role", "button");
    group.setAttribute("aria-label", `${title}, ${value}`);
    group.setAttribute("aria-pressed", focus === cell.item.key ? "true" : "false");
    group.setAttribute("data-mark", `focus:${cell.item.key}`);
    onActivate(group, () => pick(cell.item.key));
  }
  setTip(group, title, rows);
  svgNode("rect", { class: "cell", x: cell.x, y: cell.y, width: Math.max(cell.width, 0), height: Math.max(cell.height, 0), rx: radius, fill }, group);
  const room = cell.width - 16;
  const x = cell.x + 9;
  if (cell.height >= 24 && cellText(group, "cell-t", x, cell.y + 19, room, name, ink) && cell.height >= 44) {
    cellText(group, "cell-v", x, cell.y + 35, room, value, ink);
  }
  return group;
}

function treemapChart(host, width, height, { items, focus, total, pick }) {
  const svg = openChart(host, width, height, "카테고리 비중");
  for (const cell of squarify(items, 0, 0, width, height)) {
    const { item } = cell;
    cellMark(svg, cell, {
      focus,
      pick,
      fill: solidFill(item.slot),
      ink: slotInk(item.slot),
      name: item.name,
      value: labelWon(item.value),
      title: item.name,
      rows: [
        { fill: solidFill(item.slot), name: "금액", value: won(item.value) },
        { name: "비중", value: percentText(item.value / total) },
      ],
    });
  }
}

/* 묶음이 있는 트리맵. 금융 상품을 항목별로 나눈다. */
function groupedTreemapChart(host, width, height, { groups, focus, total, pick }) {
  const svg = openChart(host, width, height, "상품 비중");
  const head = 22;
  const outer = groups
    .map((group) => ({ ...group, value: sumOf(group.items, (item) => item.value) }))
    .filter((group) => group.value > 0)
    .sort((a, b) => b.value - a.value);
  for (const cell of squarify(outer, 0, 0, width, height)) {
    const group = cell.item;
    const fill = solidFill(group.slot);
    svgNode("rect", { x: cell.x + 1, y: cell.y + 1, width: Math.max(0, cell.width - 2), height: Math.max(0, cell.height - 2), rx: 6, fill, "fill-opacity": 0.16 }, svg);
    const title = markText(svg, cell.x + 8, cell.y + 16, `${group.name} ${labelWon(group.value)}`, { class: "group-t" });
    let room = cell.height > 60 ? head : 2;
    if (title.getComputedTextLength() + 14 > cell.width) {
      title.textContent = group.short;
      if (title.getComputedTextLength() + 14 > cell.width) room = 2;
    }
    if (room !== head) title.remove();
    const items = group.items.filter((item) => item.value > 0).sort((a, b) => b.value - a.value);
    for (const inner of squarify(items, cell.x + 2, cell.y + room, Math.max(1, cell.width - 4), Math.max(1, cell.height - room - 2))) {
      const item = inner.item;
      cellMark(svg, inner, {
        focus,
        pick,
        radius: 4,
        fill,
        ink: slotInk(group.slot),
        name: item.name,
        value: labelWon(item.value),
        title: item.name,
        rows: [
          { fill, name: group.name, value: won(item.value) },
          { name: "금융 자산의", value: percentText(item.value / total) },
        ],
      });
    }
  }
}

/* 돈의 흐름. 왼쪽은 무채색, 오른쪽은 항목 색이다. */
function sankeyChart(host, width, height, { sankey }) {
  const svg = openChart(host, width, height, "돈의 흐름");
  const margin = 22;
  const layout = sankeyLayout(sankey, width, height - margin, { pad: 8, nodeWidth: 8 });
  const canvas = svgNode("g", { transform: `translate(0 ${margin / 2 + 4})` }, svg);
  const links = svgNode("g", {}, canvas);
  const nodes = new Map(layout.nodes.map((node) => [node.key, node]));
  for (const link of layout.links) {
    const path = svgNode("path", { class: `sankey-link series-${link.slot}`, d: link.path, "stroke-width": link.strokeWidth }, links);
    setTip(path, `${nodes.get(link.source).name} → ${nodes.get(link.target).name}`, [{ name: "금액", value: won(link.value) }]);
  }
  for (const node of layout.nodes) {
    const group = svgNode("g", { class: `sankey-node series-${node.slot}${node.key === "center" ? " is-center" : ""}` }, canvas);
    svgNode("rect", { x: node.x0, y: node.y0, width: node.x1 - node.x0, height: Math.max(node.y1 - node.y0, 1), rx: 2 }, group);
    const middle = (node.y0 + node.y1) / 2;
    let attributes;
    if (node.column === 0) attributes = { dy: "0.32em", "text-anchor": "start" };
    else if (node.column === layout.lastColumn) attributes = { dy: "0.32em", "text-anchor": "end" };
    else attributes = { "text-anchor": "middle" };
    const x = node.column === 0 ? node.x1 + 6 : node.column === layout.lastColumn ? node.x0 - 6 : (node.x0 + node.x1) / 2;
    const y = node.column === 0 || node.column === layout.lastColumn ? middle : node.y0 - 6;
    markText(group, x, y, `${node.name} ${labelWon(node.value)}`, { class: "sankey-label", ...attributes });
  }
}

/* --- 자산 */

/* 항목별 증감을 차례로 쌓는다. 기준은 기간의 첫 기록이고 0에서 시작한다. */
function waterfallChart(host, width, height, { steps, totalLabel }) {
  const svg = openChart(host, width, height, "순자산 변화");
  const m = { t: 24, r: 4, b: 38, l: 44 };
  const plot = height - m.t - m.b;
  const band = (width - m.l - m.r) / (steps.length + 1);
  const barWidth = Math.min(30, band * 0.6);
  let running = 0;
  const placed = steps.map((step) => {
    const from = running;
    running += step.value;
    return { ...step, from, to: running };
  });
  const edges = [0, ...placed.flatMap((step) => [step.from, step.to])];
  const { lo, hi, ticks } = chartScale(Math.min(...edges), Math.max(...edges), 3);
  const yOf = (value) => m.t + plot - ((value - lo) / (hi - lo)) * plot;
  for (const tick of ticks) {
    svgNode("line", { class: tick === 0 ? "axis" : "grid", x1: m.l, x2: width - m.r, y1: yOf(tick), y2: yOf(tick) }, svg);
    markText(svg, m.l - 8, yOf(tick), tick ? compactWon(tick) : "0", { dy: ".32em", "text-anchor": "end" });
  }
  placed.forEach((step, index) => {
    const x = m.l + band * index;
    const top = yOf(Math.max(step.from, step.to));
    const size = Math.max(1.5, Math.abs(yOf(step.from) - yOf(step.to)));
    const up = step.value >= 0;
    const group = bandMark(svg, { title: step.name, rows: [{ name: "변화", value: signedWon(step.value) }] });
    svgNode("rect", { class: "hit", x, y: m.t, width: band, height: plot, rx: 4 }, group);
    svgNode("rect", { x: x + (band - barWidth) / 2, y: top, width: barWidth, height: size, rx: 3, fill: up ? "var(--slot-1)" : "var(--slot-2)" }, group);
    if (index > 0) svgNode("line", { class: "axis", x1: x - (band - barWidth) / 2, x2: x + (band - barWidth) / 2, y1: yOf(step.from), y2: yOf(step.from) }, group);
    markText(group, x + band / 2, up ? top - 5 : top + size + 13, signedLabel(step.value), { class: "val small", "text-anchor": "middle" });
    markText(group, x + band / 2, height - 22, step.short || step.name, { "text-anchor": "middle" });
  });
  const x = m.l + band * placed.length;
  const top = yOf(Math.max(0, running));
  svgNode("line", { class: "axis", x1: x - (band - barWidth) / 2, x2: x + (band - barWidth) / 2, y1: yOf(running), y2: yOf(running) }, svg);
  svgNode("rect", { x: x + (band - barWidth) / 2, y: top, width: barWidth, height: Math.max(1.5, Math.abs(yOf(0) - yOf(running))), rx: 3, fill: "var(--accent)" }, svg);
  markText(svg, x + band / 2, top - 6, signedLabel(running), { class: "val", "text-anchor": "middle" });
  markText(svg, x + band / 2, height - 22, totalLabel, { class: "strong", "text-anchor": "middle" });
}

/* 자산은 위로 쌓고 부채는 아래로 내린다. 굵은 선이 순자산이다. */
function balanceAreaChart(host, width, height, { days, groups, debts, net, selected, focus, pick, pickRecord = null }) {
  const svg = openChart(host, width, height, "자산과 부채 추이");
  const m = { t: 24, r: 10, b: 22, l: 48 };
  const plot = height - m.t - m.b;
  const positions = datePositions(days);
  const xOf = (index) => m.l + (width - m.l - m.r) * positions[index];
  const sumAt = (rows, index) => sumOf(rows, (row) => row.values[index]);
  const topMax = Math.max(...days.map((_, index) => sumAt(groups, index)));
  const bottomMax = Math.max(...days.map((_, index) => sumAt(debts, index)));
  const { lo, hi, ticks } = chartScale(-bottomMax, topMax, 3);
  const yOf = (value) => m.t + plot - ((value - lo) / (hi - lo)) * plot;
  for (const tick of ticks) {
    svgNode("line", { class: tick === 0 ? "axis" : "grid", x1: m.l, x2: width - m.r, y1: yOf(tick), y2: yOf(tick) }, svg);
    markText(svg, m.l - 8, yOf(tick), tick ? compactWon(Math.abs(tick)) : "0", { dy: ".32em", "text-anchor": "end" });
  }
  const layers = (rows, sign, fillOf) => {
    let below = days.map(() => 0);
    rows.forEach((row, order) => {
      const above = below.map((value, index) => value + row.values[index]);
      const top = above.map((value, index) => `${xOf(index)},${yOf(sign * value)}`).join(" ");
      const bottom = below.map((value, index) => `${xOf(index)},${yOf(sign * value)}`).reverse().join(" ");
      const dim = focus && focus !== row.key;
      const polygon = svgNode("polygon", { points: `${top} ${bottom}`, fill: fillOf(row, order), "fill-opacity": dim ? 0.16 : 0.74, stroke: "var(--surface)", "stroke-width": 1.5 }, svg);
      setTip(polygon, row.name, [{ name: "금액", value: won(row.values[selected]) }]);
      if (pick) {
        polygon.classList.add("is-pick");
        polygon.addEventListener("click", () => pick(row.key));
      }
      below = above;
    });
  };
  layers(groups, 1, (row) => solidFill(row.slot));
  layers(debts, -1, (_, order) => debtTint(order));
  const points = net.map((value, index) => `${xOf(index)},${yOf(value)}`).join(" ");
  svgNode("polyline", { class: "ln", points, stroke: "var(--surface)", "stroke-width": 5 }, svg);
  svgNode("polyline", { class: "ln", points, stroke: "var(--text)", "stroke-width": 2 }, svg);
  net.forEach((value, index) => {
    const on = index === selected;
    svgNode("circle", { cx: xOf(index), cy: yOf(value), r: on ? 4.5 : 2.5, fill: "var(--text)", stroke: "var(--surface)", "stroke-width": on ? 2 : 1 }, svg);
  });
  // 순자산 점을 누르면 그 기록을 고른다. 면을 누르면 그 항목을 강조한다.
  if (pickRecord) {
    net.forEach((value, index) => {
      const hit = svgNode("circle", { class: "dot-hit", cx: xOf(index), cy: yOf(value), r: 10 }, svg);
      hit.setAttribute("tabindex", 0);
      hit.setAttribute("role", "button");
      hit.setAttribute("aria-label", `${dotDate(days[index])} 기록 보기`);
      hit.setAttribute("aria-pressed", index === selected ? "true" : "false");
      hit.dataset.mark = `record:${days[index]}`;
      setTip(hit, dotDate(days[index]), [{ name: "순자산", value: won(value) }]);
      onActivate(hit, () => pickRecord(index));
    });
  }
  const last = net.length - 1;
  markText(svg, xOf(last), yOf(net[last]) - 10, `순자산 ${labelWon(net[last])}`, { class: "val", "text-anchor": "end" });
  markText(svg, xOf(0), height - 5, dotDate(days[0]));
  if (last > 0) markText(svg, xOf(last), height - 5, dotDate(days[last]), { "text-anchor": "end" });
}

function debtTint(order) {
  return `color-mix(in srgb, var(--text) ${Math.max(24, 54 - order * 20)}%, var(--surface))`;
}

function debtInk(order) {
  return order ? "var(--text)" : "var(--surface)";
}

/* --- 작은 그림: 목록 안에 들어간다 */

function sparkBars(parent, values, { width = 96, height = 20, fill = "var(--accent)", selected = values.length - 1 } = {}) {
  const svg = svgNode("svg", { class: "spark", width, height, "aria-hidden": "true" }, parent);
  const hi = Math.max(1, ...values);
  const band = width / values.length;
  const barWidth = Math.max(1.5, band - 1.5);
  values.forEach((value, index) => {
    const size = Math.max(1, (Math.max(value, 0) / hi) * (height - 1));
    svgNode("rect", { x: index * band, y: height - size, width: barWidth, height: size, rx: 1, fill: index === selected ? fill : "var(--bar-muted)" }, svg);
  });
  return svg;
}

function sparkLine(parent, values, { width = 96, height = 20, stroke = "var(--accent)" } = {}) {
  const svg = svgNode("svg", { class: "spark", width, height, "aria-hidden": "true" }, parent);
  if (!values.length) return svg;
  const lo = Math.min(...values);
  const range = Math.max(...values) - lo || 1;
  const points = values.map((value, index) => [
    values.length > 1 ? 2 + ((width - 6) * index) / (values.length - 1) : width / 2,
    height - 3 - ((value - lo) / range) * (height - 6),
  ]);
  svgNode("polyline", { fill: "none", stroke, "stroke-width": 1.75, "stroke-linejoin": "round", "stroke-linecap": "round", points: points.map((point) => point.join(",")).join(" ") }, svg);
  const end = points[points.length - 1];
  svgNode("circle", { cx: end[0], cy: end[1], r: 2.5, fill: stroke }, svg);
  return svg;
}

/* --- 금융 */

/* 원금에서 평가금액까지. 번 상품은 쪽빛, 잃은 상품은 주황이다. */
function dumbbellChart(host, width, height, { items, selected, pick }) {
  const svg = openChart(host, width, height, "투자 원금과 평가금액");
  const m = { t: 22, r: 66, b: 6, l: 120 };
  const rowHeight = (height - m.t - m.b) / items.length;
  const high = Math.max(1, ...items.flatMap((item) => [item.principal, item.valuation]));
  const { hi, ticks } = chartScale(0, high, 3);
  const xOf = (value) => m.l + ((width - m.l - m.r) * value) / hi;
  for (const tick of ticks) {
    svgNode("line", { class: tick ? "grid" : "axis", x1: xOf(tick), x2: xOf(tick), y1: m.t, y2: height - m.b }, svg);
    markText(svg, xOf(tick), m.t - 8, tick ? compactWon(tick) : "0", { "text-anchor": "middle" });
  }
  items.forEach((item, index) => {
    const y = m.t + rowHeight * index + rowHeight / 2;
    const gain = item.valuation >= item.principal;
    const color = gain ? "var(--accent)" : "var(--slot-2)";
    const rate = item.principal ? ((item.valuation - item.principal) / item.principal) * 100 : 0;
    const group = bandMark(svg, {
      on: selected === item.key,
      mark: `focus:${item.key}`,
      pick: pick && (() => pick(item.key)),
      title: item.name,
      rows: [
        { name: "원금", value: won(item.principal) },
        { name: "평가금액", value: won(item.valuation) },
        { name: "손익", value: signedWon(item.valuation - item.principal) },
      ],
    });
    svgNode("rect", { class: "hit", x: 0, y: y - rowHeight / 2, width, height: rowHeight, rx: 6 }, group);
    const label = markText(group, m.l - 10, y, item.name, { class: "strong plain", dy: ".32em", "text-anchor": "end" });
    while (label.getComputedTextLength() > m.l - 14 && label.textContent.length > 2) label.textContent = `${label.textContent.slice(0, -2)}…`;
    svgNode("line", { x1: xOf(item.principal), x2: xOf(item.valuation), y1: y, y2: y, stroke: color, "stroke-width": 2 }, group);
    svgNode("circle", { cx: xOf(item.principal), cy: y, r: 4.5, fill: "var(--surface)", stroke: "var(--text-2)", "stroke-width": 2 }, group);
    svgNode("circle", { cx: xOf(item.valuation), cy: y, r: 5, fill: color, stroke: "var(--surface)", "stroke-width": 2 }, group);
    markText(group, width - 2, y, `${rate > 0 ? "+" : ""}${rate.toFixed(1)}%`, { class: "strong", dy: ".32em", "text-anchor": "end" });
  });
}

/* 앞으로 오는 만기를 한 줄에 놓는다. */
function maturityChart(host, width, height, { items, from, to }) {
  const svg = openChart(host, width, height, "다가오는 만기");
  const m = { l: 6, r: 6 };
  const first = parseDay(from);
  const last = parseDay(to);
  const xOf = (day) => m.l + (width - m.l - m.r) * ((parseDay(day) - first) / (last - first));
  const axis = height - 24;
  svgNode("line", { class: "axis", x1: m.l, x2: width - m.r, y1: axis, y2: axis }, svg);
  for (let year = Number(from.slice(0, 4)) + 1; year <= Number(to.slice(0, 4)); year += 1) {
    const x = xOf(`${year}-01-01`);
    svgNode("line", { class: "axis", x1: x, x2: x, y1: axis - 4, y2: axis + 4 }, svg);
    markText(svg, x, height - 6, String(year), { "text-anchor": "middle" });
  }
  items.forEach((item, index) => {
    const x = xOf(item.end);
    const top = 14 + (index % 3) * 36;
    const anchor = x > width * 0.72 ? "end" : "start";
    const textX = x + (anchor === "end" ? -6 : 6);
    const fill = slotFill(item.slot);
    const value = item.value === null ? "-" : labelWon(item.value);
    const group = svgNode("g", {}, svg);
    setTip(group, item.name, [{ name: "만기", value: dotDate(item.end) }, { name: item.kind, value }]);
    svgNode("line", { x1: x, x2: x, y1: top + 4, y2: axis, stroke: fill, "stroke-width": 1.5 }, group);
    svgNode("circle", { cx: x, cy: axis, r: 4.5, fill, stroke: "var(--surface)", "stroke-width": 2 }, group);
    markText(group, textX, top, item.name, { class: "lab strong-name", "text-anchor": anchor });
    markText(group, textX, top + 15, `${item.end.slice(0, 7).replace("-", ".")} · ${value}`, { class: "lab sub", "text-anchor": anchor });
  });
}

function timelineHeight(rows) {
  return GANTT_HEAD + sumOf(rows, (row) => (row.group ? GANTT_GROUP : GANTT_ROW)) + 6;
}

/* 금융 타임라인. 지난 기간은 진하게, 남은 기간은 옅게 그린다. */
function timelineChart(host, width, height, { rows, from, to, today }) {
  const svg = openChart(host, width, height, "금융 타임라인");
  const narrow = width < 560;
  const labelWidth = narrow ? 104 : 168;
  const valueWidth = narrow ? 0 : 84;
  const first = parseDay(from);
  const last = parseDay(to);
  const usable = width - labelWidth - valueWidth - 8;
  const xOf = (day) => labelWidth + usable * Math.min(1, Math.max(0, (parseDay(day) - first) / (last - first)));
  const firstYear = Number(from.slice(0, 4));
  const lastYear = Number(to.slice(0, 4));
  const perYear = usable / (lastYear - firstYear + 1);
  const skip = Math.max(1, Math.ceil(36 / perYear));
  const todayX = xOf(today);
  for (let year = firstYear; year <= lastYear; year += 1) {
    if ((year - firstYear) % skip) continue;
    const x = xOf(`${year}-01-01`);
    svgNode("line", { class: "grid", x1: x, x2: x, y1: GANTT_HEAD - 4, y2: height }, svg);
    if (!(x > todayX - 44 && x < todayX + 14)) markText(svg, x + 3, GANTT_HEAD - 10, String(year)); // 오늘 글자와 겹치면 적지 않는다
  }
  let y = GANTT_HEAD;
  for (const row of rows) {
    if (row.group) {
      markText(svg, 0, y + 16, row.group, { class: "group-t muted-t" });
      svgNode("line", { class: "grid", x1: 0, x2: width, y1: y + 22, y2: y + 22 }, svg);
      y += GANTT_GROUP;
      continue;
    }
    const center = y + GANTT_ROW / 2;
    const start = xOf(row.start);
    const end = row.end ? xOf(row.end) : todayX;
    const fill = slotFill(row.slot);
    const tipRows = [{ name: row.startName, value: dotDate(row.start) }, { name: "만기", value: row.end ? dotDate(row.end) : "없음" }];
    if (row.extra) tipRows.push(row.extra);
    const group = bandMark(svg, { title: row.name, rows: tipRows });
    svgNode("rect", { class: "hit", x: 0, y, width, height: GANTT_ROW, rx: 6 }, group);
    const label = markText(group, 0, center, row.name, { class: "strong plain", dy: ".32em" });
    while (label.getComputedTextLength() > labelWidth - 8 && label.textContent.length > 2) label.textContent = `${label.textContent.slice(0, -2)}…`;
    const past = Math.min(todayX, end);
    svgNode("rect", { x: start, y: center - 5, width: Math.max(2, past - start), height: 10, rx: 3, fill }, group);
    if (row.end && end > todayX) svgNode("rect", { x: todayX, y: center - 5, width: end - todayX, height: 10, rx: 3, fill, "fill-opacity": 0.26 }, group);
    if (row.end) svgNode("circle", { cx: end, cy: center, r: 4.5, fill: "var(--surface)", stroke: fill, "stroke-width": 2 }, group);
    if (row.end && parseDay(row.end) > last) markText(group, end - 8, center - 9, `${row.end.slice(0, 4)}년 →`, { "text-anchor": "end" });
    if (!row.end) markText(group, end + 8, center, "만기 없음", { dy: ".32em" });
    if (!narrow && row.value !== null) markText(group, width, center, labelWon(row.value), { class: "strong", dy: ".32em", "text-anchor": "end" });
    y += GANTT_ROW;
  }
  svgNode("line", { x1: todayX, x2: todayX, y1: GANTT_HEAD - 4, y2: height, stroke: "var(--text)", "stroke-width": 1.5 }, svg);
  markText(svg, todayX, GANTT_HEAD - 10, "오늘", { class: "val small", "text-anchor": "middle" });
}
