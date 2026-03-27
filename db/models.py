"""
SQLAlchemy Models — Job Intelligence Agent

Tables:
  - FilterConfig: User-configurable search filters (editable from dashboard)
  - JobTemp: Staging table for scraped + scored jobs awaiting review
  - JobMain: Approved job tracker (saved, applied, interviewing, etc.)
  - Feedback: Every thumbs up/down — structured training data for preference learning
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime, JSON,
    UniqueConstraint, Index, create_engine
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class FilterConfig(Base):
    """User-configurable job search filters. Stored in DB, editable from dashboard."""
    __tablename__ = "filter_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(JSON, nullable=False)
    description = Column(String(500), nullable=True)
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self):
        return f"<FilterConfig(key='{self.key}', value={self.value})>"


class JobTemp(Base):
    """Staging table — scraped and scored jobs awaiting user review via Telegram."""
    __tablename__ = "jobs_temp"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hash = Column(String(64), unique=True, nullable=False, index=True)  # SHA256(title+company+url)

    # Job details
    title = Column(String(500), nullable=False)
    company = Column(String(300), nullable=True)
    location = Column(String(300), nullable=True)
    description = Column(Text, nullable=True)
    url = Column(Text, nullable=True)
    source = Column(String(50), nullable=True)  # "indeed", "glassdoor", etc.
    salary = Column(String(200), nullable=True)
    date_posted = Column(String(50), nullable=True)
    job_type = Column(String(100), nullable=True)  # "full-time", "contract", etc.

    # LLM scoring fields
    fit_score = Column(Integer, default=0)  # 0-100
    role_match = Column(Text, nullable=True)  # LLM explanation
    red_flags = Column(Text, nullable=True)  # LLM-detected issues
    match_reason = Column(Text, nullable=True)  # Why this matched

    # Hybrid scoring
    llm_score = Column(Float, default=0.0)
    embedding_score = Column(Float, default=0.0)
    rule_score = Column(Float, default=0.0)
    final_score = Column(Float, default=0.0)  # Weighted combination

    # Status
    status = Column(String(20), default="pending")  # "pending", "liked", "disliked"
    telegram_msg_id = Column(Integer, nullable=True)

    # Timestamps
    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_temp_status", "status"),
        Index("idx_temp_score", "final_score"),
    )


class JobMain(Base):
    """Approved jobs — user's active job tracker."""
    __tablename__ = "jobs_main"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hash = Column(String(64), unique=True, nullable=False, index=True)

    # Job details (copied from JobTemp on approval)
    title = Column(String(500), nullable=False)
    company = Column(String(300), nullable=True)
    location = Column(String(300), nullable=True)
    description = Column(Text, nullable=True)
    url = Column(Text, nullable=True)
    source = Column(String(50), nullable=True)
    salary = Column(String(200), nullable=True)
    date_posted = Column(String(50), nullable=True)
    job_type = Column(String(100), nullable=True)

    # Scores (preserved from scoring phase)
    fit_score = Column(Integer, default=0)
    role_match = Column(Text, nullable=True)
    match_reason = Column(Text, nullable=True)

    # Tracking
    status = Column(String(30), default="saved")  # saved, applied, interviewing, rejected, offered
    notes = Column(Text, nullable=True)
    saved_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    applied_at = Column(DateTime, nullable=True)
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("idx_main_status", "status"),
    )


class Feedback(Base):
    """
    Every thumbs up/down — structured training data for preference learning.

    This table is designed to enable future model training:
    - Phase 1: TF-IDF + cosine similarity using description text
    - Phase 2: Logistic regression on features_json
    - Phase 3: Fine-tune a small model on embeddings + labels
    """
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_hash = Column(String(64), nullable=False, index=True)
    label = Column(Integer, nullable=False)  # 1 = liked, 0 = disliked

    # Denormalized job data (for training without joins)
    title = Column(String(500), nullable=True)
    company = Column(String(300), nullable=True)
    location = Column(String(300), nullable=True)
    description = Column(Text, nullable=True)
    source = Column(String(50), nullable=True)
    salary = Column(String(200), nullable=True)

    # Score at time of feedback
    fit_score = Column(Integer, nullable=True)
    role_match = Column(Text, nullable=True)
    red_flags = Column(Text, nullable=True)

    # Structured features for ML training
    features_json = Column(JSON, nullable=True)
    # Example: {
    #   "skills_found": ["python", "llm", "rag"],
    #   "experience_years": 1,
    #   "is_remote": true,
    #   "company_size": "startup",
    #   "source": "indeed",
    #   "salary_numeric": 800000,
    #   "has_red_flags": false
    # }

    # Embedding vector (for similarity scoring)
    embedding_json = Column(JSON, nullable=True)  # List of floats (TF-IDF or sentence embedding)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_feedback_label", "label"),
    )


class AgentState(Base):
    """Persistent agent state (last run info, Telegram update offset, etc.)."""
    __tablename__ = "agent_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )
