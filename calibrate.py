"""
calibrate.py — Weekly Signal Calibration Report

Usage:
    python3 calibrate.py             # analyze + post Discord report
    python3 calibrate.py --apply     # apply suggested weights to config.py + commit
    python3 calibrate.py --days N    # override lookback window (default 7)
    python3 calibrate.py --test      # dry run, print to stdout, no Discord send
"""

import sqlite3
import re
import json
import argparse
import subprocess
import os
from datetime import datetime, timedelta
from typing import Optional, List

import config


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_graded_picks(conn: sqlite3.Connection, days: int = 7,
                       _now: Optional[str] = None) -> List[dict]:
    """Return all discord-sent, fully-graded picks within the last N days."""
    if _now is None:
        _now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cutoff_dt = datetime.strptime(_now, "%Y-%m-%d %H:%M:%S") - timedelta(days=days)
    cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")

    rows = conn.execute("""
        SELECT pick_type, pick_team, confidence, status, edge_score,
               edge_pitching, edge_offense, edge_bullpen, edge_advanced,
               edge_market, edge_weather, notes, ml_odds, ou_odds, ev_score,
               created_at
        FROM picks
        WHERE discord_sent = 1
          AND status IN ('won', 'lost', 'push')
          AND created_at >= ?
    """, (cutoff,)).fetchall()

    return [dict(r) for r in rows]


def _parse_era(text: str, side: str) -> Optional[float]:
    """Extract ERA float from bullpen edge text for given side (home/away)."""
    pattern = rf"{side.capitalize()} top pen \(7d\):.*?([\d]+\.[\d]+) ERA"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _parse_market_pct(text: str) -> Optional[float]:
    """Extract market edge percentage from edge_market text."""
    m = re.search(r"has \+([\d.]+)% edge vs market", text or "", re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _parse_wind_mph(text: str) -> Optional[float]:
    """Extract wind speed from edge_weather text."""
    m = re.search(r"Wind\s+([\d]+)\s*mph", text or "", re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def parse_signals(pick: dict) -> dict:
    """
    Parse a pick dict into boolean signal flags via string matching.
    pick may have a '_pick_side' key ('home'/'away') injected by the caller;
    otherwise we infer from pick_type + pick_team (not always possible).
    """
    ep = pick.get("edge_pitching") or ""
    eo = pick.get("edge_offense") or ""
    eb = pick.get("edge_bullpen") or ""
    ea = pick.get("edge_advanced") or ""
    em = pick.get("edge_market") or ""
    ew = pick.get("edge_weather") or ""
    notes = pick.get("notes") or ""

    # SP signals
    sp_home_adv = bool(re.search(r"Home SP.*clear pitching advantage", ep, re.IGNORECASE))
    sp_away_adv = bool(re.search(r"Away SP.*clear pitching advantage", ep, re.IGNORECASE))
    sp_home_layoff = bool(re.search(r"Home SP extended layoff", ep, re.IGNORECASE))
    sp_away_layoff = bool(re.search(r"Away SP extended layoff", ep, re.IGNORECASE))

    # Offense signals
    offense_home = bool(re.search(r"Home lineup has offensive advantage", eo, re.IGNORECASE))
    offense_away = bool(re.search(r"Away lineup has offensive advantage", eo, re.IGNORECASE))

    # Bullpen signals
    bp_home_stronger = bool(re.search(r"Home bullpen is stronger", eb, re.IGNORECASE))
    bp_away_stronger = bool(re.search(r"Away bullpen is stronger", eb, re.IGNORECASE))
    home_era = _parse_era(eb, "home")
    away_era = _parse_era(eb, "away")
    bp_home_era_bad = home_era is not None and home_era > 5.0
    bp_away_era_bad = away_era is not None and away_era > 5.0

    # Advanced signals
    adv_barrel = bool(re.search(r"barrel rate advantage", ea, re.IGNORECASE))
    adv_hardhit = bool(re.search(r"hard-hit rate edge", ea, re.IGNORECASE))
    adv_xwoba = bool(re.search(r"xwOBA", ea, re.IGNORECASE))

    # Market bucket
    mkt_pct = _parse_market_pct(em)
    mkt_low = mkt_pct is not None and mkt_pct < 2.0
    mkt_mid = mkt_pct is not None and 2.0 <= mkt_pct <= 4.0
    mkt_high = mkt_pct is not None and mkt_pct > 4.0

    # Other
    lineup_confirmed = bool(re.search(r"confirmed", notes, re.IGNORECASE))
    rain_flag = bool(re.search(r"Rain", ew, re.IGNORECASE))
    wind_mph = _parse_wind_mph(ew)
    wind_strong = wind_mph is not None and wind_mph > 12.0

    # Combo flags — need pick_side
    pick_side = pick.get("_pick_side", "")
    rust_home = sp_home_layoff and bp_home_era_bad and pick_side == "home"
    rust_away = sp_away_layoff and bp_away_era_bad and pick_side == "away"

    return {
        "sp_home_advantage": sp_home_adv,
        "sp_away_advantage": sp_away_adv,
        "sp_home_layoff": sp_home_layoff,
        "sp_away_layoff": sp_away_layoff,
        "offense_home_edge": offense_home,
        "offense_away_edge": offense_away,
        "bullpen_home_stronger": bp_home_stronger,
        "bullpen_away_stronger": bp_away_stronger,
        "bullpen_home_era_bad": bp_home_era_bad,
        "bullpen_away_era_bad": bp_away_era_bad,
        "advanced_barrel": adv_barrel,
        "advanced_hardhit": adv_hardhit,
        "advanced_xwoba": adv_xwoba,
        "market_edge_low": mkt_low,
        "market_edge_mid": mkt_mid,
        "market_edge_high": mkt_high,
        "lineup_confirmed": lineup_confirmed,
        "rain_flag": rain_flag,
        "wind_strong": wind_strong,
        "rust_weak_pen_home": rust_home,
        "rust_weak_pen_away": rust_away,
    }


# Signals that map to each agent weight key
# Note: 'momentum' agent has no stable text signal in edge_* fields — weight frozen.
# 'lineup_confirmed' maps to no agent (informational only).
_SIGNAL_TO_AGENT = {
    "sp_home_advantage":   "pitching",
    "sp_away_advantage":   "pitching",
    "sp_home_layoff":      "pitching",
    "sp_away_layoff":      "pitching",
    "offense_home_edge":   "offense",
    "offense_away_edge":   "offense",
    "bullpen_home_stronger": "bullpen",
    "bullpen_away_stronger": "bullpen",
    "bullpen_home_era_bad":  "bullpen",
    "bullpen_away_era_bad":  "bullpen",
    "advanced_barrel":     "advanced",
    "advanced_hardhit":    "advanced",
    "advanced_xwoba":      "advanced",
    "market_edge_low":     "market",
    "market_edge_mid":     "market",
    "market_edge_high":    "market",
    "rain_flag":           "weather",
    "wind_strong":         "weather",
    "rust_weak_pen_home":  "pitching",
    "rust_weak_pen_away":  "pitching",
}

MIN_SIGNAL_N = 3   # below this — noise, don't show
MIN_SUGGEST_N = 5  # below this — don't suggest weight changes
DIVERGE_THRESHOLD = 0.08   # signal win rate must differ by this much to trigger nudge
NUDGE = 0.02
MAX_NUDGE = 0.03
MIN_TOTAL_ADJUSTMENT = 0.03  # ignore noise commits below this magnitude


def analyze_signals(picks: list) -> dict:
    """
    For each signal flag, compute N, W, L, win_rate, delta vs baseline.
    Returns {baseline_win_rate, signal_table, pick_count, ml_record, ou_record}.
    """
    if not picks:
        return {"baseline_win_rate": 0.0, "signal_table": {}, "pick_count": 0,
                "ml_record": (0, 0), "ou_record": (0, 0)}

    # Baseline — all picks
    total = len(picks)
    total_wins = sum(1 for p in picks if p["status"] == "won")
    baseline = round(total_wins / total, 4) if total > 0 else 0.0

    # ML vs O/U records
    ml_picks = [p for p in picks if p.get("pick_type") in ("moneyline", "f5_ml")]
    ou_picks  = [p for p in picks if p.get("pick_type") in ("over", "under")]
    ml_rec = (sum(1 for p in ml_picks if p["status"] == "won"),
               sum(1 for p in ml_picks if p["status"] == "lost"))
    ou_rec = (sum(1 for p in ou_picks if p["status"] == "won"),
               sum(1 for p in ou_picks if p["status"] == "lost"))

    # Per-signal tallies
    tallies: dict = {}
    for pick in picks:
        sigs = parse_signals(pick)
        for sig, present in sigs.items():
            if not present:
                continue
            if sig not in tallies:
                tallies[sig] = {"n": 0, "wins": 0, "losses": 0}
            tallies[sig]["n"] += 1
            if pick["status"] == "won":
                tallies[sig]["wins"] += 1
            elif pick["status"] == "lost":
                tallies[sig]["losses"] += 1

    # Build signal_table — filter by MIN_SIGNAL_N
    signal_table = {}
    for sig, t in tallies.items():
        if t["n"] < MIN_SIGNAL_N:
            continue
        win_rate = round(t["wins"] / t["n"], 4) if t["n"] > 0 else 0.0
        delta = round(win_rate - baseline, 4)
        signal_table[sig] = {
            "n": t["n"],
            "wins": t["wins"],
            "losses": t["losses"],
            "win_rate": win_rate,
            "delta": delta,
        }

    return {
        "baseline_win_rate": baseline,
        "signal_table": signal_table,
        "pick_count": total,
        "ml_record": ml_rec,
        "ou_record": ou_rec,
    }


def suggest_weights(current: dict, signal_table: dict,
                    baseline: float, n_picks: int) -> dict:
    """
    Suggest agent weight adjustments based on signal performance.
    Returns a new weights dict (normalized to 1.0), or current if no changes warranted.
    """
    adjustments: dict = {k: 0.0 for k in current}

    for sig, row in signal_table.items():
        if row["n"] < MIN_SUGGEST_N:
            continue
        if abs(row["delta"]) <= DIVERGE_THRESHOLD:
            continue
        agent = _SIGNAL_TO_AGENT.get(sig)
        if agent is None or agent not in current:
            continue
        nudge = NUDGE if row["delta"] > 0 else -NUDGE
        adjustments[agent] += nudge

    # Cap each agent adjustment at ±MAX_NUDGE
    for agent in adjustments:
        adjustments[agent] = max(-MAX_NUDGE, min(MAX_NUDGE, adjustments[agent]))

    total_adj = sum(abs(v) for v in adjustments.values())
    if total_adj < MIN_TOTAL_ADJUSTMENT:
        return dict(current)  # return copy to avoid mutation hazard

    # Apply adjustments
    new_weights = {k: round(current[k] + adjustments[k], 4) for k in current}

    # Clamp to [0.01, 0.50] per weight
    for k in new_weights:
        new_weights[k] = max(0.01, min(0.50, new_weights[k]))

    # Normalize to sum exactly 1.0 using proportional scaling
    total = sum(new_weights.values())
    if abs(total - 1.0) > 1e-9:
        scale = 1.0 / total
        for k in new_weights:
            new_weights[k] = round(new_weights[k] * scale, 4)
        # One-shot rounding correction
        total_after = sum(new_weights.values())
        if abs(total_after - 1.0) > 1e-9:
            diff = round(1.0 - total_after, 4)
            largest = max(new_weights, key=lambda k: new_weights[k])
            new_weights[largest] = round(new_weights[largest] + diff, 4)

    return new_weights


def _signal_label(sig: str) -> str:
    """Return a human-readable label for a signal key."""
    labels = {
        "sp_home_advantage":   "Home SP advantage",
        "sp_away_advantage":   "Away SP advantage",
        "sp_home_layoff":      "Home SP layoff",
        "sp_away_layoff":      "Away SP layoff",
        "offense_home_edge":   "Home offense edge",
        "offense_away_edge":   "Away offense edge",
        "bullpen_home_stronger": "Home bullpen stronger",
        "bullpen_away_stronger": "Away bullpen stronger",
        "bullpen_home_era_bad":  "Home bullpen ERA >5.0",
        "bullpen_away_era_bad":  "Away bullpen ERA >5.0",
        "advanced_barrel":     "Barrel rate edge",
        "advanced_hardhit":    "Hard-hit rate edge",
        "advanced_xwoba":      "xwOBA edge",
        "market_edge_low":     "Market edge <2%",
        "market_edge_mid":     "Market edge 2-4%",
        "market_edge_high":    "Market edge >4%",
        "lineup_confirmed":    "Lineup confirmed",
        "rain_flag":           "Rain flag",
        "wind_strong":         "Wind >12 mph",
        "rust_weak_pen_home":  "Rust + weak pen (home)",
        "rust_weak_pen_away":  "Rust + weak pen (away)",
    }
    return labels.get(sig, sig)


def build_embed(analysis: dict, current_weights: dict,
                suggested_weights: dict, week_label: str) -> dict:
    """Build a Discord webhook payload with the calibration report embed."""
    bl = analysis["baseline_win_rate"]
    n  = analysis["pick_count"]
    ml_w, ml_l = analysis["ml_record"]
    ou_w, ou_l = analysis["ou_record"]
    st = analysis["signal_table"]

    ou_str = f"O/U: {ou_w}W-{ou_l}L" if (ou_w + ou_l) > 0 else "O/U: 0 graded"
    desc_line = (
        f"ML: {ml_w}W-{ml_l}L ({bl*100:.1f}%) | "
        f"{n} picks | {ou_str}"
    )

    # Signal breakdown field
    if st:
        sig_lines = []
        for sig, row in sorted(st.items(), key=lambda x: x[1]["delta"], reverse=True):
            icon = "✅" if row["delta"] >= 0.05 else ("⚠️" if row["delta"] <= -0.10 else "➡️")
            flag = " ← flagged" if "rust_weak_pen" in sig else ""
            sig_lines.append(
                f"{icon} {_signal_label(sig):<22} "
                f"{row['n']} picks  {row['wins']}W-{row['losses']}L  "
                f"{row['win_rate']*100:.1f}%  ({row['delta']:+.1%}){flag}"
            )
        signal_value = "```\n" + "\n".join(sig_lines) + "\n```"
    else:
        signal_value = "_No signals with N ≥ 3 picks this week._"

    # Weight suggestions field
    weights_changed = current_weights != suggested_weights
    if weights_changed and n >= 10:
        wt_lines = []
        for agent in current_weights:
            cur = current_weights[agent]
            sug = suggested_weights[agent]
            diff = sug - cur
            if abs(diff) >= 0.01:
                wt_lines.append(f"{agent:<10} {cur:.2f} → {sug:.2f}  ({diff:+.2f})")
            else:
                wt_lines.append(f"{agent:<10} {cur:.2f}   (hold)")
        wt_lines.append("\nRun: python3 calibrate.py --apply to apply")
        weight_value = "```\n" + "\n".join(wt_lines) + "\n```"
    else:
        weight_value = "_Weights look calibrated — no changes recommended._"

    sep = "━" * 32
    if n < 10:
        desc = f"{desc_line}\n\nNot enough graded picks this week ({n}) — report only, no changes suggested."
    elif not weights_changed:
        desc = f"{desc_line}\n\nWeights look calibrated — no changes recommended."
    else:
        desc = desc_line

    embed = {
        "title": f"📊 Weekly Calibration Report — Week of {week_label}",
        "description": f"{sep}\n{desc}",
        "color": 0x2ECC71 if bl >= 0.60 else 0xE74C3C,
        "fields": [
            {"name": "SIGNAL BREAKDOWN", "value": signal_value, "inline": False},
            {"name": "SUGGESTED WEIGHTS", "value": weight_value, "inline": False},
        ],
        "footer": {"text": sep},
    }

    return {"embeds": [embed]}


def post_to_discord(payload: dict) -> bool:
    """POST embed payload to Discord webhook. Returns True on success."""
    import requests
    url = config.DISCORD_WEBHOOK_URL
    resp = requests.post(url, json=payload, timeout=10)
    return resp.status_code in (200, 204)


_DEFAULT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_log.jsonl")


def write_calibration_log(entry: dict, log_path: str = _DEFAULT_LOG_PATH) -> None:
    """Append a calibration run entry to the JSONL log."""
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
MIN_APPLY_PICKS = 10


def _update_config_weights(new_weights: dict, config_path: str = _CONFIG_PATH) -> None:
    """
    Rewrite the WEIGHTS dict in config.py using line-by-line replacement.
    Matches lines like:    "pitching":    0.22,
    Preserves comments after the value.
    """
    with open(config_path) as f:
        lines = f.readlines()

    updated = []
    for line in lines:
        replaced = False
        for agent, value in new_weights.items():
            # Match pattern: "agent": <spaces> <number>, (optional trailing comment)
            # group(1) = indent + key + colon + spaces
            # group(2) = comma + everything after (whitespace, inline comment)
            pattern = rf'(\s*"{agent}":\s+)[\d.]+(,.*)'
            m = re.match(pattern, line)
            if m:
                # Preserve indentation and the trailing comma + any inline comment
                formatted_value = f"{value:.2f}"
                updated.append(f'{m.group(1)}{formatted_value}{m.group(2)}\n')
                replaced = True
                break
        if not replaced:
            updated.append(line)

    with open(config_path, "w") as f:
        f.writelines(updated)


def apply_weights(picks: list, analysis: dict, suggested_weights: dict,
                  current_weights: dict, dry_run: bool = False) -> dict:
    """
    Apply suggested weights to config.py and commit.
    Returns dict with {applied: bool, reason: str}.
    """
    n = analysis["pick_count"]
    if n < MIN_APPLY_PICKS:
        return {"applied": False,
                "reason": f"not enough graded picks this week ({n}) — need {MIN_APPLY_PICKS}"}

    if suggested_weights == current_weights:
        return {"applied": False, "reason": "no weight changes suggested"}

    if dry_run:
        return {"applied": False, "reason": "dry_run=True, skipped write"}

    # Summary for commit message
    changes = []
    for agent in current_weights:
        diff = round(suggested_weights.get(agent, current_weights[agent]) - current_weights[agent], 4)
        if abs(diff) >= 0.01:
            changes.append(f"{agent} {diff:+.2f}")
    summary = ", ".join(changes) if changes else "minor rebalance"

    date_str = datetime.now().strftime("%Y-%m-%d")
    commit_msg = f"calibration: weekly weight update {date_str} — {summary}"

    # Read original content before write in case git commit fails and we need to rollback
    with open(_CONFIG_PATH) as f:
        original_lines = f.readlines()

    _update_config_weights(suggested_weights)

    try:
        subprocess.run(["git", "add", _CONFIG_PATH], cwd=_PROJECT_ROOT, check=True)
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=_PROJECT_ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        # Roll back config.py so the engine doesn't run with uncommitted weight changes
        with open(_CONFIG_PATH, "w") as f:
            f.writelines(original_lines)
        return {"applied": False, "reason": f"git commit failed: {exc}"}

    return {"applied": True, "reason": summary}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Weekly signal calibration report")
    parser.add_argument("--apply", action="store_true",
                        help="Apply suggested weights to config.py and commit")
    parser.add_argument("--days", type=int, default=7,
                        help="Lookback window in days (default: 7)")
    parser.add_argument("--test", action="store_true",
                        help="Dry run — print report to stdout, no Discord send")
    args = parser.parse_args(argv)

    conn = _open_db()
    picks = fetch_graded_picks(conn, days=args.days)
    conn.close()

    analysis = analyze_signals(picks)
    current_weights = dict(config.WEIGHTS)
    suggested_weights = suggest_weights(
        current_weights, analysis["signal_table"],
        analysis["baseline_win_rate"], analysis["pick_count"]
    )

    week_label = datetime.now().strftime("%b %-d")
    payload = build_embed(analysis, current_weights, suggested_weights, week_label)

    if args.test:
        if args.apply:
            print("NOTE: --apply ignored in --test mode")
        e = payload["embeds"][0]
        print(e["title"])
        print(e["description"])
        for field in e.get("fields", []):
            print(f"\n{field['name']}")
            print(field["value"])
        return

    # Post to Discord
    ok = post_to_discord(payload)
    if not ok:
        print("WARNING: Discord post failed")

    # Write log entry
    apply_result = {"applied": False, "reason": "report-only run"}
    if args.apply:
        apply_result = apply_weights(
            picks=picks,
            analysis=analysis,
            suggested_weights=suggested_weights,
            current_weights=current_weights,
        )

    log_entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "window_days": args.days,
        "pick_count": analysis["pick_count"],
        "win_rate": analysis["baseline_win_rate"],
        "signal_table": analysis["signal_table"],
        "weights_before": current_weights,
        "weights_after": suggested_weights if apply_result["applied"] else current_weights,
        "applied": apply_result["applied"],
    }
    write_calibration_log(log_entry)

    status = "applied" if apply_result["applied"] else "report only"
    print(f"Calibration complete ({status}) — {analysis['pick_count']} picks, "
          f"{analysis['baseline_win_rate']*100:.1f}% win rate")


if __name__ == "__main__":
    main()

