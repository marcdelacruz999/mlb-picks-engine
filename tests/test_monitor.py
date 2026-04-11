"""
Tests for monitor.py — pitcher scratch / lineup change monitor.
"""
import unittest
from unittest.mock import patch, MagicMock


# Shared analysis log entries used across tests
ANALYSIS_LOG_MATCH = [
    {
        "mlb_game_id": 999, "game": "NYY @ TB",
        "away_pitcher": "Gerrit Cole", "home_pitcher": "Shane McClanahan",
    }
]

ANALYSIS_LOG_CHANGED = [
    {
        "mlb_game_id": 777, "game": "HOU @ TB",
        "away_pitcher": "Gerrit Cole",   # was Cole, now Brown
        "home_pitcher": "Shane McClanahan",
    }
]

ANALYSIS_LOG_TBD = [
    {
        "mlb_game_id": 888, "game": "NYM @ ATL",
        "away_pitcher": "TBD",  # was TBD — should never alert
        "home_pitcher": "Spencer Strider",
    }
]


def _make_game_conn(mlb_game_id):
    """Return a mock DB connection that resolves game_id -> mlb_game_id."""
    mock_conn = MagicMock()
    game_row = MagicMock()
    game_row.__getitem__ = lambda self, key: mlb_game_id if key == "mlb_game_id" else None
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = game_row
    mock_conn.execute.return_value = mock_cursor
    return mock_conn


class TestMonitorNoPicks(unittest.TestCase):
    """Test 1: run_monitor() with no pending picks exits gracefully."""

    @patch("monitor.get_today_picks", return_value=[])
    @patch("monitor.requests.post")
    def test_no_picks_exits_gracefully(self, mock_post, mock_get_picks):
        from monitor import run_monitor
        run_monitor()
        mock_post.assert_not_called()

    @patch("monitor.get_today_picks", return_value=[
        {"game_id": 1, "status": "won"},
        {"game_id": 2, "status": "lost"},
    ])
    @patch("monitor.requests.post")
    def test_no_pending_picks_exits_gracefully(self, mock_post, mock_get_picks):
        from monitor import run_monitor
        run_monitor()
        mock_post.assert_not_called()


class TestMonitorPitcherMatch(unittest.TestCase):
    """Test 2: same pitcher name — no Discord call made."""

    @patch("monitor.get_today_picks", return_value=[{"game_id": 10, "status": "pending"}])
    @patch("monitor.get_today_analysis_log", return_value=ANALYSIS_LOG_MATCH)
    @patch("monitor.get_current_pitchers", return_value={"away": "Gerrit Cole", "home": "Shane McClanahan"})
    @patch("monitor.pitcher_already_alerted", return_value=False)
    @patch("monitor.requests.post")
    def test_pitcher_match_no_alert(self, mock_post, mock_alerted, mock_pitchers, mock_log, mock_picks):
        from monitor import run_monitor
        mock_conn = _make_game_conn(999)
        with patch("monitor.get_connection", return_value=mock_conn):
            run_monitor()
        mock_post.assert_not_called()


class TestMonitorPitcherChanged(unittest.TestCase):
    """Test 3: different pitcher — Discord POST is called once."""

    @patch("monitor.get_today_picks", return_value=[{"game_id": 20, "status": "pending"}])
    @patch("monitor.get_today_analysis_log", return_value=ANALYSIS_LOG_CHANGED)
    @patch("monitor.get_current_pitchers", return_value={"away": "Hunter Brown", "home": "Shane McClanahan"})
    @patch("monitor.pitcher_already_alerted", return_value=False)
    @patch("monitor.save_scratch_alert")
    @patch("monitor.DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/fake")
    def test_pitcher_changed_sends_alert(self, mock_save, mock_alerted, mock_pitchers, mock_log, mock_picks):
        from monitor import run_monitor
        mock_conn = _make_game_conn(777)
        mock_response = MagicMock()
        mock_response.status_code = 204

        with patch("monitor.get_connection", return_value=mock_conn), \
             patch("monitor.requests.post", return_value=mock_response) as mock_post:
            run_monitor()

        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        self.assertIn("PITCHER SCRATCH ALERT", payload["content"])
        self.assertIn("Gerrit Cole", payload["content"])
        self.assertIn("Hunter Brown", payload["content"])


class TestMonitorTBDSkip(unittest.TestCase):
    """Test 4: TBD stored pitcher should not trigger an alert."""

    @patch("monitor.get_today_picks", return_value=[{"game_id": 30, "status": "pending"}])
    @patch("monitor.get_today_analysis_log", return_value=ANALYSIS_LOG_TBD)
    @patch("monitor.get_current_pitchers", return_value={"away": "Gerrit Cole", "home": "Spencer Strider"})
    @patch("monitor.pitcher_already_alerted", return_value=False)
    @patch("monitor.requests.post")
    def test_tbd_pitcher_no_alert(self, mock_post, mock_alerted, mock_pitchers, mock_log, mock_picks):
        from monitor import run_monitor
        mock_conn = _make_game_conn(888)
        with patch("monitor.get_connection", return_value=mock_conn):
            run_monitor()
        mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
