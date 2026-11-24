# Contract: quality bands of the Quality view

A band says whether a code metric is within its normal range. Computed on the server (`logic.py`), rendered by the page.

## Thresholds (settings)

Stored with the other module-card options (`cockpit-settings.json`, key `quality_thresholds`), returned by `/api/cardsettings` as `quality_thresholds` and editable in Settings (Module cards). One entry per metric:

`{"<metric>": {"direction": "low" | "high", "ok": number, "warn": number}}`

- `low` (lower is better): value <= `ok` is ok, value <= `warn` is warn, above is bad.
- `high` (higher is better): value >= `ok` is ok, value >= `warn` is warn, below is bad.

Defaults (proposed by the assistant, owner asked for the bars on 2026-11-24 and did not object to them; editable):

| metric key | measure | direction | ok | warn |
|---|---|---|---|---|
| `complexity` | worst block | low | 10 | 20 |
| `maintainability` | mean index | high | 20 | 10 |
| `lint` | findings per 1,000 lines | low | 10 | 30 |
| `dead` | dead-code candidates per 1,000 code lines | low | 2 | 5 |
| `cycles` | files in import cycles | low | 0 | 0 |
| `duplication` | percent of code lines | low | 3 | 5 |
| `docstrings` | percent documented | high | 80 | 50 |
| `coverage` | percent of statements covered | high | 80 | 60 |
| `tests` | test-to-code ratio | high | 0.5 | 0.2 |

Size and churn have no band.

## Bands in the code payload

Each module of `/api/code` carries `bands`: `{"<metric key>": {"band": "ok" | "warn" | "bad" | "none", "word": "normal" | "borderline" | "out of range" | "no figure"}}` for every metric key above. `none` (no figure, or a module with nothing analysed) is shown as a grey bar. Colour is never the only signal: the word is shown with the bar.
