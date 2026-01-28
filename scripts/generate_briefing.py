import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

from openai import OpenAI

# --- Prompt (English) ---
PROMPT = r"""
You are an expert scientific research assistant specialized in biotechnology, cell and gene therapy, and translational biomedical research.

Generate a DAILY BIOTECH BRIEFING for an advanced life science researcher.

GOAL
Produce a concise, high-quality daily briefing covering the MOST IMPORTANT recent developments in:
- Cell & Gene Therapy
- CRISPR / genome editing
- RNA therapeutics
- Advanced biologics
- Clinical trials (Phase I–III)
- Regulatory and manufacturing developments (FDA, EMA, CMC)
- AI in biotech/pharma (drug discovery, CGT development, manufacturing/CMC, clinical operations), ONLY when materially relevant (no hype)

CONTENT RULES
1) Select ONLY 3–5 genuinely relevant headlines.
2) Do NOT force content if nothing meaningful happened (return fewer items).
3) Prefer scientific and regulatory relevance over hype or stock market noise.
4) Avoid press-release language / marketing tone.
5) Ensure factual accuracy; do not invent studies, endpoints, or statements.
6) ALWAYS include sources with direct URLs; prefer primary sources (papers, regulators, trial registries).

FOR EACH ITEM PROVIDE
- headline: clear, factual
- preview: 1–2 sentences
- article: 2–4 short paragraphs, plus a "Why this matters" paragraph and brief background if needed
- sources: 1–4 sources with {name, url, type}

OUTPUT FORMAT
Return valid JSON strictly matching the schema.
"""

# --- Strict JSON schema for Structured Outputs ---
JSON_SCHEMA: Dict[str, Any] = {
    "name": "biotech_briefing",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "date": {"type": "string"},  # YYYY-MM-DD
            "items": {
                "type": "array",
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
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY env var is missing. Add it as a GitHub Actions secret.")

    # Model chosen in workflow via env var; fallback here
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    today = _utc_today_yyyy_mm_dd()

    client = OpenAI(api_key=api_key)

    resp = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "You must return ONLY valid JSON that matches the provided schema. "
                    "Do not fabricate URLs. If uncertain, omit."
                ),
            },
            {"role": "user", "content": f"Today's date is {today}.\n\n{PROMPT}"},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": JSON_SCHEMA["name"],
                "schema": JSON_SCHEMA["schema"],
                "strict": True,
            }
        },
    )

    # The SDK provides the final text in output_text
    raw = (resp.output_text or "").strip()
    if not raw:
        raise RuntimeError("Model returned empty output_text.")

    data = json.loads(raw)
    # Enforce date if missing/empty
    if not data.get("date"):
        data["date"] = today

    os.makedirs("briefings", exist_ok=True)
    with open("briefings/latest.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Archive copy
    os.makedirs("briefings/archive", exist_ok=True)
    with open(f"briefings/archive/{today}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
