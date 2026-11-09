/* 차트 화면 그리기. 계산은 chart-data.js에 있다.
   결제 수단 이름처럼 외부에서 온 글자는 모두 textContent로 넣는다. */
"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const AXIS_WIDTH = 52;
const PLOT_RIGHT = 8;
const PLOT_TOP = 22; // 맨 위 막대에도 금액을 적을 자리
const LABEL_BAND = 42;
const MAX_BAR_WIDTH = 24;
const MIN_BAR_WIDTH = 2;
const BAR_SHARE = 0.62; // 막대가 칸에서 차지하는 비율. 나머지는 여백이다
const BAR_GAP = 2;
const BAR_RADIUS = 4;
const MIN_BAR_HEIGHT = 2;
const MIN_LABEL_WIDTH = { day: 40, week: 40, month: 30, year: 40 };
const VALUE_BAND = 28; // 칸이 이보다 좁으면 막대마다 금액을 적을 자리가 없다
const BOTH_LABELS_BAND = 44; // 칸이 이보다 좁으면 나란한 막대 둘에 모두 적을 자리가 없다
const DOT_BAND = 20; // 칸이 이보다 좁으면 기록마다 점을 찍지 않는다
const DIRECT_LABELS = 3; // 선이 이보다 많으면 끝값을 적지 않고 범례에 맡긴다
const DONUT_MARGIN = 6;
const DONUT_HOLE = 0.68;
const CELL_PADDING = 9;
const CELL_NAME_HEIGHT = 24;
const CELL_VALUE_HEIGHT = 44;
const SANKEY_MARGIN_Y = 22; // 가운데 묶음 이름이 들어갈 자리
const SANKEY_NODE_WIDTH = 8;

function barPath(left, width, from, to) {
  const size = Math.abs(to - from);
  const radius = Math.min(BAR_RADIUS, width / 2, size);
  const right = left + width;
  const toward = to < from ? radius : -radius; // 값 쪽 모서리만 둥글다
  return (
    `M${left} ${from}V${to + toward}Q${left} ${to} ${left + radius} ${to}` +
    `H${right - radius}Q${right} ${to} ${right} ${to + toward}V${from}Z`
  );
}

function svgNode(name, attributes, parent) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (parent) parent.appendChild(node);
  return node;
}

function htmlNode(name, className, parent, text) {
  const node = document.createElement(name);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  if (parent) parent.appendChild(node);
  return node;
}

function onActivate(node, action) {
  node.addEventListener("click", action);
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      action();
    }
  });
}

/* --- 추이 */

/* 한 가지를 고르면 그것만 색을 두고 나머지는 회색으로 물러난다. */
function seriesClass(view, item) {
  return `series-${item.slot}${isFocusedSeries(view.state, item) ? "" : " muted"}`;
}

/* 선은 고른 것을 맨 위에 그린다. 고르지 않았으면 순서가 그대로다. */
function focusLast(view, items) {
  return [...items].sort((a, b) => isFocusedSeries(view.state, a) - isFocusedSeries(view.state, b));
}

/* 막대는 고른 것을 맨 아래에 쌓아 크기를 바로 읽게 한다. */
function focusFirst(view, items) {
  return [...items].sort((a, b) => isFocusedSeries(view.state, b) - isFocusedSeries(view.state, a));
}

/* 값을 차트에 직접 적는 칸. 고른 칸이 있으면 그 칸이고, 없으면 기록이 있는 마지막 칸이다. */
function markedKey(view) {
  const { model, period } = view;
  if (period.selected) return period.selected;
  return [...model.rows].reverse().find((row) => model.series.some((item) => row.values[item.key]))?.key;
}

/* 나란히 놓는 막대의 폭과, 칸 가운데에서 떨어진 거리. */
function sideBySide(band, count) {
  const width = Math.max(MIN_BAR_WIDTH, Math.min(MAX_BAR_WIDTH, (band * BAR_SHARE - BAR_GAP * (count - 1)) / count));
  const total = width * count + BAR_GAP * (count - 1);
  return { width, offset: (order) => order * (width + BAR_GAP) - total / 2 };
}

/* 막대에 직접 적는 금액. 모든 막대에 적지 않고 지금 보는 칸과, 적자처럼 눈여겨볼 칸에만 적는다. */
function barLabels(view, geometry, row, middle) {
  const { model, state } = view;
  const { yOf, marked, band } = geometry;
  const mark = model.form.mark;
  const isMarked = row.key === marked;
  if (mark === "stack") {
    if (!isMarked) return [];
    const shown = model.series.filter((item) => row.values[item.key] > 0);
    const total = sumOf(shown, (item) => row.values[item.key]);
    const value = sumOf(shown.filter((item) => isFocusedSeries(state, item)), (item) => row.values[item.key]);
    return [{ x: middle, y: yOf(total) - 7, value }];
  }
  if (mark === "diverging") {
    const value = row.values[model.series[0].key];
    if (!value || (!isMarked && !(value < 0 && band >= VALUE_BAND))) return [];
    return [{ x: middle, y: value > 0 ? yOf(value) - 7 : yOf(value) + 15, value }];
  }
  if (mark !== "bars" || !isMarked) return [];
  const { width, offset } = sideBySide(band, model.series.length);
  const labels = model.series
    .map((item, order) => ({ order, x: middle + offset(order) + width / 2, y: yOf(row.values[item.key] || 0) - 7, value: row.values[item.key] }))
    .filter((label) => label.value);
  if (labels.length < 2) return labels;
  // 낮은 막대의 글자는 높은 막대 반대쪽으로 민다. 높이가 비슷하거나 칸이 좁으면 높은 쪽만 적는다.
  const [high, low] = labels[0].value >= labels[1].value ? labels : [labels[1], labels[0]];
  if (band < BOTH_LABELS_BAND || Math.abs(high.y - low.y) < 15) return [high];
  const away = low.order > high.order;
  return [high, { ...low, x: low.x + ((away ? -1 : 1) * width) / 2, anchor: away ? "start" : "end" }];
}

function drawBand(view, chart, geometry, row, index) {
  const { model, period } = view;
  const { band, width, height, baseline, center, labelEvery, rows, yOf, marked } = geometry;
  const left = AXIS_WIDTH + index * band;
  const middle = center(index);
  const selected = row.key === period.selected;
  const described = model.series
    .filter((item) => row.values[item.key])
    .slice(0, 4)
    .map((item) => `${item.name} ${won(row.values[item.key])}`)
    .join(", ");
  const group = svgNode(
    "g",
    {
      class: `band${selected ? " selected" : ""}${row.key === marked ? " marked" : ""}`,
      tabindex: 0,
      role: "button",
      "aria-pressed": selected ? "true" : "false",
      "aria-label": `${bucketTitle(model.unit, row.key)}, ${described || "기록 없음"}`,
      "data-mark": row.key,
    },
    chart,
  );
  svgNode("rect", { class: "wash", x: left, y: PLOT_TOP - 8, width: band, height: height - PLOT_TOP + 8, rx: 4 }, group);
  drawBars(view, group, geometry, row, middle);
  for (const label of barLabels(view, geometry, row, middle)) {
    const x = Math.min(label.x, width - (label.anchor === "start" ? 34 : 18));
    svgNode("text", { class: "val", x, y: label.y, "text-anchor": label.anchor || "middle" }, group).textContent = labelWon(label.value);
  }
  if (model.form.mark === "lines" || model.form.mark === "area") {
    // 선 차트는 칸 대신 세로줄과 점으로 어느 칸을 보는지 알려 준다.
    svgNode("line", { class: "cross", x1: middle, x2: middle, y1: PLOT_TOP, y2: baseline }, group);
    for (const item of model.series) {
      const value = row.values[item.key];
      if (value !== null) svgNode("circle", { class: `dot cross-dot ${seriesClass(view, item)}`, cx: middle, cy: yOf(value), r: 4 }, group);
    }
  }

  const fromEnd = rows.length - 1 - index;
  if (fromEnd % labelEvery === 0) {
    svgNode("text", { x: middle, y: baseline + 16, "text-anchor": "middle" }, group).textContent = bucketTick(model.unit, row.key);
  }
  if (model.unit === "month") {
    const [year, month] = row.key.split("-");
    const startsYear = index === 0 && !(rows.length > 1 && rows[1].key.endsWith("-01"));
    if (startsYear || month === "01") {
      svgNode("text", { x: middle, y: baseline + 32, "text-anchor": "middle" }, group).textContent = year;
    }
  }

  onActivate(group, () => view.toggleBucket(row.key));
  group.addEventListener("pointerenter", (event) => {
    if (event.pointerType === "mouse") showBandTooltip(view, row, middle);
  });
  group.addEventListener("pointerleave", () => hideTooltip(view));
  group.addEventListener("focus", () => {
    if (group.matches(":focus-visible")) showBandTooltip(view, row, middle);
  });
  group.addEventListener("blur", () => hideTooltip(view));
}

function drawBars(view, group, geometry, row, middle) {
  const { model } = view;
  const { yOf, band } = geometry;
  const zero = yOf(0);
  const mark = model.form.mark;
  if (mark === "bars") {
    const shown = model.series;
    const { width, offset } = sideBySide(band, shown.length);
    shown.forEach((item, order) => {
      const value = row.values[item.key];
      if (!value) return;
      const to = value > 0 ? Math.min(yOf(value), zero - MIN_BAR_HEIGHT) : Math.max(yOf(value), zero + MIN_BAR_HEIGHT);
      const left = middle + offset(order);
      svgNode("path", { class: `bar ${seriesClass(view, item)}`, d: barPath(left, width, zero, to) }, group);
    });
  } else if (mark === "diverging") {
    const value = row.values[model.series[0].key];
    if (!value) return;
    const width = Math.max(MIN_BAR_WIDTH, Math.min(MAX_BAR_WIDTH, band * BAR_SHARE));
    const to = value > 0 ? Math.min(yOf(value), zero - MIN_BAR_HEIGHT) : Math.max(yOf(value), zero + MIN_BAR_HEIGHT);
    svgNode("path", { class: `bar series-${value > 0 ? "up" : "down"}`, d: barPath(middle - width / 2, width, zero, to) }, group);
  } else if (mark === "stack") {
    const width = Math.max(MIN_BAR_WIDTH, Math.min(MAX_BAR_WIDTH, band * BAR_SHARE));
    const segments = focusFirst(view, model.series.filter((item) => row.values[item.key] > 0));
    let sum = 0;
    segments.forEach((item, order) => {
      const from = yOf(sum);
      sum += row.values[item.key];
      const to = yOf(sum);
      // 조각 사이는 2px 틈으로 나누고, 맨 위 조각만 둥글게 한다.
      const gap = from - to > BAR_GAP + 1 ? BAR_GAP : 0;
      const d = order === segments.length - 1
        ? barPath(middle - width / 2, width, from - gap, to)
        : `M${middle - width / 2} ${from - gap}V${to}H${middle + width / 2}V${from - gap}Z`;
      svgNode("path", { class: `bar ${seriesClass(view, item)}`, d }, group);
    });
  }
}

/* 선 끝에 값을 적는다. 값이 가까우면 글자가 겹치지 않게 아래로 민다. */
function drawEndLabels(layer, ends, width) {
  let previous = -Infinity;
  for (const end of [...ends].sort((a, b) => a.y - b.y)) {
    const y = Math.max(end.y - 11, previous + 15, 12);
    previous = y;
    const anchor = end.x > width - 44 ? "end" : end.x < AXIS_WIDTH + 44 ? "start" : "middle";
    svgNode("text", { class: "val", x: end.x, y, "text-anchor": anchor }, layer).textContent = labelWon(end.value);
  }
}

function drawLines(view, chart, geometry, width) {
  const { model, period, state } = view;
  const { center, yOf, baseline, rows, marked, band } = geometry;
  const layer = svgNode("g", { class: "marks" }, chart);
  const floor = Math.min(yOf(Math.max(0, geometry.scale.bottom)), baseline);
  const ends = [];
  for (const item of focusLast(view, model.series)) {
    const points = rows.flatMap((row, index) =>
      row.values[item.key] === null
        ? []
        : [{ x: center(index), y: yOf(row.values[item.key]), key: row.key, value: row.values[item.key] }],
    );
    if (!points.length) continue;
    // 선이 하나면 면을 옅게 깔아 크기를 함께 읽게 한다.
    if ((model.form.mark === "area" || model.series.length === 1) && points.length > 1) {
      const path = points.map((point, index) => `${index ? "L" : "M"}${point.x} ${point.y}`).join("");
      svgNode("path", { class: `area ${seriesClass(view, item)}`, d: `${path}L${points[points.length - 1].x} ${floor}L${points[0].x} ${floor}Z` }, layer);
    }
    if (points.length > 1) {
      svgNode("polyline", { class: `line ${seriesClass(view, item)}`, points: points.map((point) => `${point.x},${point.y}`).join(" ") }, layer);
    }
    points.forEach((point, index) => {
      if (index === points.length - 1 || point.key === period.selected) {
        svgNode("circle", { class: `dot ${seriesClass(view, item)}`, cx: point.x, cy: point.y, r: 4 }, layer);
      } else if (model.dots && band >= DOT_BAND) {
        svgNode("circle", { class: `dot ${seriesClass(view, item)}`, cx: point.x, cy: point.y, r: 2.5 }, layer);
      }
    });
    if (isFocusedSeries(state, item)) ends.push(points.find((point) => point.key === marked) || points[points.length - 1]);
  }
  if (ends.length <= DIRECT_LABELS) drawEndLabels(layer, ends, width);
}

/* 막대 하나짜리 추이에는 평균선을 그어 이번 칸이 많은지 적은지 바로 읽게 한다. */
function drawAverage(view, chart, geometry, width) {
  const { model } = view;
  const key = model.series[0].key;
  const values = model.rows.map((row) => row.values[key]).filter((value) => value !== null);
  if (values.length < 2) return;
  const average = Math.round(sumOf(values, (value) => value) / values.length);
  const y = Math.round(geometry.yOf(average)) + 0.5;
  svgNode("line", { class: "ref", x1: AXIS_WIDTH, x2: width - PLOT_RIGHT, y1: y, y2: y }, chart);
  svgNode("text", { class: "ref-t", x: AXIS_WIDTH + 4, y: y - 6 }, chart).textContent = `${AVERAGE_NAMES[model.unit]} ${labelWon(average)}`;
}

function drawTrend(view, chart, width, height) {
  const { model, period, state } = view;
  const domain = modelDomain(model);
  const scale = axisScale(domain.low, domain.high);
  const baseline = height - LABEL_BAND;
  const plotHeight = baseline - PLOT_TOP;
  const yOf = (value) => baseline - (plotHeight * (value - scale.bottom)) / (scale.top - scale.bottom);
  const rows = model.rows;
  const band = (width - AXIS_WIDTH - PLOT_RIGHT) / rows.length;
  const geometry = {
    band,
    width,
    height,
    baseline,
    rows,
    scale,
    yOf,
    center: (index) => AXIS_WIDTH + (index + 0.5) * band,
    labelEvery: Math.ceil(MIN_LABEL_WIDTH[model.unit] / band),
    marked: markedKey(view),
  };
  const singleBars = model.form.mark === "bars" && model.series.length === 1;
  const lines = model.form.mark === "lines" || model.form.mark === "area";
  if (period.selected) chart.classList.add("has-selection");
  else if (singleBars) chart.classList.add("has-marked");
  if (lines) chart.classList.add("is-line");
  scale.ticks.forEach((value) => {
    const y = Math.round(yOf(value)) + 0.5;
    const isAxis = value === 0 || (value === scale.bottom && value > 0);
    svgNode("line", { class: isAxis ? "axis" : "grid", x1: AXIS_WIDTH, x2: width - PLOT_RIGHT, y1: y, y2: y }, chart);
    svgNode("text", { x: AXIS_WIDTH - 6, y, dy: "0.32em", "text-anchor": "end" }, chart).textContent = compactWon(value);
  });
  rows.forEach((row, index) => drawBand(view, chart, geometry, row, index));
  if (lines) drawLines(view, chart, geometry, width);
  if (singleBars && state.tab === "flow") drawAverage(view, chart, geometry, width);
}

/* --- 기간 비중 */

function isFocused(view, item) {
  const { state, model } = view;
  if (!state.focus) return true;
  if (item.group !== undefined && item.group === state.focus) return true;
  if (item.key === state.focus) return true;
  if (item.slot !== REST) return false;
  if (state.focus === REST) return true;
  return item.key === REST && model.items.find((entry) => entry.key === state.focus)?.slot === REST;
}

function shareDescription(item) {
  const parts = [item.name, won(item.value), percentText(item.share)];
  if (item.change) parts.push(signedWon(item.change));
  return parts.join(", ");
}

function bindShareMark(view, group, item, anchor) {
  const focusKey = item.group === undefined ? item.key : item.group;
  group.setAttribute("tabindex", 0);
  group.setAttribute("role", "button");
  group.setAttribute("aria-pressed", view.state.focus === focusKey ? "true" : "false");
  group.setAttribute("aria-label", shareDescription(item));
  group.dataset.mark = item.key;
  onActivate(group, () => view.toggleFocus(focusKey));
  group.addEventListener("pointerenter", (event) => {
    if (event.pointerType === "mouse") showShareTooltip(view, item, anchor);
  });
  group.addEventListener("pointerleave", () => hideTooltip(view));
  group.addEventListener("focus", () => {
    if (group.matches(":focus-visible")) showShareTooltip(view, item, anchor);
  });
  group.addEventListener("blur", () => hideTooltip(view));
}

function drawDonut(view, chart, width, height) {
  const { model, state } = view;
  const outer = Math.max(Math.min(width, height) / 2 - DONUT_MARGIN, 10);
  const inner = outer * DONUT_HOLE;
  const cx = width / 2;
  const cy = height / 2;
  for (const slice of donutSlices(donutItems(model, state), cx, cy, outer, inner)) {
    const dimmed = isFocused(view, slice) ? "" : " dimmed";
    const group = svgNode("g", { class: `share-mark series-${slice.slot}${dimmed}` }, chart);
    svgNode("path", { class: "slice", d: slice.path }, group);
    if (slice.showLabel) {
      svgNode("text", { class: "on-fill", x: slice.labelX, y: slice.labelY, dy: "0.32em", "text-anchor": "middle" }, group).textContent =
        percentText(slice.share);
    }
    bindShareMark(view, group, slice, { x: slice.labelX, y: slice.labelY });
  }
  svgNode("text", { class: "donut-total", x: cx, y: cy - 2, "text-anchor": "middle" }, chart).textContent = `${labelWon(model.total)}원`;
  svgNode("text", { class: "donut-caption", x: cx, y: cy + 16, "text-anchor": "middle" }, chart).textContent = model.caption;
}

/* 글자가 칸에 들어갈 때만 적는다. 들어가지 않는 값은 툴팁에 있다. */
function fittedLabel(group, className, x, y, room, text) {
  const node = svgNode("text", { class: className, x, y }, group);
  node.textContent = text;
  if (node.getComputedTextLength() <= room) return true;
  node.remove();
  return false;
}

function drawTreemap(view, chart, width, height) {
  const { model } = view;
  const cells = treemapCells(model.items, 0, 0, width, height);
  for (const cell of cells) {
    const dimmed = isFocused(view, cell) ? "" : " dimmed";
    const group = svgNode("g", { class: `share-mark series-${cell.slot}${dimmed}` }, chart);
    const box = { x: cell.x, y: cell.y, width: Math.max(cell.width, 0), height: Math.max(cell.height, 0), rx: 4 };
    svgNode("rect", { ...box, class: cell.leaf ? "cell" : "cell-group" }, group);
    const room = cell.width - CELL_PADDING * 2;
    const x = cell.x + CELL_PADDING;
    if (!cell.leaf) {
      if (cell.header) fittedLabel(group, "cell-head", x, cell.y + 14, room, `${cell.name} ${percentText(cell.share)}`);
      continue; // 묶음은 배경이고, 눌리는 것은 안의 상품이다
    }
    if (cell.height >= CELL_NAME_HEIGHT && fittedLabel(group, "on-fill", x, cell.y + 19, room, cell.name) && cell.height >= CELL_VALUE_HEIGHT) {
      fittedLabel(group, "on-fill cell-value", x, cell.y + 35, room, `${labelWon(cell.value)} · ${percentText(cell.share)}`);
    }
    bindShareMark(view, group, cell, { x: cell.x + cell.width / 2, y: cell.y + cell.height / 2 });
  }
}

function drawSankey(view, chart, width, height) {
  const layout = sankeyLayout(view.model.sankey, width, height - SANKEY_MARGIN_Y, { pad: 8, nodeWidth: SANKEY_NODE_WIDTH });
  const shift = SANKEY_MARGIN_Y / 2 + 4;
  const canvas = svgNode("g", { transform: `translate(0 ${shift})` }, chart);
  const links = svgNode("g", { class: "sankey-links" }, canvas);
  const nodes = new Map(layout.nodes.map((node) => [node.key, node]));
  for (const link of layout.links) {
    const path = svgNode("path", { class: `sankey-link series-${link.slot}`, d: link.path, "stroke-width": link.strokeWidth }, links);
    const source = nodes.get(link.source);
    const target = nodes.get(link.target);
    path.addEventListener("pointerenter", (event) => {
      if (event.pointerType !== "mouse") return;
      showTooltip(view, `${source.name} → ${target.name}`, [{ slot: link.slot, value: won(link.value), name: "" }], {
        x: (source.x1 + target.x0) / 2,
        y: (link.sy + link.ty) / 2 + shift,
      });
    });
    path.addEventListener("pointerleave", () => hideTooltip(view));
  }
  for (const node of layout.nodes) {
    const group = svgNode("g", { class: `sankey-node series-${node.slot}${node.key === "center" ? " is-center" : ""}` }, canvas);
    svgNode("rect", { x: node.x0, y: node.y0, width: node.x1 - node.x0, height: Math.max(node.y1 - node.y0, 1), rx: 2 }, group);
    const middle = (node.y0 + node.y1) / 2;
    let attributes;
    if (node.column === 0) attributes = { x: node.x1 + 6, y: middle, dy: "0.32em", "text-anchor": "start" };
    else if (node.column === layout.lastColumn) attributes = { x: node.x0 - 6, y: middle, dy: "0.32em", "text-anchor": "end" };
    else attributes = { x: (node.x0 + node.x1) / 2, y: node.y0 - 6, "text-anchor": "middle" };
    svgNode("text", { class: "sankey-label", ...attributes }, group).textContent = `${node.name} ${labelWon(node.value)}`;
  }
}

function drawChart(view) {
  const { canvas, model, state } = view;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (!width || !height) return;
  const focused = document.activeElement?.dataset?.mark;
  hideTooltip(view);
  view.chart?.remove();
  view.chart = null;
  const share = state.view === "share";
  const sankey = share && model.form.key === "sankey";
  const filled = sankey ? Boolean(model.sankey) : share ? model.items.length > 0 : hasValues(model);
  view.empty.hidden = filled;
  if (!filled) return;

  const chart = svgNode("svg", { class: `fullchart${share ? " sharechart" : ""}`, width, height });
  // 글자 폭을 재려면 먼저 화면에 붙어 있어야 한다.
  canvas.prepend(chart);
  view.chart = chart;
  if (!share) drawTrend(view, chart, width, height);
  else if (sankey) drawSankey(view, chart, width, height);
  else if (model.form.key === "treemap") drawTreemap(view, chart, width, height);
  else drawDonut(view, chart, width, height);
  if (focused) [...chart.querySelectorAll("[data-mark]")].find((node) => node.dataset.mark === focused)?.focus();
}

/* --- 툴팁 */

function showTooltip(view, title, rows, anchor) {
  const tip = view.tooltip;
  tip.replaceChildren();
  htmlNode("div", "tip-title", tip, title);
  for (const row of rows) {
    const line = htmlNode("div", "tip-row", tip);
    htmlNode("span", `tip-key series-${row.slot}`, line);
    htmlNode("strong", "", line, row.value);
    if (row.name) htmlNode("span", "tip-name", line, row.name);
  }
  tip.hidden = false;
  const left = Math.max(4, Math.min(anchor.x - tip.offsetWidth / 2, view.canvas.clientWidth - tip.offsetWidth - 4));
  tip.style.left = `${left}px`;
  const top = anchor.y === undefined ? 8 : Math.max(4, Math.min(anchor.y - tip.offsetHeight - 10, view.canvas.clientHeight - tip.offsetHeight - 4));
  tip.style.top = `${top}px`;
}

function showBandTooltip(view, row, center) {
  const { model } = view;
  let items = model.series.map((item) => ({ ...item, value: row.values[item.key] }));
  if (model.form.mark === "stack") items = items.filter((item) => item.value).sort((a, b) => b.value - a.value);
  const rows = items.map((item) => ({
    slot: item.signed ? (item.value > 0 ? "up" : "down") : item.slot,
    value: item.value === null ? "기록 없음" : item.signed ? signedWon(item.value) : won(item.value),
    name: item.name,
  }));
  if (!rows.length) rows.push({ slot: REST, value: "기록 없음", name: "" });
  showTooltip(view, bucketTitle(model.unit, row.key), rows, { x: center });
}

function showShareTooltip(view, item, anchor) {
  const rows = [{ slot: item.slot, value: won(item.value), name: percentText(item.share) }];
  if (item.change) rows.push({ slot: item.change > 0 ? "up" : "down", value: signedWon(item.change), name: "증감" });
  showTooltip(view, item.name, rows, anchor);
}

function hideTooltip(view) {
  view.tooltip.hidden = true;
}

/* --- 요약 칸과 안내 */

function setTile(view, name, label, value, format) {
  view.app.querySelector(`[data-tile-label="${name}"]`).textContent = label;
  const node = view.app.querySelector(`[data-tile="${name}"]`);
  node.title = "";
  if (value === null || value === undefined) {
    node.textContent = "-";
    return;
  }
  if (!format) {
    node.textContent = value;
    node.title = value;
    return;
  }
  node.textContent = format(value);
  // 칸이 좁아 금액이 잘리면 줄여서 보여 준다.
  if (node.scrollWidth > node.clientWidth) {
    node.textContent = `${format === signedWon && value > 0 ? "+" : ""}${compactWon(value)}원`;
  }
}

function drawTiles(view) {
  const { state, summary, period, app } = view;
  const third = app.querySelector("[data-third]");
  third.hidden = false;
  const kind = shownKind(state);
  if (kind) {
    const name = FLOW_KINDS.find((item) => item.key === kind).name;
    const amount = summary[kind];
    setTile(view, "first", `${period.short} ${name}`, amount, won);
    const focused = view.model.items.find((item) => item.key === state.focus);
    if (focused) setTile(view, "second", focused.name, `${won(focused.value)} · ${percentText(focused.share)}`);
    else setTile(view, "second", "가장 큰 카테고리", summary.top ? `${summary.top.name} ${percentText(summary.top.share)}` : null);
    if (summary.previous !== null) setTile(view, "third", `${STEP_NAMES[period.unit]}보다`, amount - summary.previous, signedWon);
    else setTile(view, "third", `${AVERAGE_NAMES[period.unit]} ${name}`, summary.average, won);
    return;
  }
  if (state.tab === "flow") {
    setTile(view, "first", `${period.short} 수입`, summary.income, won);
    setTile(view, "second", `${period.short} 지출`, summary.spend, won);
    // 조건을 걸면 수입과 지출이 서로 다른 거래의 합이라 차이에 뜻이 없다.
    const filtered = hasFilters(state);
    third.hidden = filtered && (Boolean(period.selected) || summary.average === null);
    if (third.hidden) return;
    if (filtered) setTile(view, "third", `${AVERAGE_NAMES[period.unit]} 지출`, summary.average, won);
    else setTile(view, "third", "수입 - 지출", summary.income === null ? null : summary.income - summary.spend, signedWon);
    return;
  }
  const prefix = state.tab === "finance" ? "금융 " : "";
  setTile(view, "first", `${period.selected ? period.short : "최근"} ${prefix}순자산`, summary.net, won);
  setTile(view, "second", period.selected ? "직전 기록보다" : `${period.short} 증감`, summary.change, signedWon);
  if (summary.profit !== null && summary.profit !== undefined) setTile(view, "third", "투자 평가 손익", summary.profit, signedWon);
  else setTile(view, "third", `${prefix}자산`, summary.assets, won);
}

function drawLegend(view) {
  const { legend, model, state } = view;
  legend.replaceChildren();
  // 비중은 목록과 항목 이름이, 값이 하나인 추이는 고른 버튼이 범례를 대신한다.
  legend.hidden = state.view === "share" || !model.legend.length;
  for (const item of model.legend) {
    const node = htmlNode(item.toggle ? "button" : "span", "legend-item", legend);
    htmlNode("span", `swatch ${seriesClass(view, item)}`, node);
    htmlNode("span", "", node, item.name);
    if (item.amount !== undefined && item.amount !== null) htmlNode("small", "", node, labelWon(item.amount));
    if (!item.toggle) continue;
    node.type = "button";
    node.setAttribute("aria-pressed", item.pressed ? "true" : "false");
    node.addEventListener("click", () => {
      // 모두 끄면 빈 차트가 되므로 하나는 남긴다.
      if (item.pressed && model.legend.filter((entry) => entry.pressed).length === 1) return;
      if (state.hidden.has(item.key)) state.hidden.delete(item.key);
      else state.hidden.add(item.key);
      view.update();
    });
  }
}

function drawShareList(view) {
  const { host, shareList, model, state } = view;
  const show = state.view === "share" && model.form.key !== "sankey" && model.items.length > 0;
  shareList.hidden = !show;
  host.classList.toggle("has-share", show);
  if (!show) {
    shareList.replaceChildren();
    return;
  }
  const largest = Math.max(...model.items.map((item) => item.value));
  shareList.replaceChildren(
    ...model.items.map((item) => {
      const line = document.createElement("li");
      const button = htmlNode("button", "share-row", line);
      button.type = "button";
      const pressed = state.focus === item.key || (state.focus === REST && item.slot === REST);
      button.setAttribute("aria-pressed", pressed ? "true" : "false");
      const colors = `series-${item.slot}${state.focus && !pressed ? " muted" : ""}`;
      htmlNode("span", `swatch ${colors}`, button);
      htmlNode("span", "share-name", button, item.name);
      htmlNode("span", "share-amount", button, won(item.value));
      htmlNode("small", "share-percent", button, percentText(item.share));
      htmlNode("span", `share-bar ${colors}`, button).style.width = `${(item.value / largest) * 100}%`;
      if (item.change) htmlNode("small", "share-change", button, signedWon(item.change));
      button.addEventListener("click", () => view.toggleFocus(item.key));
      return line;
    }),
  );
}

/* --- 버튼 */

function makeChip(list, name, onClick) {
  const chip = htmlNode("button", "chip", list);
  chip.type = "button";
  chip.label = htmlNode("span", "chip-name", chip, name);
  chip.amount = htmlNode("small", "", chip);
  chip.addEventListener("click", onClick);
  return chip;
}

function makeRow(parent, title) {
  const row = htmlNode("div", "filter-row", parent);
  htmlNode("span", "filter-title", row, title);
  return { row, list: htmlNode("div", "chips", row) };
}

function fitsShownKind(categoryKind, kind) {
  return !categoryKind || !kind || categoryKind === kind;
}

/* 지금 보는 쪽에 없는 카테고리 조건을 남기면 빈 화면이 된다. */
function dropHiddenCategories(view) {
  const { state, data } = view;
  const kind = shownKind(state);
  if (!kind) return;
  for (const item of data.categories) {
    if (item.kind !== kind) state.categories.delete(item.key);
  }
}

function switchTab(view, key) {
  const { state } = view;
  state.tab = key;
  state.focus = null;
  state.record = null;
  dropHiddenCategories(view);
  for (const link of document.querySelectorAll("[data-chart-tab]")) {
    if (link.dataset.chartTab === key) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  view.update();
}

function buildNavigation(view) {
  for (const link of document.querySelectorAll("[data-chart-tab]")) {
    link.addEventListener("click", (event) => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      switchTab(view, link.dataset.chartTab);
    });
  }
}

function buildChoices(view) {
  const state = view.live;
  const refresh = () => view.update();
  const add = (chip, visible, pressed) => view.staticChips.push({ chip, visible, pressed });

  view.kindRow = htmlNode("div", "segmented", view.controls);
  view.kindRow.setAttribute("role", "group");
  view.kindRow.setAttribute("aria-label", "수입과 지출 중 볼 것");
  view.kindButtons = FLOW_KINDS.map((kind) => {
    const button = htmlNode("button", "segment", view.kindRow, kind.name);
    button.type = "button";
    button.addEventListener("click", () => {
      chooseFlowKind(state, kind.key);
      dropHiddenCategories(view);
      refresh();
    });
    return { button, key: kind.key };
  });

  const formRow = htmlNode("div", "chips", view.controls);
  for (const item of VIEWS) {
    const chip = makeChip(formRow, item.name, () => {
      state.view = item.key;
      refresh();
    });
    chip.classList.add("chip-strong");
    add(chip, () => true, () => state.view === item.key);
  }
  htmlNode("span", "chip-divider", formRow);
  const addForm = (form, tab, viewKey) => {
    const chip = makeChip(formRow, form.name, () => {
      chooseForm(state, form.key);
      dropHiddenCategories(view);
      refresh();
    });
    add(
      chip,
      () => state.view === viewKey && (!tab || state.tab === tab),
      () => state.forms[state.tab][viewKey] === form.key,
    );
  };
  for (const form of SHARE_FORMS) addForm(form, null, "share");
  for (const [tab, forms] of Object.entries(TREND_FORMS)) {
    for (const form of forms) addForm(form, tab, "trend");
  }

  const scaleRow = htmlNode("div", "chips", view.controls);
  for (const scale of SCALES) {
    const chip = makeChip(scaleRow, scale.name, () => {
      if (scale.key === "custom") {
        state.start = state.start || view.period.scopeFrom;
        state.end = state.end || view.period.scopeTo;
      }
      state.scale = scale.key;
      state.bucket = null;
      refresh();
    });
    add(chip, () => true, () => state.scale === scale.key);
  }
}

function buildPeriodRow(view) {
  const state = view.live;
  const row = htmlNode("div", "period-row", view.controls);
  const step = (text, label, pick) => {
    const button = htmlNode("button", "chip period-step", row, text);
    button.type = "button";
    button.setAttribute("aria-label", label);
    button.addEventListener("click", () => {
      const target = pick();
      if (!target) return;
      state.bucket = target;
      view.update();
    });
    return button;
  };
  view.previousButton = step("‹", "이전 기간", () => view.period.previous);
  view.periodLabel = htmlNode("strong", "period-label", row);
  view.nextButton = step("›", "다음 기간", () => view.period.next);
  view.asOf = htmlNode("small", "hint", row);

  const dates = htmlNode("div", "period-dates", row);
  const dateInput = (label, name) => {
    const holder = htmlNode("label", "", dates);
    htmlNode("span", "", holder, label);
    const input = htmlNode("input", "", holder);
    input.type = "date";
    input.addEventListener("change", () => {
      state[name] = validDay(input.value);
      state.bucket = null;
      view.update();
    });
    return input;
  };
  view.dates = dates;
  view.startInput = dateInput("시작", "start");
  view.endInput = dateInput("끝", "end");
}

/* 휴대폰에서 조건을 고르는 전체 화면. 확인을 눌러야 적용되고, 취소하면 그대로다. */
function buildPicker(view) {
  const { state } = view;
  const dialog = htmlNode("dialog", "picker", view.app);
  dialog.setAttribute("aria-labelledby", "picker-title");
  const head = htmlNode("div", "picker-head", dialog);
  const heading = htmlNode("h2", "", head);
  heading.id = "picker-title";
  const count = htmlNode("p", "picker-count", head);
  count.setAttribute("aria-live", "polite");
  const list = htmlNode("div", "chips picker-list", dialog);
  const foot = htmlNode("div", "picker-foot", dialog);
  const cancel = htmlNode("button", "button", foot, "취소");
  const confirm = htmlNode("button", "button primary", foot, "확인");
  cancel.type = "button";
  confirm.type = "button";
  let apply = null;
  cancel.addEventListener("click", () => dialog.close());
  confirm.addEventListener("click", () => {
    apply();
    dialog.close();
  });
  view.openPicker = ({ title, dimension, options, shownKeys, choose }) => {
    const shown = shownKeys();
    const picked = chosenKeys(state, dimension.state, shown);
    const totals = facetTotals(view.data, state, view.period, dimension);
    const mark = (chip, key) => {
      chip.setAttribute("aria-pressed", picked.has(key) ? "true" : "false");
      chip.classList.toggle("dim", !totals.get(key) && !picked.has(key));
      count.textContent = `${picked.size}개 선택됨`;
    };
    heading.textContent = title;
    list.replaceChildren();
    let previousKind = null;
    for (const option of options.filter((item) => shown.includes(item.key))) {
      if (option.kind && previousKind && option.kind !== previousKind) htmlNode("span", "chip-divider", list);
      previousKind = option.kind;
      const chip = makeChip(list, option.name, () => {
        if (picked.has(option.key)) picked.delete(option.key);
        else picked.add(option.key);
        mark(chip, option.key);
      });
      chip.amount.textContent = chipAmount(totals.get(option.key));
      mark(chip, option.key);
    }
    apply = () => choose(picked);
    dialog.showModal();
    list.scrollTop = 0;
  };
}

/* 조건을 걸지 않았거나 모두 골라, 줄의 항목이 전부 들어가 있는지. */
function includesEverything(state, dimension) {
  return state.allChosen.has(dimension.state) || state[dimension.state].size === 0;
}

function buildFilters(view) {
  const { state, data } = view;
  view.facetChips = [];
  view.facetButtons = [];
  // allLabel이 있는 줄은 처음부터 모든 항목이 들어가 있다. 항목을 누르면 빠지거나 다시 들어가고, allLabel을 누르면 모두 들어간다.
  const addFacet = (title, tabs, dimension, options, { allLabel = null } = {}) => {
    const { row, list } = makeRow(view.filters, title);
    const shownKeys = () => {
      const kind = shownKind(state);
      return options.filter((option) => fitsShownKind(option.categoryKind, kind)).map((option) => option.key);
    };
    const choose = (keys) => {
      setChosen(state, dimension.state, keys, shownKeys());
      state.focus = null;
      view.update();
    };
    const allChip = makeChip(list, allLabel || "모두 선택", () => {
      if (allLabel) choose(new Set(shownKeys()));
      else choose(new Set(state.allChosen.has(dimension.state) ? [] : shownKeys()));
    });
    allChip.classList.add("chip-all");
    const pickChip = makeChip(list, "고르기", () => view.openPicker({ title, dimension, options, shownKeys, choose }));
    pickChip.classList.add("chip-pick");
    pickChip.setAttribute("aria-haspopup", "dialog");
    view.facetButtons.push({ row, allChip, pickChip, dimension, tabs, shownKeys, allLabel });
    let previousKind = null;
    for (const option of options) {
      let divider = null;
      if (option.kind && previousKind && option.kind !== previousKind) divider = htmlNode("span", "chip-divider", list);
      previousKind = option.kind;
      const chip = makeChip(list, option.name, () => {
        const shown = shownKeys();
        const keys = allLabel && includesEverything(state, dimension) ? new Set(shown) : chosenKeys(state, dimension.state, shown);
        if (keys.has(option.key)) keys.delete(option.key);
        else keys.add(option.key);
        if (allLabel && !keys.size) return; // 하나도 남지 않으면 전부를 고른 것과 같아져서 막는다
        choose(keys);
      });
      view.facetChips.push({
        chip,
        divider,
        dimension,
        key: option.key,
        tabs,
        categoryKind: option.categoryKind,
        pressed: () => state[dimension.state].has(option.key) || Boolean(allLabel && includesEverything(state, dimension)),
      });
    }
  };
  const flowTabs = ["category", "flow"];
  const [channels, categories, instruments] = FLOW_DIMENSIONS;
  addFacet("결제 방식", flowTabs, channels, data.channels);
  addFacet(
    "카테고리",
    flowTabs,
    categories,
    data.categories.map((item) => ({ key: item.key, name: item.name, kind: item.kind, categoryKind: item.kind })),
  );
  addFacet("결제 수단", flowTabs, instruments, data.instruments.map((item) => ({ key: item.id, name: item.name })));
  addFacet(
    "항목",
    ["assets"],
    GROUP_DIMENSION,
    data.asset_groups.map((item) => ({ key: item.key, name: item.name, kind: item.side })),
  );
  addFacet(
    "항목",
    ["finance"],
    FINANCE_GROUP_DIMENSION,
    data.finance_groups.map((item) => ({ key: item.key, name: item.name, kind: item.side })),
    { allLabel: "금융 전체" },
  );
  addFacet(
    "상품",
    ["finance"],
    PRODUCT_DIMENSION,
    data.finance_items.map((item) => ({ key: item.key, name: item.name, kind: item.side })),
  );

  view.clear = makeChip(view.filters, "조건 지우기", () => {
    for (const dimensions of Object.values(DIMENSIONS)) {
      for (const dimension of dimensions) state[dimension.state].clear();
    }
    state.allChosen.clear();
    state.focus = null;
    view.update();
  });
  view.clear.classList.add("chip-clear");
}

function drawPeriodRow(view) {
  const { state, period, data, model } = view;
  const custom = state.scale === "custom";
  const stepping = Boolean(period.selected);
  view.previousButton.hidden = !stepping;
  view.nextButton.hidden = !stepping;
  view.previousButton.disabled = !period.previous;
  view.nextButton.disabled = !period.next;
  view.periodLabel.textContent = period.label;
  view.periodLabel.hidden = custom && !period.selected;
  view.asOf.textContent = model.asOf ? `${model.asOf} 기준` : "";
  view.dates.hidden = !custom;
  if (!custom) return;
  const range = rangeOf(data, state.tab);
  for (const input of [view.startInput, view.endInput]) {
    input.min = range ? range.first : "";
    input.max = range ? range.last : "";
  }
  view.startInput.value = period.from || "";
  view.endInput.value = period.to || "";
}

function drawFreeControls(view) {
  const { state } = view;
  for (const entry of view.staticChips) {
    entry.chip.hidden = !entry.visible();
    entry.chip.setAttribute("aria-pressed", entry.pressed() ? "true" : "false");
  }
  view.kindRow.hidden = state.tab !== "flow";
  for (const { button, key } of view.kindButtons) button.setAttribute("aria-pressed", state.flowKind === key ? "true" : "false");
  drawPeriodRow(view);
}

/* --- 차트 모양 고르기 위젯: 기간과 모양을 직접 고르는 차트. 조건과 강조는 화면의 것을 함께 쓴다. */

function freeChartParts() {
  const root = htmlNode("div", "free-chart");
  const controls = htmlNode("div", "chart-controls", root);
  const tiles = htmlNode("div", "tiles", root);
  tiles.setAttribute("aria-live", "polite");
  for (const name of ["first", "second", "third"]) {
    const tile = htmlNode("div", "tile", tiles);
    if (name === "third") tile.dataset.third = "";
    htmlNode("span", "", tile, "-").dataset.tileLabel = name;
    htmlNode("strong", "", tile, "-").dataset.tile = name;
  }
  const host = htmlNode("div", "chart-host", root);
  const legend = htmlNode("div", "chart-legend", host);
  const body = htmlNode("div", "chart-body", host);
  const canvas = htmlNode("div", "chart-canvas", body);
  const shareList = htmlNode("ul", "share-list", body);
  shareList.hidden = true;
  return { root, controls, host, legend, canvas, shareList };
}

function createFreeChart(data, live, notify) {
  const parts = freeChartParts();
  const tooltip = htmlNode("div", "chart-tooltip", parts.canvas);
  tooltip.hidden = true;
  const empty = htmlNode("p", "chart-empty", parts.canvas, "아직 볼 기록이 없습니다.");
  empty.hidden = true;
  const view = { app: parts.root, data, live, state: live, tooltip, empty, staticChips: [], update: notify, ...parts };
  view.largeButton = htmlNode("button", "widget-link chart-large", null);
  view.largeButton.type = "button";
  view.largeButton.addEventListener("click", () => {
    live.large = !live.large;
    notify();
  });
  view.toggleBucket = (key) => {
    live.bucket = live.bucket === key ? null : key;
    notify();
  };
  view.toggleFocus = (key) => {
    live.focus = live.focus === key ? null : key;
    notify();
  };
  buildChoices(view);
  buildPeriodRow(view);
  const redraw = () => {
    if (!view.model) return;
    drawTiles(view);
    drawChart(view);
  };
  new ResizeObserver(redraw).observe(parts.canvas);
  view.draw = (card) => {
    view.period = resolvePeriod(data, live);
    if (live.bucket && live.bucket !== view.period.selected) live.bucket = null;
    view.model = buildModel(data, live, view.period);
    // 화면의 강조가 이 차트에 없는 항목이면 이 차트에서만 끈다.
    view.state = hasFocusTarget(view.model, live) ? live : { ...live, focus: null };
    view.summary = summarize(data, live, view.period);
    drawFreeControls(view);
    drawLegend(view);
    drawShareList(view);
    drawTiles(view);
    drawChart(view);
    card.setTitle(chartTitle(view.state, view.period));
    card.section.classList.toggle("is-large", live.large);
    view.largeButton.textContent = live.large ? "작게 보기" : "크게 보기";
    card.setExtra(view.largeButton);
  };
  return view;
}

/* --- 탭 화면 */

/* 왼쪽 목록이 대신하는 조건. 목록을 놓으면 그 조건 줄은 보이지 않는다. */
const LIST_COVERS = {
  "category.list": ["categories"],
  "assets.list": ["groups"],
  "finance.list": ["products"], // 묶음 줄은 금융 전체에 넣고 뺄 묶음을 바로 고르게 남긴다
};

function isHoldingTab(tab) {
  return tab === "assets" || tab === "finance";
}

/* 버튼 옆 금액을 낼 기간. 위젯들이 함께 보는 달이나 기록이다. */
function pagePeriod(data, state) {
  const empty = { unit: "month", keys: [], selected: null, from: null, to: null, scopeFrom: null, scopeTo: null, previous: null, next: null, label: "", short: "" };
  if (isHoldingTab(state.tab)) {
    const records = recordsWithin(recordsOf(state.tab === "assets" ? data.snapshots : data.finance_snapshots, unitOf(state)), state);
    if (!records.length) return empty;
    const days = records.map((record) => record.date);
    const at = recordIndex(days, state.record);
    const day = days[at];
    // 버튼 옆 증감은 기간의 첫 기록과 견준다.
    return { ...empty, unit: "day", selected: day, from: day, to: day, scopeFrom: day, scopeTo: day, label: day, short: day, base: at > 0 ? days[0] : null };
  }
  const unit = unitOf(state);
  const axis = bucketAxis(data, state, unit);
  if (!axis) return empty;
  const span = bucketSpan(unit, axis.selected);
  const title = bucketTitle(unit, axis.selected);
  return {
    ...empty,
    unit,
    keys: axis.months,
    selected: axis.selected,
    from: dayKey(bucketSpan(unit, axis.months[0]).start),
    to: dayKey(bucketSpan(unit, axis.months[axis.months.length - 1]).end),
    scopeFrom: dayKey(span.start),
    scopeTo: dayKey(span.end),
    previous: axis.previous,
    next: axis.next,
    label: title,
    short: title,
  };
}

function coveredFilters(view) {
  return new Set(view.layouts[view.state.tab].items.flatMap((item) => LIST_COVERS[item.key] || []));
}

function dropCoveredFilters(view) {
  for (const name of coveredFilters(view)) {
    view.state[name].clear();
    view.state.allChosen.delete(name);
  }
}

/* 칩 묶음 앞에 무엇을 고르는지 적는다. */
function chipGroup(parent, label, id) {
  const group = htmlNode("div", "ctl-group", parent);
  htmlNode("span", "ctl-label", group, label).id = id;
  const box = htmlNode("div", "chips", group);
  box.setAttribute("role", "group");
  box.setAttribute("aria-labelledby", id);
  return box;
}

function spanText(from, to) {
  return from === to ? dotDate(from) : `${dotDate(from)} ~ ${dotDate(to)}`;
}

/* 화면의 칸들이 덮는 날. 데이터가 없는 앞뒤 날은 뺀다. */
function flowSpan(flow) {
  const { unit, months } = flow.axis;
  const from = dayKey(bucketSpan(unit, months[0]).start);
  const to = dayKey(bucketSpan(unit, months[months.length - 1]).end);
  return spanText(from < flow.range.first ? flow.range.first : from, to > flow.range.last ? flow.range.last : to);
}

/* 요약 칸과 거래 보기에 붙일 고른 칸의 이름. 막대를 눌러 고르고, 고르지 않았으면 마지막 칸이다. */
function selectedName(flow) {
  return bucketName(flow.axis.unit, flow.axis.selected, flow.range.last.slice(0, 4));
}

function focusedOnPage(view, ctx) {
  const { focus, tab } = view.state;
  if (!focus) return null;
  if (tab === "category") return ctx.flow ? categoryOf(ctx.flow, focus) : null;
  if (view.layouts[tab].items.some((item) => LIST_COVERS[item.key])) return null;
  if (tab === "assets") return ctx.assets ? [...ctx.assets.groups, ...ctx.assets.debts].find((item) => item.key === focus) || null : null;
  if (tab === "finance") return ctx.finance ? findFinanceItem(ctx.finance, focus) : null;
  return null;
}

function drawPageControls(view, ctx) {
  const { state, data, period } = view;
  view.controls.replaceChildren();
  const row = htmlNode("div", "ctl", view.controls);
  const scope = htmlNode("div", "scope", row); // 기간 칩이 지금 덮는 날
  const end = htmlNode("div", "ctl-end");
  const holding = isHoldingTab(state.tab);
  const source = holding ? ctx.assets || ctx.finance : null;
  if (source) {
    htmlNode("strong", "", scope, spanText(source.days[0], source.days[source.days.length - 1]));
    htmlNode("span", "as-of", scope, `기록 ${source.days.length}회`);
  } else if (!holding && ctx.flow) {
    htmlNode("strong", "", scope, flowSpan(ctx.flow));
  }
  if (ctx.flow || ctx.assets || ctx.finance) {
    drawRangeChips(view, end);
    drawUnitChips(view, end);
  }
  if (!holding && ctx.flow) {
    const href = linesLink(data, { ...state, view: "share", flowKind: "all" }, period);
    if (href) htmlNode("a", "chip", end, `${selectedName(ctx.flow)} 거래 보기`).href = href;
  }
  if (state.tab === "assets") {
    const add = htmlNode("a", "chip", end, "자산 추가");
    add.href = "/assets/manual/new";
  }
  const focused = focusedOnPage(view, ctx);
  if (focused) {
    const chip = htmlNode("button", "chip chip-focus", row);
    chip.type = "button";
    chip.dataset.mark = "focus-off";
    htmlNode("span", "", chip, focused.name);
    htmlNode("span", "focus-chip-x", chip, "×").setAttribute("aria-hidden", "true");
    chip.setAttribute("aria-label", `${focused.name} 강조 끄기`);
    chip.addEventListener("click", () => {
      state.focus = null;
      view.update();
    });
  }
  const edit = makeChip(end, "위젯 편집", () => {
    state.editing = true;
    view.update();
  });
  edit.dataset.mark = "widget-edit";
  row.appendChild(end);
}

/* 1개월부터 최대까지. 고른 단위로 나누면 칸이 너무 많아지는 기간이면 단위를 기간에 맞게 바꾼다. */
function drawRangeChips(view, parent) {
  const { state, data } = view;
  const current = rangeKeyOf(state);
  const box = chipGroup(parent, "기간", "ctl-range");
  for (const range of RANGES) {
    const chip = makeChip(box, RANGE_LABELS[range], () => {
      const unit = unitOf(state);
      state.range = range;
      if (!isHoldingTab(state.tab) && !unitFits(data, state, unit)) state.unit = fittingUnit(data, state, range);
      view.update();
    });
    chip.dataset.mark = `range:${range}`;
    chip.setAttribute("aria-pressed", current === range ? "true" : "false");
  }
}

/* 일간, 주간, 월간, 연간. 고른 때와 보던 기간은 그대로 두고 칸만 바꾼다.
   보던 기간을 이 단위로 나누면 칸이 너무 많으면 기간을 단위에 맞춘다. 일간이면 1개월이다. */
function drawUnitChips(view, parent) {
  const { state, data } = view;
  const current = unitOf(state);
  const box = chipGroup(parent, "단위", "ctl-unit");
  for (const unit of UNITS) {
    const chip = makeChip(box, UNIT_LABELS[unit], () => {
      if (!state.range) state.range = rangeKeyOf(state);
      if (!isHoldingTab(state.tab) && !unitFits(data, state, unit)) state.range = UNIT_RANGES[unit];
      state.unit = unit;
      view.update();
    });
    chip.dataset.mark = `unit:${unit}`;
    chip.setAttribute("aria-pressed", current === unit ? "true" : "false");
  }
}

/* 위젯 배치를 저장한다. 값이 목록이면 같은 이름으로 여러 번 보낸다. 저장했는지를 돌려준다. */
async function saveLayout(view, fields) {
  const { state } = view;
  const body = new URLSearchParams({ tab: state.tab });
  for (const [name, value] of Object.entries(fields)) {
    for (const item of [].concat(value)) body.append(name, item);
  }
  try {
    const response = await fetch("/chart/widgets", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRF-Token": view.csrf, Accept: "application/json" },
      body,
    });
    if (response.status === 401) {
      window.location.assign("/auth/login");
      return false;
    }
    if (!response.ok) throw new Error(String(response.status));
    view.layouts[state.tab] = (await response.json()).layout;
    dropCoveredFilters(view);
    return true;
  } catch {
    return false;
  }
}

function pageTileRows(view, ctx) {
  const { state } = view;
  const theme = view.layouts[state.tab].theme;
  if (theme === "B") return [];
  if (state.tab === "category" && ctx.flow) {
    const { flow } = ctx;
    const { selected, previous, months, unit } = flow.axis;
    const name = selectedName(flow);
    const step = `${STEP_NAMES[unit]}보다`;
    const averageName = AVERAGE_NAMES[unit];
    const total = flow.spend(selected);
    const before = previous === null ? null : flow.spend(previous);
    const average = monthlyAverage(flow, months, (month) => flow.spend(month));
    if (theme === "A") {
      const focus = focusedOnPage(view, ctx);
      if (focus) {
        const value = flow.spend(selected, focus.key);
        return [
          [`${name} ${focus.name} 지출`, won(value), total > 0 ? `지출의 ${percentText(value / total)}` : ""],
          [step, previous === null ? "-" : signedWon(value - flow.spend(previous, focus.key))],
          [averageName, won(monthlyAverage(flow, months, (month) => flow.spend(month, focus.key)))],
          ["전체 지출", won(total)],
        ];
      }
      const top = categoryRank(flow, selected)[0];
      return [
        [`${name} 지출`, won(total)],
        [step, before === null ? "-" : signedWon(total - before)],
        [averageName, won(average)],
        ["가장 큰 카테고리", top ? `${top.name} ${percentText(top.value / total)}` : "-"],
      ];
    }
    const jump = monthlyChanges(flow, selected, previous)[0];
    return [
      [`${name} 지출`, won(total)],
      [step, before === null ? "-" : signedWon(total - before)],
      [averageName, won(average)],
      ["가장 많이 늘어남", jump && jump.change > 0 ? `${jump.name} ${signedLabel(jump.change)}` : "-"],
    ];
  }
  if (state.tab === "flow" && ctx.flow) {
    const { flow } = ctx;
    const { selected, previous, months, unit } = flow.axis;
    const name = selectedName(flow);
    const income = flow.income(selected);
    const spend = flow.spend(selected);
    const incomeTile = [`${name} 수입`, won(income)];
    // 조건을 걸면 수입과 지출이 서로 다른 거래의 합이라 차이에 뜻이 없다.
    if (hasFilters(state)) return [incomeTile, [`${name} 지출`, won(spend)]];
    if (theme === "C") {
      const running = runningNet(flow, months);
      return [incomeTile, [`${name} 지출`, won(spend)], ["수입 - 지출", signedWon(income - spend)], [`${months.length}${UNIT_COUNTS[unit]} 누적`, signedWon(running[running.length - 1])]];
    }
    return [
      incomeTile,
      [`${name} 지출`, won(spend), previous === null ? "" : `${STEP_NAMES[unit]}보다 ${signedWon(spend - flow.spend(previous))}`],
      ["수입 - 지출", signedWon(income - spend)],
      ["수입 대비 지출", income > 0 ? percentText(spend / income) : "-"],
    ];
  }
  if (state.tab === "assets" && ctx.assets) {
    const { assets } = ctx;
    const at = assets.rec;
    const moved = periodChange(assets, assets.net);
    const pace = moved && [returnText(moved.rate), moved.monthly === null ? "" : `한 달 평균 ${signedLabel(moved.monthly)}`].filter(Boolean).join(" · ");
    return [
      ["순자산", won(assets.net[at]), `${dotDate(assets.days[at])} 기록`],
      ["자산", won(assets.assets[at])],
      ["부채", won(assets.debt[at])],
      moved ? [`${dotDate(moved.from)}부터`, signedWon(moved.change), pace] : ["기간 변화", "-"],
    ];
  }
  if (state.tab === "finance" && ctx.finance) {
    const { finance } = ctx;
    const point = finance.valuation[finance.rec] === null ? null : { valuation: finance.valuation[finance.rec], principal: finance.principal[finance.rec] };
    const rate = point ? investmentReturn(point) : null;
    return [
      ["금융 순자산", won(finance.net), `${dotDate(finance.days[finance.rec])} 기록`],
      ["금융 자산", won(finance.assets)],
      ["대출·부채", won(finance.debt)],
      ["투자 평가 손익", finance.profit === null ? "-" : signedWon(finance.profit), returnText(rate)],
    ];
  }
  return [];
}

function drawPageTiles(view, ctx) {
  const rows = pageTileRows(view, ctx);
  const box = view.tilesBox;
  box.replaceChildren();
  box.hidden = !rows.length;
  box.style.setProperty("--n", rows.length);
  for (const [label, value, small] of rows) {
    const tile = htmlNode("div", "tile", box);
    htmlNode("span", "", tile, label);
    const strong = htmlNode("strong", "", tile, value);
    strong.title = value;
    if (small) htmlNode("small", "", tile, small);
  }
}

function drawFilterRows(view) {
  const { state, data, period } = view;
  const covered = coveredFilters(view);
  const kind = shownKind(state);
  const totals = new Map(DIMENSIONS[state.tab].map((dimension) => [dimension, facetTotals(data, state, period, dimension)]));
  for (const { row, allChip, pickChip, dimension, tabs, shownKeys, allLabel } of view.facetButtons) {
    const shown = shownKeys();
    const count = chosenKeys(state, dimension.state, shown).size;
    row.hidden = !tabs.includes(state.tab) || !shown.length || covered.has(dimension.state); // 고를 것이 없으면 제목만 남으므로 줄째 숨긴다
    if (allLabel) allChip.setAttribute("aria-pressed", includesEverything(state, dimension) ? "true" : "false");
    else allChip.label.textContent = state.allChosen.has(dimension.state) ? "모두 해제" : "모두 선택";
    pickChip.label.textContent = count ? `${count}개 선택됨` : "고르기";
    pickChip.classList.toggle("has-choice", count > 0);
  }
  for (const entry of view.facetChips) {
    const shown = entry.tabs.includes(state.tab) && fitsShownKind(entry.categoryKind, kind);
    entry.chip.hidden = !shown;
    if (entry.divider) entry.divider.hidden = !shown || Boolean(kind);
    if (!shown) continue;
    const pressed = entry.pressed() || state.allChosen.has(entry.dimension.state);
    entry.chip.setAttribute("aria-pressed", pressed ? "true" : "false");
    const total = totals.get(entry.dimension).get(entry.key);
    entry.chip.amount.textContent = chipAmount(total);
    entry.chip.classList.toggle("dim", !total && !pressed);
  }
  view.clear.hidden = !hasFilters(state);
  // 왼쪽 목록이 조건을 모두 대신하면 빈자리를 남기지 않는다.
  view.filters.hidden = view.clear.hidden && view.facetButtons.every((entry) => entry.row.hidden);
}

function addWidgetCard(view, ctx, parent, item, place) {
  const widget = view.catalog.get(item.key);
  const section = htmlNode("section", "card widget chart-card", parent);
  section.dataset.widget = item.key;
  if (place.span) section.style.setProperty("--span", place.span);
  if (place.spanM) section.style.setProperty("--span-m", place.spanM);
  const head = htmlNode("div", "band-head widget-head", section);
  const title = htmlNode("h2", "", head, widget.title);
  const body = htmlNode("div", "widget-body", section);
  drawWidget(widget, ctx, cardApi(section, head, title, body, item.span));
}

/* 왼쪽 목록이 있으면 그 옆에, 없으면 12칸 격자에 놓는다. */
function drawWidgetGrid(view, ctx) {
  const { items } = view.layouts[view.state.tab];
  // 항목을 누를 때마다 다시 그리므로 목록이 있던 자리를 기억해 둔다.
  const listAcross = view.grid.querySelector(".b-list")?.scrollLeft || 0;
  const listDown = view.grid.querySelector(".b-side .widget-body")?.scrollTop || 0;
  resetFigures();
  view.grid.replaceChildren();
  if (!items.length) {
    noteText(view.grid, "넣은 위젯이 없습니다.");
    return;
  }
  const sideItem = items.find((item) => view.catalog.get(item.key).side);
  const mainItems = items.filter((item) => item !== sideItem);
  let main = view.grid;
  if (sideItem) {
    const wrap = htmlNode("div", "b-wrap", view.grid);
    addWidgetCard(view, ctx, htmlNode("div", "b-side", wrap), sideItem, { index: 0, count: 1 });
    const list = wrap.querySelector(".b-list");
    if (list) list.scrollLeft = listAcross;
    const body = wrap.querySelector(".b-side .widget-body");
    if (body) body.scrollTop = listDown;
    main = wrap;
  }
  const grid = htmlNode("div", "g12", main);
  const spans = fillRows(mainItems, "span");
  const spansM = fillRows(mainItems, "span_m");
  mainItems.forEach((item, index) => {
    addWidgetCard(view, ctx, grid, item, { span: spans[index], spanM: spansM[index], index, count: mainItems.length });
  });
}


/* --- 위젯 화면. 테마를 누르면 그 테마의 위젯이 골라지고, 그림을 누를 때마다 다음 순서로 골라진다.
   다시 누르면 빠진다. 적용을 눌러야 저장하고, 그림은 지금 데이터로 그린 실제 위젯을 줄인 것이다. */

const PREVIEW_WIDTH = 640; // 미리 보기를 그리는 폭. 카드 폭에 맞춰 줄인다

function sameOrder(a, b) {
  return a.length === b.length && a.every((key, index) => key === b[index]);
}

function buildWidgetPage(view) {
  const dialog = htmlNode("dialog", "picker widget-page", document.body);
  dialog.setAttribute("aria-labelledby", "widget-page-title");
  const head = htmlNode("div", "picker-head", dialog);
  const title = htmlNode("h2", "", head);
  title.id = "widget-page-title";
  const count = htmlNode("p", "picker-count", head);
  count.setAttribute("aria-live", "polite");
  const body = htmlNode("div", "wp-body", dialog);
  htmlNode("h3", "wp-section", body, "테마");
  const themes = htmlNode("div", "wp-themes", body);
  htmlNode("h3", "wp-section", body, "위젯");
  const grid = htmlNode("div", "wp-grid", body);
  const foot = htmlNode("div", "picker-foot", dialog);
  const cancel = htmlNode("button", "button", foot, "취소");
  cancel.type = "button";
  const apply = htmlNode("button", "button primary", foot, "적용");
  apply.type = "button";
  const error = htmlNode("p", "wp-error", foot, "저장하지 못했습니다. 다시 눌러 주세요.");
  error.hidden = true;
  // 카드 폭에 맞춰 미리 보기를 줄인다.
  const resize = new ResizeObserver((entries) => {
    for (const entry of entries) entry.target.style.setProperty("--wp-scale", entry.contentRect.width / PREVIEW_WIDTH);
  });
  view.widgetPage = { dialog, title, count, themes, grid, apply, error, resize, cards: new Map(), themeButtons: [], chosen: [], base: null };
  cancel.addEventListener("click", () => closeWidgetPage(view));
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault(); // Esc도 취소와 같다
    closeWidgetPage(view);
  });
  apply.addEventListener("click", () => applyWidgetPage(view));
}

function openWidgetPage(view) {
  const page = view.widgetPage;
  const layout = view.layouts[view.state.tab];
  page.base = layout.theme;
  page.chosen = layout.items.map((item) => item.key);
  page.error.hidden = true;
  page.title.textContent = `${TABS.find((tab) => tab.key === view.state.tab).name} 위젯`;
  page.dialog.showModal(); // 먼저 열어야 미리 보기의 폭을 잴 수 있다
  drawWidgetPageThemes(view);
  drawWidgetPageCards(view);
  markWidgetPage(view);
}

function closeWidgetPage(view) {
  const page = view.widgetPage;
  view.state.editing = false;
  page.resize.disconnect();
  page.grid.replaceChildren(); // 미리 보기는 열 때마다 지금 데이터로 다시 그린다
  page.cards.clear();
  if (page.dialog.open) page.dialog.close();
  view.update();
}

function drawWidgetPageThemes(view) {
  const { data, state, widgetPage: page } = view;
  page.themes.replaceChildren();
  const items = data.widgets.theme_items[state.tab];
  page.themeButtons = data.widgets.themes.map((theme) => {
    const keys = items[theme.key];
    const button = htmlNode("button", "wp-theme", page.themes);
    button.type = "button";
    htmlNode("strong", "", button, `${theme.key} ${theme.name}`);
    htmlNode("small", "", button, keys.map((key) => view.catalog.get(key).title).join(" · "));
    button.addEventListener("click", () => {
      page.chosen = [...keys];
      page.base = theme.key;
      markWidgetPage(view);
    });
    return { button, keys };
  });
}

function drawWidgetPageCards(view) {
  const { data, state, widgetPage: page } = view;
  page.resize.disconnect();
  page.grid.replaceChildren();
  page.cards.clear();
  // 차트 모양 고르기는 화면에 하나뿐이라 미리 보기에는 설명만 둔다.
  const ctx = { ...pageContext(view), freeChart: (card) => noteText(card.body, "기간과 모양을 직접 골라 보는 차트") };
  for (const widget of data.widgets.catalog.filter((item) => item.tab === state.tab)) {
    const card = htmlNode("div", "wp-card", page.grid);
    const preview = htmlNode("div", "wp-preview", card);
    preview.inert = true; // 그림일 뿐이라 누르거나 고를 수 없다
    const stage = htmlNode("div", "wp-stage", preview);
    const section = htmlNode("section", "card widget chart-card", stage);
    const head = htmlNode("div", "band-head widget-head", section);
    const title = htmlNode("h2", "", head, widget.title);
    const body = htmlNode("div", "widget-body", section);
    drawWidget(widget, ctx, cardApi(section, head, title, body, 12));
    const mark = htmlNode("span", "wp-mark", card);
    const order = htmlNode("strong", "", mark);
    htmlNode("span", "", mark, "선택됨");
    htmlNode("span", "wp-name", card, widget.title);
    const hit = htmlNode("button", "wp-hit", card);
    hit.type = "button";
    hit.addEventListener("click", () => {
      const at = page.chosen.indexOf(widget.key);
      if (at >= 0) page.chosen.splice(at, 1);
      else page.chosen.push(widget.key);
      markWidgetPage(view);
    });
    page.resize.observe(preview);
    page.cards.set(widget.key, { card, order, hit, title: widget.title });
  }
}

/* 고른 위젯에 순서와 선택됨을 붙인다. 고른 것이 어느 테마와 같으면 그 테마가 눌린다. */
function markWidgetPage(view) {
  const page = view.widgetPage;
  for (const [key, entry] of page.cards) {
    const at = page.chosen.indexOf(key);
    entry.card.classList.toggle("is-on", at >= 0);
    entry.order.textContent = at >= 0 ? String(at + 1) : "";
    entry.hit.setAttribute("aria-pressed", at >= 0 ? "true" : "false");
    entry.hit.setAttribute("aria-label", at >= 0 ? `${entry.title}, ${at + 1}번째로 선택됨` : `${entry.title} 고르기`);
  }
  for (const { button, keys } of page.themeButtons) button.setAttribute("aria-pressed", sameOrder(keys, page.chosen) ? "true" : "false");
  page.count.textContent = page.chosen.length ? `${page.chosen.length}개 선택` : "고른 위젯이 없습니다";
}

async function applyWidgetPage(view) {
  const { data, state, widgetPage: page } = view;
  const themes = data.widgets.theme_items[state.tab];
  const matched = Object.keys(themes).find((theme) => sameOrder(themes[theme], page.chosen));
  page.apply.disabled = true;
  const saved = await saveLayout(view, { action: "set", theme: matched || page.base, keys: page.chosen });
  page.apply.disabled = false;
  if (!saved) {
    page.error.hidden = false;
    return;
  }
  closeWidgetPage(view);
}

function pageContext(view) {
  const { state } = view;
  const ctx = figuresFor(view.data, state, state.tab);
  ctx.cache = view.cache;
  ctx.pickMonth = (month) => {
    state.month = month;
    view.update();
  };
  ctx.pickRecord = (day) => {
    state.record = day;
    view.update();
  };
  ctx.pickFocus = (key) => {
    state.focus = state.focus === key ? null : key;
    view.update();
  };
  ctx.holdFocus = (key) => {
    state.focus = key;
    view.update();
  };
  ctx.pickCell = (month, key) => {
    state.month = month;
    state.focus = key;
    view.update();
  };
  ctx.freeChart = (card) => {
    view.free = view.free || createFreeChart(view.data, state, view.update);
    card.body.appendChild(view.free.root);
    view.free.draw(card);
  };
  return ctx;
}

function drawPage(view) {
  const { data, state } = view;
  const focused = document.activeElement?.dataset?.mark;
  const scrolled = window.scrollY;
  view.period = pagePeriod(data, state);
  const query = stateToQuery(state);
  window.history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
  document.title = `${TABS.find((tab) => tab.key === state.tab).name} · 가계부`;
  hideTip();
  const ctx = pageContext(view);
  drawPageControls(view, ctx);
  drawFilterRows(view);
  drawPageTiles(view, ctx);
  drawWidgetGrid(view, ctx);
  if (state.editing && !view.widgetPage.dialog.open) openWidgetPage(view);
  // 다시 그리는 동안 화면이 잠깐 짧아져도 보던 자리에 머문다.
  window.scrollTo({ top: scrolled, behavior: "instant" });
  if (focused) [...view.app.querySelectorAll("[data-mark]")].find((node) => node.dataset.mark === focused)?.focus({ preventScroll: true });
  restoreTouchedTip(view.app);
}

function start(app) {
  const state = stateFromQuery(window.location.search);
  const view = {
    app,
    state,
    controls: app.querySelector("[data-controls]"),
    filters: app.querySelector("[data-filters]"),
    tilesBox: app.querySelector("[data-tiles]"),
    grid: app.querySelector("[data-widgets]"),
    csrf: app.dataset.csrf,
    cache: new Map(),
  };
  view.update = () => drawPage(view);
  installTips();
  fetch(app.dataset.url, { headers: { Accept: "application/json" } })
    .then((response) => {
      if (response.status === 401) {
        window.location.assign("/auth/login");
        return null;
      }
      if (!response.ok) throw new Error(String(response.status));
      return response.json();
    })
    .then((raw) => {
      if (!raw) return;
      view.data = normalize(raw);
      view.catalog = new Map(view.data.widgets.catalog.map((widget) => [widget.key, widget]));
      view.layouts = view.data.widgets.layouts;
      dropCoveredFilters(view);
      buildPicker(view);
      buildFilters(view);
      buildWidgetPage(view);
      buildNavigation(view);
      view.update();
      // 달의 수와 칸의 글자가 폭에 따라 달라지므로 휴대폰 폭을 넘나들면 다시 놓는다.
      let narrow = window.innerWidth < NARROW_WIDTH;
      window.addEventListener("resize", () => {
        const now = window.innerWidth < NARROW_WIDTH;
        if (now !== narrow) {
          narrow = now;
          view.update();
        }
      });
      // 글꼴이 차트보다 늦게 도착하면 글자 폭이 달라지므로 칸에 들어가는지 다시 잰다.
      document.fonts?.addEventListener("loadingdone", view.update);
    })
    .catch((error) => {
      console.error(error);
      htmlNode("p", "notice error", view.grid, "데이터를 불러오지 못했습니다. 새로고침해 주세요.");
    });
}

const chartApp = document.getElementById("chart-app");
if (chartApp) start(chartApp);
