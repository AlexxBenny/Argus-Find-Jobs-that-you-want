"""
FastAPI Server — Dashboard API + Static File Serving

Endpoints:
  - GET  /api/stats          — Dashboard overview stats
  - GET  /api/jobs           — List saved jobs (filterable)
  - GET  /api/jobs/{hash}    — Job detail
  - PUT  /api/jobs/{hash}    — Update job status/notes
  - DELETE /api/jobs/{hash}  — Delete a saved job
  - GET  /api/pending        — Pending temp jobs
  - POST /api/jobs/{hash}/approve  — Approve from UI
  - POST /api/jobs/{hash}/reject   — Reject from UI
  - GET  /api/filters        — Get all filters
  - PUT  /api/filters/{key}  — Update a filter
  - GET  /api/learning       — Preference learning stats
  - POST /api/trigger        — Manually trigger agent run

Static dashboard served from /dashboard/ directory.
"""

import os
import threading
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db.database import init_db, get_db, get_session
from db.crud import (
    seed_default_filters, get_all_filters, get_filters_with_meta, update_filter,
    get_main_jobs, get_main_job_by_hash, update_main_job, delete_main_job,
    get_pending_jobs, approve_job, reject_job, get_temp_job_by_hash,
    get_dashboard_stats,
)
from learning.preference_engine import PreferenceEngine


# ─── Lifespan ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB and seed filters on startup."""
    init_db()
    db = get_session()
    try:
        seed_default_filters(db)
    finally:
        db.close()
    print("[SERVER] Database initialized, filters seeded.")
    yield


app = FastAPI(
    title="Job Intelligence Agent",
    description="Proactive job scraping with preference learning",
    version="1.0.0",
    lifespan=lifespan,
)

# ─── CORS (allow dashboard on any origin) ───
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════
#  PYDANTIC MODELS
# ═══════════════════════════════════════════

class JobUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None


class FilterUpdate(BaseModel):
    value: object
    description: str | None = None


# ═══════════════════════════════════════════
#  STATS
# ═══════════════════════════════════════════

@app.get("/api/stats")
def api_stats(db: Session = Depends(get_db)):
    return get_dashboard_stats(db)


# ═══════════════════════════════════════════
#  SAVED JOBS (jobs_main)
# ═══════════════════════════════════════════

@app.get("/api/jobs")
def api_list_jobs(
    status: str = Query(None),
    search: str = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    jobs = get_main_jobs(db, status=status, search=search, limit=limit, offset=offset)
    return [_serialize_main_job(j) for j in jobs]


@app.get("/api/jobs/{job_hash}")
def api_get_job(job_hash: str, db: Session = Depends(get_db)):
    job = get_main_job_by_hash(db, job_hash)
    if not job:
        raise HTTPException(404, "Job not found")
    return _serialize_main_job(job)


@app.put("/api/jobs/{job_hash}")
def api_update_job(job_hash: str, body: JobUpdate, db: Session = Depends(get_db)):
    updates = {}
    if body.status is not None:
        if body.status not in ("saved", "applied", "interviewing", "rejected", "offered"):
            raise HTTPException(400, "Invalid status")
        updates["status"] = body.status
    if body.notes is not None:
        updates["notes"] = body.notes

    if not updates:
        raise HTTPException(400, "No valid fields to update")

    success = update_main_job(db, job_hash, updates)
    if not success:
        raise HTTPException(404, "Job not found")
    return {"ok": True}


@app.delete("/api/jobs/{job_hash}")
def api_delete_job(job_hash: str, db: Session = Depends(get_db)):
    success = delete_main_job(db, job_hash)
    if not success:
        raise HTTPException(404, "Job not found")
    return {"ok": True}


# ═══════════════════════════════════════════
#  PENDING JOBS (jobs_temp)
# ═══════════════════════════════════════════

@app.get("/api/pending")
def api_pending_jobs(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    jobs = get_pending_jobs(db, limit=limit)
    return [_serialize_temp_job(j) for j in jobs]


@app.post("/api/jobs/{job_hash}/approve")
def api_approve_job(job_hash: str, db: Session = Depends(get_db)):
    success = approve_job(db, job_hash)
    if not success:
        raise HTTPException(404, "Job not found in pending queue")
    return {"ok": True, "message": "Job saved to tracker"}


@app.post("/api/jobs/{job_hash}/reject")
def api_reject_job(job_hash: str, db: Session = Depends(get_db)):
    success = reject_job(db, job_hash)
    if not success:
        raise HTTPException(404, "Job not found in pending queue")
    return {"ok": True, "message": "Job rejected"}


# ═══════════════════════════════════════════
#  FILTERS (UI-configurable)
# ═══════════════════════════════════════════

@app.get("/api/filters")
def api_get_filters(db: Session = Depends(get_db)):
    return get_filters_with_meta(db)


@app.put("/api/filters/{key}")
def api_update_filter(key: str, body: FilterUpdate, db: Session = Depends(get_db)):
    update_filter(db, key, body.value, body.description)
    return {"ok": True, "key": key}


# ═══════════════════════════════════════════
#  PREFERENCE LEARNING
# ═══════════════════════════════════════════

@app.get("/api/learning")
def api_learning_stats(db: Session = Depends(get_db)):
    engine = PreferenceEngine(db)
    return engine.get_learning_stats()


# ═══════════════════════════════════════════
#  AGENT TRIGGER
# ═══════════════════════════════════════════

@app.post("/api/trigger")
def api_trigger_agent():
    """Manually trigger an agent run in the background."""
    def _run():
        try:
            from agent import run_agent
            run_agent()
        except Exception as e:
            print(f"[TRIGGER] Agent run failed: {e}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"ok": True, "message": "Agent run triggered in background"}


# ═══════════════════════════════════════════
#  SERIALIZERS
# ═══════════════════════════════════════════

def _serialize_main_job(job) -> dict:
    return {
        "hash": job.hash,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "description": job.description,
        "url": job.url,
        "source": job.source,
        "salary": job.salary,
        "date_posted": job.date_posted,
        "job_type": job.job_type,
        "fit_score": job.fit_score,
        "role_match": job.role_match,
        "match_reason": job.match_reason,
        "status": job.status,
        "notes": job.notes,
        "saved_at": job.saved_at.isoformat() if job.saved_at else None,
        "applied_at": job.applied_at.isoformat() if job.applied_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def _serialize_temp_job(job) -> dict:
    return {
        "hash": job.hash,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "description": job.description,
        "url": job.url,
        "source": job.source,
        "salary": job.salary,
        "date_posted": job.date_posted,
        "job_type": job.job_type,
        "fit_score": job.fit_score,
        "role_match": job.role_match,
        "red_flags": job.red_flags,
        "match_reason": job.match_reason,
        "final_score": job.final_score,
        "llm_score": job.llm_score,
        "embedding_score": job.embedding_score,
        "rule_score": job.rule_score,
        "status": job.status,
        "fetched_at": job.fetched_at.isoformat() if job.fetched_at else None,
    }


# ═══════════════════════════════════════════
#  STATIC FILES (Dashboard)
# ═══════════════════════════════════════════

dashboard_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard")

# Serve index.html at root
@app.get("/")
def serve_dashboard():
    index_path = os.path.join(dashboard_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"message": "Dashboard not found. Place files in /dashboard/"}, 404)

# Mount static assets
if os.path.exists(dashboard_dir):
    app.mount("/static", StaticFiles(directory=dashboard_dir), name="static")


# ═══════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
