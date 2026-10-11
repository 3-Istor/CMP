import pytest

from app.services.registry_names import check_app, check_new_project


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
        ["64 characters"],
    ),
    ("namespace at 63 characters", "p" * 40, app("a" * 22), [], []),
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
