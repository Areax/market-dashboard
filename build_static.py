"""Renders the dashboard to a static index.html for GitHub Pages.

Run `python refresh_all.py --force` first to populate data/, then this.
"""
from pathlib import Path

from app import app, build_context

OUT_DIR = Path(__file__).resolve().parent / "dist"


def main():
    with app.app_context():
        html = app.jinja_env.get_template("dashboard.html").render(**build_context(is_static=True))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "index.html").write_text(html)
    print(f"Wrote {OUT_DIR / 'index.html'} ({len(html)} bytes)")


if __name__ == "__main__":
    main()
