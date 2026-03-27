"""
CRUD Operations — Job Intelligence Agent

All database read/write operations live here.
Used by both the agent (agent.py) and the API server (server.py).
"""

import json
import hashlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from db.models import FilterConfig, JobTemp, JobMain, Feedback, AgentState
from config import DEFAULT_FILTERS


# ═══════════════════════════════════════════
#  FILTERS (UI-configurable, stored in DB)
# ═══════════════════════════════════════════

def seed_default_filters(db: Session):
    """Seed filter_config table with defaults if empty."""
    count = db.query(FilterConfig).count()
    if count > 0:
        return  # Already seeded

    filter_descriptions = {
        "search_terms": "Job title keywords to search for",
        "locations": "Target locations for job search",
        "experience_min": "Minimum years of experience (0 = fresher)",
        "experience_max": "Maximum years of experience",
        "required_skills": "Must-have skills (used in pre-filter + LLM prompt)",
        "preferred_salary_min": "Preferred minimum salary in INR per annum",
        "deal_breaker_salary_max": "Reject jobs below this salary (INR per annum)",
        "excluded_keywords": "Reject jobs containing these keywords",
        "preferred_companies": "Preferred companies (boosted in scoring)",
        "sources": "Active job sources to scrape",
        "sources_available": "All available job sources",
    }

    for key, value in DEFAULT_FILTERS.items():
        db.add(FilterConfig(
            key=key,
            value=value,
            description=filter_descriptions.get(key, ""),
        ))
    db.commit()


def get_all_filters(db: Session) -> dict:
    """Get all filters as a flat dict."""
    rows = db.query(FilterConfig).all()
    return {row.key: row.value for row in rows}


def get_filter(db: Session, key: str):
    """Get a single filter value by key."""
    row = db.query(FilterConfig).filter(FilterConfig.key == key).first()
    return row.value if row else None


def update_filter(db: Session, key: str, value, description: str = None):
    """Update a filter value. Creates if doesn't exist."""
    row = db.query(FilterConfig).filter(FilterConfig.key == key).first()
    if row:
        row.value = value
        if description is not None:
            row.description = description
        row.updated_at = datetime.now(timezone.utc)
    else:
        db.add(FilterConfig(key=key, value=value, description=description or ""))
    db.commit()


def get_filters_with_meta(db: Session) -> list[dict]:
    """Get all filters with metadata (for dashboard settings page)."""
    rows = db.query(FilterConfig).order_by(FilterConfig.key).all()
    return [{
        "key": row.key,
        "value": row.value,
        "description": row.description,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    } for row in rows]


# ═══════════════════════════════════════════
#  JOB HASHING (dedup key)
# ═══════════════════════════════════════════

def compute_job_hash(title: str, company: str = "", url: str = "") -> str:
    """
    Compute a stable dedup hash for a job.

    Uses title + company ONLY (not URL) — the same job posted on multiple
    sources (Indeed, Google, Glassdoor) has different URLs but is the same job.
    This prevents sending "SDE at Amazon" 7 times from 7 sources.
    """
    # Normalize: lowercase, strip, collapse whitespace
    clean_title = " ".join((title or "").lower().split())
    clean_company = " ".join((company or "").lower().split())
    raw = f"{clean_title}|{clean_company}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════
#  JOBS_TEMP (staging table)
# ═══════════════════════════════════════════

def upsert_temp_job(db: Session, job_data: dict) -> tuple[bool, str]:
    """
    Insert a job into jobs_temp. Skip if hash already exists (in temp OR main).
    Returns (was_inserted, hash).
    """
    job_hash = job_data.get("hash") or compute_job_hash(
        job_data.get("title", ""),
        job_data.get("company", ""),
        job_data.get("url", ""),
    )

    # Check if already in temp or main
    existing_temp = db.query(JobTemp).filter(JobTemp.hash == job_hash).first()
    existing_main = db.query(JobMain).filter(JobMain.hash == job_hash).first()
    if existing_temp or existing_main:
        return False, job_hash

    job = JobTemp(
        hash=job_hash,
        title=job_data.get("title", ""),
        company=job_data.get("company"),
        location=job_data.get("location"),
        description=job_data.get("description"),
        url=job_data.get("url"),
        source=job_data.get("source"),
        salary=job_data.get("salary"),
        date_posted=job_data.get("date_posted"),
        job_type=job_data.get("job_type"),
        fit_score=job_data.get("fit_score", 0),
        role_match=job_data.get("role_match"),
        red_flags=job_data.get("red_flags"),
        match_reason=job_data.get("match_reason"),
        llm_score=job_data.get("llm_score", 0.0),
        embedding_score=job_data.get("embedding_score", 0.0),
        rule_score=job_data.get("rule_score", 0.0),
        final_score=job_data.get("final_score", 0.0),
        status="pending",
    )
    db.add(job)
    db.commit()
    return True, job_hash


def get_pending_jobs(db: Session, limit: int = 50) -> list[JobTemp]:
    """Get pending temp jobs, ordered by final_score descending."""
    return (
        db.query(JobTemp)
        .filter(JobTemp.status == "pending")
        .order_by(desc(JobTemp.final_score))
        .limit(limit)
        .all()
    )


def get_temp_job_by_hash(db: Session, job_hash: str) -> Optional[JobTemp]:
    """Get a temp job by its hash."""
    return db.query(JobTemp).filter(JobTemp.hash == job_hash).first()


def update_temp_job_telegram_id(db: Session, job_hash: str, msg_id: int):
    """Store the Telegram message ID for a temp job."""
    job = db.query(JobTemp).filter(JobTemp.hash == job_hash).first()
    if job:
        job.telegram_msg_id = msg_id
        db.commit()


def update_temp_job_scores(db: Session, job_hash: str, scores: dict):
    """Update scoring fields for a temp job."""
    job = db.query(JobTemp).filter(JobTemp.hash == job_hash).first()
    if job:
        for key, value in scores.items():
            if hasattr(job, key):
                setattr(job, key, value)
        db.commit()


# ═══════════════════════════════════════════
#  APPROVE / REJECT (Telegram feedback flow)
# ═══════════════════════════════════════════

def approve_job(db: Session, job_hash: str) -> bool:
    """
    Move job from temp → main. Record positive feedback.
    Returns True if successful.
    """
    temp_job = db.query(JobTemp).filter(JobTemp.hash == job_hash).first()
    if not temp_job:
        return False

    # Check if already in main
    existing = db.query(JobMain).filter(JobMain.hash == job_hash).first()
    if existing:
        # Already approved, just clean up temp
        temp_job.status = "liked"
        db.commit()
        return True

    # Create main entry
    main_job = JobMain(
        hash=temp_job.hash,
        title=temp_job.title,
        company=temp_job.company,
        location=temp_job.location,
        description=temp_job.description,
        url=temp_job.url,
        source=temp_job.source,
        salary=temp_job.salary,
        date_posted=temp_job.date_posted,
        job_type=temp_job.job_type,
        fit_score=temp_job.fit_score,
        role_match=temp_job.role_match,
        match_reason=temp_job.match_reason,
        status="saved",
    )
    db.add(main_job)

    # Record feedback
    _record_feedback(db, temp_job, label=1)

    # Update temp status
    temp_job.status = "liked"
    db.commit()
    return True


def reject_job(db: Session, job_hash: str) -> bool:
    """
    Mark job as rejected. Record negative feedback. Remove from temp.
    Returns True if successful.
    """
    temp_job = db.query(JobTemp).filter(JobTemp.hash == job_hash).first()
    if not temp_job:
        return False

    # Record feedback
    _record_feedback(db, temp_job, label=0)

    # Update temp status
    temp_job.status = "disliked"
    db.commit()
    return True


def _record_feedback(db: Session, job: JobTemp, label: int):
    """Record a feedback entry with structured features for ML training."""
    # Extract structured features
    features = _extract_features(job)

    feedback = Feedback(
        job_hash=job.hash,
        label=label,
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
        source=job.source,
        salary=job.salary,
        fit_score=job.fit_score,
        role_match=job.role_match,
        red_flags=job.red_flags,
        features_json=features,
        # embedding_json will be computed by preference engine later
    )
    db.add(feedback)


def _extract_features(job: JobTemp) -> dict:
    """Extract structured features from a job for ML training."""
    description_lower = (job.description or "").lower()
    title_lower = (job.title or "").lower()
    combined = f"{title_lower} {description_lower}"

    # Detect skills mentioned
    all_skills = [
        "python", "llm", "rag", "langchain", "llamaindex", "transformer",
        "pytorch", "tensorflow", "fastapi", "flask", "django", "docker",
        "kubernetes", "aws", "gcp", "azure", "sql", "nosql", "mongodb",
        "nlp", "computer vision", "deep learning", "machine learning",
        "generative ai", "fine-tuning", "prompt engineering", "vector database",
        "pinecone", "weaviate", "chromadb", "huggingface",
    ]
    skills_found = [s for s in all_skills if s in combined]

    # Parse salary if possible
    salary_numeric = _parse_salary(job.salary)

    # Detect remote
    is_remote = any(w in combined for w in ["remote", "work from home", "wfh"])

    return {
        "skills_found": skills_found,
        "skills_count": len(skills_found),
        "is_remote": is_remote,
        "source": job.source,
        "salary_numeric": salary_numeric,
        "has_red_flags": bool(job.red_flags and job.red_flags.strip()),
        "fit_score": job.fit_score,
        "title_keywords": title_lower.split()[:10],
    }


def _parse_salary(salary_str: str) -> Optional[int]:
    """Try to parse salary string to a numeric value (INR per annum)."""
    if not salary_str:
        return None
    s = salary_str.lower().replace(",", "").replace(" ", "")
    try:
        # Handle "8 LPA", "8LPA", "800000"
        if "lpa" in s:
            num = float("".join(c for c in s.replace("lpa", "") if c.isdigit() or c == "."))
            return int(num * 100000)
        elif "lakh" in s:
            num = float("".join(c for c in s.replace("lakh", "").replace("lakhs", "") if c.isdigit() or c == "."))
            return int(num * 100000)
        else:
            digits = "".join(c for c in s if c.isdigit())
            if digits:
                return int(digits)
    except (ValueError, TypeError):
        pass
    return None


# ═══════════════════════════════════════════
#  JOBS_MAIN (tracker)
# ═══════════════════════════════════════════

def get_main_jobs(
    db: Session,
    status: str = None,
    search: str = None,
    limit: int = 100,
    offset: int = 0,
) -> list[JobMain]:
    """Query main job tracker with optional filters."""
    query = db.query(JobMain)
    if status and status != "all":
        query = query.filter(JobMain.status == status)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            (JobMain.title.ilike(search_pattern)) |
            (JobMain.company.ilike(search_pattern)) |
            (JobMain.location.ilike(search_pattern))
        )
    return query.order_by(desc(JobMain.saved_at)).offset(offset).limit(limit).all()


def get_main_job_by_hash(db: Session, job_hash: str) -> Optional[JobMain]:
    """Get a single main job by hash."""
    return db.query(JobMain).filter(JobMain.hash == job_hash).first()


def update_main_job(db: Session, job_hash: str, updates: dict) -> bool:
    """Update a main job's status, notes, etc."""
    job = db.query(JobMain).filter(JobMain.hash == job_hash).first()
    if not job:
        return False

    for key, value in updates.items():
        if key == "status" and value == "applied" and not job.applied_at:
            job.applied_at = datetime.now(timezone.utc)
        if hasattr(job, key):
            setattr(job, key, value)
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    return True


def delete_main_job(db: Session, job_hash: str) -> bool:
    """Delete a job from the main tracker."""
    job = db.query(JobMain).filter(JobMain.hash == job_hash).first()
    if not job:
        return False
    db.delete(job)
    db.commit()
    return True


# ═══════════════════════════════════════════
#  FEEDBACK (preference learning data)
# ═══════════════════════════════════════════

def get_feedback_history(db: Session, limit: int = 500) -> list[Feedback]:
    """Get recent feedback for preference learning."""
    return (
        db.query(Feedback)
        .order_by(desc(Feedback.created_at))
        .limit(limit)
        .all()
    )


def get_liked_feedback(db: Session, limit: int = 200) -> list[Feedback]:
    """Get liked jobs for building positive preference profile."""
    return (
        db.query(Feedback)
        .filter(Feedback.label == 1)
        .order_by(desc(Feedback.created_at))
        .limit(limit)
        .all()
    )


def get_disliked_feedback(db: Session, limit: int = 200) -> list[Feedback]:
    """Get disliked jobs for building negative preference profile."""
    return (
        db.query(Feedback)
        .filter(Feedback.label == 0)
        .order_by(desc(Feedback.created_at))
        .limit(limit)
        .all()
    )


def get_feedback_counts(db: Session) -> dict:
    """Get total liked/disliked counts."""
    liked = db.query(func.count(Feedback.id)).filter(Feedback.label == 1).scalar() or 0
    disliked = db.query(func.count(Feedback.id)).filter(Feedback.label == 0).scalar() or 0
    return {"liked": liked, "disliked": disliked, "total": liked + disliked}


def update_feedback_embedding(db: Session, feedback_id: int, embedding: list):
    """Store computed embedding for a feedback entry."""
    fb = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if fb:
        fb.embedding_json = embedding
        db.commit()


# ═══════════════════════════════════════════
#  AGENT STATE (persistent key-value store)
# ═══════════════════════════════════════════

def get_agent_state(db: Session, key: str) -> Optional[str]:
    """Get a persistent agent state value."""
    row = db.query(AgentState).filter(AgentState.key == key).first()
    return row.value if row else None


def set_agent_state(db: Session, key: str, value: str):
    """Set a persistent agent state value."""
    row = db.query(AgentState).filter(AgentState.key == key).first()
    if row:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    else:
        db.add(AgentState(key=key, value=value))
    db.commit()


# ═══════════════════════════════════════════
#  STATS (dashboard overview)
# ═══════════════════════════════════════════

def get_dashboard_stats(db: Session) -> dict:
    """Get aggregate stats for the dashboard."""
    total_saved = db.query(func.count(JobMain.id)).scalar() or 0
    applied = db.query(func.count(JobMain.id)).filter(JobMain.status == "applied").scalar() or 0
    interviewing = db.query(func.count(JobMain.id)).filter(JobMain.status == "interviewing").scalar() or 0
    offered = db.query(func.count(JobMain.id)).filter(JobMain.status == "offered").scalar() or 0
    rejected = db.query(func.count(JobMain.id)).filter(JobMain.status == "rejected").scalar() or 0
    pending = db.query(func.count(JobTemp.id)).filter(JobTemp.status == "pending").scalar() or 0

    feedback = get_feedback_counts(db)

    return {
        "total_saved": total_saved,
        "applied": applied,
        "interviewing": interviewing,
        "offered": offered,
        "rejected": rejected,
        "pending_review": pending,
        "feedback_given": feedback["total"],
        "feedback_liked": feedback["liked"],
        "feedback_disliked": feedback["disliked"],
    }
