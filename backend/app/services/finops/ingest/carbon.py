"""
Carbon intensity of the French grid, one daily mean per day.

Direct method: RTE éCO2mix ``taux_co2`` (gCO2e/kWh, emissions of the plants
running at that moment). The consolidated dataset lags by months, so the
real-time one fills the recent days; consolidated values win once they exist.
Life-cycle method: a fixed ADEME factor, applied at read time.
"""

from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finops import CarbonIntensity

ZONE = "FR"
METHOD_DIRECT = "direct"
METHOD_LCA = "lca"
CARBON_METHOD_DIRECT = "rte-direct"

# "opendata.reseaux-energies.fr" redirects (301) to this Opendatasoft host.
ODRE_RECORDS_URL = (
    "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
    "{dataset}/records"
)
DATASET_CONSOLIDATED = "eco2mix-national-cons-def"
DATASET_REALTIME = "eco2mix-national-tr"

# ADEME Base Empreinte, element 43642: "Electricité - mix moyen 2024 -
# consommation - France continentale", 0.0519 kgCO2e/kWh (life cycle,
# upstream included), modified 2025-04-24.
LCA_G_PER_KWH = 51.9
LCA_SOURCE = "ADEME Base Empreinte 43642 (mix moyen 2024, France continentale)"


def _fetch_daily(dataset: str, since: date) -> dict[date, float]:
    response = httpx.get(
        ODRE_RECORDS_URL.format(dataset=dataset),
        params={
            "select": "date,avg(taux_co2) as g_per_kwh",
            "where": f"date_heure >= date'{since.isoformat()}'",
            "group_by": "date",
            "order_by": "date asc",
            "limit": 100,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return {
        date.fromisoformat(row["date"]): float(row["g_per_kwh"])
        for row in response.json()["results"]
        if row.get("g_per_kwh") is not None
    }


def fetch(since: date) -> dict[date, tuple[float, str]]:
    """Daily mean gCO2e/kWh since ``since`` with the dataset it came from."""
    out: dict[date, tuple[float, str]] = {}
    for dataset in (DATASET_REALTIME, DATASET_CONSOLIDATED):
        for day, value in _fetch_daily(dataset, since).items():
            out[day] = (value, dataset)
    return out


def refresh(db: Session, since: date) -> int:
    written = 0
    for day, (value, source) in fetch(since).items():
        row = db.scalar(
            select(CarbonIntensity).where(
                CarbonIntensity.day == day,
                CarbonIntensity.zone == ZONE,
                CarbonIntensity.method == METHOD_DIRECT,
            )
        )
        if row is None:
            db.add(
                CarbonIntensity(
                    day=day,
                    zone=ZONE,
                    g_per_kwh=value,
                    method=METHOD_DIRECT,
                    source=source,
                )
            )
        else:
            row.g_per_kwh = value
            row.source = source
        written += 1
    db.commit()
    return written


def intensity_for(db: Session, day: date) -> float | None:
    return db.scalar(
        select(CarbonIntensity.g_per_kwh).where(
            CarbonIntensity.day == day,
            CarbonIntensity.zone == ZONE,
            CarbonIntensity.method == METHOD_DIRECT,
        )
    )


def co2e_kg(energy_kwh: float, g_per_kwh: float) -> float:
    return energy_kwh * g_per_kwh / 1000


def apply_to_lines(lines, g_per_kwh: float | None) -> None:
    if g_per_kwh is None:
        return
    for line in lines:
        if line.x_energy_kwh is None:
            continue
        line.x_co2e_kg = co2e_kg(line.x_energy_kwh, g_per_kwh)
        line.x_carbon_method = CARBON_METHOD_DIRECT
