"""
MLB Picks Engine — Discord Webhook Module
===========================================
Sends pick alerts, updates, and daily recaps to a single Discord channel.
"""

import requests
import json
from datetime import date
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

def _format_pick_message(pick: dict) -> str:
    """Format a pick into the required Discord message structure."""

    pick_label = pick.get("pick_team", "?")
    pick_type = pick.get("pick_type", "moneyline")

    if pick_type == "moneyline":
        pick_display = f"{pick_label} ML"
    elif pick_type == "over":
        pick_display = f"OVER {pick.get('notes', '').replace('Total line: ', '')}"
    elif pick_type == "under":
        pick_display = f"UNDER {pick.get('notes', '').replace('Total line: ', '')}"
    else:
        pick_display = pick_label

    # Build odds line from analysis data
    odds_str = _format_odds_line(pick)

    msg = (
        f"🚨 **MLB HIGH-CONFIDENCE PICK**\n"
        f"\n"
        f"**Game:** {pick.get('game', '?')}\n"
        f"**Pick:** {pick_display}\n"
        f"**Confidence:** {pick.get('confidence', '?')}/10\n"
        f"**Win Probability:** {pick.get('win_probability', '?')}%\n"
        f"**Projected Score:** {pick.get('away_team', '?')} {pick.get('projected_away_score', '?')}"
        f" - {pick.get('home_team', '?')} {pick.get('projected_home_score', '?')}\n"
    )

    if odds_str:
        msg += f"\n**Current Odds:**\n{odds_str}\n"

    msg += (
        f"\n**Edge Summary:**\n"
        f"- Pitching: {pick.get('edge_pitching', 'N/A')}\n"
        f"- Offense: {pick.get('edge_offense', 'N/A')}\n"
        f"- Advanced (Statcast): {pick.get('edge_advanced', 'N/A')}\n"
        f"- Bullpen: {pick.get('edge_bullpen', 'N/A')}\n"
        f"- Weather: {pick.get('edge_weather', 'N/A')}\n"
        f"- Market: {pick.get('edge_market', 'N/A')}\n"
    )

    if pick.get("notes"):
        msg += f"\n**Notes:** {pick['notes']}\n"

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

    msg = (
        f"✅ **MLB DAILY RESULTS — {today}**\n"
        f"\n"
        f"**Wins:** {wins}\n"
        f"**Losses:** {losses}\n"
        f"**Pushes:** {pushes}\n"
        f"**Win Rate:** {round(wins / total * 100, 1) if total > 0 else 0}%\n"
        f"**ROI:** {roi}%\n"
        f"\n"
        f"**Summary:**\n"
        f"- Best pick: {results.get('best_pick', 'N/A')}\n"
        f"- Worst miss: {results.get('worst_miss', 'N/A')}\n"
        f"- Notes: {results.get('notes', 'N/A')}\n"
    )
    return msg


# ══════════════════════════════════════════════
#  WEBHOOK PAYLOAD EXPORT (for debugging)
# ══════════════════════════════════════════════

def export_payload(pick: dict) -> str:
    """Export the webhook JSON payload for a pick (useful for debugging)."""
    payload = {"content": _format_pick_message(pick)}
    return json.dumps(payload, indent=2)
