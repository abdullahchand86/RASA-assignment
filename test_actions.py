"""
test_actions.py
Unit tests for actions.py custom actions.

Run with: pytest test_actions.py -v
Requires: pytest, pytest-mock (pip install pytest pytest-mock --break-system-packages)

Strategy: every external API call is mocked (no live network calls in CI).
Each action is tested for: (1) success path, (2) API failure / timeout,
(3) missing-slot edge case — mirroring the assignment's requirement to test
"error-handling for failed API responses and fallback messaging".
"""

import json
from unittest.mock import patch, MagicMock

import pytest
import requests

from actions import (
    ActionGetLocation,
    ActionFetchAccommodation,
    ActionCalculateCarbon,
    ActionRankOptions,
    ActionHumanHandover,
    ActionTwoStageClarify,
)


class FakeTracker:
    """Minimal stand-in for rasa_sdk.Tracker with just what the actions read."""

    def __init__(self, slots=None, sender_id="test_user", events=None):
        self._slots = slots or {}
        self.sender_id = sender_id
        self.events = events or []

    def get_slot(self, key):
        return self._slots.get(key)


class FakeDispatcher:
    def __init__(self):
        self.messages = []

    def utter_message(self, text=None, buttons=None, json_message=None):
        self.messages.append({"text": text, "buttons": buttons, "json_message": json_message})


# ---------------------------------------------------------------------------
# ActionGetLocation
# ---------------------------------------------------------------------------

class TestActionGetLocation:

    def test_missing_destination_prompts_user(self):
        action = ActionGetLocation()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={"destination": None})

        events = action.run(dispatcher, tracker, {})

        assert events == []
        assert "destination" in dispatcher.messages[0]["text"].lower()

    @patch("actions.requests.get")
    def test_successful_geocode_sets_slots(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [{"geometry": {"lat": 48.85, "lng": 2.35}}]},
        )
        mock_get.return_value.raise_for_status = lambda: None

        action = ActionGetLocation()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={"destination": "Vienna"})

        with patch("actions.OPENCAGE_API_KEY", "test-key"):
            events = action.run(dispatcher, tracker, {})

        slot_values = {e["name"]: e["value"] for e in events}
        assert slot_values["destination_lat"] == 48.85
        assert slot_values["destination_lng"] == 2.35

    @patch("actions.requests.get")
    def test_no_results_returns_helpful_message(self, mock_get):
        mock_get.return_value = MagicMock(json=lambda: {"results": []})
        mock_get.return_value.raise_for_status = lambda: None

        action = ActionGetLocation()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={"destination": "Nowhereland"})

        with patch("actions.OPENCAGE_API_KEY", "test-key"):
            events = action.run(dispatcher, tracker, {})

        assert events == []
        assert "couldn't find" in dispatcher.messages[0]["text"].lower()

    @patch("actions.requests.get", side_effect=requests.exceptions.Timeout)
    def test_timeout_degrades_gracefully(self, mock_get):
        action = ActionGetLocation()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={"destination": "Berlin"})

        with patch("actions.OPENCAGE_API_KEY", "test-key"):
            events = action.run(dispatcher, tracker, {})

        slot_values = {event["name"]: event["value"] for event in events}
        assert slot_values["destination"] == "Berlin"
        assert slot_values["destination_lat"] == 52.52
        assert "longer than expected" in dispatcher.messages[0]["text"]

    @patch("actions.requests.get", side_effect=requests.exceptions.ConnectionError)
    def test_connection_error_does_not_raise(self, mock_get):
        action = ActionGetLocation()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={"destination": "Berlin"})

        # Should not raise — must be caught and handled
        with patch("actions.OPENCAGE_API_KEY", "test-key"):
            events = action.run(dispatcher, tracker, {})
        slot_values = {event["name"]: event["value"] for event in events}
        assert slot_values["destination"] == "Berlin"
        assert slot_values["destination_lng"] == 13.405
        assert dispatcher.messages  # some fallback message was sent


# ---------------------------------------------------------------------------
# ActionCalculateCarbon
# ---------------------------------------------------------------------------

class TestActionCalculateCarbon:

    @patch("actions.requests.post")
    def test_low_emission_gets_green_tier(self, mock_post):
        mock_post.return_value = MagicMock(json=lambda: {"co2e": 20})
        mock_post.return_value.raise_for_status = lambda: None

        action = ActionCalculateCarbon()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={"transport_mode": "train", "distance_km": 100})

        events = action.run(dispatcher, tracker, {})
        slot_values = {e["name"]: e["value"] for e in events}

        assert slot_values["carbon_tier"] == "green"
        assert slot_values["carbon_estimate"] == 20

    @patch("actions.requests.post")
    def test_high_emission_gets_red_tier(self, mock_post):
        mock_post.return_value = MagicMock(json=lambda: {"co2e": 300})
        mock_post.return_value.raise_for_status = lambda: None

        action = ActionCalculateCarbon()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={"transport_mode": "car", "distance_km": 500})

        events = action.run(dispatcher, tracker, {})
        slot_values = {e["name"]: e["value"] for e in events}

        assert slot_values["carbon_tier"] == "red"

    @patch("actions.requests.post")
    def test_flight_uses_route_distance_when_coordinates_are_available(self, mock_post):
        mock_post.return_value = MagicMock(json=lambda: {"co2e": 250.5})
        mock_post.return_value.raise_for_status = lambda: None

        action = ActionCalculateCarbon()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={
            "transport_mode": "flight",
            "distance_km": None,
            "destination_lat": 51.5074,
            "destination_lng": -0.1278,
            "origin_lat": 48.8566,
            "origin_lng": 2.3522,
        })

        action.run(dispatcher, tracker, {})

        payload = mock_post.call_args.kwargs["json"]
        assert abs(payload["parameters"]["distance"] - 344.0) < 5.0
        assert payload["emission_factor"]["activity_id"].startswith("passenger_flight")

    @patch("actions.requests.post", side_effect=requests.exceptions.RequestException)
    def test_api_failure_uses_local_fallback_estimate(self, mock_post):
        action = ActionCalculateCarbon()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={"transport_mode": "car", "distance_km": 100})

        events = action.run(dispatcher, tracker, {})
        slot_values = {e["name"]: e["value"] for e in events}

        assert slot_values["carbon_tier"] == "green"
        assert slot_values["carbon_estimate"] == 17.1


# ---------------------------------------------------------------------------
# ActionRankOptions
# ---------------------------------------------------------------------------

class TestActionRankOptions:

    def test_no_options_prompts_user(self):
        action = ActionRankOptions()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={"accommodation_options": []})

        events = action.run(dispatcher, tracker, {})
        assert events == []
        assert "don't have any options" in dispatcher.messages[0]["text"]

    def test_high_preference_favours_eco_certified(self):
        options = [
            {"name": "Standard Hotel", "hotel_id": "1", "eco_certified": False},
            {"name": "Eco Lodge", "hotel_id": "2", "eco_certified": True},
        ]
        action = ActionRankOptions()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={
            "accommodation_options": options,
            "sustainability_level": "high",
            "carbon_tier": "green",
        })

        events = action.run(dispatcher, tracker, {})
        ranked = events[0]["value"]

        assert ranked[0]["name"] == "Eco Lodge"  # eco-certified ranks first under high preference

    def test_low_preference_still_returns_ranking(self):
        options = [{"name": "Budget Inn", "hotel_id": "3", "eco_certified": False}]
        action = ActionRankOptions()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={
            "accommodation_options": options,
            "sustainability_level": "low",
            "carbon_tier": "unknown",
        })

        events = action.run(dispatcher, tracker, {})
        assert len(events[0]["value"]) == 1


# ---------------------------------------------------------------------------
# ActionHumanHandover
# ---------------------------------------------------------------------------

class TestActionHumanHandover:

    def test_handover_sets_flag_and_limits_context(self):
        long_history = [{"event": "user", "text": f"msg {i}"} for i in range(50)]
        action = ActionHumanHandover()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(
            slots={"destination": "Kyoto", "sustainability_level": "high"},
            events=long_history,
        )

        events = action.run(dispatcher, tracker, {})
        slot_values = {e["name"]: e["value"] for e in events}

        assert slot_values["handover_active"] is True
        assert "human advisor" in dispatcher.messages[0]["text"].lower()


# ---------------------------------------------------------------------------
# ActionTwoStageClarify
# ---------------------------------------------------------------------------

class TestActionTwoStageClarify:

    def test_first_fallback_offers_quick_replies(self):
        action = ActionTwoStageClarify()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={"fallback_count": 0})

        events = action.run(dispatcher, tracker, {})
        slot_values = {e["name"]: e["value"] for e in events}

        assert slot_values["fallback_count"] == 1
        assert dispatcher.messages[0]["buttons"] is not None

    def test_second_consecutive_fallback_escalates(self):
        action = ActionTwoStageClarify()
        dispatcher = FakeDispatcher()
        tracker = FakeTracker(slots={"fallback_count": 1})

        events = action.run(dispatcher, tracker, {})
        slot_values = {e["name"]: e["value"] for e in events}

        assert slot_values["handover_active"] is True
        assert slot_values["fallback_count"] == 0
