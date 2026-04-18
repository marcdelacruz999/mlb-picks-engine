"""
MLB Picks Engine — SQLite Database Module
==========================================
Tracks teams, picks, results, and ROI over time.
"""

import sqlite3
import os
from datetime import datetime, date, timedelta
from typing import Optional
from config import DATABASE_PATH

# DB_PATH is the authoritative path used by get_connection().
# Tests monkeypatch this attribute to redirect to a temp database.
DB_PATH = DATABASE_PATH

_LG_AVG_RPG = 4.3  # MLB average runs per game — used as denominator for quality weight


def get_connection():
    """Return a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS teams (
        id INTEGER PRIMARY KEY,
        mlb_id INTEGER UNIQUE,
        name TEXT NOT NULL,
        abbreviation TEXT,
        division TEXT,
        league TEXT,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY,
        mlb_id INTEGER UNIQUE,
        name TEXT NOT NULL,
        team_id INTEGER,
        position TEXT,
        bats TEXT,
        throws TEXT,
        updated_at TEXT,
        FOREIGN KEY (team_id) REFERENCES teams(id)
    );

    CREATE TABLE IF NOT EXISTS pitcher_stats (
        id INTEGER PRIMARY KEY,
        player_id INTEGER,
        season INTEGER,
        era REAL,
        xera REAL,
        fip REAL,
        xfip REAL,
        whip REAL,
        k_per_9 REAL,
        bb_per_9 REAL,
        k_bb_ratio REAL,
        innings_pitched REAL,
        games_started INTEGER,
        home_era REAL,
        away_era REAL,
        last_7_era REAL,
        last_14_era REAL,
        last_30_era REAL,
        updated_at TEXT,
        FOREIGN KEY (player_id) REFERENCES players(id)
    );

    CREATE TABLE IF NOT EXISTS team_batting (
        id INTEGER PRIMARY KEY,
        team_id INTEGER,
        season INTEGER,
        avg REAL,
        obp REAL,
        slg REAL,
        ops REAL,
        woba REAL,
        wrc_plus REAL,
        k_rate REAL,
        bb_rate REAL,
        babip REAL,
        hard_hit_rate REAL,
        barrel_rate REAL,
        vs_lhp_ops REAL,
        vs_rhp_ops REAL,
        last_7_ops REAL,
        last_14_ops REAL,
        last_30_ops REAL,
        updated_at TEXT,
        FOREIGN KEY (team_id) REFERENCES teams(id)
    );

    CREATE TABLE IF NOT EXISTS bullpen_stats (
        id INTEGER PRIMARY KEY,
        team_id INTEGER,
        season INTEGER,
        era REAL,
        whip REAL,
        k_per_9 REAL,
        bb_per_9 REAL,
        save_pct REAL,
        hold_pct REAL,
        fatigue_score REAL,
        last_3_days_ip REAL,
        updated_at TEXT,
        FOREIGN KEY (team_id) REFERENCES teams(id)
    );

    CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY,
        mlb_game_id INTEGER UNIQUE,
        game_date TEXT,
        away_team_id INTEGER,
        home_team_id INTEGER,
        away_starter_id INTEGER,
        home_starter_id INTEGER,
        status TEXT DEFAULT 'scheduled',
        away_score INTEGER,
        home_score INTEGER,
        total_runs INTEGER,
        updated_at TEXT,
        FOREIGN KEY (away_team_id) REFERENCES teams(id),
        FOREIGN KEY (home_team_id) REFERENCES teams(id)
    );

    CREATE TABLE IF NOT EXISTS odds (
        id INTEGER PRIMARY KEY,
        game_id INTEGER,
        bookmaker TEXT,
        home_ml INTEGER,
        away_ml INTEGER,
        total_line REAL,
        over_price INTEGER,
        under_price INTEGER,
        captured_at TEXT,
        FOREIGN KEY (game_id) REFERENCES games(id)
    );

    CREATE TABLE IF NOT EXISTS picks (
        id INTEGER PRIMARY KEY,
        game_id INTEGER,
        pick_type TEXT,
        pick_team TEXT,
        confidence INTEGER CHECK(confidence BETWEEN 1 AND 10),
        win_probability REAL,
        edge_score REAL,
        projected_away_score REAL,
        projected_home_score REAL,
        edge_pitching TEXT,
        edge_offense TEXT,
        edge_advanced TEXT,
        edge_bullpen TEXT,
        edge_weather TEXT,
        edge_market TEXT,
        notes TEXT,
        ev_score REAL,
        ml_odds INTEGER,
        ou_odds INTEGER,
        status TEXT DEFAULT 'pending' CHECK(status IN ('pending','won','lost','push','cancelled')),
        discord_sent INTEGER DEFAULT 0,
        discord_message_id TEXT,
        created_at TEXT,
        updated_at TEXT,
        FOREIGN KEY (game_id) REFERENCES games(id)
    );

    CREATE TABLE IF NOT EXISTS daily_results (
        id INTEGER PRIMARY KEY,
        result_date TEXT UNIQUE,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        pushes INTEGER DEFAULT 0,
        roi REAL,
        best_pick TEXT,
        worst_miss TEXT,
        notes TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS analysis_log (
        id INTEGER PRIMARY KEY,
        game_date TEXT,
        mlb_game_id INTEGER,
        game TEXT,
        away_team TEXT,
        home_team TEXT,
        away_pitcher TEXT,
        home_pitcher TEXT,
        composite_score REAL,
        ml_pick_team TEXT,
        ml_win_probability REAL,
        ml_confidence INTEGER,
        ml_status TEXT DEFAULT 'pending' CHECK(ml_status IN ('pending','correct','incorrect','push')),
        ou_pick TEXT,
        ou_line REAL,
        ou_confidence INTEGER,
        ou_status TEXT DEFAULT 'pending' CHECK(ou_status IN ('pending','correct','incorrect','push','none')),
        score_pitching REAL,
        score_offense REAL,
        score_bullpen REAL,
        score_advanced REAL,
        score_momentum REAL,
        score_market REAL,
        score_weather REAL,
        actual_away_score INTEGER,
        actual_home_score INTEGER,
        actual_total INTEGER,
        created_at TEXT,
        updated_at TEXT,
        UNIQUE(game_date, mlb_game_id)
    );

    CREATE TABLE IF NOT EXISTS scratch_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_date TEXT,
        mlb_game_id INTEGER,
        side TEXT,
        old_pitcher TEXT,
        new_pitcher TEXT,
        alerted_at TEXT,
        UNIQUE(game_date, mlb_game_id, side)
    );

    CREATE TABLE IF NOT EXISTS lineup_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mlb_game_id INTEGER NOT NULL,
        game_date TEXT NOT NULL,
        ops_actual REAL,
        ops_expected REAL,
        pct_drop REAL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(mlb_game_id, game_date)
    );

    CREATE TABLE IF NOT EXISTS pitcher_game_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mlb_game_id INTEGER,
        game_date TEXT,
        pitcher_id INTEGER,
        pitcher_name TEXT,
        team_id INTEGER,
        is_starter INTEGER,
        opponent_team_id INTEGER,
        innings_pitched REAL,
        earned_runs INTEGER,
        strikeouts INTEGER,
        walks INTEGER,
        hits INTEGER,
        home_runs INTEGER,
        created_at TEXT,
        UNIQUE(mlb_game_id, pitcher_id, game_date)
    );

    CREATE TABLE IF NOT EXISTS team_game_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mlb_game_id INTEGER,
        game_date TEXT,
        team_id INTEGER,
        is_away INTEGER,
        runs INTEGER,
        hits INTEGER,
        home_runs INTEGER,
        strikeouts INTEGER,
        walks INTEGER,
        at_bats INTEGER,
        left_on_base INTEGER,
        created_at TEXT,
        UNIQUE(mlb_game_id, team_id, game_date)
    );

    CREATE TABLE IF NOT EXISTS opening_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_date TEXT NOT NULL,
        mlb_game_id INTEGER NOT NULL,
        home_ml INTEGER,
        away_ml INTEGER,
        total_line REAL,
        over_price INTEGER,
        under_price INTEGER,
        captured_at TEXT NOT NULL,
        UNIQUE(game_date, mlb_game_id)
    );

    CREATE TABLE IF NOT EXISTS batter_game_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mlb_game_id INTEGER,
        game_date TEXT,
        batter_id INTEGER,
        batter_name TEXT,
        team_id INTEGER,
        at_bats INTEGER,
        hits INTEGER,
        doubles INTEGER,
        triples INTEGER,
        home_runs INTEGER,
        rbi INTEGER,
        walks INTEGER,
        strikeouts INTEGER,
        created_at TEXT,
        UNIQUE(mlb_game_id, batter_id)
    );

    CREATE TABLE IF NOT EXISTS daily_board (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_date TEXT UNIQUE,
        message_id TEXT,
        sent_at TEXT,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS daily_ou_board (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_date TEXT UNIQUE,
        message_id TEXT,
        sent_at TEXT,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS game_totals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mlb_game_id INTEGER NOT NULL,
        game_date TEXT NOT NULL,
        home_team_id INTEGER,
        away_team_id INTEGER,
        home_team_abbr TEXT,
        away_team_abbr TEXT,
        home_runs INTEGER,
        away_runs INTEGER,
        total_runs INTEGER,
        total_line REAL,
        ou_result TEXT,
        model_projected_total REAL,
        home_sp_id INTEGER,
        away_sp_id INTEGER,
        home_sp_era REAL,
        away_sp_era REAL,
        park_factor REAL,
        temp_f REAL,
        wind_mph REAL,
        wind_dir TEXT,
        created_at TEXT,
        UNIQUE(mlb_game_id)
    );

    CREATE INDEX IF NOT EXISTS idx_analysis_log_date ON analysis_log(game_date);
    CREATE INDEX IF NOT EXISTS idx_analysis_log_game ON analysis_log(mlb_game_id);
    CREATE INDEX IF NOT EXISTS idx_games_date ON games(game_date);
    CREATE INDEX IF NOT EXISTS idx_picks_date ON picks(created_at);
    CREATE INDEX IF NOT EXISTS idx_picks_status ON picks(status);
    CREATE INDEX IF NOT EXISTS idx_pitcher_logs_pitcher ON pitcher_game_logs(pitcher_id, game_date);
    CREATE INDEX IF NOT EXISTS idx_pitcher_logs_team ON pitcher_game_logs(team_id, game_date);
    CREATE INDEX IF NOT EXISTS idx_team_logs_team ON team_game_logs(team_id, game_date);
    CREATE INDEX IF NOT EXISTS idx_batter_logs_batter ON batter_game_logs(batter_id, game_date);
    CREATE INDEX IF NOT EXISTS idx_batter_logs_team ON batter_game_logs(team_id, game_date);
    """)

    conn.commit()

    # Migration: add discord_message_id to picks if missing
    try:
        conn.execute("ALTER TABLE picks ADD COLUMN discord_message_id TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migrate existing DB: add ev_score if not present
    try:
        conn.execute("ALTER TABLE picks ADD COLUMN ev_score REAL")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    try:
        conn.execute("ALTER TABLE picks ADD COLUMN ml_odds INTEGER")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    try:
        conn.execute("ALTER TABLE picks ADD COLUMN ou_odds INTEGER")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    for col in ("score_pitching", "score_offense", "score_bullpen", "score_advanced",
                "score_momentum", "score_market", "score_weather"):
        try:
            conn.execute(f"ALTER TABLE analysis_log ADD COLUMN {col} REAL")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    try:
        conn.execute("ALTER TABLE pitcher_game_logs ADD COLUMN opponent_team_id INTEGER")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migrate: remove CHECK constraint from pick_type by recreating picks table
    # Inspect the schema directly instead of probing with an INSERT (avoids FK side-effects)
    picks_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='picks'"
    ).fetchone()
    _needs_migration = picks_sql and "pick_type" in (picks_sql["sql"] or "") and \
        "moneyline" in (picks_sql["sql"] or "")
    if _needs_migration:
        conn.execute("ALTER TABLE picks RENAME TO picks_old")
        conn.execute("""
            CREATE TABLE picks (
                id INTEGER PRIMARY KEY,
                game_id INTEGER,
                pick_type TEXT,
                pick_team TEXT,
                confidence INTEGER CHECK(confidence BETWEEN 1 AND 10),
                win_probability REAL,
                edge_score REAL,
                projected_away_score REAL,
                projected_home_score REAL,
                edge_pitching TEXT,
                edge_offense TEXT,
                edge_advanced TEXT,
                edge_bullpen TEXT,
                edge_weather TEXT,
                edge_market TEXT,
                notes TEXT,
                ev_score REAL,
                ml_odds INTEGER,
                ou_odds INTEGER,
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending','won','lost','push','cancelled')),
                discord_sent INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (game_id) REFERENCES games(id)
            )
        """)
        conn.execute("INSERT INTO picks SELECT * FROM picks_old")
        conn.execute("DROP TABLE picks_old")
        conn.commit()
        print("[DB] Migrated picks table: removed pick_type CHECK constraint.")

    # Migrate: add lineup_alerts table if not present (added 2026-04-15)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lineup_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mlb_game_id INTEGER NOT NULL,
                game_date TEXT NOT NULL,
                ops_actual REAL,
                ops_expected REAL,
                pct_drop REAL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(mlb_game_id, game_date)
            )
        """)
        conn.commit()
    except sqlite3.OperationalError:
        pass

    # ── Expand pitcher_game_logs ──
    for col, typedef in [
        ("pitch_count", "INTEGER"),
        ("batters_faced", "INTEGER"),
        ("ground_outs", "INTEGER"),
        ("fly_outs", "INTEGER"),
        ("inherited_runners", "INTEGER"),
        ("inherited_runners_scored", "INTEGER"),
    ]:
        try:
            conn.execute(f"ALTER TABLE pitcher_game_logs ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass

    # ── Expand batter_game_logs ──
    for col, typedef in [
        ("runs", "INTEGER"),
        ("stolen_bases", "INTEGER"),
        ("hit_by_pitch", "INTEGER"),
        ("plate_appearances", "INTEGER"),
    ]:
        try:
            conn.execute(f"ALTER TABLE batter_game_logs ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass

    # ── Expand team_game_logs with opponent pitching stats ──
    for col, typedef in [
        ("pitching_strikeouts", "INTEGER"),
        ("pitching_walks", "INTEGER"),
        ("pitching_hits_allowed", "INTEGER"),
        ("pitching_earned_runs", "INTEGER"),
        ("pitching_home_runs_allowed", "INTEGER"),
    ]:
        try:
            conn.execute(f"ALTER TABLE team_game_logs ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass

    conn.commit()

    conn.close()
    backfill_game_totals_abbr()
    print("[DB] Database initialized.")


# ── Pick CRUD ────────────────────────────────────────────

def save_pick(pick: dict) -> int:
    """Insert a new pick and return its id."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    c = conn.execute("""
        INSERT INTO picks
        (game_id, pick_type, pick_team, confidence, win_probability,
         edge_score, projected_away_score, projected_home_score,
         edge_pitching, edge_offense, edge_advanced, edge_bullpen, edge_weather, edge_market,
         notes, ev_score, ml_odds, ou_odds, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        pick["game_id"], pick["pick_type"], pick.get("pick_team"),
        pick["confidence"], pick["win_probability"],
        pick["edge_score"],
        pick.get("projected_away_score"), pick.get("projected_home_score"),
        pick.get("edge_pitching"), pick.get("edge_offense"),
        pick.get("edge_advanced"), pick.get("edge_bullpen"), pick.get("edge_weather"),
        pick.get("edge_market"), pick.get("notes"),
        pick.get("ev_score"),
        pick.get("ml_odds"), pick.get("ou_odds"),
        now, now
    ))
    conn.commit()
    pick_id = c.lastrowid
    conn.close()
    return pick_id


def pick_already_sent_today(game_id: int, pick_type: str) -> bool:
    """Return True if a discord-sent pick already exists today for this game + pick type."""
    conn = get_connection()
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT id FROM picks WHERE game_id=? AND pick_type=? AND discord_sent=1 AND created_at LIKE ?",
        (game_id, pick_type, f"{today}%")
    ).fetchone()
    conn.close()
    return row is not None


def get_picks_for_date(target_date: str) -> list:
    """Return all discord-sent picks created on target_date (YYYY-MM-DD)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM picks WHERE created_at LIKE ? AND discord_sent=1 ORDER BY confidence DESC",
        (f"{target_date}%",)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_today_picks() -> list:
    """Return all discord-sent picks created today."""
    return get_picks_for_date(date.today().isoformat())


def get_today_sent_picks_with_game() -> list:
    """Return today's discord-sent picks joined with mlb_game_id from games table."""
    conn = get_connection()
    today = date.today().isoformat()
    rows = conn.execute(
        """SELECT p.*, g.mlb_game_id
           FROM picks p
           JOIN games g ON p.game_id = g.id
           WHERE p.created_at LIKE ? AND p.discord_sent=1
           ORDER BY p.confidence DESC""",
        (f"{today}%",)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_picks_for_date(target_date: str) -> list:
    """Return discord-sent picks with status='pending' for target_date (YYYY-MM-DD)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM picks WHERE created_at LIKE ? AND discord_sent=1 AND status='pending' ORDER BY confidence DESC",
        (f"{target_date}%",)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_pick_status(pick_id: int, status: str):
    """Update the outcome status of a pick."""
    conn = get_connection()
    conn.execute(
        "UPDATE picks SET status=?, updated_at=? WHERE id=?",
        (status, datetime.utcnow().isoformat(), pick_id)
    )
    conn.commit()
    conn.close()


def mark_pick_sent(pick_id: int, message_id: Optional[str] = None):
    """Mark a pick as sent to Discord, optionally storing the message ID."""
    conn = get_connection()
    conn.execute(
        "UPDATE picks SET discord_sent=1, discord_message_id=? WHERE id=?",
        (message_id, pick_id)
    )
    conn.commit()
    conn.close()


def get_sent_pick_today(game_id: int, pick_type: str) -> Optional[dict]:
    """Return the discord-sent pick for this game+type today, or None."""
    conn = get_connection()
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT id, discord_message_id, confidence FROM picks "
        "WHERE game_id=? AND pick_type=? AND discord_sent=1 AND created_at LIKE ? "
        "ORDER BY id DESC LIMIT 1",
        (game_id, pick_type, f"{today}%")
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_analysis_log(entry: dict) -> int:
    """Log one game's full analysis. Called for every game each daily run."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    ou_status = "none" if not entry.get("ou_pick") else "pending"
    c = conn.execute("""
        INSERT OR IGNORE INTO analysis_log
        (game_date, mlb_game_id, game, away_team, home_team,
         away_pitcher, home_pitcher, composite_score,
         ml_pick_team, ml_win_probability, ml_confidence,
         ou_pick, ou_line, ou_confidence,
         score_pitching, score_offense, score_bullpen, score_advanced,
         score_momentum, score_market, score_weather,
         ml_status, ou_status, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        entry["game_date"], entry["mlb_game_id"], entry["game"],
        entry["away_team"], entry["home_team"],
        entry.get("away_pitcher", "TBD"), entry.get("home_pitcher", "TBD"),
        entry.get("composite_score", 0.0),
        entry["ml_pick_team"], entry["ml_win_probability"], entry["ml_confidence"],
        entry.get("ou_pick"), entry.get("ou_line"), entry.get("ou_confidence"),
        entry.get("score_pitching"), entry.get("score_offense"), entry.get("score_bullpen"),
        entry.get("score_advanced"), entry.get("score_momentum"), entry.get("score_market"),
        entry.get("score_weather"),
        "pending", ou_status, now, now
    ))
    conn.commit()
    row_id = c.lastrowid
    if not row_id:
        # Row already existed (INSERT OR IGNORE no-op) — update analysis fields
        # but preserve ml_status/ou_status grading columns
        conn.execute("""
            UPDATE analysis_log SET
                away_pitcher=?, home_pitcher=?, composite_score=?,
                ml_pick_team=?, ml_win_probability=?, ml_confidence=?,
                ou_pick=?, ou_line=?, ou_confidence=?,
                score_pitching=?, score_offense=?, score_bullpen=?,
                score_advanced=?, score_momentum=?, score_market=?, score_weather=?,
                updated_at=?
            WHERE game_date=? AND mlb_game_id=?
        """, (
            entry.get("away_pitcher", "TBD"), entry.get("home_pitcher", "TBD"),
            entry.get("composite_score", 0.0),
            entry["ml_pick_team"], entry["ml_win_probability"], entry["ml_confidence"],
            entry.get("ou_pick"), entry.get("ou_line"), entry.get("ou_confidence"),
            entry.get("score_pitching"), entry.get("score_offense"), entry.get("score_bullpen"),
            entry.get("score_advanced"), entry.get("score_momentum"), entry.get("score_market"),
            entry.get("score_weather"), now,
            entry["game_date"], entry["mlb_game_id"]
        ))
        conn.commit()
        existing = conn.execute(
            "SELECT id FROM analysis_log WHERE game_date=? AND mlb_game_id=?",
            (entry["game_date"], entry["mlb_game_id"])
        ).fetchone()
        row_id = existing["id"] if existing else 0
    conn.close()
    return row_id


def get_today_analysis_log() -> list:
    """Return all analysis_log entries for today."""
    conn = get_connection()
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT * FROM analysis_log WHERE game_date=? ORDER BY ml_confidence DESC",
        (today,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_analysis_log_for_date(target_date: str) -> list:
    """Return all analysis_log entries for a given date string (YYYY-MM-DD)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM analysis_log WHERE game_date=? ORDER BY ml_confidence DESC",
        (target_date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_analysis_log_result(log_id: int, ml_status: str, ou_status: str,
                                actual_away: int, actual_home: int, actual_total: int):
    """Grade an analysis_log entry with final score."""
    conn = get_connection()
    conn.execute("""
        UPDATE analysis_log
        SET ml_status=?, ou_status=?,
            actual_away_score=?, actual_home_score=?, actual_total=?,
            updated_at=?
        WHERE id=?
    """, (ml_status, ou_status, actual_away, actual_home, actual_total,
          datetime.utcnow().isoformat(), log_id))
    conn.commit()
    conn.close()


# ── Game CRUD ────────────────────────────────────────────

def upsert_game(game: dict) -> int:
    """Insert or update a game, return the local id."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    existing = conn.execute(
        "SELECT id FROM games WHERE mlb_game_id=?", (game["mlb_game_id"],)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE games SET game_date=?, away_team_id=?, home_team_id=?,
            away_starter_id=?, home_starter_id=?, status=?,
            away_score=?, home_score=?, total_runs=?, updated_at=?
            WHERE mlb_game_id=?
        """, (
            game["game_date"], game.get("away_team_id"), game.get("home_team_id"),
            game.get("away_starter_id"), game.get("home_starter_id"),
            game.get("status", "scheduled"),
            game.get("away_score"), game.get("home_score"), game.get("total_runs"),
            now, game["mlb_game_id"]
        ))
        game_id = existing["id"]
    else:
        c = conn.execute("""
            INSERT INTO games (mlb_game_id, game_date, away_team_id, home_team_id,
            away_starter_id, home_starter_id, status, away_score, home_score,
            total_runs, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            game["mlb_game_id"], game["game_date"],
            game.get("away_team_id"), game.get("home_team_id"),
            game.get("away_starter_id"), game.get("home_starter_id"),
            game.get("status", "scheduled"),
            game.get("away_score"), game.get("home_score"), game.get("total_runs"),
            now
        ))
        game_id = c.lastrowid

    conn.commit()
    conn.close()
    return game_id


def upsert_team(team: dict) -> int:
    """Insert or update a team, return local id."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    existing = conn.execute(
        "SELECT id FROM teams WHERE mlb_id=?", (team["mlb_id"],)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE teams SET name=?, abbreviation=?, division=?, league=?, updated_at=?
            WHERE mlb_id=?
        """, (team["name"], team.get("abbreviation"), team.get("division"),
              team.get("league"), now, team["mlb_id"]))
        team_id = existing["id"]
    else:
        c = conn.execute("""
            INSERT INTO teams (mlb_id, name, abbreviation, division, league, updated_at)
            VALUES (?,?,?,?,?,?)
        """, (team["mlb_id"], team["name"], team.get("abbreviation"),
              team.get("division"), team.get("league"), now))
        team_id = c.lastrowid

    conn.commit()
    conn.close()
    return team_id


# ── Odds ─────────────────────────────────────────────────

def save_odds(odds: dict):
    """Save a snapshot of odds for a game."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO odds (game_id, bookmaker, home_ml, away_ml,
        total_line, over_price, under_price, captured_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        odds["game_id"], odds.get("bookmaker", "consensus"),
        odds.get("home_ml"), odds.get("away_ml"),
        odds.get("total_line"), odds.get("over_price"), odds.get("under_price"),
        datetime.utcnow().isoformat()
    ))
    conn.commit()
    conn.close()


# ── Results & ROI ────────────────────────────────────────

def save_daily_results(results: dict):
    """Save or update daily results summary."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    today = date.today().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO daily_results
        (result_date, wins, losses, pushes, roi, best_pick, worst_miss, notes, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        today, results.get("wins", 0), results.get("losses", 0),
        results.get("pushes", 0), results.get("roi"),
        results.get("best_pick"), results.get("worst_miss"),
        results.get("notes"), now
    ))
    conn.commit()
    conn.close()


def get_roi_summary(days: int = 30) -> dict:
    """Calculate ROI summary over the last N days."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT status, ml_odds, ou_odds FROM picks
        WHERE created_at >= date('now', ?)
        AND status IN ('won','lost','push')
        AND discord_sent = 1
    """, (f"-{days} days",)).fetchall()
    conn.close()

    summary = {"won": 0, "lost": 0, "push": 0}
    total_profit = 0.0
    picks_with_odds = 0

    for r in rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
        odds = r["ml_odds"] or r["ou_odds"]
        if odds is None:
            continue
        picks_with_odds += 1
        if r["status"] == "won":
            payout = 100.0 / abs(odds) if odds < 0 else odds / 100.0
            total_profit += payout
        elif r["status"] == "lost":
            total_profit -= 1.0
        # push: 0 profit

    total_graded = summary["won"] + summary["lost"] + summary["push"]
    win_loss_total = summary["won"] + summary["lost"]
    summary["total"] = total_graded
    summary["win_rate"] = round(summary["won"] / win_loss_total * 100, 1) if win_loss_total > 0 else 0.0
    summary["roi_per_unit"] = round(total_profit / total_graded, 3) if total_graded > 0 else None
    summary["net_units"] = round(total_profit, 3)
    return summary


def get_model_accuracy_summary(days: int = 30) -> dict:
    """Model accuracy across all logged games (not just sent picks) over last N days."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT ml_status, ou_status, COUNT(*) as cnt
        FROM analysis_log
        WHERE game_date >= date('now', ?)
        AND ml_status IN ('correct','incorrect','push')
        GROUP BY ml_status, ou_status
    """, (f"-{days} days",)).fetchall()
    conn.close()

    ml_correct = ml_incorrect = ou_correct = ou_incorrect = 0
    for r in rows:
        if r["ml_status"] == "correct":
            ml_correct += r["cnt"]
        elif r["ml_status"] == "incorrect":
            ml_incorrect += r["cnt"]
        if r["ou_status"] == "correct":
            ou_correct += r["cnt"]
        elif r["ou_status"] == "incorrect":
            ou_incorrect += r["cnt"]

    ml_total = ml_correct + ml_incorrect
    ou_total = ou_correct + ou_incorrect
    return {
        "ml_correct": ml_correct,
        "ml_incorrect": ml_incorrect,
        "ml_total": ml_total,
        "ml_accuracy": round(ml_correct / ml_total * 100, 1) if ml_total else 0.0,
        "ou_correct": ou_correct,
        "ou_incorrect": ou_incorrect,
        "ou_total": ou_total,
        "ou_accuracy": round(ou_correct / ou_total * 100, 1) if ou_total else 0.0,
    }


# ── Scratch Alerts ───────────────────────────────────────

def pitcher_already_alerted(mlb_game_id: int, game_date: str, side: str) -> bool:
    """Return True if a scratch alert has already been sent for this game/side today."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM scratch_alerts WHERE mlb_game_id=? AND game_date=? AND side=?",
        (mlb_game_id, game_date, side)
    ).fetchone()
    conn.close()
    return row is not None


def save_scratch_alert(mlb_game_id: int, game_date: str, old_pitcher: str, new_pitcher: str, side: str):
    """Insert a scratch alert record (one per game/side per day via UNIQUE constraint)."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO scratch_alerts
               (game_date, mlb_game_id, side, old_pitcher, new_pitcher, alerted_at)
               VALUES (?,?,?,?,?,?)""",
            (game_date, mlb_game_id, side, old_pitcher, new_pitcher, now)
        )
        conn.commit()
    except Exception as e:
        print(f"[DB] Error saving scratch alert: {e}")
    finally:
        conn.close()


# ── Rolling Stats ────────────────────────────────────────

def store_boxscores(pitcher_logs: list, team_logs: list) -> None:
    """Store post-game boxscore data for all pitchers and teams."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    for p in pitcher_logs:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO pitcher_game_logs
                (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
                 opponent_team_id, innings_pitched, earned_runs, strikeouts, walks, hits, home_runs, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (p["mlb_game_id"], p["game_date"], p["pitcher_id"], p["pitcher_name"],
                  p["team_id"], int(p["is_starter"]),
                  p.get("opponent_team_id"),
                  p["innings_pitched"], p["earned_runs"], p["strikeouts"],
                  p["walks"], p["hits"], p["home_runs"], now))
        except sqlite3.DatabaseError as e:
            print(f"[DB] store_boxscores pitcher error: {e}")
    for t in team_logs:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO team_game_logs
                (mlb_game_id, game_date, team_id, is_away, runs, hits, home_runs,
                 strikeouts, walks, at_bats, left_on_base, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (t["mlb_game_id"], t["game_date"], t["team_id"], int(t["is_away"]),
                  t["runs"], t["hits"], t["home_runs"], t["strikeouts"],
                  t["walks"], t["at_bats"], t["left_on_base"], now))
        except sqlite3.DatabaseError as e:
            print(f"[DB] store_boxscores team error: {e}")
    conn.commit()
    conn.close()


def store_pitcher_game_logs(pitcher_logs: list) -> int:
    """Store pitcher game logs with full stat fields including pitch_count, batters_faced, etc."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    inserted = 0
    for log in pitcher_logs:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO pitcher_game_logs
                (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id,
                 is_starter, opponent_team_id, innings_pitched, earned_runs,
                 strikeouts, walks, hits, home_runs,
                 pitch_count, batters_faced, ground_outs, fly_outs,
                 inherited_runners, inherited_runners_scored, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                log["mlb_game_id"], log["game_date"], log["pitcher_id"],
                log.get("pitcher_name", "Unknown"), log["team_id"],
                int(log.get("is_starter", 0)), log.get("opponent_team_id"),
                log["innings_pitched"], log.get("earned_runs", 0),
                log.get("strikeouts", 0), log.get("walks", 0),
                log.get("hits", 0), log.get("home_runs", 0),
                log.get("pitch_count", 0), log.get("batters_faced", 0),
                log.get("ground_outs", 0), log.get("fly_outs", 0),
                log.get("inherited_runners", 0), log.get("inherited_runners_scored", 0),
                now,
            ))
            inserted += conn.execute("SELECT changes()").fetchone()[0]
        except sqlite3.DatabaseError as e:
            print(f"[DB] store_pitcher_game_logs error: {e}")
    conn.commit()
    conn.close()
    return inserted


def store_team_game_logs(team_logs: list) -> int:
    """Store team game logs with full stat fields including pitching stats."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    inserted = 0
    for log in team_logs:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO team_game_logs
                (mlb_game_id, game_date, team_id, is_away,
                 runs, hits, home_runs, strikeouts, walks, at_bats, left_on_base,
                 pitching_strikeouts, pitching_walks, pitching_hits_allowed,
                 pitching_earned_runs, pitching_home_runs_allowed, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                log["mlb_game_id"], log["game_date"], log["team_id"],
                int(log.get("is_away", 0)),
                log.get("runs", 0), log.get("hits", 0), log.get("home_runs", 0),
                log.get("strikeouts", 0), log.get("walks", 0),
                log.get("at_bats", 0), log.get("left_on_base", 0),
                log.get("pitching_strikeouts", 0), log.get("pitching_walks", 0),
                log.get("pitching_hits_allowed", 0), log.get("pitching_earned_runs", 0),
                log.get("pitching_home_runs_allowed", 0),
                now,
            ))
            inserted += conn.execute("SELECT changes()").fetchone()[0]
        except sqlite3.DatabaseError as e:
            print(f"[DB] store_team_game_logs error: {e}")
    conn.commit()
    conn.close()
    return inserted


def get_pitcher_rolling_stats(pitcher_id: int, days: int = 21,
                               as_of_date: str = None) -> "dict | None":
    """
    Rolling ERA, WHIP, K/9, BB/9 for a pitcher over the last N days.
    Returns None if no games found (caller falls back to season stats).
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT innings_pitched, earned_runs, strikeouts, walks, hits
        FROM pitcher_game_logs
        WHERE pitcher_id=? AND game_date > ? AND innings_pitched > 0
        ORDER BY game_date DESC
    """, (pitcher_id, cutoff.isoformat())).fetchall()
    conn.close()
    if not rows:
        return None
    total_ip = sum(r["innings_pitched"] for r in rows)
    total_er = sum(r["earned_runs"] for r in rows)
    total_k = sum(r["strikeouts"] for r in rows)
    total_bb = sum(r["walks"] for r in rows)
    total_h = sum(r["hits"] for r in rows)
    if total_ip == 0:
        return None
    return {
        "era": round(total_er / total_ip * 9, 2),
        "whip": round((total_h + total_bb) / total_ip, 3),
        "k9": round(total_k / total_ip * 9, 2),
        "bb9": round(total_bb / total_ip * 9, 2),
        "games": len(rows),
        "innings_pitched": round(total_ip, 2),
    }


def get_pitcher_rolling_stats_adjusted(pitcher_id: int, days: int = 21,
                                        as_of_date: str = None) -> "dict | None":
    """
    Opponent-quality-adjusted rolling ERA/WHIP/K9/BB9.
    Each game log is weighted by opponent_rpg / LG_AVG_RPG.
    Opponent R/G is their 14-day rolling average up to the game date.
    Falls back to equal weighting (weight=1.0) when opponent_team_id is NULL.

    Returns same shape as get_pitcher_rolling_stats().
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT p.innings_pitched, p.earned_runs, p.strikeouts, p.walks, p.hits,
               p.opponent_team_id, p.game_date
        FROM pitcher_game_logs p
        WHERE p.pitcher_id=? AND p.game_date > ? AND p.innings_pitched > 0
        ORDER BY p.game_date DESC
    """, (pitcher_id, cutoff.isoformat())).fetchall()

    if not rows:
        conn.close()
        return None

    total_ip_w = 0.0
    total_er_w = 0.0
    total_k_w = 0.0
    total_bb_w = 0.0
    total_h_w = 0.0

    for r in rows:
        ip = r["innings_pitched"]
        opp_id = r["opponent_team_id"]
        weight = 1.0  # default: no opponent data

        if opp_id:
            # Get opponent's R/G in 14 days before this game date
            opp_cutoff = (date.fromisoformat(r["game_date"]) - timedelta(days=14)).isoformat()
            opp_rows = conn.execute("""
                SELECT SUM(runs) as total_runs, COUNT(*) as games
                FROM team_game_logs
                WHERE team_id=? AND game_date > ? AND game_date < ?
            """, (opp_id, opp_cutoff, r["game_date"])).fetchone()

            if opp_rows and opp_rows["games"] and opp_rows["games"] >= 3:
                opp_rpg = (opp_rows["total_runs"] or 0) / opp_rows["games"]
                weight = opp_rpg / _LG_AVG_RPG

        total_ip_w += ip * weight
        total_er_w += r["earned_runs"] * weight
        total_k_w += r["strikeouts"] * weight
        total_bb_w += r["walks"] * weight
        total_h_w += r["hits"] * weight

    conn.close()

    if total_ip_w == 0:
        return None

    return {
        "era": round(total_er_w / total_ip_w * 9, 2),
        "whip": round((total_h_w + total_bb_w) / total_ip_w, 3),
        "k9": round(total_k_w / total_ip_w * 9, 2),
        "bb9": round(total_bb_w / total_ip_w * 9, 2),
        "games": len(rows),
        "innings_pitched": round(sum(r["innings_pitched"] for r in rows), 2),
    }


def get_pitcher_pitch_count_rolling(pitcher_id: int, days: int = 21,
                                    as_of_date: str = None) -> "Optional[dict]":
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT pitch_count, game_date
        FROM pitcher_game_logs
        WHERE pitcher_id=? AND is_starter=1 AND game_date > ? AND pitch_count > 0
        ORDER BY game_date ASC
    """, (pitcher_id, cutoff.isoformat())).fetchall()
    conn.close()
    if not rows:
        return None
    counts = [r["pitch_count"] for r in rows]
    return {
        "starts": len(counts),
        "avg_pitch_count": round(sum(counts) / len(counts), 1),
        "last_pitch_count": counts[0],
    }


def get_pitcher_gb_fb_rate(pitcher_id: int, days: int = 21,
                           as_of_date: str = None) -> "Optional[dict]":
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    row = conn.execute("""
        SELECT SUM(ground_outs) as go, SUM(fly_outs) as fo
        FROM pitcher_game_logs
        WHERE pitcher_id=? AND game_date > ? AND (ground_outs > 0 OR fly_outs > 0)
    """, (pitcher_id, cutoff.isoformat())).fetchone()
    conn.close()
    if not row or (not row["go"] and not row["fo"]):
        return None
    go = row["go"] or 0
    fo = row["fo"] or 0
    total = go + fo
    return {
        "ground_outs": go,
        "fly_outs": fo,
        "gb_pct": round(go / total, 3) if total > 0 else None,
    }


def get_bullpen_inherited_runner_rate(team_id: int, days: int = 7,
                                      as_of_date: str = None) -> "Optional[dict]":
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    row = conn.execute("""
        SELECT SUM(inherited_runners) as ir, SUM(inherited_runners_scored) as irs
        FROM pitcher_game_logs
        WHERE team_id=? AND is_starter=0 AND game_date > ? AND inherited_runners > 0
    """, (team_id, cutoff.isoformat())).fetchone()
    conn.close()
    if not row or not row["ir"]:
        return None
    ir = row["ir"]
    irs = row["irs"] or 0
    return {
        "inherited_runners": ir,
        "inherited_runners_scored": irs,
        "strand_rate": round(1 - irs / ir, 3),
    }


def get_team_stolen_base_rate(team_id: int, days: int = 14,
                              as_of_date: str = None) -> "Optional[dict]":
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    row = conn.execute("""
        SELECT SUM(stolen_bases) as sb, COUNT(DISTINCT game_date) as games
        FROM batter_game_logs
        WHERE team_id=? AND game_date > ?
    """, (team_id, cutoff.isoformat())).fetchone()
    conn.close()
    if not row or not row["games"]:
        return None
    sb = row["sb"] or 0
    games = row["games"]
    return {
        "stolen_bases": sb,
        "games": games,
        "sb_per_game": round(sb / games, 2),
    }


def get_pitcher_vs_team_history(pitcher_id: int, opponent_team_id: int, days: int = 365) -> "dict | None":
    """
    Historical performance for a specific pitcher vs a specific opponent team.
    Queries pitcher_game_logs WHERE pitcher_id=? AND opponent_team_id=? AND game_date >= cutoff
      AND innings_pitched > 0 AND is_starter = 1
    Requires >= 2 starts vs this opponent in window.
    Returns: {"starts": int, "era_vs_team": float, "whip_vs_team": float, "k9_vs_team": float, "avg_ip": float}
      era_vs_team = (earned_runs * 9) / max(innings_pitched, 0.1)
      whip_vs_team = (hits + walks) / max(innings_pitched, 0.1)
      k9_vs_team = strikeouts * 9 / max(innings_pitched, 0.1)
    Returns None if fewer than 2 starts.
    """
    cutoff = date.today() - timedelta(days=days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT innings_pitched, earned_runs, strikeouts, walks, hits
        FROM pitcher_game_logs
        WHERE pitcher_id=? AND opponent_team_id=? AND game_date >= ?
          AND innings_pitched > 0 AND is_starter = 1
        ORDER BY game_date DESC
    """, (pitcher_id, opponent_team_id, cutoff.isoformat())).fetchall()
    conn.close()
    if len(rows) < 2:
        return None
    total_ip = sum(r["innings_pitched"] for r in rows)
    total_er = sum(r["earned_runs"] for r in rows)
    total_k = sum(r["strikeouts"] for r in rows)
    total_bb = sum(r["walks"] for r in rows)
    total_h = sum(r["hits"] for r in rows)
    safe_ip = max(total_ip, 0.1)
    return {
        "starts": len(rows),
        "era_vs_team": round(total_er * 9 / safe_ip, 2),
        "whip_vs_team": round((total_h + total_bb) / safe_ip, 3),
        "k9_vs_team": round(total_k * 9 / safe_ip, 2),
        "avg_ip": round(total_ip / len(rows), 2),
    }


def get_team_batting_rolling(team_id: int, days: int = 14,
                              as_of_date: str = None) -> "dict | None":
    """
    Rolling runs/game, OBP proxy, HR/game, K% over last N days for a team.
    OBP proxy = (H + BB) / (AB + BB).
    Returns None if no games found.
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT runs, hits, home_runs, strikeouts, walks, at_bats
        FROM team_game_logs
        WHERE team_id=? AND game_date > ?
        ORDER BY game_date DESC
    """, (team_id, cutoff.isoformat())).fetchall()
    conn.close()
    if not rows:
        return None
    g = len(rows)
    total_runs = sum(r["runs"] for r in rows)
    total_hits = sum(r["hits"] for r in rows)
    total_hr = sum(r["home_runs"] for r in rows)
    total_k = sum(r["strikeouts"] for r in rows)
    total_bb = sum(r["walks"] for r in rows)
    total_ab = sum(r["at_bats"] for r in rows)
    pa = total_ab + total_bb
    return {
        "rpg": round(total_runs / g, 3),
        "obp_proxy": round((total_hits + total_bb) / pa, 3) if pa > 0 else 0.0,
        "hr_pg": round(total_hr / g, 3),
        "k_pct": round(total_k / total_ab, 3) if total_ab > 0 else 0.0,
        "games": g,
    }


def get_team_bullpen_rolling(team_id: int, days: int = 14,
                              as_of_date: str = None) -> "dict | None":
    """
    Rolling ERA, WHIP, K/9 for relief pitchers (is_starter=0) over last N days.
    Returns None if no appearances found.
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT innings_pitched, earned_runs, strikeouts, walks, hits
        FROM pitcher_game_logs
        WHERE team_id=? AND is_starter=0 AND game_date > ? AND innings_pitched > 0
        ORDER BY game_date DESC
    """, (team_id, cutoff.isoformat())).fetchall()
    conn.close()
    if not rows:
        return None
    total_ip = sum(r["innings_pitched"] for r in rows)
    total_er = sum(r["earned_runs"] for r in rows)
    total_k = sum(r["strikeouts"] for r in rows)
    total_bb = sum(r["walks"] for r in rows)
    total_h = sum(r["hits"] for r in rows)
    if total_ip == 0:
        return None
    return {
        "era": round(total_er / total_ip * 9, 2),
        "whip": round((total_h + total_bb) / total_ip, 3),
        "k9": round(total_k / total_ip * 9, 2),
        "games": len(rows),
        "innings_pitched": round(total_ip, 2),
    }


def get_bullpen_top_relievers(team_id: int, days: int = 7,
                               as_of_date: str = None) -> list:
    """
    Return top 3 relievers (by total IP) for a team over the last N days.
    Each entry: {"pitcher_id", "pitcher_name", "total_ip", "era"}.
    Returns [] if no data.
    """
    cutoff = (date.fromisoformat(as_of_date) if as_of_date else date.today()) - timedelta(days=days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT pitcher_id, pitcher_name,
               SUM(innings_pitched) as total_ip,
               SUM(earned_runs) as total_er
        FROM pitcher_game_logs
        WHERE team_id=? AND is_starter=0 AND game_date > ? AND innings_pitched > 0
        GROUP BY pitcher_id, pitcher_name
        ORDER BY total_ip DESC
        LIMIT 3
    """, (team_id, cutoff.isoformat())).fetchall()
    conn.close()
    result = []
    for r in rows:
        ip = r["total_ip"] or 0.0
        era = round(r["total_er"] / ip * 9, 2) if ip > 0 else 0.0
        result.append({
            "pitcher_id": r["pitcher_id"],
            "pitcher_name": r["pitcher_name"],
            "total_ip": round(ip, 1),
            "era": era,
        })
    return result


# ── Opening Lines ────────────────────────────────────────

def save_opening_lines(mlb_game_id: int, game_date: str, consensus: dict) -> None:
    """Store opening odds for a game. INSERT OR IGNORE — only first capture kept."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO opening_lines
            (game_date, mlb_game_id, home_ml, away_ml, total_line,
             over_price, under_price, captured_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (game_date, mlb_game_id,
              consensus.get("home_ml"), consensus.get("away_ml"),
              consensus.get("total_line"),
              consensus.get("over_price"), consensus.get("under_price"),
              now))
        conn.commit()
    except sqlite3.DatabaseError as e:
        print(f"[DB] Error saving opening lines: {e}")
    finally:
        conn.close()


def get_opening_lines(mlb_game_id: int, game_date: str) -> "dict | None":
    """Return opening odds for a game, or None if not captured."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM opening_lines WHERE mlb_game_id=? AND game_date=?",
        (mlb_game_id, game_date)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Batter Game Logs ─────────────────────────────────────

def collect_batter_boxscores(game_date: str) -> int:
    """
    Fetch completed boxscores for game_date and store per-batter lines.
    Uses the same /game/{gamePk}/boxscore endpoint as collect_boxscores().
    Skips any player with pitching stats recorded (pitchers only).
    Returns count of rows inserted.
    """
    import requests

    MLB_BASE = "https://statsapi.mlb.com/api/v1"

    # Step 1: get list of Final game PKs for the date
    schedule_url = f"{MLB_BASE}/schedule?sportId=1&date={game_date}&gameType=R"
    try:
        resp = requests.get(schedule_url, timeout=15)
        resp.raise_for_status()
        schedule = resp.json()
    except Exception as e:
        print(f"[DB] collect_batter_boxscores schedule error for {game_date}: {e}")
        return 0

    final_game_pks = [
        game["gamePk"]
        for date_entry in schedule.get("dates", [])
        for game in date_entry.get("games", [])
        if game.get("status", {}).get("abstractGameState") == "Final"
    ]

    conn = get_connection()
    now = datetime.utcnow().isoformat()
    inserted = 0

    for game_pk in final_game_pks:
        try:
            bs_url = f"{MLB_BASE}/game/{game_pk}/boxscore"
            resp = requests.get(bs_url, timeout=15)
            resp.raise_for_status()
            boxscore = resp.json()
        except Exception as e:
            print(f"[DB] collect_batter_boxscores boxscore error game {game_pk}: {e}")
            continue

        for side in ("away", "home"):
            team_data = boxscore.get("teams", {}).get(side, {})
            team_id = team_data.get("team", {}).get("id")
            if not team_id:
                continue

            batter_ids = team_data.get("batters", [])
            players = team_data.get("players", {})

            for bid in batter_ids:
                player = players.get(f"ID{bid}", {})
                stats = player.get("stats", {})

                # Skip pitchers: any player with recorded pitching stats
                pitching = stats.get("pitching", {})
                if pitching and pitching.get("inningsPitched") not in (None, "", "0.0", "0"):
                    continue

                bstats = stats.get("batting", {})
                at_bats = bstats.get("atBats", 0) or 0
                # Only record if batter actually had at-bats or walks (appeared at plate)
                walks = bstats.get("baseOnBalls", 0) or 0
                if at_bats == 0 and walks == 0:
                    continue

                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO batter_game_logs
                        (mlb_game_id, game_date, batter_id, batter_name, team_id,
                         at_bats, hits, doubles, triples, home_runs, rbi, walks,
                         strikeouts, runs, stolen_bases, hit_by_pitch,
                         plate_appearances, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        game_pk, game_date, bid,
                        player.get("person", {}).get("fullName", "Unknown"),
                        team_id,
                        at_bats,
                        bstats.get("hits", 0) or 0,
                        bstats.get("doubles", 0) or 0,
                        bstats.get("triples", 0) or 0,
                        bstats.get("homeRuns", 0) or 0,
                        bstats.get("rbi", 0) or 0,
                        walks,
                        bstats.get("strikeOuts", 0) or 0,
                        bstats.get("runs", 0) or 0,
                        bstats.get("stolenBases", 0) or 0,
                        bstats.get("hitByPitch", 0) or 0,
                        bstats.get("plateAppearances", 0) or 0,
                        now,
                    ))
                    if conn.execute("SELECT changes()").fetchone()[0]:
                        inserted += 1
                except sqlite3.DatabaseError as e:
                    print(f"[DB] collect_batter_boxscores insert error: {e}")

        import time
        time.sleep(0.2)  # polite to the API

    conn.commit()
    conn.close()
    print(f"[DB] collect_batter_boxscores {game_date}: {inserted} rows inserted")
    return inserted


def get_team_batter_hot_cold(team_id: int, days: int = 10) -> "dict | None":
    """
    Compute hot/cold batter counts for a team over the last N days.

    A batter is "hot"  if BA >= .320 with >= 12 AB in the window.
    A batter is "cold" if BA <= .180 with >= 12 AB in the window.

    Returns:
        {
          "hot_count": int,
          "cold_count": int,
          "avg_ba_10d": float,   # team-wide BA across all qualifying batters
          "sample_abs": int,     # total AB in window
        }
    Returns None if fewer than 30 total AB are available in the window.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    conn = get_connection()
    rows = conn.execute("""
        SELECT batter_id, SUM(at_bats) as ab, SUM(hits) as h
        FROM batter_game_logs
        WHERE team_id=? AND game_date > ?
        GROUP BY batter_id
        HAVING ab >= 3
    """, (team_id, cutoff)).fetchall()
    conn.close()

    if not rows:
        return None

    total_ab = sum(r["ab"] for r in rows)
    if total_ab < 30:
        return None

    total_hits = sum(r["h"] for r in rows)
    hot_count = 0
    cold_count = 0

    for r in rows:
        ab = r["ab"]
        if ab < 12:
            continue
        ba = r["h"] / ab
        if ba >= 0.320:
            hot_count += 1
        elif ba <= 0.180:
            cold_count += 1

    return {
        "hot_count": hot_count,
        "cold_count": cold_count,
        "avg_ba_10d": round(total_hits / total_ab, 3) if total_ab > 0 else 0.0,
        "sample_abs": total_ab,
    }


# ── Alias for backward compatibility ─────────────────────

def get_db_connection():
    """Alias for get_connection() — used by monitor.py."""
    return get_connection()


if __name__ == "__main__":
    init_db()
    print("[DB] Tables created / verified.")


def get_team_rolling_k_rate(team_id: int, days: int = 10) -> "float | None":
    """
    Compute team strikeout rate (K/PA) from batter_game_logs over last N days.
    PA = AB + BB. Returns None if fewer than 50 PA in the window.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    conn = get_connection()
    row = conn.execute("""
        SELECT SUM(strikeouts) as k, SUM(at_bats) + SUM(walks) as pa
        FROM batter_game_logs
        WHERE team_id=? AND game_date > ?
    """, (team_id, cutoff)).fetchone()
    conn.close()

    if not row or not row["pa"] or row["pa"] < 50:
        return None
    return round(row["k"] / row["pa"], 3)


def get_batter_rolling_ops(batter_id: int, days: int = 15) -> "dict | None":
    """
    Compute rolling OPS for a batter from batter_game_logs over last N days.
    Returns {"ops": float, "games": int} or None if insufficient data.
    OBP = (H + BB) / (AB + BB), SLG = (H + 2B + 2*3B + 3*HR) / AB
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    conn = get_connection()
    row = conn.execute("""
        SELECT
            COUNT(DISTINCT game_date) as games,
            SUM(at_bats) as ab,
            SUM(hits) as h,
            SUM(doubles) as d,
            SUM(triples) as t,
            SUM(home_runs) as hr,
            SUM(walks) as bb
        FROM batter_game_logs
        WHERE batter_id=? AND game_date > ?
    """, (batter_id, cutoff)).fetchone()
    conn.close()

    if not row or not row["games"] or not row["ab"] or row["ab"] == 0:
        return None

    ab = row["ab"]
    h = row["h"] or 0
    d = row["d"] or 0
    t = row["t"] or 0
    hr = row["hr"] or 0
    bb = row["bb"] or 0

    obp = (h + bb) / (ab + bb) if (ab + bb) > 0 else 0.0
    slg = (h + d + 2 * t + 3 * hr) / ab if ab > 0 else 0.0
    ops = round(obp + slg, 3)

    return {"ops": ops, "games": row["games"]}


# ── Daily Board ────────────────────────────────────────────

def get_daily_board(game_date: str) -> Optional[dict]:
    """Return today's board record (message_id + timestamps) or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM daily_board WHERE game_date=?", (game_date,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_daily_board(game_date: str, message_id: str) -> None:
    """Insert or update today's board record with the Discord message ID."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO daily_board (game_date, message_id, sent_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(game_date) DO UPDATE SET
            message_id=excluded.message_id,
            updated_at=excluded.updated_at
    """, (game_date, message_id, now, now))
    conn.commit()
    conn.close()


def board_needs_update(game_date: str, interval_hours: int = 3) -> bool:
    """Return True if board hasn't been sent yet today or was last sent >interval_hours ago."""
    conn = get_connection()
    row = conn.execute(
        "SELECT updated_at FROM daily_board WHERE game_date=?", (game_date,)
    ).fetchone()
    conn.close()
    if not row:
        return True
    last = datetime.fromisoformat(row["updated_at"])
    hours_elapsed = (datetime.utcnow() - last).total_seconds() / 3600
    return hours_elapsed >= interval_hours


def get_daily_ou_board(game_date: str) -> Optional[dict]:
    """Return today's O/U board record (message_id + timestamps) or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM daily_ou_board WHERE game_date=?", (game_date,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_daily_ou_board(game_date: str, message_id: str) -> None:
    """Insert or update today's O/U board record with the Discord message ID."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO daily_ou_board (game_date, message_id, sent_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(game_date) DO UPDATE SET
            message_id=excluded.message_id,
            updated_at=excluded.updated_at
    """, (game_date, message_id, now, now))
    conn.commit()
    conn.close()


def ou_board_needs_update(game_date: str, interval_hours: int = 3) -> bool:
    """Return True if O/U board hasn't been sent today or was last sent >interval_hours ago."""
    conn = get_connection()
    row = conn.execute(
        "SELECT updated_at FROM daily_ou_board WHERE game_date=?", (game_date,)
    ).fetchone()
    conn.close()
    if not row:
        return True
    last = datetime.fromisoformat(row["updated_at"])
    hours_elapsed = (datetime.utcnow() - last).total_seconds() / 3600
    return hours_elapsed >= interval_hours


def lineup_alert_already_sent(mlb_game_id: int, game_date: str) -> bool:
    """Return True if a lineup alert has already been sent for this game today."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM lineup_alerts WHERE mlb_game_id=? AND game_date=?",
        (mlb_game_id, game_date)
    ).fetchone()
    conn.close()
    return row is not None


def save_lineup_alert(mlb_game_id: int, game_date: str, ops_actual: float, ops_expected: float, pct_drop: float) -> None:
    """Record that a lineup alert was sent for this game. INSERT OR IGNORE for dedup."""
    conn = get_connection()
    conn.execute(
        """INSERT OR IGNORE INTO lineup_alerts
           (mlb_game_id, game_date, ops_actual, ops_expected, pct_drop)
           VALUES (?, ?, ?, ?, ?)""",
        (mlb_game_id, game_date, ops_actual, ops_expected, pct_drop)
    )
    conn.commit()
    conn.close()


def get_team_abbr_by_mlb_id(mlb_id) -> str:
    """Return team abbreviation for the given MLB API team ID, or '' if not found."""
    if mlb_id is None:
        return ""
    conn = get_connection()
    row = conn.execute(
        "SELECT abbreviation FROM teams WHERE mlb_id = ?", (mlb_id,)
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] else ""


def store_game_totals(records: list) -> None:
    """
    Upsert game_totals rows. Each record is a dict with keys:
      mlb_game_id, game_date, home_team_id, away_team_id, home_team_abbr, away_team_abbr,
      home_runs, away_runs, total_runs, total_line (nullable), ou_result,
      model_projected_total (nullable), home_sp_id, away_sp_id,
      home_sp_era (nullable), away_sp_era (nullable),
      park_factor (nullable), temp_f (nullable), wind_mph (nullable), wind_dir (nullable)

    Uses INSERT OR IGNORE — existing rows are not overwritten (opening-line-style preservation).
    Call store_game_totals_update() to patch in model_projected_total after analysis runs.
    """
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    for r in records:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO game_totals
                (mlb_game_id, game_date, home_team_id, away_team_id,
                 home_team_abbr, away_team_abbr,
                 home_runs, away_runs, total_runs,
                 total_line, ou_result,
                 model_projected_total,
                 home_sp_id, away_sp_id, home_sp_era, away_sp_era,
                 park_factor, temp_f, wind_mph, wind_dir, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                r["mlb_game_id"], r["game_date"],
                r.get("home_team_id"), r.get("away_team_id"),
                r.get("home_team_abbr"), r.get("away_team_abbr"),
                r.get("home_runs"), r.get("away_runs"), r.get("total_runs"),
                r.get("total_line"), r.get("ou_result"),
                r.get("model_projected_total"),
                r.get("home_sp_id"), r.get("away_sp_id"),
                r.get("home_sp_era"), r.get("away_sp_era"),
                r.get("park_factor"), r.get("temp_f"),
                r.get("wind_mph"), r.get("wind_dir"),
                now,
            ))
        except sqlite3.DatabaseError as e:
            print(f"[DB] store_game_totals error game {r.get('mlb_game_id')}: {e}")
    conn.commit()
    conn.close()


def get_game_totals_for_bias(days: int = 90) -> list:
    """
    Return game_totals rows that have both actual runs and a model projection,
    for O/U bias analysis. Ordered oldest-first.
    """
    from datetime import timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    conn = get_connection()
    rows = conn.execute("""
        SELECT mlb_game_id, game_date, home_team_abbr, away_team_abbr,
               home_runs, away_runs, total_runs,
               total_line, ou_result,
               model_projected_total,
               home_sp_era, away_sp_era,
               park_factor, temp_f, wind_mph, wind_dir
        FROM game_totals
        WHERE game_date >= ?
          AND total_runs IS NOT NULL
          AND model_projected_total IS NOT NULL
        ORDER BY game_date ASC
    """, (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_game_total_projection(mlb_game_id: int, model_projected_total: float,
                                  total_line: Optional[float] = None,
                                  ou_result: Optional[str] = None,
                                  home_sp_id: Optional[int] = None,
                                  away_sp_id: Optional[int] = None,
                                  home_sp_era: Optional[float] = None,
                                  away_sp_era: Optional[float] = None,
                                  temp_f: Optional[float] = None,
                                  wind_mph: Optional[float] = None,
                                  wind_dir: Optional[str] = None,
                                  home_team_abbr: Optional[str] = None,
                                  away_team_abbr: Optional[str] = None) -> None:
    """
    Update an existing game_totals row with the model's projected total and
    contextual fields used by analyze_ou_bias(). Called by analysis.py each
    time _analyze_over_under() evaluates a game.
    """
    conn = get_connection()
    fields = ["model_projected_total = ?"]
    values: list = [model_projected_total]
    if total_line is not None:
        fields.append("total_line = ?"); values.append(total_line)
    if ou_result is not None:
        fields.append("ou_result = ?"); values.append(ou_result)
    if home_sp_id is not None:
        fields.append("home_sp_id = ?"); values.append(home_sp_id)
    if away_sp_id is not None:
        fields.append("away_sp_id = ?"); values.append(away_sp_id)
    if home_sp_era is not None:
        fields.append("home_sp_era = ?"); values.append(home_sp_era)
    if away_sp_era is not None:
        fields.append("away_sp_era = ?"); values.append(away_sp_era)
    if temp_f is not None:
        fields.append("temp_f = ?"); values.append(temp_f)
    if wind_mph is not None:
        fields.append("wind_mph = ?"); values.append(wind_mph)
    if wind_dir is not None:
        fields.append("wind_dir = ?"); values.append(wind_dir)
    if home_team_abbr is not None:
        fields.append("home_team_abbr = ?"); values.append(home_team_abbr)
    if away_team_abbr is not None:
        fields.append("away_team_abbr = ?"); values.append(away_team_abbr)
    values.append(mlb_game_id)
    conn.execute(f"UPDATE game_totals SET {', '.join(fields)} WHERE mlb_game_id = ?", values)
    conn.commit()
    conn.close()


def get_game_totals_all(days: int = 90) -> list:
    """
    Return all game_totals rows (including those without model projection),
    for O/U result analysis by park/weather/SP tier.
    """
    from datetime import timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    conn = get_connection()
    rows = conn.execute("""
        SELECT mlb_game_id, game_date, home_team_abbr, away_team_abbr,
               home_runs, away_runs, total_runs,
               total_line, ou_result,
               model_projected_total,
               home_sp_era, away_sp_era,
               park_factor, temp_f, wind_mph, wind_dir
        FROM game_totals
        WHERE game_date >= ?
          AND total_runs IS NOT NULL
        ORDER BY game_date ASC
    """, (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def backfill_game_totals_abbr() -> int:
    """
    Backfill home_team_abbr and away_team_abbr for all game_totals rows
    where the abbreviation is currently empty or NULL. Joins against the
    teams table using mlb_id. Idempotent — safe to call repeatedly.
    Returns count of rows now having both abbreviations populated.
    """
    conn = get_connection()
    conn.execute("""
        UPDATE game_totals
        SET home_team_abbr = (
            SELECT abbreviation FROM teams WHERE mlb_id = game_totals.home_team_id
        )
        WHERE (home_team_abbr IS NULL OR home_team_abbr = '')
          AND home_team_id IS NOT NULL
    """)
    conn.execute("""
        UPDATE game_totals
        SET away_team_abbr = (
            SELECT abbreviation FROM teams WHERE mlb_id = game_totals.away_team_id
        )
        WHERE (away_team_abbr IS NULL OR away_team_abbr = '')
          AND away_team_id IS NOT NULL
    """)
    conn.commit()
    row = conn.execute(
        "SELECT COUNT(*) FROM game_totals WHERE home_team_abbr != '' AND away_team_abbr != ''"
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def update_game_total_weather(mlb_game_id: int, temp_f, wind_mph, wind_dir) -> None:
    """Update weather fields for a single game_totals row."""
    conn = get_connection()
    conn.execute(
        "UPDATE game_totals SET temp_f=?, wind_mph=?, wind_dir=? WHERE mlb_game_id=?",
        (temp_f, wind_mph, wind_dir, mlb_game_id)
    )
    conn.commit()
    conn.close()


def get_game_totals_missing_weather():
    """Return list of (mlb_game_id, game_date) for rows where temp_f IS NULL."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT mlb_game_id, game_date FROM game_totals WHERE temp_f IS NULL ORDER BY game_date"
    ).fetchall()
    conn.close()
    return rows
