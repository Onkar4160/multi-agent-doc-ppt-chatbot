"""LLM failure types and plain-language error replies (ported from DocForge ``orchestrator/generate.py``)."""

from __future__ import annotations

from app.core.errors import AppError, BudgetExceeded


class LLMError(RuntimeError):
    """Base class for LLM failures the chat can explain to the user."""


class LLMRateLimited(LLMError):
    """Every model in the chain is out of quota (``quota=True``) or the service is busy."""

    def __init__(
        self, message: str, *, quota: bool = False, daily: bool = False, retry_after_s: float | None = None
    ) -> None:
        super().__init__(message)
        self.quota = quota
        self.daily = daily
        self.retry_after_s = retry_after_s


class LLMModelUnavailable(LLMError):
    """The configured model does not exist for this API key (HTTP 404)."""


class LLMAuthError(LLMError):
    """The provider refused the API key or its project (HTTP 401/403); no other model can help."""

    def __init__(self, message: str, provider_message: str = "") -> None:
        super().__init__(message)
        self.provider_message = provider_message


def friendly_error(exc: Exception, doing: str) -> str:
    """Plain-language failure message: what happened, whether anything was delivered, what to do next."""
    text = str(exc)
    if isinstance(exc, LLMRateLimited):
        if exc.quota or exc.daily:
            reason = (
                "the AI model's usage limit is reached (the Gemini free tier allows only about 20 requests "
                "per model per day, and every configured model is used up)"
            )
            tip = "Please try again later, or enable billing on the Gemini API key to lift the limit."
        else:
            reason = "the AI service is busy (too many requests per minute)"
            tip = "Please try again in a minute."
    elif isinstance(exc, LLMAuthError):
        said = f" (Google says: “{exc.provider_message}”)" if exc.provider_message else ""
        reason = f"the AI provider refused the API key{said}"
        tip = "Check GEMINI_API_KEY and its Google Cloud project, then restart the server."
    elif isinstance(exc, LLMModelUnavailable):
        reason = "the configured AI model is not available to this API key"
        tip = "Set GEMINI_MODEL in .env to a current model (for example gemini-3.8-flash)."
    elif isinstance(exc, BudgetExceeded):
        reason = exc.message
        tip = "Ask for a shorter deliverable, or raise LLM_MAX_CALLS_PER_RUN in .env."
    elif isinstance(exc, AppError):
        reason = exc.message
        tip = "Say “try again” to retry."
    elif "API key" in text or "API_KEY" in text or "PERMISSION_DENIED" in text or "UNAUTHENTICATED" in text:
        reason = "the AI provider rejected the API key"
        tip = "Check GEMINI_API_KEY in .env and restart the server."
    elif "SAFETY" in text or "blocked" in text.lower():
        reason = "the AI provider's safety filter declined this content"
        tip = "Try rephrasing the request."
    elif isinstance(exc, (ValueError,)) and "validation error" in text:
        reason = "the AI model returned an unusable answer"
        tip = "Say “try again”; it usually works on a second attempt."
    else:
        reason = f"something unexpected went wrong on my side ({exc.__class__.__name__})"
        tip = "Say “try again” to retry; if it keeps failing, the server log has the details."
    return f"I couldn't {doing}: {reason}. Nothing was delivered. {tip}"
