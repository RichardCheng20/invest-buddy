/** 自选名单的纯文本改动。不访问网络。 */

export function normalizeTicker(raw) {
  const ticker = String(raw || "").trim().toUpperCase().replace(/\./g, "-");
  if (!/^[A-Z]{1,5}(-[A-Z]{1,2})?$/.test(ticker)) return null;
  if (ticker === "LIST2153") return null;
  return ticker;
}

export function watchlistHas(text, ticker) {
  return text.split(/\s+/).some((token) => normalizeTicker(token) === ticker);
}

export function addToWatchlist(text, ticker) {
  if (watchlistHas(text, ticker)) return text;
  const trimmed = text.replace(/\s*$/, "");
  return `${trimmed}${trimmed ? "\n" : ""}${ticker}\n`;
}

export function removeFromWatchlist(text, ticker) {
  const lines = text.split("\n").map((line) => line
    .split(/(\s+)/)
    .filter((chunk) => /^\s+$/.test(chunk) || normalizeTicker(chunk) !== ticker)
    .join("")
    .replace(/[ \t]{2,}/g, " ")
    .trim());
  return `${lines.filter((line) => line !== "").join("\n")}\n`;
}

export function themeIds(yaml) {
  return [...yaml.matchAll(/(?:^|\n)  ([a-z0-9_]+):\n/g)].map((match) => match[1]);
}

export function addToTheme(yaml, themeId, ticker) {
  if (!themeIds(yaml).includes(themeId)) return null;
  const pattern = new RegExp(`((?:^|\\n)  ${themeId}:\\n(?:.*\\n)*?    tickers:\\s*\\[)([^\\]]*)(\\])`);
  const match = yaml.match(pattern);
  if (!match) return null;
  const items = match[2].split(",").map((item) => item.trim()).filter(Boolean);
  if (items.some((item) => normalizeTicker(item) === ticker)) return yaml;
  const body = items.length ? `${match[2].replace(/\s*$/, "")}, ${ticker}` : ticker;
  return yaml.replace(pattern, `$1${body}$3`);
}

export function holdingTickers(yaml) {
  const held = new Set();
  const pattern = /(?:^|\n)  - ticker:\s*([^\n]+)\n([\s\S]*?)(?=\n  - ticker:|\n\S|\s*$)/g;
  for (const match of yaml.matchAll(pattern)) {
    const instrument = /\n    instrument:\s*(\S+)/.exec(`\n${match[2]}`);
    if (!instrument || instrument[1] === "option") continue;
    const ticker = normalizeTicker(match[1]);
    if (ticker) held.add(ticker);
  }
  return held;
}

export async function commitText(client, path, mutate, message) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const current = await client.get(path);
    const next = mutate(current.text);
    if (next == null) {
      const error = new Error("分组不存在");
      error.status = 400;
      throw error;
    }
    if (next === current.text) return { changed: false };
    const put = await client.put(path, next, current.sha, message);
    if (put.ok) return { changed: true };
    if (put.status !== 409 && put.status !== 422) {
      const error = new Error(put.error || "写入 GitHub 失败");
      error.status = put.status || 502;
      throw error;
    }
  }
  const error = new Error("文件刚被别人改过，没有写上，再试一次");
  error.status = 409;
  throw error;
}
