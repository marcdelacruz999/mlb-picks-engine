# MLB Picks Engine — Operational Gotchas

Env quirks, API surprises, and runtime traps that have bitten us before.

## Python / Environment

**Python 3.9** — no `float | None` union syntax. Use `Optional[float]` or `"float | None"` string annotation. `zoneinfo` is stdlib (no pip install).

**launchd jobs don't log by default** — none of the plists have `StandardOutPath`. All output goes to `engine.log` via the run_*.sh wrappers. `LastExitStatus = 0` with no `LastRunTime` means the job has never fired since being loaded (likely Mac was asleep at scheduled time).

## MLB API

**`schedule?hydrate=boxscore` omits player-level pitcher stats for historical dates** — use `/game/{gamePk}/boxscore` per game instead (verified fix 2026-04-12).

**`/schedule` linescore endpoint has no `abbreviation` field** — only returns `team_id`. Use `db.get_team_abbr_by_mlb_id(team_id)` to look up from the `teams` table.

## Engine Operations

**`--collect` runs all three collectors** — `collect_boxscores`, `collect_game_totals`, `collect_batter_boxscores`. Missing any leaves gaps in batter streaks and O/U bias tracking.

**RemoteTrigger is cloud-side** — the 2 AM CEO nightly trigger (`trig_01CAuaYtQCBpWSHfNFnS1gJw`) runs against a fresh GitHub clone, not locally. Survives session restarts. To check if it ran: `git fetch origin && git log origin/main --since="YYYY-MM-DD 02:00"`.

**Stuck game with 0-0 score after `--results`** — if a game has NULL scores and `pending` status, fetch manually:
```python
requests.get('https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD&hydrate=linescore')
```
Then patch `games` (away_score, home_score, total_runs) and `analysis_log` (actual_away_score, actual_home_score, actual_total, ml_status, ou_status) directly.
