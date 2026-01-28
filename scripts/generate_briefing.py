import anthropic
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

# -----------------------------
# Config
# -----------------------------
OUT_PATH = "briefings/latest.json"
MODEL = "claude-sonnet-4-20250514"

PROMPT = """You are a biotech & Cell/Gene Therapy research analyst.
Create a DAILY Biotech/CGT briefing with 3-5 truly important items from the last ~48 hours.

Hard requirements:
- Use web search to find real, recent sources.
- Do NOT invent companies, trials, papers, or URLs.
- Every item must have at least 2 sources (prefer primary sources when possible):
  e.g. PubMed/journal page, ClinicalTrials.gov, FDA/EMA, company press release/IR, reputable news.
- Include "AI in drug discovery / CRISPR / CGT" developments when relevant.
- Keep writing concise, non-hype, technically accurate, with short background context.

Output language: English.

Please respond with a JSON object in this exact format:
{
  "date": "YYYY-MM-DD",
  "items": [
    {
      "id": "1",
      "headline": "...",
      "preview": "...",
      "article": "...",
      "sources": [
        {
          "name": "...",
          "url": "...",
          "type": "paper|regulator|trial_registry|news|company"
        }
      ]
    }
  ]
}

Date (UTC): {today}

Generate today's briefing now."""


def _utc_today_yyyy_mm_dd() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ANTHROPIC_API_KEY env var")

    client = anthropic.Anthropic(api_key=api_key)
    today = _utc_today_yyyy_mm_dd()

    # Make API call with web search enabled
    message = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search"
        }],
        messages=[{
            "role": "user",
            "content": PROMPT.replace("{today}", today)
        }]
    )

    # Extract the text response
    briefing_text = ""
    for block in message.content:
        if block.type == "text":
            briefing_text += block.text

    # Parse JSON from response
    # Claude might wrap it in markdown code blocks, so we clean it
    briefing_text = briefing_text.strip()
    if briefing_text.startswith("```json"):
        briefing_text = briefing_text[7:]  # Remove ```json
    if briefing_text.startswith("```"):
        briefing_text = briefing_text[3:]  # Remove ```
    if briefing_text.endswith("```"):
        briefing_text = briefing_text[:-3]  # Remove trailing ```
    briefing_text = briefing_text.strip()

    try:
        data = json.loads(briefing_text)
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse JSON response: {e}")
        print(f"Raw response:\n{briefing_text}")
        raise

    # Ensure date is set correctly
    data["date"] = today

    # Ensure stable string IDs
    for i, item in enumerate(data.get("items", []), start=1):
        item["id"] = str(i)

    # Save to latest.json
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Also save to archive with date
    archive_path = f"briefings/archive/{today}.json"
    os.makedirs(os.path.dirname(archive_path), exist_ok=True)
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ Briefing successfully generated!")
    print(f"📅 Date: {data['date']}")
    print(f"📊 Items: {len(data.get('items', []))}")
    print(f"💾 Saved to: {OUT_PATH}")
    print(f"📁 Archived to: {archive_path}")


if __name__ == "__main__":
    main()
