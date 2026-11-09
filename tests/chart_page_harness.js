/* public/js/chart-data.js의 계산을 실행한다. 입력은 표준 입력의 JSON, 결과는 표준 출력의 JSON이다. */
"use strict";

const path = require("path");
const fs = require("fs");
const vm = require("vm");

const chart = require(path.join(__dirname, "..", "public", "js", "chart-data.js"));
// 위젯의 계산은 브라우저처럼 chart-data.js와 같은 전역에서 돈다.
for (const name of ["chart-data.js", "widget-data.js"]) {
  vm.runInThisContext(fs.readFileSync(path.join(__dirname, "..", "public", "js", name), "utf-8"), { filename: name });
}
const { amountReading } = require(path.join(__dirname, "..", "public", "js", "amount-reading.js"));
const input = JSON.parse(fs.readFileSync(0, "utf-8"));
const WIDTH = 600;
const HEIGHT = 400;

function stateOf(scenario) {
  const { form, forms, hidden, ...overrides } = scenario;
  for (const name of ["channels", "categories", "instruments", "groups", "financeGroups", "products"]) {
    if (overrides[name]) overrides[name] = new Set(overrides[name]);
  }
  const state = chart.newState(overrides);
  for (const [tab, views] of Object.entries(forms || {})) Object.assign(state.forms[tab], views);
  if (form) state.forms[state.tab][state.view] = form;
  if (hidden) state.hidden = new Set(hidden);
  return state;
}

function shapesOf(model, state) {
  if (state.view !== "share") return {};
  const shapes = {};
  if (model.sankey) shapes.sankey = chart.sankeyLayout(model.sankey, WIDTH, HEIGHT);
  if (model.items.length) {
    shapes.donut = chart.donutSlices(chart.donutItems(model, state), 100, 100, 90, 60);
    shapes.treemap = chart.treemapCells(model.items, 0, 0, WIDTH, HEIGHT);
  }
  return shapes;
}

/* 위젯 하나가 그릴 값을 그대로 돌려준다. 함수는 JSON에 실리지 않으므로 값만 남긴다. */
function figuresOf(raw, scenario) {
  const state = stateOf(scenario);
  const flow = flowFigures(raw, state);
  const monthFlow = flow && monthFiguresOf(raw, state, flow); // 달 안을 보는 위젯은 고른 칸이 든 달을 본다
  const assets = assetFigures(raw, state);
  const finance = financeFigures(raw, state);
  const cumulative = flow && cumulativeFigures(flow, monthFlow);
  const flowValues = flow && {
    axis: flow.axis,
    month: monthFlow.axis.selected,
    spend: Object.fromEntries(flow.axis.months.map((month) => [month, flow.spend(month)])),
    income: Object.fromEntries(flow.axis.months.map((month) => [month, flow.income(month)])),
    rank: categoryRank(flow, flow.axis.selected),
    matrix: matrixRows(flow, flow.axis.months),
    changes: monthlyChanges(flow, flow.axis.selected, flow.axis.previous),
    daily: dailyAmounts(monthFlow, monthFlow.axis.selected, state.focus),
    weekday: weekdayAverages(monthFlow, monthFlow.axis.months, state.focus),
    payment: paymentFigures(raw, flow, flow.axis.selected, state.focus),
    curve: cumulative.curve,
    cumulativeAverage: cumulative.average,
    cumulativeAverageOf: cumulative.past.length,
    bucketDaily: bucketDailyAmounts(flow, flow.axis.unit, flow.axis.selected, state.focus).values,
    running: runningNet(flow, flow.axis.months),
    sankey: monthSankey(raw, state, flow, flow.axis.selected),
    series: categorySeriesOf(flow, state.focus).map((item) => ({ key: item.key, slot: item.slot })),
    average: monthlyAverage(flow, flow.axis.months, (month) => flow.spend(month)),
  };
  const assetValues = assets && {
    ...assets,
    steps: waterfallSteps(assets),
    buckets: bucketRows(raw, assets),
  };
  const financeValues = finance && {
    ...finance,
    maturity: maturityItems(finance, raw.today),
    timeline: timelineRows(finance),
    found: state.focus ? (findFinanceItem(finance, state.focus) || {}).kind || null : null,
  };
  return { flow: flowValues, assets: assetValues, finance: financeValues, averageSpend: averageSpendOf(raw) };
}

const data = input.data ? chart.normalize(input.data) : null;
const scenarios = (input.scenarios || []).map((scenario) => {
  const state = stateOf(scenario);
  const period = chart.resolvePeriod(data, state);
  const model = chart.buildModel(data, state, period);
  const facets = {};
  const changes = {};
  const labels = {};
  for (const dimension of chart.DIMENSIONS[state.tab]) {
    const totals = chart.facetTotals(data, state, period, dimension);
    facets[dimension.state] = Object.fromEntries([...totals].map(([key, value]) => [key, value.amount]));
    changes[dimension.state] = Object.fromEntries([...totals].map(([key, value]) => [key, value.change ?? null]));
    labels[dimension.state] = Object.fromEntries([...totals].map(([key, value]) => [key, chart.chipAmount(value)]));
  }
  return {
    period,
    title: chart.chartTitle(state, period),
    rows: model.rows,
    series: model.series.map((item) => item.key),
    legend: model.legend.map((item) => ({ key: item.key, slot: item.slot, amount: item.amount ?? null, pressed: item.pressed ?? null })),
    items: model.items,
    total: model.total,
    caption: model.caption,
    asOf: model.asOf,
    domain: state.view === "trend" ? chart.modelDomain(model) : null,
    filled: chart.hasValues(model),
    sankey: model.sankey,
    shapes: shapesOf(model, state),
    summary: chart.summarize(data, state, period),
    facets,
    changes,
    labels,
    link: chart.linesLink(data, state, period),
    focusKept: chart.hasFocusTarget(model, state),
    focusedSeries: state.focus ? model.series.filter((item) => chart.isFocusedSeries(state, item)).map((item) => item.key) : [],
  };
});

process.stdout.write(
  JSON.stringify({
    scenarios,
    compact: (input.compact || []).map(chart.compactWon),
    directLabels: (input.directLabels || []).map(chart.labelWon),
    scales: (input.scales || []).map(([low, high]) => chart.axisScale(low, high)),
    buckets: (input.buckets || []).map(([unit, first, last, count]) => chart.listBuckets(unit, first, last, count)),
    names: (input.names || []).map(([unit, key, year]) => bucketName(unit, key, year)),
    queries: (input.queries || []).map((query) => {
      const state = chart.stateFromQuery(query);
      return {
        query: chart.stateToQuery(state),
        tab: state.tab,
        view: state.view,
        scale: state.scale,
        form: state.forms[state.tab][state.view],
        kind: state.flowKind,
        focus: state.focus,
        month: state.month,
        unit: state.unit,
        range: state.range,
        span: state.span,
        record: state.record,
        editing: state.editing,
        start: state.start,
        end: state.end,
        instruments: [...state.instruments],
      };
    }),
    treemaps: (input.treemaps || []).map((items) => chart.squarify(items, 0, 0, WIDTH, HEIGHT)),
    choices: (input.choices || []).map(({ state: overrides, kind, form }) => {
      const state = stateOf(overrides);
      if (kind) chart.chooseFlowKind(state, kind);
      if (form) chart.chooseForm(state, form);
      return { kind: state.flowKind, share: state.forms.flow.share, trend: state.forms.flow.trend };
    }),
    readings: (input.readings || []).map(amountReading),
    figures: (input.figures || []).map((scenario) => figuresOf(normalize(input.data), scenario)),
    fills: (input.fills || []).map(([items, field]) => fillRows(items, field)),
    cells: (input.cells || []).map(matrixCellText),
    rates: (input.rates || []).map(([table, implied]) => effectiveRate(table, implied)),
    heats: (input.heats || []).map(([value, average]) => heatOf(value, average)),
    links: (input.links || []).map(({ items, rows }) => [...linkDetails(items, rows, (row) => row.amount)].map(([key, row]) => [key, row.name])),
    choosing: (input.choosing || []).map(({ keys, shown, all }) => {
      const state = chart.newState();
      if (all) state.allChosen.add("categories");
      const before = [...chart.chosenKeys(state, "categories", shown)];
      chart.setChosen(state, "categories", new Set(keys), shown);
      return { before, set: [...state.categories], all: state.allChosen.has("categories") };
    }),
    sizes: { width: WIDTH, height: HEIGHT },
  }),
);
