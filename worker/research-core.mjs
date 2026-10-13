/** 立即研究的次数账本。不访问网络。 */

export const RESEARCH_GAP_MS = 30 * 60 * 1000;
export const RESEARCH_DAILY_CAP = 10;

export function shanghaiDay(ms) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(ms);
}

export function parseLedger(text) {
  if (!text || !String(text).trim()) return [];
  try {
    const data = JSON.parse(text);
    const runs = Array.isArray(data.runs) ? data.runs : [];
    return runs.filter((run) => run && typeof run.ticker === "string" && typeof run.at === "string" && Number.isFinite(Date.parse(run.at)));
  } catch {
    return [];
  }
}

export function serializeLedger(runs) {
  return `${JSON.stringify({ runs }, null, 2)}\n`;
}

function prune(runs, nowMs) {
  const cutoff = nowMs - 36 * 60 * 60 * 1000;
  return runs.filter((run) => Date.parse(run.at) >= cutoff);
}

export function researchDecision(runs, ticker, nowMs) {
  const kept = prune(runs, nowMs);
  const day = shanghaiDay(nowMs);
  const today = kept.filter((run) => shanghaiDay(Date.parse(run.at)) === day);
  if (today.length >= RESEARCH_DAILY_CAP) {
    return { ok: false, error: "今天已经研究 10 次，到明天（北京时间）再试" };
  }
  const last = kept
    .filter((run) => run.ticker === ticker)
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at))[0];
  if (last && nowMs - Date.parse(last.at) < RESEARCH_GAP_MS) {
    return { ok: false, error: "这只 30 分钟内已经研究过，稍后再试" };
  }
  return { ok: true, runs: kept };
}

export function appendRun(text, ticker, nowMs) {
  const decision = researchDecision(parseLedger(text), ticker, nowMs);
  if (!decision.ok) {
    const error = new Error(decision.error);
    error.status = 429;
    throw error;
  }
  const at = new Date(nowMs).toISOString();
  return serializeLedger([...decision.runs, { ticker, at }]);
}

export function dropRun(text, ticker, at) {
  const runs = parseLedger(text).filter((run) => !(run.ticker === ticker && run.at === at));
  return serializeLedger(runs);
}
