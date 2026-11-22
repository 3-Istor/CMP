"""The test chat loop, usage from VictoriaMetrics and the agent draft, with
the gateway and the stores replaced by recorded answers."""

import asyncio
import json
from types import SimpleNamespace
from unittest import mock

import httpx
import pytest

from app.services.ai import draft, gateway, identity
from app.services.ai import usage as ai_usage

PLAN = gateway.TestPlan(
    project="alpha",
    agent="analyst",
    model="claude-haiku-5-5",
    effort="low",
    instructions="Réponds avec des chiffres.",
    tools=["run_python", "web_search"],
    max_steps=4,
    output=None,
    artifact_base="/api/projects/alpha/agents/analyst/artifacts",
)


def _sse(*events: dict) -> bytes:
    return b"".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode()
        for e in events
    )


def _message(
    blocks: list[dict], stop: str, tokens_in=100, tokens_out=20
) -> bytes:
    events = [
        {
            "type": "message_start",
            "message": {
                "usage": {"input_tokens": tokens_in, "output_tokens": 1}
            },
        }
    ]
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            events += [
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": block["text"]},
                },
            ]
        else:
            events += [
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {**block, "input": {}},
                },
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(block["input"]),
                    },
                },
            ]
        events.append({"type": "content_block_stop", "index": index})
    events += [
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop},
            "usage": {"output_tokens": tokens_out},
        },
        {"type": "message_stop"},
    ]
    return _sse(*events)


class FakeTools:
    def __init__(self):
        self.names = {}
        self.specs = []
        self.calls = []

    async def open(self, project, token, wanted):
        self.names = {"run_python": "run-python__run_python"}
        self.specs = [
            {"name": "run_python", "description": "", "input_schema": {}}
        ]

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        outcome = {
            "stdout": "42\n",
            "stderr": "",
            "exit_code": 0,
            "artifacts": [
                {
                    "name": "chart.png",
                    "mime": "image/png",
                    "url": "/ai-artifacts/t1/chart.png",
                }
            ],
        }
        return SimpleNamespace(
            content=[SimpleNamespace(text=json.dumps(outcome))],
            structuredContent=None,
            isError=False,
        )

    async def close(self):
        return None


@pytest.fixture
def gateway_answers(monkeypatch):
    """Queue of (status, body) the fake gateway answers, and the requests."""
    answers: list[tuple[int, bytes]] = []
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            {
                "auth": request.headers.get("authorization"),
                "trace": request.headers.get("traceparent"),
                "body": json.loads(request.content),
            }
        )
        status, body = answers.pop(0)
        return httpx.Response(status, content=body)

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(gateway.httpx, "AsyncClient", client)
    fake = FakeTools()
    monkeypatch.setattr(gateway, "Tools", lambda: fake)
    return SimpleNamespace(answers=answers, requests=requests, tools=fake)


async def _collect(plan=PLAN, message="Fais un graphe"):
    return [
        event
        async for event in gateway.run_test(plan, "gw-token", message, [], 1.0)
    ]


def test_a_code_mode_turn_shows_the_sandbox_run_and_its_artifact(
    gateway_answers,
):
    gateway_answers.answers += [
        (
            200,
            _message(
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "run_python",
                        "input": {"code": "print(6*7)"},
                    }
                ],
                "tool_use",
            ),
        ),
        (
            200,
            _message(
                [{"type": "text", "text": "La réponse est 42."}], "end_turn"
            ),
        ),
    ]

    events = asyncio.run(_collect())

    assert [e for e, _ in events] == [
        "model_call",
        "usage",
        "tool_call",
        "sandbox_run",
        "artifact",
        "tool_result",
        "model_call",
        "text_delta",
        "usage",
        "done",
    ]
    payloads = dict(events)
    assert payloads["artifact"]["url"] == (
        "/api/projects/alpha/agents/analyst/artifacts/t1/chart.png"
    )
    assert (
        payloads["sandbox_run"]["stdout"],
        payloads["done"]["stop_reason"],
    ) == (
        "42\n",
        "end_turn",
    )


def test_the_loop_sends_the_tool_result_back_and_counts_every_token(
    gateway_answers,
):
    gateway_answers.answers += [
        (
            200,
            _message(
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "run_python",
                        "input": {"code": "1"},
                    }
                ],
                "tool_use",
            ),
        ),
        (200, _message([{"type": "text", "text": "ok"}], "end_turn", 300, 10)),
    ]

    events = asyncio.run(_collect())

    second = gateway_answers.requests[1]["body"]["messages"]
    assert second[-1]["content"][0]["tool_use_id"] == "toolu_1"
    assert dict(events)["done"]["usage"]["input_tokens"] == 400
    assert {r["auth"] for r in gateway_answers.requests} == {"Bearer gw-token"}
    assert len({r["trace"] for r in gateway_answers.requests}) == 1


def test_the_request_offers_web_search_and_the_agents_effort(gateway_answers):
    gateway_answers.answers.append(
        (200, _message([{"type": "text", "text": "ok"}], "end_turn"))
    )

    asyncio.run(_collect())

    body = gateway_answers.requests[0]["body"]
    assert (
        [t["name"] for t in body["tools"]],
        body["output_config"],
        body["max_tokens"] <= 1500,
    ) == (["run_python", "web_search"], {"effort": "low"}, True)


@pytest.mark.parametrize(
    "status, body, reason",
    [
        (
            429,
            b'{"error":"ai_budget_exhausted","message":"Budget IA"}',
            "budget",
        ),
        (429, b"rate limited", "ratelimit"),
        (403, b'{"error":{"message":"tool not allowed"}}', "policy"),
    ],
)
def test_a_gateway_refusal_ends_the_chat_with_its_reason(
    gateway_answers, status, body, reason
):
    gateway_answers.answers.append((status, body))

    events = asyncio.run(_collect())

    assert [e for e, _ in events][-2:] == ["refusal", "done"]
    assert (
        dict(events)["refusal"]["reason"],
        dict(events)["done"]["stop_reason"],
    ) == (
        reason,
        "refusal",
    )


def test_a_model_refusal_is_a_guardrail_refusal(gateway_answers):
    gateway_answers.answers.append((200, _message([], "refusal")))

    events = asyncio.run(_collect())

    assert dict(events)["refusal"]["reason"] == "guardrail"


def test_the_loop_stops_at_the_step_cap(gateway_answers):
    call = {
        "type": "tool_use",
        "id": "toolu_x",
        "name": "run_python",
        "input": {"code": "1"},
    }
    gateway_answers.answers += [(200, _message([call], "tool_use"))] * 4

    events = asyncio.run(_collect())

    assert dict(events)["done"]["stop_reason"] == "max_steps"
    assert sum(1 for e, _ in events if e == "model_call") == PLAN.max_steps


def test_a_server_error_ends_the_chat_with_an_error(gateway_answers):
    gateway_answers.answers.append((502, b"bad gateway"))

    events = asyncio.run(_collect())

    assert dict(events)["done"]["stop_reason"] == "error"


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


def _vector(*samples):
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": labels, "value": [0, str(value)]}
                for labels, value in samples
            ],
        },
    }


def test_usage_folds_the_recorded_series_per_caller(monkeypatch):
    reader = "agent-alpha-reader"
    answers = {
        ai_usage.TOKENS: _vector(
            ({"azp": reader, "direction": "in"}, 1000),
            ({"azp": reader, "direction": "out"}, 200),
            ({"azp": "app-alpha-shop", "direction": "in"}, 10),
        ),
        ai_usage.COST: _vector(({"azp": reader}, 0.22)),
        ai_usage.REQUESTS: _vector(
            ({"azp": reader, "status": "200"}, 9),
            ({"azp": reader, "status": "503"}, 1),
        ),
        ai_usage.REFUSALS: _vector(({"azp": reader, "reason": "budget"}, 2)),
        ai_usage.TOOL_CALLS: _vector(({"azp": reader}, 4)),
    }

    def handler(request):
        query = request.url.params["query"]
        metric = next(m for m in answers if m + "{" in query)
        assert 'project="alpha"' in query and "[7d]" in query
        return httpx.Response(200, json=answers[metric])

    real = httpx.AsyncClient
    monkeypatch.setattr(
        ai_usage.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(ai_usage, "usd_per_eur", lambda: 1.1)

    items = asyncio.run(ai_usage.usage("alpha", "7d"))

    first = items[0]
    assert (
        first["name"],
        first["kind"],
        first["tokens_in"],
        first["tokens_out"],
        first["requests"],
        first["errors"],
        first["refused"]["budget"],
        first["tool_calls"],
        first["cost_eur"],
    ) == ("reader", "agent", 1000, 200, 10, 1, 2, 4, 0.2)
    assert items[1]["kind"] == "app"


def test_a_label_value_outside_the_allowed_set_is_refused():
    with pytest.raises(ValueError):
        ai_usage._selector(
            'alpha"} or vector(1) #', None
        )  # pylint: disable=protected-access


@pytest.mark.parametrize(
    "spend, status", [(1.0, "ok"), (4.2, "warning"), (5.0, "exhausted")]
)
def test_the_global_budget_status_follows_the_spend(
    monkeypatch, spend, status
):
    def handler(request):
        query = request.url.params["query"]
        value = spend if ai_usage.GLOBAL_SPEND in query else 5
        return httpx.Response(200, json=_vector(({}, value)))

    real = httpx.AsyncClient
    monkeypatch.setattr(
        ai_usage.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )

    assert asyncio.run(ai_usage.budget())["status"] == status


def test_a_haiku_call_is_priced_at_the_public_rate():
    assert (
        ai_usage.cost_usd("claude-haiku-5-5", 1_000_000, 1_000_000, 0) == 0.6
    )


# ---------------------------------------------------------------------------
# Draft
# ---------------------------------------------------------------------------


def _draft_answer(**overrides):
    fields = {
        "template": "assistant",
        "name": "Résumeur Hebdo",
        "description": "Résume la semaine.",
        "model": "claude-haiku-5-5",
        "effort": "low",
        "max_steps": 4,
        "trigger": {"type": "schedule", "cron": "0 8 * * 1"},
        "instructions": "Résume les tickets de la semaine.",
        "tools": ["web_search"],
        "actor": None,
    }
    fields.update(overrides)
    return {
        "content": [{"type": "text", "text": json.dumps(fields)}],
        "usage": {"input_tokens": 900, "output_tokens": 300},
    }


@pytest.fixture
def draft_world(monkeypatch):
    calls = []

    async def complete(token, body):
        calls.append((token, body))
        return calls_answer.pop(0)

    calls_answer: list = []
    monkeypatch.setattr(draft.gateway, "complete", complete)
    monkeypatch.setattr(identity, "cmp_gateway_token", lambda: "cmp-token")
    monkeypatch.setattr(
        draft.catalog,
        "templates",
        mock.AsyncMock(
            return_value=[
                {"id": "assistant", "title": "Assistant", "description": ""},
                {"id": "app-bot", "title": "Bot", "description": ""},
            ]
        ),
    )
    return SimpleNamespace(calls=calls, answers=calls_answer)


def test_a_draft_is_one_small_call_with_the_cmp_client(draft_world):
    draft_world.answers.append(_draft_answer())

    result = asyncio.run(draft.draft("Un résumé chaque lundi", None, "draft"))

    token, body = draft_world.calls[0]
    assert (
        len(draft_world.calls),
        token,
        body["model"],
        body["max_tokens"] <= 1500,
        body["output_config"]["format"]["type"],
    ) == (1, "cmp-token", "claude-haiku-5-5", True, "json_schema")
    assert (result["name"], result["agent"]["trigger"], result["errors"]) == (
        "resumeur-hebdo",
        {"type": "schedule", "cron": "0 8 * * 1"},
        [],
    )
    assert result["usage"]["cost_usd"] == pytest.approx(0.00024)


def test_a_draft_breaking_a_rule_comes_back_with_its_errors(draft_world):
    draft_world.answers.append(
        _draft_answer(trigger={"type": "schedule", "cron": ""})
    )

    result = asyncio.run(draft.draft("Un résumé", None, "draft"))

    assert any("cron" in e for e in result["errors"])


def test_an_unusable_answer_is_a_gateway_error(draft_world):
    draft_world.answers.append({"content": [{"type": "text", "text": "no"}]})

    with pytest.raises(gateway.GatewayError):
        asyncio.run(draft.draft("Un résumé", None, "draft"))
