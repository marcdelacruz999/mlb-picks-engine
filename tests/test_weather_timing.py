"""Tests for fetch_venue_weather game-time hour selection."""
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_mlb import fetch_venue_weather


def _make_mock_response(times, temps, tz="America/New_York"):
    """Build a mock requests.get response with the given hourly time/temp arrays."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "timezone": tz,
        "hourly": {
            "time": times,
            "temperature_2m": temps,
            "precipitation_probability": [0] * len(times),
            "windspeed_10m": [0.0] * len(times),
            "winddirection_10m": [0] * len(times),
            "weathercode": [0] * len(times),
        },
    }
    return mock_resp


def _make_hourly_day(date_str="2026-04-11"):
    """Return 24-element time list for a full day."""
    return [f"{date_str}T{h:02d}:00" for h in range(24)]


class TestWeatherTiming(unittest.TestCase):

    @patch("data_mlb.fetch_venue_coords", return_value=(40.829659, -74.001838))
    @patch("requests.get")
    def test_game_time_utc_selects_correct_hour(self, mock_get, mock_coords):
        """1pm ET game (17:10 UTC) should use index 13 (1pm local), not 19."""
        times = _make_hourly_day("2026-04-11")
        # Give each hour a unique temp: hour index as float (0.0, 1.0, ... 23.0)
        temps = [float(h) for h in range(24)]

        mock_get.return_value = _make_mock_response(times, temps, tz="America/New_York")

        result = fetch_venue_weather(
            venue_id=3313,
            game_date="2026-04-11",
            game_time_utc="2026-04-11T17:10:00Z",  # 1:10 PM ET → hour index 13
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.get("temp_f"), 13.0)  # index 13 = 1pm ET

    @patch("data_mlb.fetch_venue_coords", return_value=(40.829659, -74.001838))
    @patch("requests.get")
    def test_fallback_when_no_game_time(self, mock_get, mock_coords):
        """When game_time_utc is empty, function should not crash and returns a weather dict."""
        times = _make_hourly_day("2026-04-11")
        temps = [float(h) for h in range(24)]

        mock_get.return_value = _make_mock_response(times, temps)

        result = fetch_venue_weather(
            venue_id=3313,
            game_date="2026-04-11",
            game_time_utc="",
        )

        self.assertIsInstance(result, dict)
        self.assertIn("temp_f", result)
        # Default idx=19 (7pm), so temp_f should be 19.0
        self.assertEqual(result.get("temp_f"), 19.0)

    @patch("data_mlb.fetch_venue_coords", return_value=(40.829659, -74.001838))
    @patch("requests.get")
    def test_forecast_for_in_response(self, mock_get, mock_coords):
        """Returned dict should include a forecast_for key."""
        times = _make_hourly_day("2026-04-11")
        temps = [float(h) for h in range(24)]

        mock_get.return_value = _make_mock_response(times, temps)

        result = fetch_venue_weather(
            venue_id=3313,
            game_date="2026-04-11",
            game_time_utc="2026-04-11T17:10:00Z",
        )

        self.assertIn("forecast_for", result)
        self.assertIsInstance(result["forecast_for"], str)
        self.assertGreater(len(result["forecast_for"]), 0)


if __name__ == "__main__":
    unittest.main()
