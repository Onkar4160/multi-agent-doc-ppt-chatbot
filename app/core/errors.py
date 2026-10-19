"""Error taxonomy: failures the API and chat can explain to the user (ARCHITECTURE §9).

LLM-specific failures live in ``app.llm.errors``; everything else derives from ``AppError``. Each error carries an
HTTP status and a plain-language message that is safe to show (no stack traces, no secrets).
"""

from __future__ import annotations


class AppError(Exception):
    """Base class: ``message`` is shown to the user as-is."""

    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UserInputError(AppError):
    """The request cannot be carried out as given (missing input, invalid option)."""


class UnsupportedFileError(AppError):
    """An upload is of a type, structure or size the platform refuses."""

    status_code = 415


class NotFoundError(AppError):
    """A record does not exist or belongs to another user (both look the same to the caller)."""

    status_code = 404


class TemplateError(AppError):
    """A template could not be analysed or applied."""

    status_code = 422


class ResearchError(AppError):
    """A research provider failed; generation continues with the gap reported."""

    status_code = 502


class RenderError(AppError):
    """A file could not be rendered from its content model."""

    status_code = 500


class BudgetExceeded(AppError):
    """A run used up its LLM-call or search budget."""

    status_code = 429
