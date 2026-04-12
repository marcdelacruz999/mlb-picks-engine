"""
MLB Picks Engine — Odds & Lines Data Module
=============================================
Uses The Odds API (free tier: 500 requests/month).
https://the-odds-api.com
"""

import requests
from datetime import datetime, timezone
from config import ODDS_API_KEY

ODDS_BASE = "https://api.the-odds-api.com/v4"
SPORT = "baseball_mlb"


def fetch_odds() -> list:
    """
    Fetch current MLB moneyline and totals odds.
    Returns a list of game odds dicts.
    """
    if not ODDS_API_KEY:
        print("[ODDS] No API key set — skipping odds fetch.")
        print("[ODDS] Get a free key at https://the-odds-api.com")
        return []

    url = f"{ODDS_BASE}/sports/{SPORT}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american",
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # Log remaining API calls
        remaining = resp.headers.get("x-requests-remaining", "?")
        print(f"[ODDS] Fetched {len(data)} games. API calls remaining: {remaining}")

        return _parse_odds(data)

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            print("[ODDS] Invalid API key. Check your config.")
        elif e.response.status_code == 429:
            print("[ODDS] Rate limit exceeded. Wait before retrying.")
        else:
            print(f"[ODDS] HTTP error: {e}")
        return []
    except Exception as e:
        print(f"[ODDS] Error fetching odds: {e}")
        return []


def fetch_f5_odds() -> list:
    """
    Fetch F5 (First 5 Innings) MLB odds from The Odds API.
    Uses sport key 'baseball_mlb_h1'.
    Returns same structure as fetch_odds() but with F5 lines.
    """
    if not ODDS_API_KEY:
        print("[ODDS] No API key set — skipping F5 odds fetch.")
        return []

    url = f"{ODDS_BASE}/sports/baseball_mlb_h1/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,totals",
        "oddsFormat": "american",
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        remaining = resp.headers.get("x-requests-remaining", "?")
        print(f"[ODDS] F5: Fetched {len(data)} games. API calls remaining: {remaining}")
        return _parse_odds(data)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print("[ODDS] F5 market not available (baseball_mlb_h1 not found).")
        else:
            print(f"[ODDS] F5 HTTP error: {e}")
        return []
    except Exception as e:
        print(f"[ODDS] F5 error: {e}")
        return []


def _parse_odds(raw_data: list) -> list:
    """Parse raw odds API response into clean dicts."""
    results = []

    now_utc = datetime.now(timezone.utc)

    for game in raw_data:
        # Skip games that have already started — odds become live in-game lines
        commence_str = game.get("commence_time", "")
        if commence_str:
            try:
                commence_dt = datetime.fromisoformat(commence_str.replace("Z", "+00:00"))
                if commence_dt <= now_utc:
                    continue
            except ValueError:
                pass

        game_info = {
            "api_id": game.get("id"),
            "sport": game.get("sport_key"),
            "commence_time": game.get("commence_time"),
            "home_team": game.get("home_team"),
            "away_team": game.get("away_team"),
            "bookmakers": [],
        }

        for bm in game.get("bookmakers", []):
            bookmaker = {
                "name": bm.get("title", "Unknown"),
                "markets": {}
            }

            for market in bm.get("markets", []):
                key = market.get("key")

                if key == "h2h":
                    # Moneyline
                    ml = {}
                    for outcome in market.get("outcomes", []):
                        if outcome["name"] == game["home_team"]:
                            ml["home_ml"] = outcome.get("price")
                        elif outcome["name"] == game["away_team"]:
                            ml["away_ml"] = outcome.get("price")
                    bookmaker["markets"]["moneyline"] = ml

                elif key == "spreads":
                    # Run line (always ±1.5 in MLB)
                    rl = {}
                    for outcome in market.get("outcomes", []):
                        point = outcome.get("point", 0)
                        price = outcome.get("price")
                        if outcome["name"] == game["home_team"]:
                            rl["home_rl"] = point
                            rl["home_rl_price"] = price
                        elif outcome["name"] == game["away_team"]:
                            rl["away_rl"] = point
                            rl["away_rl_price"] = price
                    bookmaker["markets"]["runline"] = rl

                elif key == "totals":
                    # Over/Under
                    totals = {}
                    for outcome in market.get("outcomes", []):
                        totals["line"] = outcome.get("point")
                        if outcome["name"] == "Over":
                            totals["over_price"] = outcome.get("price")
                        elif outcome["name"] == "Under":
                            totals["under_price"] = outcome.get("price")
                    bookmaker["markets"]["totals"] = totals

            game_info["bookmakers"].append(bookmaker)

        # Calculate consensus odds (average across bookmakers)
        game_info["consensus"] = _calculate_consensus(game_info["bookmakers"])
        results.append(game_info)

    return results


def _calculate_consensus(bookmakers: list) -> dict:
    """
    Calculate consensus odds across all bookmakers.
    ML consensus uses implied probability space (averaging raw American odds
    is mathematically invalid due to sign changes near even money).
    Run line and total use median line + averaged prices.
    """
    home_ml_probs, away_ml_probs = [], []
    home_mls_raw, away_mls_raw = [], []
    total_lines, over_prices, under_prices = [], [], []
    home_rl_prices, away_rl_prices, rl_lines = [], [], []

    for bm in bookmakers:
        ml = bm["markets"].get("moneyline", {})
        # Filter out extreme ML values (>500 abs) — likely alternate/prop markets
        home_ml_val = ml.get("home_ml")
        away_ml_val = ml.get("away_ml")
        if home_ml_val and abs(home_ml_val) <= 500:
            home_mls_raw.append(home_ml_val)
            home_ml_probs.append(implied_probability(home_ml_val))
        if away_ml_val and abs(away_ml_val) <= 500:
            away_mls_raw.append(away_ml_val)
            away_ml_probs.append(implied_probability(away_ml_val))

        rl = bm["markets"].get("runline", {})
        # Only use standard ±1.5 run lines (not alternate lines like ±1 or ±2)
        home_rl_val = rl.get("home_rl")
        if home_rl_val is not None and abs(round(home_rl_val, 1)) == 1.5:
            if rl.get("home_rl_price"):
                home_rl_prices.append(rl["home_rl_price"])
            if rl.get("away_rl_price"):
                away_rl_prices.append(rl["away_rl_price"])
            rl_lines.append(home_rl_val)

        totals = bm["markets"].get("totals", {})
        if totals.get("line"):
            total_lines.append(totals["line"])
        if totals.get("over_price"):
            over_prices.append(totals["over_price"])
        if totals.get("under_price"):
            under_prices.append(totals["under_price"])

    # Convert consensus probability back to American odds
    home_ml_consensus = _prob_to_american(_avg(home_ml_probs)) if home_ml_probs else None
    away_ml_consensus = _prob_to_american(_avg(away_ml_probs)) if away_ml_probs else None

    # Run line and total: use mode (most common) line value, average prices
    home_rl = max(set(rl_lines), key=rl_lines.count) if rl_lines else None
    total_line = max(set(total_lines), key=total_lines.count) if total_lines else None

    return {
        "home_ml": home_ml_consensus,
        "away_ml": away_ml_consensus,
        "home_rl": home_rl,
        "away_rl": -home_rl if home_rl is not None else None,
        "home_rl_price": round(_avg(home_rl_prices)) if home_rl_prices else None,
        "away_rl_price": round(_avg(away_rl_prices)) if away_rl_prices else None,
        "total_line": total_line,
        "over_price": round(_avg(over_prices)) if over_prices else None,
        "under_price": round(_avg(under_prices)) if under_prices else None,
        "num_bookmakers": len(bookmakers),
    }


def implied_probability(american_odds: int) -> float:
    """Convert American odds to implied probability."""
    if not american_odds:
        return 0.5
    if american_odds > 0:
        return 100 / (american_odds + 100)
    else:
        return abs(american_odds) / (abs(american_odds) + 100)


def _prob_to_american(prob: float) -> int:
    """Convert implied probability back to American odds (rounded to nearest 5)."""
    if prob <= 0 or prob >= 1:
        return 0
    if prob >= 0.5:
        american = -(prob / (1 - prob)) * 100
    else:
        american = ((1 - prob) / prob) * 100
    return int(round(american / 5) * 5)


def find_value(model_prob: float, market_prob: float) -> float:
    """
    Calculate betting edge: positive means our model gives
    higher probability than the market implies.
    """
    if market_prob == 0:
        return 0.0
    return round(model_prob - market_prob, 4)


def match_odds_to_game(odds_list: list, home_team: str, away_team: str) -> dict:
    """
    Find the odds entry matching a specific game.
    Collects ALL matches (Odds API returns both F5 and full-game entries),
    then returns the one with the highest total line — full-game lines are
    always higher than first-5-innings lines.
    """
    home_lower = home_team.lower()
    away_lower = away_team.lower()

    matches = []
    for entry in odds_list:
        entry_home = entry.get("home_team", "").lower()
        entry_away = entry.get("away_team", "").lower()

        if (_name_match(home_lower, entry_home) and
                _name_match(away_lower, entry_away)):
            matches.append(entry)

    if not matches:
        return {}

    # Prefer the entry with the highest total line (full game > F5)
    return max(
        matches,
        key=lambda e: e.get("consensus", {}).get("total_line") or 0
    )


def match_f5_odds_to_game(f5_odds_list: list, home_team: str, away_team: str) -> dict:
    """
    Find F5 odds entry matching a specific game.
    Same logic as match_odds_to_game but for the F5 list.
    """
    home_lower = home_team.lower()
    away_lower = away_team.lower()
    for entry in f5_odds_list:
        entry_home = entry.get("home_team", "").lower()
        entry_away = entry.get("away_team", "").lower()
        if (_name_match(home_lower, entry_home) and
                _name_match(away_lower, entry_away)):
            return entry
    return {}


def _name_match(name1: str, name2: str) -> bool:
    """Simple fuzzy match — check if the last word (city or mascot) matches."""
    words1 = name1.split()
    words2 = name2.split()
    # Compare last word (usually the mascot)
    if words1 and words2 and words1[-1] == words2[-1]:
        return True
    # Check if one is a substring of the other
    return name1 in name2 or name2 in name1


def _avg(nums: list) -> float:
    """Safe average."""
    if not nums:
        return 0.0
    return round(sum(nums) / len(nums), 1)


if __name__ == "__main__":
    odds = fetch_odds()
    for o in odds[:3]:
        c = o["consensus"]
        print(f"{o['away_team']} @ {o['home_team']}")
        print(f"  ML: Away {c['away_ml']}  Home {c['home_ml']}")
        print(f"  Total: {c['total_line']}  O {c['over_price']}  U {c['under_price']}")
