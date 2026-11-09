"""Errors raised by the library."""

from __future__ import annotations


class Reject(Exception):
    """A message or record that must not be acted on.

    ``reason`` is a short, stable phrase (``"bad signature"``, ``"expired"``, ``"replayed"``,
    ...) safe to log and to send back to the sender; ``detail`` is extra context.
    """

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
