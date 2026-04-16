"""
MLB Picks Engine — Discord Webhook Module
===========================================
Sends pick alerts, updates, and daily recaps to a single Discord channel.
"""

import requests
import json
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo
from config import DISCORD_WEBHOOK_URL


def send_pick(pick: dict) -> Optional[str]:
    """
    Send a high-confidence pick alert to Discord.
    Returns the Discord message ID on success, None on failure.
    """
    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD] No webhook URL configured — printing pick locally.")
        print(_format_pick_message(pick))
        return None

    payload = {"content": _format_pick_message(pick)}

    try:
        resp = requests.post(
            f"{DISCORD_WEBHOOK_URL}?wait=true",
            json=payload,
            timeout=10
        )
        if resp.status_code == 200:
            message_id = resp.json().get("id")
            print(f"[DISCORD] Pick sent: {pick.get('game', '?')} (message {message_id})")
            return message_id
        else:
            print(f"[DISCORD] Failed ({resp.status_code}): {resp.text}")
            return None
    except Exception as e:
        print(f"[DISCORD] Error sending pick: {e}")
        return None


def send_pick_edit(message_id: str, pick: dict) -> bool:
    """
    Edit an existing pick card in Discord in-place via PATCH.
    Returns True if the edit succeeded.
    """
    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD] No webhook URL — printing pick edit locally.")
        print(_format_pick_message(pick))
        return False

    patch_url = f"{DISCORD_WEBHOOK_URL}/messages/{message_id}"
    payload = {"content": _format_pick_message(pick)}

    try:
        resp = requests.patch(patch_url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"[DISCORD] Pick updated: {pick.get('game', '?')} (message {message_id})")
            return True
        else:
            print(f"[DISCORD] Pick edit failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[DISCORD] Error editing pick: {e}")
        return False


def send_update(update: dict) -> bool:
    """Send a pick update alert (pitcher scratch, weather change, etc.)."""
    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD] No webhook URL — printing update locally.")
        print(_format_update_message(update))
        return False

    payload = {"content": _format_update_message(update)}

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        return resp.status_code == 204
    except Exception as e:
        print(f"[DISCORD] Error sending update: {e}")
        return False


def send_results(results: dict) -> bool:
    """Send the daily results recap to Discord."""
    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD] No webhook URL — printing results locally.")
        print(_format_results_message(results))
        return False

    payload = {"content": _format_results_message(results)}

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        return resp.status_code == 204
    except Exception as e:
        print(f"[DISCORD] Error sending results: {e}")
        return False


# ══════════════════════════════════════════════
#  MESSAGE FORMATTERS
# ══════════════════════════════════════════════

def _format_game_time(game_time_utc: str) -> str:
    """Convert UTC ISO game time to Eastern time string, e.g. 'April 11, 2026 — 7:10 PM ET'."""
    if not game_time_utc:
        return ""
    try:
        dt_utc = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
        dt_pt = dt_utc.astimezone(ZoneInfo("America/Los_Angeles"))
        date_str = dt_pt.strftime("%B %d, %Y")
        time_str = dt_pt.strftime("%-I:%M %p PT")
        return f"{date_str} — {time_str}"
    except Exception:
        return ""


def _format_pick_message(pick: dict) -> str:
    """Format a pick into the required Discord message structure."""

    pick_label = pick.get("pick_team", "?")
    pick_type = pick.get("pick_type", "moneyline")

    if pick_type == "moneyline":
        pick_display = f"{pick_label} ML"
    elif pick_type == "f5_ml":
        pick_display = f"{pick_label} F5 ML (First 5 Innings)"
    elif pick_type == "over":
        pick_display = f"OVER {pick.get('notes', '').replace('Total line: ', '')}"
    elif pick_type == "under":
        pick_display = f"UNDER {pick.get('notes', '').replace('Total line: ', '')}"
    else:
        pick_display = pick_label

    # Build odds line from analysis data
    odds_str = _format_odds_line(pick)

    game_time_str = _format_game_time(pick.get("game_time_utc", ""))

    msg = (
        f"🚨⚾ **MLB HIGH-CONFIDENCE PICK** ⚾🚨\n"
        f"\n"
        f"🏟️ **Game:** {pick.get('game', '?')}\n"
    )
    if game_time_str:
        msg += f"🕐 **Date/Time:** {game_time_str}\n"
    msg += (
        f"🎯 **Pick:** {pick_display}\n"
        f"💪 **Confidence:** {pick.get('confidence', '?')}/10\n"
        f"📈 **Win Probability:** {pick.get('win_probability', '?')}%\n"
    )
    ev = pick.get("ev_score")
    if ev is not None:
        ev_str = f"{ev:+.3f}"
        ev_emoji = "💰" if ev > 0 else "⚠️"
        msg += f"{ev_emoji} **Expected Value:** {ev_str} per unit\n"
    kelly = pick.get("kelly_fraction")
    if kelly is not None:
        msg += f"🎲 **Stake:** {kelly:.2f}x units\n"
    msg += (
        f"🔢 **Projected Score:** {pick.get('away_team', '?')} {pick.get('projected_away_score', '?')}"
        f" - {pick.get('home_team', '?')} {pick.get('projected_home_score', '?')}\n"
    )

    if odds_str:
        msg += f"\n📊 **Current Odds:**\n{odds_str}\n"

    msg += (
        f"\n🔍 **Edge Summary:**\n"
        f"⚾ Pitching: {pick.get('edge_pitching', 'N/A')}\n"
        f"🏏 Offense: {pick.get('edge_offense', 'N/A')}\n"
        f"📡 Advanced (Statcast): {pick.get('edge_advanced', 'N/A')}\n"
        f"🔥 Bullpen: {pick.get('edge_bullpen', 'N/A')}\n"
        f"🌤️ Weather: {pick.get('edge_weather', 'N/A')}\n"
        f"💹 Market: {pick.get('edge_market', 'N/A')}\n"
    )

    if pick.get("notes"):
        msg += f"\n📝 **Notes:** {pick['notes']}\n"

    now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
    msg += f"\n🔄 *Updated {now_pt.strftime('%-I:%M %p')} PT*"

    return msg


def _format_odds_line(pick: dict) -> str:
    """Format the current odds block from the analysis odds_data."""
    analysis = pick.get("analysis", {})
    if not analysis:
        return ""

    agents = analysis.get("agents", {})
    market_detail = agents.get("market", {}).get("detail", {})

    away = pick.get("away_team", "Away")
    home = pick.get("home_team", "Home")

    away_ml  = market_detail.get("away_ml")
    home_ml  = market_detail.get("home_ml")
    home_rl  = market_detail.get("home_rl")
    away_rl  = market_detail.get("away_rl")
    home_rl_price = market_detail.get("home_rl_price")
    away_rl_price = market_detail.get("away_rl_price")
    total    = market_detail.get("total_line")
    over_p   = market_detail.get("over_price")
    under_p  = market_detail.get("under_price")

    def fmt_odds(v, is_point=False):
        if v is None:
            return "N/A"
        if is_point:
            # Preserve .5 for run line points (e.g. +1.5, -1.5)
            s = f"{v:+.1f}" if v % 1 != 0 else f"{v:+.0f}"
            return s
        return f"+{int(v)}" if v > 0 else str(int(v))

    lines = []

    if away_ml and home_ml:
        lines.append(f"- ML: {away} {fmt_odds(away_ml)} / {home} {fmt_odds(home_ml)}")

    if home_rl is not None and home_rl_price and away_rl_price:
        away_rl_val = away_rl if away_rl is not None else -home_rl
        lines.append(
            f"- RL: {away} {fmt_odds(away_rl_val, is_point=True)} ({fmt_odds(away_rl_price)}) / "
            f"{home} {fmt_odds(home_rl, is_point=True)} ({fmt_odds(home_rl_price)})"
        )

    if total:
        o_str = f" O {fmt_odds(over_p)}" if over_p else ""
        u_str = f" U {fmt_odds(under_p)}" if under_p else ""
        lines.append(f"- Total: {total}{o_str} /{u_str}")

    return "\n".join(lines)


def _format_update_message(update: dict) -> str:
    """Format a pick update message."""
    msg = (
        f"⚠️ **MLB PICK UPDATE**\n"
        f"\n"
        f"**Game:** {update.get('game', '?')}\n"
        f"**Original Pick:** {update.get('original_pick', '?')}\n"
        f"**Update:** {update.get('update', '?')}\n"
        f"**Action:** {update.get('action', '?')}\n"
        f"\n"
        f"**Reason:**\n"
        f"- {update.get('reason', 'N/A')}\n"
    )
    return msg


def _format_results_message(results: dict) -> str:
    """Format the daily results recap."""
    today = date.today().strftime("%B %d, %Y")

    wins = results.get("wins", 0)
    losses = results.get("losses", 0)
    pushes = results.get("pushes", 0)
    total = wins + losses
    roi = results.get("roi", 0)
    pick_lines = results.get("pick_lines", [])
    all_games_lines = results.get("all_games_lines", [])
    ml_correct = results.get("ml_correct", 0)
    ml_incorrect = results.get("ml_incorrect", 0)
    ou_correct = results.get("ou_correct", 0)
    ou_incorrect = results.get("ou_incorrect", 0)

    win_rate = round(wins / total * 100, 1) if total > 0 else 0
    roi_str = f"+{roi}%" if roi > 0 else f"{roi}%"

    ml_total = ml_correct + ml_incorrect
    ml_pct = round(ml_correct / ml_total * 100, 1) if ml_total > 0 else 0
    ou_total = ou_correct + ou_incorrect
    ou_pct = round(ou_correct / ou_total * 100, 1) if ou_total > 0 else 0

    msg = f"📊 **MLB DAILY RESULTS — {today}**\n\n"

    # Sent picks with win/loss results
    if pick_lines:
        msg += "**🎯 Today's Picks:**\n"
        msg += "\n".join(pick_lines) + "\n\n"

    msg += (
        f"**Record:** {wins}W - {losses}L"
        + (f" - {pushes}P" if pushes else "")
        + f"  |  **Win Rate:** {win_rate}%  |  **ROI:** {roi_str}\n"
    )

    # Full game board — all games with model ML + O/U calls
    if all_games_lines:
        msg += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"**📋 All Games — Model Results** _(🎯 = pick sent)_\n"
        msg += "\n".join(all_games_lines) + "\n"
        msg += f"\n**ML:** {ml_correct}W - {ml_incorrect}L ({ml_pct}%)"
        if ou_total > 0:
            msg += f"  |  **O/U:** {ou_correct}W - {ou_incorrect}L ({ou_pct}%)"
        msg += "\n"

    return msg


def _format_nightly_report(
    results: dict,
    log_entries: list,
    sent_picks_by_game: dict,
    mlb_to_local: dict,
) -> str:
    """
    Format the compact nightly report with three sections:
    1. Confidence Picks (sent picks with result)
    2. ML Board (all games)
    3. O/U Board (only games with O/U pick)

    Args:
        results: grading results dict from run_results()
        log_entries: list of analysis_log dicts (with ml_status, ou_status, scores)
        sent_picks_by_game: {local_game_id: [pick_dict, ...]}
        mlb_to_local: {mlb_game_id: local_game_id}
    """
    today = date.today().strftime("%B %d, %Y")
    wins = results.get("wins", 0)
    losses = results.get("losses", 0)
    roi = results.get("roi", 0.0)
    ml_correct = results.get("ml_correct", 0)
    ml_incorrect = results.get("ml_incorrect", 0)
    ou_correct = results.get("ou_correct", 0)
    ou_incorrect = results.get("ou_incorrect", 0)

    roi_str = f"+{roi}%" if roi > 0 else f"{roi}%"

    lines = [f"⚾ **MLB NIGHTLY REPORT — {today}**", ""]

    # ── Section 1: Confidence Picks ──────────────────────────
    lines.append("🎯 **CONFIDENCE PICKS**  ━━━━━━━━━━━━━━━━━━━━")

    sent_any = False
    for entry in sorted(log_entries, key=lambda e: e.get("ml_confidence", 0), reverse=True):
        mlb_game_id = entry.get("mlb_game_id")
        local_id = mlb_to_local.get(mlb_game_id)
        game_picks = sent_picks_by_game.get(local_id, [])
        ml_picks = [p for p in game_picks if p["pick_type"] in ("moneyline", "f5_ml")]
        if not ml_picks:
            continue

        pick = ml_picks[0]
        away = entry.get("away_team", "?")
        home = entry.get("home_team", "?")
        away_short = away.split()[-1][:3].upper()
        home_short = home.split()[-1][:3].upper()
        pick_team = pick.get("pick_team", "?")
        pick_short = pick_team.split()[-1]
        conf = pick.get("confidence", "?")
        win_prob = pick.get("win_probability", 0)
        odds = pick.get("ml_odds", "")
        odds_str = f" {odds:+d}" if isinstance(odds, int) else ""
        away_sc = entry.get("actual_away_score", 0) or 0
        home_sc = entry.get("actual_home_score", 0) or 0

        # Score string: winner name first
        if away_sc > home_sc:
            score_str = f"{away_short} {away_sc}-{home_sc}"
        else:
            score_str = f"{home_short} {home_sc}-{away_sc}"

        status = entry.get("ml_status", "pending")
        if status == "correct":
            result_emoji = "✅"
            result_word = "WON"
        elif status == "incorrect":
            result_emoji = "❌"
            result_word = "LOST"
        else:
            result_emoji = "➖"
            result_word = "PUSH"

        pick_type_label = "F5 ML" if pick.get("pick_type") == "f5_ml" else "ML"
        lines.append(
            f"{result_emoji} {away_short} @ {home_short}  →  "
            f"{pick_short} {pick_type_label}{odds_str}  {int(win_prob)}% · {conf}/10  "
            f"{score_str}  **{result_word}**"
        )
        sent_any = True

    if not sent_any:
        lines.append("📋 PASS — no picks met threshold today")

    total_picks = wins + losses
    if total_picks > 0:
        lines.append(f"📊 **{wins}W-{losses}L · ROI {roi_str}**")
    lines.append("")

    # ── Section 2: ML Board ──────────────────────────────────
    lines.append("📋 **ML BOARD**  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    sorted_entries = sorted(log_entries, key=lambda e: e.get("ml_confidence", 0), reverse=True)
    for entry in sorted_entries:
        conf = entry.get("ml_confidence", 0)
        away = entry.get("away_team", "?")
        home = entry.get("home_team", "?")
        away_short = away.split()[-1][:3].upper()
        home_short = home.split()[-1][:3].upper()
        pick_team = entry.get("ml_pick_team", "?")
        pick_short = pick_team.split()[-1]

        if conf >= 8:
            tier = "🔥"
        elif conf >= 7:
            tier = "🎯"
        elif conf >= 4:
            tier = "➡️"
        else:
            tier = "⚠️"

        away_sc = entry.get("actual_away_score", 0) or 0
        home_sc = entry.get("actual_home_score", 0) or 0
        if away_sc > home_sc:
            score_str = f"{away_short} {away_sc}-{home_sc}"
        else:
            score_str = f"{home_short} {home_sc}-{away_sc}"

        status = entry.get("ml_status", "pending")
        if status == "correct":
            result = "✅"
        elif status == "incorrect":
            result = "❌"
        else:
            result = "➖"

        # Flag if a confidence pick was sent for this game
        mlb_game_id = entry.get("mlb_game_id")
        local_id = mlb_to_local.get(mlb_game_id)
        game_picks = sent_picks_by_game.get(local_id, [])
        sent_flag = " 🎯" if any(p["pick_type"] in ("moneyline", "f5_ml") for p in game_picks) else ""

        lines.append(
            f"{tier} {away_short} @ {home_short}  →  {pick_short} {conf}/10  "
            f"{score_str}  {result}{sent_flag}"
        )

    ml_total = ml_correct + ml_incorrect
    ml_pct = round(ml_correct / ml_total * 100, 1) if ml_total > 0 else 0
    lines.append(f"📊 **{ml_correct}W-{ml_incorrect}L · {ml_pct}%**")
    lines.append("")

    # ── Section 3: O/U Board ─────────────────────────────────
    ou_lines = []
    for entry in sorted_entries:
        ou_pick = entry.get("ou_pick")
        ou_line_val = entry.get("ou_line")
        if not ou_pick or not ou_line_val:
            continue

        away = entry.get("away_team", "?")
        home = entry.get("home_team", "?")
        away_short = away.split()[-1][:3].upper()
        home_short = home.split()[-1][:3].upper()

        direction_emoji = "🔼" if ou_pick == "over" else "🔽"
        direction_label = "O" if ou_pick == "over" else "U"

        away_sc = entry.get("actual_away_score", 0) or 0
        home_sc = entry.get("actual_home_score", 0) or 0
        total_sc = away_sc + home_sc

        ou_status = entry.get("ou_status", "pending")
        if ou_status == "correct":
            marker = "✅"
            result_char = "✓"
        elif ou_status == "incorrect":
            marker = "❌"
            result_char = "✗"
        else:
            marker = "➖"
            result_char = "push"

        # Flag if a sent pick matches this O/U
        mlb_game_id = entry.get("mlb_game_id")
        local_id = mlb_to_local.get(mlb_game_id)
        game_picks = sent_picks_by_game.get(local_id, [])
        sent_flag = " 🎯" if any(p["pick_type"] in ("over", "under") for p in game_picks) else ""

        ou_lines.append(
            f"{marker} {away_short} @ {home_short}  →  "
            f"{direction_emoji} {direction_label}{ou_line_val}  Total: {total_sc}  {result_char}{sent_flag}"
        )

    if ou_lines:
        lines.append("🎰 **O/U BOARD**  ━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.extend(ou_lines)
        ou_total = ou_correct + ou_incorrect
        ou_pct = round(ou_correct / ou_total * 100, 1) if ou_total > 0 else 0
        lines.append(f"📊 **{ou_correct}W-{ou_incorrect}L · {ou_pct}%**")

    return "\n".join(lines)


# ══════════════════════════════════════════════
#  WEBHOOK PAYLOAD EXPORT (for debugging)
# ══════════════════════════════════════════════

def _format_daily_board(analyses: list) -> str:
    """Format the full ML model board for all games today."""
    today = date.today().strftime("%B %d, %Y")
    pt_now = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%-I:%M %p PT")

    lines = [
        f"⚾ **MLB ML PICKS — {today}**",
        f"📊 {len(analyses)} games · sorted by confidence",
        f"",
    ]

    # Sort by confidence descending
    sorted_analyses = sorted(analyses, key=lambda a: a.get("ml_confidence", 0), reverse=True)

    for a in sorted_analyses:
        conf = a.get("ml_confidence", 0)
        pick_team = a.get("ml_pick_team", "?")
        away = a.get("away_team", "?")
        home = a.get("home_team", "?")

        away_short = away.split()[-1] if away else "?"
        home_short = home.split()[-1] if home else "?"
        pick_short = pick_team.split()[-1] if pick_team else "?"

        # Emoji tier
        if conf >= 8:
            tier = "🔥"
        elif conf >= 7:
            tier = "✅"
        elif conf >= 6:
            tier = "➡️"
        else:
            tier = "⚠️"

        lines.append(f"{tier} {away_short} @ {home_short} · 👉 {pick_short} · {conf}/10")

    lines.append("")
    lines.append("🔥 Strong (8-10) · ✅ Qualified (7) · ➡️ Below threshold · ⚠️ Lean only")
    lines.append(f"🕐 *Updated {pt_now} · refreshes every 3 hrs*")
    return "\n".join(lines)


def send_daily_board(analyses: list, existing_message_id: Optional[str] = None) -> Optional[str]:
    """
    Send or update the daily ML model board on Discord.
    If existing_message_id is provided, patches the existing message in-place.
    Returns the Discord message ID on success, None on failure.
    """
    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD] No webhook URL — printing board locally.")
        print(_format_daily_board(analyses))
        return None

    content = _format_daily_board(analyses)
    payload = {"content": content}

    try:
        if existing_message_id:
            # PATCH existing message via webhook messages endpoint
            patch_url = f"{DISCORD_WEBHOOK_URL}/messages/{existing_message_id}"
            resp = requests.patch(patch_url, json=payload, timeout=10)
            if resp.status_code == 200:
                print(f"[DISCORD] Board updated (message {existing_message_id})")
                return existing_message_id
            else:
                print(f"[DISCORD] Board patch failed ({resp.status_code}): {resp.text}")
                # Fall through to send a new message
        # Send new message with ?wait=true to get message ID back
        resp = requests.post(
            f"{DISCORD_WEBHOOK_URL}?wait=true",
            json=payload,
            timeout=10
        )
        if resp.status_code == 200:
            message_id = resp.json().get("id")
            print(f"[DISCORD] Board sent (message {message_id})")
            return message_id
        else:
            print(f"[DISCORD] Board send failed ({resp.status_code}): {resp.text}")
            return None
    except Exception as e:
        print(f"[DISCORD] Error sending board: {e}")
        return None


def _format_ou_board(analyses: list) -> str:
    """Format the full O/U model board for all games today."""
    today = date.today().strftime("%B %d, %Y")
    pt_now = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%-I:%M %p PT")

    lines = [
        f"🎯 **MLB O/U PICKS — {today}**",
        f"📊 {len(analyses)} games · sorted by O/U confidence",
        f"",
    ]

    # Sort by O/U confidence descending
    sorted_analyses = sorted(
        analyses,
        key=lambda a: (a.get("ou_pick") or {}).get("confidence", 0),
        reverse=True
    )

    for a in sorted_analyses:
        ou = a.get("ou_pick") or {}
        conf = ou.get("confidence", 0)
        pick = ou.get("pick")        # "over" / "under" / None
        line = ou.get("total_line")
        away = a.get("away_team", "?")
        home = a.get("home_team", "?")

        away_short = away.split()[-1] if away else "?"
        home_short = home.split()[-1] if home else "?"

        if conf >= 9:
            tier = "🔥"
        elif conf >= 7:
            tier = "✅"
        elif conf >= 6:
            tier = "➡️"
        else:
            tier = "⚠️"

        if pick and line:
            pick_str = f"👉 {'OVER' if pick == 'over' else 'UNDER'} {line}"
        elif pick:
            pick_str = f"👉 {'OVER' if pick == 'over' else 'UNDER'}"
        else:
            pick_str = "👉 No pick"

        conf_str = f"{conf}/10" if conf else "—"
        lines.append(f"{tier} {away_short} @ {home_short} · {pick_str} · {conf_str}")

    lines.append("")
    lines.append("🔥 9-10 · ✅ 7-8 · ➡️ 6 · ⚠️ ≤5")
    lines.append(f"🕐 *Updated {pt_now} · refreshes every 3 hrs*")
    return "\n".join(lines)


def send_ou_board(analyses: list, existing_message_id: Optional[str] = None) -> Optional[str]:
    """Send or update the daily O/U board to Discord. Returns message ID."""
    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD] No webhook URL — printing O/U board locally.")
        print(_format_ou_board(analyses))
        return None

    content = _format_ou_board(analyses)
    payload = {"content": content}

    try:
        if existing_message_id:
            patch_url = f"{DISCORD_WEBHOOK_URL}/messages/{existing_message_id}"
            resp = requests.patch(patch_url, json=payload, timeout=10)
            if resp.status_code == 200:
                print(f"[DISCORD] O/U board updated (message {existing_message_id})")
                return existing_message_id
            else:
                print(f"[DISCORD] O/U board patch failed ({resp.status_code}): {resp.text}")
        resp = requests.post(
            f"{DISCORD_WEBHOOK_URL}?wait=true",
            json=payload,
            timeout=10
        )
        if resp.status_code == 200:
            message_id = resp.json().get("id")
            print(f"[DISCORD] O/U board sent (message {message_id})")
            return message_id
        else:
            print(f"[DISCORD] O/U board send failed ({resp.status_code}): {resp.text}")
            return None
    except Exception as e:
        print(f"[DISCORD] Error sending O/U board: {e}")
        return None


def export_payload(pick: dict) -> str:
    """Export the webhook JSON payload for a pick (useful for debugging)."""
    payload = {"content": _format_pick_message(pick)}
    return json.dumps(payload, indent=2)
