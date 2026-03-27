"""
Job Scraper — Multi-source fetching via JobSpy

Uses python-jobspy to aggregate from:
  - Indeed
  - Glassdoor
  - Google Jobs
  - ZipRecruiter
  - LinkedIn (deprioritized, configurable)

Sources are read from the DB filters on each run.
"""

import traceback
from datetime import datetime, timezone
from db.crud import compute_job_hash


def fetch_jobs(search_term: str, location: str,
               sources: list, results_wanted: int = 15) -> list[dict]:
    """
    Fetch jobs from multiple sources using JobSpy.
    Returns normalized list of job dicts.
    """
    from jobspy import scrape_jobs

    try:
        df = scrape_jobs(
            site_name=sources,
            search_term=search_term,
            location=location,
            results_wanted=results_wanted,
            hours_old=72,  # Jobs from last 72 hours (covers hourly gaps)
            country_indeed="india",
        )

        if df is None or df.empty:
            return []

        return _normalize_dataframe(df)

    except Exception as e:
        print(f"  [SCRAPER ERROR] {search_term} @ {location}: {e}")
        traceback.print_exc()
        return []


def fetch_all_configured_jobs(filters: dict, jobs_per_source: int = 15) -> list[dict]:
    """
    Run all configured search_terms × locations, dedup, return combined list.

    Args:
        filters: Dict from DB with search_terms, locations, sources, etc.
        jobs_per_source: Max jobs to fetch per search_term × location combo.

    Returns:
        Deduplicated list of normalized job dicts.
    """
    search_terms = filters.get("search_terms", [])
    locations = filters.get("locations", [])
    sources = filters.get("sources", ["indeed", "google"])

    if not search_terms:
        print("  [SCRAPER] No search terms configured!")
        return []
    if not locations:
        print("  [SCRAPER] No locations configured!")
        return []

    all_jobs = []
    seen_hashes = set()
    total_fetched = 0

    for term in search_terms:
        for location in locations:
            print(f"  [SCRAPER] Searching '{term}' in '{location}' "
                  f"via {sources}...")
            jobs = fetch_jobs(term, location, sources, jobs_per_source)
            total_fetched += len(jobs)

            for job in jobs:
                if job["hash"] not in seen_hashes:
                    seen_hashes.add(job["hash"])
                    all_jobs.append(job)

            print(f"    → {len(jobs)} results "
                  f"({len(all_jobs)} unique total)")

    print(f"  [SCRAPER] Total: {total_fetched} fetched, "
          f"{len(all_jobs)} unique after dedup")
    return all_jobs


def _normalize_dataframe(df) -> list[dict]:
    """Convert JobSpy DataFrame to our standard job dict format."""
    jobs = []

    for _, row in df.iterrows():
        title = str(row.get("title", "")).strip()
        company = str(row.get("company", "")).strip()
        url = str(row.get("job_url", "")).strip()

        if not title:
            continue

        # Compute dedup hash
        job_hash = compute_job_hash(title, company, url)

        # Normalize salary
        salary = None
        min_salary = row.get("min_amount")
        max_salary = row.get("max_amount")
        currency = str(row.get("currency", "")).strip()
        interval = str(row.get("interval", "")).strip()

        if min_salary and max_salary:
            salary = f"{currency} {min_salary:,.0f} - {max_salary:,.0f} {interval}".strip()
        elif min_salary:
            salary = f"{currency} {min_salary:,.0f}+ {interval}".strip()
        elif max_salary:
            salary = f"Up to {currency} {max_salary:,.0f} {interval}".strip()

        # Normalize date
        date_posted = None
        raw_date = row.get("date_posted")
        if raw_date is not None:
            try:
                if hasattr(raw_date, "strftime"):
                    date_posted = raw_date.strftime("%Y-%m-%d")
                else:
                    date_posted = str(raw_date)[:10]
            except Exception:
                date_posted = str(raw_date)

        job = {
            "hash": job_hash,
            "title": title,
            "company": company if company and company != "nan" else None,
            "location": _clean_str(row.get("location")),
            "description": _clean_str(row.get("description")),
            "url": url if url and url != "nan" else None,
            "source": _clean_str(row.get("site")),
            "salary": salary,
            "date_posted": date_posted,
            "job_type": _clean_str(row.get("job_type")),
        }
        jobs.append(job)

    return jobs


def _clean_str(val) -> str | None:
    """Clean a value to string or None."""
    if val is None:
        return None
    s = str(val).strip()
    if s in ("", "nan", "None", "NaN"):
        return None
    return s
