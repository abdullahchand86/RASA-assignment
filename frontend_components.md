# Frontend wiring notes (Task 4)

These are the two concrete UI elements the assignment requires to be wired to
real bot data, plus how they connect to `actions.py`.

## 1. Colour-coded carbon result card

Rasa sends `carbon_tier` ("green" | "amber" | "red" | "unknown") as part of the
custom `json_message` payload from a response, or the frontend reads it off
the `ranked_options` slot via the REST channel. Example React fragment:

```jsx
const TIER_STYLES = {
  green:   { background: "#EAF3DE", color: "#173404", label: "Low impact" },
  amber:   { background: "#FAEEDA", color: "#412402", label: "Moderate impact" },
  red:     { background: "#FCEBEB", color: "#501313", label: "High impact" },
  unknown: { background: "#F1EFE8", color: "#2C2C2A", label: "Impact unverified" },
};

function CarbonCard({ name, ecoCertified, carbonTier, carbonEstimate }) {
  const style = TIER_STYLES[carbonTier] || TIER_STYLES.unknown;
  return (
    <div style={{ background: style.background, color: style.color, padding: 12, borderRadius: 8 }}>
      <strong>{name}</strong>
      {ecoCertified && <span aria-label="eco-certified"> ✓ eco-certified</span>}
      <div role="status">{style.label} — {carbonEstimate ?? "N/A"} kg CO2e</div>
    </div>
  );
}
```

`role="status"` ensures the impact label is announced by screen readers
without relying on colour alone (WCAG 1.4.1 — use of colour).

## 2. Human handover indicator

Triggered when the `handover_active` slot flips to `true` (set in
`ActionHumanHandover`). The frontend listens for the `json_message` flag on
incoming bot messages and toggles a persistent banner:

```jsx
{handoverActive && (
  <div role="status" style={{ background: "#FAECE7", color: "#4A1B0C", padding: 8 }}>
    Connected to a human advisor
  </div>
)}
```

Using `role="status"` (not `role="alert"`) is deliberate — it announces
politely without interrupting a screen-reader user mid-sentence, matching the
non-emergency nature of the handover event.
