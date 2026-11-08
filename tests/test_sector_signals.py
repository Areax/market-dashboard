"""Offline checks for the Sectors tab: no look-ahead, indicator math, event
study statistics, regime timing, failure handling, and page rendering.
Synthetic prices and mocked downloads only, so it runs without network.

Run: python -m unittest discover -s tests -t .
"""
import json
import math
import tempfile
import time
import zlib
from datetime import datetime
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from fetchers import sector_signals as ss
from fetchers.indicators import atr, ema
from fetchers.signals import SIGNALS, compute


def synthetic_ohlcv(n=1500, seed=7, drift=0.0004):
    """Random walk with volatility clustering, overnight gaps and volume
    spikes, so gap, pattern and breakout signals all actually fire."""
    rng = np.random.default_rng(seed)
    vol = 0.01 + 0.01 * (np.sin(np.arange(n) / 60) ** 2)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol)))
    gaps = rng.choice([0, 0.03, -0.03], size=n, p=[0.94, 0.03, 0.03])
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1] * (1 + gaps[1:] + rng.normal(0, 0.002, n - 1))
    span = np.abs(rng.normal(0, vol)) * close
    high = np.maximum(open_, close) + span
    low = np.minimum(open_, close) - span
    volume = rng.lognormal(15, 0.5, n) * np.where(rng.random(n) < 0.08, 3.0, 1.0)
    idx = pd.bdate_range("2019-01-01", periods=n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)


class NoLookAhead(unittest.TestCase):
    def test_truncated_history_gives_identical_past(self):
        df = synthetic_ohlcv(seed=9, drift=0.0015)
        # a cross-sectional rank that genuinely varies over time
        rs = pd.Series(np.clip(0.5 + 0.4 * np.sin(np.arange(len(df)) / 90), 0, 1), index=df.index)
        full_feat, full_events = compute(df, rs)
        fired = {k for k, v in full_events.items() if v}
        self.assertIn("breakout_52w", fired)
        self.assertIn("trend_template", fired)
        self.assertGreaterEqual(len(fired), 8)
        for cut in (400, 700, 1000, 1300, 1499):
            feat, events = compute(df.iloc[:cut], rs.iloc[:cut])
            for key in full_events:
                self.assertEqual([t for t in full_events[key] if t < cut], events[key], f"{key} at cut {cut}")
            pd.testing.assert_frame_equal(full_feat.iloc[:cut], feat, check_freq=False)


class IndicatorMath(unittest.TestCase):
    """Independent references: pandas' ewm seeded with the first-n SMA (a
    different code path from the loop under test), and Wilder's recursion."""

    def setUp(self):
        self.df = synthetic_ohlcv(400)

    def test_ema_matches_talib_convention(self):
        c = self.df["Close"]
        n = 21
        seeded = c.copy()
        seeded.iloc[n - 1] = c.iloc[:n].mean()
        want = seeded.iloc[n - 1:].ewm(span=n, adjust=False).mean()
        got = ema(c, n)
        np.testing.assert_allclose(got.iloc[n - 1:].to_numpy(), want.to_numpy(), rtol=1e-12)
        self.assertTrue(got.iloc[:n - 1].isna().all())

    def test_atr_matches_wilder(self):
        h, l, c = (self.df[k] for k in ("High", "Low", "Close"))
        n = 14
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1).iloc[1:]
        seeded = tr.copy()
        seeded.iloc[n - 1] = tr.iloc[:n].mean()
        want = seeded.iloc[n - 1:].ewm(alpha=1 / n, adjust=False).mean()
        got = atr(h, l, c, n)
        np.testing.assert_allclose(got.iloc[n:].to_numpy(), want.to_numpy(), rtol=1e-12)
        self.assertTrue(got.iloc[:n].isna().all())

    def test_ema_rejects_gaps(self):
        c = self.df["Close"].copy()
        c.iloc[50] = np.nan
        with self.assertRaises(ValueError):
            ema(c, 21)


class EventStudyStats(unittest.TestCase):
    def test_forward_excess_entry_exit_and_missing_day(self):
        spy = synthetic_ohlcv(100, seed=1)
        stock = synthetic_ohlcv(100, seed=2)
        ex = ss._forward_excess(stock, spy, 5)
        want = (stock["Close"].iloc[15] / stock["Open"].iloc[11] - 1) - (spy["Close"].iloc[15] / spy["Open"].iloc[11] - 1)
        self.assertAlmostEqual(ex.iloc[10], want, places=12)
        gapped = stock.drop(stock.index[13])  # a session the stock is missing must not stretch its window
        self.assertTrue(np.isnan(ss._forward_excess(gapped, spy, 1).loc[stock.index[12]]))

    def test_hac_t_matches_brute_force(self):
        rng = np.random.default_rng(3)
        days = np.sort(rng.choice(300, 80, replace=False))
        means = rng.normal(0.002, 0.02, 80)
        t = ss._hac_t(days, means, 300, lag=19)
        e = means - means.mean()
        var = sum(e[i] * e[j] * max(0, 1 - abs(days[i] - days[j]) / 20) for i in range(80) for j in range(80)) / 80**2
        self.assertAlmostEqual(t, means.mean() / math.sqrt(var), places=9)

    def test_by_date_averages_same_day(self):
        days, means = ss._by_date(np.array([3, 3, 7]), np.array([1.0, 3.0, 5.0]), 10)
        np.testing.assert_array_equal(days, [3, 7])
        np.testing.assert_allclose(means, [2.0, 5.0])

    def test_independent_windows_greedy(self):
        self.assertEqual(ss._independent_windows(np.array([0, 3, 25, 26, 60, 199]), 20), 4)

    def test_signal_stats_bear_sign_cost_and_adjustment(self):
        n_days, h = 400, 5
        excess = np.full((2, n_days), 0.01)  # both stocks beat SPY by 1% every window
        abnormal = np.random.default_rng(5).normal(0, 0.002, (2, n_days))
        abnormal[0, 100:300:10] -= 0.02      # ...but stock 0 does 2% worse than usual on these days
        pos = np.arange(100, 300, 10)
        st = ss._signal_stats(np.zeros(len(pos), int), pos, -1, excess, abnormal, 0, n_days, h, 60)
        self.assertAlmostEqual(st["net"], -0.01 - ss.ROUND_TRIP_COST)  # shorting a 1% gain, minus cost
        self.assertEqual(st["n"], len(pos))
        self.assertEqual(st["n_dates"], len(pos))
        self.assertAlmostEqual(st["hit_rate"], 0.0)
        # edge is centred on the placebo: shifted runs mostly miss the -2% days, so it lands near +2%
        self.assertGreater(st["edge"], 0.015)

    def test_verdict_guards(self):
        v = ss._verdict
        self.assertEqual(v(0.001, 3.3, 0.01, ss.MIN_VERDICT_DATES - 1, 100), "too_few")
        self.assertEqual(v(0.001, 3.3, 0.01, 100, ss.MIN_INDEPENDENT_WINDOWS - 1), "too_few")
        self.assertEqual(v(None, None, None, 100, 100), "too_few")
        self.assertEqual(v(0.001, 3.3, 0.01, 100, 100), "works")
        self.assertEqual(v(0.001, 3.3, 0.001, 100, 100), "too_small")  # real but under the 0.2% cost
        self.assertEqual(v(0.001, -3.3, 0.01, 100, 100), "reverses")
        self.assertEqual(v(0.03, 2.2, 0.01, 100, 100), "hint_works")
        self.assertEqual(v(0.03, -2.2, 0.01, 100, 100), "hint_backwards")
        self.assertEqual(v(0.3, 1.0, 0.01, 100, 100), "none")

    @staticmethod
    def _null_world(seed, n_stocks=30, n_days=1500, h=5):
        """A world with no edge to find: daily returns with volatility
        clustering but no predictable drift, and a signal that fires after a
        stock's trailing 10-day run-up is in its top 5% - built from its
        recent past like any real signal. Forward h-day returns overlap each
        other but are independent of the run-up, so the only way to 'find'
        an edge is a placebo landing on the run-up itself."""
        rng = np.random.default_rng(seed)
        vol = 0.01 * np.exp(0.5 * np.sin(np.arange(n_days)[None, :] / 40 + rng.uniform(0, 6, (n_stocks, 1))))
        daily = rng.normal(0, 1, (n_stocks, n_days)) * vol
        cum = np.cumsum(daily, axis=1)
        forward = np.full((n_stocks, n_days), np.nan)
        forward[:, :-h] = cum[:, h:] - cum[:, :-h]
        abnormal = forward - np.nanmean(forward, axis=1, keepdims=True)
        trailing = pd.DataFrame(daily.T).rolling(10).sum().to_numpy().T
        stock_idx, pos = np.nonzero(trailing > np.nanpercentile(trailing, 95, axis=1, keepdims=True))
        keep = pos < n_days - h
        return abnormal, stock_idx[keep], pos[keep]

    def test_placebo_null_past_conditioned_signal(self):
        """No edge planted: past-conditioning and bunched firings must not
        produce a strong verdict."""
        strong = 0
        for seed in range(6):
            abnormal, si, pos = self._null_world(seed)
            st = ss._signal_stats(si, pos, 1, abnormal + 0.001, abnormal, 0, abnormal.shape[1], 5, 60)
            strong += st["verdict"] in ("works", "too_small", "reverses")
        self.assertEqual(strong, 0)

    def test_placebo_detects_planted_edge(self):
        """Same world with +1% added to the real firings' forward returns:
        the calibrated test must find it (guards against an over-strict placebo)."""
        abnormal, si, pos = self._null_world(0)
        planted = abnormal.copy()
        planted[si, pos] += 0.01
        st = ss._signal_stats(si, pos, 1, planted + 0.001, planted, 0, planted.shape[1], 5, 60)
        self.assertEqual(st["verdict"], "works")


class Regime(unittest.TestCase):
    def test_uses_last_completed_month(self):
        idx = pd.bdate_range("2023-01-02", "2026-10-30")
        close = pd.Series(np.linspace(100, 200, len(idx)), index=idx)
        mid_month = close[close.index <= "2026-10-15"]
        self.assertEqual(ss._regime(mid_month)["as_of"], "2026-09-30")  # mid-October: September is the last full month
        self.assertEqual(ss._regime(close)["as_of"], "2026-10-30")      # Fri Oct 30 is October's last session
        self.assertEqual(ss._regime(close)["score"], 3)


class FetchWithMockedData(unittest.TestCase):
    """End to end: holdings fetch failing (seed fallback), download coverage,
    and the rendered page."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        seed = json.loads(ss.HOLDINGS_SEED_PATH.read_text())
        self.tickers = sorted({r["symbol"] for rows in seed.values() for r in rows})
        self.patches = [
            mock.patch.object(ss, "CACHE_PATH", self.tmp / "sector_signals.json"),
            mock.patch.object(ss, "HOLDINGS_CACHE_PATH", self.tmp / "holdings.json"),
            mock.patch.object(ss.yf, "Ticker", side_effect=RuntimeError("no network in tests")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _fake_download(self, keep_share=1.0):
        def download(tickers):
            keep = tickers if keep_share >= 1 else [t for i, t in enumerate(tickers)
                                                    if i % 2 == 0 or t in ss.SECTOR_ETFS or t == ss.BENCHMARK]
            return {t: synthetic_ohlcv(1500, seed=zlib.crc32(t.encode()) % 10_000) for t in keep}
        return download

    def test_seed_fallback_full_study_and_render(self):
        with mock.patch.object(ss, "_download", self._fake_download()):
            data = ss.fetch()
        self.assertTrue(all(sec["holdings_source"] == "seed" for sec in data["sectors"]))
        self.assertIsNotNone(data["study"])
        self.assertTrue(all(s["plain"] for s in data["signals"]))

        from app import app
        with app.app_context():
            html = app.jinja_env.get_template("sectors.html").render(data=data, is_static=True)
        self.assertNotIn("{PATTERN", html)
        self.assertNotIn("{PIVOT", html)
        for sig in SIGNALS:
            self.assertIn(sig.plain.replace("&", "&amp;"), html)

    def test_low_coverage_skips_study_but_keeps_page(self):
        with mock.patch.object(ss, "_download", self._fake_download(keep_share=0.5)):
            data = ss.fetch()
        self.assertIsNone(data["study"])
        self.assertIn("full year of price data", data["study_skipped"])
        self.assertTrue(data["sectors"])


class DataHygiene(unittest.TestCase):
    def _raw(self, df, ticker="AAA"):
        df = df.copy()
        df.columns = pd.MultiIndex.from_product([[ticker], df.columns])
        return df

    def test_corrupt_bars_dropped(self):
        df = synthetic_ohlcv(50)
        df.iloc[10, df.columns.get_loc("Open")] = 0.0
        df.iloc[20, df.columns.get_loc("Low")] = df.iloc[20]["High"] + 1
        with mock.patch.object(ss.yf, "download", return_value=self._raw(df)):
            out = ss._download_once(["AAA"])["AAA"]
        self.assertEqual(len(out), 48)
        self.assertNotIn(df.index[10], out.index)
        self.assertNotIn(df.index[20], out.index)

    def test_in_progress_session_dropped(self):
        df = synthetic_ohlcv(50)
        last = df.index[-1]

        class Clock(datetime):
            hour_minute = (11, 0)

            @classmethod
            def now(cls, tz=None):
                return datetime(last.year, last.month, last.day, *cls.hour_minute, tzinfo=tz)

        with mock.patch.object(ss.yf, "download", return_value=self._raw(df)), mock.patch.object(ss, "datetime", Clock):
            self.assertEqual(ss._download_once(["AAA"])["AAA"].index[-1], df.index[-2])  # 11am: still open
            Clock.hour_minute = (17, 0)
            self.assertEqual(ss._download_once(["AAA"])["AAA"].index[-1], last)  # after the close


class HoldingsSources(unittest.TestCase):
    def setUp(self):
        self.cache = Path(tempfile.mkdtemp()) / "holdings.json"
        self.p = [mock.patch.object(ss, "HOLDINGS_CACHE_PATH", self.cache),
                  mock.patch.object(ss.yf, "Ticker", side_effect=RuntimeError("offline"))]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def _write(self, age_seconds):
        rows = [{"symbol": "X", "name": "X", "weight": 0.1}]
        self.cache.write_text(json.dumps({e: {"rows": rows, "fetched_at": time.time() - age_seconds}
                                          for e in ss.SECTOR_ETFS}))

    def test_labels(self):
        self.assertEqual(set(ss._fetch_holdings()[1].values()), {"seed"})    # no cache, fetch fails
        self._write(60)
        self.assertEqual(set(ss._fetch_holdings()[1].values()), {"cached"})  # fresh cache, no fetch
        self._write(ss.HOLDINGS_TTL + 60)
        self.assertEqual(set(ss._fetch_holdings()[1].values()), {"stale"})   # expired, fetch fails


if __name__ == "__main__":
    unittest.main()
