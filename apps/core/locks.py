"""PostgreSQL advisory lock utility for management commands.

Provides a context manager that acquires a PostgreSQL advisory lock to prevent
concurrent execution of the same management command. Falls back to a no-op on
non-PostgreSQL databases (e.g. SQLite in tests) so that the test suite runs
without requiring a Postgres instance.
"""

from __future__ import annotations

import hashlib
import logging
import struct
from collections.abc import Generator
from contextlib import contextmanager

from django.db import connection

logger = logging.getLogger(__name__)


def _is_postgresql() -> bool:
    """Return True if the default database engine is PostgreSQL."""
    engine = connection.settings_dict.get("ENGINE", "")
    return "postgresql" in engine or "postgis" in engine


def _name_to_lock_id(name: str) -> int:
    """Convert a lock name string to a stable int8 for ``pg_try_advisory_lock``.

    Uses ``hashlib.blake2b`` (deterministic, no Python hash randomization)
    and maps the digest to a signed 64-bit integer that PostgreSQL accepts
    for advisory locks.
    """
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
    (unsigned,) = struct.unpack(">q", digest)
    return unsigned


@contextmanager
def advisory_lock(name: str) -> Generator[bool, None, None]:
    """Acquire a PostgreSQL advisory lock for the duration of the block.

    Returns ``True`` if the lock was acquired, ``False`` if another process
    holds it. On non-PostgreSQL databases the lock is always considered
    acquired (no-op).

    Usage::

        with advisory_lock("send_campaign_42") as acquired:
            if not acquired:
                print("Another instance is running.")
                return
            # ... do work ...
    """
    if not _is_postgresql():
        yield True
        return

    lock_id = _name_to_lock_id(name)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
        acquired = cursor.fetchone()[0]

        try:
            yield acquired
        finally:
            if acquired:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
