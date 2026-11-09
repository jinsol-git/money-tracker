/* 위젯의 계산. 화면을 건드리지 않아 node에서 그대로 시험한다. chart-data.js 다음에 읽는다.
   모양은 design-proposals/charts.html의 A, B, C안을 따르고, 숫자는 서버가 보낸 합계만 쓴다. */
"use strict";

const MONTH_RE = /^\d{4}-\d{2}$/;
const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];
const GRID_COLUMNS = 12;
const HEAT_EVEN = 0.04; // 평균과 이만큼 안쪽이면 같은 것으로 본다. 고정비가 무채색이 되는 이유다
const HEAT_FULL = 0.6; // 평균과 이만큼 벌어지면 색이 가장 진하다
const HEAT_MIN = 10;
const HEAT_RANGE = 52;
const TOP_INSTRUMENTS = 5;
const MATURITY_YEARS = 4; // 만기 위젯이 보여 주는 앞날
const MATURITY_MAX_YEARS = 10;
const TIMELINE_YEARS = 5;
const NARROW_MONTHS = 6; // 칸이 좁은 휴대폰에서 매트릭스가 보여 주는 달
const DEBT_GROUP = "group:debts"; // 금융 탭에서 대출·부채 묶음 전체를 고를 때의 키

/* --- 위젯 배치 */

/* 줄을 채우지 못한 위젯은 마지막 위젯을 늘려 빈칸을 남기지 않는다. 위젯을 빼거나 옮겨도 그대로 맞는다. */
function fillRows(items, field) {
  const spans = [];
  let row = [];
  let used = 0;
  const finish = () => {
    if (row.length && used < GRID_COLUMNS) spans[row[row.length - 1]] += GRID_COLUMNS - used;
    row = [];
    used = 0;
  };
  items.forEach((item, index) => {
    const span = Math.min(item[field], GRID_COLUMNS);
    if (used + span > GRID_COLUMNS) finish();
    spans[index] = span;
    row.push(index);
    used += span;
  });
  finish();
  return spans;
}

/* --- 단위와 칸 */

/* 탭 화면의 단위. 고르지 않았으면 수입·지출은 달, 자산과 금융은 기록마다다. */
function unitOf(state) {
  if (UNITS.includes(state.unit)) return state.unit;
  return state.tab === "assets" || state.tab === "finance" ? "day" : "month";
}

/* 칸 키가 가리키는 날. 해는 마지막 날, 달은 말일, 날과 주는 그날이다. 단위를 바꿔도 고른 때가 이어진다. */
function dayOfKey(key) {
  if (key.length === 4) return `${key}-12-31`;
  if (key.length === 7) return dayKey(bucketSpan("month", key).end);
  return key;
}

function wantedKey(state, unit) {
  const key = state.month || (MONTH_RE.test(state.bucket || "") ? state.bucket : null);
  return key ? bucketKey(unit, dayOfKey(key)) : null;
}

/* 탭 화면의 기간. 고르지 않았으면 수입·지출은 단위에 맞는 기간이고, 자산과 금융은 모든 기록이다. */
function rangeKeyOf(state, unit = unitOf(state)) {
  if (RANGES.includes(state.range)) return state.range;
  if (state.tab === "assets" || state.tab === "finance") return "max";
  return unit === "month" && state.span === 6 ? "6m" : UNIT_RANGES[unit];
}

/* 기간의 첫날. end는 기간의 마지막 날이다. 1개월이면 한 달 전 다음 날부터다. */
function rangeStart(range, end, first) {
  if (range === "max") return first;
  if (range === "ytd") return `${end.slice(0, 4)}-01-01`;
  const [year, month, day] = end.split("-").map(Number);
  const target = new Date(Date.UTC(year, month - 1 - RANGE_MONTHS[range], 1));
  const last = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  return dayKey(Date.UTC(target.getUTCFullYear(), target.getUTCMonth(), Math.min(day, last)) + DAY_MS);
}

/* 기간 안에서 시작하는 칸. 최대면 모든 칸이다. */
function bucketsWithin(unit, all, range, start, end) {
  if (range === "max") return all.filter((key) => dayKey(bucketSpan(unit, key).start) <= end);
  return all.filter((key) => {
    const begin = dayKey(bucketSpan(unit, key).start);
    return begin >= start && begin <= end;
  });
}

/* 고른 칸과 볼 칸들. 칸은 단위에 따라 날, 주, 달, 해다. 마지막 날까지의 기간을 보고,
   고른 칸이 그 밖이면 고른 칸에서 끝나는 기간으로 옮긴다. months는 칸의 키다. 달 단위로 시작해 이름이 남았다. */
function bucketAxis(data, state, unit) {
  const range = data.flow_range;
  if (!range) return null;
  const all = listBuckets(unit, range.first, range.last, 0);
  const wanted = wantedKey(state, unit);
  const selected = all.includes(wanted) ? wanted : all[all.length - 1];
  const index = all.indexOf(selected);
  const period = rangeKeyOf(state, unit);
  let months = bucketsWithin(unit, all, period, rangeStart(period, range.last, range.first), range.last);
  if (!months.includes(selected)) {
    const end = dayKey(bucketSpan(unit, selected).end);
    months = bucketsWithin(unit, all, period, rangeStart(period, end, range.first), end);
  }
  if (!months.length) months = [selected]; // 1개월을 연간으로 보면 해가 바뀌기 전까지 칸이 없다
  return {
    unit,
    range: period,
    all,
    months,
    selected,
    previous: index > 0 ? all[index - 1] : null,
    next: index < all.length - 1 ? all[index + 1] : null,
  };
}

/* 고른 기간을 이 단위로 나눠도 칸이 너무 많지 않은지. */
function unitFits(data, state, unit) {
  const axis = bucketAxis(data, state, unit);
  return !axis || axis.months.length <= MAX_BUCKETS;
}

/* 기간을 골랐을 때 쓸 단위. 기간마다 정한 단위가 맞지 않으면 더 큰 단위로 올린다. */
function fittingUnit(data, state, range) {
  const order = UNITS.slice(UNITS.indexOf(RANGE_UNITS[range]));
  return order.find((unit) => unitFits(data, { ...state, range }, unit)) || "year";
}

/* 기간 안의 기록. 마지막 기록에서 거꾸로 센다. 하나도 없으면 마지막 기록만 남긴다. */
function recordsWithin(records, state) {
  const period = rangeKeyOf(state);
  if (period === "max" || !records.length) return records;
  const start = rangeStart(period, records[records.length - 1].date, records[0].date);
  const kept = records.filter((record) => record.date >= start);
  return kept.length ? kept : records.slice(-1);
}

function monthAxis(data, state) {
  return bucketAxis(data, state, "month");
}

/* 단위마다 마지막 기록만 남긴다. 일간이면 모든 기록이다. */
function recordsOf(records, unit) {
  if (unit === "day") return records;
  const last = new Map();
  for (const record of records) last.set(record.buckets[unit], record);
  return [...last.values()];
}

/* 받침에 따라 조사를 붙인다. 전주와, 전달과. */
function withParticle(word, afterFinal, afterVowel) {
  const code = word.charCodeAt(word.length - 1) - 0xac00;
  return `${word}${code >= 0 && code < 11172 && code % 28 ? afterFinal : afterVowel}`;
}

function daysInMonth(month) {
  const [year, number] = month.split("-").map(Number);
  return new Date(Date.UTC(year, number, 0)).getUTCDate();
}

function monthDay(month, day) {
  return `${month}-${String(day).padStart(2, "0")}`;
}

function monthTick(month) {
  return `${Number(month.slice(5))}월`;
}

/* 데이터가 일부만 있는 칸은 평균을 낮추므로 뺀다. 전부 일부뿐이면 그대로 쓴다. */
function fullBucketsOf(range, unit, keys) {
  const first = parseDay(range.first);
  const last = parseDay(range.last);
  const full = keys.filter((key) => {
    const span = bucketSpan(unit, key);
    return span.start >= first && span.end <= last;
  });
  return full.length ? full : keys;
}

function fullMonthsOf(range, months) {
  return fullBucketsOf(range, "month", months);
}

/* --- 수입과 지출 */

function flowFigures(data, state, unit = unitOf(state)) {
  const axis = bucketAxis(data, state, unit);
  if (!axis) return null;
  const byMonth = new Map(axis.all.map((key) => [key, []])); // 칸마다의 거래
  for (const line of data.lines) {
    if (matches(line, state)) byMonth.get(line.buckets[unit])?.push(line);
  }
  const total = (month, kind, category) => {
    let sum = 0;
    for (const line of byMonth.get(month) || []) {
      if (line.kind === kind && (!category || line.category === category)) sum += line.amount;
    }
    return sum;
  };
  const options = categoryOptions(data, "spend");
  const slots = slotMap(options);
  return {
    axis,
    byMonth,
    range: data.flow_range,
    spend: (month, category) => total(month, "spend", category),
    income: (month, category) => total(month, "income", category),
    categories: options.map((option) => ({ key: option.key, name: option.name, slot: slots.get(option.key) })),
    incomeCategories: categoryOptions(data, "income").map((option) => ({ key: option.key, name: option.name })),
  };
}

/* 달 안을 보는 위젯(일별, 요일별, 누적 지출, 자주 쓴 곳)의 그림. 다른 단위로 볼 때는 고른 칸이 든 달을 본다. */
function monthFiguresOf(data, state, flow) {
  if (flow.axis.unit === "month") return flow;
  return flowFigures(data, { ...state, month: bucketKey("month", dayOfKey(flow.axis.selected)) }, "month");
}

function categoryOf(figures, key) {
  return figures.categories.find((category) => category.key === key) || null;
}

function monthlyAverage(figures, months, pick) {
  const counted = fullBucketsOf(figures.range, figures.axis.unit, months);
  return Math.round(sumOf(counted, pick) / counted.length);
}

function categoryRank(figures, month) {
  return figures.categories
    .map((category) => ({ ...category, value: figures.spend(month, category.key) }))
    .filter((category) => category.value > 0)
    .sort((a, b) => b.value - a.value);
}

/* 누적 막대의 묶음. 고른 항목이 "그 외"에 있으면 꺼내서 보여 준다. */
function categorySeriesOf(figures, focus) {
  const kept = figures.categories.filter((category) => category.slot !== REST || category.key === focus);
  const rest = figures.categories.filter((category) => category.slot === REST && category.key !== focus);
  const series = kept.map((category) => ({
    key: category.key,
    name: category.name,
    slot: category.slot === REST ? "leftover" : category.slot,
    valueOf: (month) => figures.spend(month, category.key),
  }));
  if (rest.length) {
    series.push({
      key: REST,
      name: "그 외",
      slot: REST,
      valueOf: (month) => sumOf(rest, (category) => figures.spend(month, category.key)),
    });
  }
  return series;
}

/* 칸의 색: 자기 평균보다 많으면 주황, 적으면 파랑. 평균 근처는 무채색이다. */
function heatOf(value, average) {
  if (!value || average <= 0) return { percent: 0, above: false };
  const ratio = (value - average) / average;
  if (Math.abs(ratio) < HEAT_EVEN) return { percent: 0, above: false };
  const strength = Math.min(1, Math.abs(ratio) / HEAT_FULL);
  return { percent: Math.round(HEAT_MIN + strength * HEAT_RANGE), above: ratio > 0 };
}

function matrixRows(figures, months) {
  const counted = fullBucketsOf(figures.range, figures.axis.unit, months);
  return figures.categories
    .map((category) => {
      const values = months.map((month) => figures.spend(month, category.key));
      const average = sumOf(counted, (month) => figures.spend(month, category.key)) / counted.length;
      return {
        ...category,
        average,
        total: sumOf(values, (value) => value),
        cells: months.map((month, index) => ({ month, value: values[index], heat: heatOf(values[index], average) })),
      };
    })
    .filter((row) => row.cells.some((cell) => cell.value !== 0))
    .sort((a, b) => b.total - a.total);
}

/* 칸에 적는 금액. 단위는 만원이고, 만원이 안 되면 소수로 적어 0으로 보이지 않게 한다. */
function matrixCellText(value) {
  if (!value) return "";
  const size = Math.abs(value);
  if (size >= 100000000) return labelWon(value);
  if (size >= 10000) return Math.round(value / 10000).toLocaleString("ko-KR");
  return String(Math.round(value / 1000) / 10);
}

function monthlyChanges(figures, month, previous) {
  if (!previous) return [];
  return figures.categories
    .map((category) => {
      const value = figures.spend(month, category.key);
      return { key: category.key, name: category.name, value, change: value - figures.spend(previous, category.key) };
    })
    .filter((item) => item.change !== 0)
    .sort((a, b) => b.change - a.change);
}

function dailyAmounts(figures, month, category) {
  const days = Array(daysInMonth(month)).fill(0);
  for (const line of figures.byMonth.get(month) || []) {
    if (line.kind === "spend" && (!category || line.category === category)) days[Number(line.day.slice(8)) - 1] += line.amount;
  }
  return days;
}

/* 요일별 하루 평균. 기록이 없는 날은 세지 않는다. 0이 일요일이다. */
function weekdayAverages(figures, months, category) {
  const sums = Array(7).fill(0);
  const counts = Array(7).fill(0);
  for (const month of months) {
    dailyAmounts(figures, month, category).forEach((value, index) => {
      const day = monthDay(month, index + 1);
      if (day < figures.range.first || day > figures.range.last) return;
      const weekday = new Date(parseDay(day)).getUTCDay();
      sums[weekday] += value;
      counts[weekday] += 1;
    });
  }
  return sums.map((sum, weekday) => (counts[weekday] ? Math.round(sum / counts[weekday]) : 0));
}

/* 결제 방식 비중과 결제 수단별 금액. */
function paymentFigures(data, figures, month, category) {
  const channels = new Map(data.channels.map((channel, index) => [channel.key, { ...channel, slot: `slot-${index + 1}`, value: 0 }]));
  const instruments = new Map();
  const names = new Map(data.instruments.map((item) => [item.id, item.name]));
  for (const line of figures.byMonth.get(month) || []) {
    if (line.kind !== "spend" || (category && line.category !== category)) continue;
    const channel = channels.get(line.channel);
    if (channel) channel.value += line.amount;
    const entry = instruments.get(line.instrument) || { key: line.instrument, name: names.get(line.instrument), channel: line.channel, value: 0 };
    entry.value += line.amount;
    instruments.set(line.instrument, entry);
  }
  const channelRows = [...channels.values()].filter((channel) => channel.value > 0);
  return {
    total: sumOf(channelRows, (channel) => channel.value),
    channels: channelRows,
    slotOf: (key) => channels.get(key)?.slot || REST,
    instruments: [...instruments.values()].filter((item) => item.value > 0).sort((a, b) => b.value - a.value).slice(0, TOP_INSTRUMENTS),
  };
}

/* 1일부터 말일까지 쌓인 지출. 내보낸 날 뒤는 비워 두어 선이 멈춘다. */
/* 칸 안의 날들. */
function bucketDays(unit, key) {
  const { start, end } = bucketSpan(unit, key);
  const days = [];
  for (let ms = start; ms <= end; ms += DAY_MS) days.push(dayKey(ms));
  return days;
}

/* 칸 안에서 날마다의 지출. figures는 같은 단위로 묶은 그림이어야 한다. */
function bucketDailyAmounts(figures, unit, key, category) {
  const days = bucketDays(unit, key);
  const at = new Map(days.map((day, index) => [day, index]));
  const values = days.map(() => 0);
  for (const line of figures.byMonth.get(key) || []) {
    const index = at.get(line.day);
    if (index !== undefined && line.kind === "spend" && (!category || line.category === category)) values[index] += line.amount;
  }
  return { days, values };
}

/* 누적 지출과 일별이 볼 칸. 하루 안에는 시간이 없으므로 일간은 그날이 든 달을 본다. */
function curveSourceOf(flow, monthFlow) {
  return flow.axis.unit === "day" ? { figures: monthFlow, unit: "month" } : { figures: flow, unit: flow.axis.unit };
}

/* 칸 안의 누적 지출. 마지막 기록 뒤의 날은 비운다. */
function bucketCurve(figures, unit, key) {
  const { days, values } = bucketDailyAmounts(figures, unit, key);
  let running = 0;
  return values.map((value, index) => {
    if (days[index] > figures.range.last) return null;
    running += value;
    return running;
  });
}

/* 평균에 쓸 지난 칸. 데이터가 일부만 있는 칸과 고른 칸은 뺀다. */
function pastFullBuckets(figures, unit, selected) {
  const first = parseDay(figures.range.first);
  const last = parseDay(figures.range.last);
  return figures.axis.months.filter((key) => {
    const span = bucketSpan(unit, key);
    return key !== selected && span.start >= first && span.end <= last;
  });
}

/* 칸들의 누적 지출 평균. 짧은 칸(28일까지 있는 달)은 마지막 합계를 이어 간다. */
function averageCurve(figures, unit, keys, length) {
  const sums = Array(length).fill(0);
  for (const key of keys) {
    const curve = bucketCurve(figures, unit, key);
    let last = 0;
    for (let index = 0; index < length; index += 1) {
      if (curve[index] !== null && curve[index] !== undefined) last = curve[index];
      sums[index] += last;
    }
  }
  return sums.map((sum) => Math.round(sum / keys.length));
}

/* 누적 지출 위젯의 그림. 고른 칸, 그 앞 칸, 기간 안의 지난 칸들의 평균이다. 지난 칸이 둘은 있어야 평균을 낸다. */
function cumulativeFigures(flow, monthFlow) {
  const { figures, unit } = curveSourceOf(flow, monthFlow);
  const { selected, previous } = figures.axis;
  const curve = bucketCurve(figures, unit, selected);
  const before = previous ? bucketCurve(figures, unit, previous) : null;
  const count = Math.max(curve.length, before ? before.length : 0);
  const past = pastFullBuckets(figures, unit, selected);
  return { unit, selected, previous, curve, before, count, past, average: past.length >= 2 ? averageCurve(figures, unit, past, count) : null };
}

function padCurve(curve, length) {
  return curve.concat(Array(Math.max(0, length - curve.length)).fill(null));
}

function lastValue(values) {
  for (let index = values.length - 1; index >= 0; index -= 1) {
    if (values[index] !== null && values[index] !== undefined) return values[index];
  }
  return null;
}

function runningNet(figures, months) {
  let running = 0;
  return months.map((month) => {
    running += figures.income(month) - figures.spend(month);
    return running;
  });
}

/* 그 달의 돈의 흐름. 수입과 지출을 함께 보는 생키이므로 수입만 보기 설정과 상관없다. */
function monthSankey(data, state, figures, month) {
  return flowSankey(data, { ...state, flowKind: "all" }, figures.byMonth.get(month) || []);
}

/* --- 자산 */

/* 짧은 이름. 좁은 칸의 눈금에 쓴다. */
function shortGroupName(name) {
  return name.replace(/ \(직접 입력\)$/, "").replace(/ 자산$/, "");
}

function recordIndex(days, wanted) {
  const index = days.indexOf(wanted);
  return index >= 0 ? index : days.length - 1;
}

function assetFigures(data, state) {
  if (!data.snapshots.length) return null;
  const shownState = { ...state, tab: "assets" };
  const snapshots = recordsWithin(recordsOf(data.snapshots, unitOf(shownState)), shownState);
  const source = holdingSource(data, { ...state, tab: "assets" });
  const days = snapshots.map((snapshot) => snapshot.date);
  const slots = slotMap(data.asset_groups.filter((group) => group.side === "asset"));
  const shown = data.asset_groups.filter((group) => source.includes(group));
  const entry = (group) => ({
    key: group.key,
    name: group.name,
    short: shortGroupName(group.name),
    bucket: group.bucket,
    slot: slots.get(group.key),
    values: snapshots.map((snapshot) => snapshot.groups[group.key] || 0),
  });
  const groups = shown.filter((group) => group.side === "asset").map(entry);
  const debts = shown.filter((group) => group.side === "liability").map(entry);
  const assets = days.map((_, index) => sumOf(groups, (group) => group.values[index]));
  const debt = days.map((_, index) => sumOf(debts, (group) => group.values[index]));
  const rec = recordIndex(days, state.record);
  return {
    days,
    groups,
    debts,
    assets,
    debt,
    net: assets.map((value, index) => value - debt[index]),
    rec,
    base: rec > 0 ? 0 : null, // 견주는 기록. 기간의 첫 기록이다
  };
}

/* 기간의 첫 기록부터 어떤 항목 때문에 순자산이 변했는지. 부채가 줄면 순자산에는 늘어난 것이다. */
function waterfallSteps(figures) {
  const { rec, base } = figures;
  if (base === null) return [];
  return [
    ...figures.groups.map((group) => ({ key: group.key, name: group.name, short: group.short, value: group.values[rec] - group.values[base] })),
    { key: "debt", name: "부채", short: "부채", value: figures.debt[base] - figures.debt[rec] },
  ]
    .filter((step) => step.value !== 0)
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
}

/* 기간의 첫 기록부터 고른 기록까지의 변화. 한 달이 넘으면 한 달 평균도 낸다. rate는 퍼센트다. */
function periodChange(figures, values) {
  const { base, rec, days } = figures;
  if (base === null) return null;
  const change = values[rec] - values[base];
  const months = (parseDay(days[rec]) - parseDay(days[base])) / (DAY_MS * 30.4375);
  return {
    from: days[base],
    change,
    rate: values[base] ? (change / Math.abs(values[base])) * 100 : null,
    monthly: months >= 1 ? Math.round(change / months) : null,
  };
}

function bucketRows(data, figures) {
  return data.asset_buckets.map((bucket, index) => ({
    key: bucket.key,
    name: bucket.name,
    slot: `slot-${index + 1}`,
    value: sumOf(figures.groups.filter((group) => group.bucket === bucket.key), (group) => group.values[figures.rec]),
  }));
}

/* 조건과 상관없는 월평균 지출. 현금성 자산이 몇 달치인지 볼 때 쓴다. */
function averageSpendOf(data) {
  const range = data.flow_range;
  if (!range) return 0;
  const months = fullMonthsOf(range, listBuckets("month", range.first, range.last, 0));
  const counted = new Set(months);
  const spent = sumOf(data.lines.filter((line) => line.kind === "spend" && counted.has(line.buckets.month)), (line) => line.amount);
  return Math.round(spent / months.length);
}

/* --- 금융 */

function nameKey(name) {
  return String(name || "").replace(/\s+/g, "").toLowerCase();
}

/* 재무현황의 상품과 현황 표의 행을 잇는다. 이름이 같은 것을 먼저, 그다음 금액이 같은 것을 찾는다. */
function linkDetails(items, rows, amountOf) {
  const links = new Map();
  const free = new Set(rows);
  const take = (item, test) => {
    if (links.has(item.key)) return;
    const found = [...free].find(test);
    if (found) {
      links.set(item.key, found);
      free.delete(found);
    }
  };
  for (const item of items) take(item, (row) => nameKey(row.name) === nameKey(item.name));
  for (const item of items) {
    take(item, (row) => {
      const a = nameKey(row.name);
      const b = nameKey(item.name);
      return Boolean(a && b) && (a.includes(b) || b.includes(a));
    });
  }
  for (const item of items) take(item, (row) => amountOf(row) !== null && amountOf(row) === item.latest);
  return links;
}

/* 보여 줄 금리. 내보내기의 표가 먼저이고, 납입액으로 구한 값은 표가 비었을 때 대신하고 표를 대조한다.
   표 값이 0.0385처럼 소수로 들어왔으면 납입액으로 구한 값과 비교해 알아낸다. */
function effectiveRate(table, implied) {
  const hasTable = table !== null && table !== undefined;
  const hasImplied = implied !== null && implied !== undefined;
  if (!hasTable) return hasImplied ? { value: implied, source: "payments" } : { value: null, source: null };
  if (hasImplied && Math.abs(table * 100 - implied) < Math.abs(table - implied)) {
    return { value: Math.round(table * 10000) / 100, source: "table" };
  }
  return { value: table, source: "table" };
}

/* 대출 하나. 재무현황의 잔액에 현황 표의 행과 연결한 납입을 합친다. 없는 값은 null이다. */
function loanFigures(item, row, link) {
  const implied = link?.implied?.rate ?? null;
  const rate = effectiveRate(row?.rate, implied);
  return {
    key: item.key,
    name: row?.name || item.name,
    org: row?.org || "",
    type: row?.type || "",
    principal: row?.principal ?? null,
    balance: item.value,
    rate: rate.value,
    rateSource: rate.source,
    impliedRate: implied,
    reason: link?.reason ?? null,
    payments: link?.payments ?? [],
    months: link?.months ?? [],
    start: row?.start ?? null,
    end: row?.end ?? null,
  };
}

function investmentReturn(row) {
  if (row.principal === null || row.valuation === null || !row.principal) return null;
  return ((row.valuation - row.principal) / row.principal) * 100;
}

function financeFigures(data, state) {
  if (!data.finance_snapshots.length) return null;
  const shownState = { ...state, tab: "finance" };
  const snapshots = recordsWithin(recordsOf(data.finance_snapshots, unitOf(shownState)), shownState);
  const source = holdingSource(data, { ...state, tab: "finance" });
  const days = snapshots.map((snapshot) => snapshot.date);
  const rec = recordIndex(days, state.record);
  const details = data.finance_details;
  const assetItems = data.finance_items.filter((item) => item.side === "asset");
  const debtItems = data.finance_items.filter((item) => item.side === "liability");
  const investments = linkDetails(assetItems, details.investment, (row) => row.valuation);
  const loans = linkDetails(debtItems, details.loan, (row) => row.balance);
  const valuesOf = (item) => snapshots.map((snapshot) => snapshot.groups[item.key] || 0);
  const assetGroups = data.finance_groups.filter((group) => group.side === "asset");
  const slots = slotMap(assetGroups);
  const groups = assetGroups
    .map((group) => ({
      key: group.key,
      name: group.name,
      short: shortGroupName(group.name),
      slot: slots.get(group.key),
      items: assetItems
        .filter((item) => item.group === group.key && source.includes(item))
        .map((item) => {
          const values = valuesOf(item);
          const investment = investments.get(item.key) || null;
          return {
            key: item.key,
            name: item.name,
            groupName: group.name,
            slot: slots.get(group.key),
            kind: investment ? "invest" : item.instrument ? "account" : "product",
            instrument: item.instrument,
            investment,
            values,
            value: values[rec],
          };
        })
        .filter((item) => item.value > 0 || item.values.some(Boolean)),
    }))
    .filter((group) => group.items.length);
  const debts = debtItems
    .filter((item) => source.includes(item))
    .map((item) => {
      const values = valuesOf(item);
      const value = values[rec];
      const loan = loanFigures({ key: item.key, name: item.name, value }, loans.get(item.key), data.loan_payments[item.key]);
      return { key: item.key, name: item.name, groupName: item.group_name, kind: "loan", loan, values, value };
    });
  const matched = new Set(loans.values());
  const otherLoans = details.loan
    .filter((row) => !matched.has(row))
    .map((row) => loanFigures({ key: `loan:${row.name}`, name: row.name, value: row.balance }, row, null));
  // 묶음도 하나의 항목처럼 고를 수 있게 기록마다의 합계를 둔다.
  const totalOf = (items) => days.map((_, index) => sumOf(items, (item) => item.values[index]));
  for (const group of groups) {
    group.kind = "group";
    group.values = totalOf(group.items);
    group.value = group.values[rec];
  }
  const debtValues = totalOf(debts);
  const debtGroup = debts.length
    ? { key: DEBT_GROUP, name: "대출·부채", short: "대출", kind: "group", slot: "slot-2", items: debts, values: debtValues, value: debtValues[rec] }
    : null;
  const assets = sumOf(groups, (group) => sumOf(group.items, (item) => item.value));
  const debt = sumOf(debts, (item) => item.value);
  const points = days.map((day) => data.finance_investments[day] || null);
  const valuation = points.map((point) => (point ? point.valuation : null));
  const principal = points.map((point) => (point ? point.principal : null));
  return {
    days,
    rec,
    base: rec > 0 ? 0 : null, // 견주는 기록. 기간의 첫 기록이다
    groups,
    debts,
    debtGroup,
    assets,
    debt,
    net: assets - debt,
    netValues: days.map((_, index) => {
      const snapshot = snapshots[index];
      return sumOf(data.finance_items.filter((item) => source.includes(item)), (item) => (item.side === "asset" ? 1 : -1) * (snapshot.groups[item.key] || 0));
    }),
    valuation,
    principal,
    profit: points[rec] ? points[rec].valuation - points[rec].principal : null,
    investments: details.investment,
    loans: [...debts.map((item) => item.loan), ...otherLoans],
    insurance: details.insurance,
  };
}

function financeProducts(figures) {
  return figures.groups.flatMap((group) => group.items);
}

function findFinanceItem(figures, key) {
  if (!key) return null;
  return (
    figures.groups.find((group) => group.key === key) ||
    (figures.debtGroup?.key === key ? figures.debtGroup : null) ||
    financeProducts(figures).find((item) => item.key === key) ||
    figures.debts.find((item) => item.key === key) ||
    figures.insurance.map((row) => ({ ...row, key: `insurance:${row.name}`, kind: "insurance" })).find((row) => row.key === key) ||
    null
  );
}

function monthsBetween(from, to) {
  return Math.round((parseDay(to) - parseDay(from)) / (30.44 * DAY_MS));
}

/* 기간 중 지난 비율. */
function elapsedShare(start, end, today) {
  const first = parseDay(start);
  const last = parseDay(end);
  if (last <= first) return 1;
  return Math.max(0, Math.min(1, (parseDay(today) - first) / (last - first)));
}

/* 앞으로 오는 대출과 투자 만기. */
function maturityItems(figures, today) {
  const year = Number(today.slice(0, 4));
  const limit = `${year + MATURITY_MAX_YEARS}-12-31`;
  const items = [
    ...figures.loans.filter((row) => row.end).map((row) => ({ name: row.name, end: row.end, kind: "대출 잔액", slot: "slot-2", value: row.balance })),
    ...figures.investments.filter((row) => row.end).map((row) => ({ name: row.name, end: row.end, kind: "평가금액", slot: "slot-1", value: row.valuation })),
  ]
    .filter((item) => item.end >= today && item.end <= limit)
    .sort((a, b) => parseDay(a.end) - parseDay(b.end));
  const lastYear = items.length ? Number(items[items.length - 1].end.slice(0, 4)) : year;
  return { items, from: today, to: `${Math.max(year + MATURITY_YEARS, lastYear)}-12-31` };
}

/* 금융 타임라인의 줄. 가입일이 있는 상품만 그릴 수 있다. */
function timelineRows(figures) {
  const rows = [];
  const section = (title, entries) => {
    const dated = entries.filter((entry) => entry.start);
    if (!dated.length) return;
    rows.push({ group: title });
    rows.push(...dated);
  };
  section(
    "투자",
    figures.investments.map((row) => ({ key: `investment:${row.name}`, name: row.name, start: row.start, end: row.end, slot: "slot-1", value: row.valuation, startName: "가입", extra: investmentReturn(row) === null ? null : { name: "수익률", value: `${investmentReturn(row) > 0 ? "+" : ""}${investmentReturn(row).toFixed(1)}%` } })),
  );
  section(
    "대출",
    figures.loans.map((row) => ({ key: `loan:${row.name}`, name: row.name, start: row.start, end: row.end, slot: "slot-2", value: row.balance, startName: "신규", extra: row.rate === null ? null : { name: "금리", value: `${row.rate}%` } })),
  );
  section(
    "보험",
    figures.insurance.map((row) => ({ key: `insurance:${row.name}`, name: row.name, start: row.start, end: row.end, slot: "muted", value: row.paid, startName: "계약", extra: row.status ? { name: "상태", value: row.status } : null })),
  );
  return rows;
}

function timelineSpan(rows, today) {
  const starts = rows.filter((row) => row.start).map((row) => row.start);
  const first = starts.length ? starts.reduce((a, b) => (a < b ? a : b)) : today;
  return { from: `${first.slice(0, 4)}-01-01`, to: `${Number(today.slice(0, 4)) + TIMELINE_YEARS}-12-31` };
}
