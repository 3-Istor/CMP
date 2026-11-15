import pytest

from app.services.registry_names import (
    check_app,
    check_new_project,
    is_platform_namespace,
)


def record(name: str, *apps: dict) -> dict:
    return {"metadata": {"name": name}, "spec": {"apps": list(apps)}}


def app(name: str, type_: str = "static", **hostnames: str) -> dict:
    return {"name": name, "type": type_, "hostnames": hostnames}


NEW_PROJECT_CASES = [
    (
        "project whose system namespace equals another project's app namespace",
        "foo-bar",
        [record("foo", app("bar-system"))],
        ["namespace 'foo-bar-system'", "project 'foo'"],
    ),
    (
        "project named like another's platform AppProject",
        "foo-platform",
        [record("foo")],
        ["appproject 'foo-platform'", "project 'foo'"],
    ),
    (
        "project whose status hostname equals another app hostname",
        "bar",
        [record("foo", app("web", prod="Status-bar.3istor.com"))],
        ["hostname 'status-bar.3istor.com'", "project 'foo'"],
    ),
    (
        "project whose bootstrap Application equals another's app Application",
        "foo-bar",
        [record("foo", app("bar", "fullstack"))],
        [],
    ),
    ("unrelated project", "baz", [record("foo", app("web"))], []),
    (
        "same name as its own existing record is a retry",
        "foo",
        [record("foo")],
        [],
    ),
]


@pytest.mark.parametrize(
    "description,project,existing,fragments",
    NEW_PROJECT_CASES,
    ids=[c[0] for c in NEW_PROJECT_CASES],
)
def test_check_new_project(description, project, existing, fragments):
    errors = check_new_project(project, existing)

    text = "\n".join(errors)
    assert bool(errors) == bool(fragments)
    for fragment in fragments:
        assert fragment in text


APP_CASES = [
    (
        "app namespace equals another project's system namespace",
        "foo",
        app("bar-system"),
        [record("foo"), record("foo-bar")],
        ["namespace 'foo-bar-system'", "project 'foo-bar'"],
    ),
    (
        "app namespace equals another project's app namespace",
        "foo",
        app("bar-baz"),
        [record("foo"), record("foo-bar", app("baz"))],
        ["namespace 'foo-bar-baz'", "project 'foo-bar'"],
    ),
    (
        "fullstack Application names coincide",
        "foo",
        app("bar-app", "fullstack"),
        [record("foo"), record("foo-bar", app("app", "fullstack"))],
        ["application 'foo-bar-app-frontend'"],
    ),
    (
        "app hostname equals another project's derived hostname",
        "foo",
        app("web", prod="auth-bar.3istor.com"),
        [record("foo"), record("bar")],
        ["hostname 'auth-bar.3istor.com'", "project 'bar'"],
    ),
    (
        "app named system",
        "foo",
        app("system"),
        [record("foo")],
        ["app 'system' is reserved"],
    ),
    (
        "namespace over 63 characters",
        "foo",
        app("a" * 60),
        [record("foo")],
        ["68 characters"],
    ),
    ("staging namespace at 63 characters", "p" * 40, app("a" * 18), [], []),
    ("uppercase app name", "foo", app("Web"), [record("foo")], ["DNS-1123"]),
    ("dotted app name", "foo", app("web.v2"), [record("foo")], ["DNS-1123"]),
    (
        "trailing hyphen app name",
        "foo",
        app("web-"),
        [record("foo")],
        ["DNS-1123"],
    ),
    (
        "redeploying an app already in the record is not a collision",
        "foo",
        app("web", prod="web-foo.3istor.com"),
        [record("foo", app("web", prod="web-foo.3istor.com"))],
        [],
    ),
    (
        "unrelated apps",
        "foo",
        app("web", prod="web-foo.3istor.com"),
        [record("foo"), record("bar", app("web", prod="web-bar.3istor.com"))],
        [],
    ),
    (
        "app hostname equals a sibling app's hostname",
        "foo",
        app("api", prod="web-foo.3istor.com"),
        [record("foo", app("web", prod="web-foo.3istor.com"))],
        ["hostname 'web-foo.3istor.com'", "project 'foo'"],
    ),
    (
        "app named after the project's status page",
        "foo",
        app("status"),
        [record("foo")],
        ["app 'status' is reserved", "status hostname"],
    ),
    (
        "app named after the project's auth hostname",
        "foo",
        app("auth"),
        [],
        ["app 'auth' is reserved"],
    ),
    (
        "app named after the project's offhours page",
        "foo",
        app("offhours"),
        [record("foo")],
        ["app 'offhours' is reserved"],
    ),
    (
        "app hostname set to the project's own status hostname",
        "foo",
        app("web", prod="status-foo.3istor.com"),
        [record("foo")],
        ["hostname 'status-foo.3istor.com'", "the status hostname"],
    ),
]


@pytest.mark.parametrize(
    "description,project,new_app,existing,fragments",
    APP_CASES,
    ids=[c[0] for c in APP_CASES],
)
def test_check_app(description, project, new_app, existing, fragments):
    errors = check_app(project, new_app, existing)

    text = "\n".join(errors)
    assert bool(errors) == bool(fragments)
    for fragment in fragments:
        assert fragment in text


def test_staging_namespace_collides_with_another_apps_prod_namespace():
    staged = {**app("web"), "environments": ["prod", "staging"]}

    errors = check_app("foo", staged, [record("foo-web", app("stg"))])

    assert any("namespace 'foo-web-stg'" in error for error in errors)


def test_staging_namespace_counts_against_the_length_limit():
    staged = {**app("a" * 20), "environments": ["prod", "staging"]}

    errors = check_app("p" * 40, staged, [])

    assert any("65 characters" in error for error in errors)


def test_a_prod_only_app_reserves_its_staging_name():
    errors = check_app("foo", app("web"), [record("foo-web", app("stg"))])

    assert any("namespace 'foo-web-stg'" in error for error in errors)


VAULT_ROLE_CASES = [
    (
        "prod role equals a platform role",
        "cert",
        "manager",
        "cert-manager-role",
    ),
    ("prod role equals the CMP's VSO role", "arcl", "cmp", "arcl-cmp-role"),
    (
        "prod role equals a role also on the cloud mounts",
        "vault-secrets",
        "operator",
        "vault-secrets-operator-role",
    ),
    (
        "prod role equals another project's system role",
        "project",
        "shop-system",
        "project-shop-system-role",
    ),
]


@pytest.mark.parametrize(
    "description,project,app_name,role",
    VAULT_ROLE_CASES,
    ids=[c[0] for c in VAULT_ROLE_CASES],
)
def test_check_app_rejects_an_app_whose_vault_role_is_reserved(
    description, project, app_name, role
):
    errors = check_app(project, app(app_name), [record(project)])

    assert any(f"Vault role '{role}'" in error for error in errors)


def test_check_app_accepts_an_app_whose_vault_roles_are_free():
    errors = check_app("shop", app("web"), [record("shop")])

    assert not errors


@pytest.mark.parametrize(
    "project,app_name",
    [
        ("envoy", "gateway-system"),
        ("gateway", "infra"),
        ("cnp", "projects-db"),
        ("cnp-projects", "db"),
        ("argo", "rollouts"),
        ("garage", "s3"),
        ("cnp", "vpn"),
        ("kube", "public"),
        ("rook", "anything"),
    ],
)
def test_check_app_refuses_an_app_whose_namespace_is_a_platform_namespace(
    project, app_name
):
    errors = check_app(project, app(app_name), [record(project)])

    assert any("a namespace of the platform" in error for error in errors)


def test_check_app_accepts_an_app_whose_name_only_resembles_a_platform_one():
    errors = check_app("demo", app("infra"), [record("demo")])

    assert not errors


@pytest.mark.parametrize(
    "project", ["kube", "cnpg", "trivy", "envoy-gateway", "default"]
)
def test_check_new_project_refuses_a_project_reserved_by_the_platform(project):
    errors = check_new_project(project, [])

    assert errors != []


@pytest.mark.parametrize(
    "project", ["iot", "self-runner", "test-link", "joetest"]
)
def test_check_new_project_accepts_the_projects_that_exist_today(project):
    errors = check_new_project(project, [])

    assert not errors


@pytest.mark.parametrize(
    "namespace,expected",
    [
        ("envoy-gateway-system", True),
        ("kube-system", True),
        ("cnpg-anything", True),
        ("vault", True),
        ("vaulty-web", False),
        ("iot-landing", False),
    ],
)
def test_is_platform_namespace_matches_the_reserved_names_and_families(
    namespace, expected
):
    assert is_platform_namespace(namespace) is expected


def test_check_app_name_refuses_a_namespace_with_a_trailing_newline():
    from app.services.registry_names import check_app_name

    errors = check_app_name("foo", "web\n")

    assert any("not a valid DNS-1123 label" in error for error in errors)
