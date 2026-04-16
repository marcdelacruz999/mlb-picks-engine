"""
Pitcher scratch monitor — checks active picks for SP changes.
Run every 30 minutes between 8am and first pitch via launchd (plist created separately).
"""
import requests
import database
import data_mlb as data_mlb_module
from datetime import date
from database import get_connection, get_today_picks, get_today_analysis_log, pitcher_already_alerted, save_scratch_alert, lineup_alert_already_sent, save_lineup_alert, get_batter_rolling_ops
from config import DISCORD_WEBHOOK_URL, LINEUP_OPS_DROP_THRESHOLD, LINEUP_MIN_PLAYERS_WITH_DATA, MIN_BATTER_GAMES
from data_mlb import get_current_lineups, fetch_lineup_batting, fetch_team_batting

MLB_API = "https://statsapi.mlb.com/api/v1"


def get_current_pitchers(mlb_game_id):
    """Fetch current probable pitchers for a game from MLB Stats API.
    Returns {"away": "Name" or None, "home": "Name" or None}.
    """
    url = f"{MLB_API}/schedule?gamePks={mlb_game_id}&hydrate=probablePitcher"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[MONITOR] Error fetching pitchers for game {mlb_game_id}: {e}")
        return {"away": None, "home": None}

    try:
        dates = data.get("dates", [])
        if not dates:
            return {"away": None, "home": None}
        games = dates[0].get("games", [])
        if not games:
            return {"away": None, "home": None}
        game = games[0]
        teams = game.get("teams", {})

        away_pitcher = None
        home_pitcher = None

        away_prob = teams.get("away", {}).get("probablePitcher")
        if away_prob:
            away_pitcher = away_prob.get("fullName")

        home_prob = teams.get("home", {}).get("probablePitcher")
        if home_prob:
            home_pitcher = home_prob.get("fullName")

        return {"away": away_pitcher, "home": home_pitcher}
    except Exception as e:
        print(f"[MONITOR] Error parsing pitcher data for game {mlb_game_id}: {e}")
        return {"away": None, "home": None}


def _normalize(name):
    """Normalize pitcher name for comparison."""
    if name is None:
        return None
    return " ".join(name.split()).lower()


def send_scratch_alert(game_label, old_pitcher, new_pitcher):
    """Send a pitcher scratch alert to Discord."""
    message = (
        f"⚠️ **PITCHER SCRATCH ALERT** — {game_label}\n"
        f"Original pick based on: {old_pitcher}\n"
        f"New probable: {new_pitcher}\n"
        f"Consider revisiting this pick."
    )
    if not DISCORD_WEBHOOK_URL:
        print(f"[MONITOR] No webhook URL — alert:\n{message}")
        return False
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10)
        if resp.ok:
            print(f"[MONITOR] Scratch alert sent for {game_label}")
            return True
        else:
            print(f"[MONITOR] Discord failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[MONITOR] Error sending scratch alert: {e}")
        return False


def send_lineup_alert(game_label: str, pick_team: str, ops_actual: float, ops_expected: float, pct_drop: float, confidence: int) -> bool:
    """Send a lineup weakness alert to Discord."""
    pct_str = f"{pct_drop * 100:.1f}%"
    message = (
        f"⚠️ **LINEUP ALERT** — {game_label}\n"
        f"{pick_team} confirmed lineup OPS: {ops_actual:.3f} "
        f"(expected {ops_expected:.3f} — {pct_str} weaker)\n"
        f"Pick: {pick_team} ML {confidence}/10 — consider revisiting."
    )
    if not DISCORD_WEBHOOK_URL:
        print(f"[MONITOR] No webhook URL — lineup alert:\n{message}")
        return False
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10)
        if resp.ok:
            print(f"[MONITOR] Lineup alert sent for {game_label}")
            return True
        else:
            print(f"[MONITOR] Discord failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[MONITOR] Error sending lineup alert: {e}")
        return False


def run_monitor():
    """Check all today's active picks for pitcher changes."""
    # 1. Load today's sent picks (discord_sent=1)
    picks = get_today_picks()
    pending_picks = [p for p in picks if p.get("status") == "pending"]

    if not pending_picks:
        print("[MONITOR] No pending picks to monitor.")
        return

    today = date.today().isoformat()

    # 2. Load analysis_log for today to get original pitcher names
    log_entries = get_today_analysis_log()
    log_by_game_id = {entry["mlb_game_id"]: entry for entry in log_entries}

    # 3. Collect unique mlb_game_ids from pending picks via games table
    conn = get_connection()
    game_ids_to_check = set()
    pick_game_map = {}  # mlb_game_id -> game_id (local)

    try:
        for pick in pending_picks:
            game_id = pick["game_id"]
            game_row = conn.execute(
                "SELECT mlb_game_id FROM games WHERE id=?", (game_id,)
            ).fetchone()
            if game_row:
                mlb_id = game_row["mlb_game_id"]
                game_ids_to_check.add(mlb_id)
                pick_game_map[mlb_id] = game_id
    finally:
        conn.close()

    if not game_ids_to_check:
        print("[MONITOR] Could not resolve mlb_game_ids for pending picks.")
        return

    print(f"[MONITOR] Checking {len(game_ids_to_check)} game(s) for pitcher changes...")

    # 4. For each game, compare current pitcher to stored pitcher
    for mlb_game_id in game_ids_to_check:
        log = log_by_game_id.get(mlb_game_id)
        if not log:
            print(f"[MONITOR] No analysis_log entry for game {mlb_game_id} — skipping.")
            continue

        stored_away = log.get("away_pitcher")
        stored_home = log.get("home_pitcher")
        game_label = log.get("game", str(mlb_game_id))

        current = get_current_pitchers(mlb_game_id)
        current_away = current.get("away")
        current_home = current.get("home")

        # Check away pitcher
        away_changed = False
        if (current_away is not None and
                _normalize(current_away) != _normalize(stored_away) and
                _normalize(stored_away) not in (None, "tbd")):
            away_changed = True
            if not pitcher_already_alerted(mlb_game_id, today, side='away'):
                print(f"[MONITOR] Away pitcher changed for {game_label}: "
                      f"{stored_away} -> {current_away}")
                sent = send_scratch_alert(game_label, stored_away, current_away)
                if sent:
                    save_scratch_alert(mlb_game_id, today, stored_away, current_away, side='away')
            else:
                print(f"[MONITOR] Already alerted for away pitcher {game_label} — skipping.")

        # Check home pitcher
        home_changed = False
        if (current_home is not None and
                _normalize(current_home) != _normalize(stored_home) and
                _normalize(stored_home) not in (None, "tbd")):
            home_changed = True
            if not pitcher_already_alerted(mlb_game_id, today, side='home'):
                print(f"[MONITOR] Home pitcher changed for {game_label}: "
                      f"{stored_home} -> {current_home}")
                sent = send_scratch_alert(game_label, stored_home, current_home)
                if sent:
                    save_scratch_alert(mlb_game_id, today, stored_home, current_home, side='home')
            else:
                print(f"[MONITOR] Already alerted for home pitcher {game_label} — skipping.")

        if not away_changed and not home_changed:
            print(f"[MONITOR] No pitcher change detected for {game_label}.")


def run_lineup_monitor():
    """Check pending picks for lineup weakness after lineups are confirmed."""
    picks = database.get_today_picks()
    pending_picks = [p for p in picks if p.get("status") == "pending"]

    if not pending_picks:
        print("[MONITOR] No pending picks for lineup check.")
        return

    today = date.today().isoformat()
    log_entries = database.get_today_analysis_log()
    log_by_mlb_id = {entry["mlb_game_id"]: entry for entry in log_entries}

    conn = database.get_connection()
    checked = set()

    try:
        for pick in pending_picks:
            game_id = pick["game_id"]

            # Look up mlb_game_id and team IDs from games table
            game_row = conn.execute(
                "SELECT mlb_game_id, away_team_id, home_team_id FROM games WHERE id=?",
                (game_id,)
            ).fetchone()
            if not game_row:
                continue

            mlb_game_id = game_row["mlb_game_id"]

            # One check per game
            if mlb_game_id in checked:
                continue
            checked.add(mlb_game_id)

            # Skip if already alerted today
            if database.lineup_alert_already_sent(mlb_game_id, today):
                print(f"[MONITOR] Lineup alert already sent for game {mlb_game_id} — skipping.")
                continue

            log = log_by_mlb_id.get(mlb_game_id)
            if not log:
                continue

            # Fetch current lineup state from MLB API
            current = data_mlb_module.get_current_lineups(mlb_game_id)

            # Skip if game already started
            if current["game_status"] in ("Live", "Final", "Game Over", "Completed"):
                print(f"[MONITOR] Game {mlb_game_id} in progress/final — skipping lineup check.")
                continue

            # Skip if lineups not yet posted
            if not current["away_confirmed"] or not current["home_confirmed"]:
                print(f"[MONITOR] Lineups not yet confirmed for game {mlb_game_id} — skipping.")
                continue

            # Determine pick team side
            pick_team = log.get("ml_pick_team", "")
            away_team = log.get("away_team", "")
            is_home_pick = pick_team.lower().strip() != away_team.lower().strip()
            pick_side = "home" if is_home_pick else "away"
            player_ids = current["home_ids"] if is_home_pick else current["away_ids"]
            team_db_id = game_row["home_team_id"] if is_home_pick else game_row["away_team_id"]

            # Look up mlb_id for the pick team from teams table
            team_row = conn.execute(
                "SELECT mlb_id FROM teams WHERE id=?", (team_db_id,)
            ).fetchone()
            if not team_row:
                print(f"[MONITOR] Could not resolve team mlb_id for game {mlb_game_id} — skipping.")
                continue
            team_mlb_id = team_row["mlb_id"]

            # Compute actual lineup OPS from confirmed starters
            stats = fetch_lineup_batting(player_ids)
            ops_values = []
            for s in stats:
                season_ops = s.get("ops", 0) or 0
                if season_ops == 0:
                    continue  # no OPS data for this player
                rolling = get_batter_rolling_ops(s["player_id"])
                if rolling and rolling["games"] >= MIN_BATTER_GAMES:
                    w = 0.8 if rolling["games"] >= 20 else 0.6
                    blended = rolling["ops"] * w + season_ops * (1 - w)
                else:
                    blended = season_ops
                ops_values.append(blended)

            if len(ops_values) < LINEUP_MIN_PLAYERS_WITH_DATA:
                print(f"[MONITOR] Only {len(ops_values)} players with OPS data for game {mlb_game_id} — skipping.")
                continue

            ops_actual = sum(ops_values) / len(ops_values)

            # Get expected OPS from season team batting stats
            team_batting = fetch_team_batting(team_mlb_id)
            ops_expected = team_batting.get("ops") or 0.0
            if ops_expected == 0.0:
                print(f"[MONITOR] No expected OPS for team {team_mlb_id} — skipping.")
                continue

            pct_drop = (ops_expected - ops_actual) / ops_expected

            if pct_drop >= LINEUP_OPS_DROP_THRESHOLD:
                conf = log.get("ml_confidence", 0)
                game_label = log.get("game", str(mlb_game_id))
                sent = send_lineup_alert(game_label, pick_team, ops_actual, ops_expected, pct_drop, conf)
                if sent:
                    save_lineup_alert(mlb_game_id, today, ops_actual, ops_expected, pct_drop)
            else:
                print(f"[MONITOR] {log.get('game', mlb_game_id)}: lineup OPS drop {pct_drop*100:.1f}% — below threshold, no alert.")
    finally:
        conn.close()


def main():
    run_monitor()
    run_lineup_monitor()


if __name__ == "__main__":
    main()
