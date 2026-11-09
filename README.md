# Market Dashboard

A dashboard combining Fear & Greed, VIX, an S&P 500 heatmap, CME FedWatch
FOMC probabilities, the PPI/CPI economic calendar, and the week's earnings
calendar.

**Live site:** https://areax.github.io/market-dashboard/ — rebuilt
automatically once a day after the US close (see below).

Tabs: Overview (this market snapshot), Sectors, Stock lookup (price charts,
signal history and the daily S&P 500 signal scan) and Signal combos. The
same pages also run inside the Contagious Wealth bot's local server.

## Run it locally

```bash
./run.sh
```

Pulls fresh data (skipping any source whose cache is still fresh) and opens
the dashboard at http://localhost:5050. You can also click **Refresh Data**
in the page itself instead of restarting the script.

## What's on it, and where the data comes from

| Section | Source | Notes |
|---|---|---|
| Fear & Greed Index | CNN's public JSON feed | Same data CNN's own page uses |
| VIX + S&P 500 | Yahoo Finance (`yfinance`) | 6 months of history |
| S&P 500 heatmap | Yahoo Finance, built locally | Finviz's map can't be embedded (`X-Frame-Options`) and its ToS forbids scraping, so this rebuilds the same idea (sized by market cap, colored by day change) from free data. A link to the live Finviz map is included. |
| CME FedWatch (self-computed) | Yahoo Finance (30-Day Fed Funds futures, `ZQ`) + FRED | CME's own tool is a licensed third-party widget that blocks automated access; its official API is paid. This reproduces CME's published "Aggregated" methodology from the same underlying futures prices CME uses, reverse-engineered and validated against a real FedWatch screenshot — exact on about half the meetings, within a few points on the rest. Labeled as an estimate on the card, with links to both the raw data (Yahoo) and the real tool (CME) for cross-checking. |
| PPI / CPI + economic calendar | ForexFactory's public calendar feed (schedule/forecast) + FRED (actual CPI/PPI m/m, official Fed data) | MarketWatch's calendar sits behind an active CAPTCHA challenge for automated browsers, so it's not used. |
| Earnings calendar | Yahoo Finance | Top ~60 companies by market cap reporting that week, toggle between next week and last week, actual report date shown per company |
| Briefing.com | Briefing.com homepage | Only the 2 free "Latest Comments" headlines are public; the rest is subscriber-only |

## Sectors tab

`sectors.html` covers the 11 SPDR sector ETFs and each one's top 10 holdings
(Yahoo Finance fund data, refreshed weekly). Built in `fetchers/indicators.py`
(math), `fetchers/signals.py` (signal detection) and
`fetchers/sector_signals.py` (data, regime, event study).

- **Regime:** per ETF, how many of three slow trend rules are on at the last
  completed month-end: close above its 10-month SMA, close above its 200-day
  SMA, positive 12-month return. This is the best-supported use of indicators
  in the research: it historically cut drawdowns, not raised returns.
- **Signals**, in two groups:
  - *Research-style:* Minervini trend-template entry and 52-week-high
    breakout on 2x volume. The broad ideas (trend following, stocks near
    their 52-week high) have published evidence; these exact versions come
    from practitioners.
  - *Your signals:* chart-pattern/candle rules with no solid published
    evidence: double top/bottom, head & shoulders and inverse, first red
    candle above the 21 EMA, and green candle below it.
  - Plain-English summaries and exact definitions are on the page.
- **Price gaps (awareness, not signals):** two same-colour candles in a row
  with a gap of at least 1% between them. The Stock lookup chart rings them
  and shades the gap until price comes back to fill it, and lists the open
  ones. The page shows how often past gaps filled within a month and a year.
- **Event study:** every firing over the last ~5 years (after a one-year
  warm-up), entered at the next session's open, held 5/20/60 sessions,
  measured vs SPY after a 0.2% round-trip cost ("net"). "Edge" removes what
  all these stocks did on the same days and each stock's own average, then
  is centred on placebo runs, so 0 = no better than luck.
  - **Placebo calibration:** every signal is re-run with all of its firing
    dates shifted together by each allowed number of sessions (wrapping
    around the window). This keeps the real bunching of firings, which a
    plain Newey-West t-stat underestimates. Shifts close to the signal's own
    setup period are skipped (60 sessions; 260 for the trend template and
    breakout). Otherwise a placebo can land on the very run-up the signal is
    built from, which made earlier versions blind to real edges.
  - **Verdicts:** the placebo p-value sets the verdict. "Real edge" needs
    p <= 0.0027 (|z| >= 3, Harvey & Liu's bar) and an edge above costs.
    "Weak hint" means p <= 0.05; with 30 tests, expect those by chance. A
    verdict also needs >= 30 firing days over >= 10 separate periods.
  - **Universe bias:** the universe is today's top holdings, so raw returns
    carry survivorship bias; the per-stock adjustment handles most of it.
  - **Share classes:** GOOG is left out next to GOOGL.

Correctness rules the code follows:
- Signals use completed daily bars only (an in-progress session bar is
  dropped).
- Corrupt bars (non-positive prices, high below low) are dropped.
- Chart-pattern swing points count only after 5 confirming bars.
- Indicator warm-up stays NaN rather than backfilled.
- EMA/ATR follow TA-Lib's seeding conventions.

`tests/test_sector_signals.py` checks these offline (look-ahead truncation,
indicator references, placebo calibration on a null world and a planted
edge, regime timing, holdings fallbacks, download coverage, page render).
CI runs it before every deploy: `python -m unittest discover -s tests -t .`

## GitHub Pages deployment

`.github/workflows/update-site.yml` runs once per weekday and can also be
run manually. It refreshes every data source, renders `build_static.py`'s
static `dist/index.html`, and deploys it via GitHub Pages.

The cron is 22:00 UTC on weekdays: 5pm EST / 6pm EDT, after the US close,
so the Sectors, Stock lookup and Signal combos pages always hold the
day's final bar. GitHub's free scheduler often delivers scheduled runs
several hours late (4.5 to 8 hours observed); a late run still picks up
the same close, so every delivered run completes regardless of clock time.

To trigger a rebuild manually: **Actions → Update dashboard and deploy to
GitHub Pages → Run workflow** on GitHub, or `gh workflow run update-site.yml`.

## Notes

- First run installs a Chromium browser for Playwright (already done) and
  downloads ~500 tickers' worth of data for the heatmap — expect the very
  first `./run.sh` to take a minute or two. After that, caching makes reruns
  fast unless the cache has expired (15 min for prices/Fear&Greed, 6 hours
  for the two calendars).
- Yahoo's free data feed (`yfinance`) rate-limits aggressively if you refresh
  too often — if a refresh fails, wait a few minutes and try again.
- `data/` holds the cached JSON each fetcher writes; delete a file (or run
  `python refresh_all.py --force`) to force a refetch of everything.
