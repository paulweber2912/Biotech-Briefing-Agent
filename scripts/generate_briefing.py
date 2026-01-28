import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from openai import OpenAI

# -----------------------------
# Config
# -----------------------------
OWNER = "paulweber2912"
REPO = "Biotech-Briefing-Agent"
OUT_PATH = "briefings/latest.json"

MODEL = "gpt-4o-mini-search-preview"  # günstig + Web Search-fähig (für Daily Briefing ideal)

TOPIC_PROMPT = """
You are a biotech & Cell/Gene Therapy research analyst.
Create a DAILY Biotech/CGT briefing with 3–5 truly important items from the last ~48 hours.

Hard requirements:
- Use web search to find real, recent sources.
- Do NOT invent companies, trials, papers, or URLs.
- Every item must have at least 2 sources (prefer primary sources when possible):
  e.g. PubMed/journal page, ClinicalTrials.gov, FDA/EMA, company press release/IR, reputable news.
- Include "AI in drug discovery / CRISPR / CGT" developments when relevant.
- Output must match the JSON schema exactly.
- Keep writing concise, non-hype, technically accurate, with short background context.

Output language: English.
"""

JSON_SCHEMA: Dict[str, Any] = {
    "name": "biotech_briefing_schema",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD (UTC)"},
            "items": {
                "type": "array",
                "minItems": 3,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "headline": {"type": "string"},
                        "preview": {"type": "string"},
                        "article": {"type": "string"},
                        "sources": {
                            "type": "array",
                            "minItems": 2,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "name": {"type": "string"},
                                    "url": {"type": "string"},
                                    "type": {
                                        "type": "string",
                                        "enum": ["paper", "regulator", "trial_registry", "news", "company"],
                                    },
                                },
                                "required": ["name", "url", "type"],
                            },
                        },
                    },
                    "required": ["id", "headline", "preview", "article", "sources"],
                },
            },
        },
        "required": ["date", "items"],
    },
}

def _utc_today_yyyy_mm_dd() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY env var")

    client = OpenAI(api_key=api_key)

    today = _utc_today_yyyy_mm_dd()

    response = client.responses.create(
        model=MODEL,
        tools=[
            {
                "type": "web_search_preview",
                # optional: you can hint location, but not required
                # "user_location": {"type": "approximate", "country": "DE", "city": "Berlin"},
            }
        ],
        input=[
            {
                "role": "system",
                "content": TOPIC_PROMPT.strip(),
            },
            {
                "role": "user",
                "content": f"Date (UTC): {today}\n\nGenerate today's briefing now.",
            },
        ],
        # Force strict JSON output matching schema
        response_format={
            "type": "json_schema",
            "json_schema": JSON_SCHEMA,
        },
    )

    # The SDK exposes the final text in output_text for JSON responses as well
    raw_text = response.output_text
    data = json.loads(raw_text)

    # Ensure date is set (and stable)
    data["date"] = today

    # Ensure stable string IDs
    for i, item in enumerate(data.get("items", []), start=1):
        item["id"] = str(i)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Wrote {OUT_PATH} with {len(data.get('items', []))} items.")

if __name__ == "__main__":
    main()
