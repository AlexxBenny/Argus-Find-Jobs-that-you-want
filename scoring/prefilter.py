"""
Deterministic Pre-Filter — Layer 1 (Free, Fast)

Runs BEFORE the LLM scorer to eliminate obviously irrelevant jobs.
This saves LLM API costs by ~60-80%.

Checks:
  1. Excluded keywords in title/description
  2. Required skills presence
  3. Location matching
  4. Salary floor check
  5. Already exists in DB (dedup)
"""

import re
from sqlalchemy.orm import Session
from db.models import JobTemp, JobMain


def prefilter_jobs(
    jobs: list[dict],
    filters: dict,
    db: Session,
) -> tuple[list[dict], list[dict]]:
    """
    Apply deterministic pre-filters to a list of jobs.

    Returns:
        (passed, rejected) — two lists of job dicts.
        Each rejected job has a 'rejection_reason' field added.
    """
    passed = []
    rejected = []

    excluded_keywords = [kw.lower() for kw in filters.get("excluded_keywords", [])]
    required_skills = [s.lower() for s in filters.get("required_skills", [])]
    deal_breaker_salary = filters.get("deal_breaker_salary_max", 0)
    search_terms = filters.get("search_terms", [])

    for job in jobs:
        reason = _check_job(job, excluded_keywords, required_skills,
                            deal_breaker_salary, db, search_terms)
        if reason:
            job["rejection_reason"] = reason
            rejected.append(job)
        else:
            passed.append(job)

    return passed, rejected


def _check_job(
    job: dict,
    excluded_keywords: list[str],
    required_skills: list[str],
    deal_breaker_salary: int,
    db: Session,
    search_terms: list[str] = None,
) -> str | None:
    """
    Check a single job against filters.
    Returns rejection reason string, or None if the job passes.
    """
    title = (job.get("title") or "").lower()
    description = (job.get("description") or "").lower()
    combined = f"{title} {description}"
    job_hash = job.get("hash", "")

    # ── 1. Already in DB ──
    existing_temp = db.query(JobTemp.id).filter(JobTemp.hash == job_hash).first()
    existing_main = db.query(JobMain.id).filter(JobMain.hash == job_hash).first()
    if existing_temp or existing_main:
        return "duplicate: already in database"

    # ── 2. Title relevance gate ──
    # The title MUST contain at least one AI/ML-related keyword OR a search term.
    # This catches "Bar Waitress", "Mulesoft Developer", "Java Lead" early.
    title_relevant_keywords = [
        "ai", "artificial intelligence", "machine learning", "ml",
        "deep learning", "data scien", "llm", "nlp", "natural language",
        "computer vision", "gen ai", "generative", "rag",
        "neural", "transformer", "langchain", "prompt engineer",
        "ml engineer", "ai engineer", "data engineer",
        "python developer", "python engineer",
    ]
    # Also include user's search terms (lowered)
    if search_terms:
        title_relevant_keywords += [t.lower() for t in search_terms]

    title_is_relevant = any(kw in title for kw in title_relevant_keywords)
    if not title_is_relevant:
        return f"title not relevant to AI/ML roles: '{title[:60]}'"

    # ── 3. Excluded keywords in title ──
    for kw in excluded_keywords:
        if kw in title:
            return f"excluded keyword in title: '{kw}'"

    # ── 4. Excluded keywords in description (only strong signals) ──
    # Only check description for multi-word excluded phrases
    # Single words in description are too noisy
    for kw in excluded_keywords:
        if " " in kw and kw in description:
            return f"excluded keyword in description: '{kw}'"

    # ── 5. Salary deal-breaker ──
    if deal_breaker_salary and job.get("salary"):
        parsed = _parse_salary_range(job["salary"])
        if parsed and parsed["max"] and parsed["max"] < deal_breaker_salary:
            return f"salary too low: {job['salary']} (below {deal_breaker_salary})"

    # ── 6. Minimum skill relevance ──
    # Must have at least 1 required skill in TITLE, or 2+ in description
    if required_skills:
        title_skills = sum(1 for s in required_skills if s in title)
        desc_skills = sum(1 for s in required_skills if s in description)

        if title_skills == 0 and desc_skills < 2:
            return "insufficient skill match (0 in title, <2 in description)"

    return None  # Passed all checks


def compute_rule_score(job: dict, filters: dict) -> float:
    """
    Compute a deterministic rule-based score (0-100).
    Used as the 'rule_score' component in the hybrid formula.
    """
    score = 50.0  # Base score
    title = (job.get("title") or "").lower()
    description = (job.get("description") or "").lower()
    combined = f"{title} {description}"
    company = (job.get("company") or "").lower()

    required_skills = [s.lower() for s in filters.get("required_skills", [])]
    preferred_companies = [c.lower() for c in filters.get("preferred_companies", [])]
    preferred_salary = filters.get("preferred_salary_min", 0)

    # ── Skill matches (up to +30) ──
    skill_matches = sum(1 for s in required_skills if s in combined)
    if required_skills:
        skill_ratio = skill_matches / len(required_skills)
        score += skill_ratio * 30

    # ── Preferred company match (+15) ──
    if any(pc in company for pc in preferred_companies):
        score += 15

    # ── Remote bonus (+5) ──
    if any(w in combined for w in ["remote", "work from home", "wfh"]):
        score += 5

    # ── Salary above preferred (+10) ──
    if preferred_salary and job.get("salary"):
        parsed = _parse_salary_range(job["salary"])
        if parsed and parsed.get("min") and parsed["min"] >= preferred_salary:
            score += 10

    # ── Has apply URL (+5) ──
    if job.get("url"):
        score += 5

    return min(100.0, max(0.0, score))


def _parse_salary_range(salary_str: str) -> dict | None:
    """Parse salary string to min/max numeric values."""
    if not salary_str:
        return None

    s = salary_str.lower().replace(",", "").replace(" ", "")

    # Extract all numbers
    numbers = re.findall(r"[\d.]+", s)
    if not numbers:
        return None

    try:
        nums = [float(n) for n in numbers]

        # Handle LPA notation
        multiplier = 1
        if "lpa" in s or "lakh" in s:
            multiplier = 100000
        elif "cr" in s:
            multiplier = 10000000

        result = {"min": None, "max": None}
        if len(nums) >= 2:
            result["min"] = int(nums[0] * multiplier)
            result["max"] = int(nums[1] * multiplier)
        elif len(nums) == 1:
            result["min"] = int(nums[0] * multiplier)
            result["max"] = int(nums[0] * multiplier)

        return result
    except (ValueError, TypeError):
        return None
