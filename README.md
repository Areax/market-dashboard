# Market Dashboard

A dashboard combining Fear & Greed, VIX, an S&P 500 heatmap, CME FedWatch
FOMC probabilities, the PPI/CPI economic calendar, and the week's earnings
calendar.

**Live site:** https://areax.github.io/market-dashboard/ — rebuilt
automatically on weekday mornings (see below).

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

## GitHub Pages deployment

`.github/workflows/update-site.yml` runs on weekday mornings (~8:40am ET —
after the 8:30am economic-data releases post, well before the 9:30am market
open) and on manual trigger. It refreshes every data source, renders
`build_static.py`'s static `dist/index.html`, and deploys it via GitHub
Pages. Because Actions cron is UTC-only and doesn't shift for daylight
saving, two cron entries cover both EST and EDT, and a guard step skips
whichever one fires at the wrong local time.

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
