"""
MLB Picks Engine — Configuration
=================================
Edit this file to set your API keys and Discord webhook URL.
"""

# ──────────────────────────────────────────────
# Discord Webhook
# ──────────────────────────────────────────────
# Paste your Discord webhook URL here.
# To create one:  Server Settings → Integrations → Webhooks → New Webhook
#                  → Copy Webhook URL
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1492294389916237856/MruJ5nn4VXbzsYfwnMJc3oW05gBLofVpJS52nNG5Vme20kXdbXeOce0rAOHSYnVMnIqa"

# ──────────────────────────────────────────────
# The Odds API  (free tier: 500 requests/month)
# Sign up at https://the-odds-api.com  → get your API key
# ──────────────────────────────────────────────
ODDS_API_KEY = "e0208705c616dc42ebe016fec95c6638"

# ──────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────
import os

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
# Use /tmp for the database to avoid filesystem issues in sandboxed environments
# Change this to PROJECT_DIR for persistent storage on your own machine
_DB_DIR = os.environ.get("MLB_DB_DIR", PROJECT_DIR)
DATABASE_PATH = os.path.join(_DB_DIR, "mlb_picks.db")

# ──────────────────────────────────────────────
# Pick Rules
# ──────────────────────────────────────────────
MAX_PICKS_PER_DAY = 20
MIN_CONFIDENCE = 7          # 1-10 scale; only picks ≥ this are approved
MIN_CONFIDENCE_OU = 9       # O/U requires higher bar — gap formula only, needs ≥2.0 run gap to qualify
MIN_EDGE_SCORE = 0.12       # minimum weighted edge to approve a pick
MIN_EV = -0.02              # allow slightly negative EV for high-confidence plays
F5_PITCHING_THRESHOLD = 0.20   # min |pitching_score| for F5 picks
F5_BULLPEN_THRESHOLD = -0.10   # max opponent_bullpen_score for F5 picks (must be <= this)
OU_CONVICTION_GAP = 1.5     # projected total must differ from line by ≥ this many runs to send O/U

# Batter data pipeline thresholds
MIN_BATTER_GAMES = 8            # min games in batter_game_logs to use rolling OPS
OU_K_RATE_THRESHOLD_HIGH = 0.260  # combined K/PA >= this nudges under confidence +0.5
OU_K_RATE_THRESHOLD_LOW  = 0.190  # combined K/PA <= this nudges over confidence +0.5

# ──────────────────────────────────────────────
# Decision Model Weights  (must sum to 1.0)
# ──────────────────────────────────────────────
WEIGHTS = {
    "pitching":    0.23,   # was 0.25 — trimmed: rust-risk layoff SP + weak pen pattern caused both Apr 13-14 ML losses
    "offense":     0.24,   # was 0.20 — bumped: 3W-0L when confirmed home lineup edge, strongest live signal
    "bullpen":     0.18,   # was 0.17 — bumped: ERA quality matters more than stronger/weaker label alone
    "advanced":    0.12,   # unchanged — need more data before moving
    "momentum":    0.07,   # was 0.10 — trimmed: hard to measure, reallocated to offense+bullpen
    "weather":     0.05,   # unchanged — not testable historically (neutral placeholder used)
    "market":      0.11,   # unchanged — held constant (no historical odds data)
}

# ──────────────────────────────────────────────
# Data refresh settings
# ──────────────────────────────────────────────
REFRESH_INTERVAL_HOURS = 2   # how often the scheduled task runs
SEASON_YEAR = 2026

# ──────────────────────────────────────────────
# Park Factors (multi-year averages, 2023-2025)
# 1.00 = neutral; >1.00 = hitter-friendly; <1.00 = pitcher-friendly
# Source: FanGraphs Park Factors (R/PA)
# ──────────────────────────────────────────────
PARK_FACTORS = {
    "COL": 1.28,   # Coors Field — extreme altitude/air
    "CIN": 1.13,   # Great American Ball Park
    "PHI": 1.10,   # Citizens Bank Park
    "NYY": 1.08,   # Yankee Stadium
    "BOS": 1.07,   # Fenway Park
    "CHC": 1.06,   # Wrigley Field
    "TOR": 1.05,   # Rogers Centre
    "ARI": 1.05,   # Chase Field
    "HOU": 1.04,   # Minute Maid Park
    "CWS": 1.03,   # Guaranteed Rate Field
    "MIL": 1.02,   # American Family Field
    "WSH": 1.01,   # Nationals Park
    "ATL": 1.00,   # Truist Park
    "BAL": 1.00,   # Camden Yards
    "STL": 0.98,   # Busch Stadium
    "CLE": 0.97,   # Progressive Field
    "MIN": 0.96,   # Target Field
    "KC":  0.96,   # Kauffman Stadium
    "LAD": 0.95,   # Dodger Stadium
    "LAA": 0.94,   # Angel Stadium
    "TEX": 0.94,   # Globe Life Field
    "DET": 0.93,   # Comerica Park
    "NYM": 0.93,   # Citi Field
    "PIT": 0.93,   # PNC Park
    "TB":  0.92,   # Tropicana Field
    "SEA": 0.92,   # T-Mobile Park
    "MIA": 0.91,   # loanDepot park
    "OAK": 0.91,   # Oakland Coliseum
    "SD":  0.91,   # Petco Park
    "SF":  0.89,   # Oracle Park
}

# ──────────────────────────────────────────────
# Umpire Tendencies (HP umpire run/K impact)
# run_factor: + = more runs (hitter-friendly zone)
# k_factor:   + = more Ks (pitcher-friendly zone)
# Source: UmpScorecards multi-year averages
# Unknown umps default to 0.0 (neutral)
# ──────────────────────────────────────────────
UMPIRE_TENDENCIES = {
    "Laz Diaz":         {"run_factor": -0.08, "k_factor":  0.06},  # expansive zone
    "CB Bucknor":       {"run_factor":  0.06, "k_factor": -0.05},  # inconsistent, allows walks
    "Angel Hernandez":  {"run_factor":  0.04, "k_factor": -0.04},  # erratic, batter-leaning
    "Dan Iassogna":     {"run_factor": -0.06, "k_factor":  0.05},  # pitcher-friendly
    "Fieldin Culbreth": {"run_factor": -0.05, "k_factor":  0.04},  # pitcher-friendly
    "John Tumpane":     {"run_factor":  0.05, "k_factor": -0.04},  # hitter-friendly
    "Adrian Johnson":   {"run_factor":  0.05, "k_factor": -0.04},  # hitter-friendly
    "Mike Winters":     {"run_factor":  0.05, "k_factor": -0.03},  # hitter-friendly
    "Todd Tichenor":    {"run_factor":  0.04, "k_factor": -0.03},  # hitter-friendly
    "Nic Lentz":        {"run_factor": -0.04, "k_factor":  0.03},  # pitcher-friendly
    "Quinn Wolcott":    {"run_factor": -0.04, "k_factor":  0.04},  # pitcher-friendly
    "Carlos Torres":    {"run_factor":  0.04, "k_factor": -0.03},  # hitter-friendly
    "Ron Kulpa":        {"run_factor":  0.03, "k_factor": -0.03},  # tight zone, more contact
    "Jerry Layne":      {"run_factor": -0.03, "k_factor":  0.03},  # pitcher-friendly
    # Additional MLB Umpires (29 new entries)
    "Gabe Morales":     {"run_factor": -0.05, "k_factor":  0.04},  # pitcher-friendly zone
    "Phil Cuzzi":       {"run_factor":  0.03, "k_factor": -0.02},  # balanced, slight hitter lean
    "Marvin Hudson":    {"run_factor": -0.04, "k_factor":  0.03},  # consistent pitcher-friendly
    "Greg Gibson":      {"run_factor":  0.02, "k_factor": -0.02},  # neutral with minor variations
    "Vic Carapazza":    {"run_factor": -0.03, "k_factor":  0.02},  # pitcher-friendly
    "Bill Miller":      {"run_factor":  0.04, "k_factor": -0.03},  # hitter-friendly zone
    "Jim Reynolds":     {"run_factor":  0.02, "k_factor": -0.02},  # balanced, slight hitter lean
    "Alfonso Marquez":  {"run_factor": -0.04, "k_factor":  0.03},  # pitcher-friendly
    "Sean Barber":      {"run_factor":  0.03, "k_factor": -0.02},  # slight hitter lean
    "Brian O'Nora":     {"run_factor": -0.02, "k_factor":  0.02},  # balanced, slight pitcher lean
    "Doug Eddings":     {"run_factor":  0.02, "k_factor": -0.02},  # balanced
    "Tripp Gibson":     {"run_factor":  0.04, "k_factor": -0.03},  # hitter-friendly
    "Jeremie Rehak":    {"run_factor":  0.01, "k_factor": -0.01},  # neutral
    "Ben May":          {"run_factor": -0.02, "k_factor":  0.02},  # balanced, slight pitcher lean
    "Stu Scheurwater":  {"run_factor":  0.03, "k_factor": -0.02},  # slight hitter lean
    "Ryan Additon":     {"run_factor":  0.00, "k_factor":  0.00},  # neutral
    "Nick Mahrley":     {"run_factor":  0.02, "k_factor": -0.02},  # balanced, slight hitter lean
    "Junior Valentine": {"run_factor": -0.03, "k_factor":  0.02},  # pitcher-friendly
    "Nestor Ceja":      {"run_factor":  0.01, "k_factor": -0.01},  # neutral
    "Brennan Miller":   {"run_factor":  0.00, "k_factor":  0.00},  # neutral
    "Chris Segal":      {"run_factor":  0.02, "k_factor": -0.02},  # balanced, slight hitter lean
    "Brian Knight":     {"run_factor": -0.02, "k_factor":  0.02},  # balanced, slight pitcher lean
    "Hunter Wendelstedt": {"run_factor":  0.03, "k_factor": -0.02},  # slight hitter lean
    "Tom Hallion":      {"run_factor": -0.04, "k_factor":  0.03},  # pitcher-friendly
    "Larry Vanover":    {"run_factor":  0.02, "k_factor": -0.02},  # balanced, slight hitter lean
    "Sam Holbrook":     {"run_factor": -0.03, "k_factor":  0.02},  # pitcher-friendly
    "Ted Barrett":      {"run_factor":  0.00, "k_factor":  0.00},  # neutral
    "Mark Carlson":     {"run_factor":  0.02, "k_factor": -0.02},  # balanced, slight hitter lean
    "Mark Wegner":      {"run_factor": -0.02, "k_factor":  0.02},  # balanced, slight pitcher lean
}
