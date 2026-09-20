import json
import subprocess
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

app = Flask(__name__)


def _load(name, default):
    path = DATA / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


@app.route("/")
def dashboard():
    fear_greed = _load("fear_greed.json", {})
    vix = _load("vix.json", {})
    heatmap = _load("heatmap.json", {"stocks": []})
    econ = _load("econ_calendar.json", {"days": [], "fred_actuals": {}})
    earnings = _load("earnings_calendar.json", {"next_week": {"range": "", "companies": []}, "last_week": {"range": "", "companies": []}})
    briefing = _load("briefing.json", {"headlines": []})

    oldest_ts = min(
        [d.get("as_of", 0) for d in [fear_greed, vix, heatmap, econ, earnings, briefing] if d.get("as_of")],
        default=None,
    )

    return render_template(
        "dashboard.html",
        fear_greed=fear_greed,
        vix=vix,
        heatmap=heatmap,
        econ=econ,
        earnings=earnings,
        briefing=briefing,
        oldest_ts=oldest_ts,
    )


@app.route("/refresh", methods=["POST"])
def refresh():
    result = subprocess.run(
        [sys.executable, str(BASE / "refresh_all.py")],
        capture_output=True, text=True, cwd=str(BASE),
    )
    return jsonify({"ok": result.returncode == 0, "log": result.stdout + result.stderr})


if __name__ == "__main__":
    app.run(debug=True, port=5050)
