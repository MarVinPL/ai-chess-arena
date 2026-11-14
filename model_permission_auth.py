"""Fail-closed identity adapter for model-permission mutations.

A future authentication middleware may set _arena_verified_actor only
after validating an application session or an equivalent trusted credential.
HTTP headers, JSON fields, the shared Caddy Basic Auth account, and Unix
service-account names are deliberately not identity sources.
"""
from __future__ import annotations

import re

_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:-]{0,127}$")


def authenticated_actor(handler) -> str | None:
    """Return only an identity attached by trusted in-process middleware."""
    actor = getattr(handler, "_arena_verified_actor", None)
    if not isinstance(actor, str) or not _ACTOR.fullmatch(actor):
        return None
    return actor
