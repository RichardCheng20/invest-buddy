# Signal Rules Spec — Dashboard Scanner (v1.0, 2026-10-09)

Source of truth: `02_framework.md` §2.1–2.2. All rules are evaluated on **daily bars at the close** (US/Eastern) unless marked `intraday`. Prices are split/dividend-adjusted. Missing data → the rule evaluates to `null` (not `false`) and the signal is suppressed.

## 1. Account parameters

| Key | Default | Notes |
|---|---|---|
| `nlv_base` | 100000 | EXAMPLE drawdown baseline in USD; updated to the rolling high-water mark |
| `risk_swing` | 0.005 | Fraction of NLV risked per short-term trade |
| `risk_position` | 0.010 | Fraction of NLV risked per medium-term trade |
| `max_open_risk` | 0.04 | Sum of (entry−stop)×shares across open positions ÷ NLV |
| `max_pos_pct` | 0.10 | Max position market value ÷ NLV |
| `max_theme_pct` | 0.25 | Max market value per theme/sector ÷ NLV |
| `max_gross` | 1.00 | No margin allowed |
| `max_stop_pct_swing` | 0.08 | Max stop distance for short-term trades |
| `max_stop_pct_position` | 0.12 | Max stop distance for medium-term trades |
| `buy_zone_pct` | 0.05 | Max entry price above pivot |

## 2. Indicator definitions

```
SMA(n), EMA(n)          on close
ATR(n)                  Wilder; ATRpct = ATR14 / close
RVOL                    volume / SMA(volume, 50)
RSI(14)                 Wilder
MACD                    EMA12 − EMA26; signal = EMA9(MACD); hist = MACD − signal
RSline                  close / SPY.close
RS_raw                  0.4*R(63) + 0.2*R(63, lag 63) + 0.2*R(63, lag 126) + 0.2*R(63, lag 189)
                        where R(n, lag k) = return over n bars ending k bars ago
RS_rank                 percentile (1–99) of RS_raw within universe (watchlist ∪ S&P 500)
BB(20,2)                SMA20 ± 2σ;  KC(20,1.5) = EMA20 ± 1.5*ATR20
squeeze                 BB_upper < KC_upper AND BB_lower > KC_lower
                        OR BBwidth <= 10th percentile of the last 126 bars
AVWAP(anchor)           Σ(typical_price*vol) / Σ(vol) from the anchor bar
                        anchors: last_earnings_day, last_gap_day(|gap| >= 5%), 52w_high, 52w_low, YTD
slope50                 (SMA50 − SMA50[20]) / SMA50[20]
flat_market             |slope50| < 0.01 AND close has crossed SMA50 >= 4 times in the last 20 bars
dist_day(idx)           idx.close/idx.close[1] − 1 <= −0.002 AND idx.vol > idx.vol[1]
chandelier              HHV(high, 22) − 3*ATR22
implied_move            ATM straddle mid (first expiry after earnings) / close
```

## 3. Market regime (L0) — computed once per day

```
score  = (SPY.close > SPY.SMA50)
       + (SPY.close > SPY.SMA200 AND SPY.SMA50 > SPY.SMA200)
       + (QQQ.close > QQQ.SMA50)
       + (pct_SPX_above_50d > 50 AND pct_SPX_above_200d > 40)
       + (VIX < VIX3M)
if count(dist_day(SPY) OR dist_day(QQQ), last 25 bars) >= 5:  score -= 1   (floor at 0)
regime = GREEN if score >= 4; YELLOW if score in [2,3]; RED if score <= 1
if VIX/VIX3M > 1.00: regime = RED   (cleared only after 2 consecutive closes < 0.95)
risk_mult = {GREEN: 1.0, YELLOW: 0.5, RED: 0.0}     # RED: no new long entries
max_new_entries_per_week = {GREEN: none, YELLOW: 2, RED: 0}
```
The final exposure cap is the lower of the macro tier (§1.1: 70 / 60 / 30–40%) and the regime cap.

## 4. Eligibility filters

```
liquid        = SMA(close*volume, 50) >= 200e6
weekly_ok     = close_w > SMA30_w AND SMA30_w > SMA30_w[1]
stage2        = close > SMA50 > SMA150 > SMA200
                AND SMA200 > SMA200[20]
                AND close >= 1.30 * LLV(low, 252)
                AND close >= 0.75 * HHV(high, 252)
leader_pos    = stage2 AND weekly_ok AND RS_rank >= 80 AND sector_RS_ok
leader_swing  = close > EMA21 AND RS_rank >= 70 AND sector_RS_ok
sector_RS_ok  = (sectorETF.close / SPY.close) > SMA50(sectorETF.close / SPY.close)
extended      = (close − EMA21) > 2.5*ATR14 OR close > 1.25*SMA50
earnings_blk  = trading days to next earnings <= 5
hi_vol        = ATRpct > 0.05      → risk_mult *= 0.5
```

## 5. Entry signals (primary). Require `liquid AND NOT extended AND NOT earnings_blk AND regime != RED AND NOT flat_market`

| ID | Condition | Stop | Size |
|---|---|---|---|
| E1 Breakout | `leader_pos AND base_detected AND close > pivot AND close <= pivot*(1+buy_zone_pct) AND RVOL >= 1.5` | `min(base_low − 0.25*ATR, entry − 2.5*ATR)`† | full |
| E2 Pullback | `stage2 AND RS_rank >= 70 AND low <= MA + 1*ATR AND close >= MA` (MA ∈ {EMA21, SMA50}) `AND mean(RVOL, pullback bars) <= 0.8 AND close > open AND (close−low)/(high−low) >= 0.5` | `min(pullback_low − 0.25*ATR, entry − 1.5*ATR)`† | 50%; add the remaining 50% when the next close > the trigger bar's high |
| E3 Pocket pivot | `close > close[1] AND volume > max(volume of down bars in the last 10) AND (abs(close/EMA10 − 1) <= 0.02 OR abs(close/SMA50 − 1) <= 0.02) AND (close − SMA50) <= 2*ATR` | `entry − 1.5*ATR`† | 50%; add the remainder on an E1 at the pivot |
| E4 Earnings gap | gap day: `open/close[1] − 1 >= 0.05 AND RVOL >= 3 AND (close−low)/(high−low) >= 0.5`; entry within 10 bars when `close > AVWAP(earnings) AND low >= gapday_low` | `gapday_low − 0.25*ATR` | full |
| E5 Squeeze release | `squeeze[1] AND close > KC_upper AND RVOL >= 1.5 AND regime == GREEN AND leader_swing` | `entry − 1.5*ATR` | short-term only; `time_stop = 10` |

† Common stop constraints: `stop_dist >= 1*ATR14`; `stop_dist/entry <= max_stop_pct_*`, otherwise reject the signal.

Base detection (`base_detected`, pivot = base high unless noted):
- **Flat base**: length >= 25 bars; (HHV − LLV)/HHV <= 0.15.
- **Cup with handle**: cup 35–325 bars with depth 12–35%; handle in the upper half of the cup, depth <= 12%, mean handle RVOL < 1. Pivot = handle high.
- **VCP**: 2–4 successive pullbacks, each with depth < 0.6× the previous one; last pullback depth <= 8% and mean RVOL < 1. Pivot = high of the last contraction.

## 6. Confirmation score (C1) → sizing

```
c = (RSline == HHV(RSline, 252))
  + (UDvol50 >= 1.2)            # Σ vol of up bars with RVOL>1 / Σ vol of down bars with RVOL>1, last 50 bars
  + momentum_ok                  # (RSI pullback min in [40,50] AND RSI > RSI[1]) OR (MACD hist > hist[1] after >= 3 falling bars); counts max 1
size_mult = 1.0 if c >= 2 else 0.5
shares = floor( NLV * risk_base * risk_mult * size_mult / (entry − stop) )
shares = min(shares, floor(max_pos_pct * NLV / entry))
reject the trade if open_risk + new_risk > max_open_risk OR theme_pct + new > max_theme_pct OR gross > max_gross
reject if new entries in the same theme today >= 1
```

## 7. Add-on

**A1**: `unrealized >= 1R AND (new E1 pivot breakout OR (low <= EMA10|EMA21 AND close > high[1]))` → add `<= 0.5 × initial shares`. Never add to a position below cost.

## 8. Exit / management (evaluated for every open position)

| ID | Condition | Action | Priority |
|---|---|---|---|
| X1 | `intraday low <= stop` (resting stop order) | exit 100% | HARD |
| X7 | `earnings_blk AND shares*entry*1.5*implied_move > 0.01*NLV` (fallback: implied_move = 0.15) | trim until the condition passes, before the earnings date | HARD |
| P1 | NLV drawdown from HWM: `<= −3%` no new swing trades; `<= −5%` gross <= 60%; `<= −7.5%` gross <= 35%; `<= −10%` flat equities + 10-day cooldown | enforce | HARD |
| X2 | within 3 bars of an E1: `close < pivot AND RVOL >= 1.0` → sell 50%; `close < breakout_day_low` → sell 100% | — | primary |
| X3 | Trailing stop. Swing: `close < EMA10` (if RS_rank >= 90) else `close < EMA21`. Position: `close < chandelier` | exit the remainder | primary |
| X4 | `close < SMA50 AND RVOL >= 1.5` OR `close < AVWAP(last_earnings)` | sell 50% (position trades) | primary |
| X5 | swing trade: `bars_held >= 10 AND max_favorable < 1R` | exit 100% | primary |
| X6 | at `+2R` sell 1/3; climax: `close > 1.25*SMA50` OR 3 consecutive gap-ups OR `(RSI > 80 AND RVOL >= 1.5 AND close−open <= 0)` → sell 1/3–1/2 | take profit | primary |
| BE | `unrealized >= 1R` → `stop = max(stop, entry)` | move stop | rule |
| X8 | `RSline <= LLV(RSline, 63) AND |close/close[20] − 1| < 0.03` OR `count(stock distribution days within 10% of the 52-week high, 10 bars) >= 3` (distribution day = down >= 2% with RVOL >= 1.5) | tighten stop to 1×ATR or sell 1/3 | confirm |

## 9. Market-level alerts

- **R1**: regime changes (any transition).
- **R2**: SPY/QQQ distribution count reaches 5 in 25 bars.
- **R3**: `VIX/VIX3M > 1.0`.
- **IDX**: SPY or QQQ closes below SMA200 → core index bucket −50%; re-enter in thirds after 2 consecutive closes above SMA200.

## 10. Suppressions / anti-whipsaw

- `flat_market` → disable E2 and the MA-based parts of X3/X4 (keep ATR stops only).
- After 2 consecutive stop-outs on the same ticker → 10-bar cooldown for that ticker.
- On FOMC, CPI, NFP and PCE days: primary signals are evaluated at the close only; no intraday entries.
- Leveraged / inverse ETFs: compute signals on the underlying index; execute in the ETF with `max_hold = 5` bars and `exposure = notional × leverage`.
- Momentum indicators (RSI, MACD, stochastics) contribute at most 1 point in total.

## 11. Output schema (per ticker per day)

```json
{"date":"YYYY-MM-DD","ticker":"XXX","regime":"GREEN|YELLOW|RED","signals":["E1","C1:2"],
 "entry":0.0,"stop":0.0,"pivot":0.0,"atr14":0.0,"rs_rank":0,"rvol":0.0,
 "shares":0,"risk_usd":0.0,"r_targets":{"1R":0.0,"2R":0.0},
 "levels":{"ema10":0.0,"ema21":0.0,"sma50":0.0,"sma200":0.0,"avwap_earn":0.0},
 "flags":["extended","earnings_blk","hi_vol"],"exits":["X4"]}
```
