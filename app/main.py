"""Flask application: JSON API + static frontend.

Routes
  GET  /              -> frontend/index.html (and other static assets)
  GET  /api/health    -> liveness + knowledge-base status
  GET  /api/config    -> public, non-secret settings for the UI (contact links, limits)
  POST /api/chat      -> {"message": str, "history": [{"role","content"}]} -> {"answer","sources","grounded","request_id"}
"""
from __future__ import annotations

import logging
import uuid

from flask import Flask, jsonify, request, send_from_directory
from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError, RateLimitError
from werkzeug.exceptions import HTTPException

from app import guardrails
from app.config import BASE_DIR, public_config, settings
from app.llm import answer_question
from app.openai_client import MissingAPIKeyError
from app.rate_limit import RateLimiter
from app.retriever import get_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("chatbot")

FRONTEND_DIR = BASE_DIR / "frontend"
MAX_BODY_BYTES = 32 * 1024

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
limiter = RateLimiter(settings.rate_limit_per_minute, settings.rate_limit_per_day)


# --------------------------------------------------------------------------- helpers
def client_ip() -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def error(message: str, status: int, **extra):
    payload = {"error": message, **extra}
    return jsonify(payload), status


# --------------------------------------------------------------------------- CORS + security headers
@app.after_request
def add_headers(response):
    origin = request.headers.get("Origin")
    if origin and origin in settings.allowed_origins and request.path.startswith("/api/"):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Max-Age"] = "600"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if not settings.embed_origins:
        response.headers["X-Frame-Options"] = "DENY"
    frame_ancestors = " ".join(["'self'", *settings.embed_origins]) if settings.embed_origins else "'none'"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self' " + " ".join(settings.allowed_origins) + "; "
        f"frame-ancestors {frame_ancestors}; base-uri 'self'; form-action 'none'"
    )
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return ("", 204)


# --------------------------------------------------------------------------- API
@app.get("/api/health")
def health():
    store = get_store()
    store.reload_if_changed()
    return jsonify(
        {
            "status": "ok",
            "knowledge_base_ready": store.ready,
            "knowledge_base": store.stats(),
            "model": settings.chat_model,
            "api_key_configured": bool(settings.openai_api_key),  # boolean only, never the key
        }
    )


@app.get("/api/config")
def config():
    return jsonify(public_config())


@app.post("/api/chat")
def chat():
    request_id = uuid.uuid4().hex[:12]

    allowed, retry_after = limiter.check(client_ip())
    if not allowed:
        resp, status = error(
            "You're sending messages too quickly. Please wait a moment and try again.",
            429,
            retry_after=retry_after,
        )
        resp.headers["Retry-After"] = str(retry_after)
        return resp, status

    if not request.is_json:
        return error("Requests must be JSON.", 415)
    data = request.get_json(silent=True) or {}

    try:
        message = guardrails.validate_user_message(data.get("message"), settings.max_message_chars)
        history = guardrails.validate_history(
            data.get("history"), settings.max_history_turns, settings.max_message_chars
        )
    except guardrails.ValidationError as exc:
        return error(str(exc), 400)

    try:
        result = answer_question(message, history)
    except MissingAPIKeyError:
        log.error("[%s] OPENAI_API_KEY missing", request_id)
        return error("The assistant is not configured yet. Please contact Powell directly.", 503)
    except AuthenticationError:
        log.error("[%s] OpenAI rejected the API key (401). Check OPENAI_API_KEY in .env.", request_id)
        return error("The assistant is not configured correctly yet. Please contact Powell directly.", 503)
    except RateLimitError:
        log.warning("[%s] OpenAI rate limit / quota reached", request_id)
        return error("The assistant is busy right now. Please try again in a minute.", 503)
    except (APITimeoutError, APIConnectionError):
        log.warning("[%s] OpenAI timeout/connection error", request_id)
        return error("The assistant could not reach the AI service. Please try again.", 504)
    except APIError as exc:
        log.error("[%s] OpenAI API error: %s", request_id, guardrails.redact_secrets(str(exc)))
        return error("The assistant hit an unexpected problem. Please try again.", 502)

    log.info("[%s] answered grounded=%s sources=%d", request_id, result["grounded"], len(result["sources"]))
    return jsonify({**result, "request_id": request_id})


@app.errorhandler(HTTPException)
def http_error(exc: HTTPException):
    if request.path.startswith("/api/"):
        if exc.code == 413:
            return error("Request too large.", 413)
        return error(exc.description or exc.name, exc.code or 500)
    return exc


@app.errorhandler(Exception)
def unhandled(exc: Exception):
    log.exception("Unhandled error: %s", guardrails.redact_secrets(str(exc)))
    return error("Internal server error.", 500)


# --------------------------------------------------------------------------- frontend
@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename: str):
    if filename.startswith("api/"):
        return error("Not found.", 404)
    return send_from_directory(FRONTEND_DIR, filename)


if __name__ == "__main__":
    import os

    port = int(os.getenv("PORT", "8000"))
    log.info("Starting development server on http://127.0.0.1:%d", port)
    app.run(host="127.0.0.1", port=port, debug=False)
