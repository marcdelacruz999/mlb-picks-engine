#!/usr/bin/env python3
"""
export_db_snapshot.py — Nightly DB state export for remote CEO agent.

Runs at 1:45 AM local time (before the 2:00 AM remote trigger fires).
Writes DB_SNAPSHOT.md to repo root, then git commits + pushes so the
remote agent has live context: picks, grades, ROI, rolling data coverage.
"""

import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "mlb_picks.db"
SNAPSHOT_PATH = Path(__file__).parent / "DB_SNAPSHOT.md"
REPO_DIR = Path(__file__).parent


def q(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def main():
    if not DB_PATH.exists():
        print(f"[SNAPSHOT] DB not found at {DB_PATH}. Skipping.")
        sys.exit(0)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    lines = [f"# DB Snapshot — {today} 01:45 AM\n"]
    lines.append("*Auto-generated nightly. Do not edit manually.*\n")

    # ── Recent picks (last 14 days) ──────────────────────────────
    lines.append("\n## Recent Picks (last 14 days)\n")
    picks = q(conn, """
        SELECT created_at, pick_type, pick_team, confidence, edge_score,
               ev_score, ml_odds, ou_odds, status
        FROM picks
        WHERE created_at >= date('now', '-14 days') AND discord_sent=1
        ORDER BY created_at DESC
    """)
    if picks:
        lines.append("| Date | Type | Team | Conf | Edge | EV | Odds | Result |")
        lines.append("|------|------|------|------|------|----|------|--------|")
        for p in picks:
            odds = p["ml_odds"] or p["ou_odds"] or "—"
            lines.append(
                f"| {str(p['created_at'])[:10]} | {p['pick_type']} | {p['pick_team']} "
                f"| {p['confidence']} | {p['edge_score']:.3f} | {p['ev_score'] or '—'} "
                f"| {odds} | {p['status'] or 'pending'} |"
            )
    else:
        lines.append("*No picks in last 14 days.*")

    # ── ROI summary ──────────────────────────────────────────────
    lines.append("\n## ROI Summary (last 30 days)\n")
    roi_rows = q(conn, """
        SELECT status, ml_odds, ou_odds FROM picks
        WHERE created_at >= date('now', '-30 days')
        AND status IN ('won','lost','push') AND discord_sent=1
    """)
    won = lost = push = 0
    net = 0.0
    for r in roi_rows:
        if r["status"] == "won":
            won += 1
            odds = r["ml_odds"] or r["ou_odds"]
            if odds:
                net += 100.0 / abs(odds) if odds < 0 else odds / 100.0
        elif r["status"] == "lost":
            lost += 1
            net -= 1.0
        else:
            push += 1
    total = won + lost + push
    wl = won + lost
    lines.append(f"- Record: **{won}-{lost}-{push}** ({total} graded)")
    lines.append(f"- Win rate: **{round(won/wl*100,1) if wl else 0}%**")
    lines.append(f"- Net units: **{round(net,3):+}**")

    # ── Win rate by pick type ────────────────────────────────────
    lines.append("\n## Win Rate by Pick Type (last 30 days)\n")
    pt_rows = q(conn, """
        SELECT pick_type, status, COUNT(*) as cnt FROM picks
        WHERE created_at >= date('now', '-30 days')
        AND status IN ('won','lost','push') AND discord_sent=1
        GROUP BY pick_type, status
    """)
    pt = {}
    for r in pt_rows:
        pt.setdefault(r["pick_type"], {"won": 0, "lost": 0, "push": 0})
        pt[r["pick_type"]][r["status"]] += r["cnt"]
    if pt:
        lines.append("| Type | W | L | P | Win% |")
        lines.append("|------|---|---|---|------|")
        for ptype, s in sorted(pt.items()):
            wl2 = s["won"] + s["lost"]
            pct = round(s["won"] / wl2 * 100, 1) if wl2 else 0
            lines.append(f"| {ptype} | {s['won']} | {s['lost']} | {s['push']} | {pct}% |")
    else:
        lines.append("*No graded picks yet.*")

    # ── Win rate by confidence ───────────────────────────────────
    lines.append("\n## Win Rate by Confidence (last 30 days)\n")
    conf_rows = q(conn, """
        SELECT confidence, status, COUNT(*) as cnt FROM picks
        WHERE created_at >= date('now', '-30 days')
        AND status IN ('won','lost','push') AND discord_sent=1
        GROUP BY confidence, status
    """)
    conf = {}
    for r in conf_rows:
        conf.setdefault(r["confidence"], {"won": 0, "lost": 0, "push": 0})
        conf[r["confidence"]][r["status"]] += r["cnt"]
    if conf:
        lines.append("| Conf | W | L | P | Win% |")
        lines.append("|------|---|---|---|------|")
        for c in sorted(conf.keys(), reverse=True):
            s = conf[c]
            wl2 = s["won"] + s["lost"]
            pct = round(s["won"] / wl2 * 100, 1) if wl2 else 0
            lines.append(f"| {c} | {s['won']} | {s['lost']} | {s['push']} | {pct}% |")
    else:
        lines.append("*No graded picks yet.*")

    # ── Model accuracy (all logged games) ────────────────────────
    lines.append("\n## Model Accuracy — All Games (last 30 days)\n")
    acc = q(conn, """
        SELECT ml_status, ou_status, COUNT(*) as cnt FROM analysis_log
        WHERE game_date >= date('now', '-30 days')
        AND ml_status IN ('correct','incorrect','push')
        GROUP BY ml_status, ou_status
    """)
    ml_c = ml_i = ou_c = ou_i = 0
    for r in acc:
        if r["ml_status"] == "correct": ml_c += r["cnt"]
        elif r["ml_status"] == "incorrect": ml_i += r["cnt"]
        if r["ou_status"] == "correct": ou_c += r["cnt"]
        elif r["ou_status"] == "incorrect": ou_i += r["cnt"]
    ml_tot = ml_c + ml_i
    ou_tot = ou_c + ou_i
    lines.append(f"- ML model: {ml_c}/{ml_tot} correct ({round(ml_c/ml_tot*100,1) if ml_tot else 0}%)")
    lines.append(f"- O/U model: {ou_c}/{ou_tot} correct ({round(ou_c/ou_tot*100,1) if ou_tot else 0}%)")

    # ── Rolling data coverage ────────────────────────────────────
    lines.append("\n## Rolling Data Coverage\n")
    p_count = q(conn, "SELECT COUNT(*) as cnt FROM pitcher_game_logs")[0]["cnt"]
    p_last  = q(conn, "SELECT MAX(game_date) as d FROM pitcher_game_logs")[0]["d"]
    t_count = q(conn, "SELECT COUNT(*) as cnt FROM team_game_logs")[0]["cnt"]
    t_last  = q(conn, "SELECT MAX(game_date) as d FROM team_game_logs")[0]["d"]
    lines.append(f"- pitcher_game_logs: **{p_count} rows**, last date: {p_last or 'none'}")
    lines.append(f"- team_game_logs: **{t_count} rows**, last date: {t_last or 'none'}")

    # Pitchers with enough data for blend (>=5 starts)
    blend_ready = q(conn, """
        SELECT COUNT(DISTINCT pitcher_id) as cnt FROM pitcher_game_logs
        WHERE is_starter=1
        GROUP BY pitcher_id HAVING COUNT(*) >= 5
    """)
    lines.append(f"- Starters with ≥5 game logs (blend active): **{len(blend_ready)}**")

    # ── Agent score trends (last 7 days) ─────────────────────────
    lines.append("\n## Agent Score Averages (last 7 days, all logged games)\n")
    agent_rows = q(conn, """
        SELECT
            AVG(score_pitching) as pit, AVG(score_offense) as off,
            AVG(score_bullpen) as bul, AVG(score_advanced) as adv,
            AVG(score_momentum) as mom, AVG(score_market) as mkt,
            AVG(score_weather) as wth,
            COUNT(*) as games
        FROM analysis_log
        WHERE game_date >= date('now', '-7 days')
        AND score_pitching IS NOT NULL
    """)
    if agent_rows and agent_rows[0]["games"]:
        r = agent_rows[0]
        lines.append(f"*(over {r['games']} game-logs)*\n")
        lines.append("| Agent | Avg Score |")
        lines.append("|-------|-----------|")
        for label, val in [
            ("Pitching", r["pit"]), ("Offense", r["off"]), ("Bullpen", r["bul"]),
            ("Advanced", r["adv"]), ("Momentum", r["mom"]), ("Market", r["mkt"]),
            ("Weather", r["wth"]),
        ]:
            lines.append(f"| {label} | {round(val,3) if val is not None else '—'} |")
    else:
        lines.append("*No agent scores logged yet.*")

    # ── Scratch alerts (last 7 days) ─────────────────────────────
    lines.append("\n## Scratch Alerts (last 7 days)\n")
    scratches = q(conn, """
        SELECT game_date, side, old_pitcher, new_pitcher FROM scratch_alerts
        WHERE game_date >= date('now', '-7 days')
        ORDER BY game_date DESC
    """)
    if scratches:
        for s in scratches:
            lines.append(f"- {s['game_date']} {s['side']}: {s['old_pitcher']} → {s['new_pitcher']}")
    else:
        lines.append("*No scratches in last 7 days.*")

    conn.close()

    # ── Write snapshot ───────────────────────────────────────────
    SNAPSHOT_PATH.write_text("\n".join(lines) + "\n")
    print(f"[SNAPSHOT] Written to {SNAPSHOT_PATH}")

    # ── Git commit + push ────────────────────────────────────────
    try:
        subprocess.run(["git", "add", "DB_SNAPSHOT.md"], cwd=REPO_DIR, check=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=REPO_DIR
        )
        if result.returncode != 0:  # changes staged
            subprocess.run(
                ["git", "commit", "-m", f"chore: nightly DB snapshot {today}"],
                cwd=REPO_DIR, check=True
            )
            subprocess.run(["git", "push"], cwd=REPO_DIR, check=True)
            print("[SNAPSHOT] Committed and pushed.")
        else:
            print("[SNAPSHOT] No changes to commit.")
    except subprocess.CalledProcessError as e:
        print(f"[SNAPSHOT] Git error: {e}. Snapshot file still written locally.")


if __name__ == "__main__":
    main()
