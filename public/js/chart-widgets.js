/* 차트 위젯. 탭 화면과 홈이 같은 코드로 그린다. 계산은 widget-data.js, 그림은 chart-marks.js에 있다.
   어떤 위젯이 있는지와 놓인 순서는 서버(app/chart_widgets.py)가 정한다. 여기에는 그리는 법만 있다. */
"use strict";

const NARROW_WIDTH = 720;

/* --- 작은 조각 */

function swatch(parent, fill) {
  const node = htmlNode("i", "sw", parent);
  node.style.setProperty("--c", fill);
  return node;
}

function noteText(parent, text) {
  return htmlNode("p", "as-of", parent, text);
}

function legendRow(parent, items) {
  const box = htmlNode("div", "lg", parent);
  for (const item of items) {
    const node = htmlNode(item.pick ? "button" : "span", "", box);
    if (item.pick) {
      node.type = "button";
      node.setAttribute("aria-pressed", item.pressed ? "true" : "false");
      node.dataset.mark = `legend:${item.key}`;
      node.addEventListener("click", item.pick);
    }
    swatch(node, item.fill);
    htmlNode("span", "", node, item.name);
    if (item.note) htmlNode("small", "", node, item.note);
  }
  return box;
}

function sharedTip(item, total) {
  return [
    { fill: item.fill, name: "금액", value: won(item.value) },
    { name: "비중", value: percentText(item.value / total) },
  ];
}

function segmentLabel(items, total) {
  return items.map((item) => `${item.name} ${percentText(item.value / total)}`).join(", ");
}

/* 얇은 분할 막대. */
function thinSegments(parent, items, total) {
  const box = htmlNode("div", "sg", parent);
  box.setAttribute("role", "img");
  box.setAttribute("aria-label", segmentLabel(items, total));
  for (const item of items.filter((entry) => entry.value > 0)) {
    const part = htmlNode("i", "", box);
    part.style.flex = `${item.value} 1 0`;
    part.style.setProperty("--c", item.fill);
    setTip(part, item.name, sharedTip(item, total));
  }
}

/* 큰 분할 막대. 칸에 들어가는 값만 적는다. */
function tallSegments(parent, items, total, narrow) {
  const box = htmlNode("div", "tall", parent);
  const shown = items.filter((entry) => entry.value > 0);
  box.setAttribute("role", "img");
  box.setAttribute("aria-label", segmentLabel(shown.filter((entry) => !entry.spacer), total));
  for (const item of shown) {
    const part = htmlNode("i", item.spacer ? "is-empty" : "", box);
    part.style.flex = `${item.value} 1 0`;
    if (item.spacer) continue;
    part.style.setProperty("--c", item.fill);
    part.style.setProperty("--on", item.ink);
    setTip(part, item.name, sharedTip(item, total));
    if (item.value / total >= (narrow ? 0.16 : 0.08)) part.textContent = item.text || labelWon(item.value);
    if (part.scrollWidth > part.clientWidth) part.textContent = "";
  }
  return box;
}

function rowsList(parent, rows, tight) {
  const list = htmlNode("ul", `rows w-rows${tight ? " rows-tight" : ""}`, parent);
  for (const row of rows) {
    const line = htmlNode("li", "", list);
    const label = htmlNode("span", "l", line);
    if (row.fill) swatch(label, row.fill);
    const name = htmlNode("span", "", label);
    if (row.href) htmlNode("a", "", name, row.name).href = row.href;
    else name.textContent = row.name;
    if (row.hint) htmlNode("small", "", name, row.hint);
    if (row.side) row.side(line);
    htmlNode("span", "num", line, row.value);
    if (row.percent) htmlNode("span", "pct", line, row.percent);
  }
  return list;
}

/* 가운데 기준선에서 양쪽으로 뻗는 막대. 늘어난 쪽이 오른쪽이다. */
function divergeRows(parent, items, ctx) {
  const { unit, selected } = ctx.flow.axis;
  const largest = Math.max(1, ...items.map((item) => Math.abs(item.change)));
  const room = ctx.narrow ? 24 : 32;
  const head = htmlNode("div", "dv-head", parent);
  htmlNode("span", "", head, "줄었음");
  htmlNode("span", "", head, "늘었음");
  const box = htmlNode("div", "dv", parent);
  for (const item of items) {
    const row = htmlNode("button", "dv-row", box);
    row.type = "button";
    row.dataset.mark = `focus:${item.key}`;
    row.setAttribute("aria-pressed", ctx.state.focus === item.key ? "true" : "false");
    row.addEventListener("click", () => ctx.pickFocus(item.key));
    setTip(row, item.name, [{ name: curveName(unit, selected), value: won(item.value) }, { name: `${STEP_NAMES[unit]}보다`, value: signedWon(item.change) }]);
    htmlNode("span", "dv-name", row, item.name);
    const track = htmlNode("span", "dv-track", row);
    const size = (Math.abs(item.change) / largest) * room;
    const up = item.change >= 0;
    const bar = htmlNode("i", `dv-bar ${up ? "up" : "down"}`, track);
    bar.style.width = `${size}%`;
    bar.style.setProperty("--c", up ? "var(--slot-2)" : "var(--slot-1)");
    const value = htmlNode("span", "dv-val", track, signedLabel(item.change));
    if (up) value.style.left = `calc(50% + ${size}% + 5px)`;
    else value.style.right = `calc(50% + ${size}% + 5px)`;
  }
}

function paymentBlock(ctx, body, month, category) {
  const pay = paymentFigures(ctx.data, ctx.flow, month, category);
  if (!pay.total) {
    noteText(body, `이 ${UNIT_WORDS[ctx.flow.axis.unit]}의 지출이 없습니다.`);
    return;
  }
  const channels = pay.channels.map((channel) => ({ ...channel, fill: slotFill(channel.slot) }));
  thinSegments(body, channels, pay.total);
  legendRow(body, channels.map((channel) => ({ ...channel, note: percentText(channel.value / pay.total) })));
  rowsList(
    body,
    pay.instruments.map((item) => ({ name: item.name, fill: slotFill(pay.slotOf(item.channel)), value: won(item.value) })),
    true,
  );
}

/* 왼쪽 목록의 한 줄. */
function listRow(parent, { pressed, fill, name, value, left, right, pick, mark }) {
  const row = htmlNode("button", "b-row", parent);
  row.type = "button";
  row.dataset.mark = mark;
  row.setAttribute("aria-pressed", pressed ? "true" : "false");
  row.addEventListener("click", pick);
  const label = htmlNode("span", "b-name", row);
  if (fill) swatch(label, fill);
  htmlNode("span", "", label, name);
  htmlNode("span", "num", row, value);
  const leftBox = htmlNode("span", "", row);
  if (left) left(leftBox);
  htmlNode("span", "b-sub", row, right || "");
}

function listGroup(parent, name, value) {
  const head = htmlNode("div", "b-group", parent);
  htmlNode("span", "", head, name);
  htmlNode("span", "num", head, value);
}

/* 누를 수 있는 묶음 제목. 누르면 묶음 전체를 본다. */
function groupRow(parent, group, ctx) {
  const row = htmlNode("button", "b-group", parent);
  row.type = "button";
  row.dataset.mark = `focus:${group.key}`;
  row.setAttribute("aria-pressed", ctx.state.focus === group.key ? "true" : "false");
  row.addEventListener("click", () => ctx.holdFocus(group.key));
  htmlNode("span", "", row, group.name);
  htmlNode("span", "num", row, won(group.value));
}

function heroLine(parent, amount, parts) {
  const box = htmlNode("div", "b-hero", parent);
  htmlNode("strong", "", box, amount);
  for (const part of parts.filter(Boolean)) htmlNode("span", "", box, part);
}

function statBoxes(parent, rows) {
  const box = htmlNode("div", "stats", parent);
  box.style.setProperty("--n", rows.length);
  for (const row of rows) {
    const cell = htmlNode("div", "stat", box);
    htmlNode("span", "", cell, row.label);
    htmlNode("strong", "", cell, row.value);
    if (row.note) htmlNode("small", "", cell, row.note);
  }
  return box;
}

function meterRow(parent, name, share, color) {
  const box = htmlNode("div", "meter-row", parent);
  box.style.setProperty("--c", color);
  const head = htmlNode("div", "loan-f", box);
  htmlNode("span", "", head, name);
  htmlNode("span", "", head, `${Math.round(share * 100)}%`);
  const meter = htmlNode("div", "meter", box);
  htmlNode("i", "", meter).style.width = `${share * 100}%`;
}

function monthlyInterest(loan) {
  return loan.balance !== null && loan.rate !== null ? Math.round((loan.balance * loan.rate) / 100 / 12) : null;
}

/* 마지막 달의 이자. 납입을 이자와 원금으로 나눴으면 그 값이고, 아니면 잔액과 금리로 어림한다. */
function latestInterest(loan) {
  const last = loan.months[loan.months.length - 1];
  if (last) return { value: last.interest, exact: last.exact, note: `${monthTick(last.month)} 납입${last.exact ? "" : " · 어림"}` };
  const value = monthlyInterest(loan);
  return value === null ? null : { value, exact: false, note: "잔액×금리÷12" };
}

function interestText(interest) {
  return interest.exact ? won(interest.value) : `약 ${labelWon(interest.value)}원`;
}

function repaidShare(loan) {
  return loan.principal && loan.balance !== null ? Math.max(0, Math.min(1, (loan.principal - loan.balance) / loan.principal)) : null;
}

function rateText(loan) {
  if (loan.rate === null) return "";
  return loan.rateSource === "payments" ? `약 ${loan.rate}%` : `${loan.rate}%`;
}

const RATE_REASONS = {
  records: "잔액 기록이 두 번 쌓이면 계산합니다",
  payments: "이 기간의 납입이 없습니다",
  range: "납입액으로 계산할 수 없습니다",
};

/* 금리 칸의 작은 글자: 어디서 온 값인지, 표와 납입액이 어떻게 다른지, 왜 비었는지. */
function rateNote(loan) {
  if (loan.rateSource === "payments") return "납입액으로 계산";
  if (loan.rate !== null && loan.impliedRate !== null) return `납입액 기준 ${loan.impliedRate}%`;
  if (loan.rate !== null) return "";
  if (!loan.payments.length) return "납입을 연결하면 계산합니다";
  return RATE_REASONS[loan.reason] || "";
}

function linkedPayments(parent, loan) {
  if (loan.payments.length) {
    const hint = (item) => ["연결된 납입", `${item.times}회`, item.kind].filter(Boolean).join(" · ");
    rowsList(parent, loan.payments.map((item) => ({ name: item.name, hint: hint(item), value: won(item.typical) })), true);
    return;
  }
  // 표에 금리가 있으면 연결하지 않아도 되므로 묻지 않는다.
  if (loan.rate !== null || !loan.key.startsWith("liability:")) return;
  const line = noteText(parent, "");
  htmlNode("a", "", line, "정기 납입 연결하기").href = `/recurring?loan=${encodeURIComponent(loan.key)}`;
}

/* 달마다 이자. 나눈 납입에서 지출로 잡힌 금액이다. */
function interestMonths(parent, loan) {
  if (!loan.months.length) return;
  const head = htmlNode("div", "loan-f", parent);
  htmlNode("span", "", head, "달마다 이자");
  htmlNode("span", "", head, loan.months.every((item) => item.exact) ? "" : "어림 포함");
  const months = loan.months.map((item) => item.month);
  const values = loan.months.map((item) => item.interest);
  figure(parent, 150, (host, width, height) => {
    monthColumns(host, width, height, { months, values, selected: months[months.length - 1], name: "이자" });
  });
}

/* 대출 한 건. 현황 표에 없는 값은 적지 않는다. */
function loanBlock(parent, loan) {
  const box = htmlNode("div", "loan", parent);
  box.style.setProperty("--c", "var(--accent)");
  const head = htmlNode("div", "loan-h", box);
  htmlNode("span", "", head, loan.name);
  htmlNode("span", "num", head, loan.balance === null ? "-" : won(loan.balance));
  const interest = latestInterest(loan);
  const parts = [loan.org, loan.rate === null ? "" : `금리 ${rateText(loan)}`, interest === null ? "" : `월 이자 ${interestText(interest)}`];
  htmlNode("div", "loan-s", box, parts.filter(Boolean).join(" · "));
  const repaid = repaidShare(loan);
  if (repaid !== null) htmlNode("i", "", htmlNode("div", "meter", box)).style.width = `${repaid * 100}%`;
  const foot = htmlNode("div", "loan-f", box);
  htmlNode("span", "", foot, repaid === null ? "" : `상환 ${Math.round(repaid * 100)}%`);
  htmlNode("span", "", foot, loan.end ? `만기 ${dotDate(loan.end)}` : "");
  setTip(box, loan.name, [
    ...(loan.principal === null ? [] : [{ name: "원금", value: won(loan.principal) }]),
    ...(loan.balance === null ? [] : [{ name: "잔액", value: won(loan.balance) }]),
    ...(interest === null ? [] : [{ name: interest.exact ? "월 이자" : "월 이자(어림)", value: won(interest.value) }]),
  ]);
}

/* 기록마다 값을 잇는 선. 끝값은 마지막 기록이다. pick이 있으면 눌러서 기록을 고른다. */
function recordLine(body, days, values, { name, color, height = 220, dots = true, selected = null, pick = null }) {
  const series = [{ name, values, color, wash: true, label: labelWon(values[values.length - 1]), dotR: days.length < 4 ? 4 : 3 }];
  const pickIndex = pick && ((index) => pick(days[index]));
  figure(body, height, (host, width, chartHeight) => {
    lineChart(host, width, chartHeight, { series, labels: days.map(dotDate), pos: datePositions(days), dots, name, selected, pick: pickIndex });
  });
}

/* 마지막이 아닌 기록을 골랐을 때 그 날을 적는다. */
function recordNote(figures) {
  return figures.rec === figures.days.length - 1 ? "" : `${dotDate(figures.days[figures.rec])} 기록`;
}

function gainLine(body, days, valuation, principal, { height, tickCount = 3, dots = false }) {
  const shown = days.map((_, index) => index).filter((index) => valuation[index] !== null);
  if (!shown.length) {
    noteText(body, "투자 현황 기록이 없습니다.");
    return;
  }
  const upper = shown.map((index) => valuation[index]);
  const lower = shown.map((index) => principal[index]);
  const series = [
    { name: "평가금액", values: upper, color: "var(--accent)", label: labelWon(upper[upper.length - 1]) },
    { name: "원금", values: lower, color: "var(--bar-muted)", label: labelWon(lower[lower.length - 1]) },
  ];
  const labels = shown.map((index) => dotDate(days[index]));
  figure(body, height, (host, width, chartHeight) => {
    lineChart(host, width, chartHeight, { series, labels, between: [upper, lower, "var(--accent)"], tickCount, dots: dots || shown.length < 4, name: "평가금액과 원금" });
  });
}

function dumbbellItems(finance) {
  return finance.investments
    .filter((row) => row.principal !== null && row.valuation !== null)
    .map((row) => ({ key: `investment:${row.name}`, name: row.name, principal: row.principal, valuation: row.valuation }));
}

/* --- 카테고리 */

function drawCategoryMatrix(ctx, card) {
  const { flow, state, narrow } = ctx;
  const { unit, selected } = flow.axis;
  const months = narrow ? flow.axis.months.slice(-NARROW_MONTHS) : flow.axis.months;
  card.setTitle(`카테고리 × ${UNIT_LETTERS[unit]}`);
  card.setRight(`${months.length}${UNIT_COUNTS[unit]}`);
  const rows = matrixRows(flow, months);
  if (!rows.length) {
    noteText(card.body, "이 기간의 지출이 없습니다.");
    return;
  }
  const totals = months.map((month) => flow.spend(month));
  const peak = Math.max(1, ...totals);
  const grid = htmlNode("div", "mx", card.body);
  grid.style.setProperty("--n", months.length);
  htmlNode("span", "mx-h static", grid);
  for (const month of months) {
    const head = htmlNode("button", `mx-h${month === selected ? " on" : ""}`, grid, bucketTick(unit, month));
    head.type = "button";
    head.dataset.mark = `month:${month}`;
    head.addEventListener("click", () => ctx.pickMonth(month));
  }
  htmlNode("span", "mx-h static total", grid, "합계");
  for (const row of rows) {
    const name = htmlNode("button", "mx-n", grid);
    name.type = "button";
    name.dataset.mark = `focus:${row.key}`;
    name.setAttribute("aria-pressed", state.focus === row.key ? "true" : "false");
    name.addEventListener("click", () => ctx.pickFocus(row.key));
    swatch(name, solidFill(row.slot));
    htmlNode("span", "", name, row.name);
    for (const cell of row.cells) {
      const on = cell.month === selected && state.focus === row.key;
      const dim = state.focus && state.focus !== row.key;
      const node = htmlNode("button", `mx-c${on ? " on" : ""}${dim ? " is-dim" : ""}`, grid, matrixCellText(cell.value));
      node.type = "button";
      node.dataset.mark = `cell:${cell.month}:${row.key}`;
      node.style.setProperty("--h", cell.heat.above ? "var(--slot-2)" : "var(--slot-1)");
      node.style.setProperty("--p", `${cell.heat.percent}%`);
      node.setAttribute("aria-label", `${row.name} ${bucketShort(unit, cell.month)} ${won(cell.value)}`);
      setTip(node, `${row.name} ${bucketShort(unit, cell.month)}`, [
        { name: "금액", value: won(cell.value) },
        { name: "평균보다", value: signedWon(cell.value - Math.round(row.average)) },
      ]);
      node.addEventListener("click", () => ctx.pickCell(cell.month, row.key));
    }
    htmlNode("span", "mx-t", grid, labelWon(row.total));
  }
  const totalName = htmlNode("span", "mx-n is-total", grid, "합계");
  totalName.setAttribute("aria-hidden", "true");
  months.forEach((month, index) => {
    const on = month === selected;
    const bar = htmlNode("button", `mx-bar${on ? " on" : ""}`, grid);
    bar.type = "button";
    bar.dataset.mark = `total:${month}`;
    bar.setAttribute("aria-label", `${bucketTitle(unit, month)} 지출 ${won(totals[index])}`);
    setTip(bar, bucketTitle(unit, month), [{ name: "지출", value: won(totals[index]) }]);
    bar.addEventListener("click", () => ctx.pickMonth(month));
    if (on) htmlNode("b", "", bar, labelWon(totals[index]));
    htmlNode("i", "", bar).style.height = `${(Math.max(totals[index], 0) / peak) * 100}%`;
  });
  htmlNode("span", "mx-t", grid, labelWon(sumOf(totals, (value) => value)));
  const legend = htmlNode("div", "mx-legend", card.body);
  htmlNode("span", "", legend, "평균보다 적음");
  for (const [hue, percent] of [["slot-1", 62], ["slot-1", 36], ["slot-1", 12], [null, 0], ["slot-2", 12], ["slot-2", 36], ["slot-2", 62]]) {
    const key = htmlNode("i", hue ? "" : "is-even", legend);
    if (hue) key.style.background = `color-mix(in srgb, var(--${hue}) ${percent}%, var(--bg))`;
  }
  htmlNode("span", "", legend, "많음");
  htmlNode("span", "mx-unit", legend, "단위 만원");
}

function focusedCategory(ctx) {
  return categoryOf(ctx.flow, ctx.state.focus);
}

/* 고른 칸 안의 날마다. 주간은 그 주의 7일, 연간은 그해의 달마다, 일간과 월간은 그 달의 날마다다. */
function drawDaily(ctx, card, category) {
  const focus = category && focusedCategory(ctx);
  const fill = focus ? solidFill(focus.slot) : "var(--accent)";
  const suffix = focus ? ` ${focus.name}` : "";
  const height = card.span >= 8 || card.home ? 190 : 176;
  const { unit, selected } = ctx.flow.axis;
  if (unit === "week" || unit === "year") {
    const { days, values } = bucketDailyAmounts(ctx.flow, unit, selected, focus?.key);
    card.setRight(`${curveName(unit, selected)}${suffix}`);
    if (unit === "week") {
      const labels = days.map((day, index) => `${bucketShort("day", day)} ${MONDAY_FIRST[index]}요일`);
      figure(card.body, height, (host, width, chartHeight) => dayColumns(host, width, chartHeight, { values, labels, xTicks: MONDAY_FIRST, fill }));
      return;
    }
    card.setTitle("월별");
    const months = Array(12).fill(0);
    days.forEach((day, index) => {
      months[Number(day.slice(5, 7)) - 1] += values[index];
    });
    const names = months.map((_, index) => `${index + 1}월`);
    figure(card.body, height, (host, width, chartHeight) => {
      dayColumns(host, width, chartHeight, { values: months, labels: names.map((name) => `${selected}년 ${name}`), xTicks: names, fill, name: "월별 지출" });
    });
    return;
  }
  const month = ctx.monthFlow.axis.selected;
  card.setRight(`${monthTick(month)}${suffix}`);
  const values = dailyAmounts(ctx.monthFlow, month, focus?.key);
  figure(card.body, height, (host, width, chartHeight) => {
    dayColumns(host, width, chartHeight, { values, title: monthTick(month), fill });
  });
}

function drawCategoryWeekday(ctx, card) {
  const focus = focusedCategory(ctx);
  card.setRight(focus ? focus.name : "하루 평균");
  const values = weekdayAverages(ctx.monthFlow, ctx.monthFlow.axis.months, focus?.key);
  figure(card.body, card.span === 3 ? 232 : 190, (host, width, height) => weekdayColumns(host, width, height, { values }));
}

function drawCategoryTreemap(ctx, card) {
  const month = ctx.flow.axis.selected;
  const items = categoryRank(ctx.flow, month);
  if (!items.length) {
    noteText(card.body, `이 ${UNIT_WORDS[ctx.flow.axis.unit]}의 지출이 없습니다.`);
    return;
  }
  const total = sumOf(items, (item) => item.value);
  figure(card.body, 318, (host, width, height) => {
    treemapChart(host, width, height, { items, focus: ctx.state.focus, total, pick: (key) => ctx.pickFocus(key) });
  });
}

function drawCategoryStack(ctx, card) {
  const { flow, state } = ctx;
  const { months, unit } = flow.axis;
  card.setTitle(`${UNIT_NAMES[unit]} 카테고리`);
  const series = categorySeriesOf(flow, state.focus);
  legendRow(
    card.body,
    series.map((item) => ({
      key: item.key,
      name: item.name,
      fill: slotFill(item.slot),
      pressed: state.focus === item.key,
      pick: item.key === REST ? null : () => ctx.pickFocus(item.key),
    })),
  );
  const focus = focusedCategory(ctx);
  const average = focus ? null : monthlyAverage(flow, months, (month) => flow.spend(month));
  figure(card.body, 268, (host, width, height) => {
    stackColumns(host, width, height, { months, unit, series, selected: flow.axis.selected, focus: focus?.key, average, pick: ctx.pickMonth });
  });
}

function drawCategoryChanges(ctx, card) {
  const { flow } = ctx;
  const { unit } = flow.axis;
  card.setTitle(`${STEP_NAMES[unit]}보다`);
  const changes = monthlyChanges(flow, flow.axis.selected, flow.axis.previous);
  if (!flow.axis.previous) noteText(card.body, `이전 ${UNIT_WORDS[unit]} 기록이 없습니다`);
  else if (!changes.length) noteText(card.body, `${withParticle(STEP_NAMES[unit], "과", "와")} 같습니다`);
  else divergeRows(card.body, changes, ctx);
}

function drawCategoryPayment(ctx, card) {
  paymentBlock(ctx, card.body, ctx.flow.axis.selected, focusedCategory(ctx)?.key);
}

function changeText(value, previous) {
  return previous !== null && value !== previous ? signedLabel(value - previous) : "";
}

function drawCategoryList(ctx, card) {
  const { flow, state } = ctx;
  const { months, selected, previous, unit } = flow.axis;
  card.setTitle("카테고리");
  card.setRight(bucketShort(unit, selected));
  const box = htmlNode("div", "b-list", card.body);
  const at = months.indexOf(selected);
  const focus = focusedCategory(ctx);
  listRow(box, {
    mark: "focus:all",
    pressed: !focus,
    name: "전체",
    value: won(flow.spend(selected)),
    left: (node) => sparkBars(node, months.map((month) => flow.spend(month)), { selected: at }),
    right: changeText(flow.spend(selected), previous ? flow.spend(previous) : null),
    pick: () => ctx.holdFocus(null),
  });
  const ordered = flow.categories
    .map((category) => ({ ...category, value: flow.spend(selected, category.key) }))
    .filter((category) => category.value !== 0 || months.some((month) => flow.spend(month, category.key)))
    .sort((a, b) => b.value - a.value);
  for (const category of ordered) {
    const fill = solidFill(category.slot);
    listRow(box, {
      mark: `focus:${category.key}`,
      pressed: state.focus === category.key,
      fill,
      name: category.name,
      value: won(category.value),
      left: (node) => sparkBars(node, months.map((month) => flow.spend(month, category.key)), { fill, selected: at }),
      right: changeText(category.value, previous ? flow.spend(previous, category.key) : null),
      pick: () => ctx.holdFocus(category.key),
    });
  }
}

function drawCategoryItem(ctx, card) {
  const { flow } = ctx;
  const { months, selected, previous, unit } = flow.axis;
  const focus = focusedCategory(ctx);
  const key = focus?.key;
  const value = flow.spend(selected, key);
  const total = flow.spend(selected);
  const before = previous ? flow.spend(previous, key) : null;
  const average = monthlyAverage(flow, months, (month) => flow.spend(month, key));
  card.setTitle(focus ? focus.name : "전체 지출");
  card.setRight(focus && total > 0 ? `지출의 ${percentText(value / total)}` : "");
  heroLine(card.body, won(value), [before === null ? "" : `${STEP_NAMES[unit]}보다 ${signedWon(value - before)}`, `${AVERAGE_NAMES[unit]} ${won(average)}`]);
  if (focus) {
    const values = months.map((month) => flow.spend(month, key));
    figure(card.body, 230, (host, width, height) => {
      monthColumns(host, width, height, { months, unit, values, selected, average, fill: solidFill(focus.slot), name: focus.name, pick: ctx.pickMonth });
    });
  } else {
    const series = categorySeriesOf(flow, null);
    figure(card.body, 230, (host, width, height) => {
      stackColumns(host, width, height, { months, unit, series, selected, average, pick: ctx.pickMonth });
    });
  }
}

function drawCategoryPlaces(ctx, card) {
  const { unit, selected } = ctx.flow.axis;
  const span = bucketSpan(unit, selected);
  const focus = focusedCategory(ctx);
  card.setRight(curveName(unit, selected));
  const params = new URLSearchParams({ start: dayKey(span.start), end: dayKey(span.end) });
  const { state } = ctx;
  const categories = focus ? [focus.key] : [...state.categories];
  for (const key of categories) params.append("category", key);
  for (const key of state.channels) params.append("channel", key);
  for (const key of state.instruments) params.append("instrument", key);
  const holder = noteText(card.body, "불러오는 중");
  ctx.fetchJson(`/chart/places?${params}`).then(
    ({ places }) => {
      if (!places.length) {
        holder.textContent = `이 ${UNIT_WORDS[unit]}의 거래가 없습니다`;
        return;
      }
      const total = sumOf(places, (place) => place.amount);
      holder.remove();
      rowsList(
        card.body,
        places.map((place) => ({ name: place.name, hint: `${place.rows}회`, value: won(place.amount), percent: percentText(place.amount / total) })),
      );
    },
    () => {
      holder.textContent = "불러오지 못했습니다";
    },
  );
}

/* --- 수입과 지출 */

function flowLegend(body, withDifference) {
  const items = [{ name: "수입", fill: "var(--slot-1)" }, { name: "지출", fill: "var(--slot-2)" }];
  if (withDifference) items.push({ name: "수입 - 지출", fill: "var(--accent)" });
  legendRow(body, items);
}

function drawFlowPair(ctx, card) {
  const { flow } = ctx;
  const { months, selected, unit } = flow.axis;
  card.setTitle(`${UNIT_NAMES[unit]} 수입·지출`);
  flowLegend(card.body, true);
  const income = months.map((month) => flow.income(month));
  const spend = months.map((month) => flow.spend(month));
  figure(card.body, 318, (host, width, height) => pairColumns(host, width, height, { months, unit, income, spend, selected, pick: ctx.pickMonth }));
}

function dayLabels(count) {
  return Array.from({ length: count }, (_, index) => `${index + 1}일`);
}

function dayTicks(count) {
  return [[0, "1일"], [9, "10일"], [19, "20일"], [count - 1, `${count}일`]];
}

const MONDAY_FIRST = ["월", "화", "수", "목", "금", "토", "일"];
const CURVE_LENGTHS = { week: 7, month: 31, year: 366 };

/* 칸끼리 견줄 때 쓰는 짧은 이름. 10월, 10/26 주, 2026년. */
function curveName(unit, key) {
  if (unit === "year") return `${key}년`;
  if (unit === "month") return monthTick(key);
  return unit === "week" ? `${bucketTick(unit, key)} 주` : bucketTick(unit, key);
}

function curveSource(ctx) {
  return curveSourceOf(ctx.flow, ctx.monthFlow);
}

/* 칸 안의 날을 가리키는 말과 눈금. */
function curveLabels(unit, key, count) {
  if (unit === "week") return MONDAY_FIRST.map((day) => `${day}요일`);
  if (unit !== "year") return dayLabels(count);
  const start = Date.UTC(Number(key), 0, 1);
  return Array.from({ length: count }, (_, index) => {
    const [, month, day] = dayKey(start + index * DAY_MS).split("-").map(Number);
    return `${month}월 ${day}일`;
  });
}

function curveTicks(unit, count) {
  if (unit === "week") return MONDAY_FIRST.map((day, index) => [index, day]);
  if (unit === "year") return [[0, "1월"], [90, "4월"], [181, "7월"], [273, "10월"], [count - 1, "12월"]];
  return dayTicks(count);
}

/* 고른 칸과 그 앞 칸의 누적 지출. 월간이면 이번 달과 지난달, 주간이면 이번 주와 지난주다.
   점선은 기간 안 지난 칸들의 평균이라 기간 칩을 바꾸면 함께 바뀐다. */
function drawFlowCumulative(ctx, card) {
  const { unit, selected, previous, curve, before, count, past, average } = cumulativeFigures(ctx.flow, ctx.monthFlow);
  const name = curveName(unit, selected);
  const beforeName = previous ? curveName(unit, previous) : "";
  const averageName = `${past.length}${UNIT_COUNTS[unit]} 평균`;
  card.setRight([before ? `${withParticle(name, "과", "와")} ${beforeName}` : name, average ? averageName : ""].filter(Boolean).join(" · "));
  const series = [];
  if (average) series.push({ name: averageName, values: average, color: "var(--text-2)", width: 1.5, dash: true, label: `평균 ${labelWon(average[average.length - 1])}` });
  if (before) series.push({ name: beforeName, values: padCurve(before, count), color: "var(--bar-muted)", label: `${beforeName} ${labelWon(lastValue(before) ?? 0)}` });
  series.push({ name, values: padCurve(curve, count), color: "var(--accent)", label: `${name} ${labelWon(lastValue(curve) ?? 0)}` });
  figure(card.body, card.span >= 7 ? 236 : 350, (host, width, height) => {
    lineChart(host, width, height, { series, labels: curveLabels(unit, selected, count), zero: true, name: "누적 지출", xLabels: curveTicks(unit, count) });
  });
}

function drawFlowSankey(ctx, card, height = 318) {
  const sankey = monthSankey(ctx.data, ctx.state, ctx.flow, ctx.flow.axis.selected);
  if (!sankey) {
    noteText(card.body, `이 ${UNIT_WORDS[ctx.flow.axis.unit]}의 기록이 없습니다.`);
    return;
  }
  figure(card.body, height, (host, width, chartHeight) => sankeyChart(host, width, chartHeight, { sankey }));
}

function drawFlowDetail(ctx, card) {
  const { flow } = ctx;
  const month = flow.axis.selected;
  const income = flow.income(month);
  const spend = flow.spend(month);
  card.setTitle(`${bucketName(flow.axis.unit, month, flow.range.last.slice(0, 4))} 돈의 흐름`);
  heroLine(card.body, signedWon(income - spend), [`수입 ${won(income)}`, `지출 ${won(spend)}`, income > 0 ? `수입 대비 지출 ${percentText(spend / income)}` : ""]);
  drawFlowSankey(ctx, card, 300);
}

function drawFlowPayment(ctx, card) {
  paymentBlock(ctx, card.body, ctx.flow.axis.selected, null);
}

function drawFlowMonths(ctx, card) {
  const { flow } = ctx;
  const { months, selected, unit } = flow.axis;
  card.setTitle(UNIT_WORDS[unit]);
  card.setRight(`${months.length}${UNIT_COUNTS[unit]}`);
  const peak = Math.max(1, ...months.map((month) => Math.max(flow.income(month), flow.spend(month))));
  const box = htmlNode("div", "b-list", card.body);
  for (const month of [...months].reverse()) {
    const income = flow.income(month);
    const spend = flow.spend(month);
    listRow(box, {
      mark: `month:${month}`,
      pressed: month === selected,
      name: unit === "month" ? bucketTitle(unit, month) : bucketShort(unit, month),
      value: signedWon(income - spend),
      left: (node) => {
        const bars = htmlNode("span", "mbar", node);
        for (const [value, slot] of [[income, "slot-1"], [spend, "slot-2"]]) {
          const bar = htmlNode("i", "", bars);
          bar.style.width = `${(Math.max(value, 0) / peak) * 100}%`;
          bar.style.setProperty("--c", slotFill(slot));
        }
      },
      right: `지출 ${labelWon(spend)}`,
      pick: () => ctx.pickMonth(month),
    });
  }
}

function drawFlowIncome(ctx, card) {
  const { flow } = ctx;
  const { months, selected } = flow.axis;
  const at = months.indexOf(selected);
  const rows = flow.incomeCategories.filter((category) => months.some((month) => flow.income(month, category.key)));
  if (!rows.length) {
    noteText(card.body, "이 기간의 수입이 없습니다.");
    return;
  }
  rowsList(
    card.body,
    rows.map((category) => ({
      name: category.name,
      value: won(flow.income(selected, category.key)),
      side: (line) => sparkBars(htmlNode("span", "w-spark", line), months.map((month) => flow.income(month, category.key)), { width: 64, height: 18, selected: at }),
    })),
  );
}

function drawFlowMirror(ctx, card) {
  const { flow } = ctx;
  const { months, selected, unit } = flow.axis;
  flowLegend(card.body, false);
  const up = months.map((month) => flow.income(month));
  const down = months.map((month) => flow.spend(month));
  figure(card.body, 300, (host, width, height) => mirrorColumns(host, width, height, { months, unit, up, down, selected, pick: ctx.pickMonth }));
}

function drawFlowNet(ctx, card) {
  const { flow } = ctx;
  const { months, unit } = flow.axis;
  const values = runningNet(flow, months);
  const series = [{ name: "수입 - 지출 누적", values, color: "var(--accent)", wash: true, label: labelWon(values[values.length - 1]) }];
  const labels = months.map((month) => bucketTitle(unit, month));
  const count = months.length;
  figure(card.body, 150, (host, width, height) => {
    lineChart(host, width, height, {
      series,
      labels,
      pos: months.map((_, index) => (count > 1 ? index / (count - 1) : 0.5)),
      zero: true,
      dots: true,
      inside: true,
      tickCount: 2,
      name: "수입 - 지출 누적",
      xLabels: count > 1 ? [[0, bucketTick(unit, months[0])], [count - 1, bucketTick(unit, months[count - 1])]] : [[0, bucketTick(unit, months[0])]],
    });
  });
}

/* 기간 안의 칸마다 누적 지출을 겹친다. 고른 칸만 색을 준다. */
function drawFlowOverlay(ctx, card) {
  const { figures, unit } = curveSource(ctx);
  const { months, selected } = figures.axis;
  card.setTitle("누적 지출");
  card.setRight(`${months.length}${UNIT_COUNTS[unit]} 겹쳐 보기`);
  const count = CURVE_LENGTHS[unit];
  const series = months
    .filter((month) => month !== selected)
    .map((month) => ({ name: curveName(unit, month), values: padCurve(bucketCurve(figures, unit, month), count), color: "var(--bar-muted)", width: 1.25, hideTip: true }));
  const curve = bucketCurve(figures, unit, selected);
  const name = curveName(unit, selected);
  series.push({ name, values: padCurve(curve, count), color: "var(--accent)", width: 2.5, label: `${name} ${labelWon(lastValue(curve) ?? 0)}` });
  figure(card.body, 260, (host, width, height) => {
    lineChart(host, width, height, { series, labels: curveLabels(unit, selected, count), zero: true, name: "누적 지출", xLabels: curveTicks(unit, count) });
  });
}

/* --- 자산 */

function assetRowsNow(assets) {
  const at = assets.rec;
  return assets.groups
    .map((group) => ({ key: group.key, name: group.name, value: group.values[at], fill: solidFill(group.slot), ink: slotInk(group.slot) }))
    .filter((group) => group.value > 0)
    .sort((a, b) => b.value - a.value);
}

/* 같은 길이의 두 막대. 위는 자산, 아래는 부채와 순자산이다. */
function balanceBars(ctx, body) {
  const { assets } = ctx;
  const at = assets.rec;
  const total = Math.max(assets.assets[at], assets.debt[at]);
  const rows = assetRowsNow(assets);
  const bottom = assets.debts.map((debt, order) => ({ key: debt.key, name: debt.name, value: debt.values[at], fill: debtTint(order), ink: debtInk(order) }));
  const net = assets.net[at];
  if (net > 0) bottom.push({ key: "net", name: "순자산", value: net, fill: "var(--accent)", ink: "var(--accent-text)" });
  const top = net < 0 ? [...rows, { spacer: true, value: -net }] : rows;
  const upper = htmlNode("div", "bal-row", body);
  htmlNode("span", "", upper, "자산");
  tallSegments(upper, top, total, ctx.narrow);
  const lower = htmlNode("div", "bal-row", body);
  htmlNode("span", "", lower, "부채·순자산");
  tallSegments(lower, bottom, total, ctx.narrow);
}

function drawAssetBalance(ctx, card) {
  balanceBars(ctx, card.body);
  const total = ctx.assets.assets[ctx.assets.rec];
  rowsList(card.body, assetRowsNow(ctx.assets).map((row) => ({ name: row.name, fill: row.fill, value: won(row.value), percent: percentText(row.value / total) })));
}

function drawAssetTrend(ctx, card) {
  const { assets } = ctx;
  card.setRight(`기록 ${assets.days.length}회`);
  recordLine(card.body, assets.days, assets.net, { name: "순자산", color: "var(--accent)", height: 334, selected: assets.rec, pick: ctx.pickRecord });
}

function drawAssetWaterfall(ctx, card) {
  const { assets, narrow } = ctx;
  const steps = waterfallSteps(assets);
  if (assets.base !== null) card.setRight(`${dotDate(assets.days[assets.base])}부터`);
  if (!steps.length) {
    noteText(card.body, assets.days.length < 2 ? "기록이 두 번 쌓이면 나옵니다" : assets.base === null ? "기간의 첫 기록입니다" : "기간 처음과 같습니다");
    return;
  }
  figure(card.body, 260, (host, width, height) => {
    waterfallChart(host, width, height, { steps: narrow ? steps.slice(0, 4) : steps, totalLabel: "순자산" });
  });
}

function drawAssetBucket(ctx, card) {
  const { assets, data } = ctx;
  const at = assets.rec;
  const total = assets.assets[at];
  if (total <= 0) {
    noteText(card.body, "이 기록의 자산이 없습니다.");
    return;
  }
  const rows = bucketRows(data, assets).map((row) => ({ ...row, fill: slotFill(row.slot), ink: slotInk(row.slot), text: percentText(row.value / total) }));
  tallSegments(card.body, rows, total, ctx.narrow);
  htmlNode("div", "gap-8", card.body);
  legendRow(card.body, rows.map((row) => ({ name: row.name, fill: row.fill, note: labelWon(row.value) })));
  const average = averageSpendOf(data);
  const cash = rows.find((row) => row.key === "cash").value;
  statBoxes(card.body, [
    { label: "현금성 ÷ 월평균 지출", value: average > 0 ? `${(cash / average).toFixed(1)}개월` : "-", note: average > 0 ? `월평균 지출 ${labelWon(average)}원` : "" },
    { label: "부채 ÷ 자산", value: percentText(assets.debt[at] / total), note: `부채 ${labelWon(assets.debt[at])}원` },
  ]).classList.add("gap-top");
}

function assetChange(assets, values) {
  return assets.base === null || values[assets.rec] === values[assets.base] ? "" : signedLabel(values[assets.rec] - values[assets.base]);
}

function drawAssetList(ctx, card) {
  const { assets, state } = ctx;
  const at = assets.rec;
  card.setTitle("항목");
  const box = htmlNode("div", "b-list", card.body);
  const focusIsKnown = [...assets.groups, ...assets.debts].some((item) => item.key === state.focus);
  listRow(box, {
    mark: "focus:all",
    pressed: !focusIsKnown,
    name: "순자산",
    value: won(assets.net[at]),
    left: (node) => sparkLine(node, assets.net),
    right: assetChange(assets, assets.net),
    pick: () => ctx.holdFocus(null),
  });
  listGroup(box, "자산", won(assets.assets[at]));
  for (const group of [...assets.groups].sort((a, b) => b.values[at] - a.values[at])) {
    const fill = solidFill(group.slot);
    listRow(box, {
      mark: `focus:${group.key}`,
      pressed: state.focus === group.key,
      fill,
      name: group.name,
      value: won(group.values[at]),
      left: (node) => sparkLine(node, group.values, { stroke: fill }),
      right: assetChange(assets, group.values),
      pick: () => ctx.holdFocus(group.key),
    });
  }
  listGroup(box, "부채", won(assets.debt[at]));
  assets.debts.forEach((debt, order) => {
    const fill = debtTint(order);
    listRow(box, {
      mark: `focus:${debt.key}`,
      pressed: state.focus === debt.key,
      fill,
      name: debt.name,
      value: won(debt.values[at]),
      left: (node) => sparkLine(node, debt.values, { stroke: fill }),
      right: assetChange(assets, debt.values),
      pick: () => ctx.holdFocus(debt.key),
    });
  });
}

/* 기간의 첫 기록부터의 증감. */
function sinceText(figures, values) {
  return figures.base === null ? "" : `${dotDate(figures.days[figures.base])}부터 ${signedWon(values[figures.rec] - values[figures.base])}`;
}

function drawAssetDetail(ctx, card) {
  const { assets, state } = ctx;
  const at = assets.rec;
  const group = assets.groups.find((item) => item.key === state.focus);
  const debt = assets.debts.find((item) => item.key === state.focus);
  if (group || debt) {
    const item = group || debt;
    const value = item.values[at];
    const whole = group ? assets.assets[at] : assets.debt[at];
    card.setTitle(item.name);
    card.setRight(whole > 0 ? `${group ? "자산" : "부채"}의 ${percentText(value / whole)}` : "");
    heroLine(card.body, won(value), [recordNote(assets), sinceText(assets, item.values)]);
    recordLine(card.body, assets.days, item.values, { name: item.name, color: group ? solidFill(group.slot) : "var(--slot-2)", selected: at, pick: ctx.pickRecord });
    return;
  }
  card.setTitle("순자산");
  card.setRight(`기록 ${assets.days.length}회`);
  heroLine(card.body, won(assets.net[at]), [recordNote(assets), sinceText(assets, assets.net), `자산 ${labelWon(assets.assets[at])}`, `부채 ${labelWon(assets.debt[at])}`]);
  legendRow(card.body, [
    ...assets.groups.map((item) => ({ key: item.key, name: item.short, fill: solidFill(item.slot), pressed: false, pick: () => ctx.holdFocus(item.key) })),
    { name: "부채", fill: debtTint(0) },
  ]);
  figure(card.body, 280, (host, width, height) => {
    balanceAreaChart(host, width, height, { days: assets.days, groups: assets.groups, debts: assets.debts, net: assets.net, selected: at, pick: ctx.holdFocus, pickRecord: ctx.pickRecord && ((index) => ctx.pickRecord(assets.days[index])) });
  });
}

function drawAssetInfo(ctx, card) {
  const { assets, state, data } = ctx;
  const group = assets.groups.find((item) => item.key === state.focus);
  const debt = assets.debts.find((item) => item.key === state.focus);
  if (!group && !debt) {
    card.setTitle("자산과 부채");
    balanceBars(ctx, card.body);
    return;
  }
  const products = data.asset_products[state.focus] || [];
  if (debt) {
    card.setTitle("대출 정보");
    const links = linkDetails(products.map((item) => ({ key: item.name, name: item.name, latest: item.amount })), data.finance_details.loan, (row) => row.balance);
    for (const item of products) {
      const key = `${state.focus}:${item.name}`;
      loanBlock(card.body, loanFigures({ key, name: item.name, value: item.amount }, links.get(item.name), data.loan_payments[key]));
    }
    return;
  }
  const manual = products.filter((item) => item.facts);
  if (manual.length) {
    card.setTitle("직접 입력 정보");
    for (const item of manual) {
      rowsList(card.body, [{ name: item.name, href: item.href, value: won(item.amount) }], true);
      statBoxes(card.body, item.facts);
    }
    return;
  }
  const total = sumOf(products, (item) => item.amount);
  card.setTitle("들어 있는 상품");
  card.setRight(`${products.length}개`);
  if (!products.length) {
    noteText(card.body, "들어 있는 상품이 없습니다.");
    return;
  }
  rowsList(card.body, products.map((item) => ({ name: item.name, hint: item.hint, value: won(item.amount), percent: total > 0 ? percentText(item.amount / total) : "" })));
}

function drawAssetArea(ctx, card) {
  const { assets } = ctx;
  card.setRight(`기록 ${assets.days.length}회`);
  legendRow(card.body, [
    ...assets.groups.map((item) => ({ key: item.key, name: item.short, fill: solidFill(item.slot), pressed: ctx.state.focus === item.key, pick: () => ctx.pickFocus(item.key) })),
    ...assets.debts.map((item, order) => ({ name: item.name, fill: debtTint(order) })),
    { name: "순자산", fill: "var(--text)" },
  ]);
  figure(card.body, 340, (host, width, height) => {
    balanceAreaChart(host, width, height, {
      days: assets.days,
      groups: assets.groups,
      debts: assets.debts,
      net: assets.net,
      selected: assets.rec,
      focus: ctx.state.focus,
      pick: ctx.pickFocus,
      pickRecord: ctx.pickRecord && ((index) => ctx.pickRecord(assets.days[index])),
    });
  });
}

function drawAssetChanges(ctx, card) {
  const { assets } = ctx;
  const at = assets.rec;
  const since = assets.base === null ? "" : `${dotDate(assets.days[assets.base])}부터`;
  card.setRight(since);
  const addRow = (item, fill) => {
    const row = htmlNode("button", "sm-row", card.body);
    row.type = "button";
    row.dataset.mark = `focus:${item.key}`;
    row.addEventListener("click", () => ctx.pickFocus(item.key));
    const rows = [{ name: "금액", value: won(item.values[at]) }];
    if (since) rows.push({ name: since, value: signedWon(item.values[at] - item.values[assets.base]) });
    setTip(row, item.name, rows);
    const name = htmlNode("span", "sm-n", row);
    swatch(name, fill);
    htmlNode("span", "", name, item.short);
    htmlNode("span", "num sm-v", row, won(item.values[at]));
    const holder = htmlNode("span", "sm-c", row);
    figure(holder, 26, (host, width, height) => sparkLine(host, item.values, { width, height, stroke: fill }));
    htmlNode("span", "d", row, since ? signedLabel(item.values[at] - item.values[assets.base]) : "");
  };
  for (const group of [...assets.groups].sort((a, b) => b.values[at] - a.values[at])) addRow(group, solidFill(group.slot));
  assets.debts.forEach((debt, order) => addRow(debt, debtTint(order)));
}

/* --- 금융 */

function returnText(rate) {
  return rate === null ? "" : `${rate > 0 ? "+" : ""}${rate.toFixed(1)}%`;
}

function drawFinanceList(ctx, card) {
  const { finance, state } = ctx;
  card.setTitle("상품");
  const box = htmlNode("div", "b-list", card.body);
  const focused = findFinanceItem(finance, state.focus);
  listRow(box, {
    mark: "focus:all",
    pressed: !focused,
    name: "금융 전체",
    value: won(finance.net),
    left: (node) => sparkLine(node, finance.netValues),
    pick: () => ctx.holdFocus(null),
  });
  for (const group of finance.groups) {
    groupRow(box, group, ctx);
    const fill = solidFill(group.slot);
    for (const item of group.items) {
      const change = finance.base === null ? "" : changeText(item.values[finance.rec], item.values[finance.base]);
      listRow(box, {
        mark: `focus:${item.key}`,
        pressed: state.focus === item.key,
        fill,
        name: item.name,
        value: won(item.value),
        left: (node) => sparkLine(node, item.values, { stroke: fill }),
        right: item.investment ? returnText(investmentReturn(item.investment)) : change,
        pick: () => ctx.holdFocus(item.key),
      });
    }
  }
  if (finance.debtGroup) {
    groupRow(box, finance.debtGroup, ctx);
    for (const item of finance.debts) {
      listRow(box, {
        mark: `focus:${item.key}`,
        pressed: state.focus === item.key,
        fill: "var(--slot-2)",
        name: item.name,
        value: won(item.value),
        left: (node) => sparkLine(node, item.values, { stroke: "var(--slot-2)" }),
        right: item.loan.rate === null ? "" : `금리 ${rateText(item.loan)}`,
        pick: () => ctx.holdFocus(item.key),
      });
    }
  }
  if (finance.insurance.length) {
    const head = htmlNode("div", "b-group", box);
    htmlNode("span", "", head, "보험");
    htmlNode("span", "", head, "납입금");
    for (const row of finance.insurance) {
      const key = `insurance:${row.name}`;
      listRow(box, {
        mark: `focus:${key}`,
        pressed: state.focus === key,
        fill: "var(--bar-muted)",
        name: row.name,
        value: row.paid === null ? "-" : won(row.paid),
        left: (node) => htmlNode("span", "b-sub", node, row.org),
        right: row.status,
        pick: () => ctx.holdFocus(key),
      });
    }
  }
}

function drawInvestmentDetail(ctx, card, item) {
  const row = item.investment;
  const rate = investmentReturn(row);
  card.setTitle(item.name);
  card.setRight([row.org, row.type].filter(Boolean).join(" · "));
  heroLine(card.body, row.valuation === null ? won(item.value) : won(row.valuation), [
    returnText(rate),
    row.valuation !== null && row.principal !== null ? signedWon(row.valuation - row.principal) : "",
  ]);
  statBoxes(card.body, [
    { label: "원금", value: row.principal === null ? "-" : won(row.principal) },
    { label: "가입일", value: row.start ? dotDate(row.start) : "-" },
    { label: "만기일", value: row.end ? dotDate(row.end) : "없음" },
  ]).classList.add("gap-bottom");
  legendRow(card.body, [{ name: "평가금액", fill: "var(--accent)" }, { name: "원금", fill: "var(--bar-muted)" }]);
  const history = row.history || [];
  gainLine(card.body, history.map((point) => point.date), history.map((point) => point.valuation), history.map((point) => point.principal), { height: 210 });
}

const PENSION_GROUPS = new Set(["연금 자산", "연금 (직접 입력)"]);

/* 연금 상품에 이어 둔 납입과 수령. 불어난 금액은 잔액 변화에서 그사이 넣은 돈은 빼고 받은 돈은 더한 값이다. */
function pensionBlock(ctx, parent, item) {
  if (!PENSION_GROUPS.has(item.groupName)) return;
  const link = ctx.data.pension_links?.[item.key];
  if (!link) {
    htmlNode("a", "", noteText(parent, ""), "정기 납입·수령 연결하기").href = "/recurring";
    return;
  }
  const growth = link.growth;
  statBoxes(parent, [
    { label: "넣은 돈", value: won(link.contributed), note: "연결한 납입" },
    { label: "받은 돈", value: won(link.received), note: "연결한 수령" },
    { label: "불어난 금액", value: growth ? signedWon(growth.amount) : "-", note: growth ? `${dotDate(growth.from)}부터` : "잔액 기록이 두 번 쌓이면 계산합니다" },
  ]).classList.add("gap-bottom");
  const hint = (payment) => `${payment.direction === "in" ? "연결된 수령" : "연결된 납입"} · ${payment.times}회`;
  rowsList(parent, link.payments.map((payment) => ({ name: payment.name, hint: hint(payment), value: won(payment.typical) })), true);
}

/* 묶음 전체. 기록마다의 합계와 상품마다의 비중을 본다. */
function drawFinanceGroupDetail(ctx, card, group) {
  const { finance } = ctx;
  card.setTitle(group.name);
  card.setRight(`상품 ${group.items.length}개`);
  heroLine(card.body, won(group.value), [recordNote(finance), sinceText(finance, group.values)]);
  recordLine(card.body, finance.days, group.values, { name: group.name, color: solidFill(group.slot), height: 220, selected: finance.rec, pick: ctx.pickRecord });
  if (group.value <= 0) return;
  figure(card.body, 220, (host, width, height) => {
    groupedTreemapChart(host, width, height, { groups: [group], focus: null, total: group.value, pick: ctx.holdFocus });
  });
}

function drawBalanceDetail(ctx, card, item) {
  const { finance } = ctx;
  card.setTitle(item.name);
  card.setRight(item.groupName);
  heroLine(card.body, won(item.value), [recordNote(finance), sinceText(finance, item.values)]);
  pensionBlock(ctx, card.body, item);
  recordLine(card.body, finance.days, item.values, { name: "잔액", color: "var(--accent)", height: item.kind === "account" ? 210 : 240, selected: finance.rec, pick: ctx.pickRecord });
}

function drawLoanDetail(ctx, card, item) {
  const { finance, data } = ctx;
  const loan = item.loan;
  card.setTitle(item.name);
  card.setRight([loan.org, loan.type].filter(Boolean).join(" · ") || item.groupName);
  heroLine(card.body, won(item.value), [recordNote(finance), loan.principal === null ? "" : `원금 ${won(loan.principal)}`]);
  const interest = latestInterest(loan);
  statBoxes(card.body, [
    { label: "금리", value: rateText(loan) || "-", note: rateNote(loan) },
    { label: "월 이자", value: interest === null ? "-" : interestText(interest), note: interest === null ? "" : interest.note },
    { label: "남은 기간", value: loan.end ? `${monthsBetween(data.today, loan.end)}개월` : "-", note: loan.end ? `만기 ${dotDate(loan.end)}` : "" },
  ]).classList.add("gap-bottom");
  linkedPayments(card.body, loan);
  interestMonths(card.body, loan);
  const repaid = repaidShare(loan);
  if (repaid !== null) meterRow(card.body, "금액 상환", repaid, "var(--accent)");
  if (loan.start && loan.end) meterRow(card.body, "기간 경과", elapsedShare(loan.start, loan.end, data.today), "var(--text-2)");
  recordLine(card.body, finance.days, item.values, { name: "잔액", color: "var(--slot-2)", height: 210, selected: finance.rec, pick: ctx.pickRecord });
}

function drawInsuranceDetail(ctx, card, row) {
  card.setTitle(row.name);
  card.setRight(row.org);
  heroLine(card.body, row.paid === null ? "-" : won(row.paid), ["총납입금"]);
  statBoxes(card.body, [
    { label: "계약 상태", value: row.status || "-" },
    { label: "계약일", value: row.start ? dotDate(row.start) : "-" },
    { label: "만기일", value: row.end ? dotDate(row.end) : "없음" },
  ]);
  if (row.start && row.end) meterRow(card.body, "기간 경과", elapsedShare(row.start, row.end, ctx.data.today), "var(--text-2)");
}

function drawFinanceDetail(ctx, card) {
  const { finance, state } = ctx;
  const item = findFinanceItem(finance, state.focus);
  if (item?.kind === "group") drawFinanceGroupDetail(ctx, card, item);
  else if (item?.kind === "invest") drawInvestmentDetail(ctx, card, item);
  else if (item?.kind === "loan") drawLoanDetail(ctx, card, item);
  else if (item?.kind === "insurance") drawInsuranceDetail(ctx, card, item);
  else if (item) drawBalanceDetail(ctx, card, item);
  else {
    card.setTitle("금융 전체");
    card.setRight(`기록 ${finance.days.length}회`);
    heroLine(card.body, won(finance.net), [recordNote(finance), `금융 자산 ${labelWon(finance.assets)}`, `대출·부채 ${labelWon(finance.debt)}`]);
    if (finance.assets <= 0) return;
    figure(card.body, 250, (host, width, height) => {
      groupedTreemapChart(host, width, height, { groups: finance.groups, total: finance.assets, pick: ctx.holdFocus });
    });
  }
}

function drawAccountUse(ctx, card, item) {
  card.setTitle("계좌 사용처");
  const month = ctx.monthFlow?.axis.selected || ctx.data.flow_range?.last.slice(0, 7);
  if (month) card.setRight(monthTick(month));
  const holder = noteText(card.body, "불러오는 중");
  ctx.fetchJson(`/chart/account/${item.instrument}?month=${month || ""}`).then(
    ({ rows }) => {
      if (!rows.length) {
        holder.textContent = "이 달의 기록이 없습니다";
        return;
      }
      holder.remove();
      const peak = Math.max(1, ...rows.map((row) => row.amount));
      const list = htmlNode("ul", "rows w-rows rows-tight use-rows", card.body);
      for (const row of rows) {
        const line = htmlNode("li", "", list);
        htmlNode("span", "", line, row.name);
        const meter = htmlNode("span", "meter", line);
        meter.style.setProperty("--c", "var(--accent)");
        htmlNode("i", "", meter).style.width = `${(row.amount / peak) * 100}%`;
        htmlNode("span", "num", line, won(row.amount));
      }
    },
    () => {
      holder.textContent = "불러오지 못했습니다";
    },
  );
}

function drawFinanceInfo(ctx, card) {
  const { finance, state } = ctx;
  const item = findFinanceItem(finance, state.focus);
  if (item?.kind === "account") {
    drawAccountUse(ctx, card, item);
    return;
  }
  if (item) {
    card.section.hidden = true;
    return;
  }
  drawFinanceGain(ctx, card, 190);
}

function drawFinanceTreemap(ctx, card) {
  const { finance } = ctx;
  if (finance.assets <= 0) {
    noteText(card.body, "이 기록의 금융 자산이 없습니다.");
    return;
  }
  figure(card.body, 322, (host, width, height) => {
    groupedTreemapChart(host, width, height, { groups: finance.groups, focus: ctx.state.focus, total: finance.assets, pick: ctx.pickFocus });
  });
}

function drawDumbbell(ctx, card, height) {
  card.setRight("○ 원금  ● 평가금액");
  const items = dumbbellItems(ctx.finance);
  if (!items.length) {
    noteText(card.body, "투자 현황이 없습니다.");
    return false;
  }
  figure(card.body, Math.max(height, items.length * 44 + 28), (host, width, chartHeight) => dumbbellChart(host, width, chartHeight, { items }));
  return true;
}

function drawFinancePnl(ctx, card) {
  const { finance } = ctx;
  if (drawDumbbell(ctx, card, 200)) gainLine(card.body, finance.days, finance.valuation, finance.principal, { height: 116, tickCount: 2 });
}

function drawFinanceLoans(ctx, card) {
  const { finance } = ctx;
  card.setRight(`잔액 ${labelWon(finance.debt)}원`);
  if (finance.loans.length) finance.loans.forEach((loan) => loanBlock(card.body, loan));
  else noteText(card.body, "대출이 없습니다.");
}

function drawFinanceMaturity(ctx, card) {
  const { finance, data } = ctx;
  const span = maturityItems(finance, data.today);
  card.setRight(`오늘부터 ${span.to.slice(0, 4)}년까지`);
  if (!span.items.length) {
    noteText(card.body, "다가오는 만기가 없습니다.");
    return;
  }
  figure(card.body, 204, (host, width, height) => maturityChart(host, width, height, span));
}

function drawFinanceTimeline(ctx, card) {
  const { finance, data } = ctx;
  card.setRight("금액은 평가금액, 잔액, 납입금");
  const rows = timelineRows(finance);
  if (!rows.length) {
    noteText(card.body, "가입일이 있는 상품이 없습니다.");
    return;
  }
  const span = timelineSpan(rows, data.today);
  figure(card.body, timelineHeight(rows), (host, width, height) => timelineChart(host, width, height, { rows, ...span, today: data.today }));
}

function drawFinanceGain(ctx, card, height = 220) {
  const { finance } = ctx;
  card.setTitle("평가금액과 원금");
  if (finance.profit !== null) card.setRight(signedWon(finance.profit));
  legendRow(card.body, [{ name: "평가금액", fill: "var(--accent)" }, { name: "원금", fill: "var(--bar-muted)" }]);
  gainLine(card.body, finance.days, finance.valuation, finance.principal, { height });
}

function drawFinanceReturns(ctx, card) {
  drawDumbbell(ctx, card, 248);
}

/* --- 위젯 목록. 서버의 목록과 키가 같아야 한다(tests/test_chart_widgets.py). */

const WIDGET_DRAW = {
  "category.matrix": drawCategoryMatrix,
  "category.daily": (ctx, card) => drawDaily(ctx, card, true),
  "category.weekday": drawCategoryWeekday,
  "category.treemap": drawCategoryTreemap,
  "category.stack": drawCategoryStack,
  "category.changes": drawCategoryChanges,
  "category.payment": drawCategoryPayment,
  "category.list": drawCategoryList,
  "category.item": drawCategoryItem,
  "category.places": drawCategoryPlaces,
  "category.free": (ctx, card) => ctx.freeChart(card),
  "flow.pair": drawFlowPair,
  "flow.cumulative": drawFlowCumulative,
  "flow.sankey": (ctx, card) => drawFlowSankey(ctx, card),
  "flow.payment": drawFlowPayment,
  "flow.months": drawFlowMonths,
  "flow.detail": drawFlowDetail,
  "flow.income": drawFlowIncome,
  "flow.mirror": drawFlowMirror,
  "flow.net": drawFlowNet,
  "flow.overlay": drawFlowOverlay,
  "flow.daily": (ctx, card) => drawDaily(ctx, card, false),
  "flow.free": (ctx, card) => ctx.freeChart(card),
  "assets.balance": drawAssetBalance,
  "assets.trend": drawAssetTrend,
  "assets.waterfall": drawAssetWaterfall,
  "assets.bucket": drawAssetBucket,
  "assets.list": drawAssetList,
  "assets.detail": drawAssetDetail,
  "assets.info": drawAssetInfo,
  "assets.area": drawAssetArea,
  "assets.changes": drawAssetChanges,
  "assets.free": (ctx, card) => ctx.freeChart(card),
  "finance.list": drawFinanceList,
  "finance.detail": drawFinanceDetail,
  "finance.info": drawFinanceInfo,
  "finance.treemap": drawFinanceTreemap,
  "finance.pnl": drawFinancePnl,
  "finance.loans": drawFinanceLoans,
  "finance.maturity": drawFinanceMaturity,
  "finance.timeline": drawFinanceTimeline,
  "finance.gain": (ctx, card) => drawFinanceGain(ctx, card),
  "finance.returns": drawFinanceReturns,
  "finance.free": (ctx, card) => ctx.freeChart(card),
};

/* 합계를 보는 위젯은 기록이 있어야 그릴 수 있다. */
function hasWidgetData(ctx, tab) {
  if (tab === "assets") return Boolean(ctx.assets);
  if (tab === "finance") return Boolean(ctx.finance);
  return Boolean(ctx.flow);
}

function drawWidget(widget, ctx, card) {
  card.body.replaceChildren();
  const free = widget.key.endsWith(".free");
  try {
    if (!free && !hasWidgetData(ctx, widget.tab)) noteText(card.body, "아직 볼 기록이 없습니다.");
    else WIDGET_DRAW[widget.key](ctx, card);
  } catch (error) {
    // 위젯 하나 때문에 화면 전체가 멈추지 않게 한다.
    console.error(widget.key, error);
    card.body.replaceChildren();
    noteText(card.body, "이 위젯을 그리지 못했습니다.");
  }
}

/* 위젯이 그릴 때 쓰는 값. 탭 화면과 홈이 같은 모양으로 만든다. */
function figuresFor(data, state, tab) {
  // 금융 탭은 계좌 사용처를 달로만 보므로 단위를 따르지 않는다.
  const flow = tab === "category" || tab === "flow" ? flowFigures(data, state) : tab === "finance" ? flowFigures(data, state, "month") : null;
  return {
    data,
    state,
    flow,
    monthFlow: flow && monthFiguresOf(data, state, flow),
    assets: tab === "assets" ? assetFigures(data, state) : null,
    finance: tab === "finance" ? financeFigures(data, state) : null,
    narrow: window.innerWidth < NARROW_WIDTH,
    cache: new Map(),
    fetchJson(url) {
      if (!this.cache.has(url)) {
        this.cache.set(
          url,
          fetch(url, { headers: { Accept: "application/json" } }).then((response) => {
            if (!response.ok) throw new Error(String(response.status));
            return response.json();
          }),
        );
      }
      return this.cache.get(url);
    },
  };
}

function cardApi(section, head, title, body, span) {
  const right = htmlNode("span", "band-right", null);
  right.hidden = true;
  const tools = head.querySelector(".widget-tools, .widget-link");
  head.insertBefore(right, tools);
  return {
    section,
    head,
    body,
    span,
    setTitle: (text) => {
      title.textContent = text;
    },
    setRight: (text) => {
      right.textContent = text;
      right.hidden = !text;
    },
    setExtra: (node) => head.insertBefore(node, right.nextSibling),
  };
}

/* 홈 위젯 화면의 미리 보기. 홈에 넣었을 때와 같은 값으로 그리고, 누를 수는 없다. */
function drawHomePreviews(data, month) {
  const sections = [...document.querySelectorAll("[data-home-preview]")];
  if (!sections.length) return;
  const catalog = new Map(data.widgets.catalog.map((widget) => [widget.key, widget]));
  const cache = new Map();
  for (const section of sections) {
    const widget = catalog.get(section.dataset.homePreview);
    if (!widget) continue;
    const { tab } = widget;
    const records = tab === "assets" ? data.snapshots : data.finance_snapshots;
    const state = newState({ tab, month, record: tab === "assets" || tab === "finance" ? lastRecordBy(records, month) : null });
    const ctx = figuresFor(data, state, tab);
    ctx.cache = cache;
    ctx.freeChart = (card) => noteText(card.body, "기간과 모양을 직접 골라 보는 차트");
    const head = section.querySelector(".band-head");
    const card = cardApi(section, head, head.querySelector("h2"), section.querySelector("[data-body]"), 12);
    card.home = true;
    drawWidget(widget, ctx, card);
  }
}

/* --- 홈: 넣어 둔 위젯을 그린다. 누르면 그 탭 화면으로 간다. */

function lastRecordBy(records, month) {
  const end = dayKey(bucketSpan("month", month).end);
  const before = records.filter((record) => record.date <= end);
  return before.length ? before[before.length - 1].date : null;
}

function homeChartHref(tab, overrides) {
  const query = stateToQuery(newState({ tab, ...overrides }));
  return query ? `/chart?${query}` : "/chart";
}

function drawHomeWidgets(data, month) {
  const sections = [...document.querySelectorAll("[data-home-chart]")];
  if (!sections.length) return;
  installTips();
  const catalog = new Map(data.widgets.catalog.map((widget) => [widget.key, widget]));
  const cache = new Map();
  for (const section of sections) {
    const widget = catalog.get(section.dataset.homeChart);
    const { tab } = widget;
    const records = tab === "assets" ? data.snapshots : data.finance_snapshots;
    const state = newState({ tab, month, record: tab === "assets" || tab === "finance" ? lastRecordBy(records, month) : null });
    const ctx = figuresFor(data, state, tab);
    ctx.cache = cache;
    const go = (overrides) => window.location.assign(homeChartHref(tab, overrides));
    ctx.pickMonth = (picked) => go({ month: picked });
    ctx.pickFocus = (key) => go({ month, focus: key });
    ctx.holdFocus = ctx.pickFocus;
    ctx.pickCell = (picked, key) => go({ month: picked, focus: key });
    ctx.freeChart = (card) => {
      const free = createFreeChart(data, state, () => free.draw(card));
      card.body.appendChild(free.root);
      free.draw(card);
    };
    const head = section.querySelector(".band-head");
    const card = cardApi(section, head, head.querySelector("h2"), section.querySelector("[data-body]"));
    card.home = true;
    // 홈에는 모든 탭의 위젯이 섞이므로 어느 탭의 것인지 함께 적는다.
    const tabName = data.widgets.tabs.find((item) => item.key === tab).name;
    const setRight = card.setRight;
    card.setRight = (text) => setRight(text ? `${tabName} · ${text}` : tabName);
    card.setRight("");
    drawWidget(widget, ctx, card);
  }
}
