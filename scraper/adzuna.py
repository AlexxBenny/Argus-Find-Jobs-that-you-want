"""
Adzuna Scraper — Pluggable, key-gated

Uses Adzuna's free API (250 requests/month on free tier).
Only runs if:
  1. "adzuna" is in enabled sources
  2. ADZUNA_APP_ID and ADZUNA_APP_KEY are set

Graceful no-op otherwise — never breaks the system.
"""

import traceback
from datetime import datetime, timezone, timedelta

import requests
from config import ADZUNA_APP_ID, ADZUNA_APP_KEY
from db.crud import compute_job_hash


_BASE_URL = "https://api.adzuna.com/v1/api/jobs"

# Map our locations to Adzuna country codes + what param
_LOCATION_MAP = {
    "india": {"country": "in", "where": ""},
    "kerala": {"country": "in", "where": "Kerala"},
    "bangalore": {"country": "in", "where": "Bangalore"},
    "remote": {"country": "in", "where": "remote"},
}


def is_available() -> bool:
    """Check if Adzuna credentials are configured."""
    return bool(ADZUNA_APP_ID and ADZUNA_APP_KEY)


def fetch_adzuna_jobs(
    search_terms: list[str],
    locations: list[str],
    results_wanted: int = 15,
    hours_old: int = 72,
) -> list[dict]:
    """
    Fetch jobs from Adzuna API.

    Only runs if credentials are present. Returns empty list if not configured.

    Args:
        search_terms: Job title keywords to search.
        locations: Locations to search in.
        results_wanted: Max results per query.
        hours_old: Only jobs posted within this many hours.

    Returns:
        List of normalized job dicts.
    """
    if not is_available():
        print("  [ADZUNA] Skipped — API keys not configured")
        return []

    all_jobs = []
    seen_hashes = set()

    for term in search_terms:
        for loc in locations:
            loc_config = _LOCATION_MAP.get(loc.lower())
            if not loc_config:
                # Default to India-wide search
                loc_config = {"country": "in", "where": loc}

            jobs = _fetch_single(
                term, loc_config["country"], loc_config["where"],
                results_wanted, hours_old,
            )

            for job in jobs:
                if job["hash"] not in seen_hashes:
                    seen_hashes.add(job["hash"])
                    all_jobs.append(job)

    print(f"  [ADZUNA] Total: {len(all_jobs)} unique jobs fetched")
    return all_jobs


def _fetch_single(
    search_term: str,
    country: str,
    where: str,
    results_wanted: int,
    hours_old: int,
) -> list[dict]:
    """Fetch a single search from Adzuna API."""
    try:
        url = f"{_BASE_URL}/{country}/search/1"

        params = {
            "app_id": ADZUNA_APP_ID,
            "app_key": ADZUNA_APP_KEY,
            "what": search_term,
            "results_per_page": min(results_wanted, 50),
            "max_days_old": max(1, hours_old // 24),
            "sort_by": "date",
            "content-type": "application/json",
        }

        if where:
            params["where"] = where

        resp = requests.get(url, params=params, timeout=30)

        if resp.status_code == 401:
            print(f"  [ADZUNA] Auth failed — check API keys")
            return []
        if resp.status_code == 429:
            print(f"  [ADZUNA] Rate limited — quota exhausted")
            return []

        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        return [_normalize_job(r) for r in results if _normalize_job(r)]

    except requests.exceptions.RequestException as e:
        print(f"  [ADZUNA ERROR] {search_term} @ {where or country}: {e}")
        return []
    except Exception as e:
        print(f"  [ADZUNA ERROR] {e}")
        traceback.print_exc()
        return []


def _normalize_job(raw: dict) -> dict | None:
    """Convert Adzuna job to our standard format."""
    title = (raw.get("title") or "").strip()
    company = (raw.get("company", {}).get("display_name") or "").strip()

    if not title:
        return None

    url = (raw.get("redirect_url") or "").strip()
    job_hash = compute_job_hash(title, company, url)

    # Salary
    salary = None
    sal_min = raw.get("salary_min")
    sal_max = raw.get("salary_max")
    if sal_min and sal_max:
        salary = f"INR {int(sal_min):,} - {int(sal_max):,} yearly"
    elif sal_min:
        salary = f"INR {int(sal_min):,}+ yearly"

    # Date
    date_posted = None
    created = raw.get("created")
    if created:
        try:
            date_posted = created[:10]  # "2026-03-27T..."
        except (TypeError, IndexError):
            pass

    # Location
    location = (raw.get("location", {}).get("display_name") or "").strip()

    return {
        "hash": job_hash,
        "title": title,
        "company": company if company else None,
        "location": location if location else None,
        "description": (raw.get("description") or "").strip() or None,
        "url": url if url else None,
        "source": "adzuna",
        "salary": salary,
        "date_posted": date_posted,
        "job_type": raw.get("contract_type") or None,
    }
