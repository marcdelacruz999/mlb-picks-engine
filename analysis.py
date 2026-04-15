"""
MLB Picks Engine — Analysis Engine
====================================
Implements the weighted decision model with all 7 agents.
Produces confidence scores, win probabilities, and pick recommendations.
"""

from config import WEIGHTS, MIN_CONFIDENCE, MIN_CONFIDENCE_OU, MIN_EDGE_SCORE, MAX_PICKS_PER_DAY, PARK_FACTORS, UMPIRE_TENDENCIES, MIN_EV, MIN_BATTER_GAMES, OU_K_RATE_THRESHOLD_HIGH, OU_K_RATE_THRESHOLD_LOW, OU_CONVICTION_GAP
from data_odds import implied_probability, find_value
from data_mlb import fetch_lineup_batting
import database as _analysis_db


# ══════════════════════════════════════════════
#  INDIVIDUAL AGENT SCORING FUNCTIONS
#  Each returns a score from -1.0 (strong away edge)
#  to +1.0 (strong home edge), with 0 = neutral.
# ══════════════════════════════════════════════

def score_pitching(game: dict) -> dict:
    """
    PITCHING AGENT — Compare starting pitchers.
    Factors: ERA, WHIP, K/BB, K/9, handedness matchup.
    """
    home_p = game.get("home_pitcher_stats", {})
    away_p = game.get("away_pitcher_stats", {})

    if not home_p or not away_p:
        return {"score": 0.0, "edge": "Insufficient pitcher data", "detail": {}}

    # Blend rolling stats if available
    away_rolling = game.get("away_pitcher_rolling") or {}
    home_rolling = game.get("home_pitcher_rolling") or {}
    away_g = away_rolling.get("games", 0)
    home_g = home_rolling.get("games", 0)

    # Prefer home/away split ERA if available (away SP pitching away, home SP pitching home)
    away_splits = game.get("away_pitcher_splits") or {}
    home_splits = game.get("home_pitcher_splits") or {}

    # Season ERA for blend base — use venue-specific split if available
    _ae = away_splits.get("away_era"); away_era_season = _ae if _ae is not None else _safe(away_p.get("era"))
    _he = home_splits.get("home_era"); home_era_season = _he if _he is not None else _safe(home_p.get("era"))
    _aw = away_splits.get("away_whip"); away_whip_season = _aw if _aw is not None else _safe(away_p.get("whip"))
    _hw = home_splits.get("home_whip"); home_whip_season = _hw if _hw is not None else _safe(home_p.get("whip"))

    # K9/BB9: use split if available, else season
    _ak = away_splits.get("away_k9"); away_k9_season = _ak if _ak is not None else _safe(away_p.get("k_per_9"))
    _hk = home_splits.get("home_k9"); home_k9_season = _hk if _hk is not None else _safe(home_p.get("k_per_9"))
    _ab = away_splits.get("away_bb9"); away_bb9_season = _ab if _ab is not None else _safe(away_p.get("bb_per_9"))
    _hb = home_splits.get("home_bb9"); home_bb9_season = _hb if _hb is not None else _safe(home_p.get("bb_per_9"))

    away_era  = _blend(away_era_season,  away_rolling.get("era"),  away_g)
    away_whip = _blend(away_whip_season, away_rolling.get("whip"), away_g)
    away_k9   = _blend(away_k9_season,   away_rolling.get("k9"),   away_g)
    away_bb9  = _blend(away_bb9_season,  away_rolling.get("bb9"),  away_g)

    home_era  = _blend(home_era_season,  home_rolling.get("era"),  home_g)
    home_whip = _blend(home_whip_season, home_rolling.get("whip"), home_g)
    home_k9   = _blend(home_k9_season,   home_rolling.get("k9"),   home_g)
    home_bb9  = _blend(home_bb9_season,  home_rolling.get("bb9"),  home_g)

    era_diff  = away_era  - home_era
    whip_diff = away_whip - home_whip
    k9_diff   = home_k9   - away_k9
    bb9_diff  = away_bb9  - home_bb9
    kbb_diff  = _safe(home_p.get("k_bb_ratio")) - _safe(away_p.get("k_bb_ratio"))

    # Weighted pitching score
    raw = (
        era_diff * 0.30 +
        whip_diff * 0.25 +
        k9_diff * 0.05 +
        bb9_diff * 0.10 +
        kbb_diff * 0.10
    )

    score = _clamp(raw / 3.0)

    # ── Handedness matchup adjustment ──
    # LH starters are rarer; batters tend to have worse splits vs opposite hand.
    # Proxy: if a team K-rate is above league avg AND they're facing a tough lefty,
    # apply a mild penalty on the batting side.
    home_throws = home_p.get("throws", "R")
    away_throws = away_p.get("throws", "R")
    home_b = game.get("home_batting", {})
    away_b = game.get("away_batting", {})
    lg_k_rate = 0.225  # approx MLB average K-rate

    away_k_rate = _safe(away_b.get("strikeouts")) / max(_safe(away_b.get("at_bats")), 1)
    home_k_rate = _safe(home_b.get("strikeouts")) / max(_safe(home_b.get("at_bats")), 1)

    handedness_notes = []
    # Home LHP vs K-prone away lineup
    if home_throws == "L" and away_k_rate > lg_k_rate + 0.02:
        score += 0.06
        handedness_notes.append(f"Home LHP vs K-prone away lineup ({away_k_rate:.1%} K-rate)")
    # Away LHP vs K-prone home lineup
    if away_throws == "L" and home_k_rate > lg_k_rate + 0.02:
        score -= 0.06
        handedness_notes.append(f"Away LHP vs K-prone home lineup ({home_k_rate:.1%} K-rate)")

    score = _clamp(score)

    # Determine narrative
    if score > 0.15:
        edge = f"Home SP ({home_p.get('name','?')}, {home_throws}HP) has clear pitching advantage"
    elif score < -0.15:
        edge = f"Away SP ({away_p.get('name','?')}, {away_throws}HP) has clear pitching advantage"
    else:
        edge = f"Pitching matchup even ({home_p.get('name','?')} {home_throws}HP vs {away_p.get('name','?')} {away_throws}HP)"

    if handedness_notes:
        edge += f" | {handedness_notes[0]}"

    # ── Pitcher rest adjustment ──
    rest_notes = []
    home_rest = home_p.get("days_rest")
    away_rest = away_p.get("days_rest")

    if home_rest is not None:
        if home_rest <= 3:
            score -= 0.12
            rest_notes.append(f"Home SP short rest ({home_rest}d)")
        elif home_rest in (5, 6):
            score += 0.05
            rest_notes.append(f"Home SP extra rest ({home_rest}d)")
        elif home_rest >= 8:
            score -= 0.03
            rest_notes.append(f"Home SP extended layoff ({home_rest}d) — rust risk")

    if away_rest is not None:
        if away_rest <= 3:
            score += 0.12
            rest_notes.append(f"Away SP short rest ({away_rest}d)")
        elif away_rest in (5, 6):
            score -= 0.05
            rest_notes.append(f"Away SP extra rest ({away_rest}d)")
        elif away_rest >= 8:
            score += 0.03
            rest_notes.append(f"Away SP extended layoff ({away_rest}d) — rust risk")

    score = _clamp(score)

    if rest_notes:
        edge += f" | {rest_notes[0]}"

    # Note when splits or rolling are active
    notes_parts = []
    if away_splits.get("away_era") or home_splits.get("home_era"):
        notes_parts.append("venue splits")
    if away_g >= 5 or home_g >= 5:
        notes_parts.append(f"rolling: {away_g}gs away, {home_g}gs home")
    if notes_parts:
        edge += f" [{', '.join(notes_parts)}]"

    return {
        "score": round(score, 3),
        "edge": edge,
        "detail": {
            "home_era": home_p.get("era"),
            "away_era": away_p.get("era"),
            "home_whip": home_p.get("whip"),
            "away_whip": away_p.get("whip"),
            "home_kbb": home_p.get("k_bb_ratio"),
            "away_kbb": away_p.get("k_bb_ratio"),
            "home_throws": home_throws,
            "away_throws": away_throws,
            "home_days_rest": home_rest,
            "away_days_rest": away_rest,
        }
    }


def score_offense(game: dict) -> dict:
    """
    LINEUP AGENT — Compare team offensive strength.
    Factors: OPS, OBP, SLG, runs per game.
    """
    home_b = game.get("home_batting", {})
    away_b = game.get("away_batting", {})

    if not home_b or not away_b:
        return {"score": 0.0, "edge": "Insufficient batting data", "detail": {}}

    # Blend rolling batting stats if available
    away_bat_r = game.get("away_batting_rolling") or {}
    home_bat_r = game.get("home_batting_rolling") or {}
    away_rg = away_bat_r.get("games", 0)
    home_rg = home_bat_r.get("games", 0)

    away_rpg_season = _safe(away_b.get("runs")) / max(_safe(away_b.get("games_played")), 1)
    home_rpg_season = _safe(home_b.get("runs")) / max(_safe(home_b.get("games_played")), 1)

    away_rpg = _blend(away_rpg_season, away_bat_r.get("rpg"), away_rg)
    home_rpg = _blend(home_rpg_season, home_bat_r.get("rpg"), home_rg)

    away_obp = _blend(_safe(away_b.get("obp")), away_bat_r.get("obp_proxy"), away_rg)
    home_obp = _blend(_safe(home_b.get("obp")), home_bat_r.get("obp_proxy"), home_rg)

    ops_diff = _safe(home_b.get("ops")) - _safe(away_b.get("ops"))
    obp_diff = home_obp - away_obp
    slg_diff = _safe(home_b.get("slg")) - _safe(away_b.get("slg"))
    rpg_diff = home_rpg - away_rpg

    raw = (
        ops_diff * 3.0 +       # OPS diff of .050 is significant
        obp_diff * 2.0 +
        slg_diff * 2.0 +
        rpg_diff * 0.15
    )

    # Lineup strength adjustment (only when confirmed lineups available)
    away_lineup_ids = game.get("away_lineup_ids", [])
    home_lineup_ids = game.get("home_lineup_ids", [])

    away_lineup_ops = None
    home_lineup_ops = None

    if away_lineup_ids or home_lineup_ids:
        if away_lineup_ids:
            away_stats = fetch_lineup_batting(away_lineup_ids)
            if away_stats:
                away_ops_list = []
                for s in away_stats:
                    season_ops = s.get("ops", 0) or 0
                    rolling = _analysis_db.get_batter_rolling_ops(s["player_id"])
                    if rolling and rolling["games"] >= MIN_BATTER_GAMES:
                        w = 0.8 if rolling["games"] >= 20 else 0.6
                        blended = rolling["ops"] * w + season_ops * (1 - w)
                    else:
                        blended = season_ops
                    away_ops_list.append(blended)
                away_lineup_ops = sum(away_ops_list) / len(away_ops_list)

        if home_lineup_ids:
            home_stats = fetch_lineup_batting(home_lineup_ids)
            if home_stats:
                home_ops_list = []
                for s in home_stats:
                    season_ops = s.get("ops", 0) or 0
                    rolling = _analysis_db.get_batter_rolling_ops(s["player_id"])
                    if rolling and rolling["games"] >= MIN_BATTER_GAMES:
                        w = 0.8 if rolling["games"] >= 20 else 0.6
                        blended = rolling["ops"] * w + season_ops * (1 - w)
                    else:
                        blended = season_ops
                    home_ops_list.append(blended)
                home_lineup_ops = sum(home_ops_list) / len(home_ops_list)

        away_team_ops = _safe(away_b.get("ops"))
        home_team_ops = _safe(home_b.get("ops"))

        if away_lineup_ops is not None and away_team_ops > 0:
            diff = (away_lineup_ops - away_team_ops) / away_team_ops
            raw -= diff * 0.5  # away advantage: negative score = away edge

        if home_lineup_ops is not None and home_team_ops > 0:
            diff = (home_lineup_ops - home_team_ops) / home_team_ops
            raw += diff * 0.5  # home advantage: positive score = home edge

    score = _clamp(raw / 1.5)

    if score > 0.15:
        edge = "Home lineup has offensive advantage"
    elif score < -0.15:
        edge = "Away lineup has offensive advantage"
    else:
        edge = "Offenses are comparable"

    if away_lineup_ops is not None or home_lineup_ops is not None:
        edge += " (confirmed lineup)"

    # ── Hot/cold batter streak adjustment ──
    hot_cold_notes = []
    away_team_id = game.get("away_team_mlb_id") or game.get("away_team_id")
    home_team_id = game.get("home_team_mlb_id") or game.get("home_team_id")

    if away_team_id is not None:
        away_hc = _analysis_db.get_team_batter_hot_cold(away_team_id)
        if away_hc is not None:
            net = away_hc["hot_count"] - away_hc["cold_count"]
            if net >= 2:
                score -= 0.04  # away team is hot → away edge (negative = away)
                hot_cold_notes.append(f"{away_hc['hot_count']} away batters hot (last 10d)")
            elif -net >= 2:
                score += 0.04  # away team is cold → home edge (positive = home)
                hot_cold_notes.append(f"{away_hc['cold_count']} away batters cold (last 10d)")

    if home_team_id is not None:
        home_hc = _analysis_db.get_team_batter_hot_cold(home_team_id)
        if home_hc is not None:
            net = home_hc["hot_count"] - home_hc["cold_count"]
            if net >= 2:
                score += 0.04  # home team is hot → home edge
                hot_cold_notes.append(f"{home_hc['hot_count']} home batters hot (last 10d)")
            elif -net >= 2:
                score -= 0.04  # home team is cold → away edge
                hot_cold_notes.append(f"{home_hc['cold_count']} home batters cold (last 10d)")

    score = _clamp(score)

    if hot_cold_notes:
        edge += f" | {'; '.join(hot_cold_notes)}"

    return {
        "score": round(score, 3),
        "edge": edge,
        "detail": {
            "home_ops": home_b.get("ops"),
            "away_ops": away_b.get("ops"),
            "home_rpg": round(home_rpg, 2),
            "away_rpg": round(away_rpg, 2),
        }
    }


def _bullpen_fatigue_penalty(usage: dict) -> float:
    """
    Returns a positive penalty magnitude based on bullpen innings in the last 3 days.
    Callers apply this as: score += away_penalty - home_penalty.
      ≤ 8.0 IP → 0.00 (fresh)
      8–12 IP  → 0.08 (moderate fatigue)
      > 12 IP  → 0.15 (heavy fatigue)
    """
    ip3 = usage.get("ip_last_3", 0.0)
    if ip3 > 12.0:
        return 0.15
    elif ip3 > 8.0:
        return 0.08
    return 0.0


def score_bullpen(game: dict) -> dict:
    """
    BULLPEN AGENT — Compare team bullpen strength with fatigue adjustment.
    Factors: season ERA, WHIP, K/9, save%, plus recent workload (last 3 days IP).

    Fatigue thresholds (ip_last_3):
      ≤ 8.0  → no penalty
      8–12   → -0.08 (moderate)
      > 12   → -0.15 (heavy)
    Positive score = home edge; negative = away edge.
    """
    home_bp = game.get("home_pitching", {})
    away_bp = game.get("away_pitching", {})

    if not home_bp or not away_bp:
        return {"score": 0.0, "edge": "Insufficient bullpen data", "detail": {}}

    # Blend rolling bullpen stats if available
    away_bp_r = game.get("away_bullpen_rolling") or {}
    home_bp_r = game.get("home_bullpen_rolling") or {}
    away_brg = away_bp_r.get("games", 0)
    home_brg = home_bp_r.get("games", 0)

    away_bp_era  = _blend(_safe(away_bp.get("era")),  away_bp_r.get("era"),  away_brg)
    away_bp_whip = _blend(_safe(away_bp.get("whip")), away_bp_r.get("whip"), away_brg)
    home_bp_era  = _blend(_safe(home_bp.get("era")),  home_bp_r.get("era"),  home_brg)
    home_bp_whip = _blend(_safe(home_bp.get("whip")), home_bp_r.get("whip"), home_brg)

    era_diff  = away_bp_era  - home_bp_era
    whip_diff = away_bp_whip - home_bp_whip
    k9_diff   = _safe(home_bp.get("k_per_9")) - _safe(away_bp.get("k_per_9"))

    home_sv = _safe(home_bp.get("saves"))
    home_svo = max(_safe(home_bp.get("save_opportunities")), 1)
    away_sv = _safe(away_bp.get("saves"))
    away_svo = max(_safe(away_bp.get("save_opportunities")), 1)
    save_pct_diff = (home_sv / home_svo) - (away_sv / away_svo)

    raw = (
        era_diff * 0.30 +
        whip_diff * 0.25 +
        k9_diff * 0.03 +
        save_pct_diff * 0.5
    )
    score = raw / 2.0

    # Fatigue adjustment
    home_usage = game.get("home_bullpen_usage", {})
    away_usage = game.get("away_bullpen_usage", {})

    home_penalty = _bullpen_fatigue_penalty(home_usage)
    away_penalty = _bullpen_fatigue_penalty(away_usage)
    score += away_penalty - home_penalty

    score = _clamp(score)

    # Build edge description
    fatigue_notes = []
    if home_penalty > 0:
        level = "heavily" if home_penalty >= 0.15 else "moderately"
        fatigue_notes.append(f"home pen {level} fatigued ({home_usage.get('ip_last_3', 0):.1f} IP/3d)")
    if away_penalty > 0:
        level = "heavily" if away_penalty >= 0.15 else "moderately"
        fatigue_notes.append(f"away pen {level} fatigued ({away_usage.get('ip_last_3', 0):.1f} IP/3d)")

    if score > 0.15:
        edge = "Home bullpen is stronger"
    elif score < -0.15:
        edge = "Away bullpen is stronger"
    else:
        edge = "Bullpens are comparable"

    if fatigue_notes:
        edge += f" — {', '.join(fatigue_notes)}"

    # ── Key reliever ERA ──
    home_key_rel = _analysis_db.get_bullpen_top_relievers(
        game.get("home_team_mlb_id"), days=7)
    away_key_rel = _analysis_db.get_bullpen_top_relievers(
        game.get("away_team_mlb_id"), days=7)

    key_rel_notes = []
    if home_key_rel:
        names = ", ".join(r["pitcher_name"].split()[-1] for r in home_key_rel)
        era = round(sum(r["era"] * r["total_ip"] for r in home_key_rel) /
                    max(sum(r["total_ip"] for r in home_key_rel), 0.1), 2)
        key_rel_notes.append(f"Home top pen (7d): {names} — {era:.2f} ERA")
    if away_key_rel:
        names = ", ".join(r["pitcher_name"].split()[-1] for r in away_key_rel)
        era = round(sum(r["era"] * r["total_ip"] for r in away_key_rel) /
                    max(sum(r["total_ip"] for r in away_key_rel), 0.1), 2)
        key_rel_notes.append(f"Away top pen (7d): {names} — {era:.2f} ERA")

    if key_rel_notes:
        edge += " | " + " | ".join(key_rel_notes)

    return {
        "score": round(score, 3),
        "edge": edge,
        "detail": {
            "home_bp_era": home_bp.get("era"),
            "away_bp_era": away_bp.get("era"),
            "home_bp_ip_last_3": home_usage.get("ip_last_3", 0.0),
            "away_bp_ip_last_3": away_usage.get("ip_last_3", 0.0),
        }
    }


def score_advanced(game: dict) -> dict:
    """
    ADVANCED METRICS AGENT — Statcast-powered edge detection.

    Real signals (when Statcast available):
    - xwOBA vs wOBA luck diff: team outperforming/underperforming contact quality
    - Barrel rate differential: who hits the ball harder
    - Pitcher xERA vs ERA: regression incoming for lucky/unlucky starters
    - Hard-hit rate differential

    Falls back to plate discipline + WHIP/ERA proxy if Statcast unavailable.
    """
    home_sc_bat = game.get("home_statcast_bat", {})
    away_sc_bat = game.get("away_statcast_bat", {})
    home_sc_pit = game.get("home_statcast_pit", {})
    away_sc_pit = game.get("away_statcast_pit", {})
    home_sp_sc  = game.get("home_pitcher_statcast", {})
    away_sp_sc  = game.get("away_pitcher_statcast", {})
    home_p      = game.get("home_pitcher_stats", {})
    away_p      = game.get("away_pitcher_stats", {})
    home_b      = game.get("home_batting", {})
    away_b      = game.get("away_batting", {})

    signals = []
    score = 0.0
    has_statcast = bool(home_sc_bat and away_sc_bat)

    if has_statcast:
        # ── xwOBA luck differential ──
        # woba_diff = actual wOBA - xwOBA. Positive = team is outperforming contact (lucky).
        # A lucky team is likely to regress; unlucky team likely to improve.
        home_luck = _safe(home_sc_bat.get("woba_diff"))  # + = lucky home offense
        away_luck = _safe(away_sc_bat.get("woba_diff"))  # + = lucky away offense

        # Unlucky away offense facing home team = away might bounce back
        if away_luck < -0.015:
            score -= 0.12
            signals.append(f"Away offense underperforming xwOBA by {abs(away_luck):.3f} — positive regression likely")
        elif away_luck > 0.020:
            score += 0.08
            signals.append(f"Away offense overperforming xwOBA by {away_luck:.3f} — regression risk")

        if home_luck < -0.015:
            score += 0.12
            signals.append(f"Home offense underperforming xwOBA by {abs(home_luck):.3f} — positive regression likely")
        elif home_luck > 0.020:
            score -= 0.08
            signals.append(f"Home offense overperforming xwOBA by {home_luck:.3f} — regression risk")

        # ── Barrel rate differential ──
        # Higher barrel rate = hitting the ball harder and at better angles
        home_barrel = _safe(home_sc_bat.get("barrel_pct"))
        away_barrel = _safe(away_sc_bat.get("barrel_pct"))
        barrel_diff = home_barrel - away_barrel

        if abs(barrel_diff) >= 2.0:
            score += _clamp(barrel_diff * 0.04)
            leader = "Home" if barrel_diff > 0 else "Away"
            signals.append(f"{leader} barrel rate advantage ({home_barrel:.1f}% vs {away_barrel:.1f}%)")

        # ── Hard-hit rate differential ──
        home_hh = _safe(home_sc_bat.get("hard_hit_pct"))
        away_hh = _safe(away_sc_bat.get("hard_hit_pct"))
        hh_diff = home_hh - away_hh

        if abs(hh_diff) >= 4.0:
            score += _clamp(hh_diff * 0.015)
            leader = "Home" if hh_diff > 0 else "Away"
            signals.append(f"{leader} hard-hit rate edge ({home_hh:.1f}% vs {away_hh:.1f}%)")

        # ── Pitcher xERA regression signal ──
        # era_minus_xera: negative = ERA < xERA = pitcher is lucky, ERA should rise
        #                 positive = ERA > xERA = pitcher is unlucky, ERA should fall
        if home_sp_sc:
            diff = _safe(home_sp_sc.get("era_minus_xera"))
            xera = _safe(home_sp_sc.get("xera"))
            era  = _safe(home_sp_sc.get("era"))
            if xera > 0 and diff < -0.60:  # ERA much lower than xERA = getting lucky
                score -= 0.12
                signals.append(f"Home SP luck warning: ERA {era:.2f} vs xERA {xera:.2f} — regression risk")
            elif xera > 0 and diff > 0.75:  # ERA much higher than xERA = getting unlucky
                score += 0.10
                signals.append(f"Home SP undervalued: ERA {era:.2f} but xERA {xera:.2f} — improvement likely")

        if away_sp_sc:
            diff = _safe(away_sp_sc.get("era_minus_xera"))
            xera = _safe(away_sp_sc.get("xera"))
            era  = _safe(away_sp_sc.get("era"))
            if xera > 0 and diff < -0.60:
                score += 0.12
                signals.append(f"Away SP luck warning: ERA {era:.2f} vs xERA {xera:.2f} — regression risk")
            elif xera > 0 and diff > 0.75:
                score -= 0.10
                signals.append(f"Away SP undervalued: ERA {era:.2f} but xERA {xera:.2f} — improvement likely")

    else:
        # ── Fallback: plate discipline + WHIP/ERA proxy ──
        home_avg = _safe(home_b.get("avg"))
        home_obp = _safe(home_b.get("obp"))
        away_avg = _safe(away_b.get("avg"))
        away_obp = _safe(away_b.get("obp"))

        home_discipline = home_obp - home_avg
        away_discipline = away_obp - away_avg

        if home_discipline > away_discipline + 0.015:
            score += 0.12
            signals.append("Home has better plate discipline (walk-driven OBP)")
        elif away_discipline > home_discipline + 0.015:
            score -= 0.12
            signals.append("Away has better plate discipline (walk-driven OBP)")

        if home_p:
            era = _safe(home_p.get("era"))
            whip = _safe(home_p.get("whip"))
            if whip > 1.35 and era < 3.50:
                score -= 0.10
                signals.append(f"Home SP may regress: ERA {era} vs WHIP {whip}")

        if away_p:
            era = _safe(away_p.get("era"))
            whip = _safe(away_p.get("whip"))
            if whip > 1.35 and era < 3.50:
                score += 0.10
                signals.append(f"Away SP may regress: ERA {era} vs WHIP {whip}")

        # K-rate mismatch
        home_k_rate = _safe(home_b.get("strikeouts")) / max(_safe(home_b.get("at_bats")), 1)
        away_k_rate = _safe(away_b.get("strikeouts")) / max(_safe(away_b.get("at_bats")), 1)
        home_p_k9 = _safe(home_p.get("k_per_9")) if home_p else 0
        away_p_k9 = _safe(away_p.get("k_per_9")) if away_p else 0

        if away_k_rate > 0.24 and home_p_k9 > 9.0:
            score += 0.15
            signals.append("Away lineup K-prone vs high-K home starter")
        if home_k_rate > 0.24 and away_p_k9 > 9.0:
            score -= 0.15
            signals.append("Home lineup K-prone vs high-K away starter")

    score = _clamp(score)
    edge = "; ".join(signals) if signals else (
        "Statcast: no significant edge detected" if has_statcast
        else "No significant advanced edge detected"
    )

    return {
        "score": round(score, 3),
        "edge": edge,
        "detail": {
            "statcast_available": has_statcast,
            "home_barrel_pct": home_sc_bat.get("barrel_pct"),
            "away_barrel_pct": away_sc_bat.get("barrel_pct"),
            "home_xwoba": home_sc_bat.get("xwoba"),
            "away_xwoba": away_sc_bat.get("xwoba"),
        }
    }


def score_momentum(game: dict) -> dict:
    """
    MOMENTUM & CONTEXT AGENT — Recent streaks and situational factors.
    """
    home_r = game.get("home_record", {})
    away_r = game.get("away_record", {})

    score = 0.0
    signals = []

    # Win streak bonus
    home_streak = home_r.get("streak_number", 0) if home_r.get("streak_type") == "W" else 0
    away_streak = away_r.get("streak_number", 0) if away_r.get("streak_type") == "W" else 0

    # Losing streak penalty
    home_lstreak = home_r.get("streak_number", 0) if home_r.get("streak_type") == "L" else 0
    away_lstreak = away_r.get("streak_number", 0) if away_r.get("streak_type") == "L" else 0

    if home_streak >= 5:
        score += 0.2
        signals.append(f"Home on {home_streak}-game win streak")
    elif home_streak >= 3:
        score += 0.1
        signals.append(f"Home on {home_streak}-game win streak")

    if away_streak >= 5:
        score -= 0.2
        signals.append(f"Away on {away_streak}-game win streak")
    elif away_streak >= 3:
        score -= 0.1
        signals.append(f"Away on {away_streak}-game win streak")

    if home_lstreak >= 4:
        score -= 0.15
        signals.append(f"Home on {home_lstreak}-game losing streak")
    if away_lstreak >= 4:
        score += 0.15
        signals.append(f"Away on {away_lstreak}-game losing streak")

    # Win percentage differential
    home_wpct = _safe(home_r.get("win_pct"))
    away_wpct = _safe(away_r.get("win_pct"))
    wpct_diff = home_wpct - away_wpct

    if abs(wpct_diff) > 0.100:
        score += _clamp(wpct_diff * 1.5)
        if wpct_diff > 0:
            signals.append(f"Home has significantly better record ({home_wpct:.3f} vs {away_wpct:.3f})")
        else:
            signals.append(f"Away has significantly better record ({away_wpct:.3f} vs {home_wpct:.3f})")

    # Travel fatigue penalty (away team only)
    away_travel = game.get("away_travel", {})
    if away_travel:
        road_games = away_travel.get("consecutive_road_games", 0)
        tz_changes = away_travel.get("timezone_changes_last_5d", 0)

        travel_penalty = 0.0
        travel_notes = []

        if road_games >= 5:
            travel_penalty += 0.04
            travel_notes.append(f"Away on extended road trip ({road_games} games)")

        if tz_changes >= 2:
            travel_penalty += 0.05
            travel_notes.append(f"Away crossed timezones {tz_changes}x in last 5 days")

        # Cap combined penalty
        travel_penalty = min(travel_penalty, 0.08)

        if travel_penalty > 0:
            score += travel_penalty  # positive = home advantage (away penalized)
            signals.extend(travel_notes)

    score = _clamp(score)
    edge = "; ".join(signals) if signals else "No significant momentum edge"

    return {"score": round(score, 3), "edge": edge, "detail": {}}


def score_weather(game: dict) -> dict:
    """
    WEATHER & BALLPARK AGENT
    Uses Open-Meteo forecast data attached to the game dict.
    Factors: wind (speed + direction), temperature, rain chance.
    Positive score = favors OVER (high-scoring conditions).
    Negative score = favors UNDER (pitcher-friendly conditions).
    """
    wx = game.get("weather", {})
    if not wx:
        return {
            "score": 0.0,
            "edge": "Weather data unavailable",
            "detail": {}
        }

    score = 0.0
    notes = []

    temp_f = wx.get("temp_f")
    wind_mph = wx.get("wind_mph", 0)
    wind_dir = wx.get("wind_dir", "")
    precip = wx.get("precip_chance", 0)
    conditions = wx.get("conditions", "")

    # Temperature: cold suppresses offense, heat is neutral
    if temp_f is not None:
        if temp_f < 45:
            score -= 0.15
            notes.append(f"Cold ({temp_f}°F) — suppresses offense")
        elif temp_f < 55:
            score -= 0.07
            notes.append(f"Cool ({temp_f}°F) — slightly pitcher-friendly")
        elif temp_f > 85:
            score += 0.05
            notes.append(f"Hot ({temp_f}°F) — ball carries well")
        else:
            notes.append(f"Temp {temp_f}°F (neutral)")

    # Wind: out = hitter-friendly, in = pitcher-friendly
    if wind_mph >= 10:
        out_dirs = {"E", "SE", "NE"}   # blowing toward OF in most parks
        in_dirs = {"W", "SW", "NW"}
        if wind_dir in out_dirs:
            bonus = min(0.20, wind_mph * 0.008)
            score += bonus
            notes.append(f"Wind {wind_mph}mph {wind_dir} (out — hitter-friendly)")
        elif wind_dir in in_dirs:
            penalty = min(0.20, wind_mph * 0.008)
            score -= penalty
            notes.append(f"Wind {wind_mph}mph {wind_dir} (in — pitcher-friendly)")
        else:
            notes.append(f"Wind {wind_mph}mph {wind_dir} (crosswind, neutral)")
    else:
        notes.append(f"Wind {wind_mph}mph (calm)")

    # Rain / storms: increases uncertainty, slight negative for totals
    if precip >= 70:
        score -= 0.10
        notes.append(f"High rain chance ({precip}%) — game may be delayed/shortened")
    elif precip >= 40:
        score -= 0.05
        notes.append(f"Rain chance {precip}%")

    # Severe conditions (thunderstorm, heavy rain)
    if any(w in conditions for w in ("Thunderstorm", "Heavy rain", "Heavy showers")):
        score -= 0.10
        notes.append(f"Severe weather: {conditions}")

    # ── Park factor ──
    home_abbr = game.get("home_team_abbr", "")
    park_factor = PARK_FACTORS.get(home_abbr, 1.00)
    park_adj = (park_factor - 1.0) * 0.5  # scale: ±0.28 max → ±0.14 score adj
    score += park_adj
    if abs(park_factor - 1.0) >= 0.03:
        label = "Hitter-friendly" if park_factor > 1.0 else "Pitcher-friendly"
        notes.append(f"{label} park ({home_abbr}, factor {park_factor:.2f})")

    # ── Umpire ──
    hp_umpire = game.get("hp_umpire", "")
    ump_data = UMPIRE_TENDENCIES.get(hp_umpire, {}) if hp_umpire else {}
    ump_run_factor = ump_data.get("run_factor", 0.0)
    if ump_run_factor:
        score += ump_run_factor
        direction = "more runs" if ump_run_factor > 0 else "fewer runs"
        notes.append(f"HP Ump {hp_umpire}: {direction} zone ({ump_run_factor:+.2f})")
    elif hp_umpire:
        notes.append(f"HP Ump: {hp_umpire}")

    score = _clamp(score)

    edge_parts = []
    if conditions and temp_f:
        edge_parts.append(f"{conditions}, {temp_f}°F")
    if wind_mph >= 5:
        edge_parts.append(f"Wind {wind_mph}mph {wind_dir}")
    if precip >= 20:
        edge_parts.append(f"Rain {precip}%")
    if abs(park_factor - 1.0) >= 0.03:
        edge_parts.append(f"Park {park_factor:.2f}x")
    if hp_umpire:
        edge_parts.append(f"Ump: {hp_umpire}")
    edge_str = " | ".join(edge_parts) if edge_parts else "; ".join(notes[:2])

    return {
        "score": round(score, 3),
        "edge": edge_str,
        "detail": {**wx, "park_factor": park_factor, "hp_umpire": hp_umpire},
    }


def score_market(game: dict, odds_data: dict) -> dict:
    """
    MARKET VALUE AGENT — Compare our model probability against market odds.
    """
    if not odds_data:
        return {"score": 0.0, "edge": "No odds data available", "detail": {}}

    consensus = odds_data.get("consensus", {})
    home_ml = consensus.get("home_ml")
    away_ml = consensus.get("away_ml")

    if not home_ml or not away_ml:
        return {"score": 0.0, "edge": "Incomplete odds data", "detail": {}}

    home_implied = implied_probability(home_ml)
    away_implied = implied_probability(away_ml)

    return {
        "score": 0.0,  # Will be calculated in the main analysis after model prob is known
        "edge": "",
        "detail": {
            "home_ml": home_ml,
            "away_ml": away_ml,
            "home_implied_prob": round(home_implied, 3),
            "away_implied_prob": round(away_implied, 3),
            "total_line": consensus.get("total_line"),
            "over_price": consensus.get("over_price"),
            "under_price": consensus.get("under_price"),
            "home_rl": consensus.get("home_rl"),
            "away_rl": consensus.get("away_rl"),
            "home_rl_price": consensus.get("home_rl_price"),
            "away_rl_price": consensus.get("away_rl_price"),
        }
    }


# ══════════════════════════════════════════════
#  MASTER ANALYSIS — Combine all agents
# ══════════════════════════════════════════════

def analyze_game(game: dict, odds_data: dict = None) -> dict:
    """
    Run all agents and produce a final analysis for a single game.
    Returns the full analysis dict.
    """
    # Run each agent
    pitching = score_pitching(game)
    offense = score_offense(game)
    bullpen = score_bullpen(game)
    advanced = score_advanced(game)
    momentum = score_momentum(game)
    weather = score_weather(game)
    market = score_market(game, odds_data)

    # ── Weighted composite score ──
    # Positive = home edge, Negative = away edge
    composite = (
        pitching["score"] * WEIGHTS["pitching"] +
        offense["score"] * WEIGHTS["offense"] +
        bullpen["score"] * WEIGHTS["bullpen"] +
        advanced["score"] * WEIGHTS["advanced"] +
        momentum["score"] * WEIGHTS["momentum"] +
        weather["score"] * WEIGHTS["weather"]
        # Market weight applied separately after probability calculation
    )

    # ── Convert to win probability ──
    # Composite in [-1, 1] → probability via sigmoid-like transform
    # 0.0 composite → 50% (with home-field bump to ~52%)
    HOME_FIELD_ADVANTAGE = 0.04  # ~4% bump for home team
    base_prob = 0.50 + (composite * 0.35) + HOME_FIELD_ADVANTAGE
    home_win_prob = _clamp_prob(base_prob)
    away_win_prob = 1.0 - home_win_prob

    # ── Market value edge ──
    market_edge = 0.0
    market_narrative = "No odds data"
    if odds_data and odds_data.get("consensus"):
        consensus = odds_data["consensus"]
        home_ml = consensus.get("home_ml")
        away_ml = consensus.get("away_ml")

        if home_ml and away_ml:
            home_implied = implied_probability(home_ml)
            away_implied = implied_probability(away_ml)

            home_value = find_value(home_win_prob, home_implied)
            away_value = find_value(away_win_prob, away_implied)

            if home_value > away_value:
                market_edge = home_value
                market_narrative = f"Home has +{home_value:.1%} edge vs market (implied {home_implied:.1%}, model {home_win_prob:.1%})"
            else:
                market_edge = -away_value
                market_narrative = f"Away has +{away_value:.1%} edge vs market (implied {away_implied:.1%}, model {away_win_prob:.1%})"

            market["score"] = _clamp(market_edge * 5)  # Scale for weighting
            market["edge"] = market_narrative

    # Recalculate with market weight
    final_composite = composite + market["score"] * WEIGHTS["market"]

    # ── Determine pick direction ──
    if final_composite > 0:
        pick_side = "home"
        pick_team = game.get("home_team_name", "Home")
        pick_prob = home_win_prob
    else:
        pick_side = "away"
        pick_team = game.get("away_team_name", "Away")
        pick_prob = away_win_prob

    edge_score = abs(final_composite)

    # ── Confidence score (1-10) ──
    confidence = _edge_to_confidence(edge_score)

    # ── Projected score ──
    projected = _project_score(game, odds_data)

    # ── Over/Under analysis ──
    ou_pick = _analyze_over_under(game, odds_data, projected)

    # ── F5 pick (strong SP edge + weak opponent bullpen) ──
    f5_odds = game.get("f5_odds", {})
    # score_bullpen() sign convention: positive = home pen stronger, negative = away pen stronger.
    # For a home pick (pitching > 0), opponent is away — away pen is weak when bullpen score > 0
    #   → negate so weak opponent = negative number, matching gate <= -0.10
    # For an away pick (pitching < 0), opponent is home — home pen is weak when bullpen score < 0
    #   → use as-is
    _bp = bullpen["score"]
    opponent_bullpen_score = -_bp if pitching["score"] > 0 else _bp
    f5_pick = _analyze_f5_pick(game, f5_odds, pitching["score"], opponent_bullpen_score)

    # ── Lineup status ──
    home_lineup_confirmed = game.get("home_lineup_confirmed", False)
    away_lineup_confirmed = game.get("away_lineup_confirmed", False)
    lineups_confirmed = home_lineup_confirmed and away_lineup_confirmed
    lineup_status = "Lineups confirmed ✅" if lineups_confirmed else (
        "Lineup TBD ⏳ — re-analysis will fire when confirmed"
    )

    # Hard-cap confidence when SPs are unknown — starter is the heaviest weighted
    # factor; a TBD pitcher means the pitching agent scored 0.0 (neutral) on bad data.
    # Applies to BOTH ML and O/U confidence — unknown SP invalidates total projections too.
    away_pitcher_known = game.get("away_pitcher_name", "TBD") not in ("TBD", "", None)
    home_pitcher_known = game.get("home_pitcher_name", "TBD") not in ("TBD", "", None)
    if not away_pitcher_known and not home_pitcher_known:
        confidence = min(confidence, 1)  # both TBD — unpickable
        if ou_pick.get("pick"):
            ou_pick["confidence"] = min(ou_pick["confidence"], 1)
    elif not away_pitcher_known or not home_pitcher_known:
        confidence = min(confidence, 3)  # one TBD — heavily penalized
        if ou_pick.get("pick"):
            ou_pick["confidence"] = min(ou_pick["confidence"], 3)

    # Penalize confidence when lineups not yet posted — offense agent scored on
    # projected/average lineup, not actual. -1 point keeps borderline picks
    # from going out on incomplete data; they'll re-qualify once lineups confirm.
    if not lineups_confirmed:
        confidence = max(1, confidence - 1)

    # ── Rust-risk + weak bullpen cap ──
    # Pattern identified Apr 13-14: both ML losses had (a) picked team's SP on extended
    # layoff (>=8d rust risk) AND (b) that team's bullpen ERA > 5.0.
    # The pitching agent scores the SP as an "advantage" even with the layoff flag,
    # but a rusty starter + bad pen = high blowup risk. Cap at 6 (below send threshold).
    BULLPEN_ERA_RUST_THRESHOLD = 5.0
    _p_detail = pitching.get("detail", {})
    _b_detail = bullpen.get("detail", {})
    _home_rest = _p_detail.get("home_days_rest") or 0
    _away_rest = _p_detail.get("away_days_rest") or 0
    _home_bp_era = _b_detail.get("home_bp_era") or 0.0
    _away_bp_era = _b_detail.get("away_bp_era") or 0.0
    if pick_side == "home" and _home_rest >= 8 and _home_bp_era > BULLPEN_ERA_RUST_THRESHOLD:
        confidence = min(confidence, 6)
    elif pick_side == "away" and _away_rest >= 8 and _away_bp_era > BULLPEN_ERA_RUST_THRESHOLD:
        confidence = min(confidence, 6)

    # ── Assemble result ──
    analysis = {
        "game": f"{game.get('away_team_name', '?')} @ {game.get('home_team_name', '?')}",
        "away_team": game.get("away_team_name", "?"),
        "home_team": game.get("home_team_name", "?"),
        "away_pitcher": game.get("away_pitcher_name", "TBD"),
        "home_pitcher": game.get("home_pitcher_name", "TBD"),
        "mlb_game_id": game.get("mlb_game_id"),
        "game_time_utc": game.get("game_time_utc", ""),
        "status": game.get("status", "Scheduled"),
        "lineup_status": lineup_status,
        "lineups_confirmed": lineups_confirmed,

        # Moneyline analysis
        "ml_pick_side": pick_side,
        "ml_pick_team": pick_team,
        "ml_win_probability": round(pick_prob * 100, 1),
        "ml_edge_score": round(edge_score, 3),
        "ml_confidence": confidence,

        # Over/Under analysis
        "ou_pick": ou_pick,

        # F5 pick
        "f5_pick": f5_pick,

        # Projected score
        "projected_away_score": projected["away"],
        "projected_home_score": projected["home"],
        "projected_total": projected["total"],

        # Agent details
        "agents": {
            "pitching": pitching,
            "offense": offense,
            "bullpen": bullpen,
            "advanced": advanced,
            "momentum": momentum,
            "weather": weather,
            "market": market,
        },

        "composite_score": round(final_composite, 3),
    }

    return analysis


def _project_score(game: dict, odds_data: dict) -> dict:
    """Estimate projected final score."""
    home_b = game.get("home_batting", {})
    away_b = game.get("away_batting", {})

    home_rpg = _safe(home_b.get("runs")) / max(_safe(home_b.get("games_played")), 1)
    away_rpg = _safe(away_b.get("runs")) / max(_safe(away_b.get("games_played")), 1)

    if home_rpg == 0:
        home_rpg = 4.3  # league average fallback
    if away_rpg == 0:
        away_rpg = 4.3

    # Adjust by opponent pitching quality
    home_p_era = _safe(game.get("home_pitching", {}).get("era"))
    away_p_era = _safe(game.get("away_pitching", {}).get("era"))
    lg_avg_era = 4.20  # approximate league average

    # Away team scores adjusted by home pitching; Home team by away pitching
    away_projected = away_rpg * (home_p_era / lg_avg_era) if home_p_era > 0 else away_rpg
    home_projected = home_rpg * (away_p_era / lg_avg_era) if away_p_era > 0 else home_rpg

    # Apply park factor (Coors adds runs, Oracle Park suppresses them)
    home_abbr = game.get("home_team_abbr", "")
    park_factor = PARK_FACTORS.get(home_abbr, 1.00)
    away_projected *= park_factor
    home_projected *= park_factor

    # ── Weather adjustment to run total (suggestion #2) ──
    # Temp and wind direction directly affect how many runs score —
    # apply these to the projection, not just the ML composite score.
    wx = game.get("weather", {})
    temp_f = wx.get("temp_f")
    wind_mph = wx.get("wind_mph", 0)
    wind_dir = wx.get("wind_dir", "")
    wx_multiplier = 1.0
    if temp_f is not None:
        if temp_f < 45:
            wx_multiplier -= 0.06   # cold suppresses offense
        elif temp_f < 55:
            wx_multiplier -= 0.03   # cool, slightly pitcher-friendly
        elif temp_f > 85:
            wx_multiplier += 0.04   # hot, ball carries
    if wind_mph >= 10:
        out_dirs = {"E", "SE", "NE"}   # compass dirs from _wind_direction_label() — blowing toward OF
        in_dirs  = {"W", "SW", "NW"}
        if wind_dir in out_dirs:
            wx_multiplier += min(0.10, wind_mph * 0.004)   # hitter-friendly
        elif wind_dir in in_dirs:
            wx_multiplier -= min(0.10, wind_mph * 0.004)   # pitcher-friendly
    away_projected *= wx_multiplier
    home_projected *= wx_multiplier

    # Blend with total line if available
    if odds_data and odds_data.get("consensus", {}).get("total_line"):
        total_line = odds_data["consensus"]["total_line"]
        model_total = away_projected + home_projected
        blended_total = (model_total * 0.6) + (total_line * 0.4)
        ratio = blended_total / max(model_total, 0.1)
        away_projected *= ratio
        home_projected *= ratio

    return {
        "away": round(away_projected, 1),
        "home": round(home_projected, 1),
        "total": round(away_projected + home_projected, 1),
    }


def _analyze_over_under(game: dict, odds_data: dict, projected: dict) -> dict:
    """Determine if there's an over/under edge."""
    if not odds_data or not odds_data.get("consensus", {}).get("total_line"):
        return {"pick": None, "confidence": 0, "edge": "No total line available"}

    # ── Suggestion #3: Block O/U when SP data is thin (<5 rolling games) ──
    # Thin SP data = unreliable projected total
    away_g = (game.get("away_pitcher_rolling") or {}).get("games", 0)
    home_g = (game.get("home_pitcher_rolling") or {}).get("games", 0)
    # Only hard-block when BOTH pitchers have zero rolling games (truly no data)
    # One thin side or early season (<5g) caps confidence but still shows projection
    if away_g == 0 and home_g == 0:
        return {"pick": None, "confidence": 0, "edge": f"SP data too thin (away {away_g}g, home {home_g}g) — O/U unreliable"}
    elif away_g < 5 or home_g < 5:
        thin_side = "away" if away_g < 5 else "home"
        thin_g = away_g if away_g < 5 else home_g
        _sp_thin_note = f"{thin_side} SP thin ({thin_g}g rolling)"
    else:
        _sp_thin_note = None

    total_line = odds_data["consensus"]["total_line"]
    model_total = projected["total"]
    diff = model_total - total_line

    if abs(diff) < 0.5:
        return {"pick": None, "confidence": 0, "edge": "Projected total too close to line"}

    if diff > 0:
        pick = "over"
        edge_desc = f"Model projects {model_total} runs vs line of {total_line} (+{diff:.1f})"
    else:
        pick = "under"
        edge_desc = f"Model projects {model_total} runs vs line of {total_line} ({diff:.1f})"

    # Confidence based on how far off the line
    if abs(diff) >= 2.0:
        conf = 9
    elif abs(diff) >= 1.5:
        conf = 8
    elif abs(diff) >= 1.0:
        conf = 7
    elif abs(diff) >= 0.5:
        conf = 6
    else:
        conf = 5

    # ── K-rate strikeout signal ──
    # High combined K/PA → pitching-dominated game → nudge under
    # Low combined K/PA → contact-heavy game → nudge over
    away_team_id = game.get("away_team_mlb_id") or game.get("away_team_id")
    home_team_id = game.get("home_team_mlb_id") or game.get("home_team_id")
    k_notes = []
    if away_team_id is not None and home_team_id is not None:
        away_krate = _analysis_db.get_team_rolling_k_rate(away_team_id)
        home_krate = _analysis_db.get_team_rolling_k_rate(home_team_id)
        if away_krate is not None and home_krate is not None:
            combined_krate = (away_krate + home_krate) / 2
            if combined_krate >= OU_K_RATE_THRESHOLD_HIGH:
                if pick == "under":
                    conf = min(10, conf + 0.5)
                    k_notes.append(f"High K-rate ({combined_krate:.3f}) supports under")
                else:
                    k_notes.append(f"High K-rate ({combined_krate:.3f}) cuts against over")
            elif combined_krate <= OU_K_RATE_THRESHOLD_LOW:
                if pick == "over":
                    conf = min(10, conf + 0.5)
                    k_notes.append(f"Low K-rate ({combined_krate:.3f}) supports over")
                else:
                    k_notes.append(f"Low K-rate ({combined_krate:.3f}) cuts against under")
    if k_notes:
        edge_desc += f" | {'; '.join(k_notes)}"

    # ── Suggestion #3 cont: cap confidence when one SP is thin ──
    if _sp_thin_note:
        conf = min(conf, 7)
        edge_desc += f" | ⚠️ {_sp_thin_note}"

    # ── Suggestion #4: O/U-specific bullpen ERA signal ──
    # Both pens hot → nudge OVER; both cold → nudge UNDER
    home_key_rel = _analysis_db.get_bullpen_top_relievers(game.get("home_team_mlb_id"), days=7)
    away_key_rel = _analysis_db.get_bullpen_top_relievers(game.get("away_team_mlb_id"), days=7)
    bp_notes = []
    if home_key_rel and away_key_rel:
        home_bp_era = sum(r["era"] * r["total_ip"] for r in home_key_rel) / max(sum(r["total_ip"] for r in home_key_rel), 0.1)
        away_bp_era = sum(r["era"] * r["total_ip"] for r in away_key_rel) / max(sum(r["total_ip"] for r in away_key_rel), 0.1)
        combined_bp_era = (home_bp_era + away_bp_era) / 2
        if combined_bp_era > 4.5:
            if pick == "over":
                conf = min(10, conf + 1)
                bp_notes.append(f"Both pens shaky ({combined_bp_era:.2f} ERA, supports OVER)")
            else:
                bp_notes.append(f"Both pens shaky ({combined_bp_era:.2f} ERA, cuts against UNDER)")
        elif combined_bp_era < 3.0:
            if pick == "under":
                conf = min(10, conf + 1)
                bp_notes.append(f"Both pens sharp ({combined_bp_era:.2f} ERA, supports UNDER)")
            else:
                bp_notes.append(f"Both pens sharp ({combined_bp_era:.2f} ERA, cuts against OVER)")
    if bp_notes:
        edge_desc += f" | {'; '.join(bp_notes)}"

    # ── Suggestion #5: Park factor tier note ──
    # Flag high/low run-environment parks so O/U results can be tracked by tier
    home_abbr = game.get("home_team_abbr", "")
    park_factor = PARK_FACTORS.get(home_abbr, 1.00)
    if park_factor >= 1.08:
        edge_desc += f" | 🏟️ Hitter's park ({home_abbr} pf={park_factor:.2f})"
    elif park_factor <= 0.92:
        edge_desc += f" | 🏟️ Pitcher's park ({home_abbr} pf={park_factor:.2f})"

    return {"pick": pick, "confidence": int(round(conf)), "edge": edge_desc, "total_line": total_line}


def _analyze_f5_pick(game: dict, f5_odds: dict, pitching_score: float,
                     opponent_bullpen_score: float = 0.0) -> "dict | None":
    """
    Determine if there's an F5 (First 5 Innings) pick.
    Fires when:
      - |pitching_score| >= 0.20  (SP has a clear edge)
      - opponent_bullpen_score <= -0.10  (opponent pen is meaningfully weak)

    The caller resolves direction: if picking home (pitching_score > 0),
    pass away bullpen score as opponent_bullpen_score; if picking away,
    pass home bullpen score.

    Pick format:
      {"pick": "f5_home" | "f5_away", "pick_team": str,
       "pick_type": "f5_ml",
       "confidence": int, "edge": str, "ml_odds": int}
    """
    if not f5_odds or not f5_odds.get("consensus"):
        return None

    consensus = f5_odds["consensus"]
    if not consensus.get("home_ml") or not consensus.get("away_ml"):
        return None

    # Gate 1: SP must have a clear edge
    if abs(pitching_score) < 0.20:
        return None

    # Gate 2: Opponent bullpen must be meaningfully weak
    if opponent_bullpen_score > -0.10:
        return None

    if pitching_score > 0:
        pick = "f5_home"
        pick_team = game.get("home_team_name", "Home")
        ml_odds = consensus.get("home_ml")
        direction = "Home SP advantage"
    else:
        pick = "f5_away"
        pick_team = game.get("away_team_name", "Away")
        ml_odds = consensus.get("away_ml")
        direction = "Away SP advantage"

    # Confidence: based on pitching score magnitude
    score_abs = abs(pitching_score)
    if score_abs >= 0.40:
        conf = 9
    elif score_abs >= 0.30:
        conf = 8
    else:
        conf = 7

    edge = (f"F5 {direction} (pitching {pitching_score:+.3f}, opp pen {opponent_bullpen_score:+.3f}) — "
            f"SP quality isolated, weak opponent pen neutralized")

    return {
        "pick": pick,
        "pick_team": pick_team,
        "pick_type": "f5_ml",
        "confidence": conf,
        "ml_odds": ml_odds,
        "edge": edge,
        "f5_total_line": consensus.get("total_line"),
    }


# ══════════════════════════════════════════════
#  RISK AGENT — Filter and approve only strong picks
# ══════════════════════════════════════════════

def build_watchlist(analyses: list, approved: list) -> list:
    """
    WATCHLIST — Games near the threshold worth monitoring before first pitch.
    Confidence 5-6, not approved, but close enough that a line move or
    pitcher scratch could push them over.
    """
    approved_ids = {p["mlb_game_id"] for p in approved}
    watchlist = []

    for a in analyses:
        if a["mlb_game_id"] in approved_ids:
            continue
        if a["ml_confidence"] in (5, 6):
            watchlist.append({
                "game": a["game"],
                "mlb_game_id": a["mlb_game_id"],
                "confidence": a["ml_confidence"],
                "edge_score": a["ml_edge_score"],
                "pick_team": a["ml_pick_team"],
                "win_probability": a["ml_win_probability"],
                "reason": "Near threshold — monitor for lineup/line changes before first pitch",
            })
        elif a.get("ou_pick", {}).get("confidence") in (5, 6):
            ou = a["ou_pick"]
            watchlist.append({
                "game": a["game"],
                "mlb_game_id": a["mlb_game_id"],
                "confidence": ou["confidence"],
                "edge_score": a["ml_edge_score"],
                "pick_team": f"{ou['pick'].upper()} {ou.get('total_line', '')}",
                "win_probability": a["ml_win_probability"],
                "reason": "O/U near threshold — watch for weather or line movement",
            })

    watchlist.sort(key=lambda x: x["confidence"], reverse=True)
    return watchlist[:5]


def kelly_stake(win_prob_pct: float, ml_odds) -> float:
    """
    Half-Kelly stake sizing. Returns stake multiplier (0.25 to 2.0).

    full_kelly = (b*p - q) / b
      b = payout per $1 staked
      p = win probability (decimal)
      q = 1 - p

    We use half-Kelly to reduce variance.
    Floor: 0.25x — always bet at least a quarter unit.
    Cap: 2.0x — never bet more than double.
    Returns 1.0 when ml_odds is None (no sizing info).
    """
    if not ml_odds:
        return 1.0
    p = win_prob_pct / 100.0
    q = 1.0 - p
    if ml_odds < 0:
        b = 100.0 / abs(ml_odds)
    else:
        b = ml_odds / 100.0
    if b <= 0:
        return 1.0
    full_kelly = (b * p - q) / b
    half_kelly = full_kelly * 0.5
    return round(max(0.25, min(half_kelly, 2.0)), 2)


def _calculate_ev(win_prob_pct: float, ml_odds) -> "float | None":
    """
    Calculate Expected Value per unit staked.
    win_prob_pct: model's win probability (0-100 scale, e.g. 62.4)
    ml_odds: American moneyline odds for the picked team (e.g. -150, +130)
    Returns EV per $1 staked, or None if odds unavailable.

    EV = (win_prob * payout_per_unit) - (loss_prob * 1.0)
    payout_per_unit:
      negative odds (e.g. -150): 100 / abs(odds)  → -150 → 0.667
      positive odds (e.g. +130): odds / 100        → +130 → 1.30
    """
    if ml_odds is None or ml_odds == 0:
        return None
    # Reject implausible odds — real juice is never tighter than ±15
    # (e.g. -14 would imply +700 payout, which is a data error)
    if abs(ml_odds) < 15:
        return None
    win_prob = win_prob_pct / 100.0
    loss_prob = 1.0 - win_prob
    if ml_odds < 0:
        payout = 100.0 / abs(ml_odds)
    else:
        payout = ml_odds / 100.0
    return round(win_prob * payout - loss_prob, 4)


def risk_filter(analyses: list) -> list:
    """
    RISK AGENT — Only approve picks that meet strict quality thresholds.
    Returns approved picks, sorted by confidence.
    """
    approved = []

    for a in analyses:
        # Moneyline pick evaluation
        if (a["ml_confidence"] >= MIN_CONFIDENCE and
                a["ml_edge_score"] >= MIN_EDGE_SCORE):

            # EV gate for moneyline picks
            market_detail = a["agents"]["market"]["detail"]
            pick_ml_odds = (market_detail.get("home_ml") if a["ml_pick_side"] == "home"
                            else market_detail.get("away_ml"))
            ev = _calculate_ev(a["ml_win_probability"], pick_ml_odds)

            if ev is not None and ev < MIN_EV:
                print(f"[EV GATE] Rejected {a['ml_pick_team']} (conf {a['ml_confidence']}, "
                      f"EV {ev:.4f} at {pick_ml_odds})")
            else:
                pick_dict = {
                    "type": "moneyline",
                    "game": a["game"],
                    "away_team": a["away_team"],
                    "home_team": a["home_team"],
                    "pick_team": a["ml_pick_team"],
                    "pick_type": "moneyline",
                    "confidence": a["ml_confidence"],
                    "win_probability": a["ml_win_probability"],
                    "edge_score": a["ml_edge_score"],
                    "projected_away_score": a["projected_away_score"],
                    "projected_home_score": a["projected_home_score"],
                    "edge_pitching": a["agents"]["pitching"]["edge"],
                    "edge_offense": a["agents"]["offense"]["edge"],
                    "edge_advanced": a["agents"]["advanced"]["edge"],
                    "edge_bullpen": a["agents"]["bullpen"]["edge"],
                    "edge_weather": a["agents"]["weather"]["edge"],
                    "edge_market": a["agents"]["market"]["edge"],
                    "notes": a.get("lineup_status", ""),
                    "mlb_game_id": a["mlb_game_id"],
                    "game_time_utc": a.get("game_time_utc", ""),
                    "analysis": a,
                    "ml_odds": pick_ml_odds,
                    "ev_score": ev,
                    "kelly_fraction": kelly_stake(a["ml_win_probability"], pick_ml_odds),
                }
                approved.append(pick_dict)

        # Over/Under pick evaluation
        ou = a.get("ou_pick", {})
        if ou.get("pick") and ou.get("confidence", 0) >= MIN_CONFIDENCE_OU:
            market_detail = a["agents"]["market"]["detail"]
            ou_odds = (market_detail.get("over_price") if ou["pick"] == "over"
                       else market_detail.get("under_price"))
            # O/U edge = line gap normalized (diff of 1.0 run ≈ edge 0.12)
            total_line = ou.get("total_line") or 0
            projected_total = a["projected_away_score"] + a["projected_home_score"]
            run_gap = abs(projected_total - total_line) if total_line else 0
            ou_edge = round(run_gap / 8.0, 3)
            if ou_edge < MIN_EDGE_SCORE:
                print(f"[EDGE GATE] O/U rejected: {ou['pick'].upper()} "
                      f"(ou_edge {ou_edge:.3f} < {MIN_EDGE_SCORE})")
                continue
            if run_gap < OU_CONVICTION_GAP:
                print(f"[CONVICTION GATE] O/U rejected: {ou['pick'].upper()} "
                      f"(gap {run_gap:.1f} runs < {OU_CONVICTION_GAP} required)")
                continue
            ev_ou = _calculate_ev(ou["confidence"] / 10 * 100, ou_odds)

            if ev_ou is not None and ev_ou < MIN_EV:
                print(f"[EV GATE] O/U rejected: {ou['pick'].upper()} "
                      f"(conf {ou['confidence']}, EV {ev_ou:.4f} at {ou_odds})")
            else:
                ou_dict = {
                    "type": "over_under",
                    "game": a["game"],
                    "away_team": a["away_team"],
                    "home_team": a["home_team"],
                    "pick_team": ou["pick"].upper(),
                    "pick_type": ou["pick"],
                    "confidence": ou["confidence"],
                    "win_probability": a["ml_win_probability"],
                    "edge_score": ou_edge,
                    "projected_away_score": a["projected_away_score"],
                    "projected_home_score": a["projected_home_score"],
                    "edge_pitching": a["agents"]["pitching"]["edge"],
                    "edge_offense": a["agents"]["offense"]["edge"],
                    "edge_advanced": a["agents"]["advanced"]["edge"],
                    "edge_bullpen": a["agents"]["bullpen"]["edge"],
                    "edge_weather": a["agents"]["weather"]["edge"],
                    "edge_market": ou["edge"],
                    "notes": f"Total line: {ou.get('total_line', '?')} | {a.get('lineup_status', '')}",
                    "mlb_game_id": a["mlb_game_id"],
                    "game_time_utc": a.get("game_time_utc", ""),
                    "analysis": a,
                    "ou_odds": ou_odds,
                    "ev_score": ev_ou,
                    "kelly_fraction": kelly_stake(ou["confidence"] / 10 * 100, ou_odds),
                }
                approved.append(ou_dict)

        # F5 ML pick evaluation
        f5 = a.get("f5_pick")
        if f5 and f5.get("confidence", 0) >= MIN_CONFIDENCE:
            f5_ml_odds = f5.get("ml_odds")
            ev_f5 = _calculate_ev(f5["confidence"] / 10 * 100, f5_ml_odds)

            if ev_f5 is not None and ev_f5 < MIN_EV:
                print(f"[EV GATE] F5 rejected: {f5['pick_team']} "
                      f"(conf {f5['confidence']}, EV {ev_f5:.4f} at {f5_ml_odds})")
            else:
                f5_dict = {
                    "type": "f5",
                    "game": a["game"],
                    "away_team": a["away_team"],
                    "home_team": a["home_team"],
                    "pick_team": f5["pick_team"],
                    "pick_type": f5["pick_type"],  # "f5_ml"
                    "confidence": f5["confidence"],
                    "win_probability": a["ml_win_probability"],
                    "edge_score": a["ml_edge_score"],
                    "projected_away_score": a["projected_away_score"],
                    "projected_home_score": a["projected_home_score"],
                    "edge_pitching": a["agents"]["pitching"]["edge"],
                    "edge_offense": a["agents"]["offense"]["edge"],
                    "edge_advanced": a["agents"]["advanced"]["edge"],
                    "edge_bullpen": a["agents"]["bullpen"]["edge"],
                    "edge_weather": a["agents"]["weather"]["edge"],
                    "edge_market": f5["edge"],
                    "notes": f"F5 ML | F5 total: {f5.get('f5_total_line', '?')} | {a.get('lineup_status', '')}",
                    "mlb_game_id": a["mlb_game_id"],
                    "game_time_utc": a.get("game_time_utc", ""),
                    "analysis": a,
                    "ml_odds": f5_ml_odds,
                    "ev_score": ev_f5,
                    "kelly_fraction": kelly_stake(f5["confidence"] / 10 * 100, f5_ml_odds),
                }
                approved.append(f5_dict)

    # Sort by confidence (highest first) and cap volume
    approved.sort(key=lambda x: x["confidence"], reverse=True)
    approved = approved[:MAX_PICKS_PER_DAY]

    if not approved:
        print("[RISK] PASS — No picks meet quality thresholds today.")
    else:
        print(f"[RISK] Approved {len(approved)} picks (max {MAX_PICKS_PER_DAY}).")

    return approved


# ══════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════

def _safe(val) -> float:
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0

def _clamp(val: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))

def _blend(season_val: float, rolling_val, rolling_games: int) -> float:
    """
    Blend season stat with rolling stat based on number of rolling games available.
    Gracefully degrades to season-only when rolling data is sparse.
      < 5 games  → 100% season
      5–9 games  → 40% rolling / 60% season
      10–19 games → 60% rolling / 40% season
      ≥ 20 games → 75% rolling / 25% season
    """
    if rolling_val is None or rolling_games < 5:
        return season_val
    if rolling_games < 10:
        w = 0.4
    elif rolling_games < 20:
        w = 0.6
    else:
        w = 0.75
    return rolling_val * w + season_val * (1 - w)

def _clamp_prob(val: float) -> float:
    return max(0.01, min(0.99, val))

def _edge_to_confidence(edge_score: float) -> int:
    """Convert edge score to 1-10 confidence."""
    if edge_score >= 0.40:
        return 10
    elif edge_score >= 0.35:
        return 9
    elif edge_score >= 0.28:
        return 8
    elif edge_score >= 0.20:
        return 7
    elif edge_score >= 0.15:
        return 6
    elif edge_score >= 0.10:
        return 5
    elif edge_score >= 0.06:
        return 4
    elif edge_score >= 0.03:
        return 3
    else:
        return 2
