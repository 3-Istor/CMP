"""The versioned FinOps inputs of the K3s repo (finops/*.yaml)."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ruamel.yaml import YAML

from app.core.config import settings


def _load(name: str) -> dict:
    path = Path(settings.FINOPS_DATA_DIR) / name
    with path.open(encoding="utf-8") as handle:
        return YAML(typ="safe").load(handle) or {}


@dataclass(frozen=True)
class OnPremTCO:
    eur_per_kwh: float
    cluster_watts: float
    cluster_eur_per_hour: float
    gpu_server_watts: float
    has_mock: bool
    gpu_has_mock: bool

    @property
    def energy_share(self) -> float:
        """Part of the cluster's hourly cost that is electricity."""
        energy = self.cluster_watts / 1000 * self.eur_per_kwh
        if self.cluster_eur_per_hour <= 0:
            return 0.0
        return energy / self.cluster_eur_per_hour


def onprem_tco() -> OnPremTCO:
    raw = _load("onprem-tco.yaml")
    power = raw.get("power", {})
    network = raw.get("network", {})
    gpu = raw.get("gpu_server", {})
    eur_per_kwh = float(power.get("electricity_eur_per_kwh_ttc", 0))
    watts = float(power.get("measured_watts", 0))
    amortization = sum(
        float(h.get("purchase_eur", 0))
        / (float(h["amortization_months"]) * 730)
        for h in raw.get("hypervisors", [])
        if float(h.get("purchase_eur", 0)) > 0
    )
    hourly = (
        watts / 1000 * eur_per_kwh
        + amortization
        + float(network.get("internet_eur_per_month", 0))
        * float(network.get("platform_share", 0))
        / 730
    )
    return OnPremTCO(
        eur_per_kwh=eur_per_kwh,
        cluster_watts=watts,
        cluster_eur_per_hour=hourly,
        gpu_server_watts=float(gpu.get("mean_watts", 0)),
        has_mock=any(
            bool(section.get("mock"))
            for section in (power, network)
            if isinstance(section, dict)
        ),
        gpu_has_mock=bool(gpu.get("mock")),
    )


@dataclass(frozen=True)
class ModelPrice:
    provider: str
    service: str
    model: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    effective_from: date


def ai_prices() -> tuple[list[ModelPrice], set[str]]:
    raw = _load("ai-prices.yaml")
    prices = [
        ModelPrice(
            provider=m["provider"],
            service=m["service"],
            model=m["model"],
            input_usd_per_mtok=float(m["input_usd_per_mtok"]),
            output_usd_per_mtok=float(m["output_usd_per_mtok"]),
            effective_from=date.fromisoformat(str(m["effective_from"])),
        )
        for m in raw.get("models", [])
    ]
    return prices, set(raw.get("local_backends", []))


def price_for(
    prices: list[ModelPrice], model: str, day: date
) -> ModelPrice | None:
    """The price of ``model`` in force on ``day``."""
    candidates = [
        p for p in prices if p.model == model and p.effective_from <= day
    ]
    return max(candidates, key=lambda p: p.effective_from, default=None)


def saas_subscriptions() -> list[dict]:
    return list(_load("saas.yaml").get("subscriptions", []))
