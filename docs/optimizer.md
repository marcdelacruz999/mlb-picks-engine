# MLB Picks Engine — Optimizer Reference

## Schedule & Behavior

- Runs every night at **11:30pm** via launchd (after --results at 11pm + collect_boxscores)
- Analysis + Discord report fires every night regardless
- Code/config changes throttled to once per **7 days** — measured via `_days_since_last_optimizer_commit()` (greps git log for `optimizer:` prefix)
- All optimizer git commits prefixed with `optimizer:` for throttle tracking

## Priority Order

```
1. Data quality issues (api_error_handling not in completed + log has issues + 7d cooldown)
2. Weight rebalance (≥20 graded picks + strong signal differential + 7d cooldown)
3. Threshold tune (≥30 graded picks + win rate signal + 7d cooldown)
4. Code queue (next unimplemented item + 7d cooldown → "cooldown" report if too soon)
5. Maintenance report (all queue items done)
```

Weight/threshold changes are **recurring** — no one-shot completed guard. They re-evaluate nightly as data grows.

Code queue items are **never repeated** — tracked in `COMPLETED_IMPROVEMENTS.md`.

## Code Queue (remaining)

| ID | Name |
|----|------|
| `api_error_handling` | API Error Handling & Retry Logic |
| `correlated_pick_cap` | Correlated Pick Cap (no same-game F5+ML) |
| `batter_game_logs` | Batter Game Logs & Hot/Cold Streaks |
| `pitcher_vs_team` | Pitcher vs Team Matchup History |
| `team_situational_stats` | Team Situational Stats (Home/Away/Recent) |
| `pitcher_velocity_trends` | Pitcher Velocity Trend Signal |

## Backtest Reference (2024+2025, 4,855 games)

```python
BACKTEST_REFERENCE = {
    "overall_win_rate": 0.589,
    "agent_lift": {
        "pitching":  0.080,   # strongest signal
        "bullpen":   0.050,   # was underweighted at 10%, raised to 17%
        "offense":   0.041,
        "advanced": -0.009,   # near-zero — end-of-season stats artifact
        "momentum":  None,    # untestable historically
        "weather":   None,
        "market":    None,
    },
    "calibration": { 8: 0.852, 7: 0.723 },
}
```

Blend weight: 100% backtest prior until 20 live picks → ramps to 80% live signal at 200+ picks.

## Pipeline Snapshot (`snapshot_pipeline()`)

Runs on every optimizer invocation. Returns:
- `file_summary`: key files with line counts
- `db_tables`: all tables with row counts and column lists
- `data_coverage`: null rates in key columns (opponent_team_id, score_pitching, ev_score, etc.)
- `data_gaps`: missing tables/columns detected (batter_game_logs, pitcher_vs_team_logs, avg_velocity)

## Context Injection (`build_claude_context()`)

Prepended to every Claude CLI task prompt:
- CLAUDE.md content (up to 4000 chars)
- File inventory with line counts
- DB table summary with column lists
- Completed improvements list
- Identified data gaps

## Rolling Data Analysis (`analyze_rolling_data()`)

- Checks pitcher_game_logs: total rows, yesterday's starters, blend-ready SPs (5/10/20+ starts)
- Checks team_game_logs: total rows, yesterday's teams, blend-ready teams (14d)
- Stale detection: yesterday_starters == 0 after a game day → issue flagged

## COMPLETED_IMPROVEMENTS.md

Format: `<!-- id: improvement_id -->` HTML comment marker before each entry.
Optimizer reads this file and skips any ID already present.
When implementing via Claude CLI, `mark_complete(id, name, description)` appends a new entry.
