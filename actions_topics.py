"""
actions_topics.py
Two custom actions replacing ~48 niche single-purpose intents. Each reads
the `topic` entity/slot (normalised via data/nlu.yml's synonyms) and returns
the matching response from the dictionaries below.

Consolidated intents:
- ask_sustainability_topic -> action_answer_sustainability_topic
- ask_travel_logistics     -> action_answer_travel_logistics

Canonical topic keys must match the synonym values in data/nlu.yml exactly
(case-sensitive). An unmapped topic falls back to a graceful message rather
than guessing, and is logged for later expansion of the answer set.
"""

import logging
from typing import Any, Text, Dict, List

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher

logger = logging.getLogger(__name__)


# Canonical topic key -> response text. Keys must match the synonym values
# in data/nlu.yml exactly (case-sensitive).
SUSTAINABILITY_ANSWERS: Dict[str, str] = {
    "carbon_offsets": "Carbon offsets fund projects that reduce or capture emissions elsewhere "
        "to compensate for your trip's footprint. Look for Gold Standard or Verra-verified "
        "offsets — those are independently audited, unlike many unverified schemes.",
    "carbon_scope": "Scope 1 covers direct emissions (e.g. a car you drive), Scope 2 covers "
        "purchased energy, and Scope 3 covers indirect emissions in your travel chain — "
        "most flight and hotel emissions fall under Scope 3.",
    "carbon_label": "An eco label on a hotel listing should be traceable to a named "
        "certification body. I only mark accommodation as eco-certified when it matches "
        "our verified dataset — never from an unverified listing alone.",
    "climate_impact": "Travel, especially flying, is a meaningful contributor to personal "
        "carbon footprints. Choosing train over short-haul flights, staying longer in fewer "
        "places, and offsetting what you can't avoid all help.",
    "food_footprint": "Locally sourced, plant-forward meals generally have a lower carbon "
        "footprint than imported or heavily processed food — worth asking where ingredients "
        "come from when you can.",
    "shopping_footprint": "Souvenirs shipped internationally or mass-produced from imported "
        "materials carry a higher footprint than locally made goods you can carry home "
        "yourself.",
    "renewable_energy": "Some accommodation options run partly or fully on renewable power — "
        "I can flag this when it's documented in our verified dataset, but I won't claim it "
        "without a source.",
    "energy_saving": "Simple guest-side habits — reusing towels, turning off AC when out, "
        "unplugging chargers — meaningfully reduce a stay's energy footprint.",
    "water_conservation": "Shorter showers, reusing towels, and reporting leaks all help in "
        "water-stressed destinations — worth checking if your destination is one.",
    "plastic_free": "Carrying a refillable bottle, saying no to single-use amenities, and "
        "packing a reusable bag are the highest-impact easy wins for plastic-free travel.",
    "wildlife_conservation": "Choose operators that don't allow direct animal contact for "
        "entertainment, and support reserves with transparent conservation funding.",
    "marine_conservation": "Use reef-safe sunscreen (avoid oxybenzone and octinoxate), never "
        "touch coral, and keep a respectful distance from marine wildlife.",
    "coral_reef": "Reef-safe sunscreen avoids oxybenzone and octinoxate — both are linked to "
        "coral bleaching even at low concentrations.",
    "reforestation": "Look for reforestation programmes with transparent, third-party-verified "
        "planting records rather than a bare pledge — I can point you to verified options if "
        "your destination has one listed.",
    "national_parks": "I can look up nearby attractions including protected natural areas — "
        "ask me to find attractions near your destination for specifics.",
    "overtourism": "Overtourism happens when visitor numbers outpace a destination's "
        "infrastructure and community capacity. Visiting shoulder season and lesser-known "
        "nearby towns both help spread impact.",
    "hotel_certifications": "Trustworthy hotel certifications include Green Key, EarthCheck, "
        "and B Corp — I only apply an eco-certified label when a hotel matches one of these "
        "in our verified dataset.",
    "community_tourism": "Community-based tourism channels revenue directly to local "
        "residents, often through homestays, local guides, or cooperatively run tours.",
    "volunteering": "If a destination has volunteering options, prioritise ones that are "
        "skills-based and locally led — be cautious of short-term placements involving "
        "children, which child-safety organisations generally advise against.",
    "local_guides": "Locally based guides typically have the deepest destination knowledge "
        "and keep more tourism revenue in the local economy than international operators.",
    "local_culture": "Local etiquette varies a lot by destination — dress norms, tipping "
        "customs, and photography permissions are the most common things travellers miss. "
        "Let me know your destination and I can be more specific.",
    "sustainable_souvenirs": "Locally made crafts from identifiable artisans generally beat "
        "mass-produced or imported souvenirs on both authenticity and footprint.",
    "general_tips": "Pack light, favour trains and buses over short flights where practical, "
        "choose verified eco-certified stays, and offset what you can't avoid.",
    "packing_tips": "Packing light reduces transport emissions; a capsule wardrobe and "
        "reusable essentials (bottle, bag, cutlery) cover most sustainable-packing advice.",
    "budget_friendly": "Sustainable travel doesn't have to cost more — public transport, "
        "local markets, and homestays are often both cheaper and lower-impact than the "
        "alternatives.",
    "family_friendly": "Nature-based activities, local markets, and community-led tours tend "
        "to work well for sustainable family travel — let me know your destination for "
        "specifics.",
    "slow_travel": "Slow travel means staying longer in fewer places, favouring surface "
        "transport, and engaging more deeply with one destination rather than covering many.",
    "digital_detox": "Some destinations have retreats specifically designed around "
        "disconnecting — tell me your destination and I can check what's nearby.",
    "solo_travel": "For solo sustainable travel, homestays and small-group local tours are "
        "both safer and lower-impact than isolated independent routes.",
    "compare_destinations": "I can compare destinations on verified carbon and transport "
        "data once you tell me which two you're weighing up.",
    "carbon_neutral_destinations": "No destination is fully carbon neutral once travel "
        "emissions are included, but some regions have strong public transport and renewable "
        "infrastructure that lowers your footprint significantly once there.",
    "best_time_to_visit": "Shoulder season generally means lower crowds, lower prices, and "
        "less strain on local infrastructure than peak season — let me know your destination "
        "for specifics.",
}

TRAVEL_LOGISTICS_ANSWERS: Dict[str, str] = {
    "visa": "Visa requirements depend on your nationality and destination — I'd recommend "
        "checking your destination's official government or embassy site, since requirements "
        "change and I don't have a live visa-rules feed.",
    "travel_insurance": "Look for a policy that explicitly covers your planned activities "
        "(e.g. hiking, diving) and has adequate medical evacuation cover — the right level "
        "varies a lot by destination and trip type.",
    "health_precautions": "Health precautions vary significantly by destination — your GP or "
        "a travel health clinic can give guidance specific to where you're going.",
    "vaccinations": "Required and recommended vaccinations depend on your destination and "
        "are best confirmed with a travel health clinic, since requirements change.",
    "time_zone": "I can check this once I know your destination — ask me again with the "
        "place name and I'll confirm the time zone.",
    "airport_info": "Tell me your destination and I can help orient you, though I don't have "
        "live airport operational data (delays, gates) — check your airline directly for that.",
    "language_tips": "Learning a handful of local greetings and courtesy phrases goes a long "
        "way — let me know your destination and I can suggest a few.",
    "safety": "I don't have a live safety-advisory feed — check your government's official "
        "travel advisory site for current, destination-specific guidance before you go.",
    "luggage_allowance": "Luggage allowances vary by airline and fare class — check directly "
        "with your airline, since this isn't something I have live access to.",
    "group_discounts": "Group discount availability depends on the specific provider — happy "
        "to help plan group logistics once I know more about your trip.",
    "loyalty_program": "I don't currently manage a loyalty programme, but I can still help "
        "you plan and compare sustainable options for your trip.",
    "booking_assistance": "I can help research and rank options, but for actual booking and "
        "payment I'd hand you off to a human advisor — want me to do that now?",
    "trip_cost": "I can give you a rough sense of accommodation and transport costs once I "
        "know your destination, dates, and preferences — share those and I'll estimate.",
    "public_transport_pass": "Worth it in most dense, well-connected cities — I can check "
        "public transport density for your destination if you tell me where you're headed.",
    "bot_language_support": "Right now I operate in English. If multi-language support is "
        "needed, that's flagged as an optional extension in the project scope.",
}


class ActionAnswerSustainabilityTopic(Action):
    """Resolve the `topic` entity for the consolidated ask_sustainability_topic intent."""

    def name(self) -> Text:
        return "action_answer_sustainability_topic"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        topic = next(tracker.get_latest_entity_values("topic"), None)
        answer = SUSTAINABILITY_ANSWERS.get(topic)

        if answer:
            dispatcher.utter_message(text=answer)
        else:
            logger.info("Unmapped sustainability topic: %r", topic)
            dispatcher.utter_message(
                text="I don't have a specific answer for that sustainability question yet, "
                     "but I can connect you with a human advisor if you'd like more detail."
            )
        return []


class ActionAnswerTravelLogistics(Action):
    """Resolve the `topic` entity for the consolidated ask_travel_logistics intent."""

    def name(self) -> Text:
        return "action_answer_travel_logistics"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        topic = next(tracker.get_latest_entity_values("topic"), None)
        answer = TRAVEL_LOGISTICS_ANSWERS.get(topic)

        if answer:
            dispatcher.utter_message(text=answer)
        else:
            logger.info("Unmapped logistics topic: %r", topic)
            dispatcher.utter_message(
                text="I don't have a specific answer for that yet, but I can connect you "
                     "with a human advisor if you'd like more detail."
            )
        return []
