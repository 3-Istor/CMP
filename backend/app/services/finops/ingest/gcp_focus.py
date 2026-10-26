"""
GCP billing export read from BigQuery through the REST API (``jobs.query``).

No Google client library: the service account JWT-bearer flow needs only
PyJWT and cryptography, which the backend already ships, and the query is a
single HTTP call. The FOCUS view is used when configured; otherwise the
detailed export table is mapped to the same FOCUS fields in SQL. Both
aggregate per day in BigQuery, so little data crosses the wire.
"""

import json
import logging
import os
import re
import time
from datetime import date, datetime, timezone

import httpx
import jwt

from app.core.config import settings
from app.services.finops.ingest.focus import (
    DayAggregator,
    RateFor,
    periods_to_read,
    wanted_days,
)
from app.services.finops.ingest.store import CostLine

logger = logging.getLogger(__name__)

SOURCE = "gcp_focus"
PROVIDER = "Google Cloud"
CLOUD = "gcp"
# Normalised form of the label GKE puts on its nodes (goog-k8s-cluster-name).
CLUSTER_TAG = "goog_k8s_cluster_name"

BIGQUERY = "https://bigquery.googleapis.com/bigquery/v2"
SCOPE = "https://www.googleapis.com/auth/bigquery"
_TABLE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_MAX_POLLS = 30

_FOCUS_SQL = """
SELECT
  FORMAT_DATE('%F', DATE(ChargePeriodStart)) AS Day,
  ServiceName, ServiceCategory, ChargeCategory,
  IFNULL(ResourceId, '') AS ResourceId,
  IFNULL(ANY_VALUE(ResourceName), '') AS ResourceName,
  SUM(BilledCost) AS BilledCost,
  SUM(EffectiveCost) AS EffectiveCost,
  SUM(ListCost) AS ListCost,
  BillingCurrency,
  SUM(ConsumedQuantity) AS ConsumedQuantity,
  ConsumedUnit,
  TO_JSON_STRING(Tags) AS TagsJson
FROM `{table}`
WHERE DATE(ChargePeriodStart) BETWEEN @start AND @end
GROUP BY Day, ServiceName, ServiceCategory, ChargeCategory, ResourceId,
  BillingCurrency, ConsumedUnit, TagsJson
"""

_DETAILED_SQL = """
SELECT
  FORMAT_DATE('%F', DATE(usage_start_time)) AS Day,
  service.description AS ServiceName,
  CASE service.description
    WHEN 'Compute Engine' THEN 'Compute'
    WHEN 'Kubernetes Engine' THEN 'Compute'
    WHEN 'Cloud Storage' THEN 'Storage'
    WHEN 'BigQuery' THEN 'Analytics'
    WHEN 'Networking' THEN 'Networking'
    ELSE 'Other'
  END AS ServiceCategory,
  CASE cost_type
    WHEN 'regular' THEN 'Usage'
    WHEN 'tax' THEN 'Tax'
    ELSE 'Adjustment'
  END AS ChargeCategory,
  IFNULL(resource.global_name, IFNULL(resource.name, '')) AS ResourceId,
  IFNULL(ANY_VALUE(resource.name), '') AS ResourceName,
  SUM(cost) AS BilledCost,
  SUM(cost + IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0))
    AS EffectiveCost,
  SUM(cost) AS ListCost,
  currency AS BillingCurrency,
  SUM(usage.amount_in_pricing_units) AS ConsumedQuantity,
  usage.pricing_unit AS ConsumedUnit,
  TO_JSON_STRING(ARRAY_CONCAT(labels, system_labels)) AS TagsJson
FROM `{table}`
WHERE DATE(usage_start_time) BETWEEN @start AND @end
GROUP BY Day, ServiceName, ServiceCategory, ChargeCategory, ResourceId,
  BillingCurrency, ConsumedUnit, TagsJson
"""

_token: dict = {"value": None, "expires": 0.0}


def enabled() -> bool:
    return bool(
        settings.FINOPS_GCP_PROJECT
        and (
            settings.FINOPS_GCP_FOCUS_VIEW or settings.FINOPS_GCP_BILLING_TABLE
        )
    )


def _credentials() -> dict:
    path = settings.FINOPS_GCP_CREDENTIALS_FILE or os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS", ""
    )
    if not path:
        raise ValueError("no GCP service account key configured")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def access_token(client: httpx.Client) -> str:
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    creds = _credentials()
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": creds["client_email"],
            "scope": SCOPE,
            "aud": creds["token_uri"],
            "iat": now,
            "exp": now + 3600,
        },
        creds["private_key"],
        algorithm="RS256",
    )
    response = client.post(
        creds["token_uri"],
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
    )
    response.raise_for_status()
    body = response.json()
    _token["value"] = body["access_token"]
    _token["expires"] = now + int(body.get("expires_in", 3600))
    return _token["value"]


def _rows(body: dict) -> list[dict]:
    names = [f["name"] for f in body.get("schema", {}).get("fields", [])]
    return [
        dict(zip(names, (cell["v"] for cell in row["f"])))
        for row in body.get("rows", [])
    ]


def run_query(
    client: httpx.Client, sql: str, start: date, end: date
) -> list[dict]:
    project = settings.FINOPS_GCP_PROJECT
    headers = {"Authorization": f"Bearer {access_token(client)}"}
    response = client.post(
        f"{BIGQUERY}/projects/{project}/queries",
        headers=headers,
        json={
            "query": sql,
            "useLegacySql": False,
            "parameterMode": "NAMED",
            "queryParameters": [
                {
                    "name": name,
                    "parameterType": {"type": "DATE"},
                    "parameterValue": {"value": value.isoformat()},
                }
                for name, value in (("start", start), ("end", end))
            ],
            "timeoutMs": 60000,
            "maxResults": 10000,
        },
    )
    response.raise_for_status()
    body = response.json()
    job = body.get("jobReference", {})
    polls = 0
    while not body.get("jobComplete", True):
        polls += 1
        if polls > _MAX_POLLS:
            raise TimeoutError("BigQuery billing query did not complete")
        body = _get_results(client, project, job, headers, None)
    rows = _rows(body)
    while body.get("pageToken"):
        body = _get_results(client, project, job, headers, body["pageToken"])
        rows.extend(_rows(body))
    return rows


def _get_results(
    client: httpx.Client,
    project: str,
    job: dict,
    headers: dict,
    page_token: str | None,
) -> dict:
    params = {"timeoutMs": 60000, "maxResults": 10000}
    if job.get("location"):
        params["location"] = job["location"]
    if page_token:
        params["pageToken"] = page_token
    response = client.get(
        f"{BIGQUERY}/projects/{project}/queries/{job['jobId']}",
        headers=headers,
        params=params,
    )
    response.raise_for_status()
    return response.json()


def _sql() -> str:
    view = settings.FINOPS_GCP_FOCUS_VIEW
    table = view or settings.FINOPS_GCP_BILLING_TABLE
    if not _TABLE.match(table):
        raise ValueError(f"invalid BigQuery table name: {table!r}")
    return (_FOCUS_SQL if view else _DETAILED_SQL).format(table=table)


def fetch_lines(
    today: date,
    window: list[date],
    since: date,
    rate_for: RateFor,
    client: httpx.Client | None = None,
) -> list[CostLine]:
    periods = periods_to_read(today, window, since)
    if not periods:
        return []
    wanted = wanted_days(today, window, since)
    first = date.fromisoformat(periods[0] + "-01")
    owns_client = client is None
    client = client or httpx.Client(timeout=90.0)
    try:
        rows = run_query(client, _sql(), first, today)
    finally:
        if owns_client:
            client.close()
    aggregator = DayAggregator(CLOUD, wanted)
    for row in rows:
        aggregator.add(
            {
                **row,
                "ChargePeriodStart": datetime.fromisoformat(
                    row["Day"]
                ).replace(tzinfo=timezone.utc),
                "Tags": row.get("TagsJson"),
            }
        )
    return aggregator.lines(
        source=SOURCE,
        provider_name=PROVIDER,
        cluster_tag=CLUSTER_TAG,
        rate_for=rate_for,
        today=today,
    )
