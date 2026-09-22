"""
test_bot_live.py
Smoke-test a running Rasa server (rasa run --enable-api) by sending a
sequence of messages through the REST channel and printing bot replies,
buttons, and any custom payload (e.g. carbon_tier, handover_active).

Usage:
    pip install requests --break-system-packages
    python test_bot_live.py                 # runs all scenarios below
    python test_bot_live.py --interactive    # type messages yourself

Assumes Rasa is running at http://localhost:5005 (default docker-compose
port from Task 6). Change RASA_URL if different.
"""

import argparse
import json
import sys
import time
import uuid

import requests

RASA_URL = "http://localhost:5005/webhooks/rest/webhook"
REQUEST_TIMEOUT = 10


def send_message(sender_id: str, message: str):
    """Send one message to the bot and return the list of response messages."""
    try:
        response = requests.post(
            RASA_URL,
            json={"sender": sender_id, "message": message},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        print(f"\n[ERROR] Could not connect to {RASA_URL}")
        print("Is the Rasa server running? Check with: docker compose ps")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print(f"\n[ERROR] Rasa server did not respond within {REQUEST_TIMEOUT}s")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"\n[ERROR] HTTP {response.status_code}: {e}")
        return []


def print_bot_replies(replies):
    if not replies:
        print("  Bot: (no response — check server logs, this often means an "
              "action_server connection failure or missing response template)")
        return

    for reply in replies:
        if "text" in reply:
            print(f"  Bot: {reply['text']}")
        if "buttons" in reply:
            for b in reply["buttons"]:
                print(f"       [button] {b['title']}  -> payload: {b['payload']}")
        if "custom" in reply:
            print(f"  Bot (custom payload): {json.dumps(reply['custom'])}")
        if "image" in reply:
            print(f"  Bot (image): {reply['image']}")


def run_scenario(name: str, turns: list):
    """Send a fixed sequence of user turns and print each exchange."""
    sender_id = f"test-{uuid.uuid4().hex[:8]}"
    print(f"\n{'=' * 60}\nSCENARIO: {name}   (sender_id={sender_id})\n{'=' * 60}")

    for turn in turns:
        print(f"\nUser: {turn}")
        replies = send_message(sender_id, turn)
        print_bot_replies(replies)
        time.sleep(0.3)  # small pause so logs / server processing keep up


def interactive_mode():
    sender_id = f"interactive-{uuid.uuid4().hex[:8]}"
    print(f"Interactive session (sender_id={sender_id}). Type 'quit' to exit.\n")
    while True:
        try:
            message = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nEnded.")
            break
        if message.lower() in ("quit", "exit"):
            break
        if not message:
            continue
        replies = send_message(sender_id, message)
        print_bot_replies(replies)


# ---------------------------------------------------------------------------
# Scripted scenarios — mirror the Task 3 flows / Task 5 test stories
# ---------------------------------------------------------------------------

SCENARIOS = {
    "happy_path_high_sustainability": [
        "hi",
        "I want to go to Kyoto",
        "low carbon is my top priority",
        "yes",
    ],
    "ambiguous_input_clarification": [
        "hi",
        "umm not sure, whatever is fine i guess",
        "asdkjhasdkjh",  # second consecutive nonsense -> should escalate to handover
    ],
    "explicit_human_request": [
        "I want to go to Lisbon",
        "can I just speak to a real person",
    ],
    "out_of_scope_recovery": [
        "tell me a joke",
        "anyway, I want to go to Berlin",
        "balance cost and impact",
    ],
    "carbon_question": [
        "hi",
        "what's the carbon footprint of flying to Bali",
    ],
}


def main():
    parser = argparse.ArgumentParser(description="Test a running Rasa bot via REST API")
    parser.add_argument("--interactive", action="store_true", help="Chat manually instead of running scripted scenarios")
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()), help="Run only one named scenario")
    parser.add_argument("--url", default=RASA_URL, help="Override the Rasa REST webhook URL")
    args = parser.parse_args()

    global RASA_URL
    RASA_URL = args.url

    if args.interactive:
        interactive_mode()
        return

    if args.scenario:
        run_scenario(args.scenario, SCENARIOS[args.scenario])
        return

    for name, turns in SCENARIOS.items():
        run_scenario(name, turns)

    print(f"\n{'=' * 60}\nAll scenarios sent. Review replies above for:")
    print("  - Correct slot-driven branching (destination/sustainability_level)")
    print("  - Carbon tier / colour-coding present in custom payload")
    print("  - Handover triggered on explicit request AND on repeated fallback")
    print("  - Out-of-scope message doesn't break the ongoing flow")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
