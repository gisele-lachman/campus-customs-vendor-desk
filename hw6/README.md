# Campus Customs Vendor Desk Agent (HW6)

An AI agent that reads one vendor document (HTML quote, PDF, or `.eml` email) for Campus Customs, a fictional Yale merchandise shop. It checks the document against `data/shop_handbook.md` and returns the vendor, SKU, unit price, lead time, minimum order, a `recommend` / `do_not_recommend` decision, and a one-sentence reason. It uses PydanticAI and the model `gpt-6-luna` through Portkey.

## Setup

```bash
cd hw6
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Then open `.env` and replace the placeholder with your own Portkey API key (`PORTKEY_API_KEY=...`). `.env` is git-ignored, so never commit it.

**Google Chrome is required for the HTML cleaning** (the code defense in `sanitize.py`). The default path is the standard macOS one: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`. To use another location, or Chromium, set `CHROME_PATH`:

```bash
export CHROME_PATH=/usr/bin/google-chrome
```

Each HTML document gets its own short-lived headless Chrome with no network access, and Chrome is closed afterward. If Chrome is missing, the agent stops with an error rather than reading uncleaned HTML. PDFs and plain-text emails don't need Chrome.

## Run

```bash
.venv/bin/python agent.py --doc data/practice_docs/p01_bluehen_quote.html
```

It prints the answer as JSON, for example:

```json
{"vendor": "Blue Hen Apparel", "sku": "CC-HD-006", "unit_price": 21.8, "lead_time_days": 14,
 "minimum_order": 240, "decision": "recommend", "reason": "Recommend: all four checks pass (...)."}
```

### Options

| Option | What it does |
|---|---|
| `--doc PATH` | The vendor document to review (required). |
| `--defense LAYERS` | Which defenses to use: `all` (the default), `none`, or a comma list of `code`, `design`, `prompt`. |
| `--no-defense` | Turns off every defense (same as `--defense none`). Used for the attack tests. |
| `--no-cache` | For cost testing only. It adds a random line to the start of every model call so nothing is cached. |

The defense layers are:
- **code:** cuts the document down to what a person sees on screen.
- **design:** a reader with no tools extracts the facts, and the handbook rules are applied in plain Python.
- **prompt:** adds safety rules to the instructions.

See `output/harness.md` for details.

## Files

- `agent.py`: the entry point.
- `tools.py`: the tools, including the trap tools `send_email` and `approve_payment`, which only write to `output/trap_calls.json`.
- `models.py`: the output types.
- `sanitize.py`: the code defense.
- `rules.py`: the handbook rules, applied in code.
- `prompts/`: the instructions for the model.
- `data/`: the course data (handbook, prices, practice and exam documents, answer key) plus `my_attacks/`, the red-team documents.
- `output/`: results and logs from every problem. Start with `hw6_report.html`, which opens offline.
- `AI_prompts.md`: every prompt given to the coding assistant, by problem.

Every run appends to `output/usage_log.json` (tokens and cost) and `output/audit_trail.json` (steps, tool calls, and the answer).
