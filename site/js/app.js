const NAV = [
  ["overview", "总览"],
  ["scanner", "自选"],
  ["portfolio", "持仓"],
  ["decisions", "决策"],
  ["reports", "报告"],
  ["market", "市场"],
  ["sentiment", "情绪"],
  ["events", "事件"],
  ["profile", "画像"],
  ["framework", "框架"],
];
const TABS = [
  ["overview", "总览"],
  ["scanner", "自选"],
  ["portfolio", "持仓"],
  ["decisions", "决策"],
  ["reports", "报告"],
];
const TITLES = Object.fromEntries(NAV);
const REGIME = { GREEN: "进攻", YELLOW: "谨慎", RED: "防守" };
const THEME_SHORT = {
  "半导体 / 存储": "半导体",
  "能源 / 电力": "能源",
  "大型科技 / 平台": "科技",
  "贵金属 / 金属": "金属",
  "金融 / 券商 / 金融科技": "金融",
  "宽基 / 风格 / 利率": "宽基",
  "消费": "消费",
  "医疗 / 生物": "医疗",
  "国防 / 航天": "国防",
  "加密相关": "加密",
  "量子计算": "量子",
  "中概 / 海外": "中概",
  "不动产": "地产",
};
const PARTS = {
  spy_above_50: "SPY 在 50 日均线之上",
  spy_above_200_stack: "SPY 在 200 日之上，且 50 日高于 200 日",
  qqq_above_50: "QQQ 在 50 日均线之上",
  breadth: "宽度过线（观察列表近似）",
  vix_contango: "VIX 低于 VIX3M",
};

const state = {
  route: "overview",
  ticker: null,
  sort: { key: "list", dir: -1 },
  psort: { key: "list", dir: -1 },
  seg: "all",
  q: "",
  report: null,
  detail: null,
  bars: [],
  dockTab: "quote",
  editing: false,
  planMode: "full",
  earnWatch: false,
};
let DATA = {};
let documentGen = 0;
let dataReady = false;
let dockGen = 0;
let pending = { add: [], remove: [] };
let watchChain = Promise.resolve();
let drag = null;
let blockOpen = false;
const researchBusy = new Set();
let researchTimer = 0;
let researchTicker = "";
let researchBaseline = "";

const $ = (sel, root = document) => root.querySelector(sel);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function num(x, d = 2) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  return Number(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}
function px(x) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  const n = Math.abs(Number(x));
  const d = n >= 100 ? 2 : n >= 1 ? 2 : 3;
  return num(x, d);
}
function pct(x, d = 1) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  const v = Number(x) * 100;
  return (v > 0 ? "+" : "") + v.toFixed(d) + "%";
}
function usd(x, d = 0) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  return Number(x).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: d });
}
function cls(x) {
  if (x == null || Number(x) === 0) return "";
  return Number(x) > 0 ? "up" : "down";
}
function pillCls(x) {
  if (x == null || Number(x) === 0) return "flat";
  return Number(x) > 0 ? "up" : "down";
}
function cname(ticker) {
  const added = pending.add.find((item) => item.ticker === ticker && item.name && item.name !== ticker);
  if (added) return added.name;
  return (DATA.names && DATA.names[ticker]) || ticker;
}
function scanOf(ticker) {
  const row = (DATA.scanner?.rows || []).find((r) => r.ticker === ticker);
  if (row) return row;
  const item = pending.add.find((entry) => entry.ticker === ticker);
  return item ? placeholderRow(item) : undefined;
}
function reasonText(call) {
  if (!call) return "";
  if (call.block) return plain(call.block);
  return plain(String(call.line || "").replace(/^[A-Z0-9.-]+：/, ""));
}
function missingConfirm(call) {
  const items = (call && call.confirmation) || [];
  if (!items.length) return "";
  const missing = items.filter((item) => !item.pass).map((item) => plain(item.label));
  const score = items.filter((item) => item.pass).length;
  if (!missing.length) return `买入确认分 ${score}/3，三项都过了。`;
  return `买入确认分 ${score}/3，还缺：${missing.join("、")}。`;
}
function whyHtml(call) {
  if (!call) return "";
  const miss = missingConfirm(call);
  let text = reasonText(call);
  if (miss) text = text.replace(/[，,]?\s*买入确认分 \d\/3/g, "");
  const bits = [`${esc(text)}${miss ? ` ${esc(miss)}` : ""}`];
  if (call.code && TERMS[call.code]) bits.push(tip(call.code));
  if ((call.confirmation || []).length) bits.push(tip("C1", miss));
  return bits.join("");
}
function shortTheme(name) {
  return THEME_SHORT[name] || String(name || "").split(/[/／]/)[0].trim();
}
function posKey(pos) {
  if (pos.instrument === "option") return `opt:${pos.underlying || pos.ticker}:${pos.expiry}:${pos.strike}:${pos.right}`;
  return `stk:${pos.ticker}`;
}
function posByKey(key) {
  return (DATA.portfolio?.positions || []).find((p) => posKey(p) === key);
}

function route() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [name, arg] = hash.split("/");
  state.route = NAV.some(([id]) => id === name) ? name : "overview";
  state.ticker = arg || null;
}

function applyQuote() {
  const stored = localStorage.getItem("invest-buddy-quote");
  const fromFile = DATA.ui && DATA.ui.quote_color;
  const mode = stored === "green_up" || stored === "red_up"
    ? stored
    : (fromFile === "green_up" || fromFile === "red_up" ? fromFile : "red_up");
  document.documentElement.dataset.quote = mode;
}

const THEME_CYCLE = ["system", "light", "dark"];
const THEME_LABEL = { system: "跟随系统", light: "浅色", dark: "深色" };

function themeMode() {
  const stored = localStorage.getItem("invest-buddy-theme");
  if (stored === "light" || stored === "dark" || stored === "system") return stored;
  return document.documentElement.dataset.themeMode || "system";
}

function resolvedTheme(mode) {
  if (mode === "light" || mode === "dark") return mode;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function redrawChart() {
  const canvas = $("#chart");
  if (canvas && state.bars && state.bars.length) drawChart(canvas, state.bars);
}

function applyTheme(mode) {
  const m = mode || themeMode();
  document.documentElement.dataset.themeMode = m;
  document.documentElement.dataset.theme = resolvedTheme(m);
  redrawChart();
}

function bindThemeMedia() {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => {
    if (themeMode() === "system") applyTheme("system");
  };
  if (mq.addEventListener) mq.addEventListener("change", onChange);
  else mq.addListener(onChange);
}

async function load() {
  const names = ["meta", "market", "sentiment", "regime", "scanner", "portfolio", "events", "decisions", "reports", "ui", "sparks", "names", "research", "themes", "risk", "earnings_risk"];
  const optional = new Set(["ui", "sparks", "names", "research", "themes", "risk", "earnings_risk"]);
  const entries = await Promise.all(names.map(async (name) => {
    const res = await fetch(`data/${name}.json`);
    if (!res.ok) {
      if (optional.has(name)) return [name, null];
      throw new Error(name);
    }
    return [name, await res.json()];
  }));
  DATA = Object.fromEntries(entries);
  DATA.ui = DATA.ui || { quote_color: "red_up", title: "Invest Buddy" };
  DATA.sparks = DATA.sparks || {};
  DATA.names = DATA.names || {};
  DATA.research = DATA.research || { tickers: {}, estimated_usd: 0, stale_days: 3, price_note: "", backend: "openai-compatible" };
  DATA.themes = DATA.themes || { groups: [] };
  DATA.risk = DATA.risk || null;
  DATA.earnings_risk = DATA.earnings_risk || null;
  applyRiskEdits();
  loadPending();
  reconcilePending();
}

function header() {
  const meta = DATA.meta || {};
  const mode = document.documentElement.dataset.quote;
  const theme = themeMode();
  const day = (meta.bar_date || "").slice(5);
  $("#top").innerHTML = `
    <div class="top-heading"><span class="workspace-name">${esc((DATA.ui && DATA.ui.title) || "Invest Buddy")}</span><h1>${esc(TITLES[state.route] || "总览")}</h1></div>
    <div class="top-actions">
      <span class="top-date" title="行情收盘日期"><span class="date-label">行情日期 </span>${esc(day || "—")}</span>
      <button type="button" class="theme-toggle" id="theme" aria-label="外观 ${THEME_LABEL[theme]}">${THEME_LABEL[theme]}</button>
      <button type="button" class="quote-toggle" id="quote">${mode === "green_up" ? "绿涨" : "红涨"}</button>
    </div>`;
  $("#theme").addEventListener("click", () => {
    const cur = themeMode();
    const next = THEME_CYCLE[(THEME_CYCLE.indexOf(cur) + 1) % THEME_CYCLE.length];
    localStorage.setItem("invest-buddy-theme", next);
    applyTheme(next);
    render();
    $("#theme").focus({ preventScroll: true });
  });
  $("#quote").addEventListener("click", () => {
    const next = document.documentElement.dataset.quote === "red_up" ? "green_up" : "red_up";
    localStorage.setItem("invest-buddy-quote", next);
    applyQuote();
    render();
    $("#quote").focus({ preventScroll: true });
  });
}

function nav() {
  $("#side").innerHTML = NAV.map(([id, label]) =>
    `<button type="button" data-go="${id}" ${state.route === id ? 'aria-current="page"' : ""}>${label}</button>`
  ).join("");
  $("#tab").innerHTML = TABS.map(([id, label]) =>
    `<button type="button" data-go="${id}" ${state.route === id ? 'aria-current="page"' : ""}>${label}</button>`
  ).join("");
}

function tape() {
  const items = (DATA.market?.groups || []).flatMap((g) => g.items || []);
  const spx = items.find((it) => it.symbol === "^GSPC");
  const el = $("#tape");
  if (!spx) { el.hidden = true; return; }
  el.hidden = false;
  el.className = cls(spx.change_1d);
  el.innerHTML = `<span>标普500</span><span><b>${px(spx.price)}</b> <span class="${cls(spx.change_1d)}">${pct(spx.change_1d)}</span></span>`;
  el.dataset.go = "market";
}

function go(id) {
  if (!NAV.some(([k]) => k === id)) return;
  closeDetail();
  if (location.hash === "#/" + id || (id === "overview" && (location.hash === "" || location.hash === "#"))) {
    route();
    render();
    return;
  }
  location.hash = "#/" + id;
}

function render() {
  if (!dataReady) return;
  documentGen += 1;
  $("#main").dataset.page = state.route;
  const pages = { overview, market, sentiment, portfolio, scanner, events, decisions, profile, framework, reports };
  if (state.route !== "scanner") {
    state.editing = false;
    document.body.classList.remove("editing");
    closeSheet();
    closeModal();
  }
  header();
  nav();
  tape();
  (pages[state.route] || overview)();
  if (state.detail) paintDock();
}

function spark(ticker, change) {
  const series = DATA.sparks[ticker];
  if (!series || series.length < 2) return `<svg class="spark" viewBox="0 0 72 32" aria-hidden="true"></svg>`;
  const min = Math.min(...series);
  const max = Math.max(...series);
  const span = max - min || 1;
  const pts = series.map((v, i) => {
    const x = (i / (series.length - 1)) * 72;
    const y = 30 - ((v - min) / span) * 28;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const tone = cls(change);
  return `<svg class="spark ${tone}" viewBox="0 0 72 32" aria-hidden="true"><polyline fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" points="${pts}"/></svg>`;
}

function actionTag(call) {
  if (!call || !call.action_label) return "";
  const kind = ["buy", "trim", "sell", "watch", "hold"].includes(call.action) ? call.action : "watch";
  return `<span class="tag ${kind}">${esc(call.action_label)}</span>`;
}

function quoteCell(price, change, sub, priceChange) {
  const tone = priceChange === undefined ? change : priceChange;
  return `<span class="qt">
    <span class="last ${cls(tone)}">${px(price)}</span>
    <span class="pill ${pillCls(change)}">${pct(change)}</span>
    <span class="sub">${sub}</span>
  </span>`;
}

function scanRow(r) {
  const name = cname(r.ticker);
  const held = !!(r.held || isStockHeld(r.ticker));
  const tags = [actionTag(r.call)];
  if (r.earnings_in_days != null && r.earnings_in_days <= 10) tags.push(`<span class="tag earn">财报</span>`);
  if (held) tags.push(`<span class="tag held">持仓</span>`);
  const subName = name === r.ticker ? esc(shortTheme(r.theme_name)) : esc(r.ticker);
  const controls = held ? "" : `<button type="button" class="more" data-more="${esc(r.ticker)}" aria-label="更多">⋯</button><button type="button" class="edit-del" data-del="${esc(r.ticker)}">删除</button><div class="menu"><button type="button" data-del="${esc(r.ticker)}">删除</button></div>`;
  const reveal = held ? "" : `<button type="button" class="swipe-del" data-del="${esc(r.ticker)}">删除</button>`;
  return `<div class="swipe" data-ticker="${esc(r.ticker)}" data-held="${held ? "1" : "0"}">
    ${reveal}
    <div class="swipe-row">
      <button type="button" class="row" data-open="scan" data-id="${esc(r.ticker)}">
        <span class="who">
          <span class="nm"><span class="name">${esc(name)}</span></span>
          <span class="tk">${subName}${tags.join("")}</span>
          <span class="why">${whyHtml(r.call)}</span>
        </span>
        ${spark(r.ticker, r.change_1d)}
        ${quoteCell(r.price, r.change_1d, "距21日均线 " + pct(r.dist_ema21))}
      </button>
      ${controls}
    </div>
  </div>`;
}

function holdRow(pos) {
  const und = pos.underlying || pos.ticker;
  const scan = scanOf(und);
  const ch = scan ? scan.change_1d : null;
  const opt = pos.instrument === "option";
  const name = opt ? `${cname(und)} ${pos.strike}${pos.right}` : cname(pos.ticker);
  const qty = opt
    ? `${pos.contracts < 0 ? "空" : ""} ${Math.abs(pos.contracts)} 张`
    : `${pos.shares} 股`;
  const tags = [actionTag(pos.call)];
  if (opt) tags.push(`<span class="tag watch">期权</span>`);
  else if (scan && scan.earnings_in_days != null && scan.earnings_in_days <= 10) tags.push(`<span class="tag earn">财报</span>`);
  const pnlBit = pos.pnl == null ? "成本未填" : usd(pos.pnl);
  const why = pos.call ? reasonText(pos.call) : plain((pos.exit_notes && pos.exit_notes[0]) || firstSentence(pos.note));
  const sub = opt ? `正股 ${pct(ch)}` : usd(pos.market_value);
  return `<button type="button" class="row" data-open="pos" data-id="${esc(posKey(pos))}">
    <span class="who">
      <span class="nm"><span class="name">${esc(name)}</span></span>
      <span class="tk">${esc(und)} · ${opt ? esc(pos.expiry) + " · " : ""}${esc(qty)}${tags.join("")}</span>
      <span class="why">${pos.call ? whyHtml(pos.call) : esc(why)} · ${pct(pos.weight, 1)} · ${esc(pnlBit)} · ${pos.stop == null ? "止损未填" : "止损 " + px(pos.stop)}</span>
    </span>
    ${spark(und, ch)}
    ${quoteCell(pos.price, opt ? null : ch, sub)}
  </button>`;
}

function sortBar(scope) {
  const st = scope === "pos" ? state.psort : state.sort;
  const btn = (key, label) => {
    const on = st.key === key;
    const arrow = on ? (st.dir > 0 ? " ↑" : " ↓") : "";
    return `<button type="button" data-sort="${key}" data-scope="${scope}" class="${on ? "on" : ""}">${label}${arrow}</button>`;
  };
  return `<div class="sortbar">${btn("price", "价格")}${btn("change_1d", "涨跌幅")}</div>`;
}

function sortRows(rows, st, changeOf) {
  if (st.key !== "price" && st.key !== "change_1d") return rows;
  const value = (row) => {
    if (st.key === "change_1d") return changeOf ? changeOf(row) : row.change_1d;
    return row.price;
  };
  return rows.slice().sort((a, b) => {
    const av = value(a);
    const bv = value(b);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return (av - bv) * st.dir;
  });
}

function sharePct(x) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  return (Number(x) * 100).toFixed(0) + "%";
}
function riskEdits() {
  try { return JSON.parse(localStorage.getItem("invest-buddy-risk") || "{}"); }
  catch (err) { return {}; }
}
function saveRiskEdits(edits) {
  localStorage.setItem("invest-buddy-risk", JSON.stringify(edits));
}
function applyRiskEdits() {
  const edits = riskEdits();
  for (const pos of DATA.portfolio?.positions || []) {
    const edit = edits[posKey(pos)];
    if (!edit) continue;
    if (pos.avg_cost == null && edit.avg_cost != null) pos.avg_cost = edit.avg_cost;
    if (pos.stop == null && edit.stop != null) pos.stop = edit.stop;
    if (!pos.entry_date && edit.entry_date) pos.entry_date = edit.entry_date;
    if (edit.stop_placed) pos.stop_placed = true;
    settleRules(pos);
  }
}
function settleRules(pos) {
  if (!pos || pos.instrument === "option") return;
  const left = new Set(pos.rules_null || []);
  if (pos.stop != null) left.delete("X1");
  if (pos.stop != null && pos.avg_cost != null) left.delete("A1");
  if (pos.horizon === "swing" && pos.stop != null && pos.avg_cost != null && pos.entry_date) left.delete("X5");
  pos.rules_null = [...left];
  if (!pos.call || !pos.call.checks) return;
  pos.call.checks = pos.call.checks.map((item) => {
    if (item.label === "X1" && pos.stop != null && item.pass == null) {
      return { ...item, pass: false, text: "止损价已填。下一次刷新会用当天最低价判断是否碰到。" };
    }
    if (item.label === "A1" && pos.stop != null && pos.avg_cost != null && item.pass == null) {
      return { ...item, pass: false, text: "成本和止损已填。下一次刷新会判断浮盈有没有到一倍风险。" };
    }
    return item;
  });
  if (pos.call.pending) pos.call.pending = pos.call.pending.filter((item) => left.has(item.label));
}
function liveStops() {
  const edits = riskEdits();
  return ((DATA.risk && DATA.risk.stops) || []).map((item) => {
    const edit = edits[item.key] || {};
    const stop = edit.stop != null ? Number(edit.stop) : item.stop;
    const pos = (DATA.portfolio?.positions || []).find((row) => posKey(row) === item.key);
    let risk = item.risk_usd;
    if (stop != null && pos && pos.price != null) risk = Math.max(0, Number(pos.price) - stop) * Number(pos.shares || 0);
    return {
      ...item,
      stop,
      avg_cost: edit.avg_cost != null ? edit.avg_cost : item.avg_cost,
      entry_date: edit.entry_date || item.entry_date,
      stop_placed: edit.stop_placed != null ? !!edit.stop_placed : !!item.stop_placed,
      risk_usd: risk,
      suggest: item.suggest,
    };
  });
}
function riskTotals(stops) {
  const cap = DATA.risk && DATA.risk.open_risk_cap_usd;
  const missing = stops.filter((item) => item.stop == null);
  const partial = stops.reduce((sum, item) => sum + (item.risk_usd || 0), 0);
  return { cap, missing: missing.length, filled: stops.length - missing.length, partial, complete: missing.length === 0 };
}
function stopBanner() {
  const risk = DATA.risk;
  if (!risk) return "";
  const stops = liveStops();
  const totals = riskTotals(stops);
  const unplaced = stops.filter((item) => item.stop != null && !item.stop_placed);
  const lines = unplaced.map((item) => `<label class="stop-line"><input type="checkbox" data-stop-placed="${esc(item.key)}"> <span>请在券商挂好止损单：${esc(item.ticker)} ${px(item.stop)}</span></label>`).join("");
  const head = unplaced.length
    ? lines
    : `<p>止损价还没填完，所以还列不出每一只要在券商挂的价格。</p>`;
  const riskLine = totals.complete
    ? `到止损的风险合计 ${usd(totals.partial)}，上限 ${usd(totals.cap)}（净值的 4%）。`
    : `已填 ${totals.filled} 只，还有 ${totals.missing} 只没有止损，风险合计还不完整。已填部分约 ${usd(totals.partial)}，全部填上后的上限是 ${usd(totals.cap)}。`;
  const width = totals.cap ? Math.max(0, Math.min(100, totals.partial / totals.cap * 100)) : 0;
  return `<section class="risk-panel" aria-label="止损">
    <h2 class="sec">止损${tip("open_risk")}</h2>
    ${head}
    <p>${riskLine}</p>
    <div class="track-bar" aria-hidden="true"><i style="width:${width.toFixed(1)}%"></i></div>
  </section>`;
}
function planGroups(packed, mode) {
  const steps = packed.steps || [];
  if (mode !== "staged") return [{ title: "一步到位", steps }];
  const done = steps.filter((step) => step.status === "done");
  const open = steps.filter((step) => step.status !== "done");
  const groups = [];
  if (done.length) groups.push({ title: "已经做到", steps: done });
  let cursor = 0;
  for (const phase of packed.phases || []) {
    groups.push({ title: phase.title, steps: open.slice(cursor, cursor + (phase.count || 0)) });
    cursor += phase.count || 0;
  }
  if (cursor < open.length) groups.push({ title: "其余", steps: open.slice(cursor) });
  return groups.filter((group) => group.steps.length);
}
function planStep(step) {
  const done = step.status === "done";
  const after = step.after || {};
  const name = after.worst_name || {};
  const theme = after.worst_theme || {};
  const money = Number(step.proceeds);
  const moneyText = money < 0 ? `估计付出 ${usd(Math.abs(money))}` : `估计收回 ${usd(money)}`;
  return `<article class="plan-step${done ? " done" : ""}">
    <b>${done ? "✅ " : ""}${esc(step.title)}</b>
    <p>${esc(step.why || "")}</p>
    <p class="plan-meta"><span>${moneyText}</span><span>之后杠杆 ${sharePct(after.gross)}</span><span>最大单票 ${esc(name.ticker || "—")} ${sharePct(name.weight)}</span><span>最大主题 ${esc(theme.name || "—")} ${sharePct(theme.weight)}</span></p>
  </article>`;
}
function planPanel() {
  const risk = DATA.risk;
  if (!risk || !risk.plans) return "";
  const mode = state.planMode === "staged" ? "staged" : "full";
  const packed = risk.plans[mode] || risk.plans.full;
  const before = packed.before || {};
  const progress = packed.progress || {};
  const total = progress.total || 0;
  const done = progress.done || 0;
  const width = total ? Math.round(done / total * 100) : 0;
  const name = before.worst_name || {};
  const theme = before.worst_theme || {};
  return `<section class="risk-panel" aria-label="减仓计划">
    <h2 class="sec">减仓计划${tip("deleverage")}</h2>
    <p class="note">${esc(risk.notice || "这是计划，不是下单。")}</p>
    <div class="segs" aria-label="计划版本">
      <button type="button" data-planmode="full" ${mode === "full" ? 'aria-current="page"' : ""}>一步到位</button>
      <button type="button" data-planmode="staged" ${mode === "staged" ? 'aria-current="page"' : ""}>财报前分两步</button>
    </div>
    <div class="risk-meters">
      <div><span>杠杆 ${tip("delta_exp")}</span><b>${sharePct(before.gross)}</b><small>目标 ≤100%。市值杠杆 ${sharePct(before.gross_market_value)}</small></div>
      <div><span>最大单票</span><b>${esc(name.ticker || "—")} ${sharePct(name.weight)}</b><small>上限 10%</small></div>
      <div><span>最大主题</span><b>${esc(theme.name || "—")} ${sharePct(theme.weight)}</b><small>上限 25%</small></div>
    </div>
    <p class="plan-meta">相对当前持仓：${done}/${total} 步已完成。还要处理约 ${usd(progress.remaining_proceeds)}。</p>
    <div class="track-bar" aria-hidden="true"><i style="width:${width}%"></i></div>
    ${planGroups(packed, mode).map((group) => `<h3>${esc(group.title)}</h3>${group.steps.map(planStep).join("")}`).join("")}
    ${planFinal(packed)}
  </section>`;
}
function planFinal(packed) {
  const final = packed.final;
  if (!final || !(final.rows || []).length) return "";
  const rows = final.rows.map((row) => {
    const split = row.stock_weight != null && Math.abs((row.weight || 0) - row.stock_weight) >= 0.015;
    const sub = split ? `<small>正股 ${sharePct(row.stock_weight)}</small>` : "";
    return `<div class="plan-hold"><span><b>${esc(row.ticker)}</b> ${esc(row.kept)}</span><span>${sharePct(row.weight)}${sub}</span></div>`;
  }).join("");
  const exited = (final.exited || []).length ? `<p class="note">规则卖出、按计划清掉：${esc(final.exited.join("、"))}。</p>` : "";
  const alt = (packed.alternatives || []).map((item) => `<p class="${item.warning ? "hot" : "note"}">${esc(item.text)}</p>`).join("");
  return `<h3>做完后留下的仓</h3>${rows}<p class="plan-meta"><span>现金 ${usd(final.cash)}</span><span>杠杆 ${sharePct(final.gross)}</span></p>${exited}${alt}`;
}
function flagText(flags) {
  if (!flags || !flags.length) return "";
  return `会碰到 ${flags.map((flag) => flag.label).join("、")} 回撤熔断`;
}
function earnCard(item) {
  const flags = [...(item.flags_implied || []), ...(item.flags_adverse || [])];
  const hot = flags.length ? " hot" : "";
  const when = item.days == null ? "日期未定" : item.days > 0 ? `还有 ${item.days} 天` : item.days === 0 ? "就是这一天" : "已过";
  const hist = item.history || {};
  const implied = item.implied;
  return `<article class="plan-step${hot}">
    <b>${esc(cname(item.ticker))} ${esc(item.ticker)}</b>
    <p class="plan-meta"><span>${esc(item.date || "日期未定")} · ${esc(item.session_cn || "")}</span><span>${esc(when)}</span><span>${item.confirmed ? "已核对" : "待确认"}</span></p>
    <p>近 ${hist.n || 0} 次财报${tip("earn_gap")}：平均绝对涨跌 ${hist.avg_abs == null ? "没有算出" : sharePct(hist.avg_abs)}，最大不利 ${hist.max_adverse == null ? "没有算出" : pct(hist.max_adverse)}</p>
    <p>隐含波动${tip("implied_move")}：${implied ? sharePct(implied.move) + "（" + esc(implied.expiry) + " 平值跨式）" : "这次没取到"}</p>
    <p>按当前仓位，隐含波动潜在亏损 ${usd(item.loss_implied)}（${sharePct(item.loss_implied_pct)}），历史最大不利 ${usd(item.loss_adverse)}（${sharePct(item.loss_adverse_pct)}）。</p>
    ${hot ? `<p class="hot">${esc([
      (item.flags_implied || []).length ? "隐含波动" + flagText(item.flags_implied) : "",
      (item.flags_adverse || []).length ? "历史不利" + flagText(item.flags_adverse) : "",
    ].filter(Boolean).join("；"))}</p>` : ""}
  </article>`;
}
function earningsPanel() {
  const earn = DATA.earnings_risk;
  if (!earn) return `<section class="risk-panel"><h2 class="sec">财报风险</h2><p class="note">这次没有财报风险数据。</p></section>`;
  const cluster = earn.cluster || {};
  const holdings = (earn.holdings || []).filter((item) => !item.etf);
  const etf = (earn.holdings || []).filter((item) => item.etf);
  const watch = earn.watchlist || [];
  const clusterFlags = [
    (cluster.flags_implied || []).length ? "隐含波动" + flagText(cluster.flags_implied) : "",
    (cluster.flags_adverse || []).length ? "历史不利" + flagText(cluster.flags_adverse) : "",
  ].filter(Boolean).join("；");
  return `<section class="earn-panel" aria-label="财报风险">
    <h2 class="sec">财报风险${tip("earn_gap")}</h2>
    <p class="note">${esc(earn.note || "")}</p>
    <h3>${esc(cluster.start || "10/15")}–${esc(cluster.end || "10/29")} 持仓</h3>
    <p>${(cluster.tickers || []).length ? esc(cluster.tickers.join("、")) : "这个窗口没有持仓财报。"} ${esc(cluster.note || "")}</p>
    <p>若跳空同向：隐含波动合计 ${usd(cluster.loss_implied)}（${sharePct(cluster.loss_implied_pct)}），历史最大不利合计 ${usd(cluster.loss_adverse)}（${sharePct(cluster.loss_adverse_pct)}）。</p>
    ${clusterFlags ? `<p class="hot">${esc(clusterFlags)}</p>` : ""}
    ${holdings.map(earnCard).join("") || `<p class="note">持仓没有算出下一次财报。</p>`}
    ${etf.map((item) => `<p class="note">${esc(item.ticker)}：${esc(item.note || "ETF，没有个股财报")}</p>`).join("")}
    <button type="button" class="secondary" data-earn-watch>${state.earnWatch ? "收起观察列表" : `观察列表 ${watch.length} 只`}</button>
    ${state.earnWatch ? watch.map((item) => `<p class="plan-meta"><span>${esc(item.ticker)}</span><span>${esc(item.date || "日期未定")}</span><span>${item.days == null ? "" : "还有 " + item.days + " 天"}</span><span>${esc(item.note || "")}</span></p>`).join("") : ""}
  </section>`;
}
function riskForm(pos) {
  if (!pos) return "";
  const stops = liveStops();
  const known = stops.find((item) => item.key === posKey(pos)) || {};
  const suggest = known.suggest || {};
  const opt = pos.instrument === "option";
  return `<form class="risk-form" data-risk-key="${esc(posKey(pos))}">
    <h3>成本、止损、建仓日${tip("stop_suggest")}</h3>
    <label>成本<input name="avg_cost" inputmode="decimal" value="${pos.avg_cost == null ? "" : esc(String(pos.avg_cost))}" placeholder="每股成本，不知道留空"></label>
    <label>止损价<input name="stop" inputmode="decimal" value="${pos.stop == null ? "" : esc(String(pos.stop))}" placeholder="券商止损价"></label>
    ${opt ? "" : `<label>建仓日<input name="entry_date" type="date" value="${esc(pos.entry_date || "")}"></label>`}
    <p class="note">${esc(suggest.note || "没有建议价。")}${suggest.price != null ? ` 建议 ${px(suggest.price)}。` : ""}</p>
    ${suggest.price != null ? `<button type="button" class="secondary" data-fill-stop="${esc(String(suggest.price))}">填入建议价 ${px(suggest.price)}</button>` : ""}
    <label class="stop-line"><input type="checkbox" name="stop_placed" ${pos.stop_placed ? "checked" : ""}> 已在券商挂单</label>
    <button type="button" class="research-go" data-save-risk>保存到账户文件</button>
    <p class="note">保存后固定止损和加仓不再显示未定。突破失败仍要交易日志里的枢轴价，填这些数消不了那条。下一次行情刷新才会用当天最低价重算。</p>
  </form>`;
}
async function saveRiskForm(button) {
  const form = button.closest("form");
  if (!form || !state.detail) return;
  const pos = posByKey(state.detail.id);
  if (!pos) return;
  const read = (name) => {
    const field = form.querySelector(`[name="${name}"]`);
    if (!field) return null;
    const text = String(field.value || "").trim();
    return text === "" ? null : text;
  };
  const body = {
    op: "risk",
    ticker: pos.underlying || pos.ticker,
    instrument: pos.instrument === "option" ? "option" : "stock",
    expiry: pos.expiry || null,
    strike: pos.strike == null ? null : pos.strike,
    right: pos.right || null,
    avg_cost: read("avg_cost"),
    stop: read("stop"),
    entry_date: read("entry_date"),
    stop_placed: !!(form.querySelector('[name="stop_placed"]') || {}).checked,
  };
  button.disabled = true;
  try {
    const res = await fetch("/api/watchlist", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || "没有写上");
    const edits = riskEdits();
    edits[posKey(pos)] = {
      avg_cost: body.avg_cost == null ? null : Number(body.avg_cost),
      stop: body.stop == null ? null : Number(body.stop),
      entry_date: body.entry_date,
      stop_placed: body.stop_placed,
    };
    saveRiskEdits(edits);
    applyRiskEdits();
    toast("已写入账户文件。下一次行情刷新后按这个止损重算。");
    render();
  } catch (err) {
    toast(err.message || "没有写上");
    button.disabled = false;
  }
}

function overview() {
  const p = DATA.portfolio;
  const r = DATA.regime;
  const meta = DATA.meta;
  const snap = p.snapshot || {};
  const us = (DATA.market.groups || []).find((g) => g.id === "us");
  const rows = DATA.scanner.rows || [];
  const entries = rows.filter((row) => row.call && row.call.code && /^E/.test(row.call.code)).slice(0, 6);
  const exits = rows.filter((row) => row.held && row.call && (row.call.action === "sell" || row.call.action === "trim"));
  const when = snapLabel(snap.as_of);
  $("#main").innerHTML = `
    <section class="account account-overview" aria-label="账户概览">
      <div class="account-label">估算净值</div>
      <div class="nlv">${usd(p.nlv_estimated)}</div>
      <div class="day ${cls(snap.day_pnl)}">当日盈亏 ${signedUsd(snap.day_pnl)} <span class="snapshot-time">${esc(when)}</span></div>
      <dl class="account-stats">
        <div><dt>杠杆</dt><dd>${Math.round((p.gross || 0) * 100)}%</dd></div>
        <div><dt>市场环境${tip("regime")}</dt><dd><i class="lamp ${esc(r.regime)}"></i>${esc(regimePhrase(r))}</dd></div>
        <div><dt>现金</dt><dd>${usd(p.cash)}</dd></div>
      </dl>
    </section>
    ${planPanel()}
    ${stopBanner()}
    ${sentimentStrip()}
    <div class="cards">
      ${(us?.items || []).map((it) => `<button type="button" class="idx" data-go="market">
        <span class="sym">${esc(it.name)}</span>
        <strong class="${cls(it.change_1d)}">${px(it.price)}</strong>
        <span class="pill ${pillCls(it.change_1d)}">${pct(it.change_1d)}</span>
      </button>`).join("")}
    </div>
    ${p.breaches?.length ? `<div class="banner">${p.breaches.map((b) => esc(b.detail)).join("<br>")}</div>` : ""}
    <div class="overview-actions">
    <section class="action-panel" aria-labelledby="hold-actions-title">
    <h2 class="sec" id="hold-actions-title">持仓动作<span class="section-count">${exits.length}</span></h2>
    ${exits.length ? exits.map(scanRow).join("") : `<p class="note">没有卖出或减仓。</p>`}
    </section>
    <section class="action-panel" aria-labelledby="entry-actions-title">
    <h2 class="sec" id="entry-actions-title">技术买点<span class="section-count">${entries.length}</span></h2>
    ${entries.length ? entries.map(scanRow).join("") : `<p class="note">今天没有技术买点。</p>`}
    </section>
    </div>
    <p class="note">${esc(meta.macro.note)} 单笔风险系数 ${r.risk_mult ?? "—"}。权益敞口上限 ${meta.exposure_cap != null ? Math.round(meta.exposure_cap * 100) + "%" : "—"}。回撤 ${pct(p.drawdown, 2)}，距 -10% 还有 ${usd((p.drawdown_levels || []).at(-1)?.distance)}。</p>
    <div class="links">
      <button type="button" data-go="market">全球市场</button>
      <button type="button" data-go="sentiment">市场情绪</button>
      <button type="button" data-go="events">关键事件</button>
      <button type="button" data-go="profile">画像</button>
      <button type="button" data-go="framework">框架</button>
    </div>
    <p class="disclaimer note">${esc(meta.disclaimer)}</p>`;
}

function firstSentence(text) {
  if (!text) return "";
  const cut = String(text).split("。")[0];
  return cut.length > 42 ? cut.slice(0, 42) : cut;
}
function signedUsd(x) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  const n = Number(x);
  return (n > 0 ? "+" : "") + usd(n);
}
function snapLabel(asOf) {
  const m = String(asOf || "").match(/(\d{2}-\d{2})T(\d{2}:\d{2})/);
  return m ? `截图 ${m[1]} ${m[2]}` : "账户截图";
}

const INDEX_SHORT = {
  "^GSPC": "SPX",
  "^DJI": "DJI",
  "^IXIC": "COMP",
  "^NDX": "NDX",
  "^RUT": "RUT",
  "^N225": "日经",
  "^HSI": "恒生",
  "^FTSE": "富时",
  "^GDAXI": "DAX",
  "^FCHI": "CAC",
  "000001.SS": "上证",
  "^TNX": "10Y",
  "^TYX": "30Y",
  "^FVX": "5Y",
  "DX-Y.NYB": "DXY",
  "CL=F": "WTI",
  "BZ=F": "布伦特",
  "GC=F": "黄金",
  "SI=F": "白银",
  "HG=F": "铜",
  "BTC-USD": "BTC",
};
function indexShort(symbol) {
  if (INDEX_SHORT[symbol]) return INDEX_SHORT[symbol];
  return String(symbol || "").replace(/^\^/, "").replace(/=F$/, "");
}

function market() {
  const groups = DATA.market.groups || [];
  $("#main").innerHTML = `<div class="market-grid">` + groups.map((g) => `
    <section class="list-panel">
    <h2 class="sec">${esc(g.title)}<span class="section-count">${g.items.length}</span></h2>
    ${g.items.map((it) => {
      const price = it.kind === "yield" ? num(it.price) + "%" : px(it.price);
      const bits = [];
      if (it.above_50 != null) bits.push(it.above_50 ? "50日上" : "50日下");
      if (it.above_200 != null) bits.push(it.above_200 ? "200日上" : "200日下");
      return `<div class="row mkt">
        <span class="who">
          <span class="nm"><span class="name">${esc(it.name)}</span></span>
          <span class="tk">${esc(indexShort(it.symbol))} · 5日 ${pct(it.change_5d)}</span>
        </span>
        <span class="qt">
          <span class="last ${cls(it.change_1d)}">${price}</span>
          <span class="pill ${pillCls(it.change_1d)}">${pct(it.change_1d)}</span>
          ${bits.length ? `<span class="sub">${esc(bits.join(" · "))}</span>` : ""}
        </span>
      </div>`;
    }).join("") || emptyState("暂无行情", "这一组尚未生成行情数据。")}
    </section>
  `).join("") + `</div>${groups.length ? "" : emptyState("暂无市场数据", "行情更新后会显示在这里。")}<p class="note">日线收盘。收益率是水平。跨时区品种可能和美股收盘日差一天。</p>`;
}

function fgScore(x) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  const n = Number(x);
  return Math.abs(n - Math.round(n)) < 0.05 ? String(Math.round(n)) : n.toFixed(1);
}
function plainPct(x, d = 1) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  return (Number(x) * 100).toFixed(d) + "%";
}
function points(x) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  const v = Number(x) * 100;
  return (v > 0 ? "+" : "") + v.toFixed(1);
}
function staleTag(obj) {
  return obj && obj.stale ? `<span class="tag watch">过期</span>` : "";
}
function fgTone(rating) {
  if (rating === "extreme greed" || rating === "greed") return "greed";
  if (rating === "extreme fear" || rating === "fear") return "fear";
  return "neutral";
}
function gauge(score) {
  const s = Math.max(0, Math.min(100, Number(score) || 0));
  const length = Math.PI * 54;
  const dash = (s / 100) * length;
  const arc = "M16 72 A54 54 0 0 0 124 72";
  return `<svg viewBox="0 0 140 84" aria-hidden="true">
    <path class="gauge-track" d="${arc}" fill="none" stroke-width="8" stroke-linecap="round"/>
    <path d="${arc}" fill="none" stroke="currentColor" stroke-width="8" stroke-linecap="round" stroke-dasharray="${dash.toFixed(2)} ${length.toFixed(2)}"/>
  </svg>`;
}
function sentimentStrip() {
  const s = DATA.sentiment;
  if (!s) return "";
  const rows = [];
  const fg = s.fear_greed;
  if (fg && fg.score != null) {
    rows.push({
      id: "fg",
      title: `CNN 恐惧贪婪指数 ${fgScore(fg.score)}`,
      read: `${fgBand(fg.score)} · 0 极度恐惧，100 极度贪婪`,
    });
  }
  const pc = s.put_call;
  if (pc && pc.equity != null) {
    const avg = pc.avg_5d && pc.avg_5d.equity != null ? `，5日 ${num(pc.avg_5d.equity, 2)}` : "";
    rows.push({
      id: "pc",
      title: `CBOE 股票看跌/看涨期权比 ${num(pc.equity, 2)}`,
      read: `${pcRead(pc.equity)}${avg}`,
    });
  }
  const term = s.vix_term;
  if (term && term.ratio != null) {
    rows.push({
      id: "vixterm",
      title: `VIX/VIX3M 期限结构 ${num(term.ratio, 2)}`,
      read: termRead(term.ratio, term.backwardation),
    });
  }
  const aa = s.aaii;
  if (aa && aa.spread != null) {
    rows.push({
      id: "aaii",
      title: `AAII 散户看多减看空 ${points(aa.spread)}`,
      read: `${aaiiRead(aa.spread)} · 单位是个百分点`,
    });
  }
  if (!rows.length) return "";
  return `<div class="sent-card">${rows.map((row) => `<div class="metric">
    <button type="button" class="sent-go" data-go="sentiment"><b>${esc(row.title)}</b><small>${esc(row.read)}</small></button>
    ${tip(row.id)}
  </div>`).join("")}</div>`;
}
function spanLabel(history) {
  if (!history || history.length < 2 || !history[0].date || !history[history.length - 1].date) return "近期";
  const days = Math.round((Date.parse(history[history.length - 1].date) - Date.parse(history[0].date)) / 86400000);
  if (days >= 300) return "近一年";
  if (days >= 70) return "近一季";
  if (days >= 20) return "近一月";
  return `近 ${history.length} 个交易日`;
}
function sparkRef(values, ref) {
  const series = (values || []).map(Number).filter((v) => !Number.isNaN(v));
  if (series.length < 2) return "";
  const width = 280;
  const height = 56;
  const pad = 6;
  const lows = ref == null ? series : series.concat(ref);
  const min = Math.min(...lows);
  const max = Math.max(...lows);
  const span = max - min || 1;
  const yOf = (v) => pad + (1 - (v - min) / span) * (height - pad * 2);
  const pts = series.map((v, i) => {
    const x = (i / (series.length - 1)) * width;
    return `${x.toFixed(1)},${yOf(v).toFixed(1)}`;
  }).join(" ");
  const guide = ref == null ? "" : `<line x1="0" x2="${width}" y1="${yOf(ref).toFixed(1)}" y2="${yOf(ref).toFixed(1)}" stroke="currentColor" stroke-dasharray="4 3" stroke-width="1" opacity="0.55"/>`;
  return `<svg class="ind-spark" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">${guide}<polyline fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="${pts}"/></svg>`;
}
function sentimentVerdict(score) {
  const n = Number(score);
  if (Number.isNaN(n)) return "情绪读数不完整，不构成买卖理由。";
  if (n <= 25) return "整体极度恐惧 → 逆向偏多，但不构成买入理由。";
  if (n <= 45) return "整体偏恐惧 → 逆向偏多，但不构成买入理由。";
  if (n < 55) return "整体中性 → 情绪不偏向任何一边，也不构成买卖理由。";
  if (n < 75) return "整体偏贪婪 → 逆向宜谨慎，但不构成卖出理由。";
  return "整体极度贪婪 → 逆向偏空，但不构成卖出理由。";
}
function indCard(spec) {
  return `<article class="ind-card">
    <div class="ind-name">${esc(spec.name)} ${spec.stale || ""} ${tip(spec.term)}</div>
    <div class="ind-value">${spec.value}</div>
    <div class="ind-status tone-${spec.tone}"><b>${esc(spec.status)}</b><span>${esc(spec.meaning)}</span></div>
    <p class="ind-range">${esc(spec.range)}</p>
    <div class="ind-chart">${spec.spark || ""}<div class="ind-chart-lab">${esc(spec.sparkLab)}</div></div>
    ${spec.extra || ""}
    <p class="ind-read">${esc(spec.read)}</p>
    <div class="ind-foot">${esc(spec.foot)}</div>
  </article>`;
}
function missingSlim(name, term, reason) {
  return `<article class="ind-miss"><div class="ind-name">${esc(name)} ${tip(term)}</div><p>${esc(reason)}</p></article>`;
}

function fgPart(item) {
  const raw = item.raw_label || (item.raw == null ? "" : String(item.raw));
  const map = {
    market_momentum_sp500: ["动量", "fg_mom", `标普相对 125 日均线 · ${raw}`],
    stock_price_strength: ["新高减新低", "fg_hl", `新高减新低家数 ${item.raw == null ? raw : item.raw}`],
    stock_price_breadth: ["涨跌量麦克莱伦", "fg_mcc", `涨跌量麦克莱伦振荡器 ${item.raw == null ? raw : item.raw}`],
    put_call_options: ["5日看跌看涨比", "fg_pc", `5 日看跌/看涨比 ${raw}`],
    market_volatility_vix_50: ["VIX 相对 50 日", "fg_vix", `VIX 相对 50 日均值 · ${raw}`],
    junk_bond_demand: ["垃圾债需求", "fg_junk", `高收益债需求原始值 ${raw}`],
    safe_haven_demand: ["避险需求", "fg_safe", `股票相对债券的强弱 · ${raw}`],
  };
  const hit = map[item.id];
  if (!hit) return { name: item.name || "分项", term: "fg_scale", raw };
  return { name: hit[0], term: hit[1], raw: hit[2] };
}

function sentiment() {
  const s = DATA.sentiment;
  const sectors = [...(s.sectors || [])].sort((a, b) => (b.change_1d || -9) - (a.change_1d || -9));
  const fg = s.fear_greed;
  const pc = s.put_call;
  const term = s.vix_term;
  const hy = s.hy_oas;
  const aa = s.aaii;
  const fgOk = fg && fg.score != null;
  const hero = fgOk ? `
    <section class="sent-hero ${fgTone(fg.rating)}">
      <div class="fg-gauge">${gauge(fg.score)}<div class="score">${fgScore(fg.score)}</div><div class="score-cap">0 极度恐惧<br>100 极度贪婪</div></div>
      <div class="sent-hero-copy">
        <div class="ind-name">CNN 恐惧贪婪指数 ${staleTag(fg)} ${tip("fg")}</div>
        <p class="sent-verdict">${esc(sentimentVerdict(fg.score))}</p>
        <p class="ind-range">${esc(fg.rating_cn || "—")} · ${esc(fgBand(fg.score))}。50 附近是中性，不计入市场环境分。</p>
        <div class="ind-sub">
          <span>前收 ${fgScore(fg.previous_close)}</span>
          <span>一周 ${fgScore(fg.previous_1_week)}</span>
          <span>一月 ${fgScore(fg.previous_1_month)}</span>
          <span>一年 ${fgScore(fg.previous_1_year)}</span>
        </div>
        <div class="ind-foot">${esc(fg.source || "CNN")} · ${esc(fg.date || "—")}</div>
      </div>
    </section>` : missingSlim("CNN 恐惧贪婪指数", "fg", "这次没有取到恐惧贪婪指数。");
  const cards = [];
  if (pc && pc.equity != null) {
    const hot = Number(pc.equity) > 1;
    const hist = pc.history || [];
    cards.push(indCard({
      name: "CBOE 股票看跌/看涨期权比",
      term: "pc",
      stale: staleTag(pc),
      value: num(pc.equity, 2),
      tone: hot ? "fear" : "neutral",
      status: hot ? "恐慌" : "正常",
      meaning: pcRead(pc.equity),
      range: "常态小于 1。大于 1 表示看跌更多。虚线是 0.7。",
      spark: sparkRef(hist.map((p) => p.equity), 0.7),
      sparkLab: `${spanLabel(hist)} · 虚线 = 0.7`,
      extra: `<div class="ind-sub">
        <span>合计 ${num(pc.total, 2)}</span>
        <span>指数 ${num(pc.index, 2)}</span>
        <span>SPX ${num(pc.spx, 2)}</span>
        <span>5日 ${num(pc.avg_5d && pc.avg_5d.equity, 2)}</span>
        <span>10日 ${num(pc.avg_10d && pc.avg_10d.equity, 2)}</span>
      </div>`,
      read: `股票期权看跌除以看涨是 ${num(pc.equity, 2)}。${pcRead(pc.equity)}。`,
      foot: `${pc.source || "CBOE"} · ${pc.date || "—"}`,
    }));
  } else cards.push(missingSlim("CBOE 股票看跌/看涨期权比", "pc", "这次没有取到看跌/看涨比。"));
  if (term && term.ratio != null) {
    const hot = !!(term.backwardation || Number(term.ratio) > 1);
    const hist = term.history || [];
    cards.push(indCard({
      name: "VIX/VIX3M 期限结构",
      term: "vixterm",
      stale: staleTag(term),
      value: num(term.ratio, 2),
      tone: hot ? "fear" : "neutral",
      status: hot ? "倒挂" : "正常",
      meaning: termRead(term.ratio, term.backwardation),
      range: "小于 1 是正常的，远月波动更高。大于 1 叫期限倒挂。虚线是 1.0。",
      spark: sparkRef(hist.map((p) => p.ratio), 1),
      sparkLab: `${spanLabel(hist)} · 虚线 = 1.0`,
      extra: `<div class="ind-sub"><span>近月 VIX ${num(term.vix)}</span><span>三个月 ${num(term.vix3m)}</span></div>`,
      read: hot ? "近月波动高于三个月，市场更慌。环境分会按防守处理。" : "近月波动低于三个月，期限正常，没有倒挂。",
      foot: `${term.source || "CBOE"} · ${term.date || "—"}`,
    }));
  } else cards.push(missingSlim("VIX/VIX3M 期限结构", "vixterm", "这次没有取到期限结构。"));
  if (aa && aa.spread != null) {
    const pts = Number(aa.spread) * 100;
    const tone = pts >= 10 ? "greed" : pts <= -10 ? "fear" : "neutral";
    const status = pts >= 10 ? "偏多" : pts <= -10 ? "偏空" : "中性";
    const bull = Math.max(0, Number(aa.bull) || 0);
    const neutral = Math.max(0, Number(aa.neutral) || 0);
    const bear = Math.max(0, Number(aa.bear) || 0);
    const hist = aa.history || [];
    cards.push(indCard({
      name: "AAII 散户看多减看空",
      term: "aaii",
      stale: staleTag(aa),
      value: points(aa.spread),
      tone,
      status,
      meaning: aaiiRead(aa.spread),
      range: "单位是个百分点。±10 以内算多空差不大。虚线是 0。",
      spark: sparkRef(hist.map((p) => p.spread), 0),
      sparkLab: `${spanLabel(hist)} · 虚线 = 0`,
      extra: `<div class="aaii-bar" aria-hidden="true"><i class="bull" style="width:${(bull * 100).toFixed(1)}%"></i><i class="neut" style="width:${(neutral * 100).toFixed(1)}%"></i><i class="bear" style="width:${(bear * 100).toFixed(1)}%"></i></div>
        <div class="ind-sub"><span>看多 ${plainPct(aa.bull)}</span><span>中性 ${plainPct(aa.neutral)}</span><span>看空 ${plainPct(aa.bear)}</span></div>`,
      read: aaiiRead(aa.spread) + "。这是周度调查，不是当日买卖信号。",
      foot: `${aa.source || "AAII"} · ${aa.date || "—"}`,
    }));
  } else cards.push(missingSlim("AAII 散户看多减看空", "aaii", "这次没有取到散户调查。"));
  let hyCard = "";
  if (hy && hy.value != null) {
    const n = Number(hy.value);
    const tone = n >= 5 ? "fear" : n <= 3 ? "greed" : "neutral";
    const status = n >= 5 ? "偏紧" : n <= 3 ? "偏松" : "中性";
    const meaning = n >= 5 ? "信用更紧，偏恐惧" : n <= 3 ? "肯承担信用风险，偏贪婪" : "信用没有明显紧张";
    const hist = hy.history || [];
    const drift = hy.change_1m == null ? "" : `较 ${hy.change_1m_date || "一月前"} ${hy.change_1m > 0 ? "+" : ""}${num(hy.change_1m, 2)} 个百分点。`;
    hyCard = indCard({
      name: "高收益债利差",
      term: "hy",
      stale: staleTag(hy),
      value: num(hy.value, 2),
      tone,
      status,
      meaning,
      range: "单位是百分点。大约 3 到 5 算常见。虚线是 4.0。",
      spark: sparkRef(hist.map((p) => p.value), 4),
      sparkLab: `${spanLabel(hist)} · 虚线 = 4.0`,
      read: `利差 ${num(hy.value, 2)} 个百分点。${meaning}。${drift}`,
      foot: `${hy.source || "FRED"} · ${hy.series || "BAMLH0A0HYM2"} · ${hy.date || "—"}`,
    });
  } else {
    const reason = (hy && hy.reason) || "这次没有取到高收益债利差，也没有可沿用的上次读数。";
    hyCard = missingSlim("高收益债利差", "hy", reason);
  }
  const pieces = cards.filter((html) => html.includes("ind-card"));
  const misses = cards.filter((html) => html.includes("ind-miss"));
  if (hyCard.includes("ind-card")) pieces.push(hyCard);
  else misses.push(hyCard);
  if (pieces.length % 2 === 1) {
    pieces[pieces.length - 1] = pieces[pieces.length - 1].replace("ind-card", "ind-card span2");
  }
  const gridHtml = pieces.join("") + misses.join("");
  const lookback = s.distribution_lookback || 25;
  const penalty = s.distribution_penalty || 5;
  const dist = s.distribution_count;
  const distHot = dist != null && Number(dist) >= penalty;
  const b50 = Math.round((Number(s.breadth_above_50) || 0) * 100);
  const b200 = Math.round((Number(s.breadth_above_200) || 0) * 100);
  const parts = fgOk ? `
    <div class="sec">分项得分 0–100（0 = 极度恐惧，100 = 极度贪婪）${tip("fg_scale")}</div>
    ${(fg.components || []).map((item) => {
      const part = fgPart(item);
      return `<div class="srow">
        <span>
          <span class="nm"><span class="name">${esc(part.name)}</span>${tip(part.term)} <span class="tk" style="display:inline">${esc(item.rating_cn || "")} · ${esc(fgBand(item.score))}</span></span>
          <span class="why">${esc(part.raw)}</span>
          <span class="meter"><i style="width:${Math.max(0, Math.min(100, Number(item.score) || 0))}%"></i></span>
        </span>
        <span class="qt"><span class="last">${fgScore(item.score)}</span><span class="sub">分项得分</span></span>
      </div>`;
    }).join("")}` : "";
  $("#main").innerHTML = `
    ${hero}
    <div class="ind-grid">${gridHtml}</div>
    <section class="breadth">
      <div class="breadth-item">
        <div class="breadth-lab"><span>站上 50 日线占比</span><b>${b50}%</b></div>
        <div class="track-bar"><i style="width:${b50}%"></i></div>
      </div>
      <div class="breadth-item">
        <div class="breadth-lab"><span>站上 200 日线占比</span><b>${b200}%</b></div>
        <div class="track-bar"><i style="width:${b200}%"></i></div>
      </div>
      <p class="breadth-note">样本：观察列表 ${s.breadth_n ?? "—"} 只。不是标普 500 全成分。</p>
      <p class="breadth-note">派发日（放量下跌天数）近 ${lookback} 日 ${dist ?? "—"} 天${tip("distribution")}${distHot ? "。已经达到扣分门槛，这一分已经从环境分里扣掉。" : "。满 " + penalty + " 天才从环境分里扣 1 分。"}</p>
      ${s.vix_lock ? `<p class="breadth-note">近月波动高于三个月，环境分已按防守锁定。</p>` : ""}
    </section>
    ${parts}
    <div class="sec">行业 ETF</div>
    <div class="heat">
      ${sectors.map((it) => `<button type="button" data-go="market">
        <span class="sym">${esc(it.name)}</span>
        <strong class="${cls(it.change_1d)}">${pct(it.change_1d)}</strong>
        <span class="sub">1月相对 ${pct(it.rs_1m)}</span>
      </button>`).join("")}
    </div>
    <div class="sec">环境分</div>
    <ul class="checks" style="padding:0 var(--pad)">
      ${Object.entries(PARTS).map(([k, label]) => {
        const v = DATA.regime.parts?.[k];
        return `<li><span>${esc(label)}</span> ${mark(v)} <small></small></li>`;
      }).join("")}
    </ul>
    <p class="note">${esc(s.confirm || "情绪只作对照，不计入环境分，门槛未改。CNN 恐惧与贪婪和 CBOE 日统计来自非官方接口。")}</p>`;
}

function portfolio() {
  const p = DATA.portfolio;
  $("#main").innerHTML = `
    <section class="account account-overview" aria-label="持仓概览">
      <div class="account-label">估算净值</div>
      <div class="nlv">${usd(p.nlv_estimated)}</div>
      <dl class="account-stats">
        <div><dt>杠杆</dt><dd>${Math.round((p.gross || 0) * 100)}%</dd></div>
        <div><dt>多头市值</dt><dd>${usd(p.long_mv)}</dd></div>
        <div><dt>回撤</dt><dd>${pct(p.drawdown, 2)}</dd></div>
      </dl>
    </section>
    ${stopBanner()}
    ${p.breaches?.length ? `<div class="banner">${p.breaches.map((b) => esc(b.detail)).join("<br>")}</div>` : `<p class="note">单票、主题和总敞口没有越过上限。</p>`}
    ${sortBar("pos")}
    <div id="plist" class="quote-list"></div>
    <div class="support-grid">
    <section class="list-panel">
    <h2 class="sec">主题</h2>
    ${(p.themes || []).map((t) => `<div class="row nospark">
      <span><span class="nm"><span class="name">${esc(t.name)}</span></span><span class="tk">${t.breach ? "超过 25% 上限" : "上限以内"}</span></span>
      <span class="qt"><span class="last">${usd(t.market_value)}</span><span class="sub">${pct(t.weight, 1)}</span></span>
    </div>`).join("") || emptyState("暂无主题持仓", "持仓主题会在数据更新后显示。")}
    </section>
    <section class="list-panel">
    <h2 class="sec">回撤熔断</h2>
    ${(p.drawdown_levels || []).map((lv) => `<div class="row nospark">
      <span><span class="nm"><span class="name">${esc(lv.label)} ${lv.breached ? "已触发" : "未触发"}</span></span><span class="why">${esc(lv.action)}</span></span>
      <span class="qt"><span class="last">${usd(lv.nlv)}</span><span class="sub">还差 ${usd(lv.distance)}</span></span>
    </div>`).join("") || emptyState("暂无回撤档位", "当前数据未提供回撤熔断档位。")}
    </section>
    </div>
    ${p.snapshot ? `<p class="note">截图未实现盈亏 ${usd(p.snapshot.unrealized_pnl)}，那是账户快照，不是按尚未填写的成本重算的。</p>` : ""}
    <p class="note">${(p.positions || []).some((x) => x.mark_stale) ? "带参考价的期权用的是截图，不是这次的报价。" : "期权价格来自这次的期权链。"}还清融资大约需要卖出 ${usd(p.need_proceeds)}。</p>
    ${pendingNote(p)}`;
  paintPosList();
}

function paintPosList() {
  const host = $("#plist");
  if (!host) return;
  let rows = (DATA.portfolio.positions || []).slice();
  rows = sortRows(rows, state.psort, (pos) => scanOf(pos.underlying || pos.ticker)?.change_1d);
  host.innerHTML = rows.map(holdRow).join("") || emptyState("暂无持仓", "持仓更新后会显示在这里。");
}

function pendingNote(p) {
  const codes = [];
  for (const pos of p.positions || []) {
    for (const code of pos.rules_null || []) {
      if (!codes.includes(code) && TERMS[code]) codes.push(code);
    }
  }
  if (!codes.length) return "";
  const bits = codes.map((code) => `<span class="pending-bit">${esc(TERMS[code].short)}${tip(code)}：${esc(TERMS[code].need || TERMS[code].tip)}</span>`).join("");
  return `<p class="note">这些规则这次是未定。未定表示缺了下面的数，规则算不了，不是没有触发。${bits}</p>`;
}

function scanner() {
  const themes = [];
  const seen = new Set();
  for (const r of DATA.scanner.rows) {
    if (!seen.has(r.theme_name)) { seen.add(r.theme_name); themes.push(r.theme_name); }
  }
  const chips = [["all", "全部"], ["held", "持仓"], ["signal", "信号"], ...themes.map((t) => [t, shortTheme(t)])];
  $("#main").innerHTML = `
    <div class="scanner-toolbar">
    <div class="segs" aria-label="自选分组">
      ${chips.map(([id, label]) => `<button type="button" data-seg="${esc(id)}" ${state.seg === id ? 'aria-current="page"' : ""}>${esc(label)}</button>`).join("")}
    </div>
    <div class="addbar">
      <button type="button" class="addbtn" id="add-open" aria-label="加入自选">+</button>
      <input class="q" id="q" type="search" placeholder="代码或名称" value="${esc(state.q)}" aria-label="搜索">
      <button type="button" class="editbtn" id="edit-toggle">${state.editing ? "完成" : "编辑"}</button>
    </div>
    <div class="list-toolbar"><span id="scan-count" role="status"></span>${sortBar("scan")}</div>
    </div>
    <div id="list" class="quote-list"></div>
    <p class="note">点一行看图和条件。加号加入美股。手机左滑删除，电脑点 ⋯ 或「编辑」。持仓不能从自选删。相对强度排名${tip("rs")}只在这份观察列表里，不是全市场。均线交叉不当作单独买卖点。</p>`;
  document.body.classList.toggle("editing", !!state.editing);
  paintScanList();
  $("#q").addEventListener("input", (e) => { state.q = e.target.value; paintScanList(); });
}

function paintScanList() {
  const host = $("#list");
  if (!host) return;
  const q = state.q.trim();
  const qu = q.toUpperCase();
  let rows = visibleScanRows().filter((r) => {
    if (state.seg === "held" && !r.held) return false;
    if (state.seg === "signal" && !(r.call && r.call.code)) return false;
    if (state.seg !== "all" && state.seg !== "held" && state.seg !== "signal" && r.theme_name !== state.seg) return false;
    if (q && !r.ticker.includes(qu) && !cname(r.ticker).includes(q)) return false;
    return true;
  });
  rows = sortRows(rows, state.sort);
  const count = $("#scan-count");
  if (count) count.textContent = `${rows.length} 只标的`;
  host.innerHTML = rows.length ? rows.map(scanRow).join("") : emptyState("没有匹配的标的", "试试其他代码、名称或分组。", `<button type="button" class="secondary" data-reset-scan>清除筛选</button>`);
}

function events() {
  const macro = DATA.events.macro || [];
  const earn = DATA.events.earnings || [];
  $("#main").innerHTML = `
    ${earningsPanel()}
    <div class="support-grid event-grid">
    <section class="list-panel">
    <h2 class="sec">宏观<span class="section-count">${macro.length}</span></h2>
    ${macro.map((e) => `<div class="row nospark">
      <span>
        <span class="nm"><span class="name">${esc(e.title)}</span></span>
        <span class="tk">${esc(e.date || "日期未定")}${e.time_cn ? " · 北京 " + esc(e.time_cn) : ""}</span>
        ${e.note ? `<span class="why">${esc(e.note)}</span>` : ""}
      </span>
      <span class="qt"><span class="sub">${e.confirmed ? "已确认" : "待确认"}</span></span>
    </div>`).join("") || emptyState("暂无宏观事件", "当前日历中没有登记的宏观事件。")}
    </section>
    <section class="list-panel">
    <h2 class="sec">财报<span class="section-count">${earn.length}</span></h2>
    ${earn.map((e) => `<button type="button" class="row nospark" data-open="scan" data-id="${esc(e.ticker)}">
      <span>
        <span class="nm"><span class="name">${esc(cname(e.ticker))}</span>${e.held ? `<span class="tag held">持仓</span>` : ""}</span>
        <span class="tk">${esc(e.ticker)} · ${esc(e.date || "日期未定")}</span>
        <span class="why">${esc(e.note || e.source || "")}</span>
      </span>
      <span class="qt"><span class="last">${e.days == null ? "—" : `${e.days} 个交易日`}</span><span class="sub">${e.confirmed ? "已确认" : "待确认"}</span></span>
    </button>`).join("") || emptyState("暂无财报日程", "已登记的财报日期会显示在这里。")}
    </section>
    </div>
    <p class="note">交易日个数用工作日近似，没有扣交易所假日。上面的潜在亏损用平值跨式和近 8 次跳空；跨式取不到就留空。规则清单在没有报价时仍按 15%。</p>`;
}

function decisions() {
  const d = DATA.decisions;
  const saved = JSON.parse(localStorage.getItem("invest-buddy-checklist") || "{}");
  $("#main").innerHTML = `
    <div class="banner">${esc(d.formula)}</div>
    ${planPanel()}
    <div class="support-grid decision-grid">
    <section class="list-panel">
    <h2 class="sec">复核<span class="section-count">${(d.reviews || []).length}</span></h2>
    ${(d.reviews || []).map((call, i) => `<button type="button" class="row review-row" data-open="review" data-id="${i}">
      <span>
        <span class="nm"><span class="name">${esc(cname(call.ticker))}</span>${actionTag(call)}</span>
        <span class="tk">${esc(call.ticker)} · ${signalHtml(call)}</span>
        <span class="why">${whyHtml(call)}</span>
      </span>
    </button>`).join("") || emptyState("暂无待复核信号", "当前数据中没有需要复核的信号。")}
    <h2 class="sec">动作</h2>
    <ul class="actions">${(d.actions || []).map((a) => `<li><b>${esc(plain(a.title))}</b><div class="action-detail">${esc(plain(a.detail))}</div></li>`).join("")}</ul>
    ${(d.actions || []).length ? "" : emptyState("暂无规则动作", "当前没有新的规则动作。")}
    </section>
    <div class="decision-tools">
    <section class="list-panel calculator-panel">
    <h2 class="sec">仓位试算</h2>
    <div class="form">
      <label>入场价<input id="en" inputmode="decimal" value="100"></label>
      <label>止损价<input id="st" inputmode="decimal" value="95"></label>
      <label>期限<select id="hz"><option value="swing">短线 0.5%</option><option value="position">中线 1%</option></select></label>
      <label>买入确认分${tip("C1")}<select id="cs"><option>0</option><option>1</option><option selected>2</option><option>3</option></select></label>
    </div>
    <p id="calc" class="calc-result" role="status"></p>
    </section>
    <section class="list-panel">
    <h2 class="sec">买入前</h2>
    <ul class="check" id="buy">
      ${(d.checklist || []).map((item) => `<li>
        <label><input type="checkbox" data-k="b${item.n}" ${saved["b" + item.n] ? "checked" : ""}>
        <span>${item.n}. ${esc(item.text)}${item.auto ? `<small>${esc(item.auto)}</small>` : ""}</span></label>
      </li>`).join("")}
    </ul>
    </section>
    <section class="list-panel">
    <h2 class="sec">卖出前</h2>
    <ul class="check" id="sell">
      ${(d.sell_checklist || []).map((item) => `<li>
        <label><input type="checkbox" data-k="s${item.n}" ${saved["s" + item.n] ? "checked" : ""}>
        <span>${esc(item.n)}. ${esc(item.text)}</span></label>
      </li>`).join("")}
    </ul>
    </section>
    </div>
    </div>
    <p class="note">勾选保存在这台设备的浏览器里，不会上传。${esc(DATA.meta.disclaimer)}</p>`;
  const recalc = () => {
    const acct = DATA.meta.account;
    const nlv = DATA.portfolio.nlv_basis || acct.nlv_base;
    const entry = Number($("#en").value);
    const stop = Number($("#st").value);
    const horizon = $("#hz").value;
    const score = Number($("#cs").value);
    const risk = horizon === "swing" ? acct.risk_swing : acct.risk_position;
    const mult = DATA.regime.risk_mult || 0;
    const size = score >= 2 ? 1 : 0.5;
    const dist = entry - stop;
    const out = $("#calc");
    if (!(entry > 0) || !(dist > 0)) { out.textContent = "止损要低于入场价。"; return; }
    const shares = Math.floor(nlv * risk * mult * size / dist);
    const capped = Math.min(shares, Math.floor(acct.max_pos_pct * nlv / entry));
    const reasons = [];
    if (DATA.portfolio.using_margin) reasons.push("账户仍在融资，规则不放行新仓");
    if (DATA.portfolio.stops_missing) reasons.push("持仓止损没填，风险合计未知");
    if (mult === 0) reasons.push("环境不允许新的多头");
    out.textContent = `约 ${capped} 股，名义 ${usd(capped * entry)}，风险 ${usd(capped * dist)}。1R ${px(entry + dist)}，2R ${px(entry + 2 * dist)}。${reasons.join("。")}`;
  };
  ["en", "st", "hz", "cs"].forEach((id) => $("#" + id).addEventListener("input", recalc));
  recalc();
  $("#main").querySelectorAll(".check input").forEach((box) => box.addEventListener("change", () => {
    saved[box.dataset.k] = box.checked;
    localStorage.setItem("invest-buddy-checklist", JSON.stringify(saved));
  }));
}

async function profile() { await prose("content/01_profile.html", "画像与配置"); }
async function framework() {
  const shown = await prose("content/02_framework.html", "投研框架");
  if (!shown) return;
  const article = $("#main article");
  if (article) article.insertAdjacentHTML("beforeend", glossarySection());
}

function prepDoc(html) {
  return String(html).replace(/<blockquote>([\s\S]*?)<\/blockquote>/g, (block, inner) => {
    const broken = inner.replace(/([^>\s])\n\s*(?=<|\S)/g, "$1<br>");
    return `<blockquote>${broken}</blockquote>`;
  });
}

function upgradeProse(root) {
  if (!root) return;
  root.querySelectorAll("table").forEach((table) => {
    if (table.closest(".table-wrap") || table.closest(".tldr-list")) return;
    const heads = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    const rows = [...table.querySelectorAll("tbody tr")];
    const numbered = heads[0] === "#" || heads[0] === "序号";
    if (numbered && heads.length === 2 && rows.length) {
      const list = document.createElement("div");
      list.className = "tldr-list";
      rows.forEach((tr) => {
        const cells = [...tr.children];
        const card = document.createElement("article");
        card.className = "tldr";
        const n = document.createElement("span");
        n.className = "tldr-n";
        n.textContent = cells[0] ? cells[0].textContent.trim() : "";
        const body = document.createElement("div");
        body.innerHTML = cells[1] ? cells[1].innerHTML : "";
        card.append(n, body);
        list.append(card);
      });
      table.replaceWith(list);
      return;
    }
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    wrap.tabIndex = 0;
    wrap.setAttribute("role", "region");
    wrap.setAttribute("aria-label", "数据表格，可横向滚动");
    table.parentNode.insertBefore(wrap, table);
    wrap.appendChild(table);
    if (numbered) {
      table.querySelectorAll("tr").forEach((tr) => {
        const cell = tr.children[0];
        if (cell) cell.classList.add("num");
      });
    }
  });
}

function emptyState(title, detail, action = "", loading = false) {
  return `<div class="empty-state" role="${loading ? "status" : "region"}" aria-label="${esc(title)}">
    <strong>${esc(title)}</strong><p>${esc(detail)}</p>${action}</div>`;
}

function documentError() {
  return emptyState("内容暂时无法读取", "请检查网络连接，或稍后重试。", `<button type="button" class="secondary" data-retry-page>重新加载</button>`);
}

async function prose(url, kicker) {
  const gen = ++documentGen;
  $("#main").innerHTML = emptyState("正在读取" + kicker, "请稍候。", "", true);
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error("document unavailable");
    const html = await res.text();
    if (gen !== documentGen) return false;
    $("#main").innerHTML = `<div class="document-sheet"><p class="document-kicker">${esc(kicker)}</p><article class="prose">${prepDoc(html)}</article></div>`;
    upgradeProse($("#main article"));
    return true;
  } catch {
    if (gen === documentGen) $("#main").innerHTML = documentError();
    return false;
  }
}

async function reports() {
  const gen = ++documentGen;
  const files = DATA.reports.files || [];
  const pick = files.includes(state.report) ? state.report : files[files.length - 1];
  state.report = pick;
  const reportLabel = (file) => file.replace(/-(am|pm)\.md$/, (_, session) => session === "am" ? " · 早报" : " · 午后报");
  $("#main").innerHTML = `
    <div class="tools report-picker" aria-label="选择日报">${files.map((f) => `<button type="button" data-report="${esc(f)}" ${f === pick ? 'aria-current="page"' : ""}>${esc(reportLabel(f))}</button>`).join("")}</div>
    <div id="report-body">${pick ? emptyState("正在读取日报", reportLabel(pick), "", true) : emptyState("还没有日报", "生成后的早报与午后报会显示在这里。")}</div>`;
  $("#main").querySelectorAll("[data-report]").forEach((btn) => btn.addEventListener("click", () => {
    state.report = btn.dataset.report;
    reports();
  }));
  if (!pick) return;
  try {
    const res = await fetch("reports/" + pick);
    if (!res.ok) throw new Error("report unavailable");
    const body = md(await res.text());
    if (gen !== documentGen) return;
    $("#report-body").innerHTML = `<div class="document-sheet"><article class="prose">${body}</article></div>`;
    upgradeProse($("#main article"));
  } catch {
    if (gen === documentGen) $("#report-body").innerHTML = documentError();
  }
}

function mdInline(s) {
  return esc(plain(s)).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
}
function mdCells(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}
function mdTable(rows) {
  if (!rows.length) return "";
  const head = mdCells(rows[0]);
  let body = rows.slice(1);
  if (body.length && /^\|?\s*:?-{3,}/.test(body[0])) body = body.slice(1);
  const th = head.map((cell) => `<th>${mdInline(cell)}</th>`).join("");
  const trs = body.map((line) => `<tr>${mdCells(line).map((cell) => `<td>${mdInline(cell)}</td>`).join("")}</tr>`).join("");
  return `<table><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`;
}
function md(src) {
  const lines = String(src || "").replace(/\r\n/g, "\n").split("\n");
  let html = "";
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    if (/^---+$/.test(line.trim()) || /^\*\*\*+$/.test(line.trim())) {
      html += "<hr>";
      i += 1;
      continue;
    }
    if (line.startsWith("### ")) { html += `<h3>${mdInline(line.slice(4))}</h3>`; i += 1; continue; }
    if (line.startsWith("## ")) { html += `<h2>${mdInline(line.slice(3))}</h2>`; i += 1; continue; }
    if (line.startsWith("# ")) { html += `<h1>${mdInline(line.slice(2))}</h1>`; i += 1; continue; }
    if (line.startsWith("> ")) {
      const bits = [];
      while (i < lines.length && lines[i].startsWith("> ")) {
        bits.push(mdInline(lines[i].slice(2)));
        i += 1;
      }
      html += `<blockquote><p>${bits.join("<br>")}</p></blockquote>`;
      continue;
    }
    if (line.trim().startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(lines[i]);
        i += 1;
      }
      html += mdTable(rows);
      continue;
    }
    if (line.startsWith("- ")) {
      html += "<ul>";
      while (i < lines.length && lines[i].startsWith("- ")) {
        html += `<li>${mdInline(lines[i].slice(2))}</li>`;
        i += 1;
      }
      html += "</ul>";
      continue;
    }
    if (/^\d+\.\s/.test(line)) {
      html += "<ol>";
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
        html += `<li>${mdInline(lines[i].replace(/^\d+\.\s/, ""))}</li>`;
        i += 1;
      }
      html += "</ol>";
      continue;
    }
    html += `<p>${mdInline(line)}</p>`;
    i += 1;
  }
  return html;
}

function mark(value) {
  if (value == null) return `<span class="tag watch">未定</span>`;
  return value ? `<span class="tag buy">成立</span>` : `<span class="tag watch">未成立</span>`;
}

function signalHtml(call) {
  if (!call) return "";
  const ids = [];
  if (call.code && TERMS[call.code]) ids.push(call.code);
  String(call.c_tag || "").split("+").forEach((part) => {
    const id = part.split(":")[0];
    if (TERMS[id] && !ids.includes(id)) ids.push(id);
  });
  if (!ids.length) return esc(plain(call.name || call.c_tag || ""));
  return ids.map((id) => `${esc(TERMS[id].short)} <span class="code">${esc(id)}</span>${tip(id)}`).join(" · ");
}
function callBody(call) {
  if (!call) return "";
  const checks = (call.checks || []).map((item) => `<li><span>${esc(plain(item.label))}${termTip(item.label)}</span> ${mark(item.pass)}<small>${esc(plain(item.text))}</small></li>`).join("");
  const confirm = (call.confirmation || []);
  const passed = confirm.filter((item) => item.pass).length;
  const confirmHtml = confirm.map((item) => `<li><span>${esc(plain(item.label))}</span> ${mark(item.pass)}<small>${esc(plain(item.text))}</small></li>`).join("");
  const miss = missingConfirm(call);
  return `
    ${call.block ? `<p class="block">${esc(plain(call.block))}</p>` : ""}
    <p>${esc(plain(call.line || ""))}</p>
    <p>${signalHtml(call)}</p>
    ${miss ? `<p class="note">${esc(miss)}${tip("C1", miss)}</p>` : ""}
    ${checks ? `<h3>触发条件</h3><ul class="checks">${checks}</ul>` : ""}
    ${confirmHtml ? `<h3>买入确认分 ${passed}/${confirm.length || 3}${tip("C1")}</h3><ul class="checks">${confirmHtml}</ul>` : ""}
    ${call.plan ? `<p>${esc(plain(call.plan.text))}</p>` : ""}
    ${call.invalidate ? `<p class="note">失效：${esc(plain(call.invalidate))}</p>` : ""}
    <p class="note">名词解释在「框架」页的术语说明。未定表示缺数据，不是没有触发。</p>`;
}
function termTip(label) {
  const id = String(label || "").split(/[\s:：]/)[0];
  return TERMS[id] ? tip(id) : "";
}

function closeDetail() {
  stopResearchPoll();
  dockGen += 1;
  state.detail = null;
  state.bars = [];
  $("#dock").hidden = true;
  $("#dock").innerHTML = "";
  $("#backdrop").hidden = true;
  document.body.classList.remove("has-detail");
}

function detailModel() {
  const spec = state.detail;
  if (!spec) return null;
  if (spec.kind === "scan") {
    const row = scanOf(spec.id);
    if (!row) return null;
    return { title: cname(row.ticker), sub: row.ticker, call: row.call, row, chart: row.ticker, extra: rowNote(row) };
  }
  if (spec.kind === "pos") {
    const pos = posByKey(spec.id);
    if (!pos) return null;
    const und = pos.underlying || pos.ticker;
    const opt = pos.instrument === "option";
    const title = opt ? `${cname(und)} ${pos.strike}${pos.right}` : cname(pos.ticker);
    return {
      title,
      sub: opt ? `${und} · ${pos.expiry}` : pos.ticker,
      call: pos.call,
      row: scanOf(und),
      chart: und,
      extra: posNote(pos),
    };
  }
  if (spec.kind === "review") {
    const call = (DATA.decisions.reviews || [])[Number(spec.id)];
    if (!call) return null;
    return { title: cname(call.ticker), sub: call.ticker, call, row: scanOf(call.ticker), chart: call.ticker, extra: "" };
  }
  return null;
}

function rowNote(row) {
  const bits = [];
  if (row.earnings_date) bits.push(`财报 ${esc(row.earnings_date)}${row.earnings_confirmed ? " 已确认" : " 待确认"}`);
  else if (row.earnings_note) bits.push(esc(row.earnings_note));
  (row.flags || []).forEach((flag) => {
    const term = TERMS[flag];
    bits.push(term ? `${esc(term.short)}${tip(flag)}` : esc(flag));
  });
  if (row.signal_source) bits.push(`信号来自 ${esc(row.signal_source)}，杠杆 ${esc(row.leverage)}`);
  return bits.join(" · ");
}

function posNote(pos) {
  const bits = [];
  if (pos.mark_stale) bits.push("价格用的是截图参考价");
  else if (pos.mark_source) bits.push(pos.mark_source);
  if (pos.note) bits.push(pos.note);
  for (const n of pos.exit_notes || []) bits.push(plain(n));
  if (pos.instrument === "option") bits.push("期权不跑股票的离场规则，图是正股。");
  return bits.map(esc).join(" · ");
}

function ruleActionFor(ticker) {
  const pos = (DATA.portfolio?.positions || []).find((p) => p.instrument !== "option" && p.ticker === ticker);
  if (pos && pos.call && pos.call.action) return pos.call.action;
  const row = scanOf(ticker);
  return row && row.call ? row.call.action : null;
}

function aiDiverges(action, rating) {
  const bull = rating === "买入" || rating === "增持";
  const bear = rating === "卖出" || rating === "减持";
  if ((action === "sell" || action === "trim") && bull) return true;
  if (action === "buy" && bear) return true;
  return false;
}

function aiTag(rating) {
  const kind = { "买入": "buy", "增持": "buy", "持有": "hold", "减持": "trim", "卖出": "sell" }[rating] || "watch";
  return `<span class="tag ${kind}">${esc(rating)}</span>`;
}

function researchStale(date) {
  const bar = DATA.meta && DATA.meta.bar_date;
  const limit = Number(DATA.research && DATA.research.stale_days) || 3;
  if (!date || !bar) return false;
  const span = Math.round((Date.parse(bar + "T00:00:00Z") - Date.parse(date + "T00:00:00Z")) / 86400000);
  return span > limit;
}

function costFoot() {
  const research = DATA.research || {};
  const usd = Number(research.estimated_usd || 0);
  const note = research.price_note || "";
  return `<p class="note dock-note">定时研究写入研究文件。本月估算 $${usd.toFixed(2)}。${esc(note)}</p>`;
}

function researchOrigin(note) {
  const text = String(note || "");
  if (!text.includes("·")) return "";
  const head = text.split("·")[0].trim();
  return head.length <= 24 ? head : "";
}

function originTag(note) {
  const name = researchOrigin(note);
  return name ? `<span class="tag watch">${esc(name)}</span>` : "";
}

function researchControls(ticker) {
  const busy = researchBusy.has(ticker);
  return `<button type="button" class="research-go" data-research="${esc(ticker)}" ${busy ? "disabled" : ""}>${busy ? "研究中…（约 5–10 分钟）" : "立即研究"}</button>
    <p class="note dock-note" id="research-msg">${busy ? "研究中…（约 5–10 分钟）" : ""}</p>`;
}

function aiPanel(full, ticker) {
  const controls = researchControls(ticker);
  if (!full) {
    return `${controls}<p class="note dock-note">还没有这份 AI 研究。</p>${costFoot()}<p class="note dock-note">股数和动作以规则引擎为准。点「立即研究」只研究这一只。</p>`;
  }
  const stale = researchStale(full.date) ? `<span class="tag watch">过期</span>` : "";
  const points = (items) => (items && items.length)
    ? `<ul class="ai-list">${items.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`
    : `<p class="note dock-note">没有列出。</p>`;
  const fundamentals = full.fundamentals && Object.keys(full.fundamentals).length
    ? `<div class="kv">${Object.entries(full.fundamentals).map(([key, value]) => kv(key, value == null ? "—" : String(value))).join("")}</div>`
    : `<p class="note dock-note">没有列出。</p>`;
  const sources = (full.sources || []).map((item) => `<li><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a></li>`).join("");
  return `
    ${controls}
    <p class="tk">${originTag(full.model_note)} ${aiTag(full.rating)} <span>${esc(full.date || "")}</span> ${stale}</p>
    <p>${esc(full.conclusion || "")}</p>
    <h3>多方</h3>${points(full.bull_points)}
    <h3>空方</h3>${points(full.bear_points)}
    <h3>新闻与情绪</h3><p>${esc(full.news_sentiment || "")}</p>
    <h3>基本面</h3>${fundamentals}
    <h3>技术</h3><p>${esc(plain(full.technical_view || ""))}</p>
    <h3>风险</h3><p>${esc(plain(full.risk_review || ""))}</p>
    <h3>来源</h3>${sources ? `<ul class="ai-list">${sources}</ul>` : `<p class="note dock-note">没有链接。</p>`}
    ${full.model_note ? `<p class="note dock-note">${esc(full.model_note)}</p>` : ""}
    <p class="note dock-note"><a href="research/${encodeURIComponent(full.ticker)}.html">完整报告</a></p>
    <p class="note dock-note">股数和动作以规则引擎为准。</p>
    ${costFoot()}`;
}

async function paintDock() {
  const model = detailModel();
  if (!model) { closeDetail(); return; }
  const gen = ++dockGen;
  const dock = $("#dock");
  dock.hidden = false;
  $("#backdrop").hidden = false;
  document.body.classList.add("has-detail");
  const row = model.row;
  const ticker = model.chart;
  const brief = DATA.research && DATA.research.tickers ? DATA.research.tickers[ticker] : null;
  const rating = brief && brief.rating;
  const banner = rating && aiDiverges(ruleActionFor(ticker), rating) ? `<p class="banner">⚠️ 分歧</p>` : "";
  const quoteOn = state.dockTab !== "ai";
  dock.innerHTML = `
    <div class="grab"></div>
    <div class="dock-head">
      <div>
        <h2>${esc(model.title)} ${actionTag(model.call)}</h2>
        <div class="tk">${esc(model.sub)}</div>
      </div>
      <button type="button" id="dock-close">关闭</button>
    </div>
    ${banner}
    <div class="segs dock-segs">
      <button type="button" data-docktab="quote" ${quoteOn ? 'aria-current="page"' : ""}>行情</button>
      <button type="button" data-docktab="ai" ${quoteOn ? "" : 'aria-current="page"'}>AI 研究${rating ? " " + aiTag(rating) : ""}</button>
    </div>
    <div id="dock-quote" ${quoteOn ? "" : "hidden"}>
      ${state.detail && state.detail.kind === "pos" ? riskForm(posByKey(state.detail.id)) : ""}
      <canvas id="chart" width="880" height="240"></canvas>
      ${row ? `<div class="kv">
        ${kv("1日", pct(row.change_1d))}${kv("5日", pct(row.change_5d))}${kv("1月", pct(row.change_1m))}
        ${kv("相对强弱", num(row.rsi14, 1), "rsi")}${kv("波动幅度", row.atr_pct == null ? "—" : (row.atr_pct * 100).toFixed(1) + "%", "atr")}
        ${kv("量比", num(row.rvol), "rvol")}${kv("相对强度排名", row.rs_rank ?? "—", "rs")}
        ${kv("距21日", pct(row.dist_ema21))}${kv("距50日", pct(row.dist_sma50))}
        ${kv("距200日", pct(row.dist_sma200))}${kv("距52周高", pct(row.dist_52w_high))}
        ${row.entry != null ? kv("入场", px(row.entry)) : ""}${row.stop != null ? kv("止损", px(row.stop)) : ""}
        ${row.shares_model != null ? kv("公式股数", String(row.shares_model)) : ""}
      </div>` : ""}
      ${callBody(model.call)}
      ${model.extra ? `<p class="note dock-note">${model.extra}</p>` : ""}
    </div>
    <div id="dock-ai" ${quoteOn ? "hidden" : ""}><p class="note dock-note">正在读取。</p></div>`;
  $("#dock-close").addEventListener("click", closeDetail);
  let bars = [];
  let full = null;
  try {
    const [barRes, aiRes] = await Promise.all([
      fetch(`data/bars/${encodeURIComponent(ticker)}.json`),
      fetch(`data/research/${encodeURIComponent(ticker)}.json`),
    ]);
    if (barRes.ok) bars = (await barRes.json()).bars || [];
    if (aiRes.ok) full = await aiRes.json();
  } catch (err) {
    bars = bars || [];
  }
  if (gen !== dockGen) return;
  state.bars = bars;
  const ai = $("#dock-ai");
  if (ai) ai.innerHTML = aiPanel(full, ticker);
  ensureResearch(ticker, full);
  const liveRating = (full && full.rating) || rating;
  if (liveRating && aiDiverges(ruleActionFor(ticker), liveRating) && !dock.querySelector(".banner")) {
    const segs = dock.querySelector(".dock-segs");
    if (segs) segs.insertAdjacentHTML("beforebegin", `<p class="banner">⚠️ 分歧</p>`);
  }
  const canvas = $("#chart");
  requestAnimationFrame(() => { if (gen === dockGen && canvas) drawChart(canvas, bars); });
}

function openDetail(kind, id) {
  state.detail = { kind, id };
  state.dockTab = "quote";
  paintDock();
}

function kv(k, v, term) {
  return `<div><span>${esc(k)}${term ? tip(term) : ""}</span><b>${esc(v)}</b></div>`;
}

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function drawChart(canvas, bars) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 360;
  const h = canvas.clientHeight || 168;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.font = '12px "Noto Sans SC", sans-serif';
  const label = cssVar("--chart-label", "#9aa1ad");
  if (!bars.length) {
    ctx.fillStyle = label;
    ctx.fillText("没有图数据", 16, 28);
    return;
  }
  const up = cssVar("--up", "#ff8d8f");
  const down = cssVar("--down", "#3dce8a");
  const grid = cssVar("--grid", "rgba(242,244,247,0.10)");
  const ma21 = cssVar("--ma21", "#e6c15a");
  const ma50 = cssVar("--ma50", "#8eb6ff");
  const ma200 = cssVar("--ma200", "#b0b6c2");
  const pad = { l: 8, r: 54, t: 16, b: 22 };
  const max = Math.max(...bars.map((b) => b.h));
  const min = Math.min(...bars.map((b) => b.l));
  const span = max - min || 1;
  const iw = w - pad.l - pad.r;
  const ih = h - pad.t - pad.b;
  const x = (i) => pad.l + (i + 0.5) * (iw / bars.length);
  const y = (v) => pad.t + (max - v) / span * ih;
  const cw = Math.max(1, (iw / bars.length) * 0.62);
  ctx.strokeStyle = grid;
  ctx.lineWidth = 1;
  [0, 0.5, 1].forEach((t) => {
    const gy = Math.round(pad.t + ih * t) + 0.5;
    ctx.beginPath();
    ctx.moveTo(pad.l, gy);
    ctx.lineTo(w - pad.r, gy);
    ctx.stroke();
  });
  bars.forEach((b, i) => {
    const rising = b.c >= b.o;
    ctx.strokeStyle = rising ? up : down;
    ctx.fillStyle = ctx.strokeStyle;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x(i), y(b.h));
    ctx.lineTo(x(i), y(b.l));
    ctx.stroke();
    const top = y(Math.max(b.o, b.c));
    const bot = y(Math.min(b.o, b.c));
    ctx.fillRect(x(i) - cw / 2, top, cw, Math.max(1, bot - top));
  });
  const line = (key, color) => {
    ctx.beginPath();
    let started = false;
    bars.forEach((b, i) => {
      if (b[key] == null) return;
      const px0 = x(i);
      const py = y(b[key]);
      if (!started) { ctx.moveTo(px0, py); started = true; }
      else ctx.lineTo(px0, py);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.25;
    ctx.stroke();
  };
  line("e21", ma21);
  line("s50", ma50);
  line("s200", ma200);
  ctx.fillStyle = label;
  ctx.fillText(bars[0].d.slice(5), pad.l, h - 6);
  let lx = pad.l + 52;
  [["21", ma21], ["50", ma50], ["200", ma200]].forEach(([name, color]) => {
    ctx.fillStyle = color;
    ctx.fillText(name, lx, h - 6);
    lx += ctx.measureText(name).width + 8;
  });
  const last = bars[bars.length - 1].c;
  const lastY = y(last);
  ctx.textAlign = "right";
  ctx.fillStyle = label;
  if (Math.abs(lastY - (pad.t + 10)) > 14) ctx.fillText(px(max), w - 4, pad.t + 10);
  if (Math.abs(lastY - (pad.t + ih)) > 14) ctx.fillText(px(min), w - 4, pad.t + ih);
  ctx.fillStyle = last >= bars[bars.length - 1].o ? up : down;
  ctx.fillText(px(last), w - 4, lastY);
  ctx.textAlign = "left";
}

function loadPending() {
  try {
    const raw = JSON.parse(localStorage.getItem("invest-buddy-watch") || "{}");
    pending = {
      add: Array.isArray(raw.add) ? raw.add.filter((item) => item && normalizeTicker(item.ticker)) : [],
      remove: Array.isArray(raw.remove) ? raw.remove.map(normalizeTicker).filter(Boolean) : [],
    };
  } catch {
    pending = { add: [], remove: [] };
  }
}

function savePending() {
  localStorage.setItem("invest-buddy-watch", JSON.stringify(pending));
}

function reconcilePending() {
  const have = new Set((DATA.scanner?.rows || []).map((row) => row.ticker));
  pending.add = pending.add.filter((item) => item.ticker && !have.has(item.ticker));
  pending.remove = pending.remove.filter((ticker) => have.has(ticker));
  savePending();
}

function normalizeTicker(raw) {
  const ticker = String(raw || "").trim().toUpperCase().replace(/\./g, "-");
  if (!/^[A-Z]{1,5}(-[A-Z]{1,2})?$/.test(ticker)) return null;
  if (ticker === "LIST2153") return null;
  return ticker;
}

function isStockHeld(ticker) {
  return (DATA.portfolio?.positions || []).some((pos) => pos.instrument !== "option" && String(pos.ticker || "").replace(/\./g, "-") === ticker);
}

function themeGroups() {
  const fromFile = DATA.themes && DATA.themes.groups;
  if (fromFile && fromFile.length) return fromFile;
  const seen = new Map();
  for (const row of DATA.scanner?.rows || []) {
    if (!row.theme || seen.has(row.theme)) continue;
    seen.set(row.theme, { id: row.theme, name: row.theme_name, short: shortTheme(row.theme_name) });
  }
  return [...seen.values()];
}

function placeholderRow(item) {
  const group = themeGroups().find((entry) => entry.id === item.group);
  return {
    ticker: item.ticker,
    theme: item.group || "",
    theme_name: item.theme_name || (group && group.name) || "未分类",
    price: null,
    change_1d: null,
    dist_ema21: null,
    held: isStockHeld(item.ticker),
    pending: true,
    call: { action: "watch", action_label: "待刷新", line: `${item.ticker}：等待下次刷新` },
  };
}

function visibleScanRows() {
  const removed = new Set(pending.remove);
  const seen = new Set();
  const rows = [];
  for (const item of pending.add) {
    if (!item || removed.has(item.ticker) || seen.has(item.ticker)) continue;
    if ((DATA.scanner?.rows || []).some((row) => row.ticker === item.ticker)) continue;
    seen.add(item.ticker);
    rows.push(placeholderRow(item));
  }
  for (const row of DATA.scanner?.rows || []) {
    if (removed.has(row.ticker) || seen.has(row.ticker)) continue;
    seen.add(row.ticker);
    rows.push(row);
  }
  return rows;
}

function toast(text) {
  const el = $("#toast");
  if (!el) return;
  el.hidden = false;
  el.textContent = text;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 3400);
}

function closeSheet() {
  const sheet = $("#sheet");
  if (!sheet) return;
  sheet.hidden = true;
  sheet.innerHTML = "";
}

function closeModal() {
  const modal = $("#modal");
  if (!modal) return;
  modal.hidden = true;
  modal.innerHTML = "";
}

function closeMenus() {
  document.querySelectorAll(".swipe.menu-open").forEach((el) => el.classList.remove("menu-open"));
}

function openAdder() {
  closeDetail();
  closeMenus();
  closeModal();
  const groups = themeGroups();
  const sheet = $("#sheet");
  sheet.hidden = false;
  sheet.innerHTML = `
    <div class="scrim" data-sheet-close></div>
    <div class="panel">
      <div class="grab"></div>
      <h2>加入自选</h2>
      <input id="add-ticker" type="text" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="美股代码，如 SMCI" aria-label="美股代码">
      <div class="groupchips">
        <button type="button" data-group="" aria-current="page">不分组</button>
        ${groups.map((group) => `<button type="button" data-group="${esc(group.id)}">${esc(group.short || group.name)}</button>`).join("")}
      </div>
      <p class="note" id="add-msg"></p>
      <button type="button" class="primary" id="add-submit">加入自选</button>
    </div>`;
  const input = $("#add-ticker");
  if (input) input.focus();
}

function askRemove(ticker) {
  closeMenus();
  if (!ticker) return;
  if (isStockHeld(ticker) || (DATA.scanner?.rows || []).some((row) => row.ticker === ticker && row.held)) {
    toast("持仓来自账户，不能从自选删除");
    return;
  }
  const modal = $("#modal");
  modal.hidden = false;
  modal.innerHTML = `<div class="confirm-card" role="dialog" aria-modal="true">
    <p>从自选去掉 ${esc(ticker)}？</p>
    <div class="confirm-actions">
      <button type="button" data-confirm="cancel">取消</button>
      <button type="button" class="danger" data-confirm="ok" data-ticker="${esc(ticker)}">删除</button>
    </div>
  </div>`;
}

function watchEnqueue(task) {
  const run = watchChain.then(task, task);
  watchChain = run.then(() => {}, () => {});
  return run;
}

async function postWatch(body) {
  return postWatchBody("/api/watchlist", body);
}

function addedCopy(data, onList) {
  if (data.group_error) return `已加入自选。分组没写上：${data.group_error}`;
  if (data.already && onList) return data.group ? "已经在自选里，分组已记下" : "已经在自选里";
  if (data.refresh === "queued") return "已加入。正在排队刷新，行情稍后补上。";
  if (data.refresh === "rate-limited") return "已加入。刷新刚触发过，这一次不再重复跑。";
  return "已加入。行情等下一次刷新补上。";
}

function submitAdd() {
  const input = $("#add-ticker");
  const ticker = normalizeTicker(input ? input.value : "");
  const msg = $("#add-msg");
  if (!ticker) {
    if (msg) msg.textContent = "代码格式不对";
    return;
  }
  const current = document.querySelector("#sheet [data-group][aria-current='page']");
  const group = current ? current.dataset.group : "";
  const found = themeGroups().find((entry) => entry.id === group);
  const themeName = group && found ? found.name : "未分类";
  const button = $("#add-submit");
  if (button) button.disabled = true;
  closeSheet();
  const onList = (DATA.scanner?.rows || []).some((row) => row.ticker === ticker);
  const wasRemove = pending.remove.includes(ticker);
  pending.remove = pending.remove.filter((item) => item !== ticker);
  if (!onList && !pending.add.some((item) => item.ticker === ticker)) {
    pending.add.unshift({ ticker, group, theme_name: themeName, name: "" });
  } else {
    const existing = pending.add.find((item) => item.ticker === ticker);
    if (existing) {
      existing.group = group;
      existing.theme_name = themeName;
    }
  }
  savePending();
  if (state.route === "scanner") paintScanList();
  toast("正在写入…");
  watchEnqueue(async () => {
    try {
      const data = await postWatch({ op: "add", ticker, group });
      const item = pending.add.find((entry) => entry.ticker === ticker);
      if (item && data.name) item.name = data.name;
      if (data.already && onList) pending.add = pending.add.filter((entry) => entry.ticker !== ticker);
      savePending();
      if (state.route === "scanner") paintScanList();
      toast(addedCopy(data, onList));
    } catch (error) {
      if (!wasRemove) pending.add = pending.add.filter((entry) => entry.ticker !== ticker);
      else if (!pending.remove.includes(ticker)) pending.remove.push(ticker);
      savePending();
      if (state.route === "scanner") paintScanList();
      toast(error.message || "没有写上");
    }
  });
}

function commitRemove(ticker) {
  closeModal();
  const snapshot = pending.add.find((item) => item.ticker === ticker);
  if (!pending.remove.includes(ticker)) pending.remove.push(ticker);
  pending.add = pending.add.filter((item) => item.ticker !== ticker);
  savePending();
  if (state.route === "scanner") paintScanList();
  watchEnqueue(async () => {
    try {
      await postWatch({ op: "remove", ticker, group: "" });
      toast("已从自选去掉");
    } catch (error) {
      pending.remove = pending.remove.filter((item) => item !== ticker);
      if (snapshot && !(DATA.scanner?.rows || []).some((row) => row.ticker === ticker)) pending.add.unshift(snapshot);
      savePending();
      if (state.route === "scanner") paintScanList();
      toast(error.message || "没有写上");
    }
  });
}

function onWatchDown(e) {
  if (e.button != null && e.button !== 0) return;
  const swipe = e.target.closest("#list .swipe");
  if (!swipe) return;
  if (e.target.closest(".info, [data-del], [data-more], .menu")) return;
  const desktop = window.matchMedia("(min-width: 960px)").matches;
  const held = swipe.dataset.held === "1";
  if (desktop) {
    if (e.pointerType === "touch") return;
    const x = e.clientX;
    const y = e.clientY;
    const ticker = swipe.dataset.ticker;
    let fired = false;
    const timer = setTimeout(() => {
      fired = true;
      blockOpen = true;
      if (held) toast("持仓来自账户，不能从自选删除");
      else askRemove(ticker);
    }, 550);
    const cleanup = () => {
      clearTimeout(timer);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
    const onMove = (ev) => {
      if (Math.hypot(ev.clientX - x, ev.clientY - y) > 8) cleanup();
    };
    const onUp = () => {
      cleanup();
      if (fired) setTimeout(() => { blockOpen = false; }, 400);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return;
  }
  const row = swipe.querySelector(".swipe-row");
  if (!row) return;
  drag = {
    row,
    x: e.clientX,
    base: held ? 0 : (row.classList.contains("open") ? -88 : 0),
    dx: held ? 0 : (row.classList.contains("open") ? -88 : 0),
    moved: false,
    held,
    id: e.pointerId,
  };
  row.style.transition = "none";
  try { swipe.setPointerCapture(e.pointerId); } catch { /* 指针可能已经结束 */ }
}

function onWatchMove(e) {
  if (!drag || e.pointerId !== drag.id) return;
  const dx = Math.max(-88, Math.min(0, drag.base + (e.clientX - drag.x)));
  if (Math.abs(e.clientX - drag.x) > 8) drag.moved = true;
  drag.dx = dx;
  drag.row.style.transform = `translateX(${dx}px)`;
  if (drag.moved) e.preventDefault();
}

function onWatchUp(e) {
  if (!drag || (e && e.pointerId != null && e.pointerId !== drag.id)) return;
  const row = drag.row;
  const moved = drag.moved;
  const dx = drag.dx;
  const held = drag.held;
  drag = null;
  row.style.transition = "";
  if (held) {
    row.style.transform = "";
    row.classList.remove("open");
    if (dx < -48) toast("持仓来自账户，不能从自选删除");
  } else if (dx < -48) {
    row.style.transform = "translateX(-88px)";
    row.classList.add("open");
  } else {
    row.style.transform = "";
    row.classList.remove("open");
  }
  if (moved) {
    blockOpen = true;
    setTimeout(() => { blockOpen = false; }, 350);
  }
}

document.addEventListener("pointerdown", onWatchDown);
document.addEventListener("pointermove", onWatchMove, { passive: false });
document.addEventListener("pointerup", onWatchUp);
document.addEventListener("pointercancel", onWatchUp);
document.addEventListener("contextmenu", (e) => {
  if (e.target.closest("#list .swipe")) e.preventDefault();
});

function stopResearchPoll() {
  clearInterval(researchTimer);
  researchTimer = 0;
}

function paintResearchChrome(ticker) {
  const button = document.querySelector("#dock-ai [data-research]");
  if (!button || button.dataset.research !== ticker) return;
  const busy = researchBusy.has(ticker);
  button.disabled = busy;
  button.textContent = busy ? "研究中…（约 5–10 分钟）" : "立即研究";
}

async function fetchResearchStatus(ticker) {
  const res = await fetch(`/api/research/status?ticker=${encodeURIComponent(ticker)}`);
  let data = {};
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok || !data.ok) throw new Error(data.error || "状态没读到");
  return data;
}

function applyResearchRecord(ticker, full) {
  DATA.research = DATA.research || { tickers: {} };
  DATA.research.tickers = DATA.research.tickers || {};
  DATA.research.tickers[ticker] = {
    date: full.date,
    rating: full.rating,
    conclusion: full.conclusion,
    diverge: aiDiverges(ruleActionFor(ticker), full.rating),
  };
  researchBaseline = full.ingested_at || researchBaseline;
  const ai = $("#dock-ai");
  if (ai) ai.innerHTML = aiPanel(full, ticker);
  const tab = document.querySelector('#dock [data-docktab="ai"]');
  if (tab && full.rating) tab.innerHTML = `AI 研究 ${aiTag(full.rating)}`;
  paintResearchChrome(ticker);
}

async function pollResearch(ticker, started) {
  if (researchTicker !== ticker || !$("#dock-ai")) {
    stopResearchPoll();
    return;
  }
  if (Date.now() - started > 20 * 60 * 1000) {
    researchBusy.delete(ticker);
    paintResearchChrome(ticker);
    const msg = $("#research-msg");
    if (msg) msg.textContent = "超过 20 分钟还没有新结果。到 GitHub Actions 看「AI 研究」。";
    stopResearchPoll();
    return;
  }
  try {
    const status = await fetchResearchStatus(ticker);
    const fileRes = await fetch(`data/research/${encodeURIComponent(ticker)}.json?t=${Date.now()}`);
    if (fileRes.ok) {
      const full = await fileRes.json();
      const stamp = full.ingested_at || "";
      if (stamp && stamp !== researchBaseline) {
        const indexRes = await fetch(`data/research.json?t=${Date.now()}`);
        if (indexRes.ok) {
          const index = await indexRes.json();
          DATA.research = Object.assign({}, DATA.research, index, {
            tickers: Object.assign({}, DATA.research.tickers, index.tickers || {}),
          });
        }
        researchBusy.delete(ticker);
        stopResearchPoll();
        applyResearchRecord(ticker, full);
        toast("研究写上了。");
        return;
      }
    }
    if (status && !status.running && status.phase === "failure") {
      researchBusy.delete(ticker);
      paintResearchChrome(ticker);
      const msg = $("#research-msg");
      if (msg) msg.textContent = "这次研究没有成功。";
      stopResearchPoll();
    }
  } catch {
    /* 下一轮再问 */
  }
}

function startResearchPoll(ticker) {
  stopResearchPoll();
  const started = Date.now();
  researchTimer = setInterval(() => { pollResearch(ticker, started); }, 4000);
  pollResearch(ticker, started);
}

async function ensureResearch(ticker, full) {
  stopResearchPoll();
  researchTicker = ticker;
  researchBaseline = (full && full.ingested_at) || "";
  try {
    const status = await fetchResearchStatus(ticker);
    if (researchTicker !== ticker) return;
    if (status.running) {
      researchBusy.add(ticker);
      paintResearchChrome(ticker);
      const msg = $("#research-msg");
      if (msg) msg.textContent = "研究中…（约 5–10 分钟）";
      startResearchPoll(ticker);
      return;
    }
    if (status.limited && status.error) {
      const msg = $("#research-msg");
      if (msg) msg.textContent = status.error;
    }
  } catch {
    /* 详情照常打开 */
  }
}

function startResearch(ticker) {
  if (!ticker || researchBusy.has(ticker)) return;
  researchBusy.add(ticker);
  researchTicker = ticker;
  paintResearchChrome(ticker);
  const msg = $("#research-msg");
  if (msg) msg.textContent = "研究中…（约 5–10 分钟）";
  postWatchBody("/api/research", { ticker }).then((data) => {
    if (data.status !== "queued") throw new Error(data.error || "没有触发研究");
    toast("已排队。研究中…（约 5–10 分钟）");
    startResearchPoll(ticker);
  }).catch((error) => {
    researchBusy.delete(ticker);
    paintResearchChrome(ticker);
    const note = $("#research-msg");
    if (note) note.textContent = error.message || "没有触发研究";
    toast(error.message || "没有触发研究");
  });
}

async function postWatchBody(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok || !data.ok) throw new Error(data.error || "没有写上");
  return data;
}

let tipPinned = false;
let tipAnchor = null;
function hideTip() {
  const el = $("#tip");
  if (!el) return;
  el.hidden = true;
  tipPinned = false;
  tipAnchor = null;
}
function showTip(anchor) {
  const el = $("#tip");
  const term = TERMS[anchor.dataset.term];
  if (!el || !term) return;
  el.hidden = false;
  el.innerHTML = "";
  const title = document.createElement("strong");
  title.textContent = term.short;
  const body = document.createElement("p");
  body.textContent = term.tip;
  el.append(title, body);
  if (anchor.dataset.extra) {
    const extra = document.createElement("span");
    extra.className = "extra";
    extra.textContent = anchor.dataset.extra;
    el.append(extra);
  }
  el.style.width = Math.min(320, window.innerWidth - 24) + "px";
  const rect = anchor.getBoundingClientRect();
  const width = el.offsetWidth;
  const height = el.offsetHeight;
  let left = rect.left;
  if (left + width > window.innerWidth - 12) left = window.innerWidth - 12 - width;
  if (left < 12) left = 12;
  let top = rect.bottom + 8;
  if (top + height > window.innerHeight - 12) top = Math.max(8, rect.top - 8 - height);
  el.style.left = left + "px";
  el.style.top = top + "px";
  tipAnchor = anchor;
}
document.body.addEventListener("click", (e) => {
  const planMode = e.target.closest("[data-planmode]");
  if (planMode) {
    state.planMode = planMode.dataset.planmode;
    render();
    return;
  }
  if (e.target.closest("[data-earn-watch]")) {
    state.earnWatch = !state.earnWatch;
    render();
    return;
  }
  const fillStop = e.target.closest("[data-fill-stop]");
  if (fillStop) {
    const form = fillStop.closest("form");
    const input = form && form.querySelector('[name="stop"]');
    if (input) input.value = fillStop.dataset.fillStop;
    return;
  }
  const saveRisk = e.target.closest("[data-save-risk]");
  if (saveRisk) { saveRiskForm(saveRisk); return; }
  if (e.target.closest("[data-retry-load]")) { start(); return; }
  if (e.target.closest("[data-retry-page]")) { render(); return; }
  if (e.target.closest("[data-reset-scan]")) {
    state.q = "";
    state.seg = "all";
    scanner();
    $("#q").focus();
    return;
  }
  const info = e.target.closest(".info");
  if (info) {
    e.preventDefault();
    e.stopPropagation();
    if (tipPinned && tipAnchor === info) hideTip();
    else { tipPinned = true; showTip(info); }
    return;
  }
  if (tipPinned && !e.target.closest("#tip")) hideTip();
  const researchBtn = e.target.closest("[data-research]");
  if (researchBtn && !researchBtn.disabled) {
    startResearch(researchBtn.dataset.research);
    return;
  }
  if (e.target.closest("[data-confirm]")) {
    const button = e.target.closest("[data-confirm]");
    if (button.dataset.confirm === "ok") commitRemove(button.dataset.ticker);
    else closeModal();
    return;
  }
  if (e.target.closest("[data-sheet-close]")) { closeSheet(); return; }
  if (e.target.closest("#add-open")) { openAdder(); return; }
  if (e.target.closest("#edit-toggle")) {
    state.editing = !state.editing;
    document.body.classList.toggle("editing", state.editing);
    e.target.closest("#edit-toggle").textContent = state.editing ? "完成" : "编辑";
    closeMenus();
    return;
  }
  if (e.target.closest("#add-submit")) { submitAdd(); return; }
  const groupBtn = e.target.closest("#sheet [data-group]");
  if (groupBtn) {
    groupBtn.parentElement.querySelectorAll("[data-group]").forEach((el) => el.removeAttribute("aria-current"));
    groupBtn.setAttribute("aria-current", "page");
    return;
  }
  const del = e.target.closest("[data-del]");
  if (del) { askRemove(del.dataset.del); return; }
  const more = e.target.closest("[data-more]");
  if (more) {
    const swipe = more.closest(".swipe");
    const open = swipe && swipe.classList.contains("menu-open");
    closeMenus();
    if (swipe && !open) swipe.classList.add("menu-open");
    return;
  }
  if (!e.target.closest(".menu")) closeMenus();
  const dockTab = e.target.closest("[data-docktab]");
  if (dockTab && dockTab.closest("#dock")) {
    state.dockTab = dockTab.dataset.docktab;
    document.querySelectorAll("#dock [data-docktab]").forEach((b) => {
      if (b.dataset.docktab === state.dockTab) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    const quote = $("#dock-quote");
    const ai = $("#dock-ai");
    if (quote) quote.hidden = state.dockTab !== "quote";
    if (ai) ai.hidden = state.dockTab !== "ai";
    if (state.dockTab === "quote") {
      const canvas = $("#chart");
      if (canvas && state.bars.length) drawChart(canvas, state.bars);
    }
    return;
  }
  const sortBtn = e.target.closest("[data-sort]");
  if (sortBtn) {
    const scope = sortBtn.dataset.scope;
    const st = scope === "pos" ? state.psort : state.sort;
    const key = sortBtn.dataset.sort;
    if (st.key === key) st.dir *= -1;
    else { st.key = key; st.dir = -1; }
    const bar = sortBtn.parentElement;
    if (bar) bar.outerHTML = sortBar(scope);
    if (scope === "pos") paintPosList();
    else paintScanList();
    document.querySelector(`[data-sort="${key}"][data-scope="${scope}"]`)?.focus({ preventScroll: true });
    return;
  }
  const seg = e.target.closest("[data-seg]");
  if (seg) {
    state.seg = seg.dataset.seg;
    document.querySelectorAll("[data-seg]").forEach((b) => {
      if (b.dataset.seg === state.seg) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    paintScanList();
    return;
  }
  const opener = e.target.closest("[data-open]");
  if (opener) {
    const row = opener.closest(".swipe-row");
    if (row && row.classList.contains("open")) {
      row.classList.remove("open");
      row.style.transform = "";
      return;
    }
    if (blockOpen) return;
    openDetail(opener.dataset.open, opener.dataset.id);
    return;
  }
  const btn = e.target.closest("[data-go]");
  if (btn) go(btn.dataset.go);
});
document.body.addEventListener("change", (e) => {
  const box = e.target.closest("[data-stop-placed]");
  if (!box) return;
  const key = box.dataset.stopPlaced;
  const checked = box.checked;
  const edits = riskEdits();
  const prev = edits[key] || {};
  const known = liveStops().find((item) => item.key === key) || {};
  const next = { ...prev, stop: known.stop, avg_cost: known.avg_cost, entry_date: known.entry_date || prev.entry_date || null, stop_placed: checked };
  const pos = (DATA.portfolio?.positions || []).find((row) => posKey(row) === key);
  if (!pos) return;
  fetch("/api/watchlist", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      op: "risk",
      ticker: pos.ticker,
      instrument: "stock",
      avg_cost: next.avg_cost,
      stop: next.stop,
      entry_date: next.entry_date,
      stop_placed: checked,
    }),
  }).then(async (res) => {
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || "没有写上");
    edits[key] = next;
    saveRiskEdits(edits);
    toast(checked ? "已记下：这只在券商挂过止损单。" : "已改回：还没在券商挂单。");
  }).catch((err) => {
    box.checked = !checked;
    toast(err.message || "没有写上");
  });
});
$("#backdrop").addEventListener("click", closeDetail);
window.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.closest && e.target.closest(".info")) {
    e.preventDefault();
    const info = e.target.closest(".info");
    tipPinned = true;
    showTip(info);
    return;
  }
  if (e.key === "Escape") {
    if (!$("#tip").hidden) { hideTip(); return; }
    if (!$("#modal").hidden) { closeModal(); return; }
    if (!$("#sheet").hidden) { closeSheet(); return; }
    closeMenus();
    closeDetail();
    return;
  }
  if (e.key === "Enter" && document.activeElement && document.activeElement.id === "add-ticker") {
    e.preventDefault();
    submitAdd();
  }
});
window.addEventListener("hashchange", () => {
  const previousRoute = state.route;
  closeDetail();
  route();
  render();
  if (state.route !== previousRoute) window.scrollTo({ top: 0, behavior: "instant" });
  if (state.ticker && state.route === "scanner") openDetail("scan", state.ticker);
});
window.addEventListener("scroll", hideTip, true);
document.body.addEventListener("pointerover", (e) => {
  const info = e.target.closest(".info");
  if (!info || tipPinned || !window.matchMedia("(hover: hover)").matches) return;
  showTip(info);
});
document.body.addEventListener("pointerout", (e) => {
  const info = e.target.closest(".info");
  if (!info || tipPinned) return;
  hideTip();
});
window.addEventListener("resize", () => {
  const canvas = $("#chart");
  if (canvas && state.bars.length) drawChart(canvas, state.bars);
});

bindThemeMedia();
route();
async function start() {
  $("#main").innerHTML = emptyState("正在读取投研数据", "正在加载行情、持仓与研究记录。", "", true);
  try {
    await load();
    dataReady = true;
    applyQuote();
    applyTheme();
    render();
    if (state.ticker && state.route === "scanner") openDetail("scan", state.ticker);
  } catch {
    dataReady = false;
    $("#main").innerHTML = emptyState("投研数据暂时无法读取", "请检查网络连接，或等待数据更新后重试。", `<button type="button" class="secondary" data-retry-load>重新加载</button>`);
  }
}
start();
