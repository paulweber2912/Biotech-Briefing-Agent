#!/usr/bin/env python3
"""
Biotech-Briefing-Agent – v3
==========================
Fixes on top of v2 (debug + caps):
- Filters/penalizes low-value journal notices (Correction/Erratum/Retraction/etc.)
- "Paper guarantee" now means: include at least 1 TOPICAL paper/preprint if available.
  If only non-topical papers/corrections exist, we do NOT force them into Top 5.

NO LLM RESEARCH. NO HALLUCINATIONS.
"""

import os
import json
import datetime as dt
from pathlib import Path
from typing import List, Dict, Tuple, Optional

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
MAX_PER_SOURCE = 2
REQUIRE_AT_LEAST_ONE_PAPER = True  # only if a topical one exists

MIN_ARTICLE_CHARS = 800
MAX_ARTICLE_CHARS = 12000

OPENAI_MODEL = "gpt-4o-mini"

# Keep Tavily very small to stay under 1000 credits/month
TAVILY_MAX_RESULTS = 5
TAVILY_QUERIES = [
    'site:wsj.com (biotech OR "gene therapy" OR "cell therapy" OR CRISPR OR "clinical trial")',
    'site:handelsblatt.com (Biotechnologie OR Gentherapie OR Zelltherapie OR klinische Studie)',
    'site:faz.net (Biotechnologie OR Medizin OR Gentherapie OR Zelltherapie)',
]

BASE_DIR = Path(__file__).resolve().parents[1]
BRIEFINGS_DIR = BASE_DIR / "briefings"
PROMPT_PATH = BASE_DIR / "prompts" / "daily_prompt.md"
SOURCES_PATH = BASE_DIR / "sources.yaml"

DEBUG_DIR = BRIEFINGS_DIR / "debug"
DEBUG_ARCHIVE_DIR = DEBUG_DIR / "archive"

# --------------------------------------------------
# Helpers
# --------------------------------------------------

def now_utc():
    return dt.datetime.utcnow()

def within_window(published: dt.datetime) -> bool:
    return (now_utc() - published).total_seconds() <= WINDOW_HOURS * 3600

def hours_old(published: dt.datetime) -> float:
    return (now_utc() - published).total_seconds() / 3600

def parse_date(entry):
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return dt.datetime(*entry.published_parsed[:6])
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return dt.datetime(*entry.updated_parsed[:6])
    return None

LOW_VALUE_JOURNAL_NOTICES = [
    "correction", "erratum", "corrigendum", "retraction",
    "expression of concern", "publisher correction"
]

def is_low_value_notice(title: str) -> bool:
    t = (title or "").strip().lower()
    return any(k in t for k in LOW_VALUE_JOURNAL_NOTICES)

def detect_kind(url: str, source_name: str) -> str:
    """Return: paper | preprint | regulator | trial_registry | company | news"""
    u = (url or "").lower()

    if "biorxiv.org" in u or "medrxiv" in u:
        return "preprint"
    if any(d in u for d in ["cell.com", "nature.com", "science.org", "nejm.org", "rupress.org", "plos.org", "tctjournal.org"]):
        return "paper"
    if "fda.gov" in u or "ema.europa.eu" in u:
        return "regulator"
    if "clinicaltrials.gov" in u:
        return "trial_registry"
    if any(x in u for x in ["ir.", "/investors", "/press", "/newsroom"]):
        return "company"
    return "news"

def is_paper_like(kind: str) -> bool:
    return kind in {"paper", "preprint"}

# --------------------------------------------------
# Fetch article text
# --------------------------------------------------

def fetch_article_text(url: str) -> Tuple[Optional[str], Optional[str]]:
    """Returns (text, drop_reason)."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None, "fetch_failed"
        text = trafilatura.extract(downloaded)
        if not text:
            return None, "extract_failed"
        if len(text) < MIN_ARTICLE_CHARS:
            return None, "text_too_short_or_teaser"
        return text[:MAX_ARTICLE_CHARS], None
    except Exception as e:
        return None, f"exception:{type(e).__name__}"

# --------------------------------------------------
# RSS ingestion
# --------------------------------------------------

def load_rss_feeds() -> List[str]:
    if not SOURCES_PATH.exists():
        raise FileNotFoundError("sources.yaml not found in repo root")
    with open(SOURCES_PATH, "r") as f:
        data = yaml.safe_load(f) or {}
    return [item.get("url") for item in (data.get("rss") or []) if item.get("url")]

def collect_rss_items(feed_urls: List[str], debug: Dict) -> List[Dict]:
    items = []
    per_feed = []
    for feed_url in feed_urls:
        feed = feedparser.parse(feed_url)
        source_name = feed.feed.get("title", "RSS")
        count_total = 0
        count_in_window = 0

        for e in feed.entries:
            count_total += 1
            published = parse_date(e)
            if not published or not within_window(published):
                continue
            count_in_window += 1
            items.append({
                "title": e.title,
                "url": e.link,
                "published": published,
                "source": source_name,
                "source_type": "rss"
            })
        per_feed.append({
            "feed_url": feed_url,
            "feed_title": source_name,
            "items_total": count_total,
            "items_in_window": count_in_window
        })

    debug["rss_per_feed"] = per_feed
    debug["rss_items_collected"] = len(items)
    return items

# --------------------------------------------------
# Tavily Search
# --------------------------------------------------

def tavily_search(query: str) -> Tuple[List[Dict], Optional[str]]:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return [], "missing_TAVILY_API_KEY"

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
        published_raw = r.get("published_date")
        if not published_raw:
            continue
        try:
            published = dt.datetime.fromisoformat(published_raw)
        except Exception:
            continue
        if not within_window(published):
            continue
        results.append({
            "title": r.get("title"),
            "url": r.get("url"),
            "published": published,
            "source": r.get("source", "search"),
            "source_type": "search"
        })
    return results, None

# --------------------------------------------------
# Scoring + topicality
# --------------------------------------------------

KEYWORDS_HIGH = [
    "gene therapy", "cell therapy", "car-t", "aav",
    "crispr", "base editing", "prime editing", "in vivo",
    "rna", "mrna", "lnp", "oligonucleotide", "si rna", "as o"
]

KEYWORDS_CLINICAL = [
    "clinical trial", "phase i", "phase ii", "phase iii",
    "first-in-human", "patients", "clinical hold", "fda", "ema"
]

HIGH_VALUE_DOMAINS = [
    "nature.com", "cell.com", "nejm.org", "science.org",
    "ema.europa.eu", "fda.gov", "biorxiv.org", "medrxiv.org"
]

NEGATIVE_KEYWORDS = [
    "price target", "earnings", "stock", "shares", "downgrade", "upgrade"
]

def is_topical(title: str, text: str) -> bool:
    t = (title or "").lower()
    b = (text or "").lower()
    return any(k in t or k in b for k in KEYWORDS_HIGH + KEYWORDS_CLINICAL)

def score_item(item: Dict, text: str) -> int:
    score = 0
    title = (item.get("title") or "").lower()
    body = (text or "").lower()
    url = (item.get("url") or "").lower()

    for kw in KEYWORDS_HIGH:
        if kw in title or kw in body:
            score += 3

    for kw in KEYWORDS_CLINICAL:
        if kw in title or kw in body:
            score += 2

    if any(dom in url for dom in HIGH_VALUE_DOMAINS):
        score += 3

    # Freshness
    age = hours_old(item["published"])
    if age < 12:
        score += 2
    elif age < 24:
        score += 1

    # Penalize finance-y articles
    if any(kw in title for kw in NEGATIVE_KEYWORDS):
        score -= 3

    # HARD penalty for low-value journal notices (Correction/Erratum/etc.)
    if is_low_value_notice(item.get("title", "")):
        score -= 20

    return score

# --------------------------------------------------
# Selection with source cap + topical paper guarantee
# --------------------------------------------------

def select_top_items(enriched: List[Dict], debug: Dict) -> List[Dict]:
    enriched_sorted = sorted(enriched, key=lambda x: x["score"], reverse=True)

    selected: List[Dict] = []
    per_source: Dict[str, int] = {}

    # First pass: pick best items obeying source caps
    for item in enriched_sorted:
        src = item["source"]["name"]
        if per_source.get(src, 0) >= MAX_PER_SOURCE:
            continue
        selected.append(item)
        per_source[src] = per_source.get(src, 0) + 1
        if len(selected) >= MAX_ITEMS:
            break

    # Paper guarantee (ONLY if a topical, non-correction paper/preprint exists)
    if REQUIRE_AT_LEAST_ONE_PAPER:
        has_topical_paper = any(
            is_paper_like(it["kind"]) and it["topical"] and not it["low_value_notice"]
            for it in selected
        )

        if not has_topical_paper:
            best_paper = None
            for it in enriched_sorted:
                if not is_paper_like(it["kind"]):
                    continue
                if not it["topical"]:
                    continue
                if it["low_value_notice"]:
                    continue
                src = it["source"]["name"]
                if per_source.get(src, 0) >= MAX_PER_SOURCE:
                    continue
                best_paper = it
                break

            if best_paper:
                # Replace lowest scoring non-paper; if none, replace lowest overall
                non_papers = [it for it in selected if not is_paper_like(it["kind"])]
                to_remove_pool = non_papers if non_papers else selected[:]
                to_remove = sorted(to_remove_pool, key=lambda x: x["score"])[0]

                selected.remove(to_remove)
                per_source[to_remove["source"]["name"]] -= 1

                selected.append(best_paper)
                per_source[best_paper["source"]["name"]] = per_source.get(best_paper["source"]["name"], 0) + 1
                selected = sorted(selected, key=lambda x: x["score"], reverse=True)

                debug["paper_guarantee_applied"] = True
                debug["paper_inserted_url"] = best_paper["source"]["url"]
                debug["paper_replaced_url"] = to_remove["source"]["url"]

    debug["selection_source_counts"] = per_source
    debug["selected_count"] = len(selected)
    return selected

# --------------------------------------------------
# LLM formatting
# --------------------------------------------------

def load_prompt() -> str:
    with open(PROMPT_PATH, "r") as f:
        return f.read()

def format_with_openai(items: List[Dict], today: str) -> Dict:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY environment variable")
    client = OpenAI(api_key=api_key)
    prompt = load_prompt()

    payload = {"date": today, "items": items}

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

    debug: Dict = {
        "date": today,
        "window_hours": WINDOW_HOURS,
        "max_items": MAX_ITEMS,
        "max_per_source": MAX_PER_SOURCE,
        "require_at_least_one_paper": REQUIRE_AT_LEAST_ONE_PAPER,
        "rss_items_collected": 0,
        "search_items_collected": 0,
        "deduped_candidates": 0,
        "fetched_ok": 0,
        "fetched_dropped": 0,
        "drop_reasons": {},
        "paper_guarantee_applied": False
    }

    # 1) RSS
    rss_feeds = load_rss_feeds()
    debug["rss_feeds_count"] = len(rss_feeds)
    rss_items = collect_rss_items(rss_feeds, debug)

    # 2) Tavily search
    search_items: List[Dict] = []
    search_errors: List[str] = []
    for q in TAVILY_QUERIES:
        try:
            items, err = tavily_search(q)
            search_items.extend(items)
            if err:
                search_errors.append(err)
        except Exception as e:
            search_errors.append(f"exception:{type(e).__name__}")
    debug["search_items_collected"] = len(search_items)
    debug["search_errors"] = sorted(list(set(search_errors)))

    candidates = rss_items + search_items

    # 3) Deduplicate URLs
    seen = set()
    deduped = []
    for c in candidates:
        url = c.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(c)
    debug["deduped_candidates"] = len(deduped)

    # 4) Fetch + score
    enriched: List[Dict] = []
    scored_preview: List[Dict] = []

    for c in deduped:
        text, drop_reason = fetch_article_text(c["url"])
        if not text:
            debug["fetched_dropped"] += 1
            debug["drop_reasons"][drop_reason] = debug["drop_reasons"].get(drop_reason, 0) + 1
            continue

        debug["fetched_ok"] += 1
        kind = detect_kind(c["url"], c["source"])
        topical = is_topical(c.get("title", ""), text)
        low_value_notice = is_low_value_notice(c.get("title", ""))
        score = score_item(c, text)

        item = {
            "headline": c["title"],
            "text": text,
            "score": score,
            "kind": kind,
            "topical": topical,
            "low_value_notice": low_value_notice,
            "age_hours": round(hours_old(c["published"]), 2),
            "source": {
                "name": c["source"],
                "url": c["url"],
                "verified_date": c["published"].date().isoformat()
            }
        }
        enriched.append(item)

        scored_preview.append({
            "headline": c["title"],
            "url": c["url"],
            "source": c["source"],
            "kind": kind,
            "topical": topical,
            "low_value_notice": low_value_notice,
            "age_hours": round(hours_old(c["published"]), 2),
            "score": score
        })

    # 5) Select Top 5 with caps + topical paper guarantee
    selected = select_top_items(enriched, debug)

    # 6) LLM input items
    llm_items = []
    for i, t in enumerate(selected, 1):
        llm_items.append({
            "id": str(i),
            "headline": t["headline"],
            "text": t["text"],
            "source": t["source"],
            "kind": t["kind"]
        })

    # 7) Format JSON with OpenAI (or empty)
    if llm_items:
        output = format_with_openai(llm_items, today)
    else:
        output = {"date": today, "items": []}

    # 8) Write outputs
    BRIEFINGS_DIR.mkdir(exist_ok=True)
    (BRIEFINGS_DIR / "archive").mkdir(exist_ok=True)

    with open(BRIEFINGS_DIR / "latest.json", "w") as f:
        json.dump(output, f, indent=2)

    with open(BRIEFINGS_DIR / "archive" / f"{today}.json", "w") as f:
        json.dump(output, f, indent=2)

    # 9) Write debug outputs
    DEBUG_DIR.mkdir(exist_ok=True)
    DEBUG_ARCHIVE_DIR.mkdir(exist_ok=True)

    scored_preview_sorted = sorted(scored_preview, key=lambda x: x["score"], reverse=True)[:50]
    debug["scored_preview_top50"] = scored_preview_sorted
    debug["selected_items"] = [
        {
            "headline": x["headline"],
            "url": x["source"]["url"],
            "source": x["source"]["name"],
            "kind": x["kind"],
            "topical": x["topical"],
            "low_value_notice": x["low_value_notice"],
            "age_hours": x["age_hours"],
            "score": x["score"],
        }
        for x in selected
    ]

    with open(DEBUG_DIR / "latest_debug.json", "w") as f:
        json.dump(debug, f, indent=2)

    with open(DEBUG_ARCHIVE_DIR / f"{today}_debug.json", "w") as f:
        json.dump(debug, f, indent=2)


if __name__ == "__main__":
    main()
