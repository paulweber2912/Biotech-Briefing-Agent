#!/usr/bin/env python3
"""
Biotech-Briefing-Agent – FINAL VERSION
=====================================

Pipeline:
- Load RSS feeds from sources.yaml
- Collect RSS items (≤48h)
- Tavily Search for no-RSS / paywalled sites (budget-safe)
- Fetch full article text locally (trafilatura)
- Hard filters (date, dedupe, text length)
- Rule-based scoring
- Select Top 5
- OpenAI formats VERIFIED content into JSON
- Write latest.json + archive/YYYY-MM-DD.json

NO LLM RESEARCH. NO HALLUCINATIONS.
"""

import os
import json
import datetime as dt
from pathlib import Path
from typing import List, Dict

import feedparser
import requests
import trafilatura
import yaml

from openai import OpenAI

# --------------------------------------------------
# Configuration
# --------------------------------------------------

WINDOW_HOURS = 48
MAX_ITEMS = 5
MIN_ARTICLE_CHARS = 800
MAX_ARTICLE_CHARS = 12000

OPENAI_MODEL = "gpt-4o-mini"

TAVILY_MAX_RESULTS = 5
TAVILY_QUERIES = [
    'site:wsj.com biotech OR "gene therapy" OR "cell therapy"',
    'site:handelsblatt.com Biotechnologie OR Gentherapie',
    'site:faz.net Biotechnologie OR Medizin',
]

BASE_DIR = Path(__file__).resolve().parents[1]
BRIEFINGS_DIR = BASE_DIR / "briefings"
PROMPT_PATH = BASE_DIR / "prompts" / "daily_prompt.md"
SOURCES_PATH = BASE_DIR / "sources.yaml"

# --------------------------------------------------
# Helpers
# --------------------------------------------------

def now_utc():
    return dt.datetime.utcnow()

def within_window(published: dt.datetime) -> bool:
    return (now_utc() - published).total_seconds() <= WINDOW_HOURS * 3600

def parse_date(entry):
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return dt.datetime(*entry.published_parsed[:6])
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return dt.datetime(*entry.updated_parsed[:6])
    return None

# --------------------------------------------------
# Fetch article text
# --------------------------------------------------

def fetch_article_text(url: str) -> str | None:
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        text = trafilatura.extract(downloaded)
        if not text or len(text) < MIN_ARTICLE_CHARS:
            return None
        return text[:MAX_ARTICLE_CHARS]
    except Exception:
        return None

# --------------------------------------------------
# RSS ingestion
# --------------------------------------------------

def load_rss_feeds() -> List[str]:
    if not SOURCES_PATH.exists():
        raise FileNotFoundError("sources.yaml not found in repo root")
    with open(SOURCES_PATH, "r") as f:
        data = yaml.safe_load(f)
    return [item["url"] for item in data.get("rss", [])]

def collect_rss_items(feed_urls: List[str]) -> List[Dict]:
    items = []
    for feed_url in feed_urls:
        feed = feedparser.parse(feed_url)
        source_name = feed.feed.get("title", "RSS")
        for e in feed.entries:
            published = parse_date(e)
            if not published or not within_window(published):
                continue
            items.append({
                "title": e.title,
                "url": e.link,
                "published": published,
                "source": source_name
            })
    return items

# --------------------------------------------------
# Tavily Search
# --------------------------------------------------

def tavily_search(query: str) -> List[Dict]:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return []

    resp = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": api_key,
            "query": query,
            "max_results": TAVILY_MAX_RESULTS,
            "search_depth": "basic"
        },
        timeout=30
    )
    resp.raise_for_status()
    data = resp.json()

    results = []
    for r in data.get("results", []):
        try:
            published = dt.datetime.fromisoformat(r.get("published_date"))
        except Exception:
            continue
        if not within_window(published):
            continue
        results.append({
            "title": r.get("title"),
            "url": r.get("url"),
            "published": published,
            "source": r.get("source", "search")
        })
    return results

# --------------------------------------------------
# Scoring
# --------------------------------------------------

KEYWORDS_HIGH = [
    "gene therapy", "cell therapy", "car-t", "aav",
    "crispr", "base editing", "prime editing", "in vivo"
]

KEYWORDS_CLINICAL = [
    "clinical trial", "phase i", "phase ii",
    "first-in-human", "patients"
]

HIGH_VALUE_DOMAINS = [
    "nature.com", "cell.com", "nejm.org",
    "science.org", "ema.europa.eu", "fda.gov"
]

def score_item(item: Dict, text: str) -> int:
    score = 0
    title = item["title"].lower()
    body = text.lower()

    for kw in KEYWORDS_HIGH:
        if kw in title or kw in body:
            score += 3

    for kw in KEYWORDS_CLINICAL:
        if kw in title or kw in body:
            score += 2

    if any(dom in item["url"] for dom in HIGH_VALUE_DOMAINS):
        score += 3

    age_hours = (now_utc() - item["published"]).total_seconds() / 3600
    if age_hours < 12:
        score += 2
    elif age_hours < 24:
        score += 1

    return score

# --------------------------------------------------
# LLM formatting
# --------------------------------------------------

def load_prompt() -> str:
    with open(PROMPT_PATH, "r") as f:
        return f.read()

def format_with_openai(items: List[Dict], today: str) -> Dict:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    prompt = load_prompt()

    payload = {
        "date": today,
        "items": items
    }

    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.2,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload)}
        ]
    )

    return json.loads(resp.choices[0].message.content)

# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    today = now_utc().date().isoformat()

    rss_feeds = load_rss_feeds()
    rss_items = collect_rss_items(rss_feeds)

    search_items = []
    for q in TAVILY_QUERIES:
        search_items.extend(tavily_search(q))

    candidates = rss_items + search_items

    # Deduplicate URLs
    seen = set()
    deduped = []
    for c in candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            deduped.append(c)

    enriched = []
    for c in deduped:
        text = fetch_article_text(c["url"])
        if not text:
            continue
        score = score_item(c, text)
        enriched.append({
            "id": None,
            "headline": c["title"],
            "text": text,
            "score": score,
            "source": {
                "name": c["source"],
                "url": c["url"],
                "verified_date": c["published"].date().isoformat()
            }
        })

    top = sorted(enriched, key=lambda x: x["score"], reverse=True)[:MAX_ITEMS]

    llm_items = []
    for i, t in enumerate(top, 1):
        llm_items.append({
            "id": str(i),
            "headline": t["headline"],
            "text": t["text"],
            "source": t["source"]
        })

    if llm_items:
        output = format_with_openai(llm_items, today)
    else:
        output = {"date": today, "items": []}

    BRIEFINGS_DIR.mkdir(exist_ok=True)
    archive_dir = BRIEFINGS_DIR / "archive"
    archive_dir.mkdir(exist_ok=True)

    with open(BRIEFINGS_DIR / "latest.json", "w") as f:
        json.dump(output, f, indent=2)

    with open(archive_dir / f"{today}.json", "w") as f:
        json.dump(output, f, indent=2)


if __name__ == "__main__":
    main()
