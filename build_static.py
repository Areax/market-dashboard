"""Renders the dashboard pages to static HTML for GitHub Pages.

Run `python refresh_all.py --force` first to populate data/, then this.
"""
import shutil
from pathlib import Path

from app import app, build_context, build_sectors_context

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "dist"
# Loaded by the Sectors and Stock lookup pages on demand (price charts).
DATA_FILES = ["sector_charts.json", "daily_scan_charts.json"]

PAGES = [
    ("dashboard.html", "index.html", build_context),
    ("sectors.html", "sectors.html", build_sectors_context),
    ("stock.html", "stock.html", build_sectors_context),
    ("combos.html", "combos.html", build_sectors_context),
]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with app.app_context():
        for template, out_name, context in PAGES:
            html = app.jinja_env.get_template(template).render(**context(is_static=True))
            (OUT_DIR / out_name).write_text(html)
            print(f"Wrote {OUT_DIR / out_name} ({len(html)} bytes)")
    for name in DATA_FILES:
        src = BASE / "data" / name
        if src.exists():
            shutil.copy(src, OUT_DIR / name)
            print(f"Copied {name} ({src.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
