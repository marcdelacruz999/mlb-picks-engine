#!/usr/bin/env python3
"""
MLB Picks Engine — Main Orchestrator
======================================
Ties together all modules: data collection, analysis, risk filtering,
Discord notifications, and database tracking.

Usage:
    python3 engine.py              # Run full analysis cycle for today
    python3 engine.py --results    # Grade today's picks and send recap
    python3 engine.py --status     # Print current tracking snapshot
    python3 engine.py --test       # Dry run: analyze but don't send to Discord
"""

import sys
import json
from datetime import date, datetime

import database as db
from data_mlb import collect_game_data, fetch_all_teams, fetch_todays_games
from data_odds import fetch_odds, match_odds_to_game
from analysis import analyze_game, risk_filter, build_watchlist
from discord_bot import send_pick, send_update, send_results, export_payload
from config import DISCORD_WEBHOOK_URL


def run_analysis(dry_run: bool = False):
    """
    MAIN ANALYSIS CYCLE
    1. Initialize DB
    2. Fetch teams
    3. Collect game data (stats, pitchers, lineups)
    4. Fetch odds
    5. Analyze each game
    6. Risk-filter for approved picks
    7. Save to DB
    8. Send to Discord (unless dry_run)
    9. Print summary
    """
    print("=" * 60)
    print(f"  MLB PICKS ENGINE — {date.today().strftime('%B %d, %Y')}")
    print("=" * 60)
    print()

    # Step 1: Init
    db.init_db()

    # Step 2: Teams
    print("[1/6] Loading teams...")
    fetch_all_teams()

    # Step 3: Game data
    print("[2/6] Collecting game data with full stats...")
    games = collect_game_data()

    if not games:
        print("\n⚠️  No games found for today. Nothing to analyze.")
        return

    # Step 4: Odds
    print("[3/6] Fetching odds and lines...")
    odds_list = fetch_odds()

    # Step 5: Analyze each game
    print(f"[4/6] Analyzing {len(games)} games...")
    analyses = []
    for g in games:
        odds_data = match_odds_to_game(
            odds_list,
            g.get("home_team_name", ""),
            g.get("away_team_name", "")
        )
        analysis = analyze_game(g, odds_data)
        analyses.append(analysis)

    # Step 6: Risk filter
    print("[5/6] Running risk filter...")
    approved = risk_filter(analyses)

    # ── Print Analysis Board ──
    print("\n" + "=" * 60)
    print("  GAME ANALYSIS BOARD")
    print("=" * 60)

    for a in analyses:
        flag = "✅" if any(p["mlb_game_id"] == a["mlb_game_id"] for p in approved) else "  "
        print(f"\n{flag} {a['game']}")
        print(f"   Pitchers: {a['away_pitcher']} vs {a['home_pitcher']}")
        print(f"   Composite: {a['composite_score']:+.3f}  |  "
              f"ML Pick: {a['ml_pick_team']} ({a['ml_win_probability']}%)  |  "
              f"Confidence: {a['ml_confidence']}/10")
        print(f"   Projected: {a['away_team']} {a['projected_away_score']} - "
              f"{a['home_team']} {a['projected_home_score']}")
        if a.get("ou_pick", {}).get("pick"):
            ou = a["ou_pick"]
            print(f"   O/U: {ou['pick'].upper()} (conf {ou['confidence']}/10) — {ou['edge']}")

    # ── Top Picks Board ──
    print("\n" + "=" * 60)
    if not approved:
        print("  TODAY'S PICKS: PASS")
        print("  No games meet quality thresholds.")
    else:
        print(f"  TODAY'S APPROVED PICKS ({len(approved)})")
    print("=" * 60)

    for i, pick in enumerate(approved, 1):
        print(f"\n  #{i}  {pick['game']}")
        pick_label = pick["pick_team"]
        if pick["pick_type"] in ("over", "under"):
            pick_label = f"{pick['pick_type'].upper()} {pick.get('notes', '')}"
        print(f"       Pick: {pick_label}")
        print(f"       Confidence: {pick['confidence']}/10  |  "
              f"Win Prob: {pick['win_probability']}%  |  "
              f"Edge: {pick['edge_score']:.3f}")

    # ── Watchlist ──
    watchlist = build_watchlist(analyses, approved)
    print("\n" + "=" * 60)
    if watchlist:
        print(f"  WATCHLIST ({len(watchlist)} games near threshold)")
        print("=" * 60)
        for w in watchlist:
            print(f"\n  👁  {w['game']}")
            print(f"       Near pick: {w['pick_team']}  |  Conf: {w['confidence']}/10")
            print(f"       {w['reason']}")
    else:
        print("  WATCHLIST: None")
        print("=" * 60)

    # ── Save picks and send to Discord ──
    print(f"\n[6/6] {'DRY RUN — skipping Discord' if dry_run else 'Saving picks and sending to Discord'}...")

    for pick in approved:
        # Upsert game to DB
        game_id = db.upsert_game({
            "mlb_game_id": pick["mlb_game_id"],
            "game_date": date.today().isoformat(),
            "status": "scheduled",
        })

        # Save pick
        pick_record = {
            "game_id": game_id,
            "pick_type": pick["pick_type"],
            "pick_team": pick["pick_team"],
            "confidence": pick["confidence"],
            "win_probability": pick["win_probability"],
            "edge_score": pick["edge_score"],
            "projected_away_score": pick["projected_away_score"],
            "projected_home_score": pick["projected_home_score"],
            "edge_pitching": pick["edge_pitching"],
            "edge_offense": pick["edge_offense"],
            "edge_advanced": pick.get("edge_advanced", ""),
            "edge_bullpen": pick["edge_bullpen"],
            "edge_weather": pick["edge_weather"],
            "edge_market": pick["edge_market"],
            "notes": pick.get("notes", ""),
        }
        pick_id = db.save_pick(pick_record)

        if not dry_run:
            sent = send_pick(pick)
            if sent:
                db.mark_pick_sent(pick_id)

        # Print webhook payload for reference
        print(f"\n  📦 Webhook payload for: {pick['game']}")
        print(export_payload(pick))

    # ── Tracking Snapshot ──
    _print_snapshot()

    print("\n✅ Analysis cycle complete.")


def run_refresh():
    """
    MID-DAY REFRESH — Re-run analysis and send Discord update alerts
    if any previously approved picks have changed materially:
    - Pick dropped out of approved list (cancel alert)
    - Confidence dropped by 2+ points (reduce confidence alert)
    Does NOT re-send unchanged picks.
    """
    print("=" * 60)
    print(f"  MLB PICKS REFRESH — {datetime.now().strftime('%B %d, %Y %I:%M %p')}")
    print("=" * 60)

    db.init_db()

    # Get today's sent picks from DB — deduplicate by game_id (keep most recent)
    prior_picks = db.get_today_picks()
    seen_games = {}
    for p in prior_picks:
        if p.get("status") == "pending":
            seen_games[p["game_id"]] = p  # later picks overwrite earlier ones
    sent_picks = list(seen_games.values())

    if not sent_picks:
        print("\nNo picks sent today yet. Nothing to refresh.")
        return

    # Re-run full analysis
    fetch_all_teams()
    games = collect_game_data()
    if not games:
        print("\nNo games found. Nothing to refresh.")
        return

    odds_list = fetch_odds()
    analyses = []
    for g in games:
        odds_data = match_odds_to_game(
            odds_list,
            g.get("home_team_name", ""),
            g.get("away_team_name", "")
        )
        analysis = analyze_game(g, odds_data)
        analyses.append(analysis)

    approved = risk_filter(analyses)
    approved_ids = {p["mlb_game_id"] for p in approved}

    print(f"\nComparing {len(sent_picks)} sent picks against refreshed analysis...")
    updates_sent = 0

    for conn_pick in sent_picks:
        # Find the game row to get mlb_game_id
        conn = db.get_connection()
        game_row = conn.execute(
            "SELECT mlb_game_id FROM games WHERE id=?", (conn_pick["game_id"],)
        ).fetchone()
        conn.close()

        if not game_row:
            continue

        mlb_game_id = game_row["mlb_game_id"]

        # Find matching refreshed analysis
        refreshed = next(
            (a for a in analyses if a["mlb_game_id"] == mlb_game_id), None
        )
        if not refreshed:
            continue

        prior_conf = conn_pick.get("confidence", 0)
        new_conf = refreshed["ml_confidence"]
        still_approved = mlb_game_id in approved_ids

        if not still_approved:
            # Pick no longer meets threshold — cancel alert
            update = {
                "game": refreshed["game"],
                "original_pick": conn_pick.get("pick_team", "?"),
                "update": "Pick no longer meets quality threshold after data refresh",
                "action": "Cancel",
                "reason": f"Confidence dropped from {prior_conf}/10 — edge no longer sufficient",
            }
            sent = send_update(update)
            if sent:
                print(f"  ⚠️  Cancel alert sent: {refreshed['game']}")
                updates_sent += 1

        elif new_conf <= prior_conf - 2:
            # Confidence dropped significantly — reduce confidence alert
            update = {
                "game": refreshed["game"],
                "original_pick": conn_pick.get("pick_team", "?"),
                "update": f"Confidence reduced from {prior_conf}/10 to {new_conf}/10",
                "action": "Reduce Confidence",
                "reason": "Updated stats/odds shifted the edge — still playable but with lower conviction",
            }
            sent = send_update(update)
            if sent:
                print(f"  ⚠️  Reduce alert sent: {refreshed['game']}")
                updates_sent += 1
        else:
            print(f"  ✅ {refreshed['game']} — unchanged (conf {new_conf}/10)")

    if updates_sent == 0:
        print("\nAll picks confirmed. No updates needed.")
    else:
        print(f"\n{updates_sent} update alert(s) sent to Discord.")

    # Show updated watchlist
    watchlist = build_watchlist(analyses, approved)
    if watchlist:
        print("\n  WATCHLIST UPDATE:")
        for w in watchlist:
            print(f"  👁  {w['game']} — {w['pick_team']} (conf {w['confidence']}/10)")


def run_results():
    """
    RESULTS GRADING
    Fetch final scores, grade picks, calculate ROI, send recap.
    """
    print("=" * 60)
    print(f"  MLB RESULTS — {date.today().strftime('%B %d, %Y')}")
    print("=" * 60)

    db.init_db()

    # Fetch today's final scores
    games = fetch_todays_games()
    final_games = [g for g in games if "Final" in g.get("status", "")]

    if not final_games:
        print("\n⚠️  No final games found yet. Run again after games complete.")
        return

    # Get today's picks
    picks = db.get_today_picks()
    if not picks:
        print("\nNo picks were made today. Nothing to grade.")
        return

    wins, losses, pushes = 0, 0, 0
    best_pick = None
    worst_miss = None

    conn = db.get_connection()

    for pick in picks:
        if pick["status"] != "pending":
            continue

        # Find matching game result
        game_row = conn.execute(
            "SELECT * FROM games WHERE id=?", (pick["game_id"],)
        ).fetchone()

        if not game_row:
            continue

        mlb_game_id = game_row["mlb_game_id"]
        result = None

        for fg in final_games:
            if fg["mlb_game_id"] == mlb_game_id:
                result = fg
                break

        if not result:
            continue

        # Grade the pick
        away_score = result.get("away_score", 0) or 0
        home_score = result.get("home_score", 0) or 0
        total_runs = away_score + home_score

        # Update game scores in DB
        db.upsert_game({
            "mlb_game_id": mlb_game_id,
            "game_date": date.today().isoformat(),
            "status": "Final",
            "away_score": away_score,
            "home_score": home_score,
            "total_runs": total_runs,
        })

        if pick["pick_type"] == "moneyline":
            home_won = home_score > away_score
            away_won = away_score > home_score

            if pick["pick_team"] == result.get("home_team_name"):
                status = "won" if home_won else "lost"
            elif pick["pick_team"] == result.get("away_team_name"):
                status = "won" if away_won else "lost"
            else:
                status = "push"

        elif pick["pick_type"] == "over":
            total_line = float(pick.get("notes", "0").replace("Total line: ", "") or 0)
            if total_runs > total_line:
                status = "won"
            elif total_runs < total_line:
                status = "lost"
            else:
                status = "push"

        elif pick["pick_type"] == "under":
            total_line = float(pick.get("notes", "0").replace("Total line: ", "") or 0)
            if total_runs < total_line:
                status = "won"
            elif total_runs > total_line:
                status = "lost"
            else:
                status = "push"
        else:
            status = "push"

        db.update_pick_status(pick["id"], status)

        if status == "won":
            wins += 1
            if not best_pick or pick["confidence"] > (best_pick.get("confidence") or 0):
                best_pick = pick
        elif status == "lost":
            losses += 1
            if not worst_miss or pick["confidence"] > (worst_miss.get("confidence") or 0):
                worst_miss = pick
        else:
            pushes += 1

        print(f"  {'✅' if status == 'won' else '❌' if status == 'lost' else '➖'} "
              f"{pick['pick_team']} ({pick['pick_type']}) — {status.upper()}")

    conn.close()

    total = wins + losses
    roi = round((wins - losses) / max(total, 1) * 100, 1)

    results = {
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "roi": roi,
        "best_pick": best_pick.get("pick_team", "N/A") if best_pick else "N/A",
        "worst_miss": worst_miss.get("pick_team", "N/A") if worst_miss else "N/A",
        "notes": f"{total} graded picks",
    }

    db.save_daily_results(results)

    print(f"\n  Record: {wins}W - {losses}L - {pushes}P  |  ROI: {roi}%")

    # Send recap to Discord
    sent = send_results(results)
    if sent:
        print("  📤 Results recap sent to Discord.")


def _print_snapshot():
    """Print tracking snapshot."""
    summary = db.get_roi_summary(30)
    print("\n" + "-" * 40)
    print("  TRACKING SNAPSHOT (Last 30 Days)")
    print("-" * 40)
    print(f"  Record: {summary['won']}W - {summary['lost']}L - {summary['push']}P")
    print(f"  Win Rate: {summary['win_rate']}%")
    print(f"  Total Graded: {summary['total']}")


def main():
    args = sys.argv[1:]

    if "--results" in args:
        run_results()
    elif "--refresh" in args:
        run_refresh()
    elif "--status" in args:
        db.init_db()
        _print_snapshot()
    elif "--test" in args:
        run_analysis(dry_run=True)
    else:
        run_analysis(dry_run=False)


if __name__ == "__main__":
    main()
