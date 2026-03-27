"""
Job Intelligence Agent — Main Orchestrator

Pipeline:
  1. Pull Telegram feedback (process any 👍/👎 since last run)
  2. Fetch jobs from all configured sources (JobSpy)
  3. Deterministic pre-filter (cheap — keywords, location, dedup)
  4. LLM scoring (Gemini 2.5 Flash via OpenRouter, structured JSON)
  5. Apply preference boost (feedback-weighted re-ranking)
  6. Store scored jobs in jobs_temp
  7. Deliver top-K to Telegram with feedback buttons
  8. Persist state

Runs hourly via GitHub Actions or APScheduler on Render.
"""

import sys
import time
import traceback
from datetime import datetime, timezone
from collections import Counter

from db.database import init_db, get_session
from db.crud import (
    seed_default_filters, get_all_filters, upsert_temp_job,
    get_pending_jobs, approve_job, reject_job,
    get_agent_state, set_agent_state, update_temp_job_telegram_id,
    update_temp_job_scores,
)
from scraper.job_scraper import fetch_all_configured_jobs
from scoring.prefilter import prefilter_jobs, compute_rule_score
from scoring.llm_scorer import score_batch
from telegram.bot import send_job_message, send_run_summary, pull_feedback
from learning.preference_engine import PreferenceEngine, compute_hybrid_score
from config import (
    JOBS_PER_SOURCE, TOP_K_DELIVER, SCORE_AUTO_REJECT,
    WEIGHT_LLM, WEIGHT_EMBEDDING, WEIGHT_RULE,
)


class JobAgent:
    """Main orchestrator for the job intelligence pipeline."""

    def __init__(self):
        self.log_lines: list[str] = []
        self.start_time = time.time()

    def _log(self, msg: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.log_lines.append(line)
        print(line, flush=True)

    def run(self):
        """Execute the full pipeline."""
        self._log("═══ Job Intelligence Agent — Starting ═══")

        # Initialize DB + seed defaults
        init_db()
        db = get_session()

        try:
            seed_default_filters(db)
            filters = get_all_filters(db)
            self._log(f"Loaded {len(filters)} filter keys from DB")

            # ── Phase 1: Sync Telegram Feedback ──
            self._sync_feedback(db)

            # ── Phase 2: Fetch Jobs ──
            self._log("\n── FETCHING JOBS ──")
            raw_jobs = fetch_all_configured_jobs(filters, JOBS_PER_SOURCE)
            self._log(f"Fetched {len(raw_jobs)} raw jobs from all sources")

            if not raw_jobs:
                self._log("No jobs found from any source. Ending run.")
                send_run_summary({
                    "fetched": 0, "prefiltered": 0, "scored": 0,
                    "delivered": 0, "duration": self._duration(),
                })
                return

            # ── Phase 3: Deterministic Pre-filter ──
            self._log("\n── PRE-FILTERING ──")
            passed, rejected = prefilter_jobs(raw_jobs, filters, db)
            self._log(f"Pre-filter: {len(passed)} passed, {len(rejected)} rejected")

            # Log rejection reasons
            rejection_reasons = Counter()
            for r in rejected:
                reason = r.get("rejection_reason", "unknown")
                # Simplify reason for counting
                simple = reason.split(":")[0].strip()
                rejection_reasons[simple] += 1

            for reason, count in rejection_reasons.most_common(5):
                self._log(f"  ↳ {reason}: {count}")

            if not passed:
                self._log("All jobs rejected by pre-filter. Ending run.")
                send_run_summary({
                    "fetched": len(raw_jobs), "prefiltered": 0,
                    "scored": 0, "delivered": 0,
                    "duration": self._duration(),
                    "rejected_reasons": dict(rejection_reasons),
                })
                return

            # ── Phase 4: LLM Scoring ──
            self._log(f"\n── LLM SCORING ({len(passed)} jobs) ──")

            # Build preference context from feedback history
            pref_engine = PreferenceEngine(db)
            feedback_context = pref_engine.build_profile()
            self._log(f"Preference profile: {feedback_context['total_feedback']} "
                      f"feedback events ({feedback_context['liked_count']} 👍, "
                      f"{feedback_context['disliked_count']} 👎)")

            # Get user profile from filters
            user_profile = {
                "search_terms": filters.get("search_terms", []),
                "required_skills": filters.get("required_skills", []),
                "preferred_companies": filters.get("preferred_companies", []),
                "excluded_keywords": filters.get("excluded_keywords", []),
                "experience_min": filters.get("experience_min", 0),
                "experience_max": filters.get("experience_max", 2),
                "preferred_salary_min": filters.get("preferred_salary_min", 800000),
            }

            # Score with LLM
            llm_scores = score_batch(passed, user_profile, feedback_context)

            # ── Phase 5: Hybrid Scoring ──
            self._log("\n── HYBRID SCORING ──")
            scored_jobs = []

            for job, llm_result in zip(passed, llm_scores):
                # Skip non-genuine postings
                if not llm_result.get("is_genuine", True):
                    self._log(f"  ✗ FAKE: {job['title'][:50]}...")
                    continue

                llm_score = float(llm_result.get("fit_score", 50))

                # Rule-based score
                rule_score = compute_rule_score(job, filters)

                # Embedding similarity score
                description = job.get("description") or ""
                embedding_score = pref_engine.compute_embedding_score(description)

                # Hybrid score
                final_score = compute_hybrid_score(
                    llm_score, embedding_score, rule_score,
                    WEIGHT_LLM, WEIGHT_EMBEDDING, WEIGHT_RULE,
                )

                # Auto-reject low scores
                if final_score < SCORE_AUTO_REJECT:
                    self._log(f"  ✗ LOW ({final_score:.0f}): "
                              f"{job['title'][:50]}...")
                    continue

                # Merge scores into job dict
                job.update({
                    "fit_score": int(llm_score),
                    "role_match": llm_result.get("role_match", ""),
                    "red_flags": llm_result.get("red_flags", ""),
                    "match_reason": llm_result.get("match_reason", ""),
                    "llm_score": llm_score,
                    "embedding_score": embedding_score,
                    "rule_score": rule_score,
                    "final_score": final_score,
                })
                scored_jobs.append(job)

            # Sort by final_score descending
            scored_jobs.sort(key=lambda j: j["final_score"], reverse=True)
            self._log(f"Scored: {len(scored_jobs)} jobs above threshold")

            # ── Phase 6: Store in DB ──
            self._log("\n── STORING JOBS ──")
            stored_count = 0
            for job in scored_jobs:
                inserted, _ = upsert_temp_job(db, job)
                if inserted:
                    stored_count += 1
            self._log(f"Stored {stored_count} new jobs in temp DB")

            # ── Phase 7: Deliver to Telegram ──
            self._log(f"\n── TELEGRAM DELIVERY (top {TOP_K_DELIVER}) ──")

            # Deduplicate by title+company for Telegram (avoid spamming near-identical jobs)
            seen_titles = set()
            unique_jobs = []
            for job in scored_jobs:
                key = f"{(job.get('title') or '').lower().strip()}|{(job.get('company') or '').lower().strip()}"
                if key not in seen_titles:
                    seen_titles.add(key)
                    unique_jobs.append(job)

            top_jobs = unique_jobs[:TOP_K_DELIVER]
            delivered = 0

            for i, job in enumerate(top_jobs):
                self._log(f"  Sending [{i + 1}/{len(top_jobs)}]: "
                          f"{job['title'][:50]}... (score: {job['final_score']:.0f})")

                result = send_job_message(job)
                if result.get("ok"):
                    delivered += 1
                    msg_id = result.get("message_id")
                    if msg_id:
                        update_temp_job_telegram_id(db, job["hash"], msg_id)
                else:
                    self._log(f"    ✗ Send failed: {result.get('error', 'unknown')}")

                # Rate limit between messages
                if i < len(top_jobs) - 1:
                    time.sleep(0.5)

            self._log(f"Delivered {delivered}/{len(top_jobs)} jobs to Telegram")

            # ── Phase 8: Send summary ──
            duration = self._duration()
            summary_stats = {
                "fetched": len(raw_jobs),
                "prefiltered": len(passed),
                "scored": len(scored_jobs),
                "delivered": delivered,
                "duration": duration,
                "rejected_reasons": dict(rejection_reasons),
            }
            send_run_summary(summary_stats)

            self._log(f"\n═══ Agent run complete — {duration} ═══")

        except Exception as e:
            self._log(f"FATAL ERROR: {e}")
            traceback.print_exc()
            raise
        finally:
            db.close()

    def _sync_feedback(self, db):
        """Process any pending Telegram feedback."""
        self._log("\n── SYNCING TELEGRAM FEEDBACK ──")

        last_update = get_agent_state(db, "telegram_last_update_id")
        last_update_id = int(last_update or 0)

        result = pull_feedback(last_update_id)

        if result.get("errors"):
            self._log("  ⚠ Feedback poll had errors (continuing with neutral)")
            return

        votes = result.get("votes", [])
        new_update_id = result.get("last_update_id", last_update_id)

        liked_count = 0
        disliked_count = 0

        for vote in votes:
            job_hash = vote["job_hash"]
            action = vote["action"]

            if action == "up":
                success = approve_job(db, job_hash)
                if success:
                    liked_count += 1
                    self._log(f"  👍 Approved: {job_hash[:12]}...")
            elif action == "down":
                success = reject_job(db, job_hash)
                if success:
                    disliked_count += 1
                    self._log(f"  👎 Rejected: {job_hash[:12]}...")

        # Persist the update offset
        set_agent_state(db, "telegram_last_update_id", str(new_update_id))

        self._log(f"  Feedback sync: {liked_count} 👍, {disliked_count} 👎, "
                  f"{len(votes)} total votes processed")

    def _duration(self) -> str:
        """Get run duration as a formatted string."""
        elapsed = time.time() - self.start_time
        if elapsed < 60:
            return f"{elapsed:.1f}s"
        return f"{elapsed / 60:.1f}m"


# ═══════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════

def run_agent():
    """Run the agent once. Called by scheduler or directly."""
    agent = JobAgent()
    agent.run()


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    try:
        run_agent()
    except Exception as e:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] FATAL: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
