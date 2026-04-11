"""
MLB Picks Engine — MLB Data Collection Module
===============================================
Uses the official MLB Stats API (free) + FanGraphs scraping for advanced stats.
"""

import requests
import csv
import io
from bs4 import BeautifulSoup
from datetime import date, datetime, timedelta
import time
import database as db
from config import SEASON_YEAR

MLB_BASE = "https://statsapi.mlb.com/api/v1"

# ──────────────────────────────────────────────
# MLB Stats API — Teams
# ──────────────────────────────────────────────

def fetch_all_teams() -> list:
    """Fetch all MLB teams and upsert into DB."""
    url = f"{MLB_BASE}/teams?sportId=1&season={SEASON_YEAR}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    teams = resp.json().get("teams", [])

    results = []
    for t in teams:
        team = {
            "mlb_id": t["id"],
            "name": t["name"],
            "abbreviation": t.get("abbreviation", ""),
            "division": t.get("division", {}).get("name", ""),
            "league": t.get("league", {}).get("name", ""),
        }
        local_id = db.upsert_team(team)
        team["local_id"] = local_id
        results.append(team)

    print(f"[DATA] Loaded {len(results)} teams.")
    return results


# ──────────────────────────────────────────────
# MLB Stats API — Today's Schedule & Probable Pitchers
# ──────────────────────────────────────────────

def fetch_todays_games(target_date: str = None) -> list:
    """
    Fetch today's games with probable pitchers.
    target_date: YYYY-MM-DD string, defaults to today.
    """
    if not target_date:
        target_date = date.today().isoformat()

    url = (
        f"{MLB_BASE}/schedule?sportId=1&date={target_date}"
        f"&hydrate=probablePitcher(note),team,linescore,venue,officials,lineups"
    )
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    games = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            away = g.get("teams", {}).get("away", {})
            home = g.get("teams", {}).get("home", {})

            venue = g.get("venue", {})

            # HP umpire (from officials hydrate)
            hp_umpire = ""
            for official in g.get("officials", []):
                if official.get("officialType") == "Home Plate":
                    hp_umpire = official.get("official", {}).get("fullName", "")
                    break

            # Lineup confirmation status (from lineups hydrate)
            lineups_data = g.get("lineups", {})
            home_lineup = [p.get("fullName", "") for p in lineups_data.get("homePlayers", [])]
            away_lineup = [p.get("fullName", "") for p in lineups_data.get("awayPlayers", [])]

            game_info = {
                "mlb_game_id": g["gamePk"],
                "game_date": target_date,
                "status": g.get("status", {}).get("detailedState", "Scheduled"),
                "away_team_name": away.get("team", {}).get("name", "TBD"),
                "away_team_mlb_id": away.get("team", {}).get("id"),
                "home_team_name": home.get("team", {}).get("name", "TBD"),
                "home_team_mlb_id": home.get("team", {}).get("id"),
                "away_pitcher_name": away.get("probablePitcher", {}).get("fullName", "TBD"),
                "away_pitcher_id": away.get("probablePitcher", {}).get("id"),
                "home_pitcher_name": home.get("probablePitcher", {}).get("fullName", "TBD"),
                "home_pitcher_id": home.get("probablePitcher", {}).get("id"),
                "away_score": away.get("score"),
                "home_score": home.get("score"),
                "venue_id": venue.get("id"),
                "venue_name": venue.get("name", ""),
                "away_team_abbr": away.get("team", {}).get("abbreviation", ""),
                "home_team_abbr": home.get("team", {}).get("abbreviation", ""),
                "hp_umpire": hp_umpire,
                "home_lineup": home_lineup,
                "away_lineup": away_lineup,
                "home_lineup_confirmed": bool(home_lineup),
                "away_lineup_confirmed": bool(away_lineup),
            }

            # Calculate total runs if game is final
            if game_info["away_score"] is not None and game_info["home_score"] is not None:
                game_info["total_runs"] = game_info["away_score"] + game_info["home_score"]
            else:
                game_info["total_runs"] = None

            games.append(game_info)

    print(f"[DATA] Found {len(games)} games for {target_date}.")
    return games


# ──────────────────────────────────────────────
# MLB Stats API — Pitcher Season Stats
# ──────────────────────────────────────────────

def fetch_pitcher_stats(pitcher_mlb_id: int) -> dict:
    """Fetch season stats for a pitcher from MLB Stats API."""
    if not pitcher_mlb_id:
        return {}

    url = (
        f"{MLB_BASE}/people/{pitcher_mlb_id}"
        f"?hydrate=stats(group=[pitching],type=[season],season={SEASON_YEAR})"
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[DATA] Error fetching pitcher {pitcher_mlb_id}: {e}")
        return {}

    person = data.get("people", [{}])[0]
    stats_groups = person.get("stats", [])
    season_stats = {}

    for sg in stats_groups:
        for split in sg.get("splits", []):
            s = split.get("stat", {})
            season_stats = {
                "name": person.get("fullName", "Unknown"),
                "throws": person.get("pitchHand", {}).get("code", "R"),
                "era": _safe_float(s.get("era")),
                "whip": _safe_float(s.get("whip")),
                "k_per_9": _safe_float(s.get("strikeoutsPer9Inn")),
                "bb_per_9": _safe_float(s.get("walksPer9Inn")),
                "innings_pitched": _safe_float(s.get("inningsPitched")),
                "games_started": s.get("gamesStarted", 0),
                "wins": s.get("wins", 0),
                "losses": s.get("losses", 0),
                "avg_against": _safe_float(s.get("avg")),
                "obp_against": _safe_float(s.get("obp")),
                "slg_against": _safe_float(s.get("slg")),
                "home_runs_allowed": s.get("homeRuns", 0),
                "strikeouts": s.get("strikeOuts", 0),
                "walks": s.get("baseOnBalls", 0),
            }
            # Calculate K/BB ratio
            if season_stats["walks"] and season_stats["walks"] > 0:
                season_stats["k_bb_ratio"] = round(
                    season_stats["strikeouts"] / season_stats["walks"], 2
                )
            else:
                season_stats["k_bb_ratio"] = None
            break

    return season_stats


# ──────────────────────────────────────────────
# MLB Stats API — Pitcher Rest (days since last start)
# ──────────────────────────────────────────────

def fetch_pitcher_rest(pitcher_id: int):
    """
    Returns the number of days since a pitcher's last start.
    Uses the game log endpoint — only counts games started, not relief appearances.
    Returns None if pitcher has no starts yet this season or data unavailable.
    """
    if not pitcher_id:
        return None

    url = (
        f"{MLB_BASE}/people/{pitcher_id}/stats"
        f"?stats=gameLog&group=pitching&season={SEASON_YEAR}&limit=10"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[DATA] Error fetching pitcher rest {pitcher_id}: {e}")
        return None

    today = date.today()
    for sg in data.get("stats", []):
        for split in sg.get("splits", []):
            if not split.get("stat", {}).get("gamesStarted", 0):
                continue  # Skip relief appearances
            game_date_str = split.get("date", "")
            if not game_date_str:
                continue
            try:
                last_start = date.fromisoformat(game_date_str)
                days = (today - last_start).days
                if days > 0:  # Skip today's game if already logged
                    return days
            except ValueError:
                continue
    return None


# ──────────────────────────────────────────────
# MLB Stats API — Team Batting Stats
# ──────────────────────────────────────────────

def fetch_team_batting(team_mlb_id: int) -> dict:
    """Fetch team season batting stats."""
    url = (
        f"{MLB_BASE}/teams/{team_mlb_id}/stats"
        f"?stats=season&group=hitting&season={SEASON_YEAR}"
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[DATA] Error fetching team batting {team_mlb_id}: {e}")
        return {}

    for sg in data.get("stats", []):
        for split in sg.get("splits", []):
            s = split.get("stat", {})
            return {
                "avg": _safe_float(s.get("avg")),
                "obp": _safe_float(s.get("obp")),
                "slg": _safe_float(s.get("slg")),
                "ops": _safe_float(s.get("ops")),
                "runs": s.get("runs", 0),
                "home_runs": s.get("homeRuns", 0),
                "strikeouts": s.get("strikeOuts", 0),
                "walks": s.get("baseOnBalls", 0),
                "hits": s.get("hits", 0),
                "at_bats": s.get("atBats", 0),
                "games_played": s.get("gamesPlayed", 0),
            }
    return {}


# ──────────────────────────────────────────────
# MLB Stats API — Team Pitching (Bullpen proxy)
# ──────────────────────────────────────────────

def fetch_team_pitching(team_mlb_id: int) -> dict:
    """Fetch team season pitching stats (used as bullpen proxy)."""
    url = (
        f"{MLB_BASE}/teams/{team_mlb_id}/stats"
        f"?stats=season&group=pitching&season={SEASON_YEAR}"
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[DATA] Error fetching team pitching {team_mlb_id}: {e}")
        return {}

    for sg in data.get("stats", []):
        for split in sg.get("splits", []):
            s = split.get("stat", {})
            return {
                "era": _safe_float(s.get("era")),
                "whip": _safe_float(s.get("whip")),
                "k_per_9": _safe_float(s.get("strikeoutsPer9Inn")),
                "bb_per_9": _safe_float(s.get("walksPer9Inn")),
                "saves": s.get("saves", 0),
                "save_opportunities": s.get("saveOpportunities", 0),
                "holds": s.get("holds", 0),
                "blown_saves": s.get("blownSaves", 0),
            }
    return {}


# ──────────────────────────────────────────────
# MLB Stats API — Team Recent Record (momentum)
# ──────────────────────────────────────────────

def fetch_team_record(team_mlb_id: int) -> dict:
    """Fetch team's season record for win streaks etc."""
    url = f"{MLB_BASE}/teams/{team_mlb_id}?hydrate=record&season={SEASON_YEAR}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[DATA] Error fetching team record {team_mlb_id}: {e}")
        return {}

    teams = data.get("teams", [{}])
    if not teams:
        return {}
    t = teams[0]
    record = t.get("record", {})
    streak = record.get("streak", {})

    return {
        "wins": record.get("wins", 0),
        "losses": record.get("losses", 0),
        "win_pct": _safe_float(record.get("winningPercentage")),
        "streak_type": streak.get("streakType", ""),
        "streak_number": streak.get("streakNumber", 0),
        "home_wins": record.get("records", {}).get("splitRecords", [{}])[0].get("wins", 0) if record.get("records") else 0,
        "home_losses": record.get("records", {}).get("splitRecords", [{}])[0].get("losses", 0) if record.get("records") else 0,
        "last_10_wins": record.get("records", {}).get("splitRecords", [{}])[-1].get("wins", 0) if record.get("records") else 0,
    }


# ──────────────────────────────────────────────
# FanGraphs Scraping — Advanced Metrics
# ──────────────────────────────────────────────

def scrape_fangraphs_team_batting() -> dict:
    """
    Scrape FanGraphs team batting leaderboard for advanced metrics.
    Returns dict keyed by team abbreviation.
    """
    url = (
        "https://www.fangraphs.com/leaders.aspx?pos=all&stats=bat&lg=all"
        f"&qual=0&type=8&season={SEASON_YEAR}&month=0&season1={SEASON_YEAR}"
        "&ind=0&team=0,ts&rost=0&age=0&filter=&players=0"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MLBPicksEngine/1.0)"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(resp.text, "lxml")
        # FanGraphs uses dynamic tables; we attempt a basic parse
        table = soup.find("table", class_="rgMasterTable")
        if not table:
            print("[SCRAPE] FanGraphs table not found — site may require JS.")
            return {}

        teams = {}
        rows = table.find_all("tr")
        for row in rows[1:]:
            cols = row.find_all("td")
            if len(cols) < 15:
                continue
            try:
                team_name = cols[1].get_text(strip=True)
                teams[team_name] = {
                    "wrc_plus": _safe_float(cols[11].get_text(strip=True)),
                    "babip": _safe_float(cols[9].get_text(strip=True)),
                    "woba": _safe_float(cols[10].get_text(strip=True)),
                }
            except (IndexError, ValueError):
                continue

        print(f"[SCRAPE] FanGraphs: parsed {len(teams)} teams.")
        return teams

    except Exception as e:
        print(f"[SCRAPE] FanGraphs scraping failed: {e}")
        return {}


def scrape_fangraphs_pitcher(pitcher_name: str) -> dict:
    """
    Attempt to scrape FanGraphs for a specific pitcher's xERA, xFIP, etc.
    Falls back gracefully if scraping fails.
    """
    # FanGraphs requires JS for most data now; this is a best-effort attempt
    search_url = f"https://www.fangraphs.com/players?search={pitcher_name.replace(' ', '+')}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; MLBPicksEngine/1.0)"}

    try:
        resp = requests.get(search_url, headers=headers, timeout=15)
        # Basic scraping — FanGraphs may block or require JS
        # Return empty to indicate we should rely on MLB API stats
        return {}
    except Exception:
        return {}


# ──────────────────────────────────────────────
# Baseball Reference Scraping — Standings & Streaks
# ──────────────────────────────────────────────

def scrape_bbref_standings() -> dict:
    """Scrape Baseball Reference standings for recent form data."""
    url = f"https://www.baseball-reference.com/leagues/majors/{SEASON_YEAR}-standings.shtml"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; MLBPicksEngine/1.0)"}

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")

        standings = {}
        tables = soup.find_all("table", {"id": lambda x: x and "standings" in str(x).lower()})

        for table in tables:
            rows = table.find("tbody")
            if not rows:
                continue
            for row in rows.find_all("tr"):
                cols = row.find_all(["td", "th"])
                if len(cols) < 4:
                    continue
                team_name = cols[0].get_text(strip=True)
                if team_name:
                    standings[team_name] = {
                        "wins": _safe_int(cols[1].get_text(strip=True)) if len(cols) > 1 else 0,
                        "losses": _safe_int(cols[2].get_text(strip=True)) if len(cols) > 2 else 0,
                    }

        print(f"[SCRAPE] BBRef: parsed {len(standings)} team standings.")
        return standings

    except Exception as e:
        print(f"[SCRAPE] BBRef scraping failed: {e}")
        return {}


# ──────────────────────────────────────────────
# Full Data Collection Pipeline
# ──────────────────────────────────────────────

def collect_game_data(target_date: str = None) -> list:
    """
    Full data collection for today's games.
    Returns enriched game dicts with stats for analysis.
    """
    games = fetch_todays_games(target_date)

    # Fetch Statcast once for all teams
    sc_batting  = fetch_statcast_team_batting()
    sc_pitching = fetch_statcast_team_pitching()
    sc_pitchers = fetch_statcast_pitcher_xera()

    enriched = []

    for g in games:
        # Skip non-scheduled / postponed games
        status = g.get("status", "")
        if "Postponed" in status or "Cancelled" in status:
            continue

        # Fetch pitcher stats + rest days
        away_ps = fetch_pitcher_stats(g.get("away_pitcher_id"))
        home_ps = fetch_pitcher_stats(g.get("home_pitcher_id"))
        away_ps["days_rest"] = fetch_pitcher_rest(g.get("away_pitcher_id"))
        home_ps["days_rest"] = fetch_pitcher_rest(g.get("home_pitcher_id"))
        g["away_pitcher_stats"] = away_ps
        g["home_pitcher_stats"] = home_ps

        # Fetch team batting
        g["away_batting"] = fetch_team_batting(g["away_team_mlb_id"])
        g["home_batting"] = fetch_team_batting(g["home_team_mlb_id"])

        # Fetch team pitching / bullpen
        g["away_pitching"] = fetch_team_pitching(g["away_team_mlb_id"])
        g["home_pitching"] = fetch_team_pitching(g["home_team_mlb_id"])

        # Fetch records / momentum
        g["away_record"] = fetch_team_record(g["away_team_mlb_id"])
        g["home_record"] = fetch_team_record(g["home_team_mlb_id"])

        # Fetch weather
        g["weather"] = fetch_venue_weather(g.get("venue_id"), target_date)

        # Attach Statcast — team batting/pitching by abbreviation
        g["away_statcast_bat"] = sc_batting.get(g.get("away_team_abbr", ""), {})
        g["home_statcast_bat"] = sc_batting.get(g.get("home_team_abbr", ""), {})
        g["away_statcast_pit"] = sc_pitching.get(g.get("away_team_abbr", ""), {})
        g["home_statcast_pit"] = sc_pitching.get(g.get("home_team_abbr", ""), {})

        # Attach Statcast — individual pitcher xERA by player_id
        g["away_pitcher_statcast"] = sc_pitchers.get(g.get("away_pitcher_id") or 0, {})
        g["home_pitcher_statcast"] = sc_pitchers.get(g.get("home_pitcher_id") or 0, {})

        enriched.append(g)
        time.sleep(0.3)  # Be polite to the API

    print(f"[DATA] Enriched {len(enriched)} games with full stats.")
    return enriched


# ──────────────────────────────────────────────
# Statcast (Baseball Savant — free, no key)
# ──────────────────────────────────────────────

SAVANT_BASE = "https://baseballsavant.mlb.com"
SAVANT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MLBPicksEngine/1.0)"}
_statcast_cache: dict = {}


def _fetch_savant_csv(url: str) -> list:
    """Fetch a Baseball Savant CSV endpoint and return list of row dicts."""
    try:
        resp = requests.get(url, headers=SAVANT_HEADERS, timeout=20)
        resp.raise_for_status()
        text = resp.content.decode("utf-8-sig")  # strips BOM
        reader = csv.DictReader(io.StringIO(text))
        return list(reader)
    except Exception as e:
        print(f"[STATCAST] CSV fetch failed ({url[:60]}...): {e}")
        return []


def fetch_statcast_team_batting() -> dict:
    """
    Team batting Statcast metrics from Baseball Savant (one call/day).
    Returns dict keyed by team abbreviation e.g. 'NYY', 'LAD'.
    Metrics: xwoba, woba, woba_diff (luck), hard_hit_pct, barrel_pct, avg_exit_velo.
    """
    cache_key = f"sc_bat_{date.today().isoformat()}"
    if cache_key in _statcast_cache:
        return _statcast_cache[cache_key]

    url = (
        f"{SAVANT_BASE}/statcast_search/csv"
        f"?hfGT=R%7C&hfSea={SEASON_YEAR}%7C&player_type=batter&group_by=team&min_pas=0"
    )
    rows = _fetch_savant_csv(url)
    result = {}
    for r in rows:
        team = r.get("player_name", "").strip()
        if not team or len(team) > 4:
            continue
        result[team] = {
            "xwoba":          _safe_float(r.get("xwoba")),
            "woba":           _safe_float(r.get("woba")),
            "woba_diff":      _safe_float(r.get("wobadiff")),  # + = lucky, - = unlucky
            "hard_hit_pct":   _safe_float(r.get("hardhit_percent")),
            "barrel_pct":     _safe_float(r.get("barrels_per_bbe_percent")),
            "avg_exit_velo":  _safe_float(r.get("launch_speed")),
        }

    print(f"[STATCAST] Team batting loaded: {len(result)} teams.")
    _statcast_cache[cache_key] = result
    return result


def fetch_statcast_team_pitching() -> dict:
    """
    Team pitching Statcast metrics from Baseball Savant (one call/day).
    Returns dict keyed by team abbreviation.
    Metrics: xwoba_against, hard_hit allowed, barrel allowed, exit velo allowed.
    """
    cache_key = f"sc_pit_{date.today().isoformat()}"
    if cache_key in _statcast_cache:
        return _statcast_cache[cache_key]

    url = (
        f"{SAVANT_BASE}/statcast_search/csv"
        f"?hfGT=R%7C&hfSea={SEASON_YEAR}%7C&player_type=pitcher&group_by=team&min_pas=0"
    )
    rows = _fetch_savant_csv(url)
    result = {}
    for r in rows:
        team = r.get("player_name", "").strip()
        if not team or len(team) > 4:
            continue
        result[team] = {
            "xwoba_against":         _safe_float(r.get("xwoba")),
            "woba_against":          _safe_float(r.get("woba")),
            "hard_hit_pct_against":  _safe_float(r.get("hardhit_percent")),
            "barrel_pct_against":    _safe_float(r.get("barrels_per_bbe_percent")),
            "avg_exit_velo_against": _safe_float(r.get("launch_speed")),
        }

    print(f"[STATCAST] Team pitching loaded: {len(result)} teams.")
    _statcast_cache[cache_key] = result
    return result


def fetch_statcast_pitcher_xera() -> dict:
    """
    Pitcher xERA and regression signals from Baseball Savant (one call/day).
    Returns dict keyed by MLB player_id (int).
    era_minus_xera: negative = ERA < xERA = pitcher getting lucky (expect regression up).
                    positive = ERA > xERA = pitcher getting unlucky (expect improvement).
    """
    cache_key = f"sc_sp_{date.today().isoformat()}"
    if cache_key in _statcast_cache:
        return _statcast_cache[cache_key]

    url = (
        f"{SAVANT_BASE}/leaderboard/expected_statistics"
        f"?type=pitcher&year={SEASON_YEAR}&position=&team=&min=0&csv=true"
    )
    rows = _fetch_savant_csv(url)
    result = {}
    for r in rows:
        pid = _safe_int(r.get("player_id"))
        if not pid:
            continue
        result[pid] = {
            "era":            _safe_float(r.get("era")),
            "xera":           _safe_float(r.get("xera")),
            "era_minus_xera": _safe_float(r.get("era_minus_xera_diff")),
            "xwoba_against":  _safe_float(r.get("est_woba")),
            "xba_against":    _safe_float(r.get("est_ba")),
        }

    print(f"[STATCAST] Pitcher xERA loaded: {len(result)} pitchers.")
    _statcast_cache[cache_key] = result
    return result


# ──────────────────────────────────────────────
# Venue Coordinates + Weather (Open-Meteo, free)
# ──────────────────────────────────────────────

_venue_coords_cache: dict = {}

def fetch_venue_coords(venue_id: int) -> tuple:
    """Return (lat, lon) for an MLB venue, or (None, None) if unavailable."""
    if not venue_id:
        return None, None
    if venue_id in _venue_coords_cache:
        return _venue_coords_cache[venue_id]
    try:
        url = f"{MLB_BASE}/venues/{venue_id}?hydrate=location"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        coords = (
            resp.json()
            .get("venues", [{}])[0]
            .get("location", {})
            .get("defaultCoordinates", {})
        )
        lat = coords.get("latitude")
        lon = coords.get("longitude")
        _venue_coords_cache[venue_id] = (lat, lon)
        return lat, lon
    except Exception:
        return None, None


def fetch_venue_weather(venue_id: int, game_date: str = None) -> dict:
    """
    Fetch weather forecast for an MLB venue using Open-Meteo (no API key required).
    Returns a dict with temp_f, wind_mph, wind_dir, precip_chance, conditions.
    """
    lat, lon = fetch_venue_coords(venue_id)
    if lat is None or lon is None:
        return {}

    if not game_date:
        game_date = date.today().isoformat()

    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&hourly=temperature_2m,precipitation_probability,weathercode,windspeed_10m,winddirection_10m"
            "&temperature_unit=fahrenheit&windspeed_unit=mph"
            f"&start_date={game_date}&end_date={game_date}"
            "&timezone=auto"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        hourly = data.get("hourly", {})

        # Use 7pm local (index 19) as proxy for game time; fall back to average
        idx = 19
        times = hourly.get("time", [])
        if idx >= len(times):
            idx = len(times) // 2

        temp_f = hourly.get("temperature_2m", [None])[idx]
        precip = hourly.get("precipitation_probability", [0])[idx] or 0
        wind_mph = hourly.get("windspeed_10m", [0])[idx] or 0
        wind_dir = hourly.get("winddirection_10m", [0])[idx] or 0
        wcode = hourly.get("weathercode", [0])[idx] or 0

        conditions = _wmo_description(wcode)
        wind_label = _wind_direction_label(wind_dir)

        return {
            "temp_f": round(temp_f, 1) if temp_f is not None else None,
            "wind_mph": round(wind_mph, 1),
            "wind_dir": wind_label,
            "wind_dir_deg": wind_dir,
            "precip_chance": precip,
            "conditions": conditions,
            "weathercode": wcode,
        }
    except Exception as e:
        print(f"[WEATHER] Failed to fetch weather for venue {venue_id}: {e}")
        return {}


def _wmo_description(code: int) -> str:
    """Convert WMO weather code to human-readable string."""
    wmo = {
        0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Foggy", 48: "Foggy",
        51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
        61: "Light rain", 63: "Rain", 65: "Heavy rain",
        71: "Light snow", 73: "Snow", 75: "Heavy snow",
        80: "Rain showers", 81: "Rain showers", 82: "Heavy showers",
        95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
    }
    return wmo.get(code, f"Code {code}")


def _wind_direction_label(deg: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(deg / 45) % 8]


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _safe_float(val) -> float:
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0

def _safe_int(val) -> int:
    try:
        return int(val) if val is not None else 0
    except (ValueError, TypeError):
        return 0


if __name__ == "__main__":
    # Quick test
    db.init_db()
    teams = fetch_all_teams()
    games = fetch_todays_games()
    for g in games[:3]:
        print(f"  {g['away_team_name']} @ {g['home_team_name']}")
        print(f"    Pitchers: {g['away_pitcher_name']} vs {g['home_pitcher_name']}")
