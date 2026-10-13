import { applyRiskFields, parseRiskSpec } from "./portfolio-core.mjs";
import {
  addToTheme,
  addToWatchlist,
  commitText,
  holdingTickers,
  normalizeTicker,
  removeFromWatchlist,
  watchlistHas,
} from "./watchlist-core.mjs";
import { appendRun, dropRun, parseLedger, researchDecision } from "./research-core.mjs";

function repo(env) {
  const value = String((env && env.GH_REPO) || "").trim();
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(value)) return "";
  if (value.toUpperCase().includes("YOUR_GITHUB_USER")) return "";
  return value;
}

function branch(env) {
  const value = String((env && env.GH_BRANCH) || "main").trim();
  if (!/^[A-Za-z0-9._/-]+$/.test(value)) return "main";
  return value;
}
const WATCHLIST = "data/watchlist.txt";
const UNIVERSE = "config/universe.yaml";
const PORTFOLIO = "data/portfolio.yaml";
const RESEARCH_LEDGER = "data/research/dispatch.json";
const DISPATCH_GAP_MS = 15 * 60 * 1000;

let lastDispatchAt = 0;
let certCache = { at: 0, byKid: new Map() };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/research") return handleResearch(request, env);
    if (url.pathname === "/api/research/status") return handleResearchStatus(request, env);
    if (url.pathname !== "/api/watchlist") {
      return env.ASSETS.fetch(request);
    }
    if (request.method !== "POST") {
      return json(405, { ok: false, error: "只接受 POST" });
    }
    const site = request.headers.get("Sec-Fetch-Site");
    if (site && site !== "same-origin" && site !== "none") {
      return json(403, { ok: false, error: "拒绝跨站请求" });
    }
    const type = request.headers.get("content-type") || "";
    if (!type.includes("application/json")) {
      return json(400, { ok: false, error: "需要 JSON" });
    }
    const identity = await accessIdentity(request, env);
    if (identity.error) return json(401, { ok: false, error: identity.error });
    if (!repo(env)) return json(503, { ok: false, error: "还没配置 GH_REPO。改 wrangler.toml 里的占位符后再部署" });
    if (!env.GH_WATCHLIST_TOKEN) {
      return json(503, { ok: false, error: "还没配置 GH_WATCHLIST_TOKEN" });
    }
    let body;
    try {
      const raw = await request.text();
      if (raw.length > 4000) return json(400, { ok: false, error: "请求太大" });
      body = JSON.parse(raw);
    } catch {
      return json(400, { ok: false, error: "JSON 无法解析" });
    }
    const op = body.op;
    if (op === "risk") return saveRisk(env, body);
    const ticker = normalizeTicker(body.ticker);
    const group = String(body.group || "").trim();
    if (op !== "add" && op !== "remove") return json(400, { ok: false, error: "op 只能是 add 或 remove" });
    if (!ticker) return json(400, { ok: false, error: "代码格式不对" });
    if (group && !/^[a-z0-9_]+$/.test(group)) return json(400, { ok: false, error: "分组不存在" });

    const client = githubClient(env);
    try {
      if (op === "remove") {
        const portfolio = await client.get(PORTFOLIO);
        if (holdingTickers(portfolio.text).has(ticker)) {
          return json(403, { ok: false, error: "持仓来自账户文件，不能从自选删除" });
        }
        await commitText(client, WATCHLIST, (text) => removeFromWatchlist(text, ticker), `watchlist: remove ${ticker}`);
      } else {
        const known = await tickerExists(ticker);
        if (!known.ok) return json(400, { ok: false, error: known.error });
        if (group) {
          const universe = await client.get(UNIVERSE);
          if (addToTheme(universe.text, group, ticker) == null) {
            return json(400, { ok: false, error: "分组不存在" });
          }
        }
        const current = await client.get(WATCHLIST);
        const already = watchlistHas(current.text, ticker);
        if (!already) {
          await commitText(client, WATCHLIST, (text) => addToWatchlist(text, ticker), `watchlist: add ${ticker}`);
        }
        let groupError = "";
        if (group) {
          try {
            await commitText(client, UNIVERSE, (text) => addToTheme(text, group, ticker), `watchlist: group ${ticker} ${group}`);
          } catch (error) {
            groupError = error.message || "分组没写上";
          }
        }
        const refresh = await maybeDispatch(env);
        return json(200, { ok: true, op, ticker, group, already, refresh, name: known.name, group_error: groupError });
      }
      const refresh = await maybeDispatch(env);
      return json(200, { ok: true, op, ticker, group: "", refresh });
    } catch (error) {
      return json(error.status || 502, { ok: false, error: error.message || "写入失败" });
    }
  },
};

async function saveRisk(env, body) {
  const parsed = parseRiskSpec(body);
  if (parsed.error) return json(400, { ok: false, error: parsed.error });
  const client = githubClient(env);
  const spec = parsed.spec;
  const label = spec.instrument === "option"
    ? `${spec.ticker} ${spec.expiry} ${spec.strike}${spec.right}`
    : spec.ticker;
  try {
    await commitText(
      client,
      PORTFOLIO,
      (text) => applyRiskFields(text, spec),
      `portfolio: risk ${label}`,
    );
  } catch (error) {
    return json(error.status || 502, { ok: false, error: error.message || "写入失败" });
  }
  return json(200, { ok: true, op: "risk", ticker: spec.ticker, instrument: spec.instrument });
}

async function handleResearch(request, env) {
  if (request.method !== "POST") return json(405, { ok: false, error: "只接受 POST" });
  const denied = await guard(request, env, true);
  if (denied) return denied;
  let body;
  try {
    const raw = await request.text();
    if (raw.length > 2000) return json(400, { ok: false, error: "请求太大" });
    body = JSON.parse(raw);
  } catch {
    return json(400, { ok: false, error: "JSON 无法解析" });
  }
  const ticker = normalizeTicker(body.ticker);
  if (!ticker) return json(400, { ok: false, error: "代码格式不对" });
  const now = Date.now();
  const at = new Date(now).toISOString();
  const client = githubClient(env);
  try {
    await commitText(client, RESEARCH_LEDGER, (text) => appendRun(text, ticker, now), `research: queue ${ticker}`);
  } catch (error) {
    return json(error.status || 502, { ok: false, error: error.message || "没有记下这次研究" });
  }
  const dispatched = await dispatchWorkflow(env, "research.yml", { ticker });
  if (dispatched !== "queued") {
    try {
      await commitText(client, RESEARCH_LEDGER, (text) => dropRun(text, ticker, at), `research: undo ${ticker}`);
    } catch {
      /* 账本没能撤回，这只仍按 30 分钟限额 */
    }
    return json(502, { ok: false, error: "研究工作流没有触发。确认令牌有 Actions 读写权限。" });
  }
  return json(200, { ok: true, ticker, status: "queued" });
}

async function handleResearchStatus(request, env) {
  if (request.method !== "GET") return json(405, { ok: false, error: "只接受 GET" });
  const denied = await guard(request, env, false);
  if (denied) return denied;
  const ticker = normalizeTicker(new URL(request.url).searchParams.get("ticker"));
  if (!ticker) return json(400, { ok: false, error: "代码格式不对" });
  const client = githubClient(env);
  let runs = [];
  try {
    const ledger = await client.get(RESEARCH_LEDGER);
    runs = parseLedger(ledger.text);
  } catch (error) {
    if (error.status !== 404) return json(error.status || 502, { ok: false, error: error.message || "读状态失败" });
  }
  const now = Date.now();
  const decision = researchDecision(runs, ticker, now);
  const last = runs
    .filter((run) => run.ticker === ticker)
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at))[0];
  let phase = "idle";
  let running = false;
  let runUrl = "";
  if (last) {
    const matched = await matchResearchRun(env, ticker, Date.parse(last.at));
    if (matched && matched.status !== "completed") {
      running = true;
      phase = matched.status || "in_progress";
      runUrl = matched.html_url || "";
    } else if (matched && matched.conclusion && matched.conclusion !== "success") {
      phase = "failure";
      runUrl = matched.html_url || "";
    } else if (matched && matched.conclusion === "success") {
      phase = "success";
      runUrl = matched.html_url || "";
    } else if (now - Date.parse(last.at) < 20 * 60 * 1000) {
      running = true;
      phase = "queued";
    }
  }
  return json(200, {
    ok: true,
    ticker,
    running,
    phase,
    started_at: last ? last.at : "",
    run_url: runUrl,
    limited: !running && !decision.ok,
    error: !running && !decision.ok ? decision.error : "",
    message: running ? "研究中…（约 5–10 分钟）" : "",
  });
}

async function guard(request, env, requireJson) {
  const site = request.headers.get("Sec-Fetch-Site");
  if (site && site !== "same-origin" && site !== "none") {
    return json(403, { ok: false, error: "拒绝跨站请求" });
  }
  if (requireJson) {
    const type = request.headers.get("content-type") || "";
    if (!type.includes("application/json")) return json(400, { ok: false, error: "需要 JSON" });
  }
  const identity = await accessIdentity(request, env);
  if (identity.error) return json(401, { ok: false, error: identity.error });
  if (!repo(env)) return json(503, { ok: false, error: "还没配置 GH_REPO。改 wrangler.toml 里的占位符后再部署" });
  if (!env.GH_WATCHLIST_TOKEN) return json(503, { ok: false, error: "还没配置 GH_WATCHLIST_TOKEN" });
  return null;
}

async function dispatchWorkflow(env, workflow, inputs) {
  const response = await fetch(`https://api.github.com/repos/${repo(env)}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: githubHeaders(env, { jsonBody: true }),
    body: JSON.stringify({ ref: branch(env), inputs }),
  });
  return response.status === 204 ? "queued" : "skipped";
}

async function matchResearchRun(env, ticker, startedMs) {
  const response = await fetch(`https://api.github.com/repos/${repo(env)}/actions/workflows/research.yml/runs?per_page=20`, {
    headers: githubHeaders(env),
  });
  if (!response.ok) return null;
  const data = await response.json();
  const runs = data.workflow_runs || [];
  return runs.find((run) => {
    const created = Date.parse(run.created_at || "");
    if (!Number.isFinite(created) || created < startedMs - 3 * 60 * 1000) return false;
    const label = `${run.name || ""} ${run.display_title || ""}`;
    return label.includes(ticker);
  }) || null;
}

function json(status, payload) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

function githubHeaders(env, { jsonBody = false } = {}) {
  const headers = {
    authorization: `Bearer ${env.GH_WATCHLIST_TOKEN}`,
    accept: "application/vnd.github+json",
    "user-agent": "invest-buddy",
    "x-github-api-version": "2022-11-28",
  };
  if (jsonBody) headers["content-type"] = "application/json";
  return headers;
}

function githubClient(env) {
  const headers = githubHeaders(env);
  return {
    async get(path) {
      const response = await fetch(`https://api.github.com/repos/${repo(env)}/contents/${path}?ref=${branch(env)}`, { headers });
      if (response.status === 404 && path === RESEARCH_LEDGER) return { text: "", sha: null };
      if (!response.ok) {
        const error = new Error("读仓库失败");
        error.status = response.status === 404 ? 404 : 502;
        throw error;
      }
      const data = await response.json();
      return { text: decode64(data.content || ""), sha: data.sha };
    },
    async put(path, text, sha, message) {
      const payload = { message, content: encode64(text), branch: branch(env) };
      if (sha) payload.sha = sha;
      const response = await fetch(`https://api.github.com/repos/${repo(env)}/contents/${path}`, {
        method: "PUT",
        headers: githubHeaders(env, { jsonBody: true }),
        body: JSON.stringify(payload),
      });
      if (response.ok) return { ok: true };
      return { ok: false, status: response.status, error: "写入 GitHub 失败" };
    },
  };
}

async function maybeDispatch(env) {
  const now = Date.now();
  if (now - lastDispatchAt < DISPATCH_GAP_MS) return "rate-limited";
  lastDispatchAt = now;
  const response = await fetch(`https://api.github.com/repos/${repo(env)}/actions/workflows/refresh.yml/dispatches`, {
    method: "POST",
    headers: githubHeaders(env, { jsonBody: true }),
    body: JSON.stringify({ ref: branch(env), inputs: { session: "auto" } }),
  });
  if (response.status === 204) return "queued";
  lastDispatchAt = 0;
  return "skipped";
}

export async function tickerExists(ticker) {
  const response = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?interval=1d&range=5d`, {
    headers: { "user-agent": "Mozilla/5.0", accept: "application/json" },
  });
  if (response.status === 404) return { ok: false, error: "找不到这个代码" };
  if (!response.ok) return { ok: false, error: "行情源没有回应，稍后再试" };
  const data = await response.json();
  const meta = data?.chart?.result?.[0]?.meta;
  if (!meta?.symbol) return { ok: false, error: "找不到这个代码" };
  const symbol = String(meta.symbol).toUpperCase().replace(/\./g, "-");
  if (symbol !== ticker) return { ok: false, error: "找不到这个代码" };
  const kind = String(meta.instrumentType || "").toUpperCase();
  if (kind && kind !== "EQUITY" && kind !== "ETF") return { ok: false, error: "只接受美股和 ETF" };
  if (meta.currency !== "USD" || !String(meta.exchangeTimezoneName || "").startsWith("America/")) {
    return { ok: false, error: "只接受美股" };
  }
  return { ok: true, name: meta.shortName || meta.longName || ticker };
}

async function accessIdentity(request, env) {
  const token = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) return { error: "需要 Cloudflare Access 登录" };
  const team = String(env.ACCESS_TEAM_DOMAIN || "").replace(/^https:\/\//, "").replace(/\/$/, "");
  const aud = String(env.ACCESS_AUD || "");
  if (!team || !aud) return { error: "Worker 还没配置 Access 校验" };
  const parts = token.split(".");
  if (parts.length !== 3) return { error: "Access 凭证无效" };
  let header;
  let payload;
  try {
    header = JSON.parse(new TextDecoder().decode(b64url(parts[0])));
    payload = JSON.parse(new TextDecoder().decode(b64url(parts[1])));
  } catch {
    return { error: "Access 凭证无效" };
  }
  if (header.alg !== "RS256" || !header.kid) return { error: "Access 凭证无效" };
  const now = Math.floor(Date.now() / 1000);
  if (!payload.exp || payload.exp < now - 30) return { error: "Access 登录已过期" };
  const iss = `https://${team}`;
  if (payload.iss !== iss) return { error: "Access 凭证无效" };
  const got = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  const allowed = aud.split(",").map((item) => item.trim()).filter(Boolean);
  if (!got.some((item) => allowed.includes(item))) return { error: "Access 凭证无效" };
  const key = await accessKey(iss, header.kid);
  if (!key) return { error: "Access 凭证无效" };
  const valid = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    b64url(parts[2]),
    new TextEncoder().encode(`${parts[0]}.${parts[1]}`),
  );
  if (!valid || (!payload.email && !payload.sub)) return { error: "Access 凭证无效" };
  return { email: payload.email || payload.sub };
}

async function accessKey(iss, kid) {
  if (Date.now() - certCache.at > 60 * 60 * 1000) {
    const response = await fetch(`${iss}/cdn-cgi/access/certs`);
    if (!response.ok) return null;
    const data = await response.json();
    const byKid = new Map();
    for (const jwk of data.keys || []) {
      if (!jwk.kid) continue;
      const key = await crypto.subtle.importKey(
        "jwk",
        { kty: jwk.kty, n: jwk.n, e: jwk.e, alg: "RS256", ext: true },
        { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
        false,
        ["verify"],
      );
      byKid.set(jwk.kid, key);
    }
    certCache = { at: Date.now(), byKid };
  }
  return certCache.byKid.get(kid) || null;
}

function b64url(value) {
  const text = value.replace(/-/g, "+").replace(/_/g, "/");
  const pad = text.length % 4 === 0 ? "" : "=".repeat(4 - (text.length % 4));
  const bin = atob(text + pad);
  return Uint8Array.from(bin, (char) => char.charCodeAt(0));
}

function decode64(value) {
  const bin = atob(String(value).replace(/\n/g, ""));
  const bytes = Uint8Array.from(bin, (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function encode64(value) {
  const bytes = new TextEncoder().encode(value);
  let bin = "";
  for (const byte of bytes) bin += String.fromCharCode(byte);
  return btoa(bin);
}
