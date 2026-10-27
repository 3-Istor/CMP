from datetime import date, datetime
from pathlib import Path
from unittest import mock

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.finops import CostLineItem, FxRate, MonthClose, SavingsEntry
from app.services.finops import ingest
from app.services.finops.ingest import (
    ai_gateway,
    fx,
    inputs,
    opencost,
    saas,
    savings,
)
from app.services.finops.ingest.projects import IDLE, PLATFORM, owner_of
from app.services.finops.ingest.store import CostLine, upsert

FIXTURES = Path(__file__).parent / "fixtures" / "finops"
DAY = date(2026, 10, 27)


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def data_dir(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_DATA_DIR", str(FIXTURES))


@pytest.fixture
def tco(data_dir):
    return inputs.onprem_tco()


def _line(cost=1.0, start=datetime(2026, 10, 27)):
    return CostLine(
        x_source="opencost",
        provider_name="CNP on-prem",
        service_name="Kubernetes CPU",
        service_category="Compute",
        charge_period_start=start,
        charge_period_end=start.replace(day=start.day + 1),
        resource_id="onprem/qcm/cpu",
        x_cost_eur=cost,
        billed_cost=cost,
        list_cost=cost,
        effective_cost=cost,
        project="qcm",
    )


def test_upsert_updates_in_place_when_same_line_is_ingested_twice(factory):
    with factory() as db:
        upsert(db, [_line(1.0)])
        upsert(db, [_line(2.5)])

        rows = db.scalars(select(CostLineItem)).all()

    assert [(r.x_cost_eur, r.billing_period) for r in rows] == [
        (2.5, "2026-10")
    ]


def test_upsert_skips_lines_of_a_closed_month(factory):
    with factory() as db:
        db.add(MonthClose(billing_period="2026-10", closed_by="finance"))
        db.commit()

        written = upsert(db, [_line()])

        assert written == 0
        assert db.scalar(select(func.count(CostLineItem.id))) == 0


@pytest.mark.parametrize(
    "namespace,expected",
    [
        ("shop-api", ("shop", "api")),
        ("shop-system", ("shop", None)),
        ("qcm", ("qcm", "qcm")),
        ("observability", (PLATFORM, "observability")),
        (IDLE, (IDLE, None)),
    ],
)
def test_owner_of_maps_namespace_to_project_and_app(namespace, expected):
    owner = owner_of(namespace, {"shop-api": "shop", "shop-system": "shop"})

    assert (owner.project, owner.app) == expected


def test_onprem_tco_flags_estimate_when_an_input_is_mock(tco):
    assert tco.has_mock is True
    assert tco.cluster_eur_per_hour == pytest.approx(0.04662, abs=1e-4)


def test_opencost_lines_split_components_and_attribute_energy(tco):
    allocations = {
        "qcm": {"cpuCost": 0.04, "ramCost": 0.02, "pvCost": 0.0},
        IDLE: {"cpuCost": 0.5},
    }

    lines = opencost.to_lines(DAY, allocations, {}, tco, is_final=False)

    by_id = {line.resource_id: line for line in lines}
    assert set(by_id) == {
        "onprem/qcm/cpu",
        "onprem/qcm/ram",
        f"onprem/{IDLE}/cpu",
    }
    cpu = by_id["onprem/qcm/cpu"]
    assert cpu.project == "qcm"
    assert cpu.x_pricing_basis == "tco"
    assert cpu.x_is_estimate is True
    assert cpu.x_energy_kwh == pytest.approx(
        0.04 * tco.energy_share / tco.eur_per_kwh
    )


def test_ai_gateway_parse_vector_sums_tokens_by_type():
    result = [
        {
            "metric": {
                "project": "shop",
                "azp": "bot",
                "gen_ai_request_model": "gemini-2.5-flash",
                "gen_ai_provider_name": "gcp.gemini",
                "gen_ai_token_type": token_type,
            },
            "value": [0, value],
        }
        for token_type, value in (("input", "1000000"), ("output", "200000"))
    ]

    usage = ai_gateway.parse_vector(result)

    assert usage == [
        ai_gateway.Usage(
            "shop", "bot", "gemini-2.5-flash", "gcp.gemini", 1e6, 2e5
        )
    ]


def test_ai_gateway_values_cloud_models_at_public_price(data_dir, tco):
    prices, local = inputs.ai_prices()
    usage = [
        ai_gateway.Usage("shop", "bot", "gemini-2.5-flash", "g", 1e6, 2e5)
    ]

    [line] = ai_gateway.to_lines(DAY, usage, prices, local, tco, 1.25, False)

    # 1M input × 0.30 $ + 0.2M output × 2.50 $ = 0.80 $, at 1.25 $/€
    assert line.list_cost == pytest.approx(0.80)
    assert line.x_cost_eur == pytest.approx(0.64)
    assert line.billed_cost == 0
    assert line.x_pricing_basis == "public_price"
    assert line.x_is_estimate is False


def test_ai_gateway_splits_gpu_server_energy_over_local_tokens(data_dir, tco):
    prices, local = inputs.ai_prices()
    usage = [
        ai_gateway.Usage("a", "x", "qwen3.5:9b", "ollama", 300, 0),
        ai_gateway.Usage("b", "y", "qwen3.5:9b", "ollama", 100, 0),
    ]

    lines = ai_gateway.to_lines(DAY, usage, prices, local, tco, 1.1, False)

    day_cost = tco.gpu_server_watts * 24 / 1000 * tco.eur_per_kwh
    assert [l.x_cost_eur for l in lines] == pytest.approx(
        [day_cost * 0.75, day_cost * 0.25]
    )
    assert all(l.x_pricing_basis == "tco" and l.x_is_estimate for l in lines)


def test_ai_gateway_marks_estimate_when_model_or_rate_is_unknown(
    data_dir, tco
):
    prices, local = inputs.ai_prices()
    usage = [
        ai_gateway.Usage("a", "x", "mystery-model", "acme", 10, 10),
        ai_gateway.Usage("a", "x", "gemini-2.5-flash", "g", 10, 10),
    ]

    lines = ai_gateway.to_lines(DAY, usage, prices, local, tco, None, False)

    assert [l.x_is_estimate for l in lines] == [True, True]
    assert lines[0].x_cost_eur == 0


def test_price_for_picks_the_entry_in_force_on_the_day():
    old = inputs.ModelPrice("G", "S", "m", 1, 1, date(2026, 1, 1))
    new = inputs.ModelPrice("G", "S", "m", 2, 2, date(2027, 1, 1))

    assert inputs.price_for([old, new], "m", date(2026, 12, 31)) is old
    assert inputs.price_for([old, new], "m", date(2027, 1, 1)) is new


def test_parse_ecb_reads_day_and_rates():
    xml = (
        '<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" '
        'xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">'
        '<Cube><Cube time="2026-10-26"><Cube currency="USD" rate="1.0812"/>'
        "</Cube></Cube></gesmes:Envelope>"
    )

    day, rates = fx.parse_ecb(xml)

    assert day == date(2026, 10, 26)
    assert rates == {"USD": 1.0812}


def test_usd_per_eur_uses_latest_rate_on_or_before_day(factory):
    with factory() as db:
        db.add_all(
            [
                FxRate(day=date(2026, 10, 23), currency="USD", rate=1.07),
                FxRate(day=date(2026, 10, 28), currency="USD", rate=1.09),
            ]
        )
        db.commit()

        assert fx.usd_per_eur(db, date(2026, 10, 27)) == 1.07


def test_saas_lines_start_with_their_effective_month(data_dir):
    subs = inputs.saas_subscriptions()

    october = saas.to_lines(date(2026, 10, 1), subs)
    november = saas.to_lines(date(2026, 11, 1), subs)

    assert {l.provider_name for l in october} == {"GitHub", "Cloudflare"}
    assert "Anthropic" in {l.provider_name for l in november}


def test_days_to_ingest_includes_today_and_the_replay_window(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_INGEST_SINCE", "2026-10-27")
    monkeypatch.setattr(settings, "FINOPS_REPLAY_DAYS", 3)
    done = {date(2026, 10, d) for d in range(27, 32)} | {date(2026, 11, 1)}

    assert ingest.days_to_ingest(date(2026, 10, 27)) == [date(2026, 10, 27)]
    assert ingest.days_to_ingest(date(2026, 11, 5), done) == [
        date(2026, 11, 2),
        date(2026, 11, 3),
        date(2026, 11, 4),
        date(2026, 11, 5),
    ]
    assert ingest.is_final(date(2026, 11, 2), date(2026, 11, 5)) is True
    assert ingest.is_final(date(2026, 11, 5), date(2026, 11, 5)) is False


def test_days_to_ingest_backfills_days_never_ingested(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_INGEST_SINCE", "2026-10-20")
    monkeypatch.setattr(settings, "FINOPS_REPLAY_DAYS", 3)

    days = ingest.days_to_ingest(date(2026, 10, 26), {date(2026, 10, 21)})

    assert date(2026, 10, 21) not in days
    assert days[0] == date(2026, 10, 20)
    assert days[-1] == date(2026, 10, 26)


def test_ingest_once_keeps_going_when_one_source_fails(
    factory, data_dir, monkeypatch
):
    monkeypatch.setattr(ingest, "SessionLocal", factory)
    monkeypatch.setattr(ingest, "in_cluster", lambda: False)
    monkeypatch.setattr(ingest.fx, "refresh", lambda db: None)
    monkeypatch.setattr(ingest.carbon, "refresh", lambda db, since: 0)
    monkeypatch.setattr(
        ingest.opencost,
        "fetch_day",
        lambda day, url=None: {"qcm": {"cpuCost": 0.1}},
    )
    boom = mock.Mock(side_effect=RuntimeError("vmselect down"))
    monkeypatch.setattr(ingest.ai_gateway, "fetch_day", boom)
    monkeypatch.setattr(ingest.savings, "fetch_day", lambda day, tco: [])

    written = ingest.ingest_once(date(2026, 10, 29))

    # 27, 28 and 29 (provisional)
    assert written["opencost"] == 3
    assert written["ai_gateway"] == 0
    assert written["saas"] == 2


def test_tco_derives_resource_prices_like_the_opencost_generator(tco):
    assert tco.cpu_eur_per_core_hour == pytest.approx(0.0037877, abs=1e-6)
    assert tco.ram_eur_per_gib_hour == pytest.approx(0.00051228, abs=1e-7)
    assert tco.watts_per_busy_core == 15


def test_savings_split_avoided_allocation_from_realized_energy(tco):
    rows = [
        savings.NamespaceDay(
            namespace="shop-api",
            asleep_fraction=0.25,
            requested_eur_per_hour=0.01,
            awake_busy_cores=0.4,
        )
    ]

    [entry] = savings.to_entries(DAY, rows, {"shop-api": "shop"}, tco)

    # 6 h asleep: 6 × 0.01 € avoided; 6 h × 0.4 core × 15 W = 36 Wh
    assert (entry.project, entry.app) == ("shop", "api")
    assert entry.avoided_eur == pytest.approx(0.06)
    assert entry.energy_avoided_kwh == pytest.approx(0.036)
    assert entry.realized_eur == pytest.approx(0.036 * tco.eur_per_kwh)


def test_savings_fetch_day_scales_busy_cores_to_awake_time(tco, monkeypatch):
    answers = {
        "asleep": {"shop-api": 0.25, "idle-ns": 0.0},
        "requested": {"shop-api": 0.01},
        "busy": {"shop-api": 0.3},
    }
    by_namespace = {"shop-api": [("Deployment", "api")]}
    monkeypatch.setattr(savings, "targets", lambda: by_namespace)
    exprs = {v: k for k, v in savings.queries(tco, by_namespace).items()}
    monkeypatch.setattr(
        savings, "_query", lambda expr, at: answers[exprs[expr]]
    )

    [row] = savings.fetch_day(DAY, tco)

    assert row.namespace == "shop-api"
    assert row.awake_busy_cores == pytest.approx(0.4)


def test_savings_fetch_day_is_empty_when_no_schedule_has_targets(
    tco, monkeypatch
):
    monkeypatch.setattr(savings, "targets", lambda: {})

    assert savings.fetch_day(DAY, tco) == []


def test_savings_asleep_query_reads_each_target_replicas_by_its_kind():
    by_namespace = {
        "qcm": [("Deployment", "qcm"), ("StatefulSet", "qcm-db")],
    }

    expr = savings.asleep_query(by_namespace)

    assert (
        'kube_deployment_spec_replicas{namespace="qcm",deployment="qcm"}'
        in expr
    )
    assert (
        'kube_statefulset_replicas{namespace="qcm",statefulset="qcm-db"}'
        in expr
    )
    assert "== bool 0" in expr


def test_savings_targets_keep_only_workload_kinds_with_replicas(monkeypatch):
    result = [
        {"metric": {"exported_namespace": "qcm", "target": "Deployment/qcm"}},
        {"metric": {"exported_namespace": "qcm", "target": "CronJob/purge"}},
    ]

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"result": result}}

    monkeypatch.setattr(savings.httpx, "get", lambda *a, **k: Response())

    assert savings.targets() == {"qcm": [("Deployment", "qcm")]}


def test_savings_queries_read_only_on_prem_series(monkeypatch):
    sent = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"result": []}}

    def get(url, params, timeout):
        sent.append(params)
        return Response()

    monkeypatch.setattr(savings.httpx, "get", get)

    savings.targets()

    assert sent[0]["extra_filters[]"] == '{cluster=~"onprem|"}'


def test_savings_store_updates_the_day_in_place(factory, tco):
    rows = [savings.NamespaceDay("qcm", 0.25, 0.01, 0.4)]
    with factory() as db:
        savings.store(db, savings.to_entries(DAY, rows, {}, tco))
        rows = [savings.NamespaceDay("qcm", 0.5, 0.01, 0.4)]
        savings.store(db, savings.to_entries(DAY, rows, {}, tco))

        [entry] = db.scalars(select(SavingsEntry)).all()

    assert entry.avoided_eur == pytest.approx(0.12)


def test_opencost_clusters_add_the_configured_clouds(monkeypatch):
    monkeypatch.setattr(
        settings, "FINOPS_OPENCOST_CLUSTERS", "aws=http://a:1, gcp=http://g:2,"
    )

    assert opencost.clusters() == {
        "onprem": settings.OPENCOST_URL,
        "aws": "http://a:1",
        "gcp": "http://g:2",
    }


def test_opencost_cloud_lines_are_list_price_shares_without_energy(tco):
    [line] = opencost.to_lines(
        DAY, {"shop-api": {"cpuCost": 0.2}}, {}, tco, False, "aws"
    )

    assert line.cloud == "aws"
    assert line.x_pricing_basis == "list_price"
    assert line.billing_currency == "USD"
    assert line.x_energy_kwh is None
    assert line.resource_id == "aws/shop-api/cpu"
