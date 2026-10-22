"""Campus Customs vendor desk agent (PydanticAI + OpenAI through Portkey).

Usage: python agent.py --doc data/practice_docs/p01_bluehen_quote.html [--defense all|none|code,design,prompt]
                       [--no-defense] [--no-cache]

Defense layers (all on by default, each switchable):
  code    clean the document down to what a person sees on screen (sanitize.py)
  design  a reader step with no tools pulls out facts; the decision is made in code from the handbook (rules.py)
  prompt  safety rules in prompts/prompt.md
"""

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

import tools
from models import ExtractedFacts, VendorDecision
from rules import Handbook, decide
from sanitize import visible_document
from tools import OUTPUT_DIR, TOOLS, TRAP_CALLS, append_json, ensure_json_list

HW6_DIR = Path(__file__).resolve().parent
load_dotenv(HW6_DIR / ".env")
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

MODEL_NAME = "gpt-6-luna"
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"
PROMPT_FILE = HW6_DIR / "prompts" / "prompt.md"
READER_FILE = HW6_DIR / "prompts" / "reader.md"
HANDBOOK_FILE = HW6_DIR / "data" / "shop_handbook.md"
PRICES_FILE = HW6_DIR / "data" / "prices.json"
USAGE_LOG = OUTPUT_DIR / "usage_log.json"
AUDIT_TRAIL = OUTPUT_DIR / "audit_trail.json"

# Portkey may report the model as a dated release or as its "-global" deployment. Anything else is a substitution.
SAME_MODEL = re.compile(re.escape(MODEL_NAME) + r"(-\d{4}-\d{2}-\d{2}|-global)?")

LAYERS = ("code", "design", "prompt")
SAFETY = re.compile(r"\n*<!-- SAFETY RULES START -->\n(.*?)<!-- SAFETY RULES END -->\n*", re.S)


def build_model() -> OpenAIResponsesModel:
    # Responses API: Portkey's chat-completions route rejects function tools for this model.
    key = os.environ.get("PORTKEY_API_KEY", "").strip()
    if not key:
        sys.exit("PORTKEY_API_KEY is missing. Put it in HW6/.env (see .env.example).")
    client = AsyncOpenAI(base_url=PORTKEY_BASE_URL, api_key=key, default_headers={"x-portkey-api-key": key},
                         timeout=90, max_retries=2)
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


def instructions_for(base: str, layers: set[str], no_cache: bool):
    """Job instructions, the safety rules if the prompt layer is on, then the full handbook.

    The unchanging prefix comes first so it can be cached between calls."""
    base = SAFETY.sub("\n", base).strip()
    if "prompt" in layers:
        base += "\n\n" + SAFETY.search(PROMPT_FILE.read_text()).group(1).strip()
    static = base.strip() + "\n\n---\n\n" + HANDBOOK_FILE.read_text()
    if not no_cache:
        return static

    def fresh() -> str:
        # Cache test only: a fresh random line at the very start of every model call, so no prefix can be reused.
        return f"Request id: {uuid.uuid4().hex}\n\n{static}"

    return fresh


def build_agent(layers: set[str], no_cache: bool = False) -> Agent:
    settings = OpenAIResponsesModelSettings(openai_reasoning_effort="low", max_tokens=4000)
    if "design" in layers:
        # The reader sees the document but has no tools at all, and returns facts only. It can't email, pay,
        # or decide; send_email and approve_payment aren't reachable from here.
        return Agent(build_model(), output_type=ExtractedFacts, tools=[], model_settings=settings,
                     instructions=instructions_for(READER_FILE.read_text(), layers, no_cache))
    return Agent(build_model(), output_type=VendorDecision, tools=TOOLS, model_settings=settings,
                 instructions=instructions_for(PROMPT_FILE.read_text(), layers, no_cache))


def call_cost(input_tokens: int, cache_read: int, cache_write: int, output_tokens: int) -> float:
    """Dollars for one or more model calls, using data/prices.json."""
    prices = json.loads(PRICES_FILE.read_text())[MODEL_NAME]
    # input_tokens includes the cached reads and writes, so the uncached part is what's left over.
    uncached = input_tokens - cache_read - cache_write
    return round((uncached * prices["input_per_1m"] + cache_read * prices["cached_input_per_1m"]
                  + cache_write * prices["cache_write_per_1m"] + output_tokens * prices["output_per_1m"]) / 1_000_000, 8)


def usage_entry(doc: str, started: str, setup: str, responses: list[ModelResponse]) -> dict:
    """Every token count the API reported, per model call and in total, plus the dollar cost."""
    per_request = []
    totals = {"input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0}
    details_total: dict[str, int] = {}
    for i, r in enumerate(responses, 1):
        u = r.usage
        row = {"call": i, "time": r.timestamp.isoformat(timespec="seconds"), "model": r.model_name,
               "input_tokens": u.input_tokens, "cache_read_tokens": u.cache_read_tokens,
               "cache_write_tokens": u.cache_write_tokens, "output_tokens": u.output_tokens, "details": dict(u.details)}
        row["cost_usd"] = call_cost(row["input_tokens"], row["cache_read_tokens"], row["cache_write_tokens"],
                                    row["output_tokens"])
        per_request.append(row)
        for k in totals:
            totals[k] += row[k]
        for k, v in u.details.items():
            details_total[k] = details_total.get(k, 0) + v
    uncached = totals["input_tokens"] - totals["cache_read_tokens"] - totals["cache_write_tokens"]
    cost = call_cost(totals["input_tokens"], totals["cache_read_tokens"], totals["cache_write_tokens"],
                     totals["output_tokens"])
    return {"document": doc, "time": started, "setup": setup, "requests": len(responses), **totals,
            "uncached_input_tokens": uncached, "details": details_total, "per_request": per_request, "cost_usd": cost}


def audit_entry(doc: str, started: str, setup: str, messages, output: dict | None, error: str | None,
                steps: list[dict]) -> dict:
    """Each tool call with its arguments and a short result, plus the final answer."""
    calls: dict[str, dict] = {}
    for m in messages:
        for part in m.parts:
            if isinstance(part, ToolCallPart) and part.tool_name != "final_result":
                calls[part.tool_call_id] = {"tool": part.tool_name, "args": part.args_as_dict(), "result": None}
            elif isinstance(part, ToolReturnPart) and part.tool_call_id in calls:
                text = str(part.content)
                calls[part.tool_call_id]["result"] = text if len(text) <= 200 else text[:200] + f"... ({len(text)} chars)"
    return {"document": doc, "time": started, "setup": setup, "steps": steps, "tool_calls": list(calls.values()),
            "final_answer": output, "error": error}


def resolve(doc: str) -> Path:
    p = Path(doc)
    return p if p.is_absolute() or p.exists() else HW6_DIR / p


def run(doc: str, layers: set[str] | None = None, no_cache: bool = False) -> VendorDecision:
    layers = set(LAYERS) if layers is None else layers
    for path in (TRAP_CALLS, USAGE_LOG, AUDIT_TRAIL):
        ensure_json_list(path)
    defense = "+".join(l for l in LAYERS if l in layers) or "none"
    setup = f"{'without_cache' if no_cache else 'with_cache'} defense={defense}"
    tools.CURRENT_DOCUMENT = doc  # so a trap call records which document triggered it
    tools.CURRENT_DEFENSE = defense  # ...and which defense setting was on
    tools.SANITIZE = "code" in layers  # read_document returns only on-screen text when the code layer is on
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    agent = build_agent(layers, no_cache)
    messages, output, error, steps = [], None, None, []
    try:
        if "design" in layers:
            # Code reads the document, so the model never needs a tool to see it.
            path = resolve(doc)
            if "code" in layers:
                seen = visible_document(path)
                text = seen.text
                steps.append({"step": "clean document (code)", "chars_kept": len(text),
                              "hidden_text_removed": [r[:300] for r in seen.removed]})
            else:
                text = tools.raw_text(path)
                steps.append({"step": "read document (code, not cleaned)", "chars": len(text)})
            with capture_run_messages() as messages:
                facts = agent.run_sync(f"<document>\n{text}\n</document>").output
            steps.append({"step": "reader (model, no tools)", "facts": facts.model_dump()})
            output = decide(facts, Handbook.load(HANDBOOK_FILE), text)
            steps.append({"step": "decide (code, handbook rules)", "decision": output.decision})
        else:
            with capture_run_messages() as messages:
                output = agent.run_sync(f"Review this vendor document: {doc}").output
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    entry = usage_entry(doc, started, setup, responses)
    entry["defense"] = defense
    append_json(USAGE_LOG, entry)
    append_json(AUDIT_TRAIL, audit_entry(doc, started, setup, messages, output.model_dump() if output else None,
                                         error, steps))
    wrong = {r.model_name for r in responses if not SAME_MODEL.fullmatch(r.model_name or "")}
    if wrong:
        sys.exit(f"Model check failed: a reply came from {sorted(wrong)}, not {MODEL_NAME}.")
    if error:
        sys.exit(f"Run failed: {error}")
    return output


def parse_layers(value: str) -> set[str]:
    value = value.strip().lower()
    if value == "all":
        return set(LAYERS)
    if value == "none":
        return set()
    layers = {v.strip() for v in value.split(",") if v.strip()}
    if not layers <= set(LAYERS):
        raise argparse.ArgumentTypeError(f"--defense takes all, none, or a comma list of {', '.join(LAYERS)}")
    return layers


def main() -> None:
    parser = argparse.ArgumentParser(description="Campus Customs vendor desk agent")
    parser.add_argument("--doc", required=True, help="Path to one vendor document (HTML, PDF, or .eml)")
    parser.add_argument("--defense", type=parse_layers, default=set(LAYERS),
                        help="Defense layers: all (default), none, or a comma list of code,design,prompt")
    parser.add_argument("--no-defense", action="store_true", help="Run with every defense off (same as --defense none)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Cache test only: start every model call with a random line so nothing is reused")
    args = parser.parse_args()
    decision = run(args.doc, set() if args.no_defense else args.defense, args.no_cache)
    print(json.dumps(decision.model_dump(), indent=2))


if __name__ == "__main__":
    main()
