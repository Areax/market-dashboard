"""Renders the dashboard pages to static HTML for GitHub Pages.

Run `python refresh_all.py --force` first to populate data/, then this.
"""
from pathlib import Path

from app import app, build_context, build_sectors_context

OUT_DIR = Path(__file__).resolve().parent / "dist"

PAGES = [
    ("dashboard.html", "index.html", build_context),
    ("sectors.html", "sectors.html", build_sectors_context),
]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with app.app_context():
        for template, out_name, context in PAGES:
            html = app.jinja_env.get_template(template).render(**context(is_static=True))
            (OUT_DIR / out_name).write_text(html)
            print(f"Wrote {OUT_DIR / out_name} ({len(html)} bytes)")


if __name__ == "__main__":
    main()
