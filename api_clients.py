"""
api_clients.py
Low-level wrappers for six key-free external APIs, used by the custom
actions in actions_new.py. Kept separate from actions.py so these functions
can be unit-tested without importing rasa_sdk.

APIs covered:
  - Overpass (OpenStreetMap): hotels, attractions, transport
  - Open-Meteo: weather
  - Wikipedia REST: place descriptions
  - Frankfurter: currency conversion

All functions raise requests.exceptions.RequestException on failure —
calling actions are responsible for catching it and sending a fallback
message, consistent with the error-handling pattern already in actions.py.
"""

import urllib.parse
import requests

HEADERS = {
    "User-Agent": "BSBI-EcoTravel-Student/1.0 (coursework)"
}

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
FRANKFURTER_URL = "https://api.frankfurter.dev/v1"
VISITABLE_HISTORIC = "castle|monument|ruins|fort|archaeological_site|city_gate"


# ---------------------------------------------------------------------------
# Hotels (OSM — replaces the dead Amadeus integration)
# ---------------------------------------------------------------------------

def find_hotels(lat, lon, radius_m=3000, limit=15):
    """Find hotels near a point via Overpass. No reliable eco-cert data —
    callers must not claim 'eco-certified' from this source alone."""
    query = f"""
    [out:json][timeout:45];
    nwr["tourism"="hotel"](around:{radius_m},{lat},{lon});
    out center {limit};
    """
    response = requests.post(OVERPASS_URL, data={"data": query},
                              headers=HEADERS, timeout=60)
    response.raise_for_status()

    hotels = []
    for element in response.json().get("elements", []):
        tags = element.get("tags", {})
        if not tags.get("name"):
            continue
        lat_val = element.get("lat") or element.get("center", {}).get("lat")
        lon_val = element.get("lon") or element.get("center", {}).get("lon")
        hotels.append({
            "name": tags["name"],
            "stars": tags.get("stars"),
            "osm_eco_tag": tags.get("green_key") or tags.get("ecolabel"),
            "lat": lat_val,
            "lon": lon_val,
        })
    return hotels


# ---------------------------------------------------------------------------
# Attractions (OSM)
# ---------------------------------------------------------------------------

def find_attractions(lat, lon, radius_m=1500, limit=12):
    """Find museums and visit-worthy historic sites near a location."""
    query = f"""
    [out:json][timeout:45];
    (
      nwr["tourism"="museum"](around:{radius_m},{lat},{lon});
      nwr["historic"~"^({VISITABLE_HISTORIC})$"](around:{radius_m},{lat},{lon});
    );
    out center {limit};
    """
    response = requests.post(OVERPASS_URL, data={"data": query},
                              headers=HEADERS, timeout=60)
    response.raise_for_status()

    sites = []
    for element in response.json().get("elements", []):
        tags = element.get("tags", {})
        if not tags.get("name"):
            continue
        lat_val = element.get("lat") or element.get("center", {}).get("lat")
        lon_val = element.get("lon") or element.get("center", {}).get("lon")
        sites.append({
            "name": tags["name"],
            "kind": tags.get("tourism") or tags.get("historic"),
            "lat": lat_val,
            "lon": lon_val,
        })
    return sites


# ---------------------------------------------------------------------------
# Public transport proximity (OSM)
# ---------------------------------------------------------------------------

def find_transport(lat, lon, radius_m=1500):
    """Find rail/metro/tram access points near a location (locations only —
    no live timetables)."""
    query = f"""
    [out:json][timeout:45];
    (
      node["railway"="station"](around:{radius_m},{lat},{lon});
      node["railway"="subway_entrance"](around:{radius_m},{lat},{lon});
      node["railway"="tram_stop"](around:{radius_m},{lat},{lon});
    );
    out body 25;
    """
    response = requests.post(OVERPASS_URL, data={"data": query},
                              headers=HEADERS, timeout=60)
    response.raise_for_status()

    stops = []
    for element in response.json().get("elements", []):
        tags = element.get("tags", {})
        stops.append({
            "name": tags.get("name", "(unnamed)"),
            "type": tags.get("railway", "unknown"),
        })
    return stops


def summarise_transport(stops):
    """Turn a stop list into a one-line verdict a bot can say out loud."""
    if len(stops) >= 10:
        return "excellent — you will not need a car"
    if len(stops) >= 3:
        return "reasonable, but check routes for your specific trip"
    return "limited — factor transport emissions into your planning"


# ---------------------------------------------------------------------------
# Place descriptions (Wikipedia)
# ---------------------------------------------------------------------------

def describe_place(title, sentences=2):
    """Short description of a place from Wikipedia, or None if no article."""
    safe_title = urllib.parse.quote(title.replace(" ", "_"))
    response = requests.get(
        f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe_title}",
        headers=HEADERS,
        timeout=20,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()

    data = response.json()
    text = data.get("extract", "")
    parts = text.split(". ")
    short = ". ".join(parts[:sentences])
    if short and not short.endswith("."):
        short += "."

    return {
        "title": data.get("title"),
        "description": data.get("description"),
        "summary": short,
        "url": data.get("content_urls", {}).get("desktop", {}).get("page"),
    }


# ---------------------------------------------------------------------------
# Weather (Open-Meteo)
# ---------------------------------------------------------------------------

def get_weather(lat, lon):
    """Current conditions plus a 3-day outlook for a coordinate."""
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,precipitation,wind_speed_10m",
            "daily": "temperature_2m_max,precipitation_sum",
            "forecast_days": 3,
            "timezone": "auto",
        },
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def weather_travel_advice(temp_c, rain_mm):
    """Turn numbers into one line of sustainability-relevant travel advice."""
    if rain_mm > 5:
        return "wet — plan indoor activities, and public transport over cycling"
    if temp_c < 5:
        return "cold — walking tours will be hard going"
    if temp_c > 30:
        return "hot — cycling and long walks are unwise in the middle of the day"
    return "good conditions for walking and cycling"


# ---------------------------------------------------------------------------
# Currency (Frankfurter)
# ---------------------------------------------------------------------------

def get_exchange_rate(from_currency, to_currency):
    """1 unit of from_currency in to_currency. Returns (rate, date)."""
    response = requests.get(
        f"{FRANKFURTER_URL}/latest",
        params={"base": from_currency, "symbols": to_currency},
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    return data["rates"][to_currency], data["date"]


def convert_currency(amount, from_currency, to_currency):
    """Convert an amount. Returns (converted_amount, rate, rate_date)."""
    rate, date = get_exchange_rate(from_currency, to_currency)
    return amount * rate, rate, date
