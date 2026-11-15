"""
An app's logs in VictoriaLogs (D6): its most frequent errors grouped by
pattern, and a bounded search.

Fluent Bit stores the app's own JSON under json.* (K3s#204): the level in
json.level, the trace in json.trace_id when the app writes one (the
templates do). The browser picks a range, a level and a phrase; the phrase
reaches LogsQL only as a quoted string, the rest from fixed lists.
"""

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.services.observability import stores

RANGES = ("15m", "1h", "6h", "24h", "7d")
LEVELS = {
    "error": "err|error|fatal|critical|panic",
    "warn": "warn|warning",
    "info": "info|notice",
    "debug": "debug|trace",
}
# A line without a level field counts as an error on these words.
ERROR_WORDS = r"\b(error|fatal|panic|exception|traceback)\b"
TRACE_ID = re.compile(r"^[0-9a-f]{16,32}$")
MAX_PHRASE = 200
SEARCH_FIELDS = "_time, _msg, pod, container, json.level, json.trace_id"


def phrase(text: str) -> str:
    """A LogsQL quoted phrase; JSON escaping is LogsQL's string escaping."""
    if not text or len(text) > MAX_PHRASE:
        raise ValueError("A search is 1 to 200 characters.")
    if any(ord(c) < 32 for c in text):
        raise ValueError("Control characters are not allowed.")
    return json.dumps(text, ensure_ascii=False)


def level_filter(level: str) -> str:
    by_field = "json.level:~" + json.dumps(f"(?i)^({LEVELS[level]})$")
    if level != "error":
        return by_field
    words = json.dumps(f"(?i){ERROR_WORDS}")
    return f'({by_field} or (json.level:"" ~{words}))'


def top_errors_query(range_name: str, limit: int) -> str:
    return (
        f"_time:{range_name} {level_filter('error')}"
        " | collapse_nums prettify"
        " | stats by (_msg) count() hits, max(json.trace_id) trace_id,"
        " max(_time) last_seen"
        f" | sort by (hits desc) | limit {limit}"
    )


def search_query(
    range_name: str,
    level: str | None,
    text: str | None,
    trace_id: str | None,
    limit: int,
) -> str:
    parts = [f"_time:{range_name}"]
    if level:
        parts.append(level_filter(level))
    if text:
        parts.append(phrase(text))
    if trace_id:
        parts.append(f'json.trace_id:="{trace_id}"')
    return (
        " ".join(parts)
        + f" | sort by (_time desc) | limit {limit}"
        + f" | fields {SEARCH_FIELDS}"
    )


def _when(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _trace(raw: Any) -> str | None:
    value = str(raw or "").lower()
    return value if TRACE_ID.match(value) else None


async def top_errors(
    scope: stores.Scope, range_name: str, limit: int
) -> list[dict[str, Any]]:
    rows = await stores.logs(
        scope.namespace, top_errors_query(range_name, limit), limit
    )
    return [
        {
            "pattern": row.get("_msg", ""),
            "count": int(row.get("hits") or 0),
            "trace_id": _trace(row.get("trace_id")),
            "last_seen": _when(row.get("last_seen")),
        }
        for row in rows
    ]


async def search(  # pylint: disable=too-many-arguments
    scope: stores.Scope,
    range_name: str,
    level: str | None,
    text: str | None,
    trace_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    rows = await stores.logs(
        scope.namespace,
        search_query(range_name, level, text, trace_id, limit),
        limit,
    )
    return [
        {
            "time": _when(row.get("_time")) or datetime.now(timezone.utc),
            "message": row.get("_msg", ""),
            "pod": row.get("pod", ""),
            "container": row.get("container", ""),
            "level": (row.get("json.level") or "").lower() or None,
            "trace_id": _trace(row.get("json.trace_id")),
        }
        for row in rows
    ]
