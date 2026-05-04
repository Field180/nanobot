"""
Tests for utils.errors — structured error hierarchy + LLM retry logic.
Verifies Claw errors.ts + withRetry.ts parity: error classification,
retry delay calculation, and should_retry decisions.
"""
import sys
import unittest
from pathlib import Path

WEB_UI = Path(__file__).parent.parent
if str(WEB_UI) not in sys.path:
    sys.path.insert(0, str(WEB_UI))

from utils.errors import (
    classify_llm_error, get_retry_delay, should_retry,
    NanobotAPIError, TransientAPIError, RateLimitError, ContextOverflowError,
    AuthenticationError, InvalidRequestError, ConnectionError_, TimeoutError_,
    ToolCallTypeError, DEFAULT_MAX_RETRIES, BASE_DELAY_MS, MAX_DELAY_MS,
)


# ── Fake SDK exceptions for testing ──────────────────────────

class FakeHTTPError(Exception):
    """Simulates an OpenAI SDK error with status_code attribute."""
    def __init__(self, msg, status_code, headers=None):
        super().__init__(msg)
        self.status_code = status_code
        self.headers = headers or {}


class TestClassifyContextOverflow(unittest.TestCase):
    """Context overflow errors must be classified correctly."""

    def test_prompt_too_long(self):
        err = classify_llm_error(Exception("prompt is too long: 137500 tokens > 135000 maximum"))
        self.assertIsInstance(err, ContextOverflowError)
        self.assertTrue(err.is_retryable)

    def test_context_length_exceeded(self):
        err = classify_llm_error(Exception("context_length_exceeded"))
        self.assertIsInstance(err, ContextOverflowError)

    def test_maximum_context(self):
        err = classify_llm_error(Exception("maximum context length is 4096"))
        self.assertIsInstance(err, ContextOverflowError)

    def test_too_many_tokens(self):
        err = classify_llm_error(Exception("too many tokens in the request"))
        self.assertIsInstance(err, ContextOverflowError)

    def test_exceed_context_limit(self):
        err = classify_llm_error(Exception("input length and max_tokens exceed context limit: 188059 + 20000 > 200000"))
        self.assertIsInstance(err, ContextOverflowError)


class TestClassifyToolCallType(unittest.TestCase):
    """P80 tool call type errors for llama.cpp."""

    def test_missing_tool_call_type(self):
        err = classify_llm_error(Exception("Missing tool call type in response"))
        self.assertIsInstance(err, ToolCallTypeError)
        self.assertTrue(err.is_retryable)

    def test_tool_call_type_keyword(self):
        err = classify_llm_error(Exception("tool_call_type required"))
        self.assertIsInstance(err, ToolCallTypeError)


class TestClassifyHTTPStatus(unittest.TestCase):
    """HTTP status code based classification."""

    def test_429_rate_limit(self):
        err = classify_llm_error(FakeHTTPError("Rate limited", 429))
        self.assertIsInstance(err, RateLimitError)
        self.assertTrue(err.is_retryable)
        self.assertEqual(err.status_code, 429)

    def test_429_with_retry_after(self):
        err = classify_llm_error(FakeHTTPError("Rate limited", 429, {"retry-after": "30"}))
        self.assertIsInstance(err, RateLimitError)
        self.assertEqual(err.retry_after, 30.0)

    def test_401_auth(self):
        err = classify_llm_error(FakeHTTPError("Unauthorized", 401))
        self.assertIsInstance(err, AuthenticationError)
        self.assertFalse(err.is_retryable)

    def test_403_auth(self):
        err = classify_llm_error(FakeHTTPError("Forbidden", 403))
        self.assertIsInstance(err, AuthenticationError)

    def test_400_invalid_request(self):
        err = classify_llm_error(FakeHTTPError("Bad request: invalid model", 400))
        self.assertIsInstance(err, InvalidRequestError)
        self.assertFalse(err.is_retryable)

    def test_500_transient(self):
        err = classify_llm_error(FakeHTTPError("Internal server error", 500))
        self.assertIsInstance(err, TransientAPIError)
        self.assertTrue(err.is_retryable)

    def test_502_transient(self):
        err = classify_llm_error(FakeHTTPError("Bad gateway", 502))
        self.assertIsInstance(err, TransientAPIError)

    def test_529_overloaded(self):
        err = classify_llm_error(FakeHTTPError("Overloaded", 529))
        self.assertIsInstance(err, TransientAPIError)

    def test_408_timeout(self):
        err = classify_llm_error(FakeHTTPError("Request timeout", 408))
        self.assertIsInstance(err, TimeoutError_)
        self.assertTrue(err.is_retryable)


class TestClassifyExceptionType(unittest.TestCase):
    """Exception type name based classification."""

    def test_connection_error(self):
        err = classify_llm_error(ConnectionError("Connection refused"))
        self.assertIsInstance(err, ConnectionError_)
        self.assertTrue(err.is_retryable)

    def test_timeout_error(self):
        err = classify_llm_error(TimeoutError("Request timed out"))
        self.assertIsInstance(err, TimeoutError_)

    def test_connection_refused_in_message(self):
        err = classify_llm_error(Exception("Connection refused by server"))
        self.assertIsInstance(err, ConnectionError_)

    def test_econnreset_in_message(self):
        err = classify_llm_error(Exception("read ECONNRESET"))
        self.assertIsInstance(err, ConnectionError_)

    def test_overloaded_in_message(self):
        err = classify_llm_error(Exception("Service overloaded, try again later"))
        self.assertIsInstance(err, TransientAPIError)

    def test_api_key_in_message(self):
        err = classify_llm_error(Exception("Invalid API key provided"))
        self.assertIsInstance(err, AuthenticationError)

    def test_unknown_error(self):
        err = classify_llm_error(Exception("Something weird happened"))
        self.assertIsInstance(err, NanobotAPIError)
        self.assertFalse(err.is_retryable)
        self.assertEqual(err.error_type, "unknown")


class TestClassifyStatusFromMessage(unittest.TestCase):
    """Status code extraction from error message string."""

    def test_error_code_429(self):
        err = classify_llm_error(Exception("Error code: 429 - Rate limit reached"))
        self.assertIsInstance(err, RateLimitError)

    def test_status_code_500(self):
        err = classify_llm_error(Exception("status_code: 500 internal"))
        self.assertIsInstance(err, TransientAPIError)


class TestRetryDelay(unittest.TestCase):
    """Exponential backoff with jitter (Claw getRetryDelay parity)."""

    def test_increasing_delays(self):
        d1 = get_retry_delay(1)
        d2 = get_retry_delay(2)
        d3 = get_retry_delay(3)
        # Delays should increase (with some jitter tolerance)
        self.assertLess(d1, d2 + 0.5)  # allow small jitter overlap
        self.assertLess(d2, d3 + 1.0)

    def test_first_attempt_base(self):
        d = get_retry_delay(1)
        # Should be around BASE_DELAY_MS/1000 ± 25% jitter
        self.assertGreater(d, BASE_DELAY_MS / 1000 * 0.9)
        self.assertLess(d, BASE_DELAY_MS / 1000 * 1.3)

    def test_capped_at_max(self):
        d = get_retry_delay(100)  # very high attempt
        self.assertLessEqual(d, MAX_DELAY_MS / 1000 * 1.26)  # max + jitter

    def test_retry_after_override(self):
        d = get_retry_delay(1, retry_after=60.0)
        self.assertEqual(d, 60.0)

    def test_retry_after_zero_falls_back(self):
        d = get_retry_delay(1, retry_after=0)
        self.assertGreater(d, 0)  # should use backoff, not 0


class TestShouldRetry(unittest.TestCase):
    """Retry decision logic."""

    def test_retryable_within_limit(self):
        err = TransientAPIError("test", original=None)
        self.assertTrue(should_retry(err, 1, 5))

    def test_retryable_at_limit(self):
        err = TransientAPIError("test", original=None)
        self.assertTrue(should_retry(err, 5, 5))

    def test_retryable_beyond_limit(self):
        err = TransientAPIError("test", original=None)
        self.assertFalse(should_retry(err, 6, 5))

    def test_non_retryable_always_false(self):
        err = AuthenticationError("test", original=None)
        self.assertFalse(should_retry(err, 1, 10))

    def test_rate_limit_retryable(self):
        err = RateLimitError("test", original=None)
        self.assertTrue(should_retry(err, 1, 3))


class TestErrorAttributes(unittest.TestCase):
    """Error objects carry the right metadata."""

    def test_original_preserved(self):
        raw = ValueError("raw error")
        err = classify_llm_error(raw)
        self.assertIs(err.original, raw)

    def test_status_code_preserved(self):
        raw = FakeHTTPError("test", 429)
        err = classify_llm_error(raw)
        self.assertEqual(err.status_code, 429)

    def test_retry_after_preserved(self):
        raw = FakeHTTPError("test", 429, {"retry-after": "15"})
        err = classify_llm_error(raw)
        self.assertEqual(err.retry_after, 15.0)

    def test_error_type_string(self):
        types_map = {
            TransientAPIError: "transient",
            RateLimitError: "rate_limit",
            ContextOverflowError: "context_overflow",
            AuthenticationError: "authentication",
            InvalidRequestError: "invalid_request",
            ConnectionError_: "connection",
            TimeoutError_: "timeout",
            ToolCallTypeError: "tool_call_type",
        }
        for cls, expected_type in types_map.items():
            self.assertEqual(cls.error_type, expected_type)


class TestIntegrationImport(unittest.TestCase):
    """Verify agentic_loop.py can import the error utilities."""

    def test_agentic_loop_imports(self):
        """agentic_loop.py should import without errors."""
        import importlib
        mod = importlib.import_module("agentic_loop")
        # The classify_llm_error should be accessible
        self.assertTrue(hasattr(mod, 'classify_llm_error'))


if __name__ == "__main__":
    unittest.main()
