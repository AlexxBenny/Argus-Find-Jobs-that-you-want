"""
Job Intelligence Agent — Central Configuration

All settings loaded from environment variables (.env file or system env).
Job search filters are stored in the database and read at runtime.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─── OpenRouter (LLM) ───
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ─── Telegram ───
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()

# ─── Telegram Webhook ───
# Auto-detected from Render's RENDER_EXTERNAL_URL, or set manually
WEBHOOK_BASE_URL = os.getenv(
    "WEBHOOK_BASE_URL",
    os.getenv("RENDER_EXTERNAL_URL", ""),
).rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "argus-job-agent-secret")

# ─── Adzuna (pluggable — only used if keys are present) ───
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")

# ─── Database ───
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///job_agent.db")

# ─── Agent Behaviour ───
JOBS_PER_SOURCE = int(os.getenv("JOBS_PER_SOURCE", "15"))
TOP_K_DELIVER = int(os.getenv("TOP_K_DELIVER", "10"))

# ─── Scoring Weights (Hybrid Formula) ───
# final_score = W_LLM * llm_score + W_EMBEDDING * embedding_sim + W_RULE * rule_score
WEIGHT_LLM = 0.6
WEIGHT_EMBEDDING = 0.3
WEIGHT_RULE = 0.1

# ─── LLM Scoring Thresholds ───
SCORE_AUTO_REJECT = 30  # Below this → never sent to Telegram
SCORE_LOW_PRIORITY = 60  # Below this → lower Telegram priority

# ─── Keep-Alive ───
KEEP_ALIVE_INTERVAL_SECONDS = int(os.getenv("KEEP_ALIVE_INTERVAL", "600"))  # 10 min

# ─── Default Filter Profile (used to seed the DB on first run) ───
DEFAULT_FILTERS = {
    "search_terms": [
        "AI Engineer",
        "AI Systems Architect",
        "Gen AI Engineer",
        "Generative AI Engineer",
        "ML Engineer",
        "Machine Learning Engineer",
        "LLM Engineer",
    ],
    "locations": [
        "Kerala",
        "Bangalore",
        "India",
        "Remote",
    ],
    "experience_min": 0,
    "experience_max": 2,
    "required_skills": [
        "Python", "LLM", "RAG", "Generative AI", "NLP",
        "Langchain", "LlamaIndex", "Transformer", "Deep Learning",
        "FastAPI", "Machine Learning",
    ],
    "preferred_salary_min": 800000,  # 8 LPA in rupees
    "deal_breaker_salary_max": 600000,  # Below 6 LPA is a deal-breaker
    "excluded_keywords": [
        "sales", "marketing", "business development",
        "content writer", "graphic design", "non-tech",
        "data entry", "telecaller",
    ],
    "preferred_companies": [
        "Google", "Microsoft", "Amazon", "Meta", "OpenAI",
        "Anthropic", "NVIDIA", "DeepMind", "Flipkart",
        "Razorpay", "CRED", "Swiggy", "Atlassian",
    ],
    "sources": [
        "indeed",
        "google",
        "remoteok",
    ],
    "sources_available": [
        "indeed",
        "linkedin",
        "glassdoor",
        "google",
        "zip_recruiter",
        "remoteok",
        "adzuna",
    ],
}
