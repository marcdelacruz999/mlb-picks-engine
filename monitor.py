"""
Pitcher scratch monitor — checks active picks for SP changes.
Run every 30 minutes between 8am and first pitch via launchd (plist created separately).
"""
import requests
from datetime import date
from database import get_db_connection, get_connection, get_today_picks, get_today_analysis_log, pitcher_already_alerted, save_scratch_alert
from config import DISCORD_WEBHOOK_URL

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


def main():
    run_monitor()


if __name__ == "__main__":
    main()
