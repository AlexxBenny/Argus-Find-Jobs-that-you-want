"""
RemoteOK Scraper — Public JSON API

Fetches remote jobs from https://remoteok.com/api
No API key required. Free, open endpoint.

Filters by:
  - Tag relevance (matches search terms to job tags)
  - Recency (within configured hours)
"""

import time
import traceback
from datetime import datetime, timezone, timedelta

import requests
from db.crud import compute_job_hash


_API_URL = "https://remoteok.com/api"

# Map our search terms to RemoteOK tags
_TAG_MAP = {
    "ai engineer": ["ai", "machine-learning", "python"],
    "ai systems architect": ["ai", "architect", "machine-learning"],
    "gen ai engineer": ["ai", "machine-learning", "nlp"],
    "generative ai engineer": ["ai", "machine-learning", "nlp"],
    "ml engineer": ["machine-learning", "python", "data-science"],
    "machine learning engineer": ["machine-learning", "python", "data-science"],
    "llm engineer": ["ai", "nlp", "python", "machine-learning"],
    "data scientist": ["data-science", "machine-learning", "python"],
    "python developer": ["python", "backend"],
}

# Broad AI/ML tags to always include in relevance check
_RELEVANT_TAGS = {
    "ai", "machine-learning", "ml", "deep-learning", "nlp",
    "data-science", "python", "tensorflow", "pytorch",
    "computer-vision", "llm", "generative-ai",
}


def fetch_remoteok_jobs(
    search_terms: list[str],
    results_wanted: int = 15,
    hours_old: int = 72,
) -> list[dict]:
    """
    Fetch jobs from RemoteOK's public API.

    Args:
        search_terms: List of search terms to match against job tags/titles.
        results_wanted: Max jobs to return.
        hours_old: Only include jobs posted within this many hours.

    Returns:
        List of normalized job dicts.
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        resp = requests.get(_API_URL, headers=headers, timeout=30)
        resp.raise_for_status()

        data = resp.json()
        # First element is metadata, rest are jobs
        raw_jobs = data[1:] if len(data) > 1 else []

        if not raw_jobs:
            print("  [REMOTEOK] No jobs returned from API")
            return []

        # Filter by recency
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_old)
        recent_jobs = []
        for job in raw_jobs:
            posted = _parse_epoch(job.get("epoch"))
            if posted and posted >= cutoff:
                recent_jobs.append(job)

        # Filter by relevance to search terms
        relevant_jobs = _filter_relevant(recent_jobs, search_terms)

        # Normalize and limit
        normalized = []
        for job in relevant_jobs[:results_wanted]:
            norm = _normalize_job(job)
            if norm:
                normalized.append(norm)

        print(f"  [REMOTEOK] {len(raw_jobs)} total → {len(recent_jobs)} recent "
              f"→ {len(relevant_jobs)} relevant → {len(normalized)} returned")
        return normalized

    except requests.exceptions.RequestException as e:
        print(f"  [REMOTEOK ERROR] API request failed: {e}")
        return []
    except Exception as e:
        print(f"  [REMOTEOK ERROR] {e}")
        traceback.print_exc()
        return []


def _filter_relevant(jobs: list[dict], search_terms: list[str]) -> list[dict]:
    """Filter jobs by tag/title relevance to search terms."""
    # Build set of target tags from search terms
    target_tags = set()
    for term in search_terms:
        mapped = _TAG_MAP.get(term.lower(), [])
        target_tags.update(mapped)
    target_tags.update(_RELEVANT_TAGS)

    # Also build lowercase search term keywords for title matching
    search_keywords = set()
    for term in search_terms:
        search_keywords.update(term.lower().split())

    relevant = []
    for job in jobs:
        tags = [t.lower().strip() for t in (job.get("tags") or [])]
        title = (job.get("position") or "").lower()

        # Match: any tag overlaps with our targets
        tag_match = bool(set(tags) & target_tags)

        # Match: title contains search keywords
        title_match = any(kw in title for kw in search_keywords)

        if tag_match or title_match:
            relevant.append(job)

    return relevant


def _normalize_job(raw: dict) -> dict | None:
    """Convert RemoteOK job to our standard format."""
    title = (raw.get("position") or "").strip()
    company = (raw.get("company") or "").strip()

    if not title:
        return None

    url = (raw.get("url") or "").strip()
    if url and not url.startswith("http"):
        url = f"https://remoteok.com{url}"

    job_hash = compute_job_hash(title, company, url)

    # Build description from available fields
    description_parts = []
    if raw.get("description"):
        description_parts.append(raw["description"])
    if raw.get("tags"):
        description_parts.append(f"Tags: {', '.join(raw['tags'])}")

    description = "\n".join(description_parts) if description_parts else None

    # Parse salary
    salary = None
    sal_min = raw.get("salary_min")
    sal_max = raw.get("salary_max")
    if sal_min and sal_max:
        salary = f"USD {int(sal_min):,} - {int(sal_max):,} yearly"
    elif sal_min:
        salary = f"USD {int(sal_min):,}+ yearly"

    # Parse date
    date_posted = None
    epoch = raw.get("epoch")
    if epoch:
        try:
            dt = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
            date_posted = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError, OSError):
            pass

    return {
        "hash": job_hash,
        "title": title,
        "company": company if company else None,
        "location": "Remote",
        "description": description,
        "url": url if url else None,
        "source": "remoteok",
        "salary": salary,
        "date_posted": date_posted,
        "job_type": "full-time",
    }


def _parse_epoch(epoch) -> datetime | None:
    """Parse epoch timestamp to datetime."""
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None
