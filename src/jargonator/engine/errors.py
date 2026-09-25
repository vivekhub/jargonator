"""Errors the Slack adapters show to the user (ephemerally)."""


class UserFacingError(Exception):
    """A friendly, user-facing refusal (e.g. "Only the host can do that.")."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
