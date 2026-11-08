"""Sectors tab: SPDR sector ETF trend regime, today's signals on each ETF's
top 10 holdings, and a historical event study of every signal.

Design follows the evidence on indicator use (see README):
  - Regime: ensemble of three slow trend rules per ETF, evaluated on
    completed month-end closes only.
  - Signals: computed from completed daily bars only (an in-progress
    session bar is dropped), see fetchers/signals.py.
  - Event study: every past firing is entered at the NEXT session's open and
    measured against SPY over the same dates, net of a round-trip cost, and
    compared to the same stocks' return on any random day. That last
    comparison matters because the universe is TODAY's top holdings - stocks
    that got there by rising - so raw returns are biased upward.
"""
from __future__ import annotations

import json
import time
import warnings
from statistics import NormalDist
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf
from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay

from fetchers.cache_utils import is_fresh
from fetchers.indicators import sma
from fetchers.signals import SIGNALS, compute

BASE = Path(__file__).resolve().parent.parent
CACHE_PATH = BASE / "data" / "sector_signals.json"
HOLDINGS_CACHE_PATH = BASE / "data" / "sector_holdings.json"
# Committed snapshot, used per ETF only when both the live fetch and the
# local cache fail - CI starts with no cache, so without this one flaky
# funds_data call would blank the whole tab.
HOLDINGS_SEED_PATH = Path(__file__).resolve().parent / "sector_holdings_seed.json"
HOLDINGS_TTL = 7 * 24 * 3600

SECTOR_ETFS = {
    "XLB": "Materials", "XLC": "Communication Services", "XLE": "Energy",
    "XLF": "Financials", "XLI": "Industrials", "XLK": "Technology",
    "XLP": "Consumer Staples", "XLRE": "Real Estate", "XLU": "Utilities",
    "XLV": "Health Care", "XLY": "Consumer Discretionary",
}
# Second share class -> the one kept in the event study and RS ranking, so
# one company doesn't count twice. Both still show in the holdings table.
SHARE_CLASS_PRIMARY = {"GOOG": "GOOGL", "BRK-A": "BRK-B", "FOX": "FOXA", "NWS": "NWSA"}
BENCHMARK = "SPY"
HORIZONS = (5, 20, 60)
ROUND_TRIP_COST = 0.002
STUDY_YEARS = 5
RECENT_SESSIONS = 5
# Verdict rules. Each signal's result is compared with placebo runs of the
# same signal shifted to other dates (see _placebo), giving a p-value. 30
# tests run (10 signals x 3 horizons), so p of ~0.05 turns up by chance; the
# strong bar matches Harvey & Liu's t >= 3 for a new finding. Few firing
# dates, or firings bunched into a handful of overlapping windows, make any
# statistic unreliable, so those get no verdict at all.
STRONG_P = 0.0027  # two-sided, = |z| >= 3
HINT_P = 0.0455    # two-sided, = |z| >= 2
MIN_VERDICT_DATES = 30
MIN_INDEPENDENT_WINDOWS = 10
MIN_COVERAGE = 0.9  # skip the study if fewer than this share of stocks have a full year of data
MIN_COVERAGE_BARS = 260
# Placebo shifts closer than this many sessions (either direction) are
# skipped: a signal is built from its recent past, so a placebo landing on
# that same stretch isn't luck, it's the signal's own setup.
PLACEBO_EXCLUSION = {"trend_template": 260, "breakout_52w": 260}
PLACEBO_EXCLUSION_DEFAULT = 60
WARMUP_SESSIONS = 260  # no signal can fire before ~a year of history
MIN_STUDY_SESSIONS = 252  # skip the study if under a year of usable window remains
ET = ZoneInfo("America/New_York")
# Approximates the NYSE calendar for "is there another session this month?";
# a misjudged day (e.g. Good Friday) only delays the regime by one session.
_NEXT_SESSION = CustomBusinessDay(calendar=USFederalHolidayCalendar())


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _fetch_holdings() -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Top 10 holdings per ETF and where each list came from: "cached" (fetched
    within the TTL), "live", or a fallback after a failed fetch - "stale"
    (older cached list) or "seed" (committed snapshot). Each ETF falls back
    on its own, so one failed call only affects that sector. The cache keeps
    a fetch time per ETF so an old list is never re-labelled as fresh."""
    cache = _read_json(HOLDINGS_CACHE_PATH)
    now = time.time()
    fresh = {e: c["rows"] for e, c in cache.items()
             if isinstance(c, dict) and c.get("rows") and now - c.get("fetched_at", 0) < HOLDINGS_TTL}
    seed = _read_json(HOLDINGS_SEED_PATH)
    out, source = {}, {}
    for etf in SECTOR_ETFS:
        if etf in fresh:  # only refetch lists whose cache expired
            out[etf], source[etf] = fresh[etf], "cached"
            continue
        rows = []
        try:
            top = yf.Ticker(etf).funds_data.top_holdings
            for sym, row in top.head(10).iterrows():
                weight = float(row["Holding Percent"])
                rows.append({"symbol": str(sym).replace(".", "-"), "name": str(row["Name"]),
                             "weight": None if np.isnan(weight) else weight})
        except Exception:
            rows = []
        old = cache.get(etf)
        old_rows = old.get("rows") if isinstance(old, dict) else None
        for label, candidate in (("live", rows), ("stale", old_rows), ("seed", seed.get(etf))):
            if candidate:
                out[etf], source[etf] = candidate, label
                break
        else:
            out[etf], source[etf] = [], "missing"
        if source[etf] == "live":
            cache[etf] = {"rows": rows, "fetched_at": now}
    if any(src == "live" for src in source.values()):
        HOLDINGS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        HOLDINGS_CACHE_PATH.write_text(json.dumps(cache, indent=2))
    return out, source


def _download_once(tickers: list[str]) -> dict[str, pd.DataFrame]:
    raw = yf.download(tickers, period="7y", interval="1d", auto_adjust=True,
                      group_by="ticker", threads=True, progress=False)
    now_et = datetime.now(ET)
    frames = {}
    for t in tickers:
        if raw is None or t not in raw.columns.get_level_values(0):
            continue
        df = raw[t][["Open", "High", "Low", "Close", "Volume"]].dropna()
        # drop corrupt bars (zero/negative prices, high below low) - one of
        # these would otherwise poison every average it touches
        ohlc = df[["Open", "High", "Low", "Close"]]
        df = df[(ohlc > 0).all(axis=1) & (df["High"] >= df["Low"]) & np.isfinite(ohlc).all(axis=1)]
        df.index = pd.DatetimeIndex(df.index).tz_localize(None)
        # Drop today's bar while the session is still open - it isn't a
        # completed candle yet and every signal assumes completed bars.
        if len(df) and df.index[-1].date() == now_et.date() and (now_et.hour, now_et.minute) < (16, 15):
            df = df.iloc[:-1]
        if len(df) >= 2:  # short histories still show; their warm-up indicators stay blank
            frames[t] = df
    return frames


def _download(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """yfinance drops individual tickers under rate limiting without raising,
    so retry whatever came back missing once, after a short pause."""
    frames = _download_once(tickers)
    missing = [t for t in tickers if t not in frames]
    if missing:
        time.sleep(5)
        frames.update(_download_once(missing))
    return frames


def _regime(close: pd.Series) -> dict:
    """Score 0-3 from three slow trend rules, all read at the last COMPLETED
    month-end: close above its 10-month SMA, close above its 200-day SMA,
    and a positive 12-month return. The in-progress month is ignored."""
    last = close.index[-1]
    month_done = (last + _NEXT_SESSION).month != last.month
    cutoff = last if month_done else last.replace(day=1) - pd.Timedelta(days=1)
    daily = close[close.index <= cutoff]
    monthly = daily.resample("ME").last().dropna()
    if len(monthly) < 13 or len(daily) < 200:
        return {"score": None}
    above_10m = bool(monthly.iloc[-1] > monthly.rolling(10).mean().iloc[-1])
    above_200d = bool(daily.iloc[-1] > sma(daily, 200).iloc[-1])
    ret_12m = float(monthly.iloc[-1] / monthly.iloc[-13] - 1)
    return {
        "score": int(above_10m) + int(above_200d) + int(ret_12m > 0),
        "above_10m": above_10m, "above_200d": above_200d,
        "ret_1m": float(monthly.iloc[-1] / monthly.iloc[-2] - 1), "ret_12m": ret_12m,
        "as_of": daily.index[-1].strftime("%Y-%m-%d"),
    }


def _forward_excess(df: pd.DataFrame, spy: pd.DataFrame, h: int) -> pd.Series:
    """For a signal on date d: buy at the next session's open, sell at the
    close h sessions after d, minus SPY over the identical dates. Computed on
    SPY's calendar so a session the stock is missing gives NaN instead of
    silently stretching its window. Indexed like df (signal date)."""
    on_cal = df.reindex(spy.index)
    stock = on_cal["Close"].shift(-h) / on_cal["Open"].shift(-1) - 1
    bench = spy["Close"].shift(-h) / spy["Open"].shift(-1) - 1
    return (stock - bench).reindex(df.index)


def _by_date(pos: np.ndarray, vals: np.ndarray, n_days: int) -> tuple[np.ndarray, np.ndarray]:
    """Average values that share a trading day. Correlated stocks firing on
    the same day aren't independent bets, so each day counts once."""
    counts = np.bincount(pos, minlength=n_days)
    sums = np.bincount(pos, weights=vals, minlength=n_days)
    days = np.nonzero(counts)[0]
    return days, sums[days] / counts[days]


def _hac_t(days: np.ndarray, means: np.ndarray, n_days: int, lag: int) -> float | None:
    """t-stat of the mean of per-day values, with a Bartlett-kernel
    (Newey-West style) variance over trading-day distance: days within `lag`
    sessions share part of their holding window, so they're not independent.
    Autocovariances via FFT, so the placebo can afford to run every shift."""
    n = len(means)
    if n <= 2:
        return None
    resid = np.zeros(n_days)
    resid[days] = means - means.mean()
    size = 1 << int(np.ceil(np.log2(2 * n_days)))
    spec = np.fft.rfft(resid, size)
    autocov = np.fft.irfft(spec * np.conj(spec), size)[:min(lag, n_days - 1) + 1]
    k = np.arange(len(autocov))
    weights = 1 - k / (lag + 1)
    var = float(weights[0] * autocov[0] + 2 * (weights[1:] @ autocov[1:])) / n**2
    return float(means.mean() / np.sqrt(var)) if var > 0 else None


def _independent_windows(pos: np.ndarray, h: int) -> int:
    """How many firing days are at least h sessions apart (greedy), i.e. how
    many non-overlapping bets the sample really holds."""
    count, last = 0, None
    for p in np.unique(pos):
        if last is None or p - last >= h:
            count, last = count + 1, p
    return count


def _verdict(p: float | None, score: float | None, edge: float | None, n_dates: int, windows: int) -> str:
    """p: two-sided placebo p-value of the timing edge; score: the same as a
    signed z-score; edge: placebo-centred edge before costs."""
    if p is None or score is None or n_dates < MIN_VERDICT_DATES or windows < MIN_INDEPENDENT_WINDOWS:
        return "too_few"
    if p <= STRONG_P:
        if score < 0:
            return "reverses"
        return "works" if edge is not None and edge > ROUND_TRIP_COST else "too_small"
    if p <= HINT_P:
        return "hint_works" if score > 0 else "hint_backwards"
    return "none"


def _placebo(stock_idx, pos, abnormal, start, n_days, lag, exclusion):
    """Raw t-stats and mean edges of the signal re-run at every allowed
    shift: all firing days move together by s sessions, wrapping around the
    study window. Moving them together keeps the real bunching of firings
    (within a stock's hot and cold stretches, and across stocks), which the
    Newey-West correction alone underestimates; only the link to the actual
    dates is broken. Shifts within `exclusion` sessions either way are
    skipped (see PLACEBO_EXCLUSION)."""
    width = n_days - start
    shifts = [d for d in range(1, width) if min(d, width - d) >= exclusion]
    ts, edges = [], []
    for d in shifts:
        shifted = start + (pos - start + d) % width
        vals = abnormal[stock_idx, shifted]
        keep = np.isfinite(vals)
        if keep.sum() <= 2:
            continue
        days, means = _by_date(shifted[keep], vals[keep], n_days)
        t = _hac_t(days, means, n_days, lag)
        if t is not None:
            ts.append(t)
            edges.append(means.mean())
    return np.asarray(ts), np.asarray(edges)


def _signal_stats(stock_idx, pos, direction, excess, abnormal, start, n_days, h, exclusion) -> dict | None:
    """One signal at one horizon. excess[i, d] is stock i's forward return
    minus SPY's for a signal on calendar day d; abnormal additionally has the
    same-day average of all stocks and stock i's own average removed.
    net = excess in the signal's direction after costs, averaged per day.
    edge = abnormal in the signal's direction (before costs), centred on the
    placebo, so 0 = no better than the same signal at random other dates.
    p = share of placebo runs at least as extreme as the real one."""
    ex = excess[stock_idx, pos]
    keep = np.isfinite(ex)
    stock_idx, pos, ex = stock_idx[keep], pos[keep], ex[keep]
    if len(pos) == 0:
        return None
    net_vals = direction * ex - ROUND_TRIP_COST
    days, net_means = _by_date(pos, net_vals, n_days)
    abn = direction * abnormal[stock_idx, pos]
    ok = np.isfinite(abn)
    abn_days, abn_means = _by_date(pos[ok], abn[ok], n_days)
    raw_t = _hac_t(abn_days, abn_means, n_days, h - 1)
    placebo_t, placebo_edge = _placebo(stock_idx[ok], pos[ok], direction * abnormal, start, n_days, h - 1, exclusion)
    p = score = edge = None
    if raw_t is not None and len(placebo_t) >= 100:
        center = float(np.median(placebo_t))
        extreme = np.abs(placebo_t - center) >= abs(raw_t - center)
        p = float((extreme.sum() + 1) / (len(placebo_t) + 1))
        score = float(np.copysign(NormalDist().inv_cdf(1 - p / 2), raw_t - center))
        edge = float(abn_means.mean() - np.median(placebo_edge))
    windows = _independent_windows(pos, h)
    net = float(net_means.mean())
    return {
        "n": int(len(pos)), "n_dates": int(len(days)), "windows": windows, "net": net,
        "hit_rate": float((net_vals > 0).mean()), "edge": edge, "score": score, "p": p,
        "placebo_runs": int(len(placebo_t)), "verdict": _verdict(p, score, edge, len(days), windows),
    }


def _event_study(stocks, frames, spy, events_by_stock, study_start) -> dict | None:
    """Everything here lives on SPY's trading calendar: arrays are
    [stock, calendar day], and a day the stock is missing is NaN."""
    if not stocks:
        return None
    cal = spy.index
    n_days = len(cal)
    # Signals need ~a year of history (52-week high/low, 200-day average)
    # before they can fire, so the window can't start earlier than that.
    start = max(int(cal.searchsorted(study_start)), WARMUP_SESSIONS)
    if n_days - start < MIN_STUDY_SESSIONS:
        return None  # caller reports why
    event_pos = {}
    for i, sym in enumerate(stocks):
        on_cal = cal.get_indexer(frames[sym].index)
        for sig in SIGNALS:
            pts = on_cal[events_by_stock[sym][sig.key]] if events_by_stock[sym][sig.key] else np.array([], int)
            event_pos[(i, sig.key)] = pts[pts >= start]

    rows = {sig.key: {} for sig in SIGNALS}
    baseline = {}
    for h in HORIZONS:
        excess = np.full((len(stocks), n_days), np.nan)
        for i, sym in enumerate(stocks):
            excess[i] = _forward_excess(frames[sym], spy, h).reindex(cal).to_numpy()
        excess[:, :start] = np.nan
        excess[~np.isfinite(excess)] = np.nan
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            day_avg = np.nanmean(excess, axis=0)
            stock_avg = np.nanmean(excess - day_avg, axis=1)
        abnormal = excess - day_avg - stock_avg[:, None]
        valid = ~np.isnan(excess)
        bull_days, bull_means = _by_date(np.nonzero(valid)[1], excess[valid] - ROUND_TRIP_COST, n_days)
        bear_days, bear_means = _by_date(np.nonzero(valid)[1], -excess[valid] - ROUND_TRIP_COST, n_days)
        baseline[str(h)] = {
            "bull": {"net": float(bull_means.mean()), "n": int(valid.sum())},
            "bear": {"net": float(bear_means.mean()), "n": int(valid.sum())},
        }
        for sig in SIGNALS:
            idx = [(i, p) for i in range(len(stocks)) for p in event_pos[(i, sig.key)]]
            stock_idx = np.array([i for i, _ in idx], dtype=int)
            pos = np.array([p for _, p in idx], dtype=int)
            exclusion = max(h, PLACEBO_EXCLUSION.get(sig.key, PLACEBO_EXCLUSION_DEFAULT))
            rows[sig.key][str(h)] = _signal_stats(stock_idx, pos, sig.direction, excess, abnormal,
                                                  start, n_days, h, exclusion)

    study_rows = [{"key": sig.key, "label": sig.label, "short": sig.short, "plain": sig.plain,
                   "direction": sig.direction, "group": sig.group, "definition": sig.definition,
                   "horizons": rows[sig.key]} for sig in SIGNALS]

    def found(*verdicts):
        return [{"label": r["label"], "horizons": [h for h in HORIZONS
                 if r["horizons"][str(h)] and r["horizons"][str(h)]["verdict"] in verdicts]}
                for r in study_rows
                if any(st and st["verdict"] in verdicts for st in r["horizons"].values())]

    return {
        "start": cal[start].strftime("%Y-%m-%d"), "end": cal[-1].strftime("%Y-%m-%d"),
        "years": round((cal[-1] - cal[start]).days / 365.25, 1),
        "cost": ROUND_TRIP_COST, "horizons": list(HORIZONS), "n_stocks": len(stocks),
        "strong_p": STRONG_P, "hint_p": HINT_P,
        "min_dates": MIN_VERDICT_DATES, "min_windows": MIN_INDEPENDENT_WINDOWS,
        "baseline": baseline, "rows": study_rows,
        "summary": {"n_signals": len(SIGNALS), "works": found("works"), "reverses": found("reverses"),
                    "too_small": found("too_small"), "hint_works": found("hint_works"),
                    "hint_backwards": found("hint_backwards")},
    }


def fetch():
    holdings, holdings_source = _fetch_holdings()
    stocks = sorted({h["symbol"] for rows in holdings.values() for h in rows})
    frames = _download(stocks + list(SECTOR_ETFS) + [BENCHMARK])
    if BENCHMARK not in frames:
        raise RuntimeError("no SPY history - can't compute anything")
    spy = frames[BENCHMARK]
    requested = len(stocks)
    stocks = [s for s in stocks if s in frames]
    full_year = [s for s in stocks if len(frames[s]) >= MIN_COVERAGE_BARS]
    coverage = len(full_year) / requested if requested else 0.0
    study_universe = [s for s in stocks if SHARE_CLASS_PRIMARY.get(s) not in stocks]

    # Minervini's relative-strength condition: 12-month return percentile
    # across this universe, ranked per date among stocks with data that day.
    # A second share class borrows its primary's rank.
    ret_12m = pd.DataFrame({s: frames[s]["Close"] / frames[s]["Close"].shift(252) - 1 for s in study_universe})
    rs_pct = ret_12m.rank(axis=1, pct=True)

    last_date = spy.index[-1]
    recent_cutoff = spy.index[-RECENT_SESSIONS]  # signals on or after this date count as recent
    events_by_stock, per_stock = {}, {}
    for sym in stocks:
        df = frames[sym]
        rs_col = sym if sym in rs_pct else SHARE_CLASS_PRIMARY[sym]
        feat, events = compute(df, rs_pct[rs_col])
        events_by_stock[sym] = events
        last = feat.iloc[-1]
        close = df["Close"]
        n = len(df)
        stop = None if pd.isna(last["atr14"]) else float(close.iloc[-1] - 10 * last["atr14"])
        per_stock[sym] = {
            # set when this stock's data ends before the market's last session
            "stale_since": None if df.index[-1] >= last_date else df.index[-1].strftime("%Y-%m-%d"),
            "close": float(close.iloc[-1]),
            "chg_pct": float(close.iloc[-1] / close.iloc[-2] - 1),
            "short_history": n < 252,
            "trend_template": bool(last["trend_template"]),
            "rs_pct": None if pd.isna(last["rs_pct"]) else float(last["rs_pct"]),
            "pct_from_high": None if pd.isna(last["high_52w"]) else float(close.iloc[-1] / last["high_52w"] - 1),
            "stop_10atr": stop if stop is not None and stop > 0 else None,
            "recent": [
                {"key": key, "date": df.index[t].strftime("%Y-%m-%d")}
                for key, idx in events.items() for t in idx if df.index[t] >= recent_cutoff
            ],
        }

    # A heavily rate-limited download would quietly shrink the universe and
    # reshuffle every RS rank, so below MIN_COVERAGE the study is skipped
    # (and says why) rather than published on a partial set.
    study, study_skipped = None, None
    if coverage >= MIN_COVERAGE:
        study = _event_study(study_universe, frames, spy, events_by_stock,
                             last_date - pd.DateOffset(years=STUDY_YEARS))
        if study is None:
            study_skipped = "not enough price history for a study window of at least a year"
    else:
        study_skipped = f"only {len(full_year)} of {requested} stocks had a full year of price data"

    regimes = {etf: _regime(frames[etf]["Close"]) for etf in SECTOR_ETFS if etf in frames}
    regimes[BENCHMARK] = _regime(spy["Close"])

    sectors = []
    for etf, name in SECTOR_ETFS.items():
        rows = []
        for hold in holdings.get(etf, []):
            info = per_stock.get(hold["symbol"])
            rows.append({**hold, **(info or {"missing": True})})
        sectors.append({"etf": etf, "name": name, "regime": regimes.get(etf, {"score": None}),
                        "holdings_source": holdings_source.get(etf, "missing"), "stocks": rows})

    result = {
        "as_of": int(time.time()),
        "last_bar": last_date.strftime("%Y-%m-%d"),
        "benchmark_regime": regimes[BENCHMARK],
        "sectors": sectors,
        "signals": [asdict(s) for s in SIGNALS],
        "study": study,
        "study_skipped": study_skipped,
        "coverage": coverage,
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    data = fetch()
    s = data["study"]
    print(f"last bar {data['last_bar']}")
    if s:
        print(f"study {s['start']}..{s['end']}  companies {s['n_stocks']}  summary {s['summary']}")
        for row in s["rows"]:
            parts = []
            for h in s["horizons"]:
                st = row["horizons"][str(h)]
                if not st:
                    parts.append("-")
                    continue
                z = "-" if st["score"] is None else f"{st['score']:+.2f}"
                e = "-" if st["edge"] is None else f"{st['edge']:+.4f}"
                parts.append(f"{h}d n={st['n']} w={st['windows']} runs={st['placebo_runs']} net={st['net']:+.4f} edge={e} z={z} p={st['p']} {st['verdict']}")
            print(f"{row['label']:<32} {' | '.join(parts)}")
    for sec in data["sectors"]:
        print(sec["etf"], sec["regime"].get("score"), sec["holdings_source"])
