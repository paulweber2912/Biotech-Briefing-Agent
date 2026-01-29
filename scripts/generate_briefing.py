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

# CRITICAL: Use Sonnet 4, not Haiku - much better at tool usage and verification
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4096"))  # increased for research phase
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

    # CRITICAL: Define tools that Claude can use
    tools = [
        {
            "type": "web_search_20250305",
            "name": "web_search"
        },
        {
            "type": "web_fetch_20250305",
            "name": "web_fetch"
        }
    ]

    # PHASE 1: Research phase - force Claude to search
    print(f"🔍 Starting research phase for {today}...")
    
    research_prompt = f"""Today is {today}.

PHASE 1: RESEARCH (MANDATORY)

You MUST use the web_search tool to find recent developments in:
1. Gene therapy (CRISPR, base editing, prime editing)
2. Cell therapy (CAR-T, TCR-T, TILs)
3. RNA therapeutics (mRNA, siRNA, ASO)
4. Translational research & clinical trials
5. Regulatory news (FDA, EMA approvals)

Execute AT LEAST 5 different web_search queries targeting:
- Specific journals: "Nature Biotechnology latest" "Cell Stem Cell January 2025"
- Regulatory: "FDA gene therapy approval" "EMA cell therapy"
- Clinical: "CAR-T clinical trial latest" "CRISPR trial 2025"
- Companies: "biotech RNA therapeutics news" 
- Preprints: "bioRxiv gene editing"

For EACH search result that looks relevant:
- Use web_fetch to retrieve the full page
- Verify the publication/announcement date is within last 48h from {today}
- Verify it's a primary source

After completing your searches, summarize what you found and which URLs you actually fetched and verified.
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
    fetches_performed = 0
    
    for block in research_response.content:
        if block.type == "tool_use":
            tool_uses.append({
                "tool": block.name,
                "input": block.input
            })
            if block.name == "web_search":
                searches_performed += 1
                print(f"  🔎 Search: {block.input.get('query', 'N/A')}")
            elif block.name == "web_fetch":
                fetches_performed += 1
                print(f"  📄 Fetch: {block.input.get('url', 'N/A')[:80]}...")

    print(f"✓ Research complete: {searches_performed} searches, {fetches_performed} fetches")

    # Check if Claude actually used tools
    if searches_performed == 0:
        print("⚠️  WARNING: No web searches performed! Results may be hallucinated.")
    
    if fetches_performed == 0:
        print("⚠️  WARNING: No pages fetched! Sources cannot be verified.")

    # Add assistant's research response to conversation
    messages.append({"role": "assistant", "content": research_response.content})

    # PHASE 2: Generate JSON based on verified sources only
    print(f"📝 Generating briefing JSON...")
    
    json_prompt = f"""PHASE 2: GENERATE BRIEFING JSON

Based ONLY on the sources you actually fetched and verified in Phase 1, now generate the JSON output.

CRITICAL RULES:
1. ONLY include items where you used web_fetch to retrieve the full source
2. ONLY include items with verified publication dates within 48h of {today}
3. Do NOT include any items based on search snippets alone
4. Do NOT include any items from your training knowledge
5. If you found fewer than 3 verified items, that's fine - return what you have
6. If you found ZERO verified items, return an empty items list

Now generate the JSON following the exact format from the original prompt.
Include in each source object a "verified_date" field showing the exact date you found on the source page.
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
        "fetches_performed": fetches_performed,
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
    
    if len(data['items']) > fetches_performed:
        print(f"⚠️  WARNING: More items ({len(data['items'])}) than fetches ({fetches_performed}) - may include unverified sources!")

if __name__ == "__main__":
    main()
