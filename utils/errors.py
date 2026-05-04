"""
Structured Error Hierarchy + LLM Retry Logic (inspired by Claw errors.ts + withRetry.ts)
========================================================================================
Provides:
  1. Typed exception classes for LLM API errors (classify, don't string-match)
  2. Error classifier: raw exception → typed NanobotAPIError subclass
  3. Retry-aware wrapper: exponential backoff + retry-after + max retries

Usage in agentic_loop.py::

    from utils.errors import classify_llm_error, LLMRetry, TransientAPIError

    try:
        stream = await client.chat.completions.create(**kwargs)
    except Exception as raw:
        typed = classify_llm_error(raw)
        if typed.is_retryable:
            ...  # caller decides to retry or not

    # Or use the retry wrapper:
    async for event in LLMRetry(max_retries=3).stream(create_fn, **kwargs):
        yield event

Thread/async safety: same contract as server_state.py (single asyncio thread).
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Constants (Claw withRetry.ts parity) ──────────────────────
DEFAULT_MAX_RETRIES = 5
BASE_DELAY_MS = 500
MAX_DELAY_MS = 32_000  # 32 seconds cap


# ══════════════════════════════════════════════════════════════
# 1. Structured Exception Hierarchy
# ══════════════════════════════════════════════════════════════

class NanobotAPIError(Exception):
    """Base class for all classified LLM API errors.

    Attributes:
        original:     The raw exception that was caught.
        status_code:  HTTP status code (or None for non-HTTP errors).
        is_retryable: Whether this error class is safe to retry.
        error_type:   Short string tag for logging/metrics.
        retry_after:  Seconds to wait before retry (from header), or None.
    """
    is_retryable: bool = False
    error_type: str = "unknown"

    def __init__(self, message: str, *, original: Optional[Exception] = None,
                 status_code: Optional[int] = None, retry_after: Optional[float] = None):
        super().__init__(message)
        self.original = original
        self.status_code = status_code
        self.retry_after = retry_after


class TransientAPIError(NanobotAPIError):
    """Transient server error (5xx, 529 overloaded). Safe to retry with backoff."""
    is_retryable = True
    error_type = "transient"


class RateLimitError(NanobotAPIError):
    """Rate limit hit (429). Retry after delay from header or backoff."""
    is_retryable = True
    error_type = "rate_limit"


class ContextOverflowError(NanobotAPIError):
    """Prompt too long / context length exceeded. Retry after compaction."""
    is_retryable = True  # retryable after compaction, not after simple backoff
    error_type = "context_overflow"


class AuthenticationError(NanobotAPIError):
    """Invalid API key, expired token, forbidden (401/403). Do NOT retry."""
    is_retryable = False
    error_type = "authentication"


class InvalidRequestError(NanobotAPIError):
    """Bad request (400) that isn't context overflow. Do NOT retry."""
    is_retryable = False
    error_type = "invalid_request"


class ConnectionError_(NanobotAPIError):
    """Network unreachable, DNS failure, connection reset. Retry with backoff."""
    is_retryable = True
    error_type = "connection"


class TimeoutError_(NanobotAPIError):
    """Request timed out. Retry with backoff."""
    is_retryable = True
    error_type = "timeout"


class ToolCallTypeError(NanobotAPIError):
    """llama.cpp 'Missing tool call type' error. Retry after P80 repair."""
    is_retryable = True
    error_type = "tool_call_type"


# ══════════════════════════════════════════════════════════════
# 2. Error Classifier (Claw getAssistantMessageFromError pattern)
# ══════════════════════════════════════════════════════════════

# Context overflow detection patterns (P10 parity)
_CONTEXT_ERROR_PATTERNS = (
    "context length", "too many tokens", "maximum context",
    "context_length_exceeded", "max_tokens", "prompt is too long",
    "exceed context limit",
)

# Tool call type error patterns (P80 parity)
_TOOL_CALL_TYPE_PATTERNS = ("missing tool call type", "tool_call_type")


def classify_llm_error(raw: Exception) -> NanobotAPIError:
    """Classify a raw exception into a typed NanobotAPIError subclass.

    Mirrors Claw's getAssistantMessageFromError + shouldRetry logic, adapted
    for the OpenAI-compatible SDK that Nanobot uses (openai, litellm).
    """
    msg = str(raw).lower()
    status = _extract_status_code(raw)
    retry_after = _extract_retry_after(raw)

    # ── Tool call type (P80) ─────────────────────────────────
    if any(p in msg for p in _TOOL_CALL_TYPE_PATTERNS):
        return ToolCallTypeError(
            str(raw), original=raw, status_code=status,
        )

    # ── Context overflow (P10) ───────────────────────────────
    if any(p in msg for p in _CONTEXT_ERROR_PATTERNS):
        return ContextOverflowError(
            str(raw), original=raw, status_code=status,
        )

    # ── HTTP status based classification ─────────────────────
    if status is not None:
        if status == 429:
            return RateLimitError(
                str(raw), original=raw, status_code=429, retry_after=retry_after,
            )
        if status in (401, 403):
            return AuthenticationError(
                str(raw), original=raw, status_code=status,
            )
        if status == 408:
            return TimeoutError_(
                str(raw), original=raw, status_code=408,
            )
        if status == 529 or (status and status >= 500):
            return TransientAPIError(
                str(raw), original=raw, status_code=status,
            )
        if status == 400:
            return InvalidRequestError(
                str(raw), original=raw, status_code=400,
            )

    # ── Exception type based classification ──────────────────
    type_name = type(raw).__name__.lower()

    if "timeout" in type_name or "timeout" in msg:
        return TimeoutError_(
            str(raw), original=raw, status_code=status,
        )

    if any(k in type_name for k in ("connection", "network", "dns", "socket")):
        return ConnectionError_(
            str(raw), original=raw, status_code=status,
        )

    if any(k in msg for k in ("connection refused", "connection reset",
                               "name resolution", "unreachable", "econnreset")):
        return ConnectionError_(
            str(raw), original=raw, status_code=status,
        )

    if any(k in msg for k in ("api key", "api_key", "unauthorized", "forbidden",
                               "authentication", "x-api-key")):
        return AuthenticationError(
            str(raw), original=raw, status_code=status,
        )

    if "overloaded" in msg or "capacity" in msg:
        return TransientAPIError(
            str(raw), original=raw, status_code=status or 529,
        )

    # ── Fallback: unknown error, not retryable ───────────────
    return NanobotAPIError(
        str(raw), original=raw, status_code=status,
    )


def _extract_status_code(err: Exception) -> Optional[int]:
    """Extract HTTP status from various SDK exception types."""
    # openai SDK
    for attr in ("status_code", "status", "http_status", "code"):
        val = getattr(err, attr, None)
        if isinstance(val, int) and 100 <= val < 600:
            return val
    # litellm wraps status in .status_code on the exception
    if hasattr(err, "response"):
        resp = getattr(err, "response", None)
        if resp is not None:
            sc = getattr(resp, "status_code", None)
            if isinstance(sc, int):
                return sc
    # parse from message: "Error code: 429" or "status_code: 429"
    import re
    m = re.search(r"(?:error code|status_code|status)[:\s]+(\d{3})", str(err).lower())
    if m:
        return int(m.group(1))
    return None


def _extract_retry_after(err: Exception) -> Optional[float]:
    """Extract Retry-After header value in seconds."""
    # openai SDK stores headers
    headers = getattr(err, "headers", None) or getattr(err, "response_headers", None)
    if headers:
        ra = None
        if hasattr(headers, "get"):
            ra = headers.get("retry-after") or headers.get("Retry-After")
        elif isinstance(headers, dict):
            ra = headers.get("retry-after") or headers.get("Retry-After")
        if ra is not None:
            try:
                return float(ra)
            except (ValueError, TypeError):
                pass
    return None


# ══════════════════════════════════════════════════════════════
# 3. Retry Delay Calculator (Claw getRetryDelay parity)
# ══════════════════════════════════════════════════════════════

def get_retry_delay(attempt: int, retry_after: Optional[float] = None,
                    max_delay_ms: int = MAX_DELAY_MS) -> float:
    """Calculate retry delay in seconds with exponential backoff + jitter.

    Mirrors Claw's getRetryDelay(attempt, retryAfterHeader, maxDelayMs).

    Args:
        attempt: 1-based attempt number.
        retry_after: Server-provided Retry-After in seconds, or None.
        max_delay_ms: Maximum backoff in milliseconds.

    Returns:
        Delay in seconds.
    """
    if retry_after is not None and retry_after > 0:
        return retry_after

    base_ms = min(BASE_DELAY_MS * math.pow(2, attempt - 1), max_delay_ms)
    jitter_ms = random.random() * 0.25 * base_ms
    return (base_ms + jitter_ms) / 1000.0


def should_retry(error: NanobotAPIError, attempt: int, max_retries: int) -> bool:
    """Determine if an error should be retried.

    Mirrors Claw's shouldRetry + attempt check in withRetry.

    Args:
        error: Classified error.
        attempt: Current 1-based attempt number.
        max_retries: Maximum number of retries allowed.

    Returns:
        True if the error is retryable and we haven't exceeded max retries.
    """
    if attempt > max_retries:
        return False
    return error.is_retryable
