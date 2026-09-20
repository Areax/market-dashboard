"""Refresh every data source the dashboard uses, skipping sources whose cache
is still fresh (protects against hammering rate-limited free endpoints when
you rerun the script often).
"""
import sys
import time

from fetchers import briefing, econ_calendar, earnings_calendar, fear_greed, fedwatch, heatmap, vix
from fetchers.cache_utils import is_fresh, retry

# (module, cache path attr, ttl seconds)
SOURCES = [
    ("Fear & Greed", fear_greed, fear_greed.CACHE_PATH, 15 * 60),
    ("VIX / S&P", vix, vix.CACHE_PATH, 15 * 60),
    ("S&P heatmap", heatmap, heatmap.CACHE_PATH, 15 * 60),
    ("FedWatch (self-computed)", fedwatch, fedwatch.CACHE_PATH, 15 * 60),
    ("Econ calendar (PPI/CPI)", econ_calendar, econ_calendar.CACHE_PATH, 6 * 3600),
    ("Earnings calendar", earnings_calendar, earnings_calendar.CACHE_PATH, 6 * 3600),
    ("Briefing.com", briefing, briefing.CACHE_PATH, 15 * 60),
]


def main():
    force = "--force" in sys.argv
    for name, module, cache_path, ttl in SOURCES:
        if not force and is_fresh(cache_path, ttl):
            print(f"[skip]  {name} (cache is fresh)")
            continue
        print(f"[fetch] {name} ...", end=" ", flush=True)
        start = time.time()
        try:
            retry(module.fetch, attempts=3, base_delay=8)
            print(f"ok ({time.time() - start:.1f}s)")
        except Exception as exc:
            print(f"FAILED: {exc}")


if __name__ == "__main__":
    main()
