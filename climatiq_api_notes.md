# Climatiq API — Notes & Reference

Personal reference doc for using the Climatiq API in the sustainable tourism chatbot project (RASAWork).

---

## 1. What Climatiq Is

Climatiq is a carbon-accounting **data and calculation engine**. It doesn't track emissions automatically — it gives you:
- **Emission factors** (kg CO2e per unit of activity, e.g. per passenger-km, per kWh)
- **Calculation endpoints** that turn an activity + quantity into a CO2e estimate

It does **not** provide tourism-specific data (no "museum visit" or "day of diving" factors) — only general activity categories (transport, energy, freight, procurement, real estate).

---

## 2. Account & Plan

- **Free tier:** 250 API calls/month
- Free tier includes: `/search` and `/estimate` endpoints only
- **NOT included on free tier:**
  - Raw `factor` values in `/search` results (always returns `null` unless you have the add-on)
  - Travel API (`/travel/v1/distance`) — returns `403 forbidden` on free plan
  - Energy, Freight, Procurement convenience endpoints

**Practical implication:** for flight/travel emissions, you must calculate distance yourself (e.g. Haversine formula) and feed it into the free `/estimate` endpoint — see Section 5.

---

## 3. Authentication

Every request needs a Bearer token header:

```
Authorization: Bearer YOUR_API_KEY
```

**Security note:** never hardcode the key in scripts or share it in plain text. Use an environment variable instead.

### Setting the env var on Mac (zsh)

Temporary (current terminal session only):
```bash
export CLIMATIQ_API_KEY="your_key_here"
```

Permanent (persists across sessions):
```bash
echo 'export CLIMATIQ_API_KEY="your_key_here"' >> ~/.zshrc
source ~/.zshrc
```

Check it's set:
```bash
echo $CLIMATIQ_API_KEY
```

⚠️ If a key has ever been pasted in plain text somewhere (chat, docs, screenshots), rotate it from the Climatiq dashboard.

---

## 4. Key Endpoints

| Endpoint | Method | Free? | Purpose |
|---|---|---|---|
| `/data/v1/search` | GET | ✅ | Browse/find emission factors by keyword + filters |
| `/data/v1/estimate` | POST | ✅ | Calculate CO2e from an `activity_id` + quantity + unit |
| `/travel/v1/distance` | POST | ❌ Premium | Auto-calculates distance + CO2e from origin/destination |
| `/travel/flights` | POST | ❌ Deprecated | Old flights endpoint — don't use, being phased out |

### Required parameter for `/search`
`data_version` is **required** (e.g. `^6` for "latest major version 6"). Missing it causes a `400 invalid_input` error.

---

## 5. Common curl Commands

### Search for emission factors (general)
```bash
curl --location --request GET "https://api.climatiq.io/data/v1/search?query=electricity&region=DE&category=Electricity&data_version=^6" \
--header "Authorization: Bearer $CLIMATIQ_API_KEY"
```

**Important:** Always wrap the entire URL (with all `?` and `&` params) in ONE pair of quotes. If any `&` sits outside quotes, your shell will misinterpret it (e.g. try to background a process).

### Search parameters available
| Parameter | Description | Example |
|---|---|---|
| `query` | Free-text search | `electricity`, `flight` |
| `data_version` | Required — dataset version | `^6` |
| `region` | ISO country code | `DE`, `US`, `GB` |
| `sector` | High-level sector | `Energy`, `Transport` |
| `category` | Specific category | `Electricity`, `Air Travel` |
| `source` | Data source | `EEA`, `UBA`, `DEFRA` |
| `year` | Emission factor year | `2023` |
| `unit_type` | Unit type filter | `Energy`, `Distance` |
| `results_per_page` / `page` | Pagination | `50` / `1` |

Every `/search` response includes a `possible_filters` block showing exactly what filter values are valid for that query — useful for exploring what's available.

### Estimate emissions (flight example, using distance you calculate yourself)
```bash
curl --location --request POST "https://api.climatiq.io/data/v1/estimate" \
--header "Authorization: Bearer $CLIMATIQ_API_KEY" \
--header "Content-Type: application/json" \
--data '{
  "emission_factor": {
    "activity_id": "passenger_flight-route_type_international-aircraft_type_na-distance_na-class_na-rf_included-distance_uplift_na",
    "data_version": "^6"
  },
  "parameters": {
    "passengers": 1,
    "distance": 8984,
    "distance_unit": "km"
  }
}'
```

**Workflow to replicate the paid Travel API on free tier:**
1. Find city/airport coordinates (e.g. `airportsdata` Python library)
2. Calculate great-circle distance yourself (Haversine formula)
3. Pick the right `activity_id` from a `/search` call (e.g. domestic vs international, with/without radiative forcing)
4. Call `/estimate` with that `activity_id` + calculated distance

---

## 6. Scripts Built So Far

| File | Purpose |
|---|---|
| `parse_climatiq.py` | Parses a saved JSON response file into a readable table |
| `climatiq_search.py` | Standalone script — calls `/search` directly with CLI args (`--query`, `--region`, `--sector`, etc.), prints table + filters, optional `--save-json` |

**Next planned script:** `climatiq_travel.py` — takes `--origin`, `--destination`, `--mode`, calculates distance itself (free, no premium Travel API needed), calls `/estimate`, and returns the CO2e result. Full replication of the premium Travel feature using only free-tier endpoints.

---

## 7. Sustainable Tourism Chatbot — Use Case Ideas

- **Trip carbon estimator:** sum emissions across flights + hotel nights + local transport for a full itinerary
- **Mode comparison:** "train vs flight" — same route, two `/estimate` calls, show the delta
- **Accommodation nudging:** rank/badge stays by estimated per-night energy emissions (PCAF real estate dataset, country-level granularity)
- **Offset suggestions:** after computing footprint, link out to a verified offset provider (Climatiq doesn't sell offsets itself)
- **"Greener alternative" tips:** e.g. "This route by rail is X% lower emissions than flying"

### Known limitations for tourism use
- No activity-level granularity (museums, tours, excursions) — must approximate using proxy categories
- Regional data is mostly **country-level**, not city-level (e.g. no "Berlin-specific" grid factor, only "DE")
- Rail network coverage is incomplete globally — car-route distances sometimes substituted
- Free tier caps at 250 calls/month — worth caching/batching results if scaling up

---

## 8. Useful Links
- API Reference: https://www.climatiq.io/docs/api-reference
- Pricing/Plans: https://www.climatiq.io/pricing
- Data Explorer (browse factors visually): via Climatiq dashboard
- Activity ID guide: https://www.climatiq.io/docs (search "Activity ID guide")

---

*Last updated: 2026-09-14*
