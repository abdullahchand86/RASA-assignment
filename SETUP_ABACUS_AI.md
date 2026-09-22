# Abacus AI Integration Setup

## Quick Start

1. **Get your API key:**
   - Go to https://apps.abacus.ai/chatllm/admin/profile
   - Navigate to API Keys section
   - Generate a new key and copy it

2. **Add to `.env` file:**
   ```bash
   # Edit .env in this directory and replace <your-api-key-here> with your actual key
   ABACUS_API_KEY=<your-api-key-here>
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements-actions.txt
   ```

4. **Test the integration:**
   - Start RASA: `rasa run actions`
   - In another terminal: `rasa shell`
   - Try: "Give me a sustainability tip"

## What's Integrated

- **Model:** Claude Opus 4.1 (latest Abacus AI model)
- **Action:** `action_generate_travel_tip` — generates personalized sustainability tips
- **Fallback:** If API key is missing or call fails, uses hardcoded tips

## How It Works

The `_get_llm_response()` helper function in `actions.py`:
1. Reads `ABACUS_API_KEY` from `.env`
2. Creates an Abacus AI client
3. Calls Claude Opus 4.1 with your prompt
4. Returns the response or `None` on error

The `ActionGenerateTravelTip` action:
1. Collects context (destination, transport mode, sustainability preference)
2. Builds a prompt for Opus
3. Calls the LLM
4. Falls back to hardcoded tips if the LLM is unavailable

## Extending It

To add more LLM-powered actions:

```python
class ActionMyNewAction(Action):
    def name(self) -> Text:
        return "action_my_new_action"
    
    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        prompt = "Your prompt here"
        response = _get_llm_response(prompt)
        if response:
            dispatcher.utter_message(text=response)
        else:
            dispatcher.utter_message(text="Fallback message")
        return []
```

Then add it to `domain.yml` under `actions:`.

## Troubleshooting

- **"ABACUS_API_KEY not configured":** Check `.env` file and ensure it's not set to `<your-api-key-here>`
- **"Abacus AI LLM call failed":** Check your internet connection and API key validity
- **Import errors:** Run `pip install -r requirements-actions.txt` again

## Credits & Billing

Each LLM call uses credits from your Abacus AI subscription. Monitor usage at:
https://apps.abacus.ai/chatllm/admin/profile
