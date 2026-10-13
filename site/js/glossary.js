var TERMS = {
  regime: {
    group: "市场环境",
    short: "市场环境分",
    tip: "0 到 5 分，看五件事：标普是否在 50 日均线之上、标普 50 日是否高于 200 日、纳指是否在 50 日之上、观察列表里有多少股票站上均线（宽度）、近月波动 VIX 是否低于三个月 VIX。4–5 分偏多，单笔风险按满额，可以进攻；2–3 分谨慎，单笔风险减半；0–1 分防守，不再开新的多头。派发日过多会再扣 1 分。这是仓位尺度，不是某一只股票的买卖指令。",
  },
  fg: {
    group: "情绪",
    short: "CNN 恐惧贪婪指数",
    tip: "0 到 100。0 是极度恐惧，100 是极度贪婪，50 附近是中性。25 以下极度恐惧，45 以下偏恐惧，这时逆向看法偏多；75 以上偏贪婪，逆向宜谨慎。它不计入市场环境分。",
  },
  fg_scale: {
    group: "情绪",
    short: "分项得分",
    tip: "右边每一项都是 0 到 100 的分，不是原始行情。0 表示这项极度恐惧，100 表示极度贪婪。原始值写在分数下面，用来说明这个分是从什么数换算来的。",
  },
  fg_mom: {
    group: "情绪",
    short: "动量",
    tip: "看标普 500 相对 125 日均线的位置。旁边的点位是标普指数本身。分高表示指数远在均线之上，偏贪婪。",
  },
  fg_hl: {
    group: "情绪",
    short: "新高减新低",
    tip: "创新高的家数减创新低的家数。负数表示新低更多，偏恐惧。分数是把这个家数差换算成 0–100。",
  },
  fg_mcc: {
    group: "情绪",
    short: "涨跌量麦克莱伦",
    tip: "麦克莱伦振荡器，用上涨成交量减下跌成交量的累计差。原始值越高，上涨的量越占上风。分数仍是 0–100。",
  },
  fg_pc: {
    group: "情绪",
    short: "5 日看跌看涨比",
    tip: "大约 5 个交易日的看跌期权成交除以看涨期权成交。比值高于 1 表示看跌更多，偏恐慌；低于 1 更常见。",
  },
  fg_vix: {
    group: "情绪",
    short: "VIX 相对 50 日",
    tip: "近月波动率 VIX 和它自己的 50 日均值比。VIX 明显高于 50 日均值时市场更慌，分数偏低。",
  },
  fg_junk: {
    group: "情绪",
    short: "垃圾债需求",
    tip: "高收益债相对国债的利差需求。分高表示市场肯承担信用风险，偏贪婪。",
  },
  fg_safe: {
    group: "情绪",
    short: "避险需求",
    tip: "股票相对债券的强弱。分高表示资金更愿意待在股票里，避险需求低。",
  },
  pc: {
    group: "情绪",
    short: "CBOE 股票看跌/看涨期权比",
    tip: "股票期权里，看跌成交量除以看涨成交量。小于 1 是常态，看涨更多；大于 1 表示看跌更多，偏恐慌。5 日是近五个交易日的平均。",
  },
  vixterm: {
    group: "情绪",
    short: "VIX/VIX3M 期限结构",
    tip: "近月波动率 VIX 除以三个月波动率 VIX3M。小于 1 是正常的，远月波动更高；大于 1 叫期限倒挂，近月更慌。",
  },
  aaii: {
    group: "情绪",
    short: "AAII 散户看多减看空",
    tip: "美国个人投资者协会周度调查：看多百分比减看空百分比，单位是个百分点。明显为正表示散户偏多，逆向宜谨慎；明显为负表示散户偏空，逆向偏多。",
  },
  hy: {
    group: "情绪",
    short: "高收益债利差",
    tip: "高收益公司债收益率减去国债收益率，单位是百分点。利差走阔通常表示信用风险上升。",
  },
  E1: { group: "买卖规则", short: "突破买点", tip: "价格放量突破整理平台。失效看收盘是否跌破突破结构的低点或止损价。" },
  E2: { group: "买卖规则", short: "回踩买点", tip: "上升趋势里缩量回踩 21 日或 50 日均线后再收阳。失效看收盘是否跌破回踩低点。" },
  E3: { group: "买卖规则", short: "口袋支点", tip: "紧贴均线、收盘上涨，而且成交量盖过近几日的下跌日。失效看收盘是否跌破 10 日均线或止损。" },
  E4: { group: "买卖规则", short: "财报跳空买点", tip: "财报后向上跳空并收在高位。失效看收盘是否跌破跳空当天的低点。" },
  E5: { group: "买卖规则", short: "挤压买点", tip: "波动被压缩后放量向上释放。短线若迟迟到不了一倍风险距离，或跌破止损，就失效。" },
  C1: {
    group: "买卖规则",
    short: "买入确认分",
    tip: "满分 3 分，用来决定仓位而不是决定能不能看。三项是：相对强度处在近一年高点、放量上涨量除以放量下跌量达到 1.2、相对强弱指数或指数平滑异同出现拐头。满 2 分用满仓系数，0 分或 1 分仓位减半。没打勾的那几项就是还缺的条件。",
  },
  X1: { group: "买卖规则", short: "固定止损", need: "在持仓里填上止损价。不填的话，价格碰到止损也判断不了。", tip: "最低价碰到你事先写下的止损价就卖出。止损价留空时这条是未定，不是没触发。" },
  X2: { group: "买卖规则", short: "突破失败", need: "在交易日志里写下这次突破的枢轴价和突破当天的最低价。不写就无法判断突破有没有失败。", tip: "突破后很快跌回平台里。要对照交易日志里的枢轴价和突破日低点。日志是空的时这条是未定。" },
  X3: { group: "买卖规则", short: "跌破吊灯移动止损", tip: "吊灯移动止损是用约 22 日最高价减 3 倍平均真实波幅得到的移动止损。中线收盘跌破它就卖出。短线则改看 10 日或 21 日均线。" },
  X4: { group: "买卖规则", short: "跌破均线减仓", tip: "放量跌破 50 日均线，或跌破财报后的锚定均价。规则是减掉一部分，不是一律清仓。" },
  X5: { group: "买卖规则", short: "时间止损", need: "填上建仓日、成本和止损价。不填就无法判断短线是否已经超时。", tip: "短线持有超过规定的 K 线数还没走到一倍风险距离，就离场。缺建仓日或成本时这条是未定。中线仓不评估这条。" },
  X6: { group: "买卖规则", short: "高潮止盈", tip: "涨得离 50 日均线太远、跳空或相对强弱指数过热时减仓锁住利润。" },
  X7: { group: "买卖规则", short: "财报前减仓", tip: "财报前的跳空压力测试没通过，就在财报前减仓。" },
  X8: { group: "买卖规则", short: "强度走坏减仓", tip: "相对强度掉下来，或高位出现放量下跌。规则是减仓。" },
  A1: { group: "买卖规则", short: "盈利加仓", need: "填上成本和止损价，才能算出一倍风险距离。不填就不能判断能不能加仓。", tip: "浮盈达到一倍风险距离后才考虑加仓。成本和止损有一个是空的，这条就是未定。" },
  rs: { group: "技术指标", short: "相对强度排名", tip: "在这份观察列表内部比较过去一年的涨幅，1 最弱，名次越大越强。不是全市场排名，也不是相对标普的超额收益本身。" },
  atr: { group: "技术指标", short: "平均真实波幅", tip: "最近 14 个交易日价格平均每天波动多少。波动幅度是它除以现价，用来看止损会不会太近或太远。" },
  rsi: { group: "技术指标", short: "相对强弱指数", tip: "0 到 100。70 以上常被看成偏热，30 以下偏冷。这里用 14 日。" },
  macd: { group: "技术指标", short: "指数平滑异同", tip: "12 日和 26 日指数均线的差，再拿 9 日线看它是否拐头。买入确认分里，它和相对强弱指数合计最多算 1 分。" },
  chandelier: { group: "技术指标", short: "吊灯移动止损", tip: "约 22 日最高价减去 3 倍平均真实波幅。收盘跌破它，中线的移动止损就触发。" },
  stage2: { group: "技术指标", short: "第二阶段上升趋势", tip: "价格在上升的 200 日均线之上，并且离 52 周低点足够远、离 52 周高点不太远。不成立只说明不在这套上升阶段里。" },
  rvol: { group: "技术指标", short: "量比", tip: "今天成交量除以过去 50 日的平均成交量。大于 1 表示比平时更活跃。" },
  illiquid: { group: "技术指标", short: "成交额偏低", tip: "50 日平均成交额低于 2 亿美元。规则会把它当成流动性不足。" },
  extended: { group: "技术指标", short: "涨幅离均线太远", tip: "收盘已经高过 21 日均线好几个波动幅度，或远高于 50 日均线。这时不追。" },
  earnings_unknown: { group: "技术指标", short: "财报日期未知", tip: "还不知道下一次财报在哪一天，财报前的限制没法算。" },
  earnings_blk: { group: "技术指标", short: "财报前不开新仓", tip: "距离财报不到规定的交易日，规则不允许新开仓。" },
  flat_market: { group: "技术指标", short: "大盘横盘", tip: "指数来回穿越均线，趋势不明显。" },
  hi_vol: { group: "技术指标", short: "波动很大", tip: "平均真实波幅占价格的比例过高，单笔风险会再减半。" },
  sector_lag: { group: "技术指标", short: "行业偏弱", tip: "所属行业 ETF 没有强于标普。" },
  weekly_below: { group: "技术指标", short: "周线在均线之下", tip: "周线收盘低于周线均线，中期趋势偏弱。" },
  inverse_etf: { group: "技术指标", short: "反向基金", tip: "涨跌方向和指数相反。不按普通多头的买点处理。" },
  distribution: {
    group: "市场环境",
    short: "派发日",
    tip: "标普或纳指 ETF 当天收跌，而且成交量高于前一天，就记一个派发日，也就是放量下跌的一天。看的是近 25 个交易日里这种日子有多少天。达到 5 天，市场环境分再减 1 分。",
  },
  stop_suggest: {
    group: "风控",
    short: "建议止损",
    tip: "两条参考里更靠近现价、且仍低于现价的那条。一条是吊灯移动止损，另一条是 50 日均线减去 2 倍平均真实波幅。点一下只是填进输入框，不会自动保存。",
  },
  open_risk: {
    group: "风控",
    short: "止损风险合计",
    tip: "每只股票用（现价减止损价）乘以股数，只加正数。全部止损都填上之后，才和净值的 4% 比较。有一只没填，合计就不完整，新开仓仍会被抑制。",
  },
  deleverage: {
    group: "风控",
    short: "减仓计划",
    tip: "按总敞口不超过 1 倍、单票不超过 10%、主题不超过 25% 排的卖出顺序。规则已经给出卖出的可以整仓退出；规则是持有的只减到上限，不会减到零。看涨的 delta 若单独超过单票上限，默认卖掉。做完后各项限额都要满足。这是计划，不是下单。",
  },
  earn_gap: {
    group: "风控",
    short: "财报跳空",
    tip: "用过去最多 8 次财报附近的日线。平均绝对涨跌看收盘相对前收，最大不利看最低价相对前收。多头的不利方向是下跌。",
  },
  implied_move: {
    group: "风控",
    short: "隐含波动",
    tip: "财报日之后最近一个到期日上，平值看涨和平值看跌的中间价相加，再除以现价。期权链取不到就留空，不用 15% 去填。",
  },
  delta_exp: {
    group: "风控",
    short: "delta 敞口",
    tip: "看涨期权按 delta 乘以 100 再乘以正股价格，计入单票和主题，而不是只算权利金。delta 用期权链的隐含波动和 4% 利率近似，不是券商给出的希腊值。",
  },
};

var TERM_ORDER = ["regime", "distribution", "stop_suggest", "open_risk", "deleverage", "earn_gap", "implied_move", "delta_exp", "fg", "fg_scale", "fg_mom", "fg_hl", "fg_mcc", "fg_pc", "fg_vix", "fg_junk", "fg_safe", "pc", "vixterm", "aaii", "hy", "E1", "E2", "E3", "E4", "E5", "C1", "X1", "X2", "X3", "X4", "X5", "X6", "X7", "X8", "A1", "rs", "atr", "rsi", "macd", "chandelier", "stage2", "rvol", "illiquid", "extended", "earnings_unknown", "earnings_blk", "flat_market", "hi_vol", "sector_lag", "weekly_below", "inverse_etf"];

function tip(id, extra) {
  if (!TERMS[id]) return "";
  const more = extra ? ` data-extra="${esc(extra)}"` : "";
  return `<span class="info" role="button" tabindex="0" data-term="${esc(id)}"${more} aria-label="${esc(TERMS[id].short)}说明">ⓘ</span>`;
}

function plain(text) {
  if (text == null || text === "") return "";
  let s = String(text);
  const phrases = [
    [/E1\s*[–-]\s*E5(?:\s*买点)?/g, "突破、回踩、口袋支点、财报跳空或挤压买点"],
    [/X1\s*硬止损/g, "固定止损"],
    [/X2\s*突破失败/g, "突破失败"],
    [/X3\s*移动止损/g, "跌破吊灯移动止损"],
    [/X4\s*跌破均线/g, "跌破均线减仓"],
    [/X5\s*时间止损/g, "时间止损"],
    [/X6\s*高潮止盈/g, "高潮止盈"],
    [/X7\s*财报减仓/g, "财报前减仓"],
    [/X8\s*强度走坏/g, "强度走坏减仓"],
    [/E1\s*突破买点/g, "突破买点"],
    [/E2\s*回踩买点/g, "回踩买点"],
    [/E3\s*口袋支点/g, "口袋支点"],
    [/E4\s*财报跳空/g, "财报跳空买点"],
    [/E5\s*挤压释放/g, "挤压买点"],
    [/C1:(\d)/g, "买入确认分 $1/3"],
    [/(?<!买入)确认分\s*(\d)\s*\/\s*3/g, "买入确认分 $1/3"],
    [/RS\s*排名/g, "相对强度排名"],
    [/RSI\s*14|RSI14/g, "14日相对强弱指数"],
    [/ATR\s*14|ATR14/g, "14日平均真实波幅"],
    [/ATR%/g, "波动幅度"],
    [/吊灯止损/g, "吊灯移动止损"],
    [/吊灯参考/g, "吊灯移动止损参考价"],
    [/stage2/gi, "第二阶段上升趋势"],
    [/\b1R\b/g, "一倍风险距离"],
    [/\b2R\b/g, "两倍风险距离"],
    [/\bMACD\b/g, "指数平滑异同"],
    [/\bRSI\b/g, "相对强弱指数"],
    [/\bATR\b/g, "平均真实波幅"],
    [/\bRS\b/g, "相对强度"],
    [/（GREEN）/g, "（偏多，可进攻）"],
    [/（YELLOW）/g, "（中性，宜谨慎）"],
    [/（RED）/g, "（偏空，宜防守）"],
    [/恐惧与贪婪/g, "CNN 恐惧贪婪指数"],
    [/散户差/g, "AAII 散户看多减看空"],
  ];
  phrases.forEach(([re, to]) => { s = s.replace(re, to); });
  s = s.replace(/\b(E[1-5]|X[1-8]|A1|C1)\b/g, (id) => (TERMS[id] ? TERMS[id].short : id));
  s = s.replace(/吊灯(?!移动)/g, "吊灯移动止损");
  s = s.replace(/没有\s+突破/g, "没有突破");
  return s;
}

function fgBand(score) {
  const n = Number(score);
  if (Number.isNaN(n)) return "";
  if (n <= 25) return "极度恐惧，逆向偏多";
  if (n <= 45) return "偏恐惧，逆向偏多";
  if (n < 55) return "中性";
  if (n < 75) return "偏贪婪，逆向偏谨慎";
  return "极度贪婪，逆向偏空";
}

function pcRead(value) {
  const n = Number(value);
  if (Number.isNaN(n)) return "";
  if (n < 1) return "小于 1，正常";
  return "大于 1，偏恐慌";
}

function termRead(ratio, backwardation) {
  const n = Number(ratio);
  if (backwardation || n > 1) return "大于 1，近月更慌";
  return "小于 1，正常";
}

function aaiiRead(spread) {
  const pts = Number(spread) * 100;
  if (Number.isNaN(pts)) return "";
  if (pts >= 10) return "散户明显偏多，逆向偏谨慎";
  if (pts <= -10) return "散户明显偏空，逆向偏多";
  return "多空差不大";
}

function regimePhrase(regime) {
  const score = regime && regime.score != null ? regime.score : "—";
  const tone = regime && regime.regime;
  const word = tone === "GREEN" ? "偏多，可进攻" : tone === "YELLOW" ? "中性，宜谨慎" : tone === "RED" ? "偏空，宜防守" : "未知";
  return `市场环境 ${score}/5（${word}）`;
}

function glossarySection() {
  const groups = [];
  TERM_ORDER.forEach((id) => {
    const term = TERMS[id];
    if (!term) return;
    let bucket = groups.find((g) => g.name === term.group);
    if (!bucket) {
      bucket = { name: term.group, items: [] };
      groups.push(bucket);
    }
    bucket.items.push(`<dt>${esc(term.short)} <span class="code">${esc(id)}</span></dt><dd>${esc(term.tip)}</dd>`);
  });
  return `<h2>术语说明</h2><p>下面这些词在总览、持仓、决策和情绪页里会缩短显示。点 ⓘ 看到的是同一段话。</p>${groups.map((g) => `<h3>${esc(g.name)}</h3><dl class="gloss">${g.items.join("")}</dl>`).join("")}`;
}
