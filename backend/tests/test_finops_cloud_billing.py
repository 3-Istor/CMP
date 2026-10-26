import io
import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import httpx
import jwt
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.finops import CostLineItem
from app.services.finops import ingest, reconciliation
from app.services.finops.ingest import aws_focus, focus, fx, gcp_focus, split
from app.services.finops.ingest.focus import CLUSTER
from app.services.finops.ingest.projects import IDLE, PLATFORM
from app.services.finops.ingest.store import CostLine, upsert

FIXTURES = Path(__file__).parent / "fixtures" / "finops"
TODAY = date(2026, 10, 29)
SINCE = date(2026, 10, 1)
WINDOW = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)]
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


def usd_at_1_25(currency, _day):
    return {"USD": 1.25, "EUR": 1.0}.get(currency)


def _hour(day, hour):
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def _row(**overrides):
    row = {
        "ChargePeriodStart": _hour(DAY, 0),
        "ServiceName": "Amazon Simple Storage Service",
        "ServiceCategory": "Storage",
        "ChargeCategory": "Usage",
        "ResourceId": "arn:aws:s3:::qcm-data",
        "ResourceName": "qcm-data",
        "BilledCost": 5.0,
        "EffectiveCost": 5.0,
        "ListCost": 5.0,
        "BillingCurrency": "USD",
        "ConsumedQuantity": 10.0,
        "ConsumedUnit": "GB-Mo",
        "Tags": json.dumps(
            {
                "user_cnp-project": "qcm",
                "user_cnp-app": "api",
                "user_cnp-env": "prod",
            }
        ),
    }
    row.update(overrides)
    return row


def _parquet(rows) -> bytes:
    table = pa.Table.from_pylist(rows)
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


class FakeS3:
    """Just enough of the boto3 S3 client for the export layout."""

    def __init__(self, objects: dict[str, tuple[bytes, datetime]]):
        self.objects = objects
        self.read: list[str] = []

    def get_paginator(self, _name):
        outer = self

        class Paginator:
            def paginate(self, Bucket, Prefix):  # noqa: N803
                keys = [
                    {"Key": k, "LastModified": modified}
                    for k, (_, modified) in outer.objects.items()
                    if k.startswith(Prefix)
                ]
                return [{"Contents": keys}]

        return Paginator()

    def get_object(self, Bucket, Key):  # noqa: N803
        self.read.append(Key)
        return {"Body": io.BytesIO(self.objects[Key][0])}


def _export(files_by_period, stale=None) -> FakeS3:
    objects = {}
    for period, rows in files_by_period.items():
        data_key = (
            f"cur/cnp/data/BILLING_PERIOD={period}/cnp-00001.snappy.parquet"
        )
        objects[data_key] = (_parquet(rows), datetime(2026, 10, 28))
        manifest = {"dataFiles": [data_key]}
        objects[
            f"cur/cnp/metadata/BILLING_PERIOD={period}/cnp-Manifest.json"
        ] = (
            json.dumps(manifest).encode(),
            datetime(2026, 10, 28),
        )
    objects.update(stale or {})
    return FakeS3(objects)


@pytest.fixture
def aws_settings(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_AWS_EXPORT_BUCKET", "cnp-billing")
    monkeypatch.setattr(settings, "FINOPS_AWS_EXPORT_PREFIX", "cur/cnp/")


def _aws_lines(s3, rate_for=usd_at_1_25):
    return aws_focus.fetch_lines(TODAY, WINDOW, SINCE, rate_for, s3=s3)


def _by_resource(lines):
    return {line.resource_id: line for line in lines}


# AWS


def test_aws_hourly_rows_become_one_line_per_day_resource_and_category(
    aws_settings,
):
    rows = [_row(ChargePeriodStart=_hour(DAY, h)) for h in range(24)]
    s3 = _export({"2026-10": rows})

    lines = _aws_lines(s3)

    assert len(lines) == 1
    assert lines[0].billed_cost == pytest.approx(120.0)
    assert lines[0].consumed_quantity == pytest.approx(240.0)


def test_aws_converts_usd_to_eur_at_the_rate_of_the_charge_day(aws_settings):
    s3 = _export({"2026-10": [_row(EffectiveCost=5.0)]})

    line = _aws_lines(s3)[0]

    assert line.x_cost_eur == pytest.approx(4.0)
    assert line.x_fx_rate == 1.25
    assert line.x_is_estimate is False


def test_aws_flags_estimate_when_no_usd_rate_is_known(aws_settings):
    s3 = _export({"2026-10": [_row()]})

    line = _aws_lines(s3, rate_for=lambda cur, day: None)[0]

    assert line.x_is_estimate is True
    assert line.x_fx_rate == 1.10


def test_aws_maps_cnp_tags_to_project_app_env_with_direct_allocation(
    aws_settings,
):
    s3 = _export({"2026-10": [_row()]})

    line = _aws_lines(s3)[0]

    assert (line.project, line.app, line.env) == ("qcm", "api", "prod")
    assert line.x_allocation_method == "direct"
    assert (line.cloud, line.provider_name) == ("aws", "AWS")
    assert line.x_pricing_basis == "invoice"


def test_aws_leaves_untagged_resources_without_project(aws_settings):
    s3 = _export({"2026-10": [_row(Tags=None, ResourceId="db-1")]})

    line = _aws_lines(s3)[0]

    assert line.project is None
    assert line.x_allocation_method == "unallocated"


def test_aws_gives_lines_without_resource_a_stable_id_per_service(
    aws_settings,
):
    tax = _row(ResourceId=None, ServiceName="Tax", ChargeCategory="Tax")
    s3 = _export({"2026-10": [tax, dict(tax)]})

    lines = _aws_lines(s3)

    assert [line.resource_id for line in lines] == ["aws:Tax"]


def test_aws_marks_eks_node_lines_for_the_split(aws_settings):
    node = _row(
        ServiceName="Amazon Elastic Compute Cloud",
        ServiceCategory="Compute",
        ResourceId="i-0abc",
        Tags=json.dumps(
            {"eks:cluster-name": "cnp-eks", "user_cnp-project": "qcm"}
        ),
    )
    s3 = _export({"2026-10": [node]})

    line = _aws_lines(s3)[0]

    assert line.project == CLUSTER
    assert line.tags()["k8s_cluster"] == "cnp-eks"


def test_aws_reads_only_the_files_of_the_newest_manifest(aws_settings):
    old_key = "cur/cnp/data/BILLING_PERIOD=2026-10/old.parquet"
    stale = {
        old_key: (_parquet([_row(BilledCost=999.0)]), datetime(2026, 10, 1)),
        "cur/cnp/metadata/BILLING_PERIOD=2026-10/old-Manifest.json": (
            json.dumps({"dataFiles": [old_key]}).encode(),
            datetime(2026, 10, 1),
        ),
    }
    s3 = _export({"2026-10": [_row(BilledCost=5.0)]}, stale=stale)

    lines = _aws_lines(s3)

    assert [line.billed_cost for line in lines] == [5.0]
    assert old_key not in s3.read


def test_aws_ignores_days_of_final_periods_outside_the_replay_window(
    aws_settings,
):
    august = _row(ChargePeriodStart=_hour(date(2026, 8, 15), 0))
    s3 = _export({"2026-10": [_row()]})
    s3.objects.update(_export({"2026-08": [august]}).objects)

    lines = aws_focus.fetch_lines(
        TODAY, WINDOW, date(2026, 8, 1), usd_at_1_25, s3=s3
    )

    assert {line.charge_period_start.month for line in lines} == {10}
    assert not any("BILLING_PERIOD=2026-08/cnp-00001" in k for k in s3.read)


def test_aws_rereads_open_periods_beyond_the_replay_window(aws_settings):
    early_october = _row(ChargePeriodStart=_hour(date(2026, 10, 3), 0))
    s3 = _export({"2026-10": [early_october]})

    lines = _aws_lines(s3)

    assert [line.charge_period_start.day for line in lines] == [3]
    assert lines[0].x_is_final is False


def test_aws_skips_a_period_whose_manifest_is_not_delivered_yet(aws_settings):
    s3 = FakeS3({})

    assert _aws_lines(s3) == []


def test_aws_client_is_built_for_the_configured_region(monkeypatch):
    client = mock.Mock()
    factory_mock = mock.Mock(return_value=client)
    monkeypatch.setattr(aws_focus.boto3, "client", factory_mock)

    assert aws_focus.s3_client() is client
    factory_mock.assert_called_once_with("s3", region_name="eu-west-3")


@pytest.mark.parametrize(
    "period,today,expected",
    [
        ("2026-09", date(2026, 10, 31), False),
        ("2026-09", date(2026, 11, 1), True),
        ("2026-10", date(2026, 11, 30), False),
        ("2026-12", date(2027, 2, 1), True),
    ],
)
def test_period_is_final_once_the_month_after_next_has_started(
    period, today, expected
):
    assert focus.period_is_final(period, today) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"cnp.project": "a"}', {"cnp_project": "a"}),
        ({"user_cnp:app": "b"}, {"cnp_app": "b"}),
        ([("cnp-env", "dev")], {"cnp_env": "dev"}),
        (
            [{"key": "aws:eks:cluster-name", "value": "c"}],
            {"eks_cluster_name": "c"},
        ),
        ("not json", {}),
        (None, {}),
    ],
)
def test_parse_tags_normalises_every_shape_and_spelling(raw, expected):
    assert focus.parse_tags(raw) == expected


def test_resource_id_is_cut_with_a_stable_hash_when_too_long():
    long_id = "arn:" + "x" * 400

    first = focus.stable_resource_id("aws", "S3", long_id)
    second = focus.stable_resource_id("aws", "S3", long_id)

    assert first == second
    assert len(first) <= 255


# GCP


def _bigquery_transport(captured, pages=None):
    body = json.loads(
        (FIXTURES / "bigquery_billing_response.json").read_text()
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if pages and request.method == "GET":
            return httpx.Response(200, json=pages.pop(0))
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


@pytest.fixture
def gcp_settings(monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_GCP_PROJECT", "cnp-billing")
    monkeypatch.setattr(
        settings, "FINOPS_GCP_BILLING_TABLE", "cnp-billing.billing.export_v1"
    )
    monkeypatch.setattr(settings, "FINOPS_GCP_FOCUS_VIEW", "")
    monkeypatch.setattr(gcp_focus, "access_token", lambda client: "tok")


def _gcp_lines(captured, **kwargs):
    client = httpx.Client(transport=_bigquery_transport(captured, **kwargs))
    return gcp_focus.fetch_lines(TODAY, WINDOW, SINCE, usd_at_1_25, client)


def test_gcp_maps_labels_to_project_app_env(gcp_settings):
    lines = _by_resource(_gcp_lines([]))

    bucket = lines["//storage/buckets/qcm-data"]

    assert (bucket.project, bucket.app, bucket.env) == ("qcm", "api", "dev")
    assert (bucket.cloud, bucket.provider_name) == ("gcp", "Google Cloud")
    assert bucket.x_cost_eur == 1.5


def test_gcp_marks_gke_node_lines_for_the_split(gcp_settings):
    lines = _by_resource(_gcp_lines([]))

    node = lines["//compute/instances/gke-node-1"]

    assert node.project == CLUSTER
    assert node.tags()["k8s_cluster"] == "cnp-gke"


def test_gcp_leaves_unlabelled_lines_unallocated(gcp_settings):
    lines = _by_resource(_gcp_lines([]))

    assert lines["//net/egress"].project is None
    assert lines["gcp:Cloud Logging"].charge_category == "Tax"


def test_gcp_queries_the_detailed_table_with_the_date_range(gcp_settings):
    captured = []

    _gcp_lines(captured)

    query = json.loads(captured[0].content)
    assert "`cnp-billing.billing.export_v1`" in query["query"]
    assert "usage_start_time" in query["query"]
    assert query["queryParameters"][0]["parameterValue"]["value"] == (
        "2026-10-01"
    )
    assert str(captured[0].url).endswith("/projects/cnp-billing/queries")
    assert captured[0].headers["authorization"] == "Bearer tok"


def test_gcp_prefers_the_focus_view_when_configured(gcp_settings, monkeypatch):
    monkeypatch.setattr(
        settings, "FINOPS_GCP_FOCUS_VIEW", "cnp-billing.billing.focus_v1"
    )
    captured = []

    _gcp_lines(captured)

    sql = json.loads(captured[0].content)["query"]
    assert "`cnp-billing.billing.focus_v1`" in sql
    assert "ChargePeriodStart" in sql


def test_gcp_rejects_a_table_name_that_could_inject_sql(
    gcp_settings, monkeypatch
):
    monkeypatch.setattr(
        settings, "FINOPS_GCP_BILLING_TABLE", "a`; DROP TABLE x; --"
    )

    with pytest.raises(ValueError):
        _gcp_lines([])


def test_gcp_follows_page_tokens_and_waits_for_unfinished_jobs(gcp_settings):
    base = json.loads(
        (FIXTURES / "bigquery_billing_response.json").read_text()
    )
    first_rows, last_row = base["rows"][:3], base["rows"][3:]
    pending = {**base, "jobComplete": False, "rows": []}
    page_one = {**base, "rows": first_rows, "pageToken": "p2"}
    page_two = {**base, "rows": last_row}
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(200, json=pending)
        token = request.url.params.get("pageToken")
        return httpx.Response(200, json=page_two if token else page_one)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    lines = gcp_focus.fetch_lines(TODAY, WINDOW, SINCE, usd_at_1_25, client)

    assert len(lines) == 4
    assert seen[1].url.params["location"] == "EU"


def test_gcp_exchanges_a_signed_service_account_assertion(
    tmp_path, monkeypatch
):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    creds = tmp_path / "sa.json"
    creds.write_text(
        json.dumps(
            {
                "client_email": "billing@cnp.iam.gserviceaccount.com",
                "private_key": pem,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
    )
    monkeypatch.setattr(settings, "FINOPS_GCP_CREDENTIALS_FILE", str(creds))
    monkeypatch.setitem(gcp_focus._token, "value", None)
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(
            200, json={"access_token": "ya29.x", "expires_in": 3600}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    token = gcp_focus.access_token(client)

    claims = jwt.decode(
        sent[0]["assertion"],
        key.public_key(),
        algorithms=["RS256"],
        audience="https://oauth2.googleapis.com/token",
    )
    assert token == "ya29.x"
    assert claims["iss"] == "billing@cnp.iam.gserviceaccount.com"
    assert claims["scope"] == gcp_focus.SCOPE


# Split


def _node(cloud, cost, day=DAY, cluster="c1", rid="i-1", final=False):
    start = datetime(day.year, day.month, day.day)
    return CostLine(
        x_source=f"{cloud}_focus",
        provider_name="AWS" if cloud == "aws" else "Google Cloud",
        service_name="Compute",
        service_category="Compute",
        charge_period_start=start,
        charge_period_end=datetime(day.year, day.month, day.day + 1),
        resource_id=rid,
        x_cost_eur=cost,
        billed_cost=cost,
        list_cost=cost,
        effective_cost=cost,
        project=CLUSTER,
        cloud=cloud,
        x_allocation_method="unallocated",
        x_is_final=final,
        extra_tags={"k8s_cluster": cluster},
    )


def _opencost(cloud, namespace, project, cost, day=DAY, app=None):
    start = datetime(day.year, day.month, day.day)
    return CostLine(
        x_source="opencost",
        provider_name="CNP",
        service_name="Kubernetes CPU",
        service_category="Compute",
        charge_period_start=start,
        charge_period_end=datetime(day.year, day.month, day.day + 1),
        resource_id=f"{cloud}/{namespace}/cpu",
        resource_name=namespace,
        x_cost_eur=cost,
        billed_cost=cost,
        list_cost=cost,
        effective_cost=cost,
        project=project,
        app=app,
        cloud=cloud,
        x_allocation_method="opencost",
    )


def _split_rows(db, cloud="aws"):
    return db.scalars(
        select(CostLineItem).where(CostLineItem.x_source == f"{cloud}_split")
    ).all()


def test_split_shares_add_up_to_the_node_invoice(factory):
    with factory() as db:
        upsert(db, [_node("aws", 10.0)])
        upsert(
            db,
            [
                _opencost("aws", "qcm-api", "qcm", 1.0, app="api"),
                _opencost("aws", "roadmap", "roadmap", 2.0),
                _opencost("aws", "platform-x", PLATFORM, 4.0),
            ],
        )

        written = split.split_day(db, "aws", DAY)
        rows = _split_rows(db)

    assert written == 3
    assert sum(r.x_cost_eur for r in rows) == pytest.approx(10.0, abs=1e-12)
    by_project = {r.project: r.x_cost_eur for r in rows}
    assert by_project["qcm"] == pytest.approx(10 / 7)
    assert by_project["roadmap"] == pytest.approx(20 / 7)


def test_split_sends_the_idle_share_to_the_idle_project(factory):
    with factory() as db:
        upsert(db, [_node("aws", 8.0)])
        upsert(
            db,
            [
                _opencost("aws", "qcm-api", "qcm", 3.0),
                _opencost("aws", IDLE, IDLE, 1.0),
            ],
        )

        split.split_day(db, "aws", DAY)
        idle = [r for r in _split_rows(db) if r.project == IDLE]

    assert [r.x_cost_eur for r in idle] == [pytest.approx(2.0)]


def test_split_lines_follow_the_documented_shape(factory):
    with factory() as db:
        upsert(db, [_node("gcp", 6.0)])
        upsert(db, [_opencost("gcp", "qcm-api", "qcm", 1.0, app="api")])

        split.split_day(db, "gcp", DAY)
        row = _split_rows(db, "gcp")[0]

    assert row.x_source == "gcp_split"
    assert row.x_allocation_method == "opencost"
    assert row.charge_category == "Usage"
    assert (row.cloud, row.project, row.app) == ("gcp", "qcm", "api")
    assert row.resource_name == "qcm-api"


def test_split_leaves_nodes_unsplit_when_opencost_has_no_data(factory):
    with factory() as db:
        upsert(db, [_node("aws", 10.0)])

        written = split.split_day(db, "aws", DAY)
        node = db.scalar(select(CostLineItem))

    assert written == 0
    assert node.project == CLUSTER
    assert split.SPLIT_TAG not in node.tags


def test_split_marks_node_lines_it_has_split(factory):
    with factory() as db:
        upsert(db, [_node("aws", 10.0)])
        upsert(db, [_opencost("aws", "qcm-api", "qcm", 1.0)])

        split.split_day(db, "aws", DAY)
        node = db.scalar(
            select(CostLineItem).where(CostLineItem.x_source == "aws_focus")
        )

    assert node.tags[split.SPLIT_TAG] == "aws_split"
    assert node.x_cost_eur == 10.0


def test_split_ignores_opencost_lines_of_another_cloud(factory):
    with factory() as db:
        upsert(db, [_node("aws", 10.0)])
        upsert(db, [_opencost("onprem", "qcm-api", "qcm", 5.0)])

        written = split.split_day(db, "aws", DAY)

    assert written == 0


def test_split_replaces_previous_lines_when_a_namespace_disappears(factory):
    with factory() as db:
        upsert(db, [_node("aws", 10.0)])
        upsert(
            db,
            [
                _opencost("aws", "qcm-api", "qcm", 1.0),
                _opencost("aws", "roadmap", "roadmap", 1.0),
            ],
        )
        split.split_day(db, "aws", DAY)
        db.execute(
            CostLineItem.__table__.delete().where(
                CostLineItem.resource_name == "roadmap"
            )
        )
        db.commit()

        split.split_day(db, "aws", DAY)
        rows = _split_rows(db)

    assert [(r.project, r.x_cost_eur) for r in rows] == [("qcm", 10.0)]


def test_split_is_idempotent(factory):
    with factory() as db:
        upsert(db, [_node("aws", 10.0)])
        upsert(db, [_opencost("aws", "qcm-api", "qcm", 1.0)])

        split.split_day(db, "aws", DAY)
        split.split_day(db, "aws", DAY)

        assert len(_split_rows(db)) == 1


# Reconciliation


def _tagged(cloud, cost, project, rid="bucket-1", day=DAY):
    start = datetime(day.year, day.month, day.day)
    return CostLine(
        x_source=f"{cloud}_focus",
        provider_name="AWS",
        service_name="S3",
        service_category="Storage",
        charge_period_start=start,
        charge_period_end=datetime(day.year, day.month, day.day + 1),
        resource_id=rid,
        x_cost_eur=cost,
        billed_cost=cost,
        list_cost=cost,
        effective_cost=cost,
        project=project,
        cloud=cloud,
    )


def test_reconcile_parts_add_up_to_the_invoice_after_a_split(factory):
    with factory() as db:
        upsert(
            db,
            [
                _node("aws", 10.0),
                _tagged("aws", 5.0, "qcm"),
                _tagged("aws", 3.0, None, rid="orphan"),
            ],
        )
        upsert(
            db,
            [
                _opencost("aws", "qcm-api", "qcm", 3.0),
                _opencost("aws", IDLE, IDLE, 1.0),
                _opencost("aws", "monitoring", PLATFORM, 1.0),
            ],
        )
        split.split_day(db, "aws", DAY)

        result = reconciliation.reconcile(db, "aws", "2026-10")

    assert result["invoice_total_eur"] == pytest.approx(18.0)
    assert result["allocated_eur"] == pytest.approx(5.0 + 6.0)
    assert result["idle_eur"] == pytest.approx(2.0)
    assert result["platform_eur"] == pytest.approx(2.0)
    assert result["unallocated_eur"] == pytest.approx(3.0)
    assert result["unallocated_pct"] == pytest.approx(16.67)
    assert result["gap_eur"] == pytest.approx(0.0, abs=1e-9)


def test_reconcile_counts_unsplit_nodes_as_unallocated(factory):
    with factory() as db:
        upsert(db, [_node("aws", 10.0), _tagged("aws", 10.0, "qcm")])

        result = reconciliation.reconcile(db, "aws", "2026-10")

    assert result["unallocated_eur"] == 10.0
    assert result["unallocated_pct"] == 50.0
    assert result["gap_eur"] == 0.0


def test_reconcile_does_not_count_nodes_twice_on_split_days_only(factory):
    other = date(2026, 10, 28)
    with factory() as db:
        upsert(db, [_node("aws", 4.0), _node("aws", 6.0, day=other)])
        upsert(db, [_opencost("aws", "qcm-api", "qcm", 1.0)])
        split.split_cloud(db, "aws", [DAY, other])

        result = reconciliation.reconcile(db, "aws", "2026-10")

    assert result["invoice_total_eur"] == 10.0
    assert result["allocated_eur"] == 4.0
    assert result["unallocated_eur"] == 6.0
    assert result["gap_eur"] == 0.0


def test_reconcile_of_an_empty_period_is_all_zero(factory):
    with factory() as db:
        result = reconciliation.reconcile(db, "gcp", "2026-10")

    assert set(result.values()) == {0.0}


def test_reconcile_ignores_the_other_cloud(factory):
    with factory() as db:
        upsert(db, [_tagged("aws", 5.0, "qcm"), _tagged("gcp", 7.0, "qcm")])

        result = reconciliation.reconcile(db, "gcp", "2026-10")

    assert result["invoice_total_eur"] == 7.0


def test_reconcile_rejects_an_unknown_provider(factory):
    with factory() as db, pytest.raises(ValueError):
        reconciliation.reconcile(db, "azure", "2026-10")


def test_tag_compliance_lists_untagged_resources_by_cost(factory):
    with factory() as db:
        upsert(
            db,
            [
                _tagged("aws", 1.0, None, rid="small"),
                _tagged("aws", 9.0, None, rid="big"),
                _tagged("aws", 3.0, None, rid="big", day=date(2026, 10, 28)),
                _tagged("aws", 50.0, "qcm", rid="tagged"),
                _node("aws", 20.0),
            ],
        )

        top = reconciliation.tag_compliance(db, "aws", "2026-10")

    assert [(t["resource_id"], t["cost_eur"]) for t in top] == [
        ("big", 12.0),
        ("small", 1.0),
    ]
    assert top[0]["share_of_untagged_pct"] == pytest.approx(92.31)


# Wiring


@pytest.fixture
def wired(factory, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "FINOPS_DATA_DIR", str(FIXTURES))
    monkeypatch.setattr(settings, "FINOPS_INGEST_SINCE", "2026-10-01")
    monkeypatch.setattr(ingest, "SessionLocal", factory)
    monkeypatch.setattr(ingest, "in_cluster", lambda: False)
    monkeypatch.setattr(fx, "refresh", lambda db: None)
    monkeypatch.setattr(ingest.opencost, "fetch_day", lambda day: {})
    monkeypatch.setattr(ingest.ai_gateway, "fetch_day", lambda day: [])
    return factory


def test_ingest_once_skips_cloud_billing_when_settings_are_empty(
    wired, monkeypatch
):
    fetch = mock.Mock()
    monkeypatch.setattr(ingest.aws_focus, "fetch_lines", fetch)
    monkeypatch.setattr(ingest.gcp_focus, "fetch_lines", fetch)

    written = ingest.ingest_once(TODAY)

    fetch.assert_not_called()
    assert written["aws_focus"] == written["gcp_focus"] == 0


def test_ingest_once_writes_aws_invoice_and_splits_the_nodes(
    wired, aws_settings, monkeypatch
):
    node = _row(
        ServiceName="Amazon Elastic Compute Cloud",
        ServiceCategory="Compute",
        ResourceId="i-0abc",
        BillingCurrency="EUR",
        EffectiveCost=10.0,
        Tags=json.dumps({"eks:cluster-name": "cnp-eks"}),
    )
    s3 = _export({"2026-10": [node]})
    monkeypatch.setattr(aws_focus, "s3_client", lambda: s3)
    with wired() as db:
        upsert(db, [_opencost("aws", "qcm-api", "qcm", 2.0)])

    written = ingest.ingest_once(TODAY)

    with wired() as db:
        result = reconciliation.reconcile(db, "aws", "2026-10")
    assert (written["aws_focus"], written["aws_split"]) == (1, 1)
    assert result["allocated_eur"] == 10.0
    assert result["gap_eur"] == 0.0


def test_ingest_once_keeps_going_when_aws_billing_fails(wired, monkeypatch):
    monkeypatch.setattr(settings, "FINOPS_AWS_EXPORT_BUCKET", "cnp-billing")
    monkeypatch.setattr(
        ingest.aws_focus,
        "fetch_lines",
        mock.Mock(side_effect=RuntimeError("AccessDenied")),
    )

    written = ingest.ingest_once(TODAY)

    assert written["aws_focus"] == 0
    assert written["saas"] > 0
