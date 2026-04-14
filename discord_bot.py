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


def send_pick(pick: dict) -> bool:
    """
    Send a high-confidence pick alert to Discord.
    Returns True if sent successfully.
    """
    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD] No webhook URL configured — printing pick locally.")
        print(_format_pick_message(pick))
        return False

    payload = {"content": _format_pick_message(pick)}

    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=10
        )
        if resp.status_code == 204:
            print(f"[DISCORD] Pick sent: {pick.get('game', '?')}")
            return True
        else:
            print(f"[DISCORD] Failed ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[DISCORD] Error sending pick: {e}")
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

    win_rate = round(wins / total * 100, 1) if total > 0 else 0
    roi_str = f"+{roi}%" if roi > 0 else f"{roi}%"

    msg = f"📊 **MLB DAILY RESULTS — {today}**\n\n"

    if pick_lines:
        msg += "\n".join(pick_lines) + "\n\n"

    msg += (
        f"**Record:** {wins}W - {losses}L"
        + (f" - {pushes}P" if pushes else "")
        + f"  |  **Win Rate:** {win_rate}%  |  **ROI:** {roi_str}\n"
    )
    return msg


# ══════════════════════════════════════════════
#  WEBHOOK PAYLOAD EXPORT (for debugging)
# ══════════════════════════════════════════════

def _format_daily_board(analyses: list) -> str:
    """Format the full ML model board for all games today."""
    today = date.today().strftime("%B %d, %Y")
    pt_now = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%-I:%M %p PT")

    lines = [
        f"⚾ **MLB MONEYLINE MODEL BOARD — {today}**",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 **ALL {len(analyses)} GAMES — ML Picks & Confidence**",
        f"```",
        f"{'Game':<32} {'ML Pick':<20} {'Conf'}",
        f"{'─'*32} {'─'*20} {'─'*6}",
    ]

    # Sort by confidence descending
    sorted_analyses = sorted(analyses, key=lambda a: a.get("ml_confidence", 0), reverse=True)

    for a in sorted_analyses:
        conf = a.get("ml_confidence", 0)
        pick_team = a.get("ml_pick_team", "?")
        away = a.get("away_team", "?")
        home = a.get("home_team", "?")

        # Shorten team names for table fit
        away_short = away.split()[-1] if away else "?"
        home_short = home.split()[-1] if home else "?"
        game_str = f"{away_short} @ {home_short}"

        # Shorten pick team too
        pick_short = pick_team.split()[-1] if pick_team else "?"

        # Emoji tier
        if conf >= 8:
            tier = "🔥"
        elif conf >= 7:
            tier = "✅"
        elif conf >= 6:
            tier = "➡️ "
        else:
            tier = "⚠️ "

        lines.append(f"{tier} {game_str:<30} {pick_short:<20} {conf}/10")

    lines.append("```")
    lines.append("🔥 Strong (8-10) · ✅ Qualified (7) · ➡️ Below threshold · ⚠️ Lean only")
    lines.append(f"🕐 *Updated {pt_now} — board refreshes every 3 hrs as lineups confirm*")
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


def export_payload(pick: dict) -> str:
    """Export the webhook JSON payload for a pick (useful for debugging)."""
    payload = {"content": _format_pick_message(pick)}
    return json.dumps(payload, indent=2)
