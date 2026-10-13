/** 只改持仓里的成本、止损、建仓日和「已在券商挂单」。保留注释和其余行的顺序。 */

function blockMeta(block) {
  const pick = (key) => {
    const match = new RegExp(`(?:^|\\n)    ${key}:\\s*([^#\\n]*)`).exec(block);
    return match ? match[1].trim().replace(/^["']|["']$/g, "") : "";
  };
  const tickerLine = /(?:^|\n)[ \t-]*ticker:\s*([^\s#]+)/.exec(block);
  return {
    ticker: (tickerLine ? tickerLine[1] : "").replace(/\./g, "-").replace(/^["']|["']$/g, "").toUpperCase(),
    instrument: pick("instrument") || "stock",
    expiry: pick("expiry"),
    strike: pick("strike"),
    right: pick("right").toUpperCase(),
  };
}

function splitPositions(yaml) {
  const marker = "\n  - ticker:";
  const start = yaml.indexOf(marker);
  if (start < 0) return null;
  const head = yaml.slice(0, start);
  const rest = yaml.slice(start + 1);
  const parts = rest.split(marker);
  const blocks = parts.map((part, index) => (index === 0 ? part : `  - ticker:${part}`));
  return { head, blocks };
}

function samePosition(meta, spec) {
  if (!meta.ticker || meta.ticker !== spec.ticker) return false;
  const instrument = spec.instrument || "stock";
  if (meta.instrument !== instrument) return false;
  if (instrument !== "option") return true;
  return meta.expiry === spec.expiry && Number(meta.strike) === Number(spec.strike) && meta.right === spec.right;
}

function renderValue(key, value) {
  if (value == null || value === "") return "null";
  if (key === "stop_placed") return value ? "true" : "false";
  if (key === "entry_date") return `"${value}"`;
  const number = Number(value);
  if (!Number.isFinite(number)) return "null";
  const text = number.toFixed(4).replace(/\.?0+$/, "");
  return text || "0";
}

function setField(block, key, value) {
  const rendered = renderValue(key, value);
  const line = new RegExp(`^(    ${key}:\\s*)([^#\\n]*?)([ \\t]*#.*)?$`, "m");
  if (line.test(block)) {
    return block.replace(line, (_, prefix, _old, comment) => `${prefix}${rendered}${comment || ""}`);
  }
  const inserted = `    ${key}: ${rendered}\n`;
  if (block.endsWith("\n")) return `${block}${inserted}`;
  return `${block}\n${inserted}`;
}

export function applyRiskFields(yaml, spec) {
  const parts = splitPositions(String(yaml || ""));
  if (!parts) {
    const error = new Error("账户文件里没有持仓");
    error.status = 400;
    throw error;
  }
  const index = parts.blocks.findIndex((block) => samePosition(blockMeta(block), spec));
  if (index < 0) {
    const error = new Error("账户文件里没有这笔记仓");
    error.status = 404;
    throw error;
  }
  let block = parts.blocks[index];
  for (const key of ["avg_cost", "stop", "entry_date", "stop_placed"]) {
    if (Object.prototype.hasOwnProperty.call(spec, key)) block = setField(block, key, spec[key]);
  }
  parts.blocks[index] = block;
  return `${parts.head}\n${parts.blocks.join("\n")}`;
}

export function parseRiskSpec(body) {
  const ticker = String(body.ticker || "").trim().toUpperCase().replace(/\./g, "-");
  if (!/^[A-Z]{1,5}(-[A-Z]{1,2})?$/.test(ticker)) return { error: "代码格式不对" };
  const instrument = body.instrument === "option" ? "option" : "stock";
  const spec = { ticker, instrument };
  if (instrument === "option") {
    spec.expiry = String(body.expiry || "").slice(0, 10);
    spec.strike = Number(body.strike);
    spec.right = String(body.right || "").trim().toUpperCase();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(spec.expiry) || !Number.isFinite(spec.strike) || !/^[CP]$/.test(spec.right)) {
      return { error: "期权持仓对不上" };
    }
  }
  for (const key of ["avg_cost", "stop"]) {
    if (body[key] == null || body[key] === "") spec[key] = null;
    else {
      const number = Number(body[key]);
      if (!Number.isFinite(number) || number < 0 || number > 1000000) return { error: `${key === "stop" ? "止损价" : "成本"}不对` };
      spec[key] = number;
    }
  }
  if (body.entry_date == null || body.entry_date === "") spec.entry_date = null;
  else {
    const day = String(body.entry_date).slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return { error: "建仓日要写成 YYYY-MM-DD" };
    spec.entry_date = day;
  }
  spec.stop_placed = Boolean(body.stop_placed);
  return { spec };
}
