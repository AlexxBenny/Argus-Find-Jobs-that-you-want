"""
Job Scraper — Multi-source fetching

Sources:
  - JobSpy (Indeed, Google, LinkedIn, Glassdoor, ZipRecruiter)
  - RemoteOK (public JSON API — no key needed)
  - Adzuna (pluggable — only if API keys are configured)

Sources are read from the DB filters on each run.
New sources are merged and deduped with JobSpy results.
"""

import traceback
from datetime import datetime, timezone
from db.crud import compute_job_hash


def fetch_jobs(search_term: str, location: str,
               sources: list, results_wanted: int = 15) -> list[dict]:
    """
    Fetch jobs from JobSpy sources (indeed, google, linkedin, etc.).
    Returns normalized list of job dicts.
    """
    # Filter to only JobSpy-supported sources
    jobspy_sources = [s for s in sources if s in _JOBSPY_SOURCES]
    if not jobspy_sources:
        return []

    from jobspy import scrape_jobs

    try:
        df = scrape_jobs(
            site_name=jobspy_sources,
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


# Sources handled by python-jobspy
_JOBSPY_SOURCES = {"indeed", "google", "linkedin", "glassdoor", "zip_recruiter"}

# Sources handled by our custom scrapers
_CUSTOM_SOURCES = {"remoteok", "adzuna"}


def fetch_all_configured_jobs(filters: dict, jobs_per_source: int = 15) -> list[dict]:
    """
    Run all configured search_terms × locations, plus custom sources.
    Dedup and return combined list.

    Args:
        filters: Dict from DB with search_terms, locations, sources, etc.
        jobs_per_source: Max jobs to fetch per search_term × location combo.

    Returns:
        Deduplicated list of normalized job dicts.
    """
    search_terms = filters.get("search_terms", [])
    locations = filters.get("locations", [])
    enabled_sources = filters.get("sources", ["indeed", "google"])

    if not search_terms:
        print("  [SCRAPER] No search terms configured!")
        return []
    if not locations:
        print("  [SCRAPER] No locations configured!")
        return []

    all_jobs = []
    seen_hashes = set()
    total_fetched = 0

    # ── Phase 1: JobSpy sources ──
    jobspy_enabled = [s for s in enabled_sources if s in _JOBSPY_SOURCES]
    if jobspy_enabled:
        for term in search_terms:
            for location in locations:
                print(f"  [SCRAPER] Searching '{term}' in '{location}' "
                      f"via {jobspy_enabled}...")
                jobs = fetch_jobs(term, location, jobspy_enabled, jobs_per_source)
                total_fetched += len(jobs)

                for job in jobs:
                    if job["hash"] not in seen_hashes:
                        seen_hashes.add(job["hash"])
                        all_jobs.append(job)

                print(f"    → {len(jobs)} results "
                      f"({len(all_jobs)} unique total)")

    # ── Phase 2: RemoteOK ──
    if "remoteok" in enabled_sources:
        print(f"  [SCRAPER] Fetching from RemoteOK API...")
        try:
            from scraper.remoteok import fetch_remoteok_jobs
            rok_jobs = fetch_remoteok_jobs(
                search_terms=search_terms,
                results_wanted=jobs_per_source * 2,  # Single API call, get more
                hours_old=72,
            )
            total_fetched += len(rok_jobs)
            for job in rok_jobs:
                if job["hash"] not in seen_hashes:
                    seen_hashes.add(job["hash"])
                    all_jobs.append(job)
            print(f"    → {len(rok_jobs)} results from RemoteOK "
                  f"({len(all_jobs)} unique total)")
        except Exception as e:
            print(f"  [SCRAPER ERROR] RemoteOK failed: {e}")
            traceback.print_exc()

    # ── Phase 3: Adzuna (pluggable — only if keys configured) ──
    if "adzuna" in enabled_sources:
        try:
            from scraper.adzuna import fetch_adzuna_jobs, is_available
            if is_available():
                print(f"  [SCRAPER] Fetching from Adzuna API...")
                adz_jobs = fetch_adzuna_jobs(
                    search_terms=search_terms,
                    locations=locations,
                    results_wanted=jobs_per_source,
                    hours_old=72,
                )
                total_fetched += len(adz_jobs)
                for job in adz_jobs:
                    if job["hash"] not in seen_hashes:
                        seen_hashes.add(job["hash"])
                        all_jobs.append(job)
                print(f"    → {len(adz_jobs)} results from Adzuna "
                      f"({len(all_jobs)} unique total)")
            else:
                print(f"  [SCRAPER] Adzuna enabled but API keys not set — skipping")
        except Exception as e:
            print(f"  [SCRAPER ERROR] Adzuna failed: {e}")
            traceback.print_exc()

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
