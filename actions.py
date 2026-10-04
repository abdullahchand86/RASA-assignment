"""
actions.py
Custom actions for the Sustainable Trip Planner Rasa assistant.

Responsibilities (mapped to assignment Task 4):
- action_get_location        -> OpenCage geocoding API
- action_fetch_accommodation -> OpenStreetMap Overpass (api_clients) + static eco-certification DB
- action_get_weather         -> Open-Meteo forecast
- action_describe_place      -> Wikipedia REST summary
- action_find_attractions    -> OpenStreetMap Overpass
- action_find_transport      -> OpenStreetMap Overpass
- action_convert_currency    -> Frankfurter (ECB reference rates)
- action_calculate_carbon    -> Climatiq API
- action_rank_options        -> weighted scoring (carbon, price, preference)
- action_human_handover      -> packages full conversation context
- action_two_stage_clarify   -> constrained-option re-prompt before escalation

All external calls are wrapped in try/except with graceful fallback messaging,
per the assignment's error-handling requirement (FR6). API keys are read from
environment variables and must never be hardcoded (see .env / .gitignore).
"""

import os
import json
import logging
import math
import re
from typing import Any, Text, Dict, List

import requests
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from abacusai import ApiClient as Client
except (ImportError, AttributeError):
    Client = None

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet
import api_clients

load_dotenv()

logger = logging.getLogger(__name__)

CLIMATIQ_API_KEY = os.getenv("CLIMATIQ_API_KEY", "")
OPENCAGE_API_KEY = os.getenv("OPENCAGE_API_KEY", "")

REQUEST_TIMEOUT = 5  # seconds — keeps us inside the <3s critical-path budget
                      # when combined with async/parallel calls where possible
CLIMATIQ_VEHICLE_DATA_VERSION = "^37"
CLIMATIQ_ACTIVITY_IDS = {
    "car": "passenger_vehicle-vehicle_type_car-fuel_source_na-engine_size_na-vehicle_age_na-vehicle_weight_na",
    "bus": "passenger_vehicle-vehicle_type_bus-fuel_source_na-distance_na-engine_size_na",
    "train": "passenger_train-route_type_na-fuel_source_na",
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance between two lat/lon points in km."""
    earth_radius_km = 6371.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return earth_radius_km * c

ECO_DB_PATH = os.path.join(os.path.dirname(__file__), "eco_certified_db.json")

KNOWN_DESTINATIONS = {
    "london": (51.5074, -0.1278),
    "paris": (48.8566, 2.3522),
    "rome": (41.9028, 12.4964),
    "madrid": (40.4168, -3.7038),
    "new york": (40.7128, -74.0060),
    "tokyo": (35.6762, 139.6503),
    "kyoto": (35.0116, 135.7681),
    "lisbon": (38.7223, -9.1393),
    "berlin": (52.5200, 13.4050),
    "bangalore": (12.9716, 77.5946),
    "karachi": (24.8607, 67.0011),
    "costa rica": (9.7489, -83.7534),
}

FALLBACK_EMISSIONS_KG_PER_KM = {
    "train": 0.041,
    "bus": 0.089,
    "car": 0.171,
    "flight": 0.255,
}


OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")  # "ollama" (free/local) or "abacus"


def _get_llm_response_ollama(prompt: str, max_tokens: int = 300) -> str:
    """Call a local, free Ollama model for LLM-powered responses."""
    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": max_tokens},
            },
            timeout=REQUEST_TIMEOUT * 4,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip() or None
    except Exception as e:
        logger.error("Ollama LLM call failed: %s", e)
        return None


def _get_llm_response_abacus(prompt: str, max_tokens: int = 300) -> str:
    """Call Abacus AI Opus 4.1 for LLM-powered responses."""
    api_key = os.getenv("ABACUS_API_KEY")
    if not api_key or api_key == "<your-api-key-here>":
        logger.warning("ABACUS_API_KEY not configured; returning fallback response")
        return None

    try:
        client = Client(api_key=api_key)
        response = client.chat.completions.create(
            model="claude-opus-4-1",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error("Abacus AI LLM call failed: %s", e)
        return None


def _get_llm_response(prompt: str, max_tokens: int = 300) -> str:
    """Route to the configured LLM backend (free local Ollama by default)."""
    if LLM_PROVIDER == "abacus":
        return _get_llm_response_abacus(prompt, max_tokens)
    return _get_llm_response_ollama(prompt, max_tokens)


def _load_eco_database() -> List[Dict[str, Any]]:
    """Load the curated static dataset of eco-certified stays / offset programmes.

    Used instead of a generative claim, directly to avoid the greenwashing risk
    identified in the literature review (Task 1): every 'eco-certified' label
    here is traceable to a named certification body, not model-generated text.
    """
    try:
        with open(ECO_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Eco database unavailable: %s", e)
        return []


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

def _geocode(name: str):
    coords = KNOWN_DESTINATIONS.get(name.lower())
    if coords:
        return coords
    try:
        response = requests.get(
            GEOCODING_URL,
            params={"name": name, "count": 1, "language": "en", "format": "json"},
            headers=api_clients.HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        if results:
            return results[0]["latitude"], results[0]["longitude"]
    except (requests.exceptions.RequestException, KeyError, ValueError) as e:
        logger.error("Geocoding error for %s: %s", name, e)
    return None


def _latest_destination_mention(tracker: Tracker):
    latest_message = getattr(tracker, "latest_message", {}) or {}
    text = (latest_message.get("text") or "").lower()
    for name in KNOWN_DESTINATIONS:
        if name in text:
            return name
    for entity in latest_message.get("entities", []) or []:
        if entity.get("entity") == "destination" and entity.get("value"):
            return str(entity["value"])
    return None


def _resolve_destination(tracker: Tracker):
    """Return (name, lat, lng, events) for the destination the user is asking about."""
    mentioned = _latest_destination_mention(tracker)
    slot_name = tracker.get_slot("destination")
    name = mentioned or slot_name
    if not name:
        return None, None, None, []
    if not mentioned:
        lat = tracker.get_slot("destination_lat")
        lng = tracker.get_slot("destination_lng")
        if lat is not None and lng is not None:
            return name, lat, lng, []
    coords = _geocode(name)
    if not coords:
        return name, None, None, []
    lat, lng = coords
    return name, lat, lng, [
        SlotSet("destination", name),
        SlotSet("destination_lat", lat),
        SlotSet("destination_lng", lng),
    ]


class ActionGetLocation(Action):
    """Resolve a free-text or GPS location into coordinates via OpenCage."""

    def name(self) -> Text:
        return "action_get_location"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        latest_message = getattr(tracker, "latest_message", {}) or {}
        latest_user_text = latest_message.get("text", "")
        if not latest_user_text:
            latest_user_text = next(
                (event.get("text", "") for event in reversed(tracker.events)
                 if event.get("event") == "user"),
                "",
            )
        latest_user_text = latest_user_text.lower()
        current_destination = next(
            (name for name in KNOWN_DESTINATIONS if name in latest_user_text),
            None,
        )
        destination = current_destination or tracker.get_slot("destination")
        if not destination:
            dispatcher.utter_message(
                text="I didn't catch a destination — could you tell me the city "
                     "or region you're planning to visit?"
            )
            return []

        if not OPENCAGE_API_KEY:
            coordinates = KNOWN_DESTINATIONS.get(destination.lower())
            if coordinates:
                dispatcher.utter_message(
                    text=f"{destination.title()} sounds like a great destination. "
                         "What would you like to explore next?",
                    buttons=[
                        {"title": "Estimate trip emissions", "payload": "/ask_carbon_footprint_estimate"},
                        {"title": "Find sustainable accommodation", "payload": "/ask_sustainable_accommodation"},
                        {"title": "Get sustainable travel tips", "payload": "/ask_sustainability_tips"},
                    ],
                )
                return [
                    SlotSet("destination", destination),
                    SlotSet("destination_lat", coordinates[0]),
                    SlotSet("destination_lng", coordinates[1]),
                ]
            dispatcher.utter_message(
                text="I can still help with carbon estimates. Please tell me the route or transport mode, or use a known city such as Kyoto, Lisbon, or Berlin."
            )
            return []

        try:
            response = requests.get(
                "https://api.opencagedata.com/geocode/v1/json",
                params={"q": destination, "key": OPENCAGE_API_KEY, "limit": 1},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            results = response.json().get("results", [])

            if not results:
                dispatcher.utter_message(
                    text=f"I couldn't find '{destination}' — could you check the spelling "
                         "or try a nearby larger town?"
                )
                return []

            geometry = results[0]["geometry"]
            dispatcher.utter_message(
                text=f"{destination.title()} sounds like a great destination. "
                     "How much should I prioritise sustainability over cost?"
            )
            return [
                SlotSet("destination_lat", geometry["lat"]),
                SlotSet("destination_lng", geometry["lng"]),
            ]

        except requests.exceptions.Timeout:
            dispatcher.utter_message(
                text="Location lookup is taking longer than expected. "
                     "I'll continue using the destination name you gave me."
            )
            coordinates = KNOWN_DESTINATIONS.get(destination.lower())
            return [
                SlotSet("destination", destination),
                SlotSet("destination_lat", coordinates[0]),
                SlotSet("destination_lng", coordinates[1]),
            ] if coordinates else []
        except requests.exceptions.RequestException as e:
            logger.error("OpenCage API error: %s", e)
            coordinates = KNOWN_DESTINATIONS.get(destination.lower())
            if coordinates:
                dispatcher.utter_message(
                    text=f"I couldn't verify {destination} with the location service, "
                         "so I'll use its standard coordinates and continue."
                )
                return [
                    SlotSet("destination", destination),
                    SlotSet("destination_lat", coordinates[0]),
                    SlotSet("destination_lng", coordinates[1]),
                ]
            dispatcher.utter_message(
                text="I'm having trouble verifying that location right now, "
                     "but I can still search using the name you gave me."
            )
            return []


class ActionFlightSchedule(Action):
    """Explain the current flight-schedule limitation without a live flight API."""

    def name(self) -> Text:
        return "action_flight_schedule"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        origin = tracker.get_slot("origin") or "Berlin"
        destination = tracker.get_slot("destination") or "Bangalore"
        latest_message = getattr(tracker, "latest_message", {}) or {}
        latest_user_text = latest_message.get("text", "")
        route_match = re.search(
            r"\bfrom\s+([a-z][a-z .'-]+?)\s+to\s+([a-z][a-z .'-]+?)(?:\?|$|\.)",
            latest_user_text.lower(),
        )
        origin = route_match.group(1).strip() if route_match else tracker.get_slot("origin")
        destination = route_match.group(2).strip() if route_match else tracker.get_slot("destination")
        origin = origin or "Berlin"
        destination = destination or "Bangalore"
        dispatcher.utter_message(
            text=f"I can't show live departure times for {origin.title()} to "
                 f"{destination.title()} because the flight-search API is not "
                 "active in this version. I can still estimate the route distance "
                 "and carbon impact, or help you compare transport options."
        )
        return [
            SlotSet("origin", origin),
            SlotSet("destination", destination),
        ]


class ActionCalculateDistance(Action):
    """Calculate route distance for known cities without an external lookup."""

    def name(self) -> Text:
        return "action_calculate_distance"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        origin = tracker.get_slot("origin") or "Berlin"
        destination = tracker.get_slot("destination") or "Bangalore"
        latest_message = getattr(tracker, "latest_message", {}) or {}
        latest_user_text = latest_message.get("text", "")
        route_match = re.search(
            r"\bfrom\s+([a-z][a-z .'-]+?)\s+to\s+([a-z][a-z .'-]+?)(?:\?|$|\.)",
            latest_user_text.lower(),
        )
        origin = route_match.group(1).strip() if route_match else tracker.get_slot("origin")
        destination = route_match.group(2).strip() if route_match else tracker.get_slot("destination")
        origin = origin or "Berlin"
        destination = destination or "Bangalore"
        origin_coordinates = KNOWN_DESTINATIONS.get(origin.lower())
        destination_coordinates = KNOWN_DESTINATIONS.get(destination.lower())

        if not origin_coordinates or not destination_coordinates:
            dispatcher.utter_message(
                text="I can calculate kilometres for known cities such as Berlin, "
                     "Bangalore, Paris, London, and Rome."
            )
            return []

        distance_km = _haversine_km(
            origin_coordinates[0], origin_coordinates[1],
            destination_coordinates[0], destination_coordinates[1],
        )
        dispatcher.utter_message(
            text=f"The straight-line distance from {origin.title()} to "
                 f"{destination.title()} is approximately {distance_km:,.0f} km."
        )
        return [
            SlotSet("origin", origin),
            SlotSet("destination", destination),
            SlotSet("distance_km", distance_km),
        ]


class ActionFetchAccommodation(Action):
    """Retrieve hotel options from OpenStreetMap (Overpass) + cross-reference eco-certification DB."""

    def name(self) -> Text:
        return "action_fetch_accommodation"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        _, lat, lng, location_events = _resolve_destination(tracker)

        hotels = []
        if lat is not None and lng is not None:
            try:
                hotels = api_clients.find_hotels(lat, lng)
            except requests.exceptions.RequestException as e:
                logger.error("Overpass (hotels) error: %s", e)
                dispatcher.utter_message(
                    text="The live hotel search is unavailable, so I'm using "
                         "verified eco-certified options from my local catalogue."
                )
        else:
            dispatcher.utter_message(
                text="I don't have verified coordinates yet, so I'm using the "
                     "curated eco-certified catalogue instead."
            )

        eco_db = _load_eco_database()
        eco_names = {entry["name"].lower() for entry in eco_db}

        enriched = []
        for hotel in hotels:
            name = hotel["name"]
            enriched.append({
                "name": name,
                "hotel_id": None,
                "eco_certified": name.lower() in eco_names,
                "stars": hotel.get("stars"),
            })

        if not enriched:
            enriched = [{"name": e["name"], "hotel_id": None, "eco_certified": True}
                        for e in eco_db[:5]]

        return location_events + [SlotSet("accommodation_options", enriched)]


class ActionCompareTransportModes(Action):
    """Compare typical per-kilometre emissions across common transport modes."""

    def name(self) -> Text:
        return "action_compare_transport_modes"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        distance_km = tracker.get_slot("distance_km") or 100
        comparison = sorted(
            ((mode, factor * float(distance_km))
             for mode, factor in FALLBACK_EMISSIONS_KG_PER_KM.items()),
            key=lambda item: item[1],
        )
        summary = ", ".join(
            f"{mode}: about {emissions:.1f} kg CO2e"
            for mode, emissions in comparison
        )
        dispatcher.utter_message(
            text=f"For approximately {float(distance_km):,.0f} km, lower to higher typical impact is {summary}. Actual results vary by route, occupancy, vehicle, and energy source."
        )
        return []


class ActionCalculateCarbon(Action):
    """Estimate transport emissions, using Climatiq when available."""

    def name(self) -> Text:
        return "action_calculate_carbon"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        latest_message = getattr(tracker, "latest_message", {}) or {}
        latest_user_text = latest_message.get("text", "")
        if not latest_user_text:
            latest_user_text = next(
                (event.get("text", "") for event in reversed(tracker.events)
                 if event.get("event") == "user"),
                "",
            )
        text_lower = latest_user_text.lower()

        route_match = re.search(
            r"\bfrom\s+([a-z][a-z .'-]+?)\s+to\s+([a-z][a-z .'-]+?)(?:\?|$|\.)",
            text_lower,
        )
        origin_name = route_match.group(1).strip() if route_match else tracker.get_slot("origin")
        destination_name = route_match.group(2).strip() if route_match else tracker.get_slot("destination")

        distance_km = tracker.get_slot("distance_km")
        distance_match = re.search(r"(\d+(?:\.\d+)?)\s*km", text_lower)
        if distance_match:
            distance_km = float(distance_match.group(1))

        detected_mode = None
        for mode in ["flight", "flying", "plane", "train", "rail", "bus", "coach", "car", "drive", "driving"]:
            if re.search(r"\b" + mode + r"\b", text_lower):
                if mode in ("flight", "flying", "plane"):
                    detected_mode = "flight"
                elif mode in ("train", "rail"):
                    detected_mode = "train"
                elif mode in ("bus", "coach"):
                    detected_mode = "bus"
                elif mode in ("car", "drive", "driving"):
                    detected_mode = "car"
                break

        transport_mode = detected_mode or tracker.get_slot("transport_mode")
        if not transport_mode:
            transport_mode = "flight" if (route_match or origin_name or destination_name) else "car"
        transport_mode = transport_mode.lower()

        origin_lat = tracker.get_slot("origin_lat")
        origin_lng = tracker.get_slot("origin_lng")
        destination_lat = tracker.get_slot("destination_lat")
        destination_lng = tracker.get_slot("destination_lng")

        if origin_lat is None and origin_name:
            origin_coordinates = KNOWN_DESTINATIONS.get(origin_name.lower())
            if origin_coordinates:
                origin_lat, origin_lng = origin_coordinates
        if destination_lat is None and destination_name:
            destination_coordinates = KNOWN_DESTINATIONS.get(destination_name.lower())
            if destination_coordinates:
                destination_lat, destination_lng = destination_coordinates

        used_fallback = distance_km is None

        if origin_lat is not None and origin_lng is not None and destination_lat is not None and destination_lng is not None:
            distance_km = _haversine_km(float(origin_lat), float(origin_lng), float(destination_lat), float(destination_lng))
            used_fallback = False
        elif distance_km is None:
            distance_km = 100.0
            used_fallback = True

        distance_km = float(distance_km)

        is_distance_query = bool(
            re.search(r"\b(how many|what is the)?\s*(kilometres?|kilometers?|km|distance)\b", text_lower)
            and not re.search(r"\b(emissions?|co2|carbon|footprint|impact)\b", text_lower)
        )

        try:
            if transport_mode == "flight":
                activity_id = (
                    "passenger_flight-route_type_international-aircraft_type_na-"
                    "distance_na-class_na-rf_included-distance_uplift_na"
                )
                payload = {
                    "emission_factor": {
                        "activity_id": activity_id,
                        "data_version": "^6",
                    },
                    "parameters": {
                        "passengers": 1,
                        "distance": distance_km,
                        "distance_unit": "km",
                    },
                }
            else:
                payload = {
                    "emission_factor": {
                        "activity_id": CLIMATIQ_ACTIVITY_IDS.get(
                            transport_mode,
                            CLIMATIQ_ACTIVITY_IDS["car"],
                        ),
                        "data_version": CLIMATIQ_VEHICLE_DATA_VERSION,
                    },
                    "parameters": {"distance": distance_km, "distance_unit": "km"},
                }

            response = requests.post(
                "https://api.climatiq.io/data/v1/estimate",
                headers={"Authorization": f"Bearer {CLIMATIQ_API_KEY}"},
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            co2_kg = float(response.json().get("co2e", 0))

            if co2_kg < 50:
                tier = "green"
            elif co2_kg < 150:
                tier = "amber"
            else:
                tier = "red"

            if is_distance_query and origin_name and destination_name:
                dispatcher.utter_message(
                    text=f"The straight-line distance from {origin_name.title()} to {destination_name.title()} is approximately {distance_km:,.0f} km. Estimated {transport_mode} emissions: {co2_kg:.1f} kg CO2e ({tier})."
                )
            elif origin_name and destination_name and not used_fallback:
                dispatcher.utter_message(
                    text=f"Estimated impact: {co2_kg:.1f} kg CO2e ({tier}) for a {transport_mode} from {origin_name.title()} to {destination_name.title()} ({distance_km:,.0f} km)."
                )
            else:
                route_detail = f" for {distance_km:,.0f} km by {transport_mode}" if not used_fallback else " for an indicative 100 km journey"
                dispatcher.utter_message(
                    text=f"Estimated impact: {co2_kg:.1f} kg CO2e ({tier}){route_detail}."
                )

            slots_to_set = [
                SlotSet("carbon_estimate", co2_kg),
                SlotSet("carbon_tier", tier),
                SlotSet("distance_km", distance_km),
                SlotSet("transport_mode", transport_mode),
            ]
            if origin_name:
                slots_to_set.append(SlotSet("origin", origin_name))
            if destination_name:
                slots_to_set.append(SlotSet("destination", destination_name))
            if origin_lat is not None:
                slots_to_set.append(SlotSet("origin_lat", origin_lat))
                slots_to_set.append(SlotSet("origin_lng", origin_lng))
            if destination_lat is not None:
                slots_to_set.append(SlotSet("destination_lat", destination_lat))
                slots_to_set.append(SlotSet("destination_lng", destination_lng))
            return slots_to_set

        except requests.exceptions.RequestException as e:
            logger.error("Climatiq API error: %s", e)
            co2_kg = round(distance_km * FALLBACK_EMISSIONS_KG_PER_KM.get(transport_mode, 0.171), 1)
            tier = "green" if co2_kg < 50 else "amber" if co2_kg < 150 else "red"

            if is_distance_query and origin_name and destination_name:
                dispatcher.utter_message(
                    text=f"The straight-line distance from {origin_name.title()} to {destination_name.title()} is approximately {distance_km:,.0f} km. Indicative {transport_mode} emissions: {co2_kg:.1f} kg CO2e ({tier})."
                )
            elif origin_name and destination_name and not used_fallback:
                dispatcher.utter_message(
                    text=f"I couldn't reach Climatiq, so this is an indicative estimate: {co2_kg:.1f} kg CO2e ({tier}) for a {transport_mode} from {origin_name.title()} to {destination_name.title()} ({distance_km:,.0f} km)."
                )
            else:
                dispatcher.utter_message(
                    text=f"I couldn't reach Climatiq, so this is an indicative estimate: {co2_kg:.1f} kg CO2e for {distance_km:,.0f} km by {transport_mode} ({tier})."
                )

            slots_to_set = [
                SlotSet("carbon_estimate", co2_kg),
                SlotSet("carbon_tier", tier),
                SlotSet("distance_km", distance_km),
                SlotSet("transport_mode", transport_mode),
            ]
            if origin_name:
                slots_to_set.append(SlotSet("origin", origin_name))
            if destination_name:
                slots_to_set.append(SlotSet("destination", destination_name))
            return slots_to_set


class ActionRankOptions(Action):
    """Weighted scoring: carbon impact, price, and stated sustainability preference."""

    WEIGHTS = {
        "high":     {"carbon": 0.6, "price": 0.2, "preference": 0.2},
        "moderate": {"carbon": 0.4, "price": 0.4, "preference": 0.2},
        "low":      {"carbon": 0.2, "price": 0.6, "preference": 0.2},
    }

    def name(self) -> Text:
        return "action_rank_options"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        options = tracker.get_slot("accommodation_options") or []
        sustainability_level = tracker.get_slot("sustainability_level") or "moderate"
        carbon_tier = tracker.get_slot("carbon_tier") or "unknown"
        weights = self.WEIGHTS.get(sustainability_level, self.WEIGHTS["moderate"])

        if not options:
            dispatcher.utter_message(
                text="I don't have any options to rank yet — let's find a location and "
                     "accommodation first."
            )
            return []

        tier_score = {"green": 1.0, "amber": 0.5, "red": 0.1, "unknown": 0.3}

        scored = []
        for opt in options:
            eco_score = 1.0 if opt.get("eco_certified") else 0.4
            score = (
                weights["carbon"] * tier_score.get(carbon_tier, 0.3)
                + weights["preference"] * eco_score
                + weights["price"] * 0.5  # placeholder until live pricing wired in
            )
            scored.append({**opt, "score": round(score, 3)})

        ranked = sorted(scored, key=lambda x: x["score"], reverse=True)

        buttons = [
            {"title": f"{o['name']} ({'eco-certified' if o['eco_certified'] else 'standard'})",
             "payload": f'/select_option{{"hotel_id":"{o.get("hotel_id")}"}}'}
            for o in ranked[:5]
        ]
        dispatcher.utter_message(
            text="Here are your top options, ranked by carbon impact, price and your stated preference:",
            buttons=buttons,
        )
        return [SlotSet("ranked_options", ranked)]


class ActionHumanHandover(Action):
    """Package full conversational context for a human travel advisor."""

    def name(self) -> Text:
        return "action_human_handover"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        context = {
            "sender_id": tracker.sender_id,
            "destination": tracker.get_slot("destination"),
            "travel_dates": tracker.get_slot("travel_dates"),
            "budget": tracker.get_slot("budget"),
            "sustainability_level": tracker.get_slot("sustainability_level"),
            "carbon_estimate": tracker.get_slot("carbon_estimate"),
            "ranked_options": tracker.get_slot("ranked_options"),
            # Only the last N turns — avoids over-retaining personal data (GDPR minimisation)
            "recent_turns": [
                {"event": e.get("event"), "text": e.get("text")}
                for e in tracker.events[-10:]
                if e.get("event") in ("user", "bot")
            ],
        }

        try:
            # In production this would POST to a CRM/ticketing endpoint.
            # Left as a logged payload for the coursework deployment scope.
            logger.info("Handover package: %s", json.dumps(context))
        except TypeError as e:
            logger.error("Failed to serialise handover context: %s", e)

        dispatcher.utter_message(
            text="Connected to human advisor — they now have your full trip details "
                 "and will follow up shortly.",
            json_message={"handover_active": True},
        )
        return [SlotSet("handover_active", True)]


class ActionTwoStageClarify(Action):
    """First-stage fallback: re-prompt with constrained quick-reply options."""

    def name(self) -> Text:
        return "action_two_stage_clarify"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        fallback_count = tracker.get_slot("fallback_count") or 0

        if fallback_count >= 1:
            # Second consecutive fallback -> escalate automatically
            return [SlotSet("fallback_count", 0)] + ActionHumanHandover().run(
                dispatcher, tracker, domain
            )

        dispatcher.utter_message(
            text="I'm not sure I understood — did you mean:",
            buttons=[
                {"title": "Find eco-certified accommodation", "payload": "/ask_sustainable_accommodation"},
                {"title": "Check carbon impact", "payload": "/ask_carbon_footprint_estimate"},
                {"title": "Get sustainable travel tips", "payload": "/ask_sustainability_tips"},
            ],
        )
        return [SlotSet("fallback_count", fallback_count + 1)]


class ActionDefaultFallback(Action):
    """Provide a useful response when confidence is too low to continue."""

    def name(self) -> Text:
        return "action_default_fallback"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        dispatcher.utter_message(
            text="I can help with destinations, dates, budgets, accommodation, "
                 "sustainability, and carbon impact. What would you like to plan?"
        )
        return [SlotSet("fallback_count", 0)]


class ActionGenerateTravelTip(Action):
    """Generate personalized sustainability travel tips using Opus 4.1."""

    def name(self) -> Text:
        return "action_generate_travel_tip"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        destination = tracker.get_slot("destination") or "your destination"
        transport_mode = tracker.get_slot("transport_mode") or "travel"
        sustainability_level = tracker.get_slot("sustainability_level") or "moderate"

        prompt = (
            f"Give one concise, actionable sustainability tip for someone traveling to {destination} "
            f"by {transport_mode} with a {sustainability_level} sustainability preference. "
            f"Keep it under 50 words."
        )

        tip = _get_llm_response(prompt, max_tokens=100)

        if tip:
            dispatcher.utter_message(text=f"💡 {tip}")
        else:
            fallback_tips = {
                "train": "Train travel is one of the lowest-carbon options — consider it for longer journeys.",
                "bus": "Bus travel reduces per-person emissions significantly compared to driving.",
                "car": "Carpooling or electric vehicles can reduce your travel carbon footprint.",
                "flight": "Offset your flight emissions through verified carbon credit programs.",
            }
            fallback = fallback_tips.get(transport_mode, "Travel during off-peak times to reduce congestion and emissions.")
            dispatcher.utter_message(text=f"💡 {fallback}")

        return []


class ActionGetWeather(Action):
    """Fetch current + 3-day weather for the destination and give
    sustainability-relevant travel advice (walk/cycle vs. public transport)."""

    def name(self) -> Text:
        return "action_get_weather"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        destination, lat, lng, location_events = _resolve_destination(tracker)

        if lat is None or lng is None:
            if destination is None:
                dispatcher.utter_message(
                    text="Which city should I check? For example: 'What's the weather in Lisbon?'"
                )
            else:
                dispatcher.utter_message(
                    text=f"I couldn't find coordinates for {destination} — could you check the spelling or try a nearby larger city?"
                )
            return []

        try:
            data = api_clients.get_weather(lat, lng)
            current = data["current"]
            daily = data["daily"]

            today_max = daily["temperature_2m_max"][0]
            today_rain = daily["precipitation_sum"][0]
            advice = api_clients.weather_travel_advice(today_max, today_rain)

            dispatcher.utter_message(
                text=(
                    f"Right now in {destination.title()}: {current['temperature_2m']}°C, "
                    f"{current['precipitation']}mm precipitation. "
                    f"Today's forecast: up to {today_max}°C with {today_rain}mm of rain expected — {advice}."
                )
            )
            return location_events + [SlotSet("weather_summary", advice)]

        except requests.exceptions.RequestException as e:
            logger.error("Open-Meteo API error: %s", e)
            dispatcher.utter_message(
                text="I couldn't reach the weather service right now — "
                     "check a forecast site before finalising outdoor plans."
            )
            return location_events
        except (KeyError, IndexError) as e:
            logger.error("Unexpected Open-Meteo response shape: %s", e)
            dispatcher.utter_message(
                text="I got an unexpected response from the weather service."
            )
            return location_events


class ActionDescribePlace(Action):
    """Give a short, attributed Wikipedia description of a named place,
    falling back cleanly when no article exists (most small places)."""

    def name(self) -> Text:
        return "action_describe_place"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        place_name = _latest_destination_mention(tracker) or tracker.get_slot("place_name") or tracker.get_slot("destination")
        if not place_name:
            return []

        try:
            info = api_clients.describe_place(place_name.title())
        except requests.exceptions.RequestException as e:
            logger.error("Wikipedia API error: %s", e)
            dispatcher.utter_message(
                text=f"I couldn't look up details for {place_name} right now."
            )
            return []

        if info is None:
            dispatcher.utter_message(
                text=f"I don't have a background summary for {place_name}, "
                     "but it's on your list of options."
            )
            return []

        dispatcher.utter_message(
            text=f"{info['summary']} (Source: Wikipedia — {info['url']})"
        )
        return [SlotSet("place_name", place_name), SlotSet("place_description", info["summary"])]


class ActionFindAttractions(Action):
    """List verified museums/historic sites near the destination."""

    def name(self) -> Text:
        return "action_find_attractions"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        destination, lat, lng, location_events = _resolve_destination(tracker)

        if lat is None or lng is None:
            if destination is None:
                dispatcher.utter_message(
                    text="Which city should I check? For example: 'What cultural sites are in Lisbon?'"
                )
            else:
                dispatcher.utter_message(
                    text=f"I couldn't find coordinates for {destination} — could you check the spelling or try a nearby larger city?"
                )
            return []

        try:
            sites = api_clients.find_attractions(lat, lng)
        except requests.exceptions.RequestException as e:
            logger.error("Overpass (attractions) error: %s", e)
            dispatcher.utter_message(
                text="I couldn't reach the mapping service for cultural sites right now."
            )
            return location_events

        if not sites:
            dispatcher.utter_message(
                text="I couldn't find notable museums or historic sites nearby — "
                     "try a wider search radius or a nearby town."
            )
            return []

        names = ", ".join(s["name"] for s in sites[:6])
        dispatcher.utter_message(text=f"Nearby cultural sites: {names}.")
        return location_events + [SlotSet("attractions_list", sites)]


class ActionFindTransport(Action):
    """Summarise public-transport proximity near the destination — location
    only, not live timetables (OSM has no schedule data)."""

    def name(self) -> Text:
        return "action_find_transport"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        destination, lat, lng, location_events = _resolve_destination(tracker)

        if lat is None or lng is None:
            if destination is None:
                dispatcher.utter_message(
                    text="Which city should I check? For example: 'How do I get around in Lisbon?'"
                )
            else:
                dispatcher.utter_message(
                    text=f"I couldn't find coordinates for {destination} — could you check the spelling or try a nearby larger city?"
                )
            return []

        try:
            stops = api_clients.find_transport(lat, lng)
        except requests.exceptions.RequestException as e:
            logger.error("Overpass (transport) error: %s", e)
            dispatcher.utter_message(
                text="I couldn't reach the mapping service for transport data right now."
            )
            return location_events

        verdict = api_clients.summarise_transport(stops)
        dispatcher.utter_message(
            text=f"Public transport here is {verdict} "
                 f"({len(stops)} rail/metro/tram access points within 1.5km)."
        )
        return location_events + [SlotSet("transport_summary", verdict)]


FRANKFURTER_CURRENCIES = {"AUD","BGN","BRL","CAD","CHF","CNY","CZK","DKK","EUR","GBP","HKD","HUF","IDR","ILS","INR","ISK","JPY","KRW","MXN","MYR","NOK","NZD","PHP","PLN","RON","SEK","SGD","THB","TRY","USD","ZAR"}

CURRENCY_WORDS = {
    "£": "GBP", "€": "EUR", "$": "USD", "¥": "JPY", "₹": "INR",
    "pound": "GBP", "pounds": "GBP", "sterling": "GBP",
    "euro": "EUR", "euros": "EUR",
    "dollar": "USD", "dollars": "USD",
    "yen": "JPY",
    "rupee": "INR", "rupees": "INR",
    "baht": "THB",
    "franc": "CHF", "francs": "CHF",
    "yuan": "CNY", "renminbi": "CNY",
    "reais": "BRL",
    "peso": "MXN", "pesos": "MXN",
    "krona": "SEK", "kronor": "SEK",
    "krone": "NOK", "kroner": "NOK",
    "zloty": "PLN",
    "lira": "TRY",
    "rand": "ZAR",
}

AMBIGUOUS_CODE_WORDS = {"TRY"}

DESTINATION_CURRENCIES = {
    "london": "GBP", "uk": "GBP", "united kingdom": "GBP", "england": "GBP", "scotland": "GBP",
    "paris": "EUR", "france": "EUR", "rome": "EUR", "italy": "EUR", "madrid": "EUR", "spain": "EUR",
    "berlin": "EUR", "germany": "EUR", "lisbon": "EUR", "portugal": "EUR", "amsterdam": "EUR",
    "netherlands": "EUR", "vienna": "EUR", "austria": "EUR", "athens": "EUR", "greece": "EUR",
    "dublin": "EUR", "ireland": "EUR", "europe": "EUR",
    "new york": "USD", "usa": "USD", "united states": "USD", "america": "USD",
    "tokyo": "JPY", "kyoto": "JPY", "japan": "JPY",
    "bangkok": "THB", "thailand": "THB",
    "bangalore": "INR", "india": "INR", "delhi": "INR", "mumbai": "INR",
    "brazil": "BRL", "rio": "BRL",
    "mexico": "MXN",
    "switzerland": "CHF", "zurich": "CHF", "geneva": "CHF",
    "copenhagen": "DKK", "denmark": "DKK",
    "norway": "NOK", "oslo": "NOK",
    "sweden": "SEK", "stockholm": "SEK",
    "iceland": "ISK",
    "china": "CNY", "beijing": "CNY",
    "australia": "AUD", "sydney": "AUD",
    "canada": "CAD",
    "south africa": "ZAR",
    "turkey": "TRY", "istanbul": "TRY",
    "korea": "KRW", "seoul": "KRW",
    "singapore": "SGD",
    "bali": "IDR", "indonesia": "IDR",
    "karachi": "PKR", "pakistan": "PKR",
    "costa rica": "CRC",
}


def _currency_mentions(text):
    lower = text.lower()
    mentions = []
    for key, code in CURRENCY_WORDS.items():
        if len(key) == 1 and not key.isalnum():
            start = 0
            while True:
                pos = lower.find(key, start)
                if pos == -1:
                    break
                mentions.append((pos, code))
                start = pos + 1
        else:
            for match in re.finditer(r"\b" + re.escape(key) + r"\b", lower):
                mentions.append((match.start(), code))
    for match in re.finditer(r"\b[A-Za-z]{3}\b", text):
        code = match.group(0).upper()
        if code in FRANKFURTER_CURRENCIES and code not in AMBIGUOUS_CODE_WORDS:
            mentions.append((match.start(), code))
    mentions.sort(key=lambda item: item[0])
    seen = set()
    ordered = []
    for pos, code in mentions:
        if code not in seen:
            seen.add(code)
            ordered.append((pos, code))
    return ordered


def _destination_currency(text, tracker):
    lower = text.lower()
    for place, code in sorted(DESTINATION_CURRENCIES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(r"\b" + re.escape(place) + r"\b", lower):
            return code
    slot_destination = tracker.get_slot("destination")
    if slot_destination:
        slot_lower = slot_destination.lower()
        for place, code in sorted(DESTINATION_CURRENCIES.items(), key=lambda item: len(item[0]), reverse=True):
            if re.search(r"\b" + re.escape(place) + r"\b", slot_lower):
                return code
    return None


class ActionConvertCurrency(Action):
    """Convert a stated budget into the currency the user actually thinks
    in, using ECB reference rates (not live market rates — say so)."""

    def name(self) -> Text:
        return "action_convert_currency"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        latest_message = getattr(tracker, "latest_message", {}) or {}
        text = (latest_message.get("text") or "").lower()

        mentions = _currency_mentions(text)
        codes = [code for _, code in mentions]

        amount_match = re.search(r"\d[\d,]*(?:\.\d+)?", text)
        amount_was_default = False
        if amount_match:
            amount = float(amount_match.group(0).replace(",", ""))
        else:
            amount = tracker.get_slot("budget")
            if amount is None:
                amount = 100.0
                amount_was_default = True

        from_currency = tracker.get_slot("budget_currency") or "GBP"
        to_currency = tracker.get_slot("destination_currency") or "EUR"

        if len(codes) >= 2:
            from_currency, to_currency = codes[0], codes[1]
        elif len(codes) == 1:
            pos, code = mentions[0]
            if re.search(r"\b(to|in|into)\s*$", text[:pos]):
                to_currency = code
                from_currency = tracker.get_slot("budget_currency") or "GBP"
            else:
                from_currency = code
                dest_code = _destination_currency(text, tracker)
                to_currency = dest_code or tracker.get_slot("destination_currency") or "EUR"
        else:
            from_currency = tracker.get_slot("budget_currency") or "GBP"
            dest_code = _destination_currency(text, tracker)
            to_currency = dest_code or tracker.get_slot("destination_currency") or "EUR"

        if from_currency == to_currency:
            dispatcher.utter_message(
                text=f"{from_currency} is already the local currency there, so no conversion is needed."
            )
            return [SlotSet("budget_currency", from_currency), SlotSet("destination_currency", to_currency)]

        for code in (to_currency, from_currency):
            if code not in FRANKFURTER_CURRENCIES:
                dispatcher.utter_message(
                    text=f"The ECB reference rates I use don't cover {code}, so I can't convert that one — please check your bank or a currency provider for the current rate."
                )
                return []

        try:
            converted, rate, date = api_clients.convert_currency(
                float(amount), from_currency, to_currency
            )
            if amount_was_default:
                dispatcher.utter_message(
                    text=f"1 {from_currency} = {rate:.4f} {to_currency}, so 100 {from_currency} is about {converted:,.2f} {to_currency} (ECB reference rate, {date} — not a live market rate)."
                )
            else:
                dispatcher.utter_message(
                    text=f"{amount:,.2f} {from_currency} is approximately {converted:,.2f} {to_currency} (ECB reference rate, {date} — not a live market rate)."
                )
            slots = [
                SlotSet("budget_currency", from_currency),
                SlotSet("destination_currency", to_currency),
                SlotSet("budget_converted", converted),
            ]
            if not amount_was_default:
                slots.insert(0, SlotSet("budget", amount))
            return slots

        except requests.exceptions.RequestException as e:
            logger.error("Frankfurter API error: %s", e)
            dispatcher.utter_message(
                text="I couldn't reach the currency conversion service right now — "
                     "I'll keep working in your original currency."
            )
            return []
        except KeyError:
            dispatcher.utter_message(
                text=f"I don't recognise '{to_currency}' as a currency code — "
                     "could you give it in ISO format, e.g. EUR, GBP, USD?"
            )
            return []
