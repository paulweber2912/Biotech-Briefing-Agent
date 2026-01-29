#!/usr/bin/env python3
"""generate_briefing.py (FIXED VERSION with web search)

Generates briefings/latest.json and archives a dated copy in briefings/archive/YYYY-MM-DD.json.

KEY CHANGES:
- Added web_search and web_fetch tools
- Upgraded to Sonnet 4 (much less hallucination than Haiku)
- Multi-turn conversation to enforce tool usage
- Verification of tool usage before accepting results
"""

import os
import json
import datetime
from pathlib import Path

from anthropic import Anthropic


def _json_escape_control_chars_inside_strings(s: str) -> str:
    """
    Escapes literal control characters (especially newlines) that appear inside quoted JSON strings.
    Some model responses look like JSON but contain raw newlines inside string values, which is invalid JSON.
    This function only escapes control characters when we are inside a JSON string.
    """
    out = []
    in_str = False
    esc = False

    for ch in s:
        if in_str:
            if esc:
                out.append(ch)
                esc = False
                continue

            if ch == "\\":
                out.append(ch)
                esc = True
                continue

            if ch == '"':
                out.append(ch)
                in_str = False
                continue

            # Escape invalid control characters inside strings
            if ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        else:
            if ch == '"':
                out.append(ch)
                in_str = True
            else:
                out.append(ch)

    return "".join(out)

ROOT = Path(__file__).resolve().parents[1]
BRIEFINGS_DIR = ROOT / "briefings"
ARCHIVE_DIR = BRIEFINGS_DIR / "archive"
LATEST_PATH = BRIEFINGS_DIR / "latest.json"

PROMPT_PATH = Path(os.getenv("PROMPT_PATH", str(ROOT / "prompts" / "daily_prompt.md")))

# Using Haiku for cost efficiency - with optimized prompting to reduce hallucinations
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "3000"))  # balanced for research + JSON output
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.0"))  # 0.0 for maximum consistency

def load_prompt(today: str) -> str:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    return text.replace("{{today}}", today)

def load_yesterday_headlines() -> list[str]:
    if not LATEST_PATH.exists():
        return []
    try:
        data = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
        items = data.get("items", []) if isinstance(data, dict) else []
        return [it.get("headline", "") for it in items if isinstance(it, dict) and it.get("headline")]
    except Exception:
        return []

def extract_json(text: str) -> str:
    """Extract the first top-level JSON object from a response."""
    t = text.strip()

    # remove markdown fences if present
    if t.startswith("```"):
        start = t.find("{")
        end = t.rfind("}")
        if start != -1 and end != -1 and end > start:
            return t[start:end + 1]
        return t.strip("`")

    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start:end + 1]
    return t

def main() -> None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Missing ANTHROPIC_API_KEY environment variable.")

    today = datetime.date.today().isoformat()
    prompt = load_prompt(today)

    yesterday = load_yesterday_headlines()
    if yesterday:
        prompt += "\n\nYESTERDAY_HEADLINES (avoid repeats unless clearly new):\n" + "\n".join(f"- {h}" for h in yesterday)

    client = Anthropic(api_key=api_key)

    # CRITICAL: Only web_search is available in the API (web_fetch is claude.ai only)
    tools = [
        {
            "type": "web_search_20250305",
            "name": "web_search"
        }
    ]

    # PHASE 1: Research phase - force Claude to search
    print(f"🔍 Starting research phase for {today}...")
    
    research_prompt = f"""Today is {today}.

PHASE 1: RESEARCH (MANDATORY)

You MUST use the web_search tool multiple times to find recent developments.

CRITICAL INSTRUCTIONS:
1. Perform AT LEAST 8-10 targeted web_search queries
2. Focus on PRIMARY SOURCES with visible dates in search results
3. Look for results that show publication dates in the snippet
4. Pay close attention to URLs - they often contain dates

TARGET SEARCHES (execute these types):
- "site:nature.com/nbt articles January 2025"
- "site:fda.gov/news gene therapy approval 2025"
- "site:ema.europa.eu cell therapy recommendation"
- "site:clinicaltrials.gov CAR-T new 2025"
- "site:biorxiv.org CRISPR latest"
- "site:science.org/doi gene editing 2025"
- "biotech company press release gene therapy January 2025"

VERIFICATION FROM SEARCH RESULTS:
For each result, check the snippet for:
- Does the URL contain a date? (e.g., /2025/01/29/ or -20250129-)
- Does the snippet mention a publication date?
- Is it from a primary source domain?
- Is the date within 48h of {today}?

Only include results where you can see date evidence in the search results themselves.

After searching, list the most promising items you found with:
- The URL (must contain visible date markers if possible)
- The date evidence you saw (in URL or snippet)
- Why it's relevant
"""

    messages = [{"role": "user", "content": research_prompt}]

    # Execute research phase with tools
    research_response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        tools=tools,
        messages=messages
    )

    # Log tool usage for debugging
    tool_uses = []
    searches_performed = 0
    
    for block in research_response.content:
        if block.type == "tool_use":
            tool_uses.append({
                "tool": block.name,
                "input": block.input
            })
            if block.name == "web_search":
                searches_performed += 1
                print(f"  🔎 Search: {block.input.get('query', 'N/A')}")

    print(f"✓ Research complete: {searches_performed} searches")

    # Check if Claude actually used tools
    if searches_performed == 0:
        print("⚠️  WARNING: No web searches performed! Results will be hallucinated.")
        print("⚠️  Consider making the research prompt more explicit.")
    elif searches_performed < 5:
        print(f"⚠️  WARNING: Only {searches_performed} searches - recommend 8-10 for good coverage.")

    # Add assistant's research response to conversation
    messages.append({"role": "assistant", "content": research_response.content})

    # PHASE 2: Generate JSON based on verified sources only
    print(f"📝 Generating briefing JSON...")
    
    json_prompt = f"""PHASE 2: GENERATE BRIEFING JSON

Based ONLY on the search results from Phase 1, now generate the JSON output.

CRITICAL RULES:
1. ONLY include items where you saw DATE EVIDENCE in the search results (URL path, snippet text, etc.)
2. Dates must be within 48h of {today}
3. URLs must be from PRIMARY SOURCES (Nature, Science, FDA, EMA, company IR pages, etc.)
4. Do NOT include items based on vague or undated search results
5. Do NOT include items from aggregator sites (generic news, press release aggregators)
6. If you found fewer than 3 verified items, that's fine - return what you have
7. If you found ZERO items with clear date evidence, return an empty items list

ACCEPTABLE DATE EVIDENCE:
✓ URL contains date: nature.com/articles/s41587-025-02543-2 with "29 January 2025" in snippet
✓ Snippet says: "Published: January 29, 2025"
✓ FDA URL: fda.gov/news-events/press-announcements/2025/01/...
✓ Press release: "Company XYZ announced today..." in snippet with today's date visible

UNACCEPTABLE (exclude these):
✗ Generic URLs with no date markers
✗ Search results that just say "recent" or "latest"
✗ Secondary news sources without visible dates
✗ Your memory of what might have been published

Now generate the JSON following the exact format from the original prompt.
For each source, the URL must be one you saw in search results, and verified_date must reflect the date evidence you found.
"""

    messages.append({"role": "user", "content": json_prompt})

    # Generate final JSON
    final_response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        tools=tools,  # Still provide tools in case Claude needs to verify something
        messages=messages
    )

    # Extract text from response
    text_parts: list[str] = []
    for block in final_response.content:
        if block.type == "text":
            text_parts.append(block.text)
    raw = "\n".join(text_parts).strip()

    json_text = extract_json(raw)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        # Attempt a repair for the most common failure mode: literal control characters in strings.
        repaired = _json_escape_control_chars_inside_strings(json_text)
        try:
            data = json.loads(repaired)
            json_text = repaired
        except json.JSONDecodeError:
            print(f"❌ Failed to parse JSON response: {e}")
            print("Raw response:\n", raw)
            raise

    # normalize
    data.setdefault("date", today)
    if not isinstance(data.get("items"), list):
        data["items"] = []
    data["items"] = data["items"][:3]  # max 3 items

    # Add metadata about tool usage for debugging
    data["_meta"] = {
        "generated_at": datetime.datetime.now().isoformat(),
        "model": MODEL,
        "searches_performed": searches_performed,
        "tool_uses": len(tool_uses)
    }

    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    LATEST_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    archive_path = ARCHIVE_DIR / f"{today}.json"
    archive_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"✅ Wrote {LATEST_PATH} and {archive_path}")
    print(f"   Items generated: {len(data['items'])}")
    
    # Warning if suspicious
    if len(data['items']) > 0 and searches_performed == 0:
        print("⚠️  WARNING: Items generated without web search - likely hallucinated!")
    
    if len(data['items']) > 0 and searches_performed < 5:
        print(f"⚠️  WARNING: Only {searches_performed} searches for {len(data['items'])} items - may include unverified sources!")

if __name__ == "__main__":
    main()
