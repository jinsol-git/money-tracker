/* 차트 화면의 계산. 화면을 건드리지 않아 node에서 그대로 시험한다.
   서버가 보낸 합계를 브라우저에서 나눠 보므로 버튼을 누르는 즉시 다시 계산한다. */
"use strict";

const TABS = [
  { key: "category", name: "카테고리" },
  { key: "flow", name: "수입·지출" },
  { key: "assets", name: "자산" },
  { key: "finance", name: "금융" },
];
const VIEWS = [
  { key: "share", name: "기간 비중" },
  { key: "trend", name: "추이" },
];
const SCALES = [
  { key: "day", name: "일일", unit: "day", count: 30, period: "최근 30일" },
  { key: "week", name: "주간", unit: "week", count: 26, period: "최근 26주" },
  { key: "month", name: "월간", unit: "month", count: 12, period: "최근 12개월" },
  { key: "year", name: "연간", unit: "year", count: 0, period: "전체 기간" },
  { key: "all", name: "전체", unit: "month", count: 0, period: "전체 기간" },
  { key: "custom", name: "기간 지정", unit: null, count: 0, period: "지정한 기간" },
];
const AVERAGE_NAMES = { day: "일평균", week: "주평균", month: "월평균", year: "연평균" };
// 탭 화면 위에서 고르는 단위. 수입·지출은 칸마다 합계를, 자산과 금융은 칸마다 마지막 기록을 본다.
const UNITS = ["day", "week", "month", "year"];
const UNIT_LABELS = { day: "일간", week: "주간", month: "월간", year: "연간" };
// 탭 화면 위의 기간. 구글 금융처럼 최근 기간을 고르고, 기간마다 읽기 좋은 단위를 먼저 고른다.
const RANGES = ["1m", "6m", "ytd", "1y", "5y", "max"];
const RANGE_LABELS = { "1m": "1개월", "6m": "6개월", ytd: "올해", "1y": "1년", "5y": "5년", max: "최대" };
const RANGE_MONTHS = { "1m": 1, "6m": 6, "1y": 12, "5y": 60 };
const RANGE_UNITS = { "1m": "day", "6m": "week", ytd: "month", "1y": "month", "5y": "month", max: "month" };
const UNIT_RANGES = { day: "1m", week: "6m", month: "1y", year: "max" }; // 기간을 고르지 않았을 때 단위에 맞는 기간
const MAX_BUCKETS = 120; // 칸이 이보다 많으면 막대를 읽을 수 없어 그 단위를 막는다
const UNIT_COUNTS = { day: "일", week: "주", month: "개월", year: "년" };
const UNIT_LETTERS = { day: "일", week: "주", month: "월", year: "연" };
const UNIT_WORDS = { day: "날", week: "주", month: "달", year: "해" };
const KEY_RE = /^\d{4}(-\d{2}(-\d{2})?)?$/; // 해, 달, 날의 키
const UNIT_NAMES = { day: "일별", week: "주별", month: "월별", year: "연별" };
const SUBJECT_NAMES = { category: "카테고리", assets: "자산", finance: "금융" };
const STEP_NAMES = { day: "전날", week: "전주", month: "전달", year: "전년" };
const SHARE_FORMS = [
  { key: "donut", name: "원형" },
  { key: "treemap", name: "트리맵" },
  { key: "sankey", name: "생키" },
];
const HOLDING_FORMS = [
  { key: "line", name: "꺾은선", mark: "lines" },
  { key: "stack", name: "누적 막대", mark: "stack" },
  { key: "area", name: "영역", mark: "area" },
  { key: "bars", name: "막대", mark: "bars" },
  { key: "change", name: "증감", mark: "diverging" },
];
const TREND_FORMS = {
  category: [
    { key: "line", name: "꺾은선", mark: "lines" },
    { key: "stack", name: "누적 막대", mark: "stack" },
  ],
  flow: [
    { key: "bars", name: "막대", mark: "bars" },
    { key: "line", name: "꺾은선", mark: "lines" },
    { key: "stack", name: "누적 막대", mark: "stack" },
    { key: "area", name: "영역", mark: "area" },
    { key: "cumulative", name: "누적선", mark: "lines" },
    { key: "difference", name: "차이", mark: "diverging" },
  ],
  assets: HOLDING_FORMS,
  finance: [...HOLDING_FORMS, { key: "profit", name: "손익", mark: "diverging" }],
};
const SERIES = {
  flow: [
    { key: "income", name: "수입" },
    { key: "spend", name: "지출" },
  ],
  assets: [
    { key: "net", name: "순자산" },
    { key: "assets", name: "자산" },
    { key: "liabilities", name: "부채" },
  ],
  finance: [
    { key: "net", name: "금융 순자산" },
    { key: "assets", name: "금융 자산" },
    { key: "liabilities", name: "대출·부채" },
  ],
};
const FLOW_KINDS = [
  { key: "all", name: "수입·지출" },
  { key: "income", name: "수입" },
  { key: "spend", name: "지출" },
];
// 한쪽의 구성을 보여 주는 모양과 둘을 비교하는 모양은 고른 보기에 따라 쓸 수 없다.
const SINGLE_KIND_FORMS = new Set(["donut", "treemap", "stack"]);
const BOTH_KINDS_FORMS = new Set(["difference"]);
const FLOW_FORM_FALLBACK = { share: "sankey", trend: "bars" };
const FLOW_DIMENSIONS = [
  { state: "channels", field: "channel" },
  { state: "categories", field: "category" },
  { state: "instruments", field: "instrument" },
];
const GROUP_DIMENSION = { state: "groups" };
const FINANCE_GROUP_DIMENSION = { state: "financeGroups" };
const PRODUCT_DIMENSION = { state: "products" };
const DIMENSIONS = {
  category: FLOW_DIMENSIONS,
  flow: FLOW_DIMENSIONS,
  assets: [GROUP_DIMENSION],
  finance: [FINANCE_GROUP_DIMENSION, PRODUCT_DIMENSION],
};
const URL_SETS = [
  { name: "channel", set: "channels", parse: String },
  { name: "category", set: "categories", parse: String },
  { name: "instrument", set: "instruments", parse: Number },
  { name: "group", set: "groups", parse: String },
  { name: "fgroup", set: "financeGroups", parse: String },
  { name: "product", set: "products", parse: String },
];
const COMPOSITION_SLOTS = 7; // 색을 구분할 수 있는 수. 나머지는 "그 외"로 묶는다
const REST = "rest";
const CUSTOM_DAY_LIMIT = 45; // 지정한 기간이 이보다 짧으면 하루 단위로 나눈다
const CUSTOM_WEEK_LIMIT = 200;
const DAY_MS = 86400000;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const DONUT_LABEL_SHARE = 0.07; // 이보다 작은 조각에는 비중을 적을 자리가 없다
const TREEMAP_HEADER = 20;

/* --- 표기 */

function won(value) {
  return `${value.toLocaleString("ko-KR")}원`;
}

function signedWon(value) {
  return `${value > 0 ? "+" : ""}${won(value)}`;
}

function compactWon(value) {
  const sign = value < 0 ? "-" : "";
  const size = Math.abs(value);
  // 억 단위는 소수 넷째 자리까지 둔다. 눈금 간격이 좁아도 값이 같아 보이지 않게 하기 위해서다.
  if (size >= 100000000) return `${sign}${Math.round(size / 10000) / 10000}억`;
  if (size >= 10000) return `${sign}${Math.round(size / 10000).toLocaleString("ko-KR")}만`;
  return `${sign}${size.toLocaleString("ko-KR")}`;
}

/* 차트에 직접 적는 값. 눈금과 달리 자릿수를 줄여 한눈에 읽게 한다. */
function labelWon(value) {
  const size = Math.abs(value);
  if (size < 100000000) return compactWon(value);
  return `${value < 0 ? "-" : ""}${Math.round(size / 1000000) / 100}억`;
}

function percentText(share) {
  const rounded = Math.round(share * 100);
  return rounded === 0 && share > 0 ? "1% 미만" : `${rounded}%`;
}

/* --- 기간. 날짜는 시간대 영향을 받지 않도록 UTC로 계산한다. */

function parseDay(text) {
  const [year, month, day] = text.split("-").map(Number);
  return Date.UTC(year, month - 1, day);
}

function dayKey(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

function validDay(text) {
  if (!text || !DAY_RE.test(text)) return null;
  const ms = parseDay(text);
  return Number.isNaN(ms) || dayKey(ms) !== text ? null : text;
}

function weekStart(ms) {
  return ms - ((new Date(ms).getUTCDay() + 6) % 7) * DAY_MS; // 월요일에 시작한다
}

function bucketKey(unit, day) {
  if (unit === "day") return day;
  if (unit === "week") return dayKey(weekStart(parseDay(day)));
  return unit === "month" ? day.slice(0, 7) : day.slice(0, 4);
}

function nextBucket(unit, key) {
  if (unit === "day") return dayKey(parseDay(key) + DAY_MS);
  if (unit === "week") return dayKey(parseDay(key) + 7 * DAY_MS);
  if (unit === "year") return String(Number(key) + 1);
  const [year, month] = key.split("-").map(Number);
  return month === 12 ? `${year + 1}-01` : `${year}-${String(month + 1).padStart(2, "0")}`;
}

function listBuckets(unit, first, last, count) {
  const stop = bucketKey(unit, last);
  const keys = [];
  for (let key = bucketKey(unit, first); key <= stop; key = nextBucket(unit, key)) keys.push(key);
  return count ? keys.slice(-count) : keys;
}

function bucketSpan(unit, key) {
  if (unit === "day") return { start: parseDay(key), end: parseDay(key) };
  if (unit === "week") return { start: parseDay(key), end: parseDay(key) + 6 * DAY_MS };
  const [year, month] = key.split("-").map(Number);
  if (unit === "month") return { start: Date.UTC(year, month - 1, 1), end: Date.UTC(year, month, 0) };
  return { start: Date.UTC(year, 0, 1), end: Date.UTC(year, 11, 31) };
}

function bucketTitle(unit, key) {
  const [year, month, day] = key.split("-").map(Number);
  if (unit === "day") return `${year}년 ${month}월 ${day}일`;
  if (unit === "week") return `${year}년 ${month}월 ${day}일부터 한 주`;
  return unit === "month" ? `${year}년 ${month}월` : `${year}년`;
}

/* 요약 칸 이름에 붙이는 칸 이름. year와 같은 해면 해를 뺀다. */
function bucketName(unit, key, year) {
  const [number, month, day] = key.split("-").map(Number);
  if (unit === "year") return `${number}년`;
  const head = String(number) === year ? "" : `${number}년 `;
  if (unit === "month") return `${head}${month}월`;
  return `${head}${month}월 ${day}일${unit === "week" ? " 주" : ""}`;
}

/* 금액 칸처럼 좁은 자리에 쓴다. */
function bucketShort(unit, key) {
  const [, month, day] = key.split("-").map(Number);
  if (unit === "day") return `${month}월 ${day}일`;
  if (unit === "week") return `${month}월 ${day}일 주`;
  return bucketTitle(unit, key);
}

function bucketTick(unit, key) {
  const [, month, day] = key.split("-").map(Number);
  if (unit === "day" || unit === "week") return `${month}/${day}`;
  return unit === "month" ? `${month}월` : key;
}

function rangeOf(data, tab) {
  if (tab === "assets") return data.asset_range;
  if (tab === "finance") return data.finance_range;
  return data.flow_range;
}

function customPeriod(range, state) {
  const clamp = (day) => (day < range.first ? range.first : day > range.last ? range.last : day);
  let from = clamp(state.start || range.first);
  let to = clamp(state.end || range.last);
  if (from > to) [from, to] = [to, from];
  const days = (parseDay(to) - parseDay(from)) / DAY_MS + 1;
  const unit = days <= CUSTOM_DAY_LIMIT ? "day" : days <= CUSTOM_WEEK_LIMIT ? "week" : "month";
  const keys = listBuckets(unit, from, to, 0);
  const selected = state.view === "trend" && keys.includes(state.bucket) ? state.bucket : null;
  const period = { unit, keys, selected, from, to, scopeFrom: from, scopeTo: to, previous: null, next: null };
  const label = `${from} ~ ${to}`;
  if (!selected) return { ...period, label, short: label };
  const span = bucketSpan(unit, selected);
  return {
    ...period,
    scopeFrom: dayKey(span.start) < from ? from : dayKey(span.start),
    scopeTo: dayKey(span.end) > to ? to : dayKey(span.end),
    label: bucketTitle(unit, selected),
    short: bucketShort(unit, selected),
  };
}

/* 지금 보는 기간. keys는 추이에 그릴 칸, selected는 고른 한 칸, scope는 합계를 낼 날짜 범위다.
   비중을 볼 때는 언제나 한 칸을 보므로 고르지 않았으면 마지막 칸을 쓴다. */
function resolvePeriod(data, state) {
  const scale = SCALES.find((item) => item.key === state.scale);
  const range = rangeOf(data, state.tab);
  if (!range) {
    return {
      unit: scale.unit || "month",
      keys: [],
      selected: null,
      from: null,
      to: null,
      scopeFrom: null,
      scopeTo: null,
      label: scale.period,
      short: scale.period,
      previous: null,
      next: null,
    };
  }
  if (state.scale === "custom") return customPeriod(range, state);

  const all = listBuckets(scale.unit, range.first, range.last, 0);
  const wanted = all.includes(state.bucket) ? state.bucket : null;
  let selected = wanted;
  if (state.view === "share") selected = state.scale === "all" ? null : wanted || all[all.length - 1];
  const index = all.indexOf(selected);
  let keys = scale.count ? all.slice(-scale.count) : all;
  // 창 밖의 칸을 고르면 그 칸이 보이도록 창을 옮긴다.
  if (selected && !keys.includes(selected)) keys = all.slice(index, index + scale.count);
  const from = dayKey(bucketSpan(scale.unit, keys[0]).start);
  const to = dayKey(bucketSpan(scale.unit, keys[keys.length - 1]).end);
  const period = {
    unit: scale.unit,
    keys,
    selected,
    from,
    to,
    scopeFrom: from,
    scopeTo: to,
    label: scale.period,
    short: scale.period,
    previous: index > 0 ? all[index - 1] : null,
    next: selected && index < all.length - 1 ? all[index + 1] : null,
  };
  if (!selected) return period;
  const span = bucketSpan(scale.unit, selected);
  return {
    ...period,
    scopeFrom: dayKey(span.start),
    scopeTo: dayKey(span.end),
    label: bucketTitle(scale.unit, selected),
    short: bucketShort(scale.unit, selected),
  };
}

/* --- 데이터 */

function normalize(raw) {
  const cache = new Map();
  const bucketsOf = (day) => {
    if (!cache.has(day)) {
      cache.set(day, Object.fromEntries(["day", "week", "month", "year"].map((unit) => [unit, bucketKey(unit, day)])));
    }
    return cache.get(day);
  };
  return {
    ...raw,
    lines: raw.lines.map(([day, kind, channel, instrument, category, amount]) => ({
      day,
      buckets: bucketsOf(day),
      kind,
      channel,
      instrument,
      category,
      amount,
    })),
    snapshots: raw.snapshots.map((item) => ({ ...item, buckets: bucketsOf(item.date) })),
    finance_snapshots: raw.finance_snapshots.map((item) => ({ ...item, buckets: bucketsOf(item.date) })),
  };
}

function defaultForms() {
  return {
    category: { share: "donut", trend: "line" },
    flow: { share: "sankey", trend: "bars" },
    assets: { share: "donut", trend: "line" },
    finance: { share: "donut", trend: "line" },
  };
}

function newState(overrides) {
  return {
    tab: "category",
    view: "share",
    scale: "month",
    bucket: null,
    start: null,
    end: null,
    focus: null,
    flowKind: "all",
    forms: defaultForms(),
    hidden: new Set(["assets", "liabilities"]),
    channels: new Set(),
    categories: new Set(),
    instruments: new Set(),
    groups: new Set(),
    financeGroups: new Set(),
    products: new Set(),
    allChosen: new Set(), // 모두 선택을 누른 조건. 조건이 비어 있으면 어차피 전부 보이므로 합계는 그대로다
    large: false, // 넓은 화면에서 차트 모양 고르기 위젯이 화면을 모두 쓰는지
    month: null, // 탭의 위젯들이 함께 보는 칸의 키. 단위에 따라 날, 주, 달, 해이고 없으면 마지막 칸이다
    unit: null, // 탭 화면의 단위. 없으면 수입·지출은 달, 자산과 금융은 기록마다다
    range: null, // 탭 화면의 기간. 없으면 수입·지출은 단위에 맞는 기간, 자산과 금융은 모든 기록이다
    span: 12, // 위젯들이 보는 개월 수
    record: null, // 자산과 금융 위젯들이 함께 보는 기록. 없으면 마지막 기록이다
    editing: false, // 위젯 편집 중인지
    ...overrides,
  };
}

function formsOf(state) {
  return state.view === "share" ? SHARE_FORMS : TREND_FORMS[state.tab];
}

function formOf(state) {
  return formsOf(state).find((form) => form.key === state.forms[state.tab][state.view]);
}

function fitsFlowKind(formKey, kind) {
  return !(kind === "all" ? SINGLE_KIND_FORMS : BOTH_KINDS_FORMS).has(formKey);
}

/* 고른 보기에 맞지 않는 모양은 그 보기의 기본 모양으로 돌린다. */
function chooseFlowKind(state, kind) {
  state.flowKind = kind;
  for (const view of VIEWS) {
    if (!fitsFlowKind(state.forms.flow[view.key], kind)) state.forms.flow[view.key] = FLOW_FORM_FALLBACK[view.key];
  }
}

/* 고른 모양을 지금 보기로 그릴 수 없으면 보기를 바꾼다. 어떤 버튼을 눌러도 막히지 않는다. */
function chooseForm(state, formKey) {
  state.forms[state.tab][state.view] = formKey;
  if (state.tab === "flow" && !fitsFlowKind(formKey, state.flowKind)) {
    chooseFlowKind(state, state.flowKind === "all" ? "spend" : "all");
  }
}

/* 지금 보는 쪽. 수입과 지출을 함께 볼 때는 null이다. */
function shownKind(state) {
  if (state.tab === "category") return "spend";
  return state.tab === "flow" && state.flowKind !== "all" ? state.flowKind : null;
}

/* 고른 조건을 주소에 남긴다. 거래 내역을 봤다가 뒤로 돌아와도 그대로 보인다. */
function stateFromQuery(search) {
  const params = new URLSearchParams(search);
  const state = newState();
  if (TABS.some((tab) => tab.key === params.get("tab"))) state.tab = params.get("tab");
  if (VIEWS.some((view) => view.key === params.get("view"))) state.view = params.get("view");
  if (SCALES.some((scale) => scale.key === params.get("scale"))) state.scale = params.get("scale");
  if (params.get("kind") === "income" || params.get("kind") === "spend") state.flowKind = params.get("kind");
  if (formsOf(state).some((form) => form.key === params.get("form"))) chooseForm(state, params.get("form"));
  state.bucket = params.get("bucket");
  state.start = validDay(params.get("start"));
  state.end = validDay(params.get("end"));
  state.focus = params.get("focus") || null;
  state.large = params.get("size") === "large";
  const month = params.get("month") || "";
  state.month = KEY_RE.test(month) && (month.length < 10 || validDay(month)) ? month : null;
  if (UNITS.includes(params.get("unit"))) state.unit = params.get("unit");
  if (RANGES.includes(params.get("range"))) state.range = params.get("range");
  if (params.get("span") === "6") state.span = 6;
  state.record = validDay(params.get("record"));
  state.editing = params.get("edit") === "1";
  for (const { name, set, parse } of URL_SETS) {
    for (const value of params.getAll(name)) state[set].add(parse(value));
  }
  for (const name of params.getAll("all")) {
    const entry = URL_SETS.find((item) => item.name === name);
    if (entry) state.allChosen.add(entry.set);
  }
  return state;
}

function stateToQuery(state) {
  const params = new URLSearchParams();
  const defaults = newState();
  if (state.tab !== defaults.tab) params.set("tab", state.tab);
  if (state.view !== defaults.view) params.set("view", state.view);
  if (state.scale !== defaults.scale) params.set("scale", state.scale);
  const form = state.forms[state.tab][state.view];
  if (form !== defaults.forms[state.tab][state.view]) params.set("form", form);
  if (state.bucket) params.set("bucket", state.bucket);
  if (state.scale === "custom" && state.start) params.set("start", state.start);
  if (state.scale === "custom" && state.end) params.set("end", state.end);
  if (state.flowKind !== defaults.flowKind) params.set("kind", state.flowKind);
  if (state.focus) params.set("focus", state.focus);
  if (state.large) params.set("size", "large");
  if (state.month) params.set("month", state.month);
  if (state.unit) params.set("unit", state.unit);
  if (state.range) params.set("range", state.range);
  if (state.span !== defaults.span) params.set("span", state.span);
  if (state.record) params.set("record", state.record);
  if (state.editing) params.set("edit", "1");
  for (const { name, set } of URL_SETS) {
    for (const value of state[set]) params.append(name, value);
    if (state.allChosen.has(set)) params.append("all", name);
  }
  return params.toString();
}

/* 차트 띠에 적는 제목. */
function chartTitle(state, period) {
  const subject = SUBJECT_NAMES[state.tab] || FLOW_KINDS.find((item) => item.key === (shownKind(state) || "all")).name;
  const form = formOf(state).key;
  if (state.view === "share") return `${period.short} ${subject} ${form === "sankey" ? "흐름" : "비중"}`;
  const unit = UNIT_NAMES[period.unit] || "";
  if (form === "difference") return `${unit} 수입 - 지출`.trim();
  if (form === "cumulative") return `누적 ${subject}`;
  if (form === "change") return `${unit} ${subject} 증감`.trim();
  if (form === "profit") return `${unit} 평가 손익`.trim();
  return `${unit} ${subject}`.trim();
}

function hasFilters(state) {
  return DIMENSIONS[state.tab].some((dimension) => state[dimension.state].size > 0);
}

/* 조건에서 고른 항목. 모두 선택은 지금 보이는 항목 전부다. */
function chosenKeys(state, name, shown) {
  return new Set(state.allChosen.has(name) ? shown : state[name]);
}

/* 고른 항목을 조건에 넣는다. 보이는 항목을 전부 고르면 모두 선택과 같다. */
function setChosen(state, name, keys, shown) {
  state[name].clear();
  state.allChosen.delete(name);
  if (shown.length && shown.every((key) => keys.has(key))) state.allChosen.add(name);
  else for (const key of keys) state[name].add(key);
}

function matches(line, state, ignored) {
  return FLOW_DIMENSIONS.every(
    ({ state: name, field }) => name === ignored || state[name].size === 0 || state[name].has(line[field]),
  );
}

function scopedLines(data, state, period, ignored) {
  if (!period.scopeFrom) return [];
  return data.lines.filter(
    (line) => line.day >= period.scopeFrom && line.day <= period.scopeTo && matches(line, state, ignored),
  );
}

function windowLines(data, state, period) {
  return data.lines.filter((line) => line.day >= period.from && line.day <= period.to && matches(line, state));
}

function sumOf(items, pick) {
  return items.reduce((sum, item) => sum + pick(item), 0);
}

function categoryOptions(data, kind) {
  return data.categories.filter((item) => item.kind === kind);
}

function categoryAmounts(lines, kind) {
  const amounts = new Map();
  for (const line of lines) {
    if (line.kind === kind) amounts.set(line.category, (amounts.get(line.category) || 0) + line.amount);
  }
  return amounts;
}

/* 묶음과 색. 색이 항목을 따라가도록 조건과 상관없이 전체 순위로 정한다. */
function compositionSeries(options) {
  const series = options.slice(0, COMPOSITION_SLOTS).map((option, index) => ({
    key: String(option.key),
    name: option.name,
    slot: `slot-${index + 1}`,
    members: [option.key],
  }));
  if (options.length > COMPOSITION_SLOTS) {
    series.push({
      key: REST,
      name: "그 외",
      slot: REST,
      members: options.slice(COMPOSITION_SLOTS).map((option) => option.key),
    });
  }
  return series;
}

function memberMap(series) {
  const map = new Map();
  for (const item of series) for (const member of item.members) map.set(member, item.key);
  return map;
}

function emptyRow(key, series) {
  return { key, values: Object.fromEntries(series.map((item) => [item.key, 0])) };
}

function emptyModel(state, period) {
  return {
    tab: state.tab,
    view: state.view,
    form: formOf(state),
    unit: period.unit,
    rows: [],
    series: [],
    legend: [],
    items: [],
    total: 0,
    caption: "",
    asOf: null,
    sankey: null,
    dots: false,
    tight: false,
  };
}

/* --- 기간 비중 */

function slotMap(options) {
  return new Map(options.map((option, index) => [option.key, index < COMPOSITION_SLOTS ? `slot-${index + 1}` : REST]));
}

/* 비중은 양수끼리만 낼 수 있다. 환불이 더 많아 음수인 항목은 빼고 따로 알린다. */
function shareItems(amounts, options) {
  const slots = slotMap(options);
  const items = options
    .map((option) => ({ key: option.key, name: option.name, value: amounts.get(option.key) || 0, slot: slots.get(option.key) }))
    .filter((item) => item.value > 0)
    .sort((a, b) => b.value - a.value);
  const total = sumOf(items, (item) => item.value);
  for (const item of items) item.share = item.value / total;
  return { items, total };
}

function foldRest(items) {
  const rest = items.filter((item) => item.slot === REST);
  if (rest.length < 2) return items;
  return [
    ...items.filter((item) => item.slot !== REST),
    {
      key: REST,
      name: "그 외",
      slot: REST,
      value: sumOf(rest, (item) => item.value),
      share: sumOf(rest, (item) => item.share || 0),
    },
  ];
}

/* 원형은 색 수만큼만 조각을 나눈다. 항목을 직접 골랐다면 고른 것을 그대로 보여 준다. */
function donutItems(model, state) {
  const picked =
    state.tab === "assets"
      ? state.groups.size
      : state.tab === "finance"
        ? state.financeGroups.size + state.products.size
        : state.categories.size;
  return picked ? model.items : foldRest(model.items);
}

/* 왼쪽 묶음이 가운데로 모였다가 오른쪽 묶음으로 나간다. 양쪽 합이 같아야 그릴 수 있다. */
function threeColumnSankey(left, centerName, right) {
  const center = { key: "center", name: centerName, column: 1, slot: REST };
  const place = (items, column, prefix) =>
    items.map((item) => ({ key: `${prefix}:${item.key}`, name: item.name, column, slot: item.slot, value: item.value }));
  const sources = place(left, 0, "in");
  const targets = place(right, 2, "out");
  return {
    nodes: [...sources, center, ...targets],
    links: [
      ...sources.map((node) => ({ source: node.key, target: center.key, value: node.value, slot: node.slot })),
      ...targets.map((node) => ({ source: center.key, target: node.key, value: node.value, slot: node.slot })),
    ],
  };
}

/* 결제 방식과 카테고리를 잇는 수입이나 지출. 환불이 더 많은 조합은 흐름으로 그릴 수 없어 뺀다. */
function kindSankey(data, lines, kind) {
  const series = compositionSeries(categoryOptions(data, kind));
  const owners = memberMap(series);
  const pairs = new Map();
  for (const line of lines) {
    if (line.kind !== kind) continue;
    const key = `${line.channel}\n${owners.get(line.category)}`;
    pairs.set(key, (pairs.get(key) || 0) + line.amount);
  }
  const kept = [];
  for (const [key, value] of pairs) {
    const [channel, category] = key.split("\n");
    if (value > 0) kept.push({ channel, category, value });
  }
  if (!kept.length) return null;
  const totalOf = (field, key) => sumOf(kept.filter((pair) => pair[field] === key), (pair) => pair.value);
  const channels = data.channels
    .map((item) => ({ key: `channel:${item.key}`, name: item.name, column: 0, slot: item.key, value: totalOf("channel", item.key) }))
    .filter((node) => node.value > 0)
    .sort((a, b) => b.value - a.value);
  const categories = series
    .map((item) => ({ key: `category:${item.key}`, name: item.name, column: 1, slot: item.slot, value: totalOf("category", item.key) }))
    .filter((node) => node.value > 0)
    .sort((a, b) => (a.slot === REST) - (b.slot === REST) || b.value - a.value);
  const slots = new Map(series.map((item) => [item.key, item.slot]));
  return {
    nodes: [...channels, ...categories],
    links: kept.map((pair) => ({
      source: `channel:${pair.channel}`,
      target: `category:${pair.category}`,
      value: pair.value,
      slot: slots.get(pair.category),
    })),
  };
}

/* 조건을 걸면 수입과 지출이 서로 다른 거래의 합이라 남은 돈에 뜻이 없다. 그때는 지출만 보여 준다. */
function flowSankey(data, state, lines) {
  if (state.flowKind !== "all") return kindSankey(data, lines, state.flowKind);
  if (hasFilters(state)) return kindSankey(data, lines, "spend");
  const income = shareItems(categoryAmounts(lines, "income"), categoryOptions(data, "income"));
  const spend = shareItems(categoryAmounts(lines, "spend"), categoryOptions(data, "spend"));
  if (!income.total && !spend.total) return null;
  const left = foldRest(income.items).map((item) => ({ ...item, slot: "income" }));
  const right = foldRest(spend.items);
  const balance = income.total - spend.total;
  if (balance < 0) left.push({ key: "shortfall", name: "모자란 금액", slot: "income", value: -balance });
  if (balance > 0) right.push({ key: "leftover", name: "수입 - 지출", slot: "leftover", value: balance });
  return threeColumnSankey(left, "수입", right);
}

function holdingSankey(assets, liabilities) {
  const left = foldRest(assets);
  const right = foldRest(liabilities).map((item) => ({ ...item, slot: "liabilities" }));
  const net = sumOf(left, (item) => item.value) - sumOf(right, (item) => item.value);
  if (!left.length && !right.length) return null;
  if (net > 0) right.unshift({ key: "net", name: "순자산", slot: "net", value: net });
  if (net < 0) left.push({ key: "shortfall", name: "자산보다 많은 부채", slot: "down", value: -net });
  return threeColumnSankey(left, net < 0 ? "총자산 + 모자란 만큼" : "총자산", right);
}

function flowShare(data, state, period, base) {
  const kind = shownKind(state) || "spend";
  const lines = scopedLines(data, state, period);
  const options = categoryOptions(data, kind);
  const amounts = categoryAmounts(lines, kind);
  const { items, total } = shareItems(amounts, options);
  base.items = items;
  base.total = total;
  base.caption = FLOW_KINDS.find((item) => item.key === kind).name;
  if (base.form.key === "sankey") {
    base.sankey = state.tab === "flow" ? flowSankey(data, state, lines) : kindSankey(data, lines, "spend");
  }
  return base;
}

/* 지금 범위의 마지막 기록과, 증감을 비교할 기록. 범위 앞의 마지막 기록을 먼저 쓰고,
   없으면 범위의 첫 기록과 비교한다. 범위 안에 기록이 없으면 그 전의 마지막 기록을 보여 준다. */
function holdingBounds(source, period) {
  let closing = null;
  let opening = null;
  let before = null;
  if (!period.scopeFrom) return { closing, start: null };
  for (const snapshot of source.snapshots) {
    if (snapshot.date > period.scopeTo) break;
    closing = snapshot;
    if (snapshot.date < period.scopeFrom) before = snapshot;
    else if (!opening) opening = snapshot;
  }
  // 탭 화면은 기간의 첫 기록(base)과 견준다. base가 null이면 견줄 기록이 없다.
  if ("base" in period) {
    const base = period.base ? source.snapshots.find((snapshot) => snapshot.date === period.base) : null;
    return { closing, start: base && base !== closing ? base : null };
  }
  const start = before || opening;
  return { closing, start: start === closing ? null : start };
}

function holdingShare(data, state, period, base) {
  const source = holdingSource(data, state);
  const { closing, start } = holdingBounds(source, period);
  if (!closing) return base;
  base.asOf = closing.date;
  const owners = new Map();
  for (const item of source.items) {
    if (!source.includes(item)) continue;
    const amount = closing.groups[item.key] || 0;
    const earlier = start ? start.groups[item.key] || 0 : 0;
    if (!amount && !earlier) continue;
    const owner = source.ownerOf(item);
    const entry = owners.get(owner.key) || { side: item.side, value: 0, change: start ? 0 : null, children: [] };
    entry.value += amount;
    if (start) entry.change += amount - earlier;
    entry.children.push({ key: item.key, name: item.name, value: amount, change: start ? amount - earlier : null });
    owners.set(owner.key, entry);
  }
  const sideOf = (side) => {
    const entries = [...owners].filter(([, entry]) => entry.side === side);
    const options = source.groups.filter((group) => group.side === side);
    const amounts = new Map(entries.map(([key, entry]) => [key, entry.value]));
    const result = shareItems(amounts, options);
    for (const item of result.items) {
      const entry = owners.get(item.key);
      item.change = entry.change;
      const children = entry.children.filter((child) => child.value > 0).sort((a, b) => b.value - a.value);
      if (state.tab === "finance" && children.length > 1) item.children = children;
    }
    return result;
  };
  const assets = sideOf("asset");
  const liabilities = sideOf("liability");
  const pool = assets.items.length ? assets : liabilities;
  base.items = pool.items;
  base.total = pool.total;
  base.caption = assets.items.length ? (state.tab === "finance" ? "금융 자산" : "자산") : "부채";
  if (base.form.key === "sankey") base.sankey = holdingSankey(assets.items, liabilities.items);
  return base;
}

function shareModel(data, state, period) {
  const base = emptyModel(state, period);
  if (!period.scopeFrom) return base;
  return state.tab === "category" || state.tab === "flow"
    ? flowShare(data, state, period, base)
    : holdingShare(data, state, period, base);
}

/* --- 추이 */

function legendOf(present, state, rows) {
  const visible = present.filter((item) => !state.hidden.has(item.key));
  return {
    series: visible.length ? visible : present,
    legend: present.map((item) => ({
      ...item,
      toggle: true,
      pressed: visible.length ? !state.hidden.has(item.key) : true,
      amount: rows ? sumOf(rows, (row) => row.values[item.key] || 0) : undefined,
    })),
  };
}

function compositionModel(data, state, period, base, kind) {
  const all = compositionSeries(categoryOptions(data, kind));
  const owners = memberMap(all);
  const byKey = new Map(period.keys.map((key) => [key, emptyRow(key, all)]));
  for (const line of windowLines(data, state, period)) {
    if (line.kind === kind) byKey.get(line.buckets[period.unit]).values[owners.get(line.category)] += line.amount;
  }
  const rows = [...byKey.values()];
  const present = all.filter((item) => rows.some((row) => row.values[item.key] !== 0));
  base.rows = rows;
  Object.assign(base, legendOf(present, state, rows));
  return base;
}

function flowModel(data, state, period) {
  const base = emptyModel(state, period);
  if (!period.keys.length) return base;
  if (base.form.key === "stack") return compositionModel(data, state, period, base, shownKind(state) || "spend");

  const totals = new Map(period.keys.map((key) => [key, { income: 0, spend: 0 }]));
  for (const line of windowLines(data, state, period)) totals.get(line.buckets[period.unit])[line.kind] += line.amount;

  if (base.form.key === "difference") {
    base.series = [{ key: "difference", name: "수입 - 지출", signed: true }];
    base.rows = period.keys.map((key) => ({ key, values: { difference: totals.get(key).income - totals.get(key).spend } }));
    base.legend = [
      { key: "up", name: "수입이 많음", slot: "up" },
      { key: "down", name: "지출이 많음", slot: "down" },
    ];
    return base;
  }

  base.series = SERIES.flow
    .filter((item) => state.flowKind === "all" || item.key === state.flowKind)
    .map((item) => ({ ...item, slot: item.key }));
  base.legend = base.series.length > 1 ? base.series : [];
  const running = { income: 0, spend: 0 };
  base.rows = period.keys.map((key) => {
    const values = {};
    for (const item of base.series) {
      running[item.key] += totals.get(key)[item.key];
      values[item.key] = base.form.key === "cumulative" ? running[item.key] : totals.get(key)[item.key];
    }
    return { key, values };
  });
  return base;
}

/* 자산 탭은 항목 단위, 금융 탭은 상품 단위로 같은 계산을 쓴다. */
function holdingSource(data, state) {
  if (state.tab === "finance") {
    return {
      snapshots: data.finance_snapshots,
      items: data.finance_items,
      groups: data.finance_groups,
      includes: (item, ignored) =>
        (ignored === "financeGroups" || !state.financeGroups.size || state.financeGroups.has(item.group)) &&
        (ignored === "products" || !state.products.size || state.products.has(item.key)),
      ownerOf: (item) => ({ key: item.group, name: item.group_name }),
    };
  }
  return {
    snapshots: data.snapshots,
    items: data.asset_groups,
    groups: data.asset_groups,
    includes: (item, ignored) => ignored === "groups" || !state.groups.size || state.groups.has(item.key),
    ownerOf: (item) => ({ key: item.key, name: item.name }),
  };
}

function holdingTotals(source, snapshot) {
  const result = { assets: 0, liabilities: 0, net: 0 };
  for (const item of source.items) {
    if (!source.includes(item)) continue;
    const amount = snapshot.groups[item.key] || 0;
    if (item.side === "asset") result.assets += amount;
    else result.liabilities += amount;
  }
  result.net = result.assets - result.liabilities;
  return result;
}

function holdingModel(data, state, period) {
  const source = holdingSource(data, state);
  const base = { ...emptyModel(state, period), dots: true };
  if (!period.keys.length) return base;

  const wanted = new Set(period.keys);
  const closings = new Map(); // 구간의 마지막 기록이 그 구간의 값이다
  const openings = new Map();
  const before = new Map();
  let previous = null; // 화면 밖의 기록과도 비교해야 첫 칸의 증감이 맞다
  for (const snapshot of source.snapshots) {
    const key = snapshot.buckets[period.unit];
    const record = { snapshot, totals: holdingTotals(source, snapshot) };
    if (wanted.has(key) && snapshot.date >= period.from && snapshot.date <= period.to) {
      if (!before.has(key)) {
        before.set(key, previous);
        openings.set(key, record);
      }
      closings.set(key, record);
    }
    previous = record;
  }
  // 구간 안에 기록이 여러 번 있으면 그 구간의 첫 기록과도 비교할 수 있다.
  const baseOf = (key) => {
    const opening = openings.get(key);
    return before.get(key) || (opening.snapshot === closings.get(key).snapshot ? null : opening);
  };

  if (base.form.key === "stack") {
    // 부채만 골랐으면 부채를 쌓는다. 색이 항목을 따라가도록 조건을 걸기 전의 순위로 묶는다.
    const side = source.items.some((item) => item.side === "asset" && source.includes(item)) ? "asset" : "liability";
    const included = source.items.filter((item) => item.side === side && source.includes(item));
    const all = compositionSeries(source.groups.filter((group) => group.side === side));
    const owners = memberMap(all);
    base.rows = period.keys.map((key) => {
      const closing = closings.get(key);
      if (!closing) return { key, values: Object.fromEntries(all.map((item) => [item.key, null])) };
      const row = emptyRow(key, all);
      for (const item of included) {
        row.values[owners.get(source.ownerOf(item).key)] += closing.snapshot.groups[item.key] || 0;
      }
      return row;
    });
    const present = all.filter((item) => base.rows.some((row) => row.values[item.key]));
    const { series, legend } = legendOf(present, state);
    const lastRow = [...base.rows].reverse().find((row) => all.some((item) => row.values[item.key] !== null));
    base.series = series;
    base.legend = legend.map((item) => ({ ...item, amount: lastRow ? lastRow.values[item.key] : 0 }));
    return base;
  }

  if (base.form.key === "change") {
    base.series = [{ key: "change", name: "증감", signed: true }];
    base.rows = period.keys.map((key) => {
      const closing = closings.get(key);
      const prior = closing ? baseOf(key) : null;
      return { key, values: { change: prior ? closing.totals.net - prior.totals.net : null } };
    });
    base.legend = [
      { key: "up", name: "늘어남", slot: "up" },
      { key: "down", name: "줄어듦", slot: "down" },
    ];
    return base;
  }

  if (base.form.key === "profit") {
    base.series = [{ key: "profit", name: "평가 손익", signed: true }];
    base.rows = period.keys.map((key) => ({ key, values: { profit: profitOf(data, closings.get(key)?.snapshot) } }));
    base.legend = [
      { key: "up", name: "이익", slot: "up" },
      { key: "down", name: "손실", slot: "down" },
    ];
    return base;
  }

  Object.assign(base, legendOf(SERIES[state.tab].map((item) => ({ ...item, slot: item.key })), state));
  base.tight = base.form.mark === "lines"; // 막대와 영역은 0에서 시작해야 크기를 속이지 않는다
  base.rows = period.keys.map((key) => {
    const closing = closings.get(key);
    return {
      key,
      values: Object.fromEntries(base.series.map((item) => [item.key, closing ? closing.totals[item.key] : null])),
    };
  });
  return base;
}

/* 투자 현황의 평가금액에서 투자 원금을 뺀 값. 상품별로 나눌 수 없어 조건과 상관없이 전체 값이다. */
function profitOf(data, snapshot) {
  const point = snapshot ? data.finance_investments[snapshot.date] : null;
  return point ? point.valuation - point.principal : null;
}

function trendModel(data, state, period) {
  if (state.tab === "category") {
    const base = emptyModel(state, period);
    return period.keys.length ? compositionModel(data, state, period, base, "spend") : base;
  }
  return state.tab === "flow" ? flowModel(data, state, period) : holdingModel(data, state, period);
}

function buildModel(data, state, period = resolvePeriod(data, state)) {
  return state.view === "share" ? shareModel(data, state, period) : trendModel(data, state, period);
}

/* --- 요약 칸 */

/* 데이터가 일부만 있는 구간은 평균을 낮추므로 뺀다. */
function averageOf(data, state, period, kind) {
  const range = rangeOf(data, state.tab);
  if (!range || !period.keys.length) return null;
  const first = parseDay(range.first > period.from ? range.first : period.from);
  const last = parseDay(range.last < period.to ? range.last : period.to);
  const full = new Set(
    period.keys.filter((key) => {
      const span = bucketSpan(period.unit, key);
      return span.start >= first && span.end <= last;
    }),
  );
  if (!full.size) return null;
  const counted = windowLines(data, state, period).filter((line) => line.kind === kind && full.has(line.buckets[period.unit]));
  return Math.round(sumOf(counted, (line) => line.amount) / full.size);
}

/* 한쪽만 볼 때의 요약: 가장 큰 카테고리, 직전 구간의 합계, 평균. */
function kindFigures(data, state, period, kind) {
  const lines = scopedLines(data, state, period);
  const { items } = shareItems(categoryAmounts(lines, kind), categoryOptions(data, kind));
  const earlier = period.selected && period.previous
    ? data.lines.filter((line) => line.kind === kind && line.buckets[period.unit] === period.previous && matches(line, state))
    : null;
  return {
    top: items.length ? { name: items[0].name, share: items[0].share } : null,
    previous: earlier ? sumOf(earlier, (line) => line.amount) : null,
    average: averageOf(data, state, period, kind),
  };
}

function flowSummary(data, state, period) {
  if (!period.scopeFrom) return { income: null, spend: null, top: null, previous: null, average: null };
  const lines = scopedLines(data, state, period);
  const total = (kind) => sumOf(lines.filter((line) => line.kind === kind), (line) => line.amount);
  return { income: total("income"), spend: total("spend"), ...kindFigures(data, state, period, shownKind(state) || "spend") };
}

function categorySummary(data, state, period) {
  if (!period.scopeFrom) return { spend: null, top: null, previous: null, average: null };
  const spent = scopedLines(data, state, period).filter((line) => line.kind === "spend");
  return { spend: sumOf(spent, (line) => line.amount), ...kindFigures(data, state, period, "spend") };
}

function holdingSummary(data, state, period) {
  const source = holdingSource(data, state);
  const { closing, start } = holdingBounds(source, period);
  if (!closing) return { net: null, assets: null, liabilities: null, change: null, profit: null };
  const totals = holdingTotals(source, closing);
  return {
    ...totals,
    change: start ? totals.net - holdingTotals(source, start).net : null,
    profit: state.tab === "finance" ? profitOf(data, closing) : null,
  };
}

function summarize(data, state, period = resolvePeriod(data, state)) {
  if (state.tab === "category") return categorySummary(data, state, period);
  return state.tab === "flow" ? flowSummary(data, state, period) : holdingSummary(data, state, period);
}

function holdingFacets(data, state, period, dimension) {
  const totals = new Map();
  const source = holdingSource(data, state);
  const { closing, start } = holdingBounds(source, period);
  if (!closing) return totals;
  for (const item of source.items) {
    if (!source.includes(item, dimension.state)) continue;
    const amount = closing.groups[item.key];
    const earlier = start ? start.groups[item.key] : undefined;
    if (amount === undefined && earlier === undefined) continue;
    const key = dimension === FINANCE_GROUP_DIMENSION ? item.group : item.key;
    const entry = totals.get(key) || { amount: 0, change: start ? 0 : null, lines: 0 };
    entry.amount += amount || 0;
    if (start) entry.change += (amount || 0) - (earlier || 0);
    entry.lines += 1;
    totals.set(key, entry);
  }
  return totals;
}

/* 버튼 옆 금액. 자기 조건은 빼고 나머지 조건만 적용해야 다른 버튼을 눌렀을 때의 값을 미리 알 수 있다. */
function facetTotals(data, state, period, dimension) {
  if (state.tab === "assets" || state.tab === "finance") return holdingFacets(data, state, period, dimension);
  const totals = new Map();
  const kind = shownKind(state);
  for (const line of scopedLines(data, state, period, dimension.state)) {
    if (kind && line.kind !== kind) continue;
    const key = line[dimension.field];
    const entry = totals.get(key) || { amount: 0, lines: 0 };
    // 둘 다 볼 때 결제 방식과 결제 수단의 금액은 지출만 적는다. 카테고리는 수입 카테고리가 따로 있다.
    if (kind || dimension.state === "categories" || line.kind === "spend") entry.amount += line.amount;
    entry.lines += 1;
    totals.set(key, entry);
  }
  return totals;
}

/* 자산과 금융 버튼에는 지금 금액 옆에 고른 기간의 증감도 적는다. */
function chipAmount(total) {
  if (!total) return "";
  const amount = total.amount ? compactWon(total.amount) : "";
  if (!total.change) return amount;
  return `${amount} ${total.change > 0 ? "+" : ""}${compactWon(total.change)}`.trim();
}

function niceStep(span) {
  const rough = Math.max(span, 1) / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const factor = [1, 2, 5, 10].find((item) => rough <= item * magnitude);
  return Math.max(Math.trunc(factor * magnitude), 1);
}

function axisScale(low, high) {
  const step = niceStep(high - low);
  const bottom = Math.floor(low / step) * step;
  let top = Math.ceil(high / step) * step;
  if (top === bottom) top += step;
  const ticks = [];
  for (let value = bottom; value <= top; value += step) ticks.push(value);
  return { bottom, top, ticks };
}

/* 칸은 있어도 값이 하나도 없으면 빈 축만 그리지 않는다. */
function hasValues(model) {
  return model.rows.some((row) => model.series.some((item) => row.values[item.key]));
}

function modelDomain(model) {
  const values = [];
  for (const row of model.rows) {
    if (model.form.mark === "stack") {
      values.push(sumOf(model.series, (item) => Math.max(row.values[item.key] || 0, 0)));
    } else {
      for (const item of model.series) if (row.values[item.key] !== null) values.push(row.values[item.key]);
    }
  }
  if (!values.length) return { low: 0, high: 0 };
  if (!model.tight) return { low: Math.min(0, ...values), high: Math.max(0, ...values) };
  const low = Math.min(...values);
  const high = Math.max(...values);
  if (low !== high) return { low, high };
  // 값이 하나뿐이면 범위가 없어 눈금이 겹치므로 위아래로 넓힌다.
  const margin = Math.max(Math.abs(high) * 0.05, 1);
  return { low: low - margin, high: high + margin };
}

/* 한 가지만 볼 때 그 항목을 그리는 묶음인지. 묶음은 "그 외"처럼 여러 항목을 품을 수 있다. */
function isFocusedSeries(state, item) {
  return !state.focus || item.key === state.focus || Boolean(item.members?.includes(state.focus));
}

/* 고른 항목이 지금 그림에 없으면 강조를 풀어야 빈 색으로 남지 않는다. */
function hasFocusTarget(model, state) {
  if (!state.focus) return true;
  if (state.view === "trend") return model.series.some((item) => isFocusedSeries(state, item));
  return state.focus === REST || model.items.some((item) => item.key === state.focus);
}

/* 거래 내역 화면은 조건마다 값을 하나만 받으므로, 그대로 옮길 수 있을 때만 주소를 만든다. */
function linesLink(data, state, period) {
  if (state.tab !== "category" && state.tab !== "flow") return null;
  if (!period.scopeFrom || (state.view === "trend" && !period.selected)) return null;
  if (FLOW_DIMENSIONS.some((dimension) => state[dimension.state].size > 1)) return null;
  if (state.focus === REST) return null;
  const params = new URLSearchParams({ start: period.scopeFrom, end: period.scopeTo });
  const pick = (values) => [...values][0];
  const key = state.focus || (state.categories.size ? pick(state.categories) : null);
  let nature = shownKind(state) || "spend";
  if (key) {
    const category = data.categories.find((item) => item.key === key);
    if (!category || key === "none") return null;
    params.set("category", key);
    nature = category.kind === "income" ? "income" : "spend";
  }
  if (state.channels.size) params.set("channel", pick(state.channels));
  if (state.instruments.size) params.set("instrument", pick(state.instruments));
  params.set("nature", nature);
  return `/transactions?${params}`;
}

/* --- 원형, 트리맵, 생키의 좌표 */

function round2(value) {
  return Math.round(value * 100) / 100;
}

function arcPath(cx, cy, outer, inner, start, end) {
  const large = end - start > Math.PI ? 1 : 0;
  const at = (radius, angle) => `${round2(cx + radius * Math.cos(angle))} ${round2(cy + radius * Math.sin(angle))}`;
  return (
    `M${at(outer, start)}A${outer} ${outer} 0 ${large} 1 ${at(outer, end)}` +
    `L${at(inner, end)}A${inner} ${inner} 0 ${large} 0 ${at(inner, start)}Z`
  );
}

function donutSlices(items, cx, cy, outer, inner) {
  const total = sumOf(items, (item) => item.value);
  if (total <= 0) return [];
  let angle = -Math.PI / 2;
  return items.map((item) => {
    const sweep = (2 * Math.PI * item.value) / total;
    const start = angle;
    const end = angle + sweep;
    angle = end;
    const middle = start + sweep / 2;
    const radius = (outer + inner) / 2;
    return {
      ...item,
      start,
      end,
      // 한 번의 호로는 온전한 원을 그릴 수 없어 둘로 나눈다.
      path:
        sweep >= 2 * Math.PI - 1e-6
          ? arcPath(cx, cy, outer, inner, start, start + Math.PI) + arcPath(cx, cy, outer, inner, start + Math.PI, end)
          : arcPath(cx, cy, outer, inner, start, end),
      labelX: round2(cx + radius * Math.cos(middle)),
      labelY: round2(cy + radius * Math.sin(middle)),
      showLabel: item.value / total >= DONUT_LABEL_SHARE,
    };
  });
}

/* 칸이 정사각형에 가깝도록 줄을 나눈다. 값이 큰 순서로 받아야 한다. */
function squarify(items, x, y, width, height) {
  const total = sumOf(items, (item) => item.value);
  if (total <= 0 || width <= 0 || height <= 0) return [];
  const scale = (width * height) / total;
  const cells = [];
  let left = x;
  let top = y;
  let restWidth = width;
  let restHeight = height;
  const worst = (row, side) => {
    const sum = sumOf(row, (entry) => entry.area);
    const largest = Math.max(...row.map((entry) => entry.area));
    const smallest = Math.min(...row.map((entry) => entry.area));
    return Math.max((side * side * largest) / (sum * sum), (sum * sum) / (side * side * smallest));
  };
  const place = (row) => {
    const sum = sumOf(row, (entry) => entry.area);
    if (restWidth >= restHeight) {
      const stripWidth = sum / restHeight;
      let cursor = top;
      for (const entry of row) {
        const cellHeight = entry.area / stripWidth;
        cells.push({ item: entry.item, x: left, y: cursor, width: stripWidth, height: cellHeight });
        cursor += cellHeight;
      }
      left += stripWidth;
      restWidth -= stripWidth;
    } else {
      const stripHeight = sum / restWidth;
      let cursor = left;
      for (const entry of row) {
        const cellWidth = entry.area / stripHeight;
        cells.push({ item: entry.item, x: cursor, y: top, width: cellWidth, height: stripHeight });
        cursor += cellWidth;
      }
      top += stripHeight;
      restHeight -= stripHeight;
    }
  };
  let row = [];
  for (const entry of items.map((item) => ({ item, area: item.value * scale }))) {
    const side = Math.min(restWidth, restHeight);
    if (row.length && worst([...row, entry], side) > worst(row, side)) {
      place(row);
      row = [];
    }
    row.push(entry);
  }
  if (row.length) place(row);
  return cells;
}

/* 묶음 안에 상품이 여럿이면 그 안을 다시 나눈다. 묶음 이름을 적을 자리가 있으면 위에 비워 둔다. */
function treemapCells(items, x, y, width, height) {
  const cells = [];
  for (const cell of squarify(items, x, y, width, height)) {
    const { item } = cell;
    const box = { x: cell.x, y: cell.y, width: cell.width, height: cell.height };
    if (!item.children) {
      cells.push({ ...item, ...box, group: item.key, leaf: true, header: 0 });
      continue;
    }
    const header = cell.height >= TREEMAP_HEADER * 2.5 && cell.width >= 72 ? TREEMAP_HEADER : 0;
    cells.push({ ...item, ...box, group: item.key, leaf: false, header });
    for (const child of squarify(item.children, cell.x, cell.y + header, cell.width, cell.height - header)) {
      cells.push({
        ...child.item,
        x: child.x,
        y: child.y,
        width: child.width,
        height: child.height,
        share: (child.item.value / item.value) * item.share,
        slot: item.slot,
        group: item.key,
        leaf: true,
        header: 0,
      });
    }
  }
  return cells;
}

function sankeyLayout(sankey, width, height, options = {}) {
  const nodeWidth = options.nodeWidth ?? 12;
  const gap = options.gap ?? 16;
  const pad = options.pad ?? 8;
  const nodes = sankey.nodes.map((node) => ({ ...node, inflow: 0, outflow: 0 }));
  const byKey = new Map(nodes.map((node) => [node.key, node]));
  for (const link of sankey.links) {
    byKey.get(link.source).outflow += link.value;
    byKey.get(link.target).inflow += link.value;
  }
  for (const node of nodes) node.value = Math.max(node.inflow, node.outflow);

  const columnIds = [...new Set(nodes.map((node) => node.column))].sort((a, b) => a - b);
  const columns = columnIds.map((id) => nodes.filter((node) => node.column === id));
  const usable = height - pad * 2;
  // 항목이 많으면 간격을 줄여 흐름이 굵기를 잃지 않게 한다.
  const gapOf = (column) => (column.length > 1 ? Math.min(gap, (usable * 0.4) / (column.length - 1)) : 0);
  const scale = Math.min(
    ...columns.map((column) => (usable - gapOf(column) * (column.length - 1)) / sumOf(column, (node) => node.value)),
  );
  const stepX = columns.length > 1 ? (width - pad * 2 - nodeWidth) / (columns.length - 1) : 0;
  const order = new Map();
  columns.forEach((column, index) => {
    const spaced = gapOf(column);
    const size = sumOf(column, (node) => node.value * scale) + spaced * (column.length - 1);
    let cursor = pad + (usable - size) / 2;
    for (const node of column) {
      node.x0 = round2(pad + index * stepX);
      node.x1 = round2(node.x0 + nodeWidth);
      node.y0 = cursor;
      node.y1 = cursor + node.value * scale;
      node.outY = node.y0;
      node.inY = node.y0;
      order.set(node.key, order.size);
      cursor = node.y1 + spaced;
    }
  });

  const links = sankey.links.map((link) => ({ ...link, width: link.value * scale }));
  const byOrder = (field) => (a, b) => order.get(a[field]) - order.get(b[field]);
  for (const link of [...links].sort(byOrder("target"))) {
    const source = byKey.get(link.source);
    link.sy = source.outY + link.width / 2;
    source.outY += link.width;
  }
  for (const link of [...links].sort(byOrder("source"))) {
    const target = byKey.get(link.target);
    link.ty = target.inY + link.width / 2;
    target.inY += link.width;
  }
  for (const link of links) {
    const x0 = byKey.get(link.source).x1;
    const x1 = byKey.get(link.target).x0;
    const middle = round2((x0 + x1) / 2);
    link.path = `M${x0} ${round2(link.sy)}C${middle} ${round2(link.sy)} ${middle} ${round2(link.ty)} ${x1} ${round2(link.ty)}`;
    link.strokeWidth = Math.max(round2(link.width), 1);
  }
  return { nodes, links, scale, lastColumn: columnIds[columnIds.length - 1] };
}

if (typeof module !== "undefined") {
  module.exports = {
    DIMENSIONS,
    axisScale,
    buildModel,
    chartTitle,
    chipAmount,
    chooseFlowKind,
    chooseForm,
    chosenKeys,
    compactWon,
    donutItems,
    donutSlices,
    facetTotals,
    formOf,
    hasFocusTarget,
    hasValues,
    isFocusedSeries,
    labelWon,
    linesLink,
    listBuckets,
    modelDomain,
    newState,
    normalize,
    resolvePeriod,
    sankeyLayout,
    setChosen,
    squarify,
    stateFromQuery,
    stateToQuery,
    summarize,
    treemapCells,
  };
}
