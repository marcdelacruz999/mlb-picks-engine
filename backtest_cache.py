"""
MLB Picks Engine — Backtest Cache
===================================
SQLite cache for historical season data used by backtest.py.
Keeps a separate DB from mlb_picks.db to avoid polluting live data.
"""

import sqlite3
import json
import os
from config import PROJECT_DIR

DEFAULT_CACHE_PATH = os.path.join(PROJECT_DIR, "backtest_cache.db")


class BacktestCache:
    """SQLite-backed cache for historical MLB stats and schedules."""

    def __init__(self, db_path: str = DEFAULT_CACHE_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self):
        self.conn.close()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS season_games (
                mlb_game_id  INTEGER NOT NULL,
                season       INTEGER NOT NULL,
                game_date    TEXT,
                away_team_id INTEGER,
                away_team_name TEXT,
                away_team_abbr TEXT,
                home_team_id INTEGER,
                home_team_name TEXT,
                home_team_abbr TEXT,
                away_score   INTEGER,
                home_score   INTEGER,
                home_team_won INTEGER,
                away_pitcher_id INTEGER,
                away_pitcher_name TEXT,
                home_pitcher_id INTEGER,
                home_pitcher_name TEXT,
                venue_id     INTEGER,
                venue_name   TEXT,
                PRIMARY KEY (mlb_game_id, season)
            );

            CREATE TABLE IF NOT EXISTS team_stats (
                team_mlb_id  INTEGER NOT NULL,
                season       INTEGER NOT NULL,
                stat_type    TEXT NOT NULL,
                stats_json   TEXT NOT NULL,
                PRIMARY KEY (team_mlb_id, season, stat_type)
            );

            CREATE TABLE IF NOT EXISTS pitcher_stats (
                pitcher_id   INTEGER NOT NULL,
                season       INTEGER NOT NULL,
                stats_json   TEXT NOT NULL,
                PRIMARY KEY (pitcher_id, season)
            );

            CREATE TABLE IF NOT EXISTS statcast_batting (
                season       INTEGER PRIMARY KEY,
                data_json    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS statcast_pitching (
                season       INTEGER PRIMARY KEY,
                data_json    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS statcast_pitchers (
                season       INTEGER PRIMARY KEY,
                data_json    TEXT NOT NULL
            );
        """)
        self.conn.commit()

    # ── Season games ──────────────────────────────

    def is_season_games_cached(self, season: int) -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM season_games WHERE season = ?", (season,)
        ).fetchone()
        return row[0] > 0

    def save_season_games(self, season: int, games: list):
        self.conn.execute("DELETE FROM season_games WHERE season = ?", (season,))
        for g in games:
            self.conn.execute("""
                INSERT OR REPLACE INTO season_games
                (mlb_game_id, season, game_date, away_team_id, away_team_name,
                 away_team_abbr, home_team_id, home_team_name, home_team_abbr,
                 away_score, home_score, home_team_won,
                 away_pitcher_id, away_pitcher_name, home_pitcher_id, home_pitcher_name,
                 venue_id, venue_name)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                g["mlb_game_id"], season, g.get("game_date"),
                g.get("away_team_id"), g.get("away_team_name"), g.get("away_team_abbr"),
                g.get("home_team_id"), g.get("home_team_name"), g.get("home_team_abbr"),
                g.get("away_score"), g.get("home_score"), int(g.get("home_team_won", False)),
                g.get("away_pitcher_id"), g.get("away_pitcher_name"),
                g.get("home_pitcher_id"), g.get("home_pitcher_name"),
                g.get("venue_id"), g.get("venue_name"),
            ))
        self.conn.commit()

    def load_season_games(self, season: int) -> list:
        rows = self.conn.execute(
            "SELECT * FROM season_games WHERE season = ? ORDER BY game_date", (season,)
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["home_team_won"] = bool(d["home_team_won"])
            result.append(d)
        return result

    # ── Team stats ────────────────────────────────

    def save_team_stats(self, season: int, team_mlb_id: int, stat_type: str, stats: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO team_stats (team_mlb_id, season, stat_type, stats_json)
            VALUES (?, ?, ?, ?)
        """, (team_mlb_id, season, stat_type, json.dumps(stats)))
        self.conn.commit()

    def load_team_stats(self, season: int, team_mlb_id: int, stat_type: str):
        row = self.conn.execute(
            "SELECT stats_json FROM team_stats WHERE team_mlb_id=? AND season=? AND stat_type=?",
            (team_mlb_id, season, stat_type)
        ).fetchone()
        return json.loads(row[0]) if row else None

    # ── Pitcher stats ─────────────────────────────

    def save_pitcher_stats(self, season: int, pitcher_id: int, stats: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO pitcher_stats (pitcher_id, season, stats_json)
            VALUES (?, ?, ?)
        """, (pitcher_id, season, json.dumps(stats)))
        self.conn.commit()

    def load_pitcher_stats(self, season: int, pitcher_id: int):
        row = self.conn.execute(
            "SELECT stats_json FROM pitcher_stats WHERE pitcher_id=? AND season=?",
            (pitcher_id, season)
        ).fetchone()
        return json.loads(row[0]) if row else None

    # ── Statcast ──────────────────────────────────

    def save_statcast_batting(self, season: int, data: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO statcast_batting (season, data_json) VALUES (?,?)",
            (season, json.dumps(data))
        )
        self.conn.commit()

    def load_statcast_batting(self, season: int):
        row = self.conn.execute(
            "SELECT data_json FROM statcast_batting WHERE season=?", (season,)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    def save_statcast_pitching(self, season: int, data: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO statcast_pitching (season, data_json) VALUES (?,?)",
            (season, json.dumps(data))
        )
        self.conn.commit()

    def load_statcast_pitching(self, season: int):
        row = self.conn.execute(
            "SELECT data_json FROM statcast_pitching WHERE season=?", (season,)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    def save_statcast_pitchers(self, season: int, data: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO statcast_pitchers (season, data_json) VALUES (?,?)",
            (season, json.dumps({str(k): v for k, v in data.items()}))
        )
        self.conn.commit()

    def load_statcast_pitchers(self, season: int):
        row = self.conn.execute(
            "SELECT data_json FROM statcast_pitchers WHERE season=?", (season,)
        ).fetchone()
        if not row:
            return {}
        return {int(k): v for k, v in json.loads(row[0]).items()}
