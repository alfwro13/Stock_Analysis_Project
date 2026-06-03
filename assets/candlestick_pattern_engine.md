## Candlestick Pattern Recognition Engine

The candlestick engine is a rule-based structural analyser that evaluates the last three daily OHLC candles on every ticker refresh cycle. It is implemented in `quant_signals.py → get_candlestick_patterns(prev2, prev1, curr)` and runs as part of the `QuantEngine.analyze_ticker()` pipeline. Patterns are **not mutually exclusive at the tier level** — a single day can score multiple confluent signals (e.g., a Bullish Engulfing that is also a Hammer). The only mutually exclusive block is the Tier 3 single-candle section, where Hammer, Shooting Star, and Doji compete in a priority chain.

---

### Architecture Overview

```
analyze_ticker()
    │
    ├─ df.iloc[-3], df.iloc[-2], df.iloc[-1]  ← last 3 daily bars
    │
    └─ get_candlestick_patterns(prev2, prev1, curr)
            │
            ├─ TIER 1: 3-Candle Patterns  (Morning Star, Evening Star, Three White Soldiers)
            ├─ TIER 2: 2-Candle Patterns  (Bullish/Bearish Engulfing, Harami Cross ×2, Piercing Line)
            └─ TIER 3: 1-Candle Patterns  (Hammer, Shooting Star, Doji — mutually exclusive)
                    │
                    returns List[Dict]  →  { name, tooltip, breakdown, score }
                    │
                    ├─ tags[]       → JSON → stock_signals.setup_tags  (displayed in portfolio / watchlist)
                    ├─ breakdown[]  → HTML → stock_signals.educational_notes
                    └─ score        → int  → composite_score  (clamped −100 … +100)
```

Each detected pattern contributes its `score` directly to the ticker's `composite_score`. Multiple patterns firing on the same day accumulate additively before the final clamp.

---

### Candle Anatomy Variables

Before any tier evaluation, the engine pre-computes geometry for the current candle and both lookback candles:

| Variable | Formula |
|---|---|
| `curr_body` | `abs(curr.Close − curr.Open)` |
| `curr_body_safe` | `max(curr_body, 0.001)` — prevents division-by-zero on flat candles |
| `curr_range` | `max(curr.High − curr.Low, 0.001)` |
| `curr_upper_wick` | `curr.High − max(curr.Open, curr.Close)` |
| `curr_lower_wick` | `min(curr.Open, curr.Close) − curr.Low` |
| `prev1_body` | `abs(prev1.Open − prev1.Close)` |
| `prev1_range` | `max(prev1.High − prev1.Low, 0.001)` |
| `prev2_body` | `abs(prev2.Open − prev2.Close)` |
| `prev2_range` | `max(prev2.High − prev2.Low, 0.001)` |

---

### Tier 1 — 3-Candle Patterns

#### 🌅 Morning Star (Bullish Reversal) · Score: +20

The crown jewel of bottom-fishing setups. Three conditions must all hold across three consecutive sessions:

1. **Day 1 (prev2):** Strong bearish candle — `body > 50% of range`. Represents panic selling.
2. **Day 2 (prev1):** Indecision — `body ≤ 30% of range`. Sellers and buyers reach equilibrium.
3. **Day 3 (curr):** Bullish candle whose close exceeds the midpoint of Day 1 (`curr.Close > (prev2.Open + prev2.Close) / 2`). Institutional buyers have recovered more than half the initial dump.

---

#### 🌇 Evening Star (Bearish Reversal) · Score: −20

Symmetric counterpart to the Morning Star — a reliable 3-day topping signal:

1. **Day 1 (prev2):** Strong bullish candle — `body > 50% of range`.
2. **Day 2 (prev1):** Indecision — `body ≤ 30% of range`.
3. **Day 3 (curr):** Bearish candle whose close falls below the midpoint of Day 1 (`curr.Close < (prev2.Open + prev2.Close) / 2`).

---

#### 🪖 Three White Soldiers (Bullish Continuation) · Score: +18

A multi-session confirmation that institutional buyers are systematically accumulating rather than producing a one-day spike. Six conditions must all hold:

1. All three candles are bullish: `close > open` for prev2, prev1, and curr.
2. All three have meaningful bodies: `body > 40% of range` for each (filters out near-doji candles).
3. Successive higher closes: `prev1.Close > prev2.Close` and `curr.Close > prev1.Close`.
4. **D2 opens inside D1's body:** `prev2.Open ≤ prev1.Open ≤ prev2.Close` — an orderly advance, not a gap-up burst.
5. **D3 opens inside D2's body:** `prev1.Open ≤ curr.Open ≤ prev1.Close` — the same restraint on Day 3.

The open-inside-body condition is what distinguishes Three White Soldiers from a simple three-day rally. A gap-up open would indicate short-covering exhaustion rather than controlled accumulation.

---

### Tier 2 — 2-Candle Patterns

#### 🐂 Bullish Engulfing · Score: +15

The current bullish candle fully overtakes the prior bearish candle:
- `prev1` is bearish, `curr` is bullish.
- `curr.Open ≤ prev1.Close` (opens at or below where sellers closed).
- `curr.Close ≥ prev1.Open` (closes at or above where sellers opened — full engulf).

---

#### 🐻 Bearish Engulfing · Score: −15

Symmetric to Bullish Engulfing:
- `prev1` is bullish, `curr` is bearish.
- `curr.Open ≥ prev1.Close` and `curr.Close ≤ prev1.Open`.

---

#### 🌱 Bullish Harami Cross · Score: +8

A Doji that forms **entirely inside** a large bearish candle's body. The significance over a standalone Doji is directional context: sellers had a strong down day, and now they cannot move price at all — exhaustion signal.

Conditions:
- `prev1` is strongly bearish: `prev1.Close < prev1.Open` AND `prev1_body > 50% of prev1_range`.
- `curr` is a Doji: `curr_body ≤ 10% of curr_range`.
- Curr's open and close are both inside prev1's body: `prev1.Close ≤ curr.Open ≤ prev1.Open` and `prev1.Close ≤ curr.Close ≤ prev1.Open`.

**Note:** When this pattern fires, the standalone `⚖️ Doji` is suppressed via the `harami_cross_detected` flag — the same candle cannot generate a second, weaker signal.

---

#### 🕸️ Bearish Harami Cross · Score: −8

Symmetric to Bullish Harami Cross:
- `prev1` is strongly bullish AND `prev1_body > 50% of prev1_range`.
- `curr` is a Doji (`curr_body ≤ 10% of curr_range`).
- Curr's open and close are both inside prev1's body: `prev1.Open ≤ curr.Open ≤ prev1.Close` and `prev1.Open ≤ curr.Close ≤ prev1.Close`.

Also suppresses the standalone Doji when it fires.

---

#### 🗡️ Piercing Line · Score: +10

A partial-recovery bullish reversal — stronger than a Doji in a downtrend, but weaker than a full Bullish Engulfing. Four conditions:

1. `prev1` is strongly bearish: `prev1_body > 50% of prev1_range`.
2. `curr` is bullish: `curr.Close > curr.Open`.
3. `curr` opens at or below `prev1.Close` (opens into weakness — no gap-up).
4. `curr` closes **above the midpoint** of prev1's body: `curr.Close > (prev1.Open + prev1.Close) / 2`.
5. `curr` does **not** fully engulf: `curr.Close < prev1.Open` (otherwise Bullish Engulfing fires instead).

The midpoint guard (condition 4) is critical — without it, any small bullish bounce after a down day would qualify.

---

### Tier 3 — 1-Candle Patterns (Mutually Exclusive)

These three patterns use an `if / elif / elif` chain — only the first matching condition fires.

#### 🔨 Hammer Rejection (Bullish) · Score: +10

- `lower_wick ≥ 2× body_safe` — sellers drove price down but were violently rejected.
- `upper_wick ≤ 20% of range` — minimal upper movement; all the action was to the downside.

Priority: **checked first** in the Tier 3 chain.

---

#### 🌠 Shooting Star (Bearish) · Score: −10

- `upper_wick ≥ 2× body_safe` — buyers pushed price up but were aggressively sold into.
- `lower_wick ≤ 20% of range`.

Priority: **checked second**, only if Hammer did not fire.

---

#### ⚖️ Doji (Indecision) · Score: 0

- `body ≤ 10% of range` — open and close are nearly identical.

Priority: **checked last**, and only if neither a Hammer, Shooting Star, nor Harami Cross (Tier 2) has already been assigned to this candle. The `harami_cross_detected` flag from Tier 2 is what prevents the same candle from generating both a Harami Cross and a standalone Doji tag.

---

### Score Contribution Summary

| Pattern | Emoji | Tier | Score |
|---|---|---|---|
| Morning Star | 🌅 | 3-candle | +20 |
| Evening Star | 🌇 | 3-candle | −20 |
| Three White Soldiers | 🪖 | 3-candle | +18 |
| Bullish Engulfing | 🐂 | 2-candle | +15 |
| Bearish Engulfing | 🐻 | 2-candle | −15 |
| Piercing Line | 🗡️ | 2-candle | +10 |
| Bullish Harami Cross | 🌱 | 2-candle | +8 |
| Bearish Harami Cross | 🕸️ | 2-candle | −8 |
| Hammer Rejection | 🔨 | 1-candle | +10 |
| Shooting Star | 🌠 | 1-candle | −10 |
| Doji | ⚖️ | 1-candle | 0 |

The maximum theoretical contribution from candlestick patterns alone in one session is **+53** (Morning Star +20, Bullish Engulfing +15, Piercing Line +10, Bullish Harami Cross +8 — though most combinations are geometrically impossible on the same three candles). In practice, the most common high-scoring day would be a Morning Star (+20) with a Hammer Rejection (+10) for +30, or Three White Soldiers (+18) with a Bullish Engulfing (+15) for +33.

---

### Where Patterns Surface in the UI

| Surface | Mechanism |
|---|---|
| **Portfolio / Watchlist tables** | Each pattern's `name` and `tooltip` are stored as JSON in `stock_signals.setup_tags` and rendered as `<span class="setup-tag">` chips with `<abbr title="">` hover text |
| **Watchlist filter dropdown** | All 11 patterns are listed in `#candleFilter`; the JS filter checks column 18 (Setups & Tags) for a substring match on the pattern name |
| **Details page — macro chart** | `visuals.py` loops over the last 16 daily bars and annotates each pattern with ▲ (bullish, green), ▼ (bearish, red), or ◆ (neutral, orange) markers with Plotly hover tooltips |
| **Details page — intraday chart** | `page_routes.py` constructs a synthetic "today" candle from intraday data and runs it through the engine; the first matching pattern is passed to `create_intraday_chart()` as a single annotation |
| **Score breakdown panel** | Each pattern's `breakdown` HTML string is appended to the ticker's `educational_notes` and shown in the score explanation section |
| **Glossary** | All patterns have entries under the Candlestick Patterns `<details>` section in `templates/glossary.html` |

---

### Test Coverage

All 11 patterns have dedicated unit tests in `tests/test_candlestick_patterns.py`. Tests use hand-crafted `pd.Series` OHLC candles with known geometry so results are fully deterministic without any network or database access. Coverage includes:

- Positive detection for every pattern
- Score value assertions for all new patterns
- Boundary / near-miss conditions (e.g., open just outside prior body, close just below midpoint)
- Mutual exclusion: Harami Cross suppresses standalone Doji
- False-positive guard: Piercing Line does not fire when the candle is actually a full Engulfing
- Regression: all 7 original patterns still fire correctly after the new additions
