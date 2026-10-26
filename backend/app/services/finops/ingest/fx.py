"""ECB reference rates, stored per day. 1 EUR = rate × currency."""

import logging
import xml.etree.ElementTree as ET
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finops import FxRate

logger = logging.getLogger(__name__)

ECB_DAILY = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_NS = {"e": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}


def parse_ecb(xml: str) -> tuple[date, dict[str, float]]:
    root = ET.fromstring(xml)
    day_node = root.find(".//e:Cube[@time]", _NS)
    if day_node is None:
        raise ValueError("ECB feed has no dated Cube")
    rates = {
        node.attrib["currency"]: float(node.attrib["rate"])
        for node in day_node.findall("e:Cube", _NS)
    }
    return date.fromisoformat(day_node.attrib["time"]), rates


def refresh(db: Session) -> None:
    """Store today's ECB rates; a failure keeps the last known ones."""
    try:
        response = httpx.get(ECB_DAILY, timeout=10.0)
        response.raise_for_status()
        day, rates = parse_ecb(response.text)
    except (httpx.HTTPError, ValueError, ET.ParseError) as exc:
        logger.warning("FinOps: ECB rates not refreshed: %s", exc)
        return
    for currency, rate in rates.items():
        exists = db.scalar(
            select(FxRate).where(
                FxRate.day == day, FxRate.currency == currency
            )
        )
        if exists is None:
            db.add(FxRate(day=day, currency=currency, rate=rate))
    db.commit()


def usd_per_eur(db: Session, day: date) -> float | None:
    """The latest USD rate published on or before ``day``."""
    return db.scalar(
        select(FxRate.rate)
        .where(FxRate.currency == "USD", FxRate.day <= day)
        .order_by(FxRate.day.desc())
        .limit(1)
    )
