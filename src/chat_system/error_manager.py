from typing import Callable, Awaitable, Optional

import telegram.error
from google.genai import errors as genai_errors
from huggingface_hub.errors import HfHubHTTPError, InferenceTimeoutError, BadRequestError


UNCLASSIFIED_FIXED_BACKOFF = 10  # seconds -- no escalation, since we have no
                                  # basis to believe repeated unclassified
                                  # errors are correlated with one another


class ResourceBackoffTracker:
    """
    Tracks consecutive-error state and current backoff for ONE specific
    external resource (e.g. network, Telegram API, LLM provider).

    Each tracked resource gets its own instance -- they never share state,
    since failures in one resource shouldn't influence backoff decisions
    for an unrelated one. Intentionally synchronous/pure (no asyncio.sleep
    inside) so it's trivially testable and so "when do we actually wait"
    stays visible at the call site rather than hidden inside this class.
    """

    def __init__(self, base_backoff: float, multiplier: float, max_backoff: float):
        if base_backoff <= 0:
            raise ValueError('base_backoff must be positive')
        if multiplier <= 1:
            raise ValueError('multiplier must be > 1, or backoff will never grow')
        if max_backoff < base_backoff:
            raise ValueError('max_backoff must be >= base_backoff')

        self.base_backoff = base_backoff
        self.multiplier = multiplier
        self.max_backoff = max_backoff
        self.consecutive_errors = 0
        self.current_backoff = base_backoff

    def record_error(self, explicit_backoff: Optional[float] = None) -> float:
        """
        Call on a resource-attributable failure. Returns the backoff (in
        seconds) the caller should sleep for.

        explicit_backoff overrides the computed multiplier formula (e.g.
        Telegram's RetryAfter.retry_after) -- when provided, it's used
        directly (capped at max_backoff) rather than escalating via the
        multiplier, since an authoritative number from the resource
        itself is more reliable than our own heuristic.
        """
        self.consecutive_errors += 1
        if explicit_backoff is not None:
            self.current_backoff = min(explicit_backoff, self.max_backoff)
        else:
            self.current_backoff = min(
                self.base_backoff * (self.multiplier ** (self.consecutive_errors - 1)),
                self.max_backoff,
            )
        return self.current_backoff

    def record_success(self) -> None:
        """
        Call when an operation touching this resource succeeds. Resets
        fully -- a success is the strongest signal we have that this
        resource has recovered.
        """
        self.consecutive_errors = 0
        self.current_backoff = self.base_backoff

    def __repr__(self) -> str:
        return (
            f'ResourceBackoffTracker(consecutive_errors={self.consecutive_errors}, '
            f'current_backoff={self.current_backoff})'
        )


class ErrorClassification:
    """Result of classifying a caught exception."""

    def __init__(self, retryable: bool, backoff_seconds: Optional[float] = None, resource: Optional[str] = None):
        self.retryable = retryable
        self.backoff_seconds = backoff_seconds
        self.resource = resource  # 'network' | 'telegram' | 'llm' | None (unclassified)

    def __repr__(self) -> str:
        return (
            f'ErrorClassification(retryable={self.retryable}, '
            f'backoff_seconds={self.backoff_seconds}, resource={self.resource!r})'
        )


class ErrorManager:
    """
    GLOBAL, process-wide singleton -- create exactly ONCE at application
    startup (e.g. inside ApplicationOrchestrator.__init__), and pass the
    same instance into every UserProcessor. Network/Telegram/LLM outages
    are system-wide, not per-user; sharing this instance means every
    user's failures and successes inform the same backoff state, instead
    of each UserProcessor independently re-discovering the same outage.

    Do NOT instantiate this inside UserProcessor.__init__ or .from_disk --
    doing so would silently create one instance per user, defeating the
    entire purpose of sharing resource-health state across users.
    """

    def __init__(self):
        self.network = ResourceBackoffTracker(base_backoff=5, multiplier=3, max_backoff=30)
        self.telegram = ResourceBackoffTracker(base_backoff=5, multiplier=5, max_backoff=30)
        self.llm = ResourceBackoffTracker(base_backoff=5, multiplier=5, max_backoff=30)

    @staticmethod
    async def _is_connected(timeout: float = 3.0) -> bool:
        """
        Lightweight local-connectivity check, independent of Telegram or
        any other tracked resource -- used to disambiguate "my network is
        down" from "Telegram itself is down" when a NetworkError/TimedOut
        is caught, since PTB's exception types alone can't tell these apart.
        Targets a stable, unrelated host (Google's public DNS) on the DNS
        port, which doesn't require any application-level protocol.
        """
        import asyncio
        
        writer = None
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection('8.8.8.8', 53), timeout=timeout
            )
            return True
        except (OSError, asyncio.TimeoutError):
            return False
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # SEND (Telegram) side -- based on the verified python-telegram-bot
    # v20+ exception hierarchy (telegram.error module):
    #   TelegramError
    #   |-- NetworkError
    #   |     |-- TimedOut
    #   |     |-- BadRequest        (NOT a connectivity issue, despite
    #   |                            being a NetworkError subclass --
    #   |                            content/request problem, never
    #   |                            retryable)
    #   |-- Forbidden                (bot blocked / lacks rights -- never
    #   |                            retryable, not resource-correlated)
    #   |-- RetryAfter               (flood control -- carries explicit
    #                                 .retry_after seconds from Telegram)
    # ------------------------------------------------------------------

    async def classify_send_error(self, e: Exception) -> ErrorClassification:
        """
        Classifies an exception raised while sending via Telegram.
        Also records the error against the appropriate resource tracker
        as a side effect (so the caller doesn't need a second call).
        """
        if isinstance(e, telegram.error.RetryAfter):
            backoff_time = self.telegram.record_error(explicit_backoff=e.retry_after)
            return ErrorClassification(retryable=True, backoff_seconds=backoff_time, resource='telegram')

        # IMPORTANT: BadRequest is a NetworkError subclass in PTB's
        # hierarchy, so this check MUST come before the NetworkError
        # check below, or BadRequest would be incorrectly treated as a
        # resource/connectivity issue.
        if isinstance(e, telegram.error.BadRequest):
            return ErrorClassification(retryable=False, resource=None)

        if isinstance(e, telegram.error.Forbidden):
            return ErrorClassification(retryable=False, resource=None)

        if isinstance(e, (telegram.error.TimedOut, telegram.error.NetworkError)):
            if await ErrorManager._is_connected():
                backoff_time = self.telegram.record_error()
                return ErrorClassification(retryable=True, backoff_seconds=backoff_time, resource='telegram')
            else:
                backoff_time = self.network.record_error()
                return ErrorClassification(retryable=True, backoff_seconds=backoff_time, resource='network')

        # Unclassified -- some other TelegramError subclass (Conflict,
        # InvalidToken, ChatMigrated, EndPointNotFound, etc.) or a
        # completely unrelated exception. No basis to assume this
        # correlates with other failures, so: fixed backoff, no tracker.
        return ErrorClassification(retryable=True, backoff_seconds=UNCLASSIFIED_FIXED_BACKOFF, resource=None)

    def record_send_success(self) -> None:
        """
        Call after a successful send. Resets BOTH network and telegram
        trackers -- a successful send necessarily exercised both the
        local network path and Telegram's API in one shot, so success is
        valid evidence both have recovered.
        """
        self.telegram.record_success()
        self.network.record_success()

    # ------------------------------------------------------------------
    # PROCESS (LLM) side -- covers both backends used by LLMModel:
    #
    # google-genai (Gemini), verified hierarchy:
    #   APIError (base, carries .code = HTTP status, .message)
    #   |-- ClientError   (4xx -- e.g. 429 RESOURCE_EXHAUSTED, 401
    #   |                  UNAUTHENTICATED, 400 INVALID_ARGUMENT)
    #   |-- ServerError   (5xx -- e.g. 500 INTERNAL, 503)
    #   NOTE: this is a SEPARATE hierarchy from google.api_core.exceptions
    #   -- no inheritance relationship between them, so api_core exception
    #   names (ResourceExhausted, ServiceUnavailable, etc.) do NOT apply
    #   to errors raised by the genai.Client used here.
    #
    # huggingface_hub (InferenceClient), verified hierarchy:
    #   HfHubHTTPError (base for HTTP errors; also subclasses OSError)
    #   |-- BadRequestError   (4xx request-content problems)
    #   InferenceTimeoutError (separate from HfHubHTTPError -- raised on
    #                          request timeout, not an HTTP status error)
    # ------------------------------------------------------------------

    async def classify_process_error(self, e: Exception) -> ErrorClassification:
        """
        Classifies an exception raised while generating a reply via
        LLMModel.run(). Also records the error against the appropriate
        resource tracker as a side effect (so the caller doesn't need a
        second call). Handles both Gemini (google-genai) and HuggingFace
        (huggingface_hub) backends, since LLMModel can wrap either.
        """
        # ---- Gemini (google-genai) ----
        if isinstance(e, genai_errors.ServerError):
            # 5xx -- Gemini's own infrastructure struggling. Resource-correlated.
            backoff_time = self.llm.record_error()
            return ErrorClassification(retryable=True, backoff_seconds=backoff_time, resource='llm')

        if isinstance(e, genai_errors.ClientError):
            code = getattr(e, 'code', None)
            if code == 429:
                # Rate limit -- still resource-correlated (the LLM provider
                # is the resource being exhausted), but not a connectivity
                # issue, so it goes to the llm tracker, not network.
                backoff_time = self.llm.record_error()
                return ErrorClassification(retryable=True, backoff_seconds=backoff_time, resource='llm')
            # 400 INVALID_ARGUMENT, 401 UNAUTHENTICATED, 404, etc. --
            # request/auth/content problems. Retrying the identical
            # request will fail identically every time.
            return ErrorClassification(retryable=False, resource=None)

        # ---- HuggingFace (huggingface_hub) ----
        if isinstance(e, InferenceTimeoutError):
            if await ErrorManager._is_connected():
                backoff_time = self.llm.record_error()
                return ErrorClassification(retryable=True, backoff_seconds=backoff_time, resource='llm')
            else:
                backoff_time = self.network.record_error()
                return ErrorClassification(retryable=True, backoff_seconds=backoff_time, resource='network')

        if isinstance(e, BadRequestError):
            # Malformed request/payload -- not retryable, not resource-correlated.
            return ErrorClassification(retryable=False, resource=None)

        if isinstance(e, HfHubHTTPError):
            status_code = None
            response = getattr(e, 'response', None)
            if response is not None:
                status_code = getattr(response, 'status_code', None)

            if status_code == 503:
                # Model loading / temporarily unavailable on HF's side --
                # resource-correlated, retryable.
                backoff_time = self.llm.record_error()
                return ErrorClassification(retryable=True, backoff_seconds=backoff_time, resource='llm')
            if status_code is not None and 500 <= status_code < 600:
                backoff_time = self.llm.record_error()
                return ErrorClassification(retryable=True, backoff_seconds=backoff_time, resource='llm')
            if status_code == 429:
                backoff_time = self.llm.record_error()
                return ErrorClassification(retryable=True, backoff_seconds=backoff_time, resource='llm')
            # Other 4xx (401, 404, 422, etc.) -- request/auth/routing
            # problems, not retryable.
            return ErrorClassification(retryable=False, resource=None)

        # Unclassified -- network-layer exceptions not wrapped by either
        # SDK (e.g. raw OSError/ConnectionError bubbling up), or anything
        # else unanticipated. No basis to assume correlation, so: fixed
        # backoff, no tracker touched.
        return ErrorClassification(retryable=True, backoff_seconds=UNCLASSIFIED_FIXED_BACKOFF, resource=None)

    def record_process_success(self) -> None:
        """
        Call after a successful LLM call. Resets both the llm tracker and
        the network tracker -- a successful call necessarily exercised
        both the network path and the LLM provider in one shot (for the
        HuggingFace/Gemini hosted APIs; if LLMModel is ever pointed at a
        fully local model with no network call involved, resetting
        network here is harmless -- it just means one extra, no-op reset).
        """
        self.llm.record_success()
        self.network.record_success()