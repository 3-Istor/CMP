"""
Model calls through the AI gateway, and the test chat of an agent.

The CMP holds no provider key: it calls the gateway's Anthropic-shaped
``/v1/messages`` route and the project's ``/mcp/<project>`` route with a
gateway token (the user's, exchanged for the agent, or the CMP's own client
for a draft). Budgets, tool rights, masking and traces are the gateway's, so
a test chat behaves as the agent does in production, steps included.

The loop is plain HTTP on purpose: the gateway (and the trial's stub) speak
the Messages API wire format, and the CMP must not depend on a provider SDK
to reach a route it does not own.
"""

import json
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.core.config import settings
from app.services.ai import usage as ai_usage

logger = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 3,
}
WEB_SEARCH_USD = 0.01
RESULT_SHOWN = 4000
RESULT_TO_MODEL = 20_000
CODE_TOOL = "run_python"
TIMEOUT = httpx.Timeout(120.0, connect=5.0)
BUDGET_MARKERS = ("ai_budget_exhausted", "budget")
REFUSAL_HEADER = "x-cnp-refusal"


class GatewayRefusal(Exception):
    """The gateway refused the call: budget, policy or rate limit."""

    def __init__(self, reason: str, status: int, message: str) -> None:
        self.reason = reason
        self.status = status
        super().__init__(message)


class GatewayError(RuntimeError):
    """The gateway or the model failed (not a refusal)."""


def refusal_of(
    status: int, body: str, marker: str | None = None
) -> GatewayRefusal | None:
    """The gateway names its own refusals (header x-cnp-refusal); a 429
    without it is the rate limiter."""
    if marker in ("budget", "policy"):
        return GatewayRefusal(marker, status, _message(body))
    if status == 429:
        text = body.lower()
        reason = (
            "budget" if any(m in text for m in BUDGET_MARKERS) else "ratelimit"
        )
        return GatewayRefusal(reason, status, _message(body))
    if status in (401, 403):
        return GatewayRefusal("policy", status, _message(body))
    return None


def _message(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body[:300] or "refused"
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or body)[:300]
        return str(data.get("message") or error or body)[:300]
    return body[:300]


def traceparent() -> tuple[str, str]:
    trace_id = secrets.token_hex(16)
    return trace_id, f"00-{trace_id}-{secrets.token_hex(8)}-01"


def _headers(token: str, trace: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    if trace:
        headers["traceparent"] = trace
    return headers


async def complete(token: str, body: dict) -> dict:
    """
    One non-streaming Messages call.

    Raises:
        GatewayRefusal: Budget, policy or rate limit.
        GatewayError: Anything else.
    """
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{settings.AI_GATEWAY_URL}/v1/messages",
                headers=_headers(token),
                json=body,
            )
    except httpx.HTTPError as exc:
        raise GatewayError(f"AI gateway unreachable: {exc}") from exc
    refused = refusal_of(
        response.status_code,
        response.text,
        response.headers.get(REFUSAL_HEADER),
    )
    if refused:
        raise refused
    if response.status_code >= 400:
        raise GatewayError(
            f"AI gateway answered {response.status_code}: "
            f"{_message(response.text)}"
        )
    return response.json()


async def reachable() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.get(f"{settings.AI_GATEWAY_URL}/v1/messages")
    except httpx.HTTPError:
        return False
    return True


# ---------------------------------------------------------------------------
# Test chat
# ---------------------------------------------------------------------------


@dataclass
class TestPlan:  # pylint: disable=too-many-instance-attributes
    project: str
    agent: str
    model: str
    effort: str
    instructions: str
    tools: list[str]
    max_steps: int
    output: dict | None
    artifact_base: str


@dataclass
class Totals:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Totals") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cost_usd += other.cost_usd

    def view(self, usd_per_eur: float | None = None) -> dict[str, Any]:
        view: dict[str, Any] = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }
        if usd_per_eur:
            view["cost_eur"] = round(self.cost_usd / usd_per_eur, 6)
        return view


@dataclass
class Turn:
    """One streamed model response, rebuilt block by block."""

    blocks: dict[int, dict] = field(default_factory=dict)
    partial: dict[int, str] = field(default_factory=dict)
    stop_reason: str | None = None
    usage: dict = field(default_factory=dict)

    def content(self) -> list[dict]:
        return [self.blocks[i] for i in sorted(self.blocks)]


def _text_of(result: Any) -> str:
    parts = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(text)
    if not parts and getattr(result, "structuredContent", None) is not None:
        parts.append(json.dumps(result.structuredContent))
    return "\n".join(parts)


def _code_result(result: Any, text: str) -> dict:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if isinstance(structured.get("result"), dict):
            return structured["result"]
        return structured
    try:
        parsed = json.loads(text)
    except ValueError:
        return {"stdout": text}
    return parsed if isinstance(parsed, dict) else {"stdout": text}


class Tools:
    """The agent's MCP tools on the gateway, under their declared names."""

    def __init__(self) -> None:
        self.stack = AsyncExitStack()
        self.session: ClientSession | None = None
        self.names: dict[str, str] = {}
        self.specs: list[dict] = []

    async def open(self, project: str, token: str, wanted: list[str]) -> None:
        client = await self.stack.enter_async_context(
            httpx.AsyncClient(
                headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT
            )
        )
        read, write, _ = await self.stack.enter_async_context(
            streamable_http_client(
                f"{settings.AI_GATEWAY_URL}/mcp/{project}", http_client=client
            )
        )
        self.session = await self.stack.enter_async_context(
            ClientSession(read, write)
        )
        await self.session.initialize()
        listed = (await self.session.list_tools()).tools
        for name in wanted:
            match = next(
                (
                    t
                    for t in listed
                    if t.name == name or t.name.endswith(f"__{name}")
                ),
                None,
            )
            if match is None:
                continue
            self.names[name] = match.name
            self.specs.append(
                {
                    "name": name,
                    "description": (match.description or "")[:1024],
                    "input_schema": match.inputSchema
                    or {"type": "object", "properties": {}},
                }
            )

    async def call(self, name: str, arguments: dict) -> Any:
        assert self.session is not None
        return await self.session.call_tool(self.names[name], arguments)

    async def close(self) -> None:
        await self.stack.aclose()


def _is_http_status(
    exc: BaseException, statuses: tuple[int, ...]
) -> int | None:
    found = [exc]
    while found:
        current = found.pop()
        if isinstance(current, httpx.HTTPStatusError):
            if current.response.status_code in statuses:
                return current.response.status_code
        found.extend(getattr(current, "exceptions", ()) or ())
        if current.__cause__ is not None:
            found.append(current.__cause__)
    return None


async def _stream(
    client: httpx.AsyncClient, token: str, trace: str, body: dict
) -> AsyncIterator[tuple[str, Any]]:
    """Yields ("text", str) while streaming, then ("turn", Turn)."""
    turn = Turn()
    async with client.stream(
        "POST",
        f"{settings.AI_GATEWAY_URL}/v1/messages",
        headers=_headers(token, trace),
        json=body,
    ) as response:
        if response.status_code >= 400:
            text = (await response.aread()).decode(errors="replace")
            refused = refusal_of(
                response.status_code,
                text,
                response.headers.get(REFUSAL_HEADER),
            )
            if refused:
                raise refused
            raise GatewayError(
                f"AI gateway answered {response.status_code}: {_message(text)}"
            )
        event = ""
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            data = json.loads(line[5:].strip() or "{}")
            kind = data.get("type", event)
            if kind == "error":
                raise GatewayError(_message(json.dumps(data)))
            if kind == "message_start":
                turn.usage.update(data["message"].get("usage") or {})
            elif kind == "content_block_start":
                turn.blocks[data["index"]] = dict(data["content_block"])
            elif kind == "content_block_delta":
                block = turn.blocks[data["index"]]
                delta = data["delta"]
                if delta["type"] == "text_delta":
                    block["text"] = block.get("text", "") + delta["text"]
                    yield "text", delta["text"]
                elif delta["type"] == "input_json_delta":
                    turn.partial[data["index"]] = (
                        turn.partial.get(data["index"], "")
                        + delta["partial_json"]
                    )
                elif delta["type"] == "thinking_delta":
                    block["thinking"] = (
                        block.get("thinking", "") + delta["thinking"]
                    )
                elif delta["type"] == "signature_delta":
                    block["signature"] = delta["signature"]
                elif delta["type"] == "citations_delta":
                    block.setdefault("citations", []).append(delta["citation"])
            elif kind == "content_block_stop":
                index = data["index"]
                if index in turn.partial:
                    raw = turn.partial.pop(index)
                    turn.blocks[index]["input"] = (
                        json.loads(raw) if raw else {}
                    )
            elif kind == "message_delta":
                turn.stop_reason = data["delta"].get("stop_reason")
                turn.usage.update(data.get("usage") or {})
    yield "turn", turn


def _turn_cost(model: str, usage: dict) -> Totals:
    tokens_in = int(usage.get("input_tokens") or 0)
    tokens_out = int(usage.get("output_tokens") or 0)
    cache = int(usage.get("cache_read_input_tokens") or 0)
    searches = int(
        (usage.get("server_tool_use") or {}).get("web_search_requests") or 0
    )
    return Totals(
        tokens_in,
        tokens_out,
        cache,
        ai_usage.cost_usd(model, tokens_in, tokens_out, cache)
        + searches * WEB_SEARCH_USD,
    )


def _search_summary(block: dict) -> tuple[str, bool]:
    content = block.get("content")
    if isinstance(content, dict):
        return f"Erreur : {content.get('error_code', 'inconnue')}", True
    lines = [
        f"{item.get('title', '')} {item.get('url', '')}".strip()
        for item in content or []
        if isinstance(item, dict)
    ]
    return "\n".join(lines)[:RESULT_SHOWN], False


async def run_test(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    plan: TestPlan,
    token: str,
    message: str,
    history: list[dict],
    usd_per_eur: float,
) -> AsyncIterator[tuple[str, dict]]:
    """The agent's loop on one user message, as (event, payload) steps."""
    started = time.monotonic()
    trace_id, trace = traceparent()
    totals = Totals()
    tools = Tools()
    steps = 0

    def done(stop: str, error: str | None = None) -> tuple[str, dict]:
        return "done", {
            "stop_reason": stop,
            "steps": steps,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "usage": totals.view(usd_per_eur),
            "trace_id": trace_id,
            "message": error,
        }

    client_tools = [t for t in plan.tools if t != "web_search"]
    try:
        if client_tools:
            try:
                await tools.open(plan.project, token, client_tools)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                status = _is_http_status(exc, (401, 403, 429))
                if status is None:
                    logger.warning("Test chat MCP failed: %r", exc)
                    yield done(
                        "error", "Les outils de l'agent sont injoignables."
                    )
                    return
                yield "refusal", {
                    "step": 0,
                    "reason": "ratelimit" if status == 429 else "policy",
                    "status": status,
                    "message": "La gateway refuse l'accès aux outils de l'agent.",
                }
                yield done("refusal")
                return
        offered: list[dict] = list(tools.specs)
        if "web_search" in plan.tools:
            offered.append(dict(WEB_SEARCH_TOOL))
        messages: list[dict] = [
            {"role": m["role"], "content": m["content"]} for m in history
        ]
        messages.append({"role": "user", "content": message})
        output_config: dict[str, Any] = {"effort": plan.effort}
        if plan.output and plan.output.get("schema"):
            output_config["format"] = {
                "type": "json_schema",
                "schema": plan.output["schema"],
            }

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            while steps < plan.max_steps:
                steps += 1
                body: dict[str, Any] = {
                    "model": plan.model,
                    "max_tokens": settings.AI_TEST_MAX_TOKENS,
                    "system": plan.instructions,
                    "messages": messages,
                    "output_config": output_config,
                    "stream": True,
                }
                if offered:
                    body["tools"] = offered
                yield "model_call", {
                    "step": steps,
                    "model": plan.model,
                    "effort": plan.effort,
                    "max_tokens": settings.AI_TEST_MAX_TOKENS,
                }
                turn: Turn | None = None
                try:
                    async for kind, value in _stream(
                        client, token, trace, body
                    ):
                        if kind == "text":
                            yield "text_delta", {"step": steps, "text": value}
                        else:
                            turn = value
                except GatewayRefusal as refused:
                    yield "refusal", {
                        "step": steps,
                        "reason": refused.reason,
                        "status": refused.status,
                        "message": str(refused),
                    }
                    yield done("refusal")
                    return
                assert turn is not None
                cost = _turn_cost(plan.model, turn.usage)
                totals.add(cost)
                yield "usage", {
                    "step": steps,
                    **cost.view(),
                    "total": totals.view(usd_per_eur),
                }
                for block in turn.content():
                    if block.get("type") == "server_tool_use":
                        yield "tool_call", {
                            "step": steps,
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "input": block.get("input") or {},
                            "server": True,
                        }
                    elif block.get("type") == "web_search_tool_result":
                        text, failed = _search_summary(block)
                        yield "tool_result", {
                            "step": steps,
                            "id": block.get("tool_use_id"),
                            "name": "web_search",
                            "is_error": failed,
                            "content": text,
                            "duration_ms": None,
                        }
                if turn.stop_reason == "refusal":
                    yield "refusal", {
                        "step": steps,
                        "reason": "guardrail",
                        "status": 200,
                        "message": "Le modèle a refusé de répondre.",
                    }
                    yield done("refusal")
                    return
                messages.append(
                    {"role": "assistant", "content": turn.content()}
                )
                if turn.stop_reason == "pause_turn":
                    continue
                if turn.stop_reason != "tool_use":
                    yield done("end_turn")
                    return
                results = []
                for block in turn.content():
                    if block.get("type") != "tool_use":
                        continue
                    async for event in _run_tool(
                        plan, tools, block, steps, results
                    ):
                        yield event
                messages.append({"role": "user", "content": results})
        yield done("max_steps")
    except GatewayError as exc:
        yield done("error", str(exc))
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning(
            "Test chat of %s/%s failed: %r", plan.project, plan.agent, exc
        )
        yield done("error", "La gateway IA a répondu de façon inattendue.")
    finally:
        await tools.close()


async def _run_tool(
    plan: TestPlan, tools: Tools, block: dict, step: int, results: list
) -> AsyncIterator[tuple[str, dict]]:
    name = block.get("name", "")
    arguments = block.get("input") or {}
    yield "tool_call", {
        "step": step,
        "id": block.get("id"),
        "name": name,
        "input": arguments,
        "server": False,
    }
    started = time.monotonic()
    failed = False
    result = None
    if name not in tools.names:
        text, failed = f"Outil inconnu : {name}", True
    else:
        try:
            result = await tools.call(name, arguments)
            text = _text_of(result)
            failed = bool(getattr(result, "isError", False))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            status = _is_http_status(exc, (401, 403, 429))
            text, failed = (
                (
                    f"Refusé par la gateway ({status})."
                    if status
                    else f"Échec de l'outil : {type(exc).__name__}"
                ),
                True,
            )
            if status:
                yield "refusal", {
                    "step": step,
                    "reason": "ratelimit" if status == 429 else "policy",
                    "status": status,
                    "message": f"La gateway refuse l'outil {name}.",
                }
    duration = int((time.monotonic() - started) * 1000)
    if name == CODE_TOOL and result is not None and not failed:
        outcome = _code_result(result, text)
        yield "sandbox_run", {
            "step": step,
            "id": block.get("id"),
            "code": str(arguments.get("code", "")),
            "stdout": str(outcome.get("stdout", ""))[:RESULT_SHOWN],
            "stderr": str(outcome.get("stderr", ""))[:RESULT_SHOWN],
            "exit_code": outcome.get("exit_code"),
            "duration_ms": duration,
        }
        for artifact in outcome.get("artifacts") or []:
            url = str(artifact.get("url", ""))
            relative = url.split("/ai-artifacts/", 1)[-1]
            yield "artifact", {
                "step": step,
                "name": artifact.get("name"),
                "mime": artifact.get("mime"),
                "url": f"{plan.artifact_base}/{relative}",
            }
    yield "tool_result", {
        "step": step,
        "id": block.get("id"),
        "name": name,
        "is_error": failed,
        "content": text[:RESULT_SHOWN],
        "duration_ms": duration,
    }
    results.append(
        {
            "type": "tool_result",
            "tool_use_id": block.get("id"),
            "content": text[:RESULT_TO_MODEL],
            "is_error": failed,
        }
    )
