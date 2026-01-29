#!/usr/bin/env python3
"""generate_briefing.py (WITH CUSTOM WEB FETCH)

This version implements custom web fetching using requests + BeautifulSoup
to verify URLs that Claude finds via web_search.
"""

import os
import json
import datetime
from pathlib import Path
from typing import Optional
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from anthropic import Anthropic


def _json_escape_control_chars_inside_strings(s: str) -> str:
    """Escapes literal control characters inside quoted JSON strings."""
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


def fetch_url(url: str, timeout: int = 10) -> Optional[dict]:
    """
    Fetch a URL and extract basic metadata.
    
    Returns dict with:
    - url: the URL
    - title: page title
    - text_preview: first 500 chars of text
    - date_found: any dates found in the HTML
    - success: bool
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; ResearchBot/1.0)',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        
        response = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract title
        title = soup.title.string if soup.title else "No title"
        
        # Extract main text (simple heuristic)
        for script in soup(["script", "style"]):
            script.decompose()
        text = soup.get_text(separator=' ', strip=True)
        
        # Look for dates in meta tags or text
        date_candidates = []
        
        # Check meta tags
        for meta in soup.find_all('meta'):
            if meta.get('name') in ['published', 'article:published_time', 'publishdate']:
                if meta.get('content'):
                    date_candidates.append(meta.get('content'))
            if meta.get('property') in ['article:published_time']:
                if meta.get('content'):
                    date_candidates.append(meta.get('content'))
        
        return {
            'url': url,
            'title': title[:200],
            'text_preview': text[:500],
            'dates_found': date_candidates[:3],  # max 3 date candidates
            'success': True,
            'final_url': response.url  # in case of redirects
        }
        
    except Exception as e:
        return {
            'url': url,
            'success': False,
            'error': str(e)
        }


ROOT = Path(__file__).resolve().parents[1]
BRIEFINGS_DIR = ROOT / "briefings"
ARCHIVE_DIR = BRIEFINGS_DIR / "archive"
LATEST_PATH = BRIEFINGS_DIR / "latest.json"

PROMPT_PATH = Path(os.getenv("PROMPT_PATH", str(ROOT / "prompts" / "daily_prompt.md")))

<<<<<<< HEAD
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4000"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.0"))

=======
# Using Haiku for cost efficiency - with optimized prompting to reduce hallucinations
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "3000"))  # balanced for research + JSON output
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.0"))  # 0.0 for maximum consistency
>>>>>>> da20f2f00e268185b0782c23fb7b427b5aa016a4

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

    tools = [{"type": "web_search_20250305", "name": "web_search"}]

    # PHASE 1: Research with web_search
    print(f"🔍 Starting research phase for {today}...")
    
    research_prompt = f"""Today is {today}.

PHASE 1: WEB SEARCH (MANDATORY)

Use web_search AT LEAST 8-10 times to find recent biotech/gene therapy/cell therapy developments.

Focus on PRIMARY SOURCES:
- Major journals (Nature, Science, Cell families)
- Regulatory bodies (FDA, EMA)
- Clinical trial registries
- Biotech company investor relations

After searching, list the TOP 5-8 most promising URLs you found with visible date evidence.
Format each as:
- URL: [full URL]
- Date evidence: [where you saw the date - in URL path, snippet, etc.]
- Relevance: [why it's interesting]
"""

    messages = [{"role": "user", "content": research_prompt}]

    research_response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        tools=tools,
        messages=messages
    )

    # Log searches
    searches_performed = 0
    for block in research_response.content:
        if block.type == "tool_use" and block.name == "web_search":
            searches_performed += 1
            print(f"  🔎 Search {searches_performed}: {block.input.get('query', 'N/A')}")

    print(f"✓ Research complete: {searches_performed} searches")

    # Extract URLs that Claude found
    messages.append({"role": "assistant", "content": research_response.content})
    
    # Ask Claude to list the URLs it wants to verify
    messages.append({
        "role": "user",
        "content": """Now, list the URLs you want me to fetch and verify.
        
Provide them as a JSON array like:
```json
["url1", "url2", "url3"]
```

Only include URLs where you saw clear date evidence."""
    })
    
    url_list_response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        temperature=0.0,
        messages=messages
    )
    
    # Extract URLs
    urls_to_fetch = []
    for block in url_list_response.content:
        if block.type == "text":
            try:
                # Try to parse JSON from response
                text = block.text.strip()
                if "```json" in text:
                    json_start = text.find("[")
                    json_end = text.rfind("]") + 1
                    if json_start != -1 and json_end > json_start:
                        urls_to_fetch = json.loads(text[json_start:json_end])
            except:
                pass
    
    # PHASE 2: Fetch URLs
    print(f"\n📄 Fetching {len(urls_to_fetch)} URLs...")
    
    fetch_results = []
    for url in urls_to_fetch[:10]:  # Max 10 fetches
        print(f"  → Fetching: {url[:60]}...")
        result = fetch_url(url)
        fetch_results.append(result)
        if result['success']:
            print(f"    ✓ Success: {result['title'][:50]}")
            if result['dates_found']:
                print(f"    📅 Dates: {', '.join(result['dates_found'][:2])}")
        else:
            print(f"    ✗ Failed: {result.get('error', 'Unknown error')}")
    
    # PHASE 3: Generate JSON with fetched data
    print(f"\n📝 Generating briefing...")
    
    messages.append({"role": "assistant", "content": url_list_response.content})
    
    fetch_summary = "FETCHED URL RESULTS:\n\n"
    for i, result in enumerate(fetch_results, 1):
        if result['success']:
            fetch_summary += f"{i}. {result['url']}\n"
            fetch_summary += f"   Title: {result['title']}\n"
            fetch_summary += f"   Dates found: {', '.join(result['dates_found']) if result['dates_found'] else 'None in meta tags'}\n"
            fetch_summary += f"   Preview: {result['text_preview'][:200]}...\n\n"
        else:
            fetch_summary += f"{i}. {result['url']} - FAILED TO FETCH\n\n"
    
    json_prompt = f"""PHASE 3: GENERATE BRIEFING JSON

I have fetched the URLs you requested. Here are the results:

{fetch_summary}

Now create the JSON briefing following the original format.

CRITICAL RULES:
- ONLY include items where fetch was successful
- Verify dates from the fetched content
- Only include if date is within 48h of {today}
- Use the fetched content to write accurate summaries
- If none of the fetched URLs have dates within 48h, return empty items list

Generate the JSON now.
"""
    
    messages.append({"role": "user", "content": json_prompt})
    
    final_response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        messages=messages
    )

    # Extract JSON
    text_parts = []
    for block in final_response.content:
        if block.type == "text":
            text_parts.append(block.text)
    raw = "\n".join(text_parts).strip()

    json_text = extract_json(raw)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        repaired = _json_escape_control_chars_inside_strings(json_text)
        try:
            data = json.loads(repaired)
            json_text = repaired
        except json.JSONDecodeError:
            print(f"❌ Failed to parse JSON response: {e}")
            print("Raw response:\n", raw)
            raise

    data.setdefault("date", today)
    if not isinstance(data.get("items"), list):
        data["items"] = []
    data["items"] = data["items"][:3]

    data["_meta"] = {
        "generated_at": datetime.datetime.now().isoformat(),
        "model": MODEL,
        "searches_performed": searches_performed,
        "urls_fetched": len([r for r in fetch_results if r['success']])
    }

    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    LATEST_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    archive_path = ARCHIVE_DIR / f"{today}.json"
    archive_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\n✅ Wrote {LATEST_PATH} and {archive_path}")
    print(f"   Items generated: {len(data['items'])}")


if __name__ == "__main__":
    main()
