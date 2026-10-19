"""Tag to digest, asked to GHCR with the platform's pull credentials."""

import httpx

from app.core.config import settings

ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


class RegistryLookupError(Exception):
    pass


def resolve(repository: str, tag: str) -> str:
    """
    repository@sha256 that *tag* points at now.

    Only for clusters the CMP cannot read pods on: a tag can move, a running
    pod's imageID cannot. The app CIs refuse to push a version twice, which
    is what makes the answer stable.

    Raises:
        RegistryLookupError: Not a GHCR image, or GHCR refused or failed.
    """
    host, _, path = repository.partition("/")
    if host != "ghcr.io":
        raise RegistryLookupError(f"{repository} is not on ghcr.io")
    try:
        with httpx.Client(timeout=10.0) as client:
            token = client.get(
                "https://ghcr.io/token",
                params={
                    "scope": f"repository:{path}:pull",
                    "service": "ghcr.io",
                },
                auth=(
                    settings.GITHUB_REGISTRY_USERNAME,
                    settings.GITHUB_REGISTRY_TOKEN,
                ),
            )
            token.raise_for_status()
            manifest = client.head(
                f"https://ghcr.io/v2/{path}/manifests/{tag}",
                headers={
                    "Authorization": f"Bearer {token.json()['token']}",
                    "Accept": ACCEPT,
                },
            )
            manifest.raise_for_status()
    except httpx.HTTPError as exc:
        raise RegistryLookupError(f"{repository}:{tag}: {exc}") from exc
    digest = manifest.headers.get("docker-content-digest")
    if not digest:
        raise RegistryLookupError(f"{repository}:{tag}: GHCR gave no digest")
    return f"{repository}@{digest}"
