"""
MLB Picks Engine — SQLite Database Module
==========================================
Tracks teams, picks, results, and ROI over time.
"""

import sqlite3
import os
from datetime import datetime, date
from config import DATABASE_PATH


def get_connection():
    """Return a connection to the SQLite database."""
    conn = sqlite3.connect(DATABASE_PATH)
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
        pick_type TEXT CHECK(pick_type IN ('moneyline','over','under')),
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
        status TEXT DEFAULT 'pending' CHECK(status IN ('pending','won','lost','push','cancelled')),
        discord_sent INTEGER DEFAULT 0,
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
        actual_away_score INTEGER,
        actual_home_score INTEGER,
        actual_total INTEGER,
        created_at TEXT,
        updated_at TEXT,
        UNIQUE(game_date, mlb_game_id)
    );

    CREATE INDEX IF NOT EXISTS idx_analysis_log_date ON analysis_log(game_date);
    CREATE INDEX IF NOT EXISTS idx_analysis_log_game ON analysis_log(mlb_game_id);
    CREATE INDEX IF NOT EXISTS idx_games_date ON games(game_date);
    CREATE INDEX IF NOT EXISTS idx_picks_date ON picks(created_at);
    CREATE INDEX IF NOT EXISTS idx_picks_status ON picks(status);
    """)

    conn.commit()

    # Migrate existing DB: add ev_score if not present
    try:
        conn.execute("ALTER TABLE picks ADD COLUMN ev_score REAL")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    conn.close()
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
         notes, ev_score, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        pick["game_id"], pick["pick_type"], pick.get("pick_team"),
        pick["confidence"], pick["win_probability"],
        pick["edge_score"],
        pick.get("projected_away_score"), pick.get("projected_home_score"),
        pick.get("edge_pitching"), pick.get("edge_offense"),
        pick.get("edge_advanced"), pick.get("edge_bullpen"), pick.get("edge_weather"),
        pick.get("edge_market"), pick.get("notes"),
        pick.get("ev_score"),
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


def get_today_picks() -> list:
    """Return all discord-sent picks created today."""
    conn = get_connection()
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT * FROM picks WHERE created_at LIKE ? AND discord_sent=1 ORDER BY confidence DESC",
        (f"{today}%",)
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


def mark_pick_sent(pick_id: int):
    """Mark a pick as sent to Discord."""
    conn = get_connection()
    conn.execute(
        "UPDATE picks SET discord_sent=1 WHERE id=?", (pick_id,)
    )
    conn.commit()
    conn.close()


def save_analysis_log(entry: dict) -> int:
    """Log one game's full analysis. Called for every game each daily run."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    ou_status = "none" if not entry.get("ou_pick") else "pending"
    c = conn.execute("""
        INSERT OR REPLACE INTO analysis_log
        (game_date, mlb_game_id, game, away_team, home_team,
         away_pitcher, home_pitcher, composite_score,
         ml_pick_team, ml_win_probability, ml_confidence,
         ou_pick, ou_line, ou_confidence,
         ml_status, ou_status, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        entry["game_date"], entry["mlb_game_id"], entry["game"],
        entry["away_team"], entry["home_team"],
        entry.get("away_pitcher", "TBD"), entry.get("home_pitcher", "TBD"),
        entry.get("composite_score", 0.0),
        entry["ml_pick_team"], entry["ml_win_probability"], entry["ml_confidence"],
        entry.get("ou_pick"), entry.get("ou_line"), entry.get("ou_confidence"),
        "pending", ou_status, now, now
    ))
    conn.commit()
    row_id = c.lastrowid
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
        SELECT status, COUNT(*) as cnt FROM picks
        WHERE created_at >= date('now', ?)
        AND status IN ('won','lost','push')
        GROUP BY status
    """, (f"-{days} days",)).fetchall()
    conn.close()

    summary = {"won": 0, "lost": 0, "push": 0}
    for r in rows:
        summary[r["status"]] = r["cnt"]

    total = summary["won"] + summary["lost"]
    summary["total"] = total
    summary["win_rate"] = round(summary["won"] / total * 100, 1) if total > 0 else 0.0
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


if __name__ == "__main__":
    init_db()
    print("[DB] Tables created / verified.")
