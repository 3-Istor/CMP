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
from datetime import date

import boto3
import pyarrow.parquet as pq

from app.core.config import settings
from app.services.finops.ingest.focus import (
    DayAggregator,
    RateFor,
    periods_to_read,
    wanted_days,
)
from app.services.finops.ingest.store import CostLine

logger = logging.getLogger(__name__)

SOURCE = "aws_focus"
PROVIDER = "AWS"
CLOUD = "aws"
# Normalised form of the tag EKS puts on its worker nodes (eks:cluster-name).
CLUSTER_TAG = "eks_cluster_name"

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
    """Credentials come from the standard AWS_* environment variables."""
    return boto3.client("s3", region_name=settings.FINOPS_AWS_REGION)


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


def fetch_lines(
    today: date,
    window: list[date],
    since: date,
    rate_for: RateFor,
    s3=None,
) -> list[CostLine]:
    s3 = s3 or s3_client()
    bucket = settings.FINOPS_AWS_EXPORT_BUCKET
    aggregator = DayAggregator(CLOUD, wanted_days(today, window, since))
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
