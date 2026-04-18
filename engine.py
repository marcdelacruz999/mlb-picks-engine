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
    python3 engine.py --game X     # Full analysis for game(s) matching team name X, sent to Discord
"""

import sys
import json
import requests
from datetime import date, datetime, timedelta
from typing import Optional

import database as db
from data_mlb import collect_game_data, fetch_all_teams, fetch_todays_games
from data_odds import fetch_odds, match_odds_to_game, implied_probability, fetch_f5_odds, match_f5_odds_to_game
from analysis import analyze_game, risk_filter, build_watchlist
from discord_bot import send_pick, send_pick_edit, send_update, send_results, send_nightly_report, send_daily_board, send_ou_board, export_payload, _format_game_time
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
    f5_odds_list = fetch_f5_odds()

    # Step 5: Analyze each game
    print(f"[4/6] Analyzing {len(games)} games...")
    analyses = []
    for g in games:
        odds_data = match_odds_to_game(
            odds_list,
            g.get("home_team_name", ""),
            g.get("away_team_name", "")
        )
        g["f5_odds"] = match_f5_odds_to_game(
            f5_odds_list,
            g.get("home_team_name", ""),
            g.get("away_team_name", "")
        )
        # Store opening odds for this game (INSERT OR IGNORE — only first capture kept)
        if odds_data and odds_data.get("consensus"):
            db.save_opening_lines(
                g.get("mlb_game_id"),
                date.today().isoformat(),
                odds_data["consensus"]
            )
        analysis = analyze_game(g, odds_data)
        analyses.append(analysis)

    # Step 6: Risk filter
    print("[5/6] Running risk filter...")
    approved = risk_filter(analyses)

    # ── Log all game analyses to DB ──
    if not dry_run:
        today = date.today().isoformat()
        for a in analyses:
            ou = a.get("ou_pick") or {}
            agents = a.get("agents", {})
            db.save_analysis_log({
                "game_date": today,
                "mlb_game_id": a["mlb_game_id"],
                "game": a["game"],
                "away_team": a["away_team"],
                "home_team": a["home_team"],
                "away_pitcher": a.get("away_pitcher", "TBD"),
                "home_pitcher": a.get("home_pitcher", "TBD"),
                "composite_score": a["composite_score"],
                "ml_pick_team": a["ml_pick_team"],
                "ml_win_probability": a["ml_win_probability"],
                "ml_confidence": a["ml_confidence"],
                "ou_pick": ou.get("pick"),
                "ou_line": ou.get("total_line"),
                "ou_confidence": ou.get("confidence"),
                "score_pitching": agents.get("pitching", {}).get("score"),
                "score_offense": agents.get("offense", {}).get("score"),
                "score_bullpen": agents.get("bullpen", {}).get("score"),
                "score_advanced": agents.get("advanced", {}).get("score"),
                "score_momentum": agents.get("momentum", {}).get("score"),
                "score_market": agents.get("market", {}).get("score"),
                "score_weather": agents.get("weather", {}).get("score"),
            })
        print(f"[DB] Logged {len(analyses)} game analyses to analysis_log.")

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
    print(f"\n[6/6] {'DRY RUN — skipping Discord and DB save' if dry_run else 'Saving picks and sending to Discord'}...")

    for pick in approved:
        # Upsert game to DB
        game_id = db.upsert_game({
            "mlb_game_id": pick["mlb_game_id"],
            "game_date": date.today().isoformat(),
            "status": "scheduled",
        })

        if dry_run:
            # Print payload only — no DB write, no Discord
            print(f"\n  📦 Webhook payload for: {pick['game']}")
            print(export_payload(pick))
            continue

        # Skip games that have already started or finished — prevents late launchd fires from sending stale picks
        game_status = pick.get("analysis", {}).get("status", pick.get("status", "Scheduled"))
        pre_game_statuses = {"Scheduled", "Pre-Game", "Warmup", "Delayed Start"}
        if game_status not in pre_game_statuses:
            print(f"  ⏭️  {pick['game']} — skipping send ({game_status}, game already started/finished)")
            continue

        # If already sent today, PATCH the existing message with fresh data
        existing = db.get_sent_pick_today(game_id, pick["pick_type"])
        if existing:
            msg_id = existing.get("discord_message_id")
            if msg_id:
                send_pick_edit(msg_id, pick)
            else:
                print(f"[DB] Pick already sent for game_id={game_id} type={pick['pick_type']} but no message_id stored — skipping.")
            continue

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
            "ev_score": pick.get("ev_score"),
            "ml_odds": pick.get("ml_odds"),
            "ou_odds": pick.get("ou_odds"),
        }
        pick_id = db.save_pick(pick_record)

        message_id = send_pick(pick)
        if message_id:
            db.mark_pick_sent(pick_id, message_id=message_id)

        # Print webhook payload for reference
        print(f"\n  📦 Webhook payload for: {pick['game']}")
        print(export_payload(pick))

    # ── Daily ML Model Board (send/update every 1 hour) ──
    today = date.today().isoformat()
    sent_picks = db.get_today_sent_picks_with_game()
    if db.board_needs_update(today, interval_hours=1):
        board_record = db.get_daily_board(today)
        existing_id = board_record["message_id"] if board_record else None
        message_id = send_daily_board(analyses, existing_message_id=existing_id, approved=sent_picks)
        if message_id:
            db.save_daily_board(today, message_id)

    # ── Daily O/U Model Board (send/update every 1 hour) ──
    if db.ou_board_needs_update(today, interval_hours=1):
        ou_board_record = db.get_daily_ou_board(today)
        existing_ou_id = ou_board_record["message_id"] if ou_board_record else None
        ou_message_id = send_ou_board(analyses, existing_message_id=existing_ou_id, approved=sent_picks)
        if ou_message_id:
            db.save_daily_ou_board(today, ou_message_id)

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
    f5_odds_list = fetch_f5_odds()
    analyses = []
    for g in games:
        odds_data = match_odds_to_game(
            odds_list,
            g.get("home_team_name", ""),
            g.get("away_team_name", "")
        )
        g["f5_odds"] = match_f5_odds_to_game(
            f5_odds_list, g.get("home_team_name", ""), g.get("away_team_name", "")
        )
        # Refresh opening lines capture (INSERT OR IGNORE no-ops if already saved today)
        if odds_data and odds_data.get("consensus"):
            db.save_opening_lines(
                g.get("mlb_game_id"),
                date.today().isoformat(),
                odds_data["consensus"]
            )
        analysis = analyze_game(g, odds_data)
        analyses.append(analysis)

    approved = risk_filter(analyses)
    approved_ids = {p["mlb_game_id"] for p in approved}
    approved_by_type = {(p["mlb_game_id"], p["pick_type"]) for p in approved}

    # ── Update analysis_log with latest analysis (lineups may now be confirmed) ──
    today = date.today().isoformat()
    for a in analyses:
        ou = a.get("ou_pick") or {}
        agents = a.get("agents", {})
        db.save_analysis_log({
            "game_date": today,
            "mlb_game_id": a["mlb_game_id"],
            "game": a["game"],
            "away_team": a["away_team"],
            "home_team": a["home_team"],
            "away_pitcher": a.get("away_pitcher", "TBD"),
            "home_pitcher": a.get("home_pitcher", "TBD"),
            "composite_score": a["composite_score"],
            "ml_pick_team": a["ml_pick_team"],
            "ml_win_probability": a["ml_win_probability"],
            "ml_confidence": a["ml_confidence"],
            "ou_pick": ou.get("pick"),
            "ou_line": ou.get("total_line"),
            "ou_confidence": ou.get("confidence"),
            "score_pitching": agents.get("pitching", {}).get("score"),
            "score_offense": agents.get("offense", {}).get("score"),
            "score_bullpen": agents.get("bullpen", {}).get("score"),
            "score_advanced": agents.get("advanced", {}).get("score"),
            "score_momentum": agents.get("momentum", {}).get("score"),
            "score_market": agents.get("market", {}).get("score"),
            "score_weather": agents.get("weather", {}).get("score"),
        })
    print(f"[DB] Updated {len(analyses)} game analyses in analysis_log.")

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

        # Skip games that have already started — no actionable update possible
        game_status = refreshed.get("status", "Scheduled")
        pre_game_statuses = {"Scheduled", "Pre-Game", "Warmup", "Delayed Start"}
        if game_status not in pre_game_statuses:
            print(f"  ⏭️  {refreshed['game']} — skipping ({game_status})")
            continue

        prior_conf = conn_pick.get("confidence", 0)
        pick_type = conn_pick.get("pick_type", "moneyline")
        if pick_type in ("over", "under"):
            ou = refreshed.get("ou_pick") or {}
            new_conf = ou.get("confidence") or refreshed["ml_confidence"]
        else:
            new_conf = refreshed["ml_confidence"]
        still_approved = (mlb_game_id, pick_type) in approved_by_type

        # ── Line movement warning ──
        opening = db.get_opening_lines(mlb_game_id, date.today().isoformat())
        line_moved_against = False
        line_move_desc = ""
        if opening:
            cur_consensus = refreshed.get("agents", {}).get("market", {}).get("detail", {})
            if pick_type == "moneyline":
                pick_side = "home" if conn_pick.get("pick_team") == refreshed.get("home_team") else "away"
                if pick_side == "home" and opening.get("home_ml") and cur_consensus.get("home_ml"):
                    open_prob = implied_probability(opening["home_ml"])
                    cur_prob = implied_probability(cur_consensus["home_ml"])
                    if open_prob - cur_prob >= 0.05:
                        line_moved_against = True
                        line_move_desc = (f"Home ML moved from {opening['home_ml']} to "
                                         f"{cur_consensus['home_ml']} — sharp money on away")
                elif pick_side == "away" and opening.get("away_ml") and cur_consensus.get("away_ml"):
                    open_prob = implied_probability(opening["away_ml"])
                    cur_prob = implied_probability(cur_consensus["away_ml"])
                    if open_prob - cur_prob >= 0.05:
                        line_moved_against = True
                        line_move_desc = (f"Away ML moved from {opening['away_ml']} to "
                                         f"{cur_consensus['away_ml']} — sharp money on home")
            elif pick_type in ("over", "under"):
                open_total = opening.get("total_line")
                cur_total = cur_consensus.get("total_line")
                if open_total and cur_total:
                    if pick_type == "over" and cur_total >= open_total + 0.5:
                        line_moved_against = True
                        line_move_desc = f"Total moved from {open_total} to {cur_total} — line went against OVER"
                    elif pick_type == "under" and cur_total <= open_total - 0.5:
                        line_moved_against = True
                        line_move_desc = f"Total moved from {open_total} to {cur_total} — line went against UNDER"

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
            if line_moved_against:
                update = {
                    "game": refreshed["game"],
                    "original_pick": conn_pick.get("pick_team", "?"),
                    "update": "Line has moved against pick — sharp money disagrees",
                    "action": "Watch",
                    "reason": line_move_desc,
                }
                send_update(update)
                print(f"  ⚠️  Line movement alert: {refreshed['game']} — {line_move_desc}")
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


def _parse_total_line(notes: str) -> Optional[float]:
    """Extract numeric total line from notes field.
    Handles formats like:
      'Total line: 8.5'
      'Total line: 8.5 | Lineups confirmed'
      'Total line: 8.5 | Lineup TBD — monitor before first pitch'
    Returns None if not found or value is outside plausible range (5–15).
    """
    import re
    match = re.search(r"Total line:\s*([\d.]+)", notes or "")
    if not match:
        return None
    val = float(match.group(1))
    if not (5.0 <= val <= 15.0):
        print(f"[WARNING] _parse_total_line: implausible total line {val} in notes: {notes!r}")
        return None
    return val


def _fetch_verified_score(mlb_game_id: int) -> Optional[dict]:
    """
    Cross-check score via the individual game boxscore endpoint.
    Returns {away_score, home_score} or None on error / game not final.
    Used to validate schedule-endpoint scores before grading.
    """
    try:
        url = f"https://statsapi.mlb.com/api/v1/game/{mlb_game_id}/boxscore"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        bs = resp.json()
        away = bs.get("teams", {}).get("away", {}).get("teamStats", {}).get("batting", {})
        home = bs.get("teams", {}).get("home", {}).get("teamStats", {}).get("batting", {})
        away_r = away.get("runs")
        home_r = home.get("runs")
        if away_r is None or home_r is None:
            return None
        return {"away_score": int(away_r), "home_score": int(home_r)}
    except Exception as e:
        print(f"[DATA] Score cross-check error for game {mlb_game_id}: {e}")
        return None


def _fetch_f5_linescore(mlb_game_id: int) -> dict:
    """Fetch linescore for a completed game. Returns {} on error."""
    try:
        url = f"https://statsapi.mlb.com/api/v1/game/{mlb_game_id}/linescore"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[DATA] Error fetching linescore for game {mlb_game_id}: {e}")
        return {}


def _grade_f5_pick(pick_type: str, linescore: dict) -> str:
    """
    Grade an F5 ML pick.
    pick_type: 'f5_home' or 'f5_away'
    Returns 'won', 'lost', or 'push'.
    """
    innings = linescore.get("innings", [])
    away_f5 = sum(
        (inn.get("away", {}).get("runs") or 0)
        for inn in innings if 1 <= inn.get("num", 0) <= 5
    )
    home_f5 = sum(
        (inn.get("home", {}).get("runs") or 0)
        for inn in innings if 1 <= inn.get("num", 0) <= 5
    )

    if away_f5 == home_f5:
        return "push"
    if pick_type == "f5_home":
        return "won" if home_f5 > away_f5 else "lost"
    elif pick_type == "f5_away":
        return "won" if away_f5 > home_f5 else "lost"
    return "push"


def run_results():
    """
    RESULTS GRADING
    Fetch final scores, grade picks, calculate ROI, send recap.

    Day-boundary fallback: if the Mac wakes after midnight and today has no final
    games yet but yesterday has pending picks, grade yesterday automatically.
    """
    db.init_db()

    # Resolve the grading date — today first, fall back to yesterday if needed
    today_str = date.today().isoformat()
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()

    games = fetch_todays_games(today_str)
    final_games = [g for g in games if "Final" in g.get("status", "")]

    grading_date = today_str
    if not final_games:
        # Check if yesterday has pending picks that never got graded
        yesterday_pending = db.get_pending_picks_for_date(yesterday_str)
        if yesterday_pending:
            print(f"⚠️  No final games today — found {len(yesterday_pending)} ungraded picks "
                  f"from {yesterday_str}. Grading yesterday instead.")
            games = fetch_todays_games(yesterday_str)
            final_games = [g for g in games if "Final" in g.get("status", "")]
            grading_date = yesterday_str
        if not final_games:
            print("=" * 60)
            print(f"  MLB RESULTS — {date.today().strftime('%B %d, %Y')}")
            print("=" * 60)
            print("\n⚠️  No final games found yet. Run again after games complete.")
            return

    print("=" * 60)
    print(f"  MLB RESULTS — {date.fromisoformat(grading_date).strftime('%B %d, %Y')}")
    print("=" * 60)

    # Get picks for the resolved grading date
    picks = db.get_picks_for_date(grading_date)
    if not picks:
        print("\nNo picks were made today — grading model accuracy only.")

    wins, losses, pushes = 0, 0, 0
    best_pick = None
    worst_miss = None
    pick_lines = []  # list of (emoji, label) for Discord

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

        # Grade the pick — cross-check score via boxscore endpoint
        away_score = result.get("away_score", 0) or 0
        home_score = result.get("home_score", 0) or 0
        verified = _fetch_verified_score(mlb_game_id)
        if verified:
            if verified["away_score"] != away_score or verified["home_score"] != home_score:
                print(f"  ⚠️  Score mismatch game {mlb_game_id}: "
                      f"schedule={away_score}-{home_score}, "
                      f"boxscore={verified['away_score']}-{verified['home_score']} → using boxscore")
                away_score = verified["away_score"]
                home_score = verified["home_score"]
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
            is_tie = home_score == away_score

            if pick["pick_team"] == result.get("home_team_name"):
                status = "won" if home_won else ("push" if is_tie else "lost")
            elif pick["pick_team"] == result.get("away_team_name"):
                status = "won" if away_won else ("push" if is_tie else "lost")
            else:
                status = "push"

        elif pick["pick_type"] == "over":
            total_line = _parse_total_line(pick.get("notes", ""))
            if total_line is None:
                print(f"  ⚠️  Could not parse total line for pick {pick['id']} — skipping grading (stays pending)")
                continue
            elif total_runs > total_line:
                status = "won"
            elif total_runs < total_line:
                status = "lost"
            else:
                status = "push"

        elif pick["pick_type"] == "under":
            total_line = _parse_total_line(pick.get("notes", ""))
            if total_line is None:
                print(f"  ⚠️  Could not parse total line for pick {pick['id']} — skipping grading (stays pending)")
                continue
            elif total_runs < total_line:
                status = "won"
            elif total_runs > total_line:
                status = "lost"
            else:
                status = "push"

        elif pick["pick_type"] == "f5_ml":
            # Fetch linescore to get F5 (innings 1-5) scores
            linescore = _fetch_f5_linescore(mlb_game_id)
            if linescore:
                # Determine pick direction from pick_team vs home/away team name
                home_name = result.get("home_team_name", "")
                if pick.get("pick_team") == home_name:
                    f5_direction = "f5_home"
                else:
                    f5_direction = "f5_away"
                status = _grade_f5_pick(f5_direction, linescore)
            else:
                status = "push"  # can't grade without linescore

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

        emoji = "✅" if status == "won" else "❌" if status == "lost" else "➖"
        pick_type = pick["pick_type"]
        away_name = result.get("away_team_name", "?")
        home_name = result.get("home_team_name", "?")
        matchup = f"{away_name} @ {home_name}"
        final_score = f"Final: {away_score}-{home_score}"

        if pick_type in ("over", "under"):
            ou_line = pick.get("notes", "").replace("Total line: ", "O/U ").split("|")[0].strip()
            model_pick = f"Model: {pick_type.upper()} {ou_line}"
            score_context = f"{final_score}, Total: {total_runs}"
            label = f"**{ou_line}** — {matchup} ({score_context}) — {model_pick}"
        elif pick_type == "f5_ml":
            model_pick = f"Model: {pick['pick_team']} F5 Win"
            label = f"**{pick['pick_team']} F5 ML** — {matchup} ({final_score}) — {model_pick}"
        else:
            model_pick = f"Model: {pick['pick_team']} ML Win"
            label = f"**{pick['pick_team']} ML** — {matchup} ({final_score}) — {model_pick}"

        pick_lines.append(f"{emoji} {label}")

        print(f"  {emoji} {pick['pick_team']} ({pick_type}) — {status.upper()}")

    conn.close()

    # ── Grade analysis_log entries ──
    log_entries = db.get_analysis_log_for_date(grading_date)
    log_correct = 0
    log_incorrect = 0
    log_ou_correct = 0
    log_ou_incorrect = 0

    for entry in log_entries:
        if entry["ml_status"] != "pending":
            continue

        mlb_game_id = entry["mlb_game_id"]
        result = next((fg for fg in final_games if fg["mlb_game_id"] == mlb_game_id), None)
        if not result:
            continue

        away_score = result.get("away_score", 0) or 0
        home_score = result.get("home_score", 0) or 0
        verified = _fetch_verified_score(mlb_game_id)
        if verified:
            if verified["away_score"] != away_score or verified["home_score"] != home_score:
                print(f"  ⚠️  Score mismatch game {mlb_game_id}: "
                      f"schedule={away_score}-{home_score}, "
                      f"boxscore={verified['away_score']}-{verified['home_score']} → using boxscore")
                away_score = verified["away_score"]
                home_score = verified["home_score"]
        total_runs = away_score + home_score

        # Grade ML prediction
        home_won = home_score > away_score
        away_won = away_score > home_score
        if entry["ml_pick_team"] == result.get("home_team_name"):
            ml_status = "correct" if home_won else ("push" if not away_won else "incorrect")
        elif entry["ml_pick_team"] == result.get("away_team_name"):
            ml_status = "correct" if away_won else ("push" if not home_won else "incorrect")
        else:
            ml_status = "push"

        # Grade O/U prediction
        ou_status = "none"
        if entry.get("ou_pick") and entry.get("ou_line"):
            ou_line = float(entry["ou_line"])
            if entry["ou_pick"] == "over":
                ou_status = "correct" if total_runs > ou_line else ("push" if total_runs == ou_line else "incorrect")
            elif entry["ou_pick"] == "under":
                ou_status = "correct" if total_runs < ou_line else ("push" if total_runs == ou_line else "incorrect")

        db.update_analysis_log_result(
            entry["id"], ml_status=ml_status, ou_status=ou_status,
            actual_away=away_score, actual_home=home_score, actual_total=total_runs
        )

        if ml_status == "correct":
            log_correct += 1
        elif ml_status == "incorrect":
            log_incorrect += 1
        if ou_status == "correct":
            log_ou_correct += 1
        elif ou_status == "incorrect":
            log_ou_incorrect += 1

    log_total = log_correct + log_incorrect
    print(f"\n  Model Accuracy (all {len(log_entries)} games):")
    print(f"  ML: {log_correct}W {log_incorrect}L  ({round(log_correct/log_total*100,1) if log_total else 0}%)")
    print(f"  O/U: {log_ou_correct}W {log_ou_incorrect}L")

    total = wins + losses

    if total == 0 and pushes == 0:
        print("\nNo picks were newly graded (all already resolved or no final scores matched).")
        if not log_entries:
            print("[RESULTS] No analysis log entries for today — skipping nightly report.")
            return
        # Still send the nightly report using already-graded data from the DB
        _sent_picks_by_game: dict = {}
        for p in picks:
            gid = p["game_id"]
            if gid not in _sent_picks_by_game:
                _sent_picks_by_game[gid] = []
            _sent_picks_by_game[gid].append(p)
        _mlb_to_local: dict = {}
        _conn = db.get_connection()
        for entry in log_entries:
            mid = entry.get("mlb_game_id")
            if mid and mid not in _mlb_to_local:
                game_row = _conn.execute(
                    "SELECT id FROM games WHERE mlb_game_id=?", (mid,)
                ).fetchone()
                if game_row:
                    _mlb_to_local[mid] = game_row["id"]
        _results = {
            "wins": wins, "losses": losses, "pushes": pushes, "roi": 0.0,
            "best_pick": "N/A", "worst_miss": "N/A", "notes": "0 newly graded picks",
            "pick_lines": [], "all_games_lines": [],
            "ml_correct": log_correct, "ml_incorrect": log_incorrect,
            "ou_correct": log_ou_correct, "ou_incorrect": log_ou_incorrect,
        }
        if db.is_daily_report_sent(grading_date):
            print(f"[REPORT] Nightly report already sent for {grading_date} — skipping.")
        else:
            if send_nightly_report(_results, log_entries, _sent_picks_by_game, _mlb_to_local):
                db.mark_daily_report_sent(grading_date)
        return

    roi = round((wins - losses) / max(total, 1) * 100, 1)

    # ── Build full-day game board for Discord ──
    # Index sent picks by game_id for quick lookup
    sent_picks_by_game = {}
    for p in picks:
        gid = p["game_id"]
        if gid not in sent_picks_by_game:
            sent_picks_by_game[gid] = []
        sent_picks_by_game[gid].append(p)

    # Build mlb_game_id -> local game_id map for nightly report
    mlb_to_local = {}
    conn = db.get_connection()
    for p in picks:
        game_row = conn.execute(
            "SELECT id, mlb_game_id FROM games WHERE id=?", (p["game_id"],)
        ).fetchone()
        if game_row:
            mlb_to_local[game_row["mlb_game_id"]] = game_row["id"]
    # Also cover games in log_entries that had no sent pick
    for entry in log_entries:
        mid = entry.get("mlb_game_id")
        if mid and mid not in mlb_to_local:
            game_row = conn.execute(
                "SELECT id FROM games WHERE mlb_game_id=?", (mid,)
            ).fetchone()
            if game_row:
                mlb_to_local[mid] = game_row["id"]
    conn.close()

    all_games_lines = []
    for entry in sorted(log_entries, key=lambda e: e.get("away_team", "")):
        mlb_game_id = entry["mlb_game_id"]
        result = next((fg for fg in final_games if fg["mlb_game_id"] == mlb_game_id), None)
        if not result:
            continue

        away_name = result.get("away_team_name", entry.get("away_team", "?"))
        home_name = result.get("home_team_name", entry.get("home_team", "?"))
        away_sc = result.get("away_score", 0) or 0
        home_sc = result.get("home_score", 0) or 0
        # Use verified scores if available (already updated in DB above)
        total_sc = away_sc + home_sc
        matchup = f"{away_name} @ {home_name}"
        score_str = f"{away_sc}-{home_sc}"

        # ML model result
        ml_team_full = entry.get("ml_pick_team", "?")
        ml_team_short = ml_team_full.split()[-1] if ml_team_full else "?"
        ml_conf = entry.get("ml_confidence", 0)
        ml_status = entry.get("ml_status", "pending")
        if ml_status == "correct":
            ml_emoji = "✅"
        elif ml_status == "incorrect":
            ml_emoji = "❌"
        else:
            ml_emoji = "➖"

        # Was an ML pick actually sent for this game?
        game_row = db.get_connection().execute(
            "SELECT id FROM games WHERE mlb_game_id=?", (mlb_game_id,)
        ).fetchone()
        game_id = game_row["id"] if game_row else None
        game_sent_picks = sent_picks_by_game.get(game_id, []) if game_id else []
        ml_sent = any(p["pick_type"] in ("moneyline", "f5_ml") for p in game_sent_picks)
        pick_flag = " 🎯" if ml_sent else ""

        line = (
            f"{ml_emoji} **{matchup}** — Final: {score_str} | "
            f"{ml_team_short} WIN {ml_conf}/10{pick_flag}"
        )

        # O/U model result (if model had an O/U call)
        ou_pick = entry.get("ou_pick")
        ou_line_val = entry.get("ou_line")
        ou_status = entry.get("ou_status", "none")
        if ou_pick and ou_line_val:
            if ou_status == "correct":
                ou_emoji = "✅"
            elif ou_status == "incorrect":
                ou_emoji = "❌"
            else:
                ou_emoji = "➖"
            ou_sent = any(p["pick_type"] in ("over", "under") for p in game_sent_picks)
            ou_flag = " 🎯" if ou_sent else ""
            if ou_status == "correct":
                result_marker = "✓"
            elif ou_status == "incorrect":
                result_marker = "✗"
            else:
                result_marker = "push"
            line += f" | {ou_emoji} {ou_pick.upper()} {ou_line_val} → Total: {total_sc} {result_marker}{ou_flag}"

        all_games_lines.append(line)

    # Sent picks summary stays as the header section; full board goes after
    results = {
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "roi": roi,
        "best_pick": best_pick.get("pick_team", "N/A") if best_pick else "N/A",
        "worst_miss": worst_miss.get("pick_team", "N/A") if worst_miss else "N/A",
        "notes": f"{total} graded picks",
        "pick_lines": pick_lines,
        "all_games_lines": all_games_lines,
        "ml_correct": log_correct,
        "ml_incorrect": log_incorrect,
        "ou_correct": log_ou_correct,
        "ou_incorrect": log_ou_incorrect,
    }

    db.save_daily_results(results)

    print(f"\n  Record: {wins}W - {losses}L - {pushes}P  |  ROI: {roi}%")

    # ── Send nightly report to Discord ──
    if db.is_daily_report_sent(grading_date):
        print(f"[REPORT] Nightly report already sent for {grading_date} — skipping.")
    else:
        if send_nightly_report(results, log_entries, sent_picks_by_game, mlb_to_local):
            db.mark_daily_report_sent(grading_date)

    # ── Collect and store post-game boxscore data ──
    print("\n[DATA] Collecting post-game boxscore data...")
    from data_mlb import collect_boxscores, collect_game_totals
    boxscore_data = collect_boxscores(grading_date)
    if boxscore_data["pitcher_logs"] or boxscore_data["team_logs"]:
        db.store_boxscores(boxscore_data["pitcher_logs"], boxscore_data["team_logs"])
        print(f"[DB] Stored {len(boxscore_data['pitcher_logs'])} pitcher lines, "
              f"{len(boxscore_data['team_logs'])} team logs.")
    else:
        print("[DATA] No boxscore data collected.")

    # ── Collect and store game totals (O/U bias tracking) ──
    totals = collect_game_totals(grading_date)
    if totals:
        db.store_game_totals(totals)
        print(f"[DB] Stored {len(totals)} game total rows for O/U bias tracking.")

    # ── Collect and store per-batter boxscore data ──
    print("[DATA] Collecting per-batter boxscore data...")
    batter_inserted = db.collect_batter_boxscores(grading_date)
    print(f"[DB] Stored {batter_inserted} batter game log rows.")


def run_game_analysis(query: str):
    """
    TARGETED GAME ANALYSIS
    Run the full 7-agent pipeline on any game(s) matching the query string
    and send each analysis to Discord. Does not apply confidence thresholds
    or send approved picks — pure analysis only.

    Query is matched case-insensitively against away_team_name and home_team_name.
    Multiple space-separated tokens all have to match somewhere in the game.
    """
    print("=" * 60)
    print(f"  MLB GAME ANALYSIS — {date.today().strftime('%B %d, %Y')}")
    print(f"  Query: \"{query}\"")
    print("=" * 60)

    db.init_db()
    fetch_all_teams()
    games = collect_game_data()

    if not games:
        print("\n⚠️  No games found for today.")
        return

    tokens = query.lower().split()
    matches = []
    for g in games:
        game_str = f"{g.get('away_team_name','')} {g.get('home_team_name','')}".lower()
        if all(t in game_str for t in tokens):
            matches.append(g)

    if not matches:
        print(f"\n⚠️  No games found matching \"{query}\".")
        print("Today's games:")
        for g in games:
            print(f"  - {g.get('away_team_name','')} @ {g.get('home_team_name','')}")
        return

    print(f"\nFound {len(matches)} matching game(s). Running full analysis...\n")

    odds_list = fetch_odds()

    for g in matches:
        odds_data = match_odds_to_game(odds_list, g["home_team_name"], g["away_team_name"])
        a = analyze_game(g, odds_data)

        agents = a.get("agents", {})
        away = a["away_team"]
        home = a["home_team"]

        def agent_score(key):
            return f"{agents.get(key, {}).get('score', 0):+.3f}"

        def agent_detail(key):
            ag = agents.get(key, {})
            return ag.get("edge", ag.get("detail", {}).get("note", "N/A"))

        # Odds block
        mkt = agents.get("market", {}).get("detail", {})

        def fmt_ml(v):
            if v is None: return "N/A"
            return f"+{int(v)}" if v > 0 else str(int(v))

        def fmt_pt(v):
            if v is None: return "N/A"
            return f"{v:+.1f}" if v % 1 != 0 else f"{v:+.0f}"

        odds_lines = []
        away_ml = mkt.get("away_ml"); home_ml = mkt.get("home_ml")
        if away_ml and home_ml:
            odds_lines.append(f"- ML: {away} {fmt_ml(away_ml)} / {home} {fmt_ml(home_ml)}")
        home_rl = mkt.get("home_rl"); away_rl = mkt.get("away_rl")
        hrp = mkt.get("home_rl_price"); arp = mkt.get("away_rl_price")
        if home_rl is not None and hrp and arp:
            aw_rl = away_rl if away_rl is not None else -home_rl
            odds_lines.append(
                f"- RL: {away} {fmt_pt(aw_rl)} ({fmt_ml(arp)}) / "
                f"{home} {fmt_pt(home_rl)} ({fmt_ml(hrp)})"
            )
        total = mkt.get("total_line"); op = mkt.get("over_price"); up_p = mkt.get("under_price")
        if total:
            o_str = f" O {fmt_ml(op)}" if op else ""
            u_str = f" U {fmt_ml(up_p)}" if up_p else ""
            odds_lines.append(f"- Total: {total}{o_str} /{u_str}")
        odds_block = "\n".join(odds_lines) if odds_lines else "N/A"

        # O/U signal
        ou = a.get("ou_pick", {})
        ou_line = ""
        if ou.get("pick"):
            ou_line = (
                f"\n**O/U Signal:** {ou['pick'].upper()} {ou.get('line','?')} — "
                f"conf {ou.get('confidence','?')}/10 | {ou.get('edge','')}"
            )

        # Status label
        conf = a["ml_confidence"]
        if conf >= 7:
            status_line = f"✅ **APPROVED PICK** — {a['ml_pick_team']} ML"
        elif conf >= 5:
            status_line = f"👁 **WATCHLIST** — {a['ml_pick_team']} ML (monitor lineups/lines)"
        else:
            status_line = f"📊 **ANALYSIS ONLY** — below threshold"

        game_time_str = _format_game_time(a.get("game_time_utc", ""))
        game_time_line = f"**Date/Time:** {game_time_str}\n" if game_time_str else ""

        msg = (
            f"⚾ **MLB FULL GAME ANALYSIS — {date.today().strftime('%B %d, %Y')}**\n"
            f"\n"
            f"**{a['game']}**\n"
            f"{game_time_line}"
            f"**Pitchers:** {a['away_pitcher']} ({away}) vs {a['home_pitcher']} ({home})\n"
            f"\n"
            f"{status_line}\n"
            f"**Confidence:** {conf}/10  |  **Win Prob:** {a['ml_win_probability']}%\n"
            f"**Composite Score:** {a['composite_score']:+.3f}\n"
            f"**Projected Score:** {away} {a['projected_away_score']} — {home} {a['projected_home_score']}"
            f"{ou_line}\n"
            f"\n"
            f"**Current Odds:**\n{odds_block}\n"
            f"\n"
            f"**Agent Breakdown:**\n"
            f"- Pitching ({agent_score('pitching')}): {agent_detail('pitching')}\n"
            f"- Offense ({agent_score('offense')}): {agent_detail('offense')}\n"
            f"- Advanced/Statcast ({agent_score('advanced')}): {agent_detail('advanced')}\n"
            f"- Bullpen ({agent_score('bullpen')}): {agent_detail('bullpen')}\n"
            f"- Momentum ({agent_score('momentum')}): {agent_detail('momentum')}\n"
            f"- Weather ({agent_score('weather')}): {agent_detail('weather')}\n"
            f"- Market ({agent_score('market')}): {agent_detail('market')}\n"
        )

        print(msg)

        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=10)
        if resp.status_code == 204:
            print(f"[DISCORD] Sent: {a['game']}")
        else:
            print(f"[DISCORD] Failed ({resp.status_code}): {resp.text}")

    print("\n✅ Game analysis complete.")


def _print_snapshot():
    """Print pick accuracy + model accuracy tracking snapshot."""
    pick_summary = db.get_roi_summary(30)
    model_summary = db.get_model_accuracy_summary(30)

    print("\n" + "-" * 40)
    print("  TRACKING SNAPSHOT (Last 30 Days)")
    print("-" * 40)
    net = pick_summary.get("net_units")
    roi_str = f"  {net:+.3f} units" if net is not None else ""
    print(f"  PICKS SENT:  {pick_summary['won']}W - {pick_summary['lost']}L - {pick_summary['push']}P  "
          f"({pick_summary['win_rate']}% win rate){roi_str}  [{pick_summary['total']} graded]")
    print(f"  MODEL ML:    {model_summary['ml_correct']}W - {model_summary['ml_incorrect']}L  "
          f"({model_summary['ml_accuracy']}% accuracy)  [{model_summary['ml_total']} games]")
    print(f"  MODEL O/U:   {model_summary['ou_correct']}W - {model_summary['ou_incorrect']}L  "
          f"({model_summary['ou_accuracy']}% accuracy)  [{model_summary['ou_total']} games]")


def run_report(target_date: Optional[str] = None) -> None:
    """
    Re-send the nightly report for a given date (default: today).
    Queries graded analysis_log and sent picks from the DB.
    """
    db.init_db()
    grade_date = target_date or date.today().isoformat()
    conn = db.get_connection()

    log_entries = db.get_analysis_log_for_date(grade_date)
    if not log_entries:
        print(f"[REPORT] No analysis log entries for {grade_date}.")
        return

    picks = db.get_picks_for_date(grade_date)

    # Build sent_picks_by_game
    sent_picks_by_game = {}
    for p in picks:
        gid = p["game_id"]
        if gid not in sent_picks_by_game:
            sent_picks_by_game[gid] = []
        sent_picks_by_game[gid].append(p)

    # Build mlb_to_local
    mlb_to_local = {}
    for p in picks:
        game_row = conn.execute(
            "SELECT id, mlb_game_id FROM games WHERE id=?", (p["game_id"],)
        ).fetchone()
        if game_row:
            mlb_to_local[game_row["mlb_game_id"]] = game_row["id"]
    for entry in log_entries:
        mid = entry.get("mlb_game_id")
        if mid and mid not in mlb_to_local:
            game_row = conn.execute(
                "SELECT id FROM games WHERE mlb_game_id=?", (mid,)
            ).fetchone()
            if game_row:
                mlb_to_local[mid] = game_row["id"]

    # Compute summary stats from graded entries
    wins = sum(1 for p in picks if p.get("status") == "won")
    losses = sum(1 for p in picks if p.get("status") == "lost")
    pushes = sum(1 for p in picks if p.get("status") == "push")
    roi = round((wins - losses) / max(wins + losses, 1) * 100, 1)
    ml_correct = sum(1 for e in log_entries if e.get("ml_status") == "correct")
    ml_incorrect = sum(1 for e in log_entries if e.get("ml_status") == "incorrect")
    ou_correct = sum(1 for e in log_entries if e.get("ou_status") == "correct")
    ou_incorrect = sum(1 for e in log_entries if e.get("ou_status") == "incorrect")

    results = {
        "wins": wins, "losses": losses, "pushes": pushes, "roi": roi,
        "ml_correct": ml_correct, "ml_incorrect": ml_incorrect,
        "ou_correct": ou_correct, "ou_incorrect": ou_incorrect,
        "pick_lines": [],
    }

    if db.is_daily_report_sent(grade_date):
        print(f"[REPORT] Nightly report already sent for {grade_date} — skipping.")
    else:
        if send_nightly_report(results, log_entries, sent_picks_by_game, mlb_to_local):
            db.mark_daily_report_sent(grade_date)


def main():
    args = sys.argv[1:]

    if "--collect" in args:
        idx = args.index("--collect")
        target = args[idx + 1] if idx + 1 < len(args) else date.today().isoformat()
        db.init_db()
        print(f"[DATA] Collecting boxscores for {target}...")
        from data_mlb import collect_boxscores, collect_game_totals
        boxscore_data = collect_boxscores(target)
        db.store_boxscores(boxscore_data["pitcher_logs"], boxscore_data["team_logs"])
        print(f"[DB] Stored {len(boxscore_data['pitcher_logs'])} pitcher lines, "
              f"{len(boxscore_data['team_logs'])} team logs for {target}.")
        totals = collect_game_totals(target)
        db.store_game_totals(totals)
        print(f"[DB] Stored {len(totals)} game total rows for {target}.")
        batter_inserted = db.collect_batter_boxscores(target)
        print(f"[DB] Stored {batter_inserted} batter game log rows for {target}.")
        return

    if "--backfill-totals" in args:
        idx = args.index("--backfill-totals")
        # Optional: --backfill-totals 2026-04-01 2026-04-15
        start = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("--") else None
        end = args[idx + 2] if start and idx + 2 < len(args) and not args[idx + 2].startswith("--") else None
        db.init_db()
        from data_mlb import backfill_game_totals
        print(f"[DATA] Backfilling game totals ({start or 'season start'} → {end or 'yesterday'})...")
        count = backfill_game_totals(start_date=start, end_date=end)
        print(f"[DB] Backfill complete: {count} games stored.")
        return

    if "--results" in args:
        run_results()
    elif "--report" in args:
        idx = args.index("--report")
        target = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("--") else None
        run_report(target)
    elif "--refresh" in args:
        run_refresh()
    elif "--status" in args:
        db.init_db()
        _print_snapshot()
    elif "--test" in args:
        run_analysis(dry_run=True)
    elif "--game" in args:
        idx = args.index("--game")
        query = args[idx + 1] if idx + 1 < len(args) else ""
        if not query:
            print("Usage: python3 engine.py --game <team name>")
            sys.exit(1)
        run_game_analysis(query)
    else:
        run_analysis(dry_run=False)


if __name__ == "__main__":
    main()
