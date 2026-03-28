"""
Telegram Bot — Job delivery + Webhook-based feedback

Two feedback modes:
  1. WEBHOOK (primary) — Instant. FastAPI endpoint receives callbacks in real-time.
  2. POLLING (fallback) — Used during agent runs to catch any missed callbacks.

Sends richly formatted job cards with inline 👍/👎 buttons.
"""

import requests
import time
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ADMIN_USER_ID, WEBHOOK_SECRET


_BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# ═══════════════════════════════════════════
#  WEBHOOK MANAGEMENT
# ═══════════════════════════════════════════

def register_webhook(base_url: str) -> dict:
    """
    Register the webhook URL with Telegram.
    Call this on server startup.

    Args:
        base_url: Public base URL (e.g. https://argus-find-jobs-that-you-want.onrender.com)

    Returns:
        {"ok": bool, "description": str}
    """
    webhook_url = f"{base_url}/api/telegram/webhook"
    url = f"{_BASE_URL}/setWebhook"
    payload = {
        "url": webhook_url,
        "secret_token": WEBHOOK_SECRET,
        "allowed_updates": ["callback_query"],  # Only receive button presses
        "drop_pending_updates": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        body = resp.json()

        if body.get("ok"):
            print(f"  [TELEGRAM] Webhook registered: {webhook_url}")
            return {"ok": True, "description": body.get("description", "OK")}
        else:
            print(f"  [TELEGRAM] Webhook registration failed: {body}")
            return {"ok": False, "description": body.get("description", "Unknown error")}

    except requests.exceptions.RequestException as e:
        print(f"  [TELEGRAM] Webhook registration error: {e}")
        return {"ok": False, "description": str(e)}


def delete_webhook() -> dict:
    """Remove the webhook (switch back to polling mode)."""
    url = f"{_BASE_URL}/deleteWebhook"
    try:
        resp = requests.post(url, timeout=15)
        body = resp.json()
        return {"ok": body.get("ok", False), "description": body.get("description", "")}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "description": str(e)}


def get_webhook_info() -> dict:
    """Get current webhook status from Telegram."""
    url = f"{_BASE_URL}/getWebhookInfo"
    try:
        resp = requests.get(url, timeout=15)
        return resp.json().get("result", {})
    except requests.exceptions.RequestException:
        return {}


def process_webhook_update(update_data: dict) -> dict:
    """
    Process a single incoming webhook update from Telegram.
    Called synchronously by the FastAPI endpoint — no queueing.

    Args:
        update_data: Raw update JSON from Telegram.

    Returns:
        {"processed": bool, "action": str|None, "job_hash": str|None, "error": str|None}
    """
    callback = update_data.get("callback_query") or {}
    if not callback:
        return {"processed": False, "action": None, "job_hash": None, "error": "No callback_query"}

    callback_id = callback.get("id", "")
    payload = callback.get("data", "")

    # Validate callback format: "job:up:abc123" or "job:down:abc123"
    if not payload.startswith("job:"):
        _answer_callback(callback_id, "Unknown action")
        return {"processed": False, "action": None, "job_hash": None, "error": "Not a job callback"}

    parts = payload.split(":", 2)
    if len(parts) != 3 or parts[1] not in ("up", "down"):
        _answer_callback(callback_id, "Invalid action")
        return {"processed": False, "action": None, "job_hash": None, "error": "Invalid format"}

    # Admin-only check
    user = callback.get("from") or {}
    user_id = str(user.get("id", "unknown"))

    if ADMIN_USER_ID and user_id != ADMIN_USER_ID:
        _answer_callback(callback_id, "Feedback restricted to admin.")
        return {"processed": False, "action": None, "job_hash": None, "error": "Not admin"}

    action = parts[1]
    job_hash = parts[2]

    # Acknowledge the button press IMMEDIATELY
    emoji = "👍 Saved!" if action == "up" else "👎 Passed!"
    _answer_callback(callback_id, emoji)

    # Remove buttons and update message with status
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")

    if chat_id and message_id:
        _update_message_after_feedback(chat_id, message_id, message, action)

    return {
        "processed": True,
        "action": action,
        "job_hash": job_hash,
        "user_id": user_id,
        "error": None,
    }


def validate_webhook_secret(secret_header: str) -> bool:
    """Validate the X-Telegram-Bot-Api-Secret-Token header."""
    if not WEBHOOK_SECRET:
        return True  # No secret configured = skip validation
    return secret_header == WEBHOOK_SECRET


def _update_message_after_feedback(chat_id: int, message_id: int, message: dict, action: str):
    """
    Edit the original job message after feedback:
    - Remove the inline keyboard (Save/Pass buttons)
    - Append a status line showing the action taken
    """
    # Get original text from the message
    original_text = message.get("text") or ""

    # Build status footer
    if action == "up":
        status = "\n\n✅ *Saved to tracker*"
    else:
        status = "\n\n❌ *Passed*"

    # Remove the old "Tap 👍 to save or 👎 to pass" line and append status
    updated_text = original_text.replace("Tap 👍 to save or 👎 to pass", "").rstrip()
    updated_text += status

    url = f"{_BASE_URL}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": updated_text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
        # No reply_markup = buttons removed
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            # Fallback: just remove the keyboard without changing text
            _remove_keyboard(chat_id, message_id)
    except requests.exceptions.RequestException:
        # Fallback: just remove the keyboard
        _remove_keyboard(chat_id, message_id)


def _remove_keyboard(chat_id: int, message_id: int):
    """Fallback: just remove the inline keyboard without changing text."""
    url = f"{_BASE_URL}/editMessageReplyMarkup"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": []},
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except requests.exceptions.RequestException:
        pass


# ═══════════════════════════════════════════
#  SEND MESSAGES
# ═══════════════════════════════════════════

def send_job_message(job: dict) -> dict:
    """
    Send a richly formatted job card to Telegram with inline feedback buttons.

    Returns: {"ok": bool, "message_id": int|None, "error": str|None}
    """
    job_hash = job.get("hash", "unknown")
    title = job.get("title", "Untitled")
    company = job.get("company") or "Unknown Company"
    location = job.get("location") or "Not specified"
    salary = job.get("salary") or "Not disclosed"
    source = job.get("source") or "unknown"
    url = job.get("url") or ""
    fit_score = job.get("fit_score", 0)
    match_reason = job.get("match_reason") or ""
    red_flags = job.get("red_flags") or "None"

    # Score emoji
    if fit_score >= 85:
        score_emoji = "🔥"
    elif fit_score >= 70:
        score_emoji = "🎯"
    elif fit_score >= 50:
        score_emoji = "⚡"
    else:
        score_emoji = "📋"

    # Build message
    lines = [
        f"{score_emoji} *{_escape_md(title)}*",
        f"🏢 {_escape_md(company)} | 📍 {_escape_md(location)}",
        f"💰 {_escape_md(salary)} | 📊 Score: {fit_score}/100",
        f"🔗 Source: {_escape_md(source)}",
        "",
        f"📝 {_escape_md(match_reason)}",
    ]

    if red_flags and red_flags.lower() not in ("none", "none detected", ""):
        lines.append(f"⚠️ {_escape_md(red_flags)}")

    if url:
        lines.append(f"\n🔗 [Apply Here]({url})")

    lines.append("\n_Tap 👍 to save or 👎 to pass_")

    message = "\n".join(lines)

    # Callback data (max 64 bytes for Telegram)
    cb_up = f"job:up:{job_hash}"[:64]
    cb_down = f"job:down:{job_hash}"[:64]

    keyboard = {
        "inline_keyboard": [[
            {"text": "👍 Save", "callback_data": cb_up},
            {"text": "👎 Pass", "callback_data": cb_down},
        ]]
    }

    return _send_message(message, reply_markup=keyboard)


def send_status_message(text: str) -> dict:
    """Send a plain status update message."""
    return _send_message(text)


def send_run_summary(stats: dict) -> dict:
    """Send a summary of the agent run."""
    lines = [
        "📊 *Job Agent Run Summary*",
        "",
        f"🔍 Fetched: {stats.get('fetched', 0)} raw jobs",
        f"🔧 Pre-filtered: {stats.get('prefiltered', 0)} passed",
        f"🧠 LLM scored: {stats.get('scored', 0)} jobs",
        f"📬 Delivered: {stats.get('delivered', 0)} to Telegram",
        f"⏱️ Duration: {stats.get('duration', 'N/A')}",
    ]

    if stats.get("rejected_reasons"):
        lines.append("\n📋 *Top rejection reasons:*")
        for reason, count in list(stats["rejected_reasons"].items())[:5]:
            lines.append(f"  • {_escape_md(reason)}: {count}")

    return _send_message("\n".join(lines))


def _send_message(text: str, reply_markup: dict = None) -> dict:
    """Send a single Telegram message. Falls back to plaintext on Markdown error."""
    url = f"{_BASE_URL}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            body = resp.json()
            return {
                "ok": True,
                "message_id": (body.get("result") or {}).get("message_id"),
            }

        # Markdown might have caused the error — retry as plaintext
        plain = text.replace("*", "").replace("_", "").replace("`", "")
        payload2 = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": plain,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload2["reply_markup"] = reply_markup

        resp2 = requests.post(url, json=payload2, timeout=15)
        if resp2.status_code == 200:
            body2 = resp2.json()
            return {
                "ok": True,
                "message_id": (body2.get("result") or {}).get("message_id"),
            }

        return {"ok": False, "error": f"HTTP {resp2.status_code}: {resp2.text[:200]}"}

    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": str(e)}


def _escape_md(text: str) -> str:
    """Escape special Markdown characters for Telegram."""
    if not text:
        return ""
    # Only escape characters that break Markdown v1
    for char in ["[", "]", "(", ")", "~", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"]:
        text = text.replace(char, f"\\{char}")
    return text


# ═══════════════════════════════════════════
#  POLL FEEDBACK (fallback — used during agent runs)
# ═══════════════════════════════════════════

def pull_feedback(last_update_id: int = 0) -> dict:
    """
    Poll for new feedback from Telegram callback queries.
    This is the FALLBACK method — webhook handles most callbacks in real-time.

    Returns:
        {
            "votes": [{"job_hash": str, "action": "up"|"down", "user_id": str}],
            "last_update_id": int,
            "errors": int,
        }
    """
    url = f"{_BASE_URL}/getUpdates"
    params = {
        "offset": last_update_id + 1,
        "timeout": 0,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()

        if not body.get("ok"):
            return {"votes": [], "last_update_id": last_update_id, "errors": 1}

    except requests.exceptions.RequestException as e:
        print(f"  [TELEGRAM] Feedback poll failed: {e}")
        return {"votes": [], "last_update_id": last_update_id, "errors": 1}

    updates = body.get("result", [])
    votes = []
    max_update_id = last_update_id
    errors = 0

    for update in updates:
        update_id = update.get("update_id", 0)
        if isinstance(update_id, int):
            max_update_id = max(max_update_id, update_id)

        callback = update.get("callback_query") or {}
        callback_id = callback.get("id", "")
        payload = callback.get("data", "")

        if not payload.startswith("job:"):
            if callback_id:
                _answer_callback(callback_id, "Unknown action")
            continue

        # Parse callback: "job:up:abc123" or "job:down:abc123"
        parts = payload.split(":", 2)
        if len(parts) != 3 or parts[1] not in ("up", "down"):
            if callback_id:
                _answer_callback(callback_id, "Invalid action")
            continue

        # Admin-only check
        user = callback.get("from") or {}
        user_id = str(user.get("id", "unknown"))

        if ADMIN_USER_ID and user_id != ADMIN_USER_ID:
            _answer_callback(callback_id, "Feedback restricted to admin.")
            continue

        action = parts[1]
        job_hash = parts[2]

        votes.append({
            "job_hash": job_hash,
            "action": action,
            "user_id": user_id,
            "callback_id": callback_id,
        })

        # Acknowledge the button press
        emoji = "👍" if action == "up" else "👎"
        _answer_callback(callback_id, f"{emoji} Recorded!")

    return {
        "votes": votes,
        "last_update_id": max_update_id,
        "errors": errors,
    }


def _answer_callback(callback_query_id: str, text: str):
    """Acknowledge a callback query (stops loading animation on button)."""
    if not callback_query_id:
        return
    try:
        url = f"{_BASE_URL}/answerCallbackQuery"
        payload = {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": False,
        }
        requests.post(url, json=payload, timeout=10)
    except requests.exceptions.RequestException:
        pass
