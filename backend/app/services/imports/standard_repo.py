"""
The template model for an imported repository (CNP-303), as pure functions.

What the standard pull request adds: deploy/values-<app>.yaml for every app
of the import, which the cnp-apps ApplicationSet reads from the repository
once the record names it as the app's repoURL, and the CI of the template
repositories, building and signing one image per service. Nothing outside
deploy/, cnp/ and .github/ is written: the app code stays as it is.
"""

import json
import re
from dataclasses import dataclass

from app.core.config import settings
from app.models.project import TargetCloud
from app.services.imports import generate
from app.services.imports.generate import AppSpec
from app.services.imports.service import SAFE_PATH

CI_PATH = ".github/workflows/ci.yml"
DOCKERFILE_DIR = "cnp/docker"
WORKFLOWS_DIR = ".github/workflows/"
# Copied as they are from the template repositories, so an imported repo
# gets the same files as one created from a template: path in the imported
# repo -> (template repository, path there).
TEMPLATE_FILES = {
    ".github/dependabot.yml": (
        "template-html-css",
        ".github/dependabot.yml",
    ),
    ".github/workflows/cnp-verify.yml": (
        "template-html-css",
        ".github/workflows/cnp-verify.yml",
    ),
    ".github/workflows/security.yml": (
        "app-templates",
        "templates/k3s-gitops-app/security.yml",
    ),
}
# Where a release's image lives: in cnp-projects, not on a branch of the
# imported repo, since Argo CD refuses two revisions of one repository in an
# application and the values are read from the repo's default branch.
IMAGE_FILE = "apps/{project}/{app}/release.yaml"
# The ephemeral, isolated runners of the import workflow: the CI builds the
# repo's code, never on a persistent runner shared with other jobs.
RUNS_ON = '["self-hosted","cnp-import-ephemeral"]'
BUILDKIT = "tcp://buildkitd:1234"

VALUES_HEADER = (
    "# Deploy values of {app} on CNP, read by Argo CD from {branch}.\n"
    "# Change them here: a merge to {branch} changes the deployment. Names,\n"
    "# links, network policy, route and pull secret come from the platform\n"
    "# and win over this file.\n"
)
CI_IMAGE_NOTE = (
    "# The image tag is not here: a vX.Y.Z tag builds X.Y.Z and Argo CD\n"
    "# Image Updater writes it to apps/<project>/{app}/release.yaml in\n"
    "# 3-Istor/cnp-projects, which Argo CD reads on top of this file.\n"
)
IMAGE_HEADER = (
    "# Release of {app}, written by Argo CD Image Updater on each vX.Y.Z\n"
    "# tag. Until the first one, the image the CNP import built.\n"
)

CI_TEMPLATE = """\
# CNP template CI of an imported repository: one image per service, built on
# every push and pull request to check it builds, published and signed on
# vX.Y.Z tags only. A tag is a release: Argo CD Image Updater deploys X.Y.Z.
# The matrix lists the services of the import: change it when a service
# moves. Jobs run on the CNP import runners (ephemeral, one job each, no
# Docker daemon: BuildKit is remote), never on a shared runner.
name: CI Pipeline

on:
  push:
    branches: [ "**" ]
    tags: [ "v*.*.*" ]
    paths-ignore:
      - "deploy/**"
  pull_request:
    branches: [ "main" ]
    paths-ignore:
      - "deploy/**"

permissions:
  contents: read

jobs:
  docker-build-and-push:
    name: Build ${{ matrix.app }}
    runs-on: ${{ fromJSON(vars.CNP_IMPORT_RUNS_ON || '__RUNS_ON__') }}
    permissions:
      contents: read
      packages: write
      id-token: write
    # Each build gets its own runner and BuildKit on one shared VM: two at a
    # time keep a multi-service repo from filling its disk.
    strategy:
      fail-fast: false
      max-parallel: 2
      matrix:
        include:
__MATRIX__
    env:
      PUBLISH: ${{ github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v') }}
    steps:
      - name: Checkout repository
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4.4.1
        with:
          driver: remote
          endpoint: ${{ vars.CNP_IMPORT_BUILDKIT_ENDPOINT || '__BUILDKIT__' }}

      # No daemon to log in through: BuildKit reads the client's config.
      - name: Registry credentials
        if: env.PUBLISH == 'true'
        env:
          TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ACTOR: ${{ github.actor }}
        run: |
          mkdir -p ~/.docker
          auth=$(printf '%s:%s' "$ACTOR" "$TOKEN" | base64 -w0)
          printf '{"auths":{"ghcr.io":{"auth":"%s"}}}' "$auth" > ~/.docker/config.json

      - name: Refuse to overwrite a released version
        # A version is built once: rebuilding under the same tag would put an
        # image nobody tested behind a name that was.
        if: env.PUBLISH == 'true'
        env:
          IMAGE: ${{ matrix.image }}
        run: |
          image="${IMAGE}:${GITHUB_REF_NAME#v}"
          if docker buildx imagetools inspect "$image" >/dev/null 2>&1; then
            echo "::error::$image already exists. Push a new tag instead of moving this one."
            exit 1
          fi

      - name: Extract metadata (tags, labels)
        id: meta
        uses: docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302 # v6.2.0
        with:
          images: ${{ matrix.image }}
          flavor: latest=false
          tags: |
            type=semver,pattern={{version}}
            type=sha,prefix=sha-

      - name: Build and push the image
        id: build
        uses: docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc # v7.4.0
        with:
          context: ${{ matrix.context }}
          file: ${{ matrix.file }}
          target: ${{ matrix.target }}
          push: ${{ env.PUBLISH == 'true' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: |
            ${{ steps.meta.outputs.labels }}
            org.opencontainers.image.source=https://github.com/${{ github.repository }}

      # Not sigstore/cosign-installer: it needs envsubst, which the ephemeral
      # import runners lack. Same cosign release, checksum verified.
      - name: Install cosign
        if: env.PUBLISH == 'true'
        env:
          COSIGN_VERSION: "3.1.4"
          COSIGN_SHA256: 424e2c97d0b45fd7a6b608a87f090f35633f4c23ece5f092d48bba11aaf4bbfa
        run: |
          set -euo pipefail
          bin="$RUNNER_TEMP/bin"
          mkdir -p "$bin"
          curl --fail --silent --show-error --location --proto '=https' --output "$bin/cosign" "https://github.com/sigstore/cosign/releases/download/v${COSIGN_VERSION}/cosign-linux-amd64"
          echo "${COSIGN_SHA256}  $bin/cosign" | sha256sum --check --strict
          chmod 0755 "$bin/cosign"
          echo "$bin" >> "$GITHUB_PATH"

      # Keyless: the certificate binds the signature to this workflow and tag
      # through the GitHub OIDC token, so there is no key to store or rotate.
      # Admission checks it to refuse images built outside this CI (CNP-299).
      - name: Sign image
        if: env.PUBLISH == 'true'
        env:
          IMAGE: ${{ matrix.image }}@${{ steps.build.outputs.digest }}
        run: cosign sign --yes "$IMAGE"

  # Merged by the workflow once the images build: GitHub auto-merge would need
  # a repo setting and branch protection that imported repos may not have.
  dependabot-merge:
    runs-on: ${{ fromJSON(vars.CNP_IMPORT_RUNS_ON || '__RUNS_ON__') }}
    needs: [docker-build-and-push]
    if: github.event_name == 'pull_request' && github.actor == 'dependabot[bot]'
    permissions:
      contents: write
      pull-requests: write
    steps:
      - id: metadata
        uses: dependabot/fetch-metadata@25dd0e34f4fe68f24cc83900b1fe3fe149efef98 # v3.1.0

      - name: Merge patch and minor updates
        if: >-
          steps.metadata.outputs.update-type == 'version-update:semver-patch' ||
          steps.metadata.outputs.update-type == 'version-update:semver-minor'
        run: gh pr merge --squash "$PR_URL"
        env:
          PR_URL: ${{ github.event.pull_request.html_url }}
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
"""


@dataclass(frozen=True)
class Build:
    app: str
    context: str
    file: str
    target: str
    image: str


IMPORT_RUNS_ON = (
    "runs-on: ${{ fromJSON(vars.CNP_IMPORT_RUNS_ON || '__RUNS_ON__') }}"
)
_TEMPLATE_RUNS_ON = re.compile(r"runs-on:[^\n]*")


def on_import_runners(workflow: str) -> str:
    """A template workflow with every job moved to the ephemeral import runners.

    The template repos run on the org's persistent runners, which must never
    execute an imported repository's code.
    """
    runs_on = IMPORT_RUNS_ON.replace("__RUNS_ON__", RUNS_ON)
    workflow = _TEMPLATE_RUNS_ON.sub(lambda _match: runs_on, workflow)
    # The template's image scan runs docker build on the repo's own
    # Dockerfiles: the import runners have no Docker daemon, and an imported
    # repo's Dockerfiles may be the ones the analyzer had to repair. Its images
    # are scanned when the analyzer builds them and in the cluster by Trivy.
    return workflow.replace(
        "    if: needs.discover-images.outputs.matrix != '[]'",
        "    if: false",
    )


def values_file(app: str) -> str:
    return f"deploy/values-{app}.yaml"


def ci_image(project: str, app: str) -> str:
    """
    A package of its own: the import workflow's package of the same app
    belongs to extern-project-analyzer, which the repo's CI cannot push to.
    Under imports/<project>/, the only org images restrict-tenant-images
    lets the project's namespaces run.
    """
    return f"{settings.IMPORT_IMAGE_PREFIX}/{project}/{app}/ci".lower()


def dockerfile_path(app: str) -> str:
    return f"{DOCKERFILE_DIR}/{app}.Dockerfile"


def _safe(path: str | None) -> bool:
    return (
        path is not None
        and bool(SAFE_PATH.fullmatch(path))
        and ".." not in path.split("/")
    )


def builds(
    project: str, specs: list[AppSpec], plan: dict
) -> tuple[list[Build], dict[str, str], dict[str, str]]:
    """
    The CI matrix of the services, the Dockerfiles the analyzer repaired
    (written under cnp/docker, the repo's own left as they are), and the
    services the CI cannot build, with why.
    """
    recipes = {s["name"]: s["build"] for s in plan.get("services") or []}
    matrix: list[Build] = []
    files: dict[str, str] = {}
    excluded: dict[str, str] = {}
    for spec in sorted(specs, key=lambda s: s.name):
        if spec.kind != "service":
            continue
        build = recipes.get(spec.plan_name) or {}
        strategy = build.get("strategy")
        context = build.get("context") or "."
        target = build.get("stage") or ""
        if strategy == "dockerfile" and _safe(build.get("dockerfile")):
            dockerfile = build["dockerfile"]
        elif strategy == "repaired_dockerfile" and build.get(
            "generated_dockerfile"
        ):
            dockerfile = dockerfile_path(spec.name)
            files[dockerfile] = build["generated_dockerfile"]
            if build.get("generated_dockerignore"):
                # BuildKit reads <Dockerfile>.dockerignore before the
                # context's own .dockerignore.
                files[f"{dockerfile}.dockerignore"] = build[
                    "generated_dockerignore"
                ]
        else:
            excluded[spec.name] = (
                f"built by the platform with {strategy or 'no recipe'}: the "
                "standard CI builds from a Dockerfile, add one and list the "
                f"service in {CI_PATH}"
            )
            continue
        if not _safe(context) or (target and not _safe(target)):
            excluded[spec.name] = "unsafe build path in the analysis"
            continue
        matrix.append(
            Build(
                spec.name,
                context,
                dockerfile,
                target,
                ci_image(project, spec.name),
            )
        )
    return matrix, files, excluded


def ci_workflow(matrix: list[Build]) -> str:
    # JSON is YAML: every value is quoted, none can open a ${{ }} expression
    # (the paths are SAFE_PATH, the names DNS labels).
    lines = "\n".join(
        "          - "
        + json.dumps(
            {
                "app": b.app,
                "context": b.context,
                "file": b.file,
                "target": b.target,
                "image": b.image,
            }
        )
        for b in matrix
    )
    return (
        CI_TEMPLATE.replace("__MATRIX__", lines)
        .replace("__RUNS_ON__", RUNS_ON)
        .replace("__BUILDKIT__", BUILDKIT)
    )


def repo_values(  # pylint: disable=too-many-arguments
    project: str,
    specs: list[AppSpec],
    digests: dict[str, str],
    built: set[str],
    branch: str,
    cloud: TargetCloud,
    wake_order: dict[str, int],
) -> dict[str, str]:
    """
    deploy/values-<app>.yaml of every app, the values the CMP writes to
    cnp-projects today. A service the CI builds follows its CI image; one
    it cannot build keeps the platform's last digest.
    """
    files: dict[str, str] = {}
    for spec in specs:
        if spec.kind == "backing":
            values = generate.backing_values(project, spec)
        elif spec.name in digests:
            values = generate.service_values(
                project,
                spec,
                settings.IMPORT_IMAGE_PREFIX,
                digests[spec.name],
                cloud,
            )
        else:
            continue
        header = VALUES_HEADER.format(app=spec.name, branch=branch)
        if spec.name in built:
            values["image"] = {"repository": ci_image(project, spec.name)}
            header += CI_IMAGE_NOTE.format(app=spec.name)
        values["offhours"] = generate.offhours_values(
            spec, wake_order.get(spec.name, 0)
        )
        files[values_file(spec.name)] = generate.dump_yaml(values, header)
    return files


def image_files(
    project: str, built: set[str], digests: dict[str, str]
) -> dict[str, str]:
    """
    apps/<project>/<app>/release.yaml in cnp-projects for each service the CI
    builds, until its first release: the image the import built, pinned by
    digest through the tag (the chart's image.digest would outlive the
    release the updater writes as image.tag).
    """
    return {
        IMAGE_FILE.format(project=project, app=app): generate.dump_yaml(
            {
                "image": {
                    "repository": f"{settings.IMPORT_IMAGE_PREFIX}/"
                    f"{project}/{app}".lower(),
                    "tag": f"imported@{digests[app]}",
                }
            },
            IMAGE_HEADER.format(app=app),
        )
        for app in sorted(built)
        if app in digests
    }


def conflicts(existing: set[str], files: set[str]) -> list[dict]:
    """
    What the repository already has where the pull request writes: files it
    replaces, and files it keeps that may deploy or build the app too.
    """
    found = []
    for path in sorted(existing):
        if path in files:
            found.append({"path": path, "kind": "replaced"})
        elif path.startswith("deploy/"):
            found.append({"path": path, "kind": "deploy"})
        elif path.startswith(WORKFLOWS_DIR) and path.endswith(
            (".yml", ".yaml")
        ):
            found.append({"path": path, "kind": "workflow"})
    return found


CONFLICT_TEXT = {
    "replaced": "replaced by this pull request",
    "deploy": "kept in deploy/: check it is not meant for another deployment",
    "workflow": "kept: check it does not build or deploy these services too",
}


def pr_body(  # pylint: disable=too-many-arguments
    project: str,
    branch: str,
    values: list[str],
    matrix: list[Build],
    found: list[dict],
    excluded: dict[str, str],
) -> str:
    lines = [
        f"CNP opens this pull request so this repository deploys project "
        f"`{project}` like an app created from a CNP template.",
        "",
        "## What it adds",
        "",
        f"- `deploy/values-<app>.yaml` for {len(values)} apps: Argo CD reads "
        f"them from `{branch}` once this is merged. Edit them to change a "
        "deployment, no CMP needed.",
        f"- `{CI_PATH}`: builds {len(matrix)} images on every push to check "
        "them, publishes and signs them on `vX.Y.Z` tags only. A tag is a "
        "release: CNP deploys X.Y.Z, like an app created from a template. "
        "The release lands in CNP's own registry, nothing is pushed to "
        f"`{branch}`.",
        "- The security scans, Dependabot settings and verification workflow "
        "of the template repositories.",
        "",
        "No application code changes.",
        "",
        "## Until it is merged",
        "",
        "The apps keep running from the platform's copy of their settings, "
        "rebuilt by the platform on each push. Once merged, the platform "
        "stops building them and the CMP no longer changes their settings. "
        "They keep running the image CNP built until your first `vX.Y.Z` "
        "tag.",
        "",
        "## Runners",
        "",
        "The CI runs on the CNP import runners (runner group "
        "`cnp-import-ephemeral`: one job per runner, then destroyed), never "
        "on a shared runner. A CNP organization admin must give this "
        "repository access to that group (Organization settings > Actions > "
        "Runner groups > cnp-import-ephemeral: add the repository, and allow "
        "its `.github/workflows/ci.yml` if the group is limited to selected "
        "workflows). Until then the jobs wait for a runner.",
    ]
    if found:
        lines += ["", "## Conflicts", ""]
        lines += [
            f"- `{c['path']}`: {CONFLICT_TEXT[c['kind']]}" for c in found
        ]
    if excluded:
        lines += ["", "## Not built by this CI", ""]
        lines += [f"- `{app}`: {why}" for app, why in sorted(excluded.items())]
    return "\n".join(lines) + "\n"
