#!/usr/bin/env python3
"""generate_briefing.py

Generates briefings/latest.json and archives a dated copy in briefings/archive/YYYY-MM-DD.json.

This version reads the prompt from prompts/daily_prompt.md so the prompt can be edited
without touching code. The prompt uses {{today}} as the placeholder.
It also passes yesterday's headlines (from briefings/latest.json) to reduce repeats.
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

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "2200"))  # keep cost under control
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))

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

    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        messages=[{"role": "user", "content": prompt}],
    )

    # concatenate all returned text blocks
    text_parts: list[str] = []
    for block in getattr(message, "content", []):
        if getattr(block, "type", None) == "text":
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
            json_text = repaired  # keep repaired text for any downstream logging/debug
        except json.JSONDecodeError:
            print(f"❌ Failed to parse JSON response: {e}")
            print("Raw response:\n", raw)
            raise

    # normalize
    data.setdefault("date", today)
    if not isinstance(data.get("items"), list):
        data["items"] = []
    data["items"] = data["items"][:3]  # max 3 items

    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    LATEST_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    archive_path = ARCHIVE_DIR / f"{today}.json"
    archive_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"✅ Wrote {LATEST_PATH} and {archive_path}")

if __name__ == "__main__":
    main()