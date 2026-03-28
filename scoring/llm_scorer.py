"""
LLM Scorer — Layer 2 (Gemini 2.5 Flash via OpenRouter)

Scores pre-filtered jobs using structured JSON output.
Includes few-shot examples from user's feedback history.
Enforces hard constraints on experience mismatch.
"""

import json
import time
import traceback
from openai import OpenAI
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL


# ─── OpenRouter Client (OpenAI-compatible) ───
_client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url=OPENROUTER_BASE_URL,
)


def score_single_job(
    job: dict,
    user_profile: dict,
    feedback_context: dict = None,
) -> dict:
    """
    Score a single job using Gemini 2.5 Flash via OpenRouter.

    Args:
        job: Normalized job dict with title, company, description, etc.
        user_profile: Dict with target roles, skills, preferences.
        feedback_context: Dict with liked/disliked examples from feedback history.

    Returns:
        Dict with: fit_score (0-100), role_match, red_flags, match_reason,
                   is_genuine, experience_required
    """
    prompt = _build_scoring_prompt(job, user_profile, feedback_context)

    for attempt in range(3):
        try:
            response = _client.chat.completions.create(
                model=OPENROUTER_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=500,
                temperature=0.1,
                response_format={"type": "json_object"},
            )

            raw = response.choices[0].message.content.strip()
            result = json.loads(raw)

            fit_score = max(0, min(100, int(result.get("fit_score", 0))))

            # Double-check experience constraint in code
            experience_required = result.get("experience_required")
            experience_max = user_profile.get("experience_max", 2)

            if experience_required is not None:
                try:
                    exp_req = int(experience_required)
                    if exp_req > experience_max:
                        # Hard cap: LLM might have been lenient
                        fit_score = min(fit_score, 25)
                        result["red_flags"] = (
                            f"Requires {exp_req} years experience "
                            f"(your max: {experience_max}). "
                            + str(result.get("red_flags", ""))
                        )
                except (ValueError, TypeError):
                    pass

            return {
                "fit_score": fit_score,
                "role_match": str(result.get("role_match", "")),
                "red_flags": str(result.get("red_flags", "None")),
                "match_reason": str(result.get("match_reason", "")),
                "is_genuine": bool(result.get("is_genuine", True)),
                "experience_required": experience_required,
            }

        except json.JSONDecodeError:
            print(f"  [LLM] JSON parse failed (attempt {attempt + 1}/3)")
            if attempt < 2:
                time.sleep(2)
            continue

        except Exception as e:
            err = str(e)
            if "429" in err or "rate" in err.lower():
                wait = 10 * (attempt + 1)
                print(f"  [LLM RATE-LIMITED] Waiting {wait}s...")
                time.sleep(wait)
                continue
            if attempt == 2:
                print(f"  [LLM ERROR] {e}")
                traceback.print_exc()
            else:
                time.sleep(3)

    # Fallback: return neutral score
    return {
        "fit_score": 50,
        "role_match": "Scoring failed — manual review needed",
        "red_flags": "Could not analyze",
        "match_reason": "LLM scoring unavailable",
        "is_genuine": True,
        "experience_required": None,
    }


def score_batch(
    jobs: list[dict],
    user_profile: dict,
    feedback_context: dict = None,
    delay_between: float = 1.0,
) -> list[dict]:
    """
    Score multiple jobs sequentially with rate limiting.

    Returns list of score dicts (same order as input jobs).
    """
    results = []
    for i, job in enumerate(jobs):
        title = (job.get("title") or "")[:50]
        print(f"  [LLM] Scoring [{i + 1}/{len(jobs)}]: {title}...")

        score = score_single_job(job, user_profile, feedback_context)
        results.append(score)

        # Rate limiting between calls
        if i < len(jobs) - 1:
            time.sleep(delay_between)

    return results


# ═══════════════════════════════════════════
#  PROMPTS
# ═══════════════════════════════════════════

SYSTEM_PROMPT = """You are an expert AI job market analyst and career advisor.
Your task is to evaluate job postings for a candidate and score their relevance.

CRITICAL RULES:
1. You MUST respond with valid JSON only. No markdown, no extra text.
2. You MUST extract the required years of experience from the job description.
3. If the required experience exceeds the candidate's maximum, the fit_score MUST be ≤ 25.
4. Be strict about experience requirements — do not be lenient."""


def _build_scoring_prompt(
    job: dict,
    user_profile: dict,
    feedback_context: dict = None,
) -> str:
    """Build the scoring prompt with job details and user preferences."""

    search_terms = user_profile.get("search_terms", [])
    required_skills = user_profile.get("required_skills", [])
    preferred_companies = user_profile.get("preferred_companies", [])
    excluded_keywords = user_profile.get("excluded_keywords", [])
    experience_min = user_profile.get("experience_min", 0)
    experience_max = user_profile.get("experience_max", 2)
    preferred_salary = user_profile.get("preferred_salary_min", 800000)

    # Truncate description to save tokens
    description = (job.get("description") or "")[:1500]

    prompt = f"""Evaluate this job posting for the candidate described below.

## CANDIDATE PROFILE
- Target Roles: {', '.join(search_terms)}
- Must-Have Skills: {', '.join(required_skills)}
- Experience Level: {experience_min}-{experience_max} years (HARD LIMIT — candidate has at most {experience_max} years)
- Preferred Salary: ≥ {preferred_salary / 100000:.0f} LPA
- Preferred Companies: {', '.join(preferred_companies[:10])}
- Deal Breakers: {', '.join(excluded_keywords)}

## JOB POSTING
- Title: {job.get('title', 'N/A')}
- Company: {job.get('company', 'N/A')}
- Location: {job.get('location', 'N/A')}
- Salary: {job.get('salary', 'Not disclosed')}
- Source: {job.get('source', 'N/A')}
- Description:
{description}
"""

    # Add feedback context (few-shot from past likes/dislikes)
    if feedback_context and feedback_context.get("total_feedback", 0) > 0:
        prompt += "\n## USER PREFERENCE SIGNALS (from past feedback)\n"

        liked = feedback_context.get("liked_examples", [])
        if liked:
            prompt += "\nJobs the user LIKED (similar = good):\n"
            for ex in liked[:5]:
                prompt += f"  - '{ex['title']}' at {ex.get('company', 'N/A')}\n"

        disliked = feedback_context.get("disliked_examples", [])
        if disliked:
            prompt += "\nJobs the user DISLIKED (similar = bad):\n"
            for ex in disliked[:5]:
                prompt += f"  - '{ex['title']}' at {ex.get('company', 'N/A')}"
                if ex.get("reason"):
                    prompt += f" (reason: {ex['reason']})"
                prompt += "\n"

        prompt += "\nUse these signals to adjust scoring. Prefer patterns from liked jobs.\n"

    prompt += f"""
## SCORING INSTRUCTIONS

STEP 1: Extract the minimum years of experience required from the job description.
Look for patterns like "X+ years", "X-Y years", "minimum X years", etc.
If the description says "fresher" or "0 years", set experience_required to 0.
If experience is not mentioned, set experience_required to null.

STEP 2: Compare extracted experience with candidate's maximum ({experience_max} years).
- If experience_required > {experience_max}: fit_score MUST be ≤ 25 (HARD RULE)
- If experience_required <= {experience_max}: score normally

STEP 3: Evaluate overall fit and respond with this exact JSON structure:
{{
    "fit_score": <integer 0-100>,
    "role_match": "<1-2 sentence explanation of role fit>",
    "red_flags": "<any concerns, deceptive patterns, or 'None detected'>",
    "match_reason": "<1-2 sentence summary of why this job matches or doesn't>",
    "is_genuine": <true/false — whether this is a real, legitimate job posting>,
    "experience_required": <integer or null — years of experience the JD requires>
}}

Scoring guide:
- 85-100: Perfect match — right role, right skills, right experience level, strong company
- 70-84: Strong match — most criteria met, experience within range
- 50-69: Moderate match — some criteria met, some gaps
- 30-49: Weak match — significant gaps but experience is acceptable
- 0-25: Poor match — experience too high, wrong role, or deal-breakers present

CRITICAL: If the job requires more than {experience_max} years of experience, score ≤ 25.
Be strict. Detect jobs that disguise non-AI roles as AI roles.
Penalize vague descriptions, missing company info, or unrealistic requirements.
"""

    return prompt
