"""
AWS Data Exports, "FOCUS 1.2 with AWS columns", read from S3 as Parquet.

Layout under ``FINOPS_AWS_EXPORT_PREFIX`` (the export root):
``metadata/BILLING_PERIOD=YYYY-MM/<name>-Manifest.json`` lists the data files
of the latest delivery of that period, ``data/BILLING_PERIOD=YYYY-MM/`` holds
them. Older deliveries can still be present, so only the files named by the
newest manifest are read.
"""

import io
import json
import logging
import re
from datetime import date

import boto3
import httpx
import pyarrow.parquet as pq

from app.core.config import settings
from app.services.finops.ingest import federation
from app.services.finops.ingest.focus import (
    DayAggregator,
    RateFor,
    periods_to_read,
    wanted_days,
)
from app.services.finops.ingest.store import CostLine
from app.services.metrics_isolation import VMSELECT

logger = logging.getLogger(__name__)

SOURCE = "aws_focus"
PROVIDER = "AWS"
CLOUD = "aws"
# Normalised form of the tag EKS puts on its worker nodes (eks:cluster-name).
CLUSTER_TAG = "eks_cluster_name"
# Same value as the tag, so metrics-found and tagged nodes read alike.
EKS_CLUSTER = "cnp-eks"
_INSTANCE = re.compile(r"^i-[0-9a-f]+$")

_COLUMNS = (
    "ChargePeriodStart",
    "ServiceName",
    "ServiceCategory",
    "ChargeCategory",
    "ResourceId",
    "ResourceName",
    "BilledCost",
    "EffectiveCost",
    "ListCost",
    "BillingCurrency",
    "ConsumedQuantity",
    "ConsumedUnit",
    "Tags",
)
_BATCH_ROWS = 50_000


def enabled() -> bool:
    return bool(settings.FINOPS_AWS_EXPORT_BUCKET)


def s3_client():
    """
    STS credentials federated from Keycloak when FINOPS_AWS_ROLE_ARN is set,
    else the standard AWS_* environment variables.
    """
    credentials = (
        federation.aws_credentials() if federation.aws_enabled() else {}
    )
    return boto3.client(
        "s3", region_name=settings.FINOPS_AWS_REGION, **credentials
    )


def _root() -> str:
    return settings.FINOPS_AWS_EXPORT_PREFIX.strip("/")


def _key(*parts: str) -> str:
    return "/".join(p for p in (_root(), *parts) if p)


def latest_manifest(s3, bucket: str, period: str) -> dict | None:
    prefix = _key("metadata", f"BILLING_PERIOD={period}") + "/"
    newest = None
    pages = s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix
    )
    for page in pages:
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith("Manifest.json"):
                continue
            if newest is None or obj["LastModified"] > newest["LastModified"]:
                newest = obj
    if newest is None:
        return None
    body = s3.get_object(Bucket=bucket, Key=newest["Key"])["Body"].read()
    return json.loads(body)


def _read_parquet(
    s3, bucket: str, key: str, aggregator: DayAggregator
) -> None:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    parquet = pq.ParquetFile(io.BytesIO(body))
    present = set(parquet.schema_arrow.names)
    columns = [c for c in _COLUMNS if c in present]
    for batch in parquet.iter_batches(batch_size=_BATCH_ROWS, columns=columns):
        for row in batch.to_pylist():
            aggregator.add(row)


def eks_nodes(oldest: date, today: date) -> dict[str, str]:
    """
    EC2 instance id -> EKS cluster of every node seen since ``oldest``, from
    kube-state-metrics in the on-prem VictoriaMetrics. Node instances carry
    no cnp.* tag and eks:cluster-name reaches the export only once activated,
    so without this a node bought tonight would never be split. Raises when
    the metrics cannot be read: the pass must then leave AWS untouched rather
    than rewrite a month of node lines without their cluster.
    """
    days = (today - oldest).days + 2
    response = httpx.get(
        f"{VMSELECT}/api/v1/query",
        params={
            "query": (
                "max by (provider_id) (max_over_time("
                f'kube_node_info{{cluster="{CLOUD}"}}[{days}d]))'
            )
        },
        timeout=30.0,
    )
    response.raise_for_status()
    nodes = {}
    for sample in response.json()["data"]["result"]:
        instance = sample["metric"].get("provider_id", "").rsplit("/", 1)[-1]
        if _INSTANCE.match(instance):
            nodes[instance] = EKS_CLUSTER
    return nodes


def _oldest_day(today: date, window: list[date], since: date) -> date:
    period = periods_to_read(today, window, since)[:1]
    first = date.fromisoformat(f"{period[0]}-01") if period else today
    return max(min([first, *window]), since)


def node_tags(nodes: dict[str, str]) -> dict[str, dict[str, str]]:
    return {instance: {CLUSTER_TAG: name} for instance, name in nodes.items()}


def fetch_lines(
    today: date,
    window: list[date],
    since: date,
    rate_for: RateFor,
    s3=None,
) -> list[CostLine]:
    s3 = s3 or s3_client()
    bucket = settings.FINOPS_AWS_EXPORT_BUCKET
    aggregator = DayAggregator(
        CLOUD,
        wanted_days(today, window, since),
        node_tags(eks_nodes(_oldest_day(today, window, since), today)),
    )
    for period in periods_to_read(today, window, since):
        manifest = latest_manifest(s3, bucket, period)
        if manifest is None:
            logger.info("FinOps: no AWS export manifest for %s yet", period)
            continue
        for key in manifest.get("dataFiles", []):
            _read_parquet(s3, bucket, key, aggregator)
    return aggregator.lines(
        source=SOURCE,
        provider_name=PROVIDER,
        cluster_tag=CLUSTER_TAG,
        rate_for=rate_for,
        today=today,
    )
