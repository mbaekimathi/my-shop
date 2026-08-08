"""Lightweight cache store for communications (read-state only).

Replies and contacts are live-fetched from WhatsApp via the bridge.
"""

from __future__ import annotations

from typing import Any

from django.core.cache import cache

# Read/unread markers only — not message bodies.
TTL_SECONDS = 60 * 60 * 24 * 14
READ_STATE_KEY = "comms:reply_read_state:v1"
OUTBOUND_INDEX_KEY = "comms:outbound_index:v1"
OUTBOUND_INDEX_TTL = 20


def get_read_state() -> dict[str, str]:
    """phone -> ISO timestamp of last mark-read."""
    data = cache.get(READ_STATE_KEY)
    return dict(data) if isinstance(data, dict) else {}


def set_read_state(state: dict[str, str]) -> None:
    cache.set(READ_STATE_KEY, state, timeout=TTL_SECONDS)


def mark_phones_read(phones: list[str], *, when_iso: str) -> None:
    if not phones:
        return
    state = get_read_state()
    for phone in phones:
        key = str(phone or "").strip()
        if key:
            state[key] = when_iso
    set_read_state(state)


def get_cached_outbound_index() -> list[dict[str, Any]] | None:
    data = cache.get(OUTBOUND_INDEX_KEY)
    return data if isinstance(data, list) else None


def set_cached_outbound_index(rows: list[dict[str, Any]]) -> None:
    cache.set(OUTBOUND_INDEX_KEY, rows, timeout=OUTBOUND_INDEX_TTL)


def bust_outbound_index() -> None:
    cache.delete(OUTBOUND_INDEX_KEY)
