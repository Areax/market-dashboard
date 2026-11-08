"""Per-stock signal detection for the Sectors tab.

Every signal at bar t uses only bars 0..t (closes included): a swing high or
low found at bar i is treated as unknown until bar i + PIVOT_K, so chart
patterns fire only once all their pivots are confirmed. The event study
enters on the NEXT bar's open, so nothing here may peek past bar t.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from fetchers.indicators import atr, ema, sma, swing_points

PIVOT_K = 5            # bars each side that confirm a swing high/low
PATTERN_MAX_WAIT = 30  # bars after the last pivot is confirmed to wait for the breakout
GAP_MIN = 0.01         # gap must open at least 1% beyond the prior close
GAP_RETEST_WINDOW = 10
BREAKOUT_COOLDOWN = 10  # ignore repeat 52w breakouts within this many bars
TEMPLATE_RESET = 20     # trend template must be off this many bars before a new entry counts


@dataclass(frozen=True)
class Signal:
    key: str
    label: str
    direction: int  # +1 bullish, -1 bearish
    group: str      # "research": published evidence of an edge; "yours": your chart-pattern/candle signals, unproven
    definition: str
    short: str      # compact badge label
    plain: str      # one-line plain-English summary shown above the exact rule


SIGNALS = [
    Signal("trend_template", "Trend template entry", +1, "research",
           f"First day all Minervini trend-template conditions hold after at least {TEMPLATE_RESET} "
           "sessions without them: close > 50 > 150 > 200-day SMA, "
           "200-day SMA higher than 21 sessions ago, close at least 30% above its 52-week low and within "
           "25% of its 52-week high, and 12-month return in the top 30% of this ~110-stock universe.", 'Trend entry',
           'The stock just moved into a healthy long-term uptrend and sits near its 1-year high.'),
    Signal("breakout_52w", "52-week-high breakout", +1, "research",
           "Close above the highest high of the prior 252 sessions on at least 2x the prior 50-day average volume. "
           f"Repeats within {BREAKOUT_COOLDOWN} sessions are ignored.", '52w breakout',
           'The stock closed at a new 1-year high on unusually heavy trading.'),
    Signal("double_bottom", "Double bottom", +1, "yours",
           "Two back-to-back confirmed swing lows (no other swing low between them) 10-60 sessions apart, "
           "within 3% of each other, with a peak at least 5% above them in between. "
           f"Fires on the first close above that peak, within {PATTERN_MAX_WAIT} "
           f"sessions after the second low is confirmed ({PIVOT_K} sessions after it). Skipped if price closes above "
           "the peak before then (the breakout happened before the pattern was knowable) or makes a new low first.", 'Dbl bottom',
           'Price fell to about the same low twice, then broke above the bounce in between.'),
    Signal("inverse_hs", "Inverse head & shoulders", +1, "yours",
           "Three back-to-back confirmed swing lows, each 5-60 sessions after the previous one, where the "
           "middle (head) is at least 3% below both shoulders and the shoulders are within 5% of each other. "
           f"Fires on the first close above the neckline drawn through the two peaks between them, within "
           f"{PATTERN_MAX_WAIT} sessions after the right shoulder is confirmed ({PIVOT_K} sessions after it). "
           "Skipped if that close came before confirmation or price undercuts the head first.", 'Inv H&S',
           'Three dips with the middle one deepest, then price broke above the line joining the bounces.'),
    Signal("bull_gap_retest", "Bull gap retest", +1, "yours",
           f"Gap up of at least {GAP_MIN:.0%} that leaves an unfilled gap (low above prior high). Within "
           f"{GAP_RETEST_WINDOW} sessions price dips back into the gap zone but closes at or above the "
           "prior high. Cancelled if a close fills the gap first.", 'Bull gap retest',
           'The stock jumped up overnight, dipped back into that gap, and held.'),
    Signal("first_red_above_ema21", "First red candle above 21 EMA", +1, "yours",
           "Red candle (close < open) whose whole range sits above the 21-day EMA, after two green "
           "candles that also sat above it.", '1st red > EMA21',
           'In a short uptrend above the 21-day average, the first down day.'),
    Signal("double_top", "Double top", -1, "yours",
           "Mirror of double bottom: two back-to-back confirmed swing highs 10-60 sessions apart within 3% "
           "of each other, a trough at least 5% below them, fires on the first close below that trough "
           "(same confirmation, wait and cancel rules).", 'Dbl top',
           'Price hit about the same high twice, then broke below the dip in between.'),
    Signal("head_shoulders", "Head & shoulders", -1, "yours",
           "Mirror of inverse head & shoulders: three back-to-back swing highs 5-60 sessions apart, head at "
           "least 3% above both shoulders, fires on the first close below the neckline through the two "
           "troughs (same confirmation, wait and cancel rules).", 'H&S',
           'Three peaks with the middle one highest, then price broke below the line joining the dips.'),
    Signal("bear_gap_retest", "Bear gap retest", -1, "yours",
           "Mirror of bull gap retest: gap down, price rallies back into the gap zone but closes at or "
           "below the prior low.", 'Bear gap retest',
           'The stock dropped overnight, bounced back into that gap, and failed there.'),
    Signal("green_below_ema21", "Green candle below 21 EMA", -1, "yours",
           "Green candle (close > open) whose whole range sits below the 21-day EMA, after two red "
           "candles that also sat below it.", 'Green < EMA21',
           'In a short downtrend below the 21-day average, the first up day.'),
]


def _double(highs, lows, closes, swings, kind):
    """Double top (kind='top', swings are swing-high indices) or double
    bottom (kind='bottom', swing lows). Returns fire-bar indices."""
    fires = []
    for a, b in zip(swings, swings[1:]):
        if not 10 <= b - a <= 60:
            continue
        if kind == "top":
            p1, p2 = highs[a], highs[b]
            if abs(p1 - p2) / max(p1, p2) > 0.03:
                continue
            level = lows[a + 1:b].min()
            if (min(p1, p2) - level) / min(p1, p2) < 0.05:
                continue
            extreme = max(p1, p2)
        else:
            p1, p2 = lows[a], lows[b]
            if abs(p1 - p2) / min(p1, p2) > 0.03:
                continue
            level = highs[a + 1:b].max()
            if (level - max(p1, p2)) / max(p1, p2) < 0.05:
                continue
            extreme = min(p1, p2)
        start = b + PIVOT_K
        for t in range(b + 1, min(start + PATTERN_MAX_WAIT, len(closes))):
            if kind == "top" and highs[t] > extreme or kind == "bottom" and lows[t] < extreme:
                break  # pattern failed before it triggered
            broke = kind == "top" and closes[t] < level or kind == "bottom" and closes[t] > level
            if broke and t < start:
                break  # broke out before the second pivot was confirmed: not tradable as this pattern
            if broke:
                fires.append(t)
                break
    return fires


def _head_shoulders(highs, lows, closes, swings, kind):
    """Head & shoulders top (kind='top', swing highs) or inverse
    (kind='bottom', swing lows). Neckline runs through the two opposite
    extremes between the shoulders and the head."""
    fires = []
    for l, h, r in zip(swings, swings[1:], swings[2:]):
        if not (5 <= h - l <= 60 and 5 <= r - h <= 60):
            continue
        if kind == "top":
            pl, ph, pr = highs[l], highs[h], highs[r]
            if ph < 1.03 * max(pl, pr) or abs(pl - pr) / max(pl, pr) > 0.05:
                continue
            n1 = l + 1 + int(np.argmin(lows[l + 1:h]))
            n2 = h + 1 + int(np.argmin(lows[h + 1:r]))
            v1, v2 = lows[n1], lows[n2]
        else:
            pl, ph, pr = lows[l], lows[h], lows[r]
            if ph > 0.97 * min(pl, pr) or abs(pl - pr) / min(pl, pr) > 0.05:
                continue
            n1 = l + 1 + int(np.argmax(highs[l + 1:h]))
            n2 = h + 1 + int(np.argmax(highs[h + 1:r]))
            v1, v2 = highs[n1], highs[n2]
        slope = (v2 - v1) / (n2 - n1)
        start = r + PIVOT_K
        for t in range(r + 1, min(start + PATTERN_MAX_WAIT, len(closes))):
            if kind == "top" and highs[t] > ph or kind == "bottom" and lows[t] < ph:
                break
            neck = v1 + slope * (t - n1)
            broke = kind == "top" and closes[t] < neck or kind == "bottom" and closes[t] > neck
            if broke and t < start:
                break  # broke the neckline before the right shoulder was confirmed
            if broke:
                fires.append(t)
                break
    return fires


def _gap_retests(opens, highs, lows, closes, direction):
    fires = []
    for g in range(1, len(closes)):
        if direction > 0:
            if not (lows[g] > highs[g - 1] and opens[g] >= closes[g - 1] * (1 + GAP_MIN)):
                continue
            zone_edge, zone_far = lows[g], highs[g - 1]  # gap zone spans prior high .. gap-day low
        else:
            if not (highs[g] < lows[g - 1] and opens[g] <= closes[g - 1] * (1 - GAP_MIN)):
                continue
            zone_edge, zone_far = highs[g], lows[g - 1]
        for t in range(g + 1, min(g + 1 + GAP_RETEST_WINDOW, len(closes))):
            if direction > 0:
                if closes[t] < zone_far:
                    break  # gap filled on a close: no retest signal
                if lows[t] <= zone_edge:
                    fires.append(t)
                    break
            else:
                if closes[t] > zone_far:
                    break
                if highs[t] >= zone_edge:
                    fires.append(t)
                    break
    return fires


def compute(df: pd.DataFrame, rs_pct: pd.Series) -> tuple[pd.DataFrame, dict[str, list[int]]]:
    """df: one symbol's Open/High/Low/Close/Volume, no NaN rows. rs_pct: that
    symbol's cross-sectional 12-month-return percentile (0-1) per date.

    Returns (features frame, {signal_key: [bar indices where it fired]})."""
    o, h, l, c, v = (df[col] for col in ("Open", "High", "Low", "Close", "Volume"))
    feat = pd.DataFrame(index=df.index)
    feat["sma50"], feat["sma150"], feat["sma200"] = sma(c, 50), sma(c, 150), sma(c, 200)
    feat["ema21"] = ema(c, 21)
    feat["atr14"] = atr(h, l, c, 14)
    feat["high_52w"] = h.rolling(252, min_periods=252).max()
    feat["low_52w"] = l.rolling(252, min_periods=252).min()
    feat["rs_pct"] = rs_pct.reindex(df.index)

    template = (
        (c > feat["sma50"]) & (feat["sma50"] > feat["sma150"]) & (feat["sma150"] > feat["sma200"])
        & (feat["sma200"] > feat["sma200"].shift(21))
        & (c >= 1.3 * feat["low_52w"]) & (c >= 0.75 * feat["high_52w"])
        & (feat["rs_pct"] >= 0.70)
    )
    feat["trend_template"] = template

    oa, ha, la, ca = (s.to_numpy(dtype=float) for s in (o, h, l, c))
    events: dict[str, list[int]] = {s.key: [] for s in SIGNALS}

    # A fresh entry: template true today after being false (with all inputs
    # real numbers, so the end of warm-up doesn't look like an entry) for
    # each of the prior TEMPLATE_RESET sessions. Without that, a stock
    # flickering across one condition registers as many separate entries.
    inputs_ready = (
        feat[["sma50", "sma150", "sma200", "high_52w", "low_52w", "rs_pct"]].notna().all(axis=1)
        & feat["sma200"].shift(21).notna()
    ).to_numpy()
    tpl = template.to_numpy()
    events["trend_template"] = [
        t for t in range(TEMPLATE_RESET, len(tpl))
        if tpl[t] and not tpl[t - TEMPLATE_RESET:t].any() and inputs_ready[t - TEMPLATE_RESET:t].all()
    ]

    prior_high = h.shift(1).rolling(252, min_periods=252).max().to_numpy()
    prior_vol = v.shift(1).rolling(50, min_periods=50).mean().to_numpy()
    va = v.to_numpy(dtype=float)
    last_breakout = -10**9
    for t in range(len(ca)):
        if ca[t] > prior_high[t] and va[t] >= 2 * prior_vol[t] and t - last_breakout > BREAKOUT_COOLDOWN:
            events["breakout_52w"].append(t)
            last_breakout = t

    sh = swing_points(ha, PIVOT_K, "high")
    sl = swing_points(la, PIVOT_K, "low")
    events["double_top"] = _double(ha, la, ca, sh, "top")
    events["double_bottom"] = _double(ha, la, ca, sl, "bottom")
    events["head_shoulders"] = _head_shoulders(ha, la, ca, sh, "top")
    events["inverse_hs"] = _head_shoulders(ha, la, ca, sl, "bottom")
    events["bull_gap_retest"] = _gap_retests(oa, ha, la, ca, +1)
    events["bear_gap_retest"] = _gap_retests(oa, ha, la, ca, -1)

    e21 = feat["ema21"].to_numpy()
    green, red = ca > oa, ca < oa
    above, below = la > e21, ha < e21
    for t in range(2, len(ca)):
        if red[t] and above[t] and green[t - 1] and green[t - 2] and above[t - 1] and above[t - 2]:
            events["first_red_above_ema21"].append(t)
        if green[t] and below[t] and red[t - 1] and red[t - 2] and below[t - 1] and below[t - 2]:
            events["green_below_ema21"].append(t)

    for key in events:
        events[key] = sorted(set(events[key]))
    return feat, events
