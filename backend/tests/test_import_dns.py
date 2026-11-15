import asyncio

import pytest

from app.core.config import settings
from app.services.imports import dns

HOST = "web-shop.3istor.com"
TUNNEL = "t-1.cfargotunnel.com"


class Cloudflare:
    def __init__(self, records):
        self.records = records
        self.writes: list[tuple[str, str]] = []

    async def call(self, client, method, path, **kw):
        if path.endswith("/cfd_tunnel"):
            return [{"id": "t-1"}]
        if method == "GET":
            return list(self.records)
        self.writes.append((method, path))
        return {}


@pytest.fixture
def cloudflare(monkeypatch):
    monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "t")
    monkeypatch.setattr(settings, "CLOUDFLARE_ZONE_ID", "z")
    monkeypatch.setattr(settings, "CLOUDFLARE_ACCOUNT_ID", "a")

    def install(records):
        fake = Cloudflare(records)
        monkeypatch.setattr(dns, "_call", fake.call)
        return fake

    return install


def _record(comment, content="old.cfargotunnel.com"):
    return {
        "id": "r1",
        "type": "CNAME",
        "content": content,
        "proxied": True,
        "comment": comment,
    }


def test_marker_names_the_app_and_fits_cloudflare_comments():
    marker = dns.marker("p" * 40, "a" * 18)

    assert "cnp.app=" + "a" * 18 in marker
    assert len(marker) <= 100


def test_new_cname_is_created_with_the_app_marker(cloudflare):
    fake = cloudflare([])

    asyncio.run(dns.ensure_cname("shop", "web", HOST))

    assert fake.writes == [("POST", "/zones/z/dns_records")]


@pytest.mark.parametrize(
    "comment",
    [
        # A template app of the project (k3s-gitops-app marker).
        "cnp.project=shop cnp.cloud=onprem cnp.env=prod",
        # Another import app of the project.
        "cnp.project=shop cnp.app=api cnp.env=prod",
        None,
    ],
)
def test_record_of_something_else_is_never_adopted(cloudflare, comment):
    fake = cloudflare([_record(comment)])

    with pytest.raises(dns.DnsError):
        asyncio.run(dns.ensure_cname("shop", "web", HOST))

    assert fake.writes == []


def test_record_of_the_same_app_is_repointed(cloudflare):
    fake = cloudflare([_record(dns.marker("shop", "web"))])

    asyncio.run(dns.ensure_cname("shop", "web", HOST))

    assert fake.writes == [("PATCH", "/zones/z/dns_records/r1")]


def test_removal_deletes_only_the_apps_own_record(cloudflare):
    fake = cloudflare(
        [_record("cnp.project=shop cnp.cloud=onprem cnp.env=prod")]
    )

    asyncio.run(dns.remove_cname("shop", "web", HOST))

    assert fake.writes == []
