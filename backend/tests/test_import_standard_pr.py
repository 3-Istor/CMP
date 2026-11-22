"""The standard pull request of an import and the switch to it (CNP-303)."""

import asyncio
import copy

import pytest
from ruamel.yaml import YAML

from app.core.config import settings
from app.models.imports import ImportRun, ImportSource
from app.services.github_service import GitHubAppError
from app.services.imports import (
    generate,
    rebuilds,
    service,
    standard_pr,
    standard_repo,
)
from app.services.imports.service import ImportRefused
from tests.test_import_deploy import (  # noqa: F401
    PLAN,
    _deploy,
    _published_run,
    world,
)

HEAD = "f" * 40
MERGE = "e" * 40
REPO_URL = "https://github.com/3-Istor/epitweet"


class GitHub:
    """The imported repository and the template repositories."""

    def __init__(self):
        self.tree: set[str] = {"README.md", "tinyx/pom.xml"}
        self.merged_tree: set[str] | None = None
        self.deploy_tree: set[str] = set()
        self.created: dict[str, str] = {}
        self.branches: dict[str, str] = {}
        self.commits: list[dict] = []
        self.pulls: list[dict] = []
        self.pull_state = {"state": "open", "merged": False}
        self.tokens: list[dict] = []

    async def token(self, installation, repos, permissions):
        self.tokens.append(dict(permissions))
        return "ghs_scoped"

    async def org_token(self):
        return "ghs_org"

    async def file(self, token, repo, path, ref="main"):
        return f"# {repo}/{path}\n", "sha"

    async def head(self, token, repo, branch="main"):
        if branch == "main":
            return HEAD
        if branch not in self.branches:
            raise GitHubAppError(f"no branch {branch}")
        return self.branches[branch]

    async def paths(self, token, repo, sha):
        if sha == "d" * 40:
            return set(self.deploy_tree)
        if sha != MERGE:
            return set(self.tree)
        if self.merged_tree is not None:
            return set(self.merged_tree)
        return self.tree | set(self.commits[0]["files"])

    async def branch(self, token, repo, branch, sha):
        self.created[branch] = sha
        self.branches[branch] = sha

    async def commit(self, token, repo, files, message, parent_sha, branch):
        self.commits.append(
            {"files": files, "parent": parent_sha, "branch": branch}
        )
        self.branches[branch] = "d" * 40
        return "d" * 40

    async def open(self, token, repo, head, base, title, body):
        self.pulls.append({"head": head, "base": base, "body": body})
        return {"number": 7, "html_url": f"{REPO_URL}/pull/7"}

    async def pull(self, token, repo, number):
        return {
            **self.pull_state,
            "merge_commit_sha": MERGE,
            "merged_by": {"login": "brian"},
        }


@pytest.fixture
def github(world, monkeypatch):
    Session, _ = world
    hub = GitHub()
    monkeypatch.setattr(settings, "IMPORT_STANDARD_PR_ENABLED", True)
    monkeypatch.setattr(standard_pr, "SessionLocal", Session)
    for name, fake in (
        ("get_repository_id_scoped_token", hub.token),
        ("get_org_installation_token", hub.org_token),
        ("get_file_content", hub.file),
        ("get_branch_head", hub.head),
        ("list_tree_paths", hub.paths),
        ("create_branch", hub.branch),
        ("commit_files", hub.commit),
        ("open_pull_request", hub.open),
        ("get_pull_request", hub.pull),
    ):
        monkeypatch.setattr(standard_pr, name, fake)
    monkeypatch.setattr(standard_pr, "commit_record_change", world[1].commit)
    monkeypatch.setattr(
        standard_pr.audit, "record_system_event", lambda *a, **k: None
    )
    return hub


def _deployed(world, **source_fields) -> str:
    """An import deployed before its pull request could open on its own."""
    Session, _ = world
    run_id = _published_run(Session)
    settings.IMPORT_STANDARD_PR_ENABLED = False
    _deploy(run_id)
    settings.IMPORT_STANDARD_PR_ENABLED = True
    with Session() as db:
        source = db.query(ImportSource).one()
        for key, value in source_fields.items():
            setattr(source, key, value)
        db.get(ImportRun, run_id).plan = {"plan": PLAN, "edits": {}}
        db.commit()
        return source.id


def _open(world, source_id) -> dict:
    Session, _ = world
    with Session() as db:
        source = db.get(ImportSource, source_id)
        return asyncio.run(standard_pr.open_pr(db, source, "alice"))


def _source(world, source_id) -> ImportSource:
    with world[0]() as db:
        return db.get(ImportSource, source_id)


def _load(text: str) -> dict:
    return YAML(typ="safe").load(text)


# ── What the pull request adds ──────────────────────────────────────────────


def test_pull_request_adds_one_values_file_per_app_of_epitweet(world, github):
    source_id = _deployed(world)

    _open(world, source_id)

    values = {p for p in github.commits[0]["files"] if p.startswith("deploy/")}
    assert values == {
        standard_repo.values_file(a)
        for a in (
            "mongodb",
            "redis",
            "neo4j",
            "elasticsearch",
            "user-service",
            "post-service",
            "like-service",
            "social-graph-service",
            "timeline-service",
            "frontend",
            "search-service",
        )
    }


def test_pull_request_never_writes_outside_deploy_cnp_and_github(
    world, github
):
    source_id = _deployed(world)

    _open(world, source_id)

    assert all(
        path.split("/")[0] in ("deploy", "cnp", ".github")
        for path in github.commits[0]["files"]
    )


def test_pull_request_is_committed_on_its_own_branch_off_the_head(
    world, github
):
    source_id = _deployed(world)

    _open(world, source_id)

    assert github.commits[0]["parent"] == HEAD
    assert github.pulls[0]["base"] == "main"
    assert github.created == {github.pulls[0]["head"]: HEAD}


def test_pull_request_token_writes_contents_pulls_and_workflows_only(
    world, github
):
    source_id = _deployed(world)

    _open(world, source_id)

    assert standard_pr.WRITE in github.tokens
    assert all(
        set(t) <= {"contents", "pull_requests", "workflows"}
        for t in github.tokens
    )


def test_a_ci_built_service_names_its_ci_image_but_no_release_on_main(
    world, github
):
    source_id = _deployed(world)

    _open(world, source_id)

    values = _load(
        github.commits[0]["files"]["deploy/values-user-service.yaml"]
    )
    assert values["image"] == {
        "repository": "ghcr.io/3-istor/imports/epitweet/user-service/ci"
    }


def test_ci_publishes_and_signs_on_release_tags_only(world, github):
    source_id = _deployed(world)

    _open(world, source_id)

    job = _load(github.commits[0]["files"][standard_repo.CI_PATH])["jobs"][
        "docker-build-and-push"
    ]
    assert job["env"]["PUBLISH"] == (
        "${{ github.event_name == 'push' && "
        "startsWith(github.ref, 'refs/tags/v') }}"
    )


def test_ci_runs_on_the_ephemeral_import_runners_only(world, github):
    source_id = _deployed(world)

    _open(world, source_id)

    jobs = _load(github.commits[0]["files"][standard_repo.CI_PATH])["jobs"]
    assert {job["runs-on"] for job in jobs.values()} == {
        "${{ fromJSON(vars.CNP_IMPORT_RUNS_ON || "
        '\'["self-hosted","cnp-import-ephemeral"]\') }}'
    }


def test_backing_values_are_the_catalogue_preset(world, github):
    source_id = _deployed(world)

    _open(world, source_id)

    values = _load(github.commits[0]["files"]["deploy/values-mongodb.yaml"])
    preset = generate.load_preset("mongodb")
    assert values["image"] == preset["image"]


def test_network_policy_is_left_to_the_platform(world, github):
    source_id = _deployed(world)

    _open(world, source_id)

    assert all(
        "network" not in _load(text)
        for path, text in github.commits[0]["files"].items()
        if path.startswith("deploy/")
    )


def test_a_repaired_dockerfile_lands_under_cnp_docker(world, github):
    source_id = _deployed(world)

    _open(world, source_id)

    files = github.commits[0]["files"]
    assert "cnp/docker/user-service.Dockerfile" in files
    assert "cnp/docker/user-service.Dockerfile.dockerignore" in files


def test_ci_builds_every_service_from_its_recipe(world, github):
    source_id = _deployed(world)

    _open(world, source_id)

    workflow = _load(github.commits[0]["files"][standard_repo.CI_PATH])
    matrix = {
        m["app"]: m
        for m in workflow["jobs"]["docker-build-and-push"]["strategy"][
            "matrix"
        ]["include"]
    }
    assert len(matrix) == 7
    assert matrix["frontend"]["file"] == "frontend/Dockerfile"
    assert matrix["user-service"] == {
        "app": "user-service",
        "context": "tinyx",
        "file": "cnp/docker/user-service.Dockerfile",
        "target": "",
        "image": "ghcr.io/3-istor/imports/epitweet/user-service/ci",
    }


def test_the_template_files_are_copied_from_the_template_repositories(
    world, github
):
    source_id = _deployed(world)

    _open(world, source_id)

    files = github.commits[0]["files"]
    assert files[".github/workflows/security.yml"] == (
        "# 3-Istor/app-templates/templates/k3s-gitops-app/security.yml\n"
    )


def test_a_successful_first_deploy_opens_the_pull_request(world, github):
    Session, _ = world
    run_id = _published_run(Session)

    _deploy(run_id)

    with Session() as db:
        state = db.query(ImportSource).one().standard_repo
    assert state["state"] == "open"
    assert state["url"] == f"{REPO_URL}/pull/7"


# ── Conflicts ───────────────────────────────────────────────────────────────


def test_existing_ci_and_deploy_files_are_flagged_as_conflicts(world, github):
    github.tree |= {
        ".github/workflows/ci.yml",
        ".github/workflows/release.yaml",
        "deploy/k8s.yaml",
    }
    source_id = _deployed(world)

    state = _open(world, source_id)

    assert state["conflicts"] == [
        {"path": ".github/workflows/ci.yml", "kind": "replaced"},
        {"path": ".github/workflows/release.yaml", "kind": "workflow"},
        {"path": "deploy/k8s.yaml", "kind": "deploy"},
    ]
    assert "`deploy/k8s.yaml`" in github.pulls[0]["body"]


def test_a_service_without_a_dockerfile_keeps_its_platform_digest():
    specs = [
        generate.AppSpec(name="api", kind="service", plan_name="api", ports=[])
    ]
    plan = {"services": [{"name": "api", "build": {"strategy": "railpack"}}]}

    matrix, _, excluded = standard_repo.builds("shop", specs, plan)

    assert not matrix
    assert "api" in excluded


def test_an_unsafe_build_path_is_never_written_into_the_workflow():
    specs = [generate.AppSpec(name="api", kind="service", plan_name="api")]
    plan = {
        "services": [
            {
                "name": "api",
                "build": {
                    "strategy": "dockerfile",
                    "context": "${{ secrets.X }}",
                    "dockerfile": "Dockerfile",
                },
            }
        ]
    }

    matrix, _, excluded = standard_repo.builds("shop", specs, plan)

    assert not matrix
    assert excluded == {"api": "unsafe build path in the analysis"}


# ── Refusals ────────────────────────────────────────────────────────────────


def test_repository_outside_the_org_cannot_follow_the_template_model(
    world, github
):
    source_id = _deployed(world, repo_full_name="alice/epitweet")

    with pytest.raises(ImportRefused, match="not a repository of"):
        _open(world, source_id)


def test_a_second_pull_request_is_refused_while_one_is_open(world, github):
    source_id = _deployed(world)
    _open(world, source_id)

    with pytest.raises(ImportRefused, match="already open"):
        _open(world, source_id)


def test_pull_request_is_refused_while_the_feature_is_off(
    world, github, monkeypatch
):
    source_id = _deployed(world)
    monkeypatch.setattr(settings, "IMPORT_STANDARD_PR_ENABLED", False)

    with pytest.raises(ImportRefused, match="not enabled"):
        _open(world, source_id)


# ── Merge and switch ────────────────────────────────────────────────────────


def _merged(world, github) -> str:
    source_id = _deployed(world)
    _open(world, source_id)
    github.pull_state = {"state": "closed", "merged": True}
    return source_id


def test_merge_points_the_apps_at_the_repository(world, github):
    source_id = _merged(world, github)

    asyncio.run(standard_pr.refresh(source_id))

    apps = world[1].record["spec"]["apps"]
    assert {a["name"] for a in apps if a.get("repoURL") == REPO_URL} == {
        a["name"] for a in apps
    }


def test_merge_removes_the_values_files_from_cnp_projects(world, github):
    source_id = _merged(world, github)

    asyncio.run(standard_pr.refresh(source_id))

    files = world[1].commits[-1]["files"]
    removed = {path for path, text in files.items() if text is None}
    assert len(removed) == 11
    assert all(path.endswith("/values.yaml") for path in removed)


def test_merge_pins_the_imported_image_in_cnp_projects(world, github):
    source_id = _merged(world, github)

    asyncio.run(standard_pr.refresh(source_id))

    files = world[1].commits[-1]["files"]
    image = _load(files["apps/epitweet/frontend/release.yaml"])["image"]
    assert image["repository"] == "ghcr.io/3-istor/imports/epitweet/frontend"
    assert image["tag"].startswith("imported@sha256:")


def test_merge_writes_nothing_to_the_imported_repository(world, github):
    source_id = _merged(world, github)
    before = len(github.commits)

    asyncio.run(standard_pr.refresh(source_id))

    assert len(github.commits) == before


def test_the_cmp_never_commits_to_the_default_branch(world, github):
    source_id = _merged(world, github)

    asyncio.run(standard_pr.refresh(source_id))

    assert all(c["branch"] != "main" for c in github.commits)


def test_merge_stops_the_platform_rebuild(world, github):
    source_id = _merged(world, github)

    asyncio.run(standard_pr.refresh(source_id))

    source = _source(world, source_id)
    assert source.standard_repo["state"] == "switched"
    assert source.auto_rebuild is False


def test_an_app_whose_values_were_dropped_before_merge_stays_on_the_platform(
    world, github
):
    source_id = _merged(world, github)
    github.merged_tree = {
        standard_repo.values_file(a)
        for a in _source(world, source_id).standard_repo["apps"]
        if a != "frontend"
    }

    asyncio.run(standard_pr.refresh(source_id))

    frontend = next(
        a for a in world[1].record["spec"]["apps"] if a["name"] == "frontend"
    )
    assert "repoURL" not in frontend
    assert _source(world, source_id).standard_repo["kept"] == ["frontend"]


def test_a_pull_request_closed_without_merge_changes_nothing(world, github):
    source_id = _deployed(world)
    _open(world, source_id)
    commits = len(world[1].commits)
    github.pull_state = {"state": "closed", "merged": False}

    state = asyncio.run(standard_pr.refresh(source_id))

    assert state == "closed"
    assert len(world[1].commits) == commits


def test_a_closed_pull_request_can_be_opened_again(world, github):
    source_id = _deployed(world)
    _open(world, source_id)
    github.pull_state = {"state": "closed", "merged": False}
    asyncio.run(standard_pr.refresh(source_id))

    state = _open(world, source_id)

    assert state["state"] == "open"


def test_a_build_is_refused_once_the_repository_deploys_itself(world, github):
    source_id = _merged(world, github)
    asyncio.run(standard_pr.refresh(source_id))

    with pytest.raises(ImportRefused, match="deploys from its own deploy/"):
        service.refuse_if_repo_managed(_source(world, source_id))


def test_the_push_merging_the_pull_request_is_not_rebuilt_by_the_platform(
    world, github, monkeypatch
):
    source_id = _merged(world, github)
    monkeypatch.setattr(rebuilds, "SessionLocal", world[0])

    outcome = asyncio.run(rebuilds.auto_rebuild(source_id, "push"))

    assert outcome == "standard_ci"


# ── Registry helpers ────────────────────────────────────────────────────────


def test_switch_leaves_template_apps_and_other_repositories_alone():
    record = {
        "spec": {
            "apps": [
                {"name": "site", "type": "static", "repoURL": "x"},
                {
                    "name": "api",
                    "type": "service",
                    "source": {"repo": "https://github.com/3-Istor/other"},
                },
                {"name": "db", "type": "backing"},
            ]
        }
    }
    before = copy.deepcopy(record)

    switched = generate.switch_to_repo(
        record, "3-Istor/epitweet", {"site", "api", "db"}
    )

    assert switched == {"db"}
    assert record["spec"]["apps"][:2] == before["spec"]["apps"][:2]


def test_removing_an_app_read_from_its_repository_deletes_no_values_file():
    record = {
        "spec": {
            "apps": [
                {"name": "api", "type": "service", "repoURL": REPO_URL},
                {"name": "db", "type": "backing"},
            ]
        }
    }

    files = generate.remove_from_record(record, "shop", {"api", "db"})

    assert files == {generate.values_path("shop", "db"): None}
