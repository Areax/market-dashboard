# Market Dashboard

A local dashboard combining Fear & Greed, VIX, an S&P 500 heatmap, the
PPI/CPI economic calendar, and the week's earnings calendar.

## Run it

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
| CME FedWatch | Link-out only | The real tool is a licensed third-party widget that blocks automated access outside cmegroup.com; CME's official API is a paid product. The card links straight to the live tool. |
| PPI / CPI + economic calendar | ForexFactory's public calendar feed (schedule/forecast) + FRED (actual CPI/PPI m/m, official Fed data) | MarketWatch's calendar sits behind an active CAPTCHA challenge for automated browsers, so it's not used. |
| Earnings calendar | Yahoo Finance | Top ~40 companies by market cap reporting in the next 7 days |
| Briefing.com | Briefing.com homepage | Only the 2 free "Latest Comments" headlines are public; the rest is subscriber-only |

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
