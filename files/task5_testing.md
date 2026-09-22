# Task 5 — Testing

## 5.1 NLU testing

**Command:**
```bash
rasa test nlu --nlu data/nlu.yml --cross-validation --folds 5
```

Cross-validation (rather than a single train/test split) is used because the
dataset is small — a single 80/20 split on ~30 examples per intent would give
a noisy, unstable F1 estimate. 5-fold CV trains on 4/5 of the data and tests
on the held-out fold five times, averaging the result.

**What to report in your submission:**
- The generated `results/intent_confusion_matrix.png` — look specifically at
  which intents get confused with each other (in this domain, expect
   `ask_carbon_footprint_estimate` vs `ask_sustainability_tips` to be the most
  likely confusion pair, since both mention environmental terms).
- Per-intent F1, precision, recall from `results/intent_report.json`.
- **Target**: F1 ≥ 0.85 per intent, as set in the assignment brief. If an
  intent falls below this, the fix is usually adding more varied training
  examples (different phrasings, lengths, entity positions) — not more
  epochs.
- Entity extraction accuracy from `results/DIETClassifier_report.json`,
  particularly for `destination` and `sustainability_level`, since these
  drive the branching logic in Task 3.

## 5.2 Dialogue (Core) testing

**Command:**
```bash
rasa test core --stories data/test_stories.yml --out results
```

`test_stories.yml` covers:
1. **Happy path** — full trip-intake-to-recommendation flow.
2. **Edge case: ambiguous input** — verifies `action_two_stage_clarify` fires
   correctly on low-confidence input rather than guessing an intent.
3. **Repeated fallback → escalation** — verifies the two-stage clarification
   rule from Task 3 actually escalates to `action_human_handover` on a
   second consecutive failure, not just on explicit request.
4. **Mid-flow human request** — verifies handover works even when the user
   interrupts an in-progress trip-planning flow, and that slots collected so
   far are still present in the handover package (checked via the unit test
   `test_handover_sets_flag_and_limits_context` in `test_actions.py`).
5. **Out-of-scope recovery** — verifies an irrelevant message doesn't corrupt
   slots or derail the story when the user returns to trip planning.

Failed stories appear in `results/failed_test_stories.yml` with a diff
showing predicted vs. expected action — include a screenshot of any failures
you had to debug in your report; graders want to see iteration, not just a
clean final run.

## 5.3 Unit testing (pytest)

`test_actions.py` (14 tests, all passing against the Task 4 `actions.py`)
mocks every external API call so tests run offline and deterministically.
Coverage per action:

| Action | Success case | Failure/timeout case | Edge case |
|---|---|---|---|
| `action_get_location` | valid geocode → slots set | timeout / connection error → fallback message | empty destination slot |
| `action_calculate_carbon` | tier assigned correctly | API exception → `carbon_tier="unknown"` | — |
| `action_rank_options` | high preference ranks eco-certified first | — | empty options list |
| `action_human_handover` | flag set, context capped at last 10 events | — | — |
| `action_two_stage_clarify` | first fallback shows buttons | — | second fallback auto-escalates |

Run with `pytest test_actions.py -v --cov=actions` to also get a coverage
percentage to quote in your report.

## 5.4 User testing

Given coursework time constraints, a lightweight think-aloud protocol with
5–8 participants is realistic and defensible, rather than a large-N study.

**Protocol:**
1. Each participant completes one of the three scenarios from Task 3
   (city break / rural eco-tour / business trip) unaided, thinking aloud.
2. Record: task completion (yes/no), time to first recommendation, number of
   fallback triggers, whether they noticed the carbon-impact colour coding
   unprompted.
3. Immediately after, a 5-question post-task survey (5-point Likert unless
   noted):

   1. "The chatbot's questions were easy to understand." (clarity)
   2. "I trusted the sustainability information the chatbot gave me."
      (trust)
   3. "The carbon-impact indicators influenced my choice." (persuasiveness)
   4. "I knew what was happening when I was handed over to a human advisor."
      (transparency)
   5. Open text: "What's one thing you'd change?" (qualitative — required
      design-change input per the brief)

**Reporting:** present Likert results as a simple bar chart per question,
summarise the open-text responses thematically (2–3 themes, not a full
transcript — respects both space limits and interviewee anonymity), and
state explicitly which one design change you made as a result, as the
brief requires ("1 design change required").

**Limitation to state in your report:** a 5–8 participant sample is
indicative, not statistically generalisable — frame findings as directional
insight for iteration, not as proof of usability at scale.
