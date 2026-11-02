"""
Shared FOCUS handling for the cloud billing exports: tag normalisation, daily
aggregation and conversion to ``CostLine``. Rows are aggregated while they
stream in, so a month of hourly lines never sits in memory.
"""

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from app.services.finops.ingest.ai_gateway import FALLBACK_USD_PER_EUR
from app.services.finops.ingest.store import CostLine

CLUSTER = "__cluster__"

_PROJECT_KEYS = ("cnp_project", "project")
_APP_KEYS = ("cnp_app",)
_ENV_KEYS = ("cnp_env",)
_MAX_RESOURCE_ID = 255
_PROVIDER_PREFIXES = ("user_", "aws_")

RateFor = Callable[[str, date], float | None]


def normalize_key(key: str) -> str:
    """``cnp.project``, ``cnp-project`` and ``user_cnp:project`` all become
    ``cnp_project``: each cloud spells and prefixes the same tag differently.
    """
    key = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    for prefix in _PROVIDER_PREFIXES:
        if key.startswith(prefix):
            return key.removeprefix(prefix)
    return key


def parse_tags(raw: object) -> dict[str, str]:
    """Accepts the shapes tags come in: JSON text, a dict, a Parquet map
    (list of pairs) or a list of ``{key, value}`` records."""
    if not raw:
        return {}
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    pairs: list[tuple[object, object]] = []
    if isinstance(raw, Mapping):
        pairs = list(raw.items())
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping):
                pairs.append((item.get("key"), item.get("value")))
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                pairs.append((item[0], item[1]))
    return {
        normalize_key(str(k)): str(v)
        for k, v in pairs
        if k and v not in (None, "")
    }


def _first(tags: Mapping[str, str], keys: tuple[str, ...]) -> str | None:
    return next((tags[k] for k in keys if tags.get(k)), None)


def _shift_month(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def period_is_final(billing_period: str, today: date) -> bool:
    """
    FOCUS has no finality column. Clouds close a month's invoice in the first
    days of the next one, then credits and support adjustments can still land
    during that next month. A period is therefore final once the month after
    the one following it has started: only the current and previous months
    are ever revised.
    """
    year, month = map(int, billing_period.split("-"))
    return today >= _shift_month(date(year, month, 1), 2)


def wanted_days(
    today: date, window: list[date], since: date
) -> Callable[[date], bool]:
    """Days to (re)read: the replay window plus every day of a billing period
    that is not final yet (late adjustments), never today's partial day."""
    window_set = set(window)
    oldest_open = _shift_month(today, -1)

    def wanted(day: date) -> bool:
        return since <= day < today and (
            day in window_set or day >= oldest_open
        )

    return wanted


def periods_to_read(today: date, window: list[date], since: date) -> list[str]:
    wanted = wanted_days(today, window, since)
    first = min([_shift_month(today, -1), *window])
    days = (first + timedelta(days=i) for i in range((today - first).days))
    return sorted({d.strftime("%Y-%m") for d in days if wanted(d)})


@dataclass
class _Bucket:  # pylint: disable=too-many-instance-attributes
    day: date
    resource_id: str
    charge_category: str
    service_name: str
    service_category: str
    resource_name: str
    currency: str
    billed: float = 0.0
    effective: float = 0.0
    listed: float = 0.0
    quantity: float | None = None
    unit: str | None = None
    units_differ: bool = False
    tags: dict[str, str] = field(default_factory=dict)


def stable_resource_id(
    cloud: str, service_name: str, resource_id: object
) -> str:
    """Lines without a resource (tax, support) get one id per service; a long
    id is cut and suffixed with a hash of the whole so it stays unique."""
    rid = (
        str(resource_id or "").strip() or f"{cloud}:{service_name or 'other'}"
    )
    if len(rid) <= _MAX_RESOURCE_ID:
        return rid
    digest = hashlib.sha1(rid.encode()).hexdigest()[:16]
    return f"{rid[: _MAX_RESOURCE_ID - 17]}~{digest}"


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


class DayAggregator:
    """One bucket per (day, resource_id, charge_category)."""

    def __init__(self, cloud: str, wanted: Callable[[date], bool]):
        self.cloud = cloud
        self._wanted = wanted
        self._buckets: dict[tuple, _Bucket] = {}

    def add(self, row: Mapping[str, object]) -> None:
        start = row.get("ChargePeriodStart")
        if not isinstance(start, datetime) or not self._wanted(start.date()):
            return
        day = start.date()
        service = str(row.get("ServiceName") or "")
        resource_id = stable_resource_id(
            self.cloud, service, row.get("ResourceId")
        )
        category = str(row.get("ChargeCategory") or "Usage")
        bucket = self._buckets.setdefault(
            (day, resource_id, category),
            _Bucket(
                day=day,
                resource_id=resource_id,
                charge_category=category,
                service_name=service,
                service_category=str(row.get("ServiceCategory") or ""),
                resource_name=str(row.get("ResourceName") or ""),
                currency=str(row.get("BillingCurrency") or "USD"),
            ),
        )
        bucket.billed += _number(row.get("BilledCost"))
        bucket.effective += _number(row.get("EffectiveCost"))
        bucket.listed += _number(row.get("ListCost"))
        quantity = row.get("ConsumedQuantity")
        if quantity is not None:
            unit = str(row.get("ConsumedUnit") or "") or None
            if bucket.quantity is not None and bucket.unit != unit:
                bucket.units_differ = True
            bucket.unit = bucket.unit or unit
            bucket.quantity = (bucket.quantity or 0.0) + _number(quantity)
        for tag, value in parse_tags(row.get("Tags")).items():
            bucket.tags.setdefault(tag, value)

    def lines(
        self,
        *,
        source: str,
        provider_name: str,
        cluster_tag: str,
        rate_for: RateFor,
        today: date,
    ) -> list[CostLine]:
        return [
            _to_line(
                bucket,
                self.cloud,
                source,
                provider_name,
                cluster_tag,
                rate_for,
                today,
            )
            for bucket in self._buckets.values()
        ]


def _to_line(
    bucket: _Bucket,
    cloud: str,
    source: str,
    provider_name: str,
    cluster_tag: str,
    rate_for: RateFor,
    today: date,
) -> CostLine:
    tags = bucket.tags
    rate = rate_for(bucket.currency, bucket.day)
    estimate = rate is None
    if rate is None:
        rate = FALLBACK_USD_PER_EUR if bucket.currency == "USD" else 1.0
    start = datetime.combine(bucket.day, time.min)
    cluster = tags.get(cluster_tag)
    project = _first(tags, _PROJECT_KEYS)
    app = _first(tags, _APP_KEYS)
    env = _first(tags, _ENV_KEYS)
    method = "direct" if project else "unallocated"
    extra = {k: v for k, v in tags.items() if k != cluster_tag}
    if cluster and bucket.service_category == "Compute":
        # Cluster node: shared by every namespace, split later with OpenCost.
        project, app, env, method = CLUSTER, None, None, "unallocated"
        extra = {"k8s_cluster": cluster}
    if bucket.quantity is None or bucket.units_differ:
        quantity, unit = None, None
    else:
        quantity, unit = bucket.quantity, bucket.unit
    return CostLine(
        x_source=source,
        provider_name=provider_name,
        service_name=bucket.service_name,
        service_category=bucket.service_category,
        charge_category=bucket.charge_category,
        charge_period_start=start,
        charge_period_end=start + timedelta(days=1),
        resource_id=bucket.resource_id,
        resource_name=bucket.resource_name,
        x_cost_eur=bucket.effective / rate,
        billed_cost=bucket.billed,
        list_cost=bucket.listed,
        effective_cost=bucket.effective,
        billing_currency=bucket.currency,
        x_fx_rate=rate,
        consumed_quantity=quantity,
        consumed_unit=unit,
        project=project,
        app=app,
        env=env,
        cloud=cloud,
        x_allocation_method=method,
        x_pricing_basis="invoice",
        x_is_final=period_is_final(start.strftime("%Y-%m"), today),
        x_is_estimate=estimate,
        extra_tags=extra,
    )
