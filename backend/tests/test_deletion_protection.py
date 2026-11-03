import asyncio
from io import StringIO
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import BackgroundTasks, HTTPException
from ruamel.yaml import YAML

from app.models.deployment import Deployment
from app.models.project import Project, TargetCloud
from app.routers import deployments as deployments_router
from app.routers import projects as projects_router
from app.schemas.project import DeletionProtectionUpdate
from app.services import (
    deletion_protection,
    project_registry,
    terraform_orchestrator,
)
from app.services.kube_client import (
    ClusterNotConnectedError,
    KubeUnavailableError,
)
from app.services.project_registry import RegistryError
from tests.test_api_auth import _user, db, roles  # noqa: F401

AGENT = {
    "sub": "u1",
    "preferred_username": "u1",
    "cnp_agent": {"grant_id": "g"},
}


def put(db, token, project="alpha", enabled=True):
    return asyncio.run(
        projects_router.update_deletion_protection(
            project, DeletionProtectionUpdate(enabled=enabled), token, db
        )
    )


def registry(**seams):
    return mock.patch.object(
        deletion_protection,
        "is_deletion_protected",
        new=mock.AsyncMock(**seams),
    )


def delete_app(db, project="alpha", background=None):
    background = background or BackgroundTasks()
    deployment = db.query(Deployment).filter_by(project_id=project).one()
    asyncio.run(
        deployments_router.delete_deployment(
            deployment.id, background, _user("u1"), db
        )
    )
    return background


def test_an_agent_cannot_change_protection(db, roles):  # noqa: F811
    with mock.patch.object(
        projects_router, "set_deletion_protection", new=mock.AsyncMock()
    ) as write:
        with pytest.raises(HTTPException) as exc:
            put(db, AGENT)

    assert exc.value.status_code == 403
    assert "Assistants cannot change" in exc.value.detail
    write.assert_not_called()


def test_a_member_cannot_change_protection(db, roles):  # noqa: F811
    with mock.patch.object(
        projects_router, "set_deletion_protection", new=mock.AsyncMock()
    ) as write:
        with pytest.raises(HTTPException) as exc:
            put(db, _user("u2"))

    assert exc.value.status_code == 403
    write.assert_not_called()


def test_an_admin_turns_protection_on(db, roles):  # noqa: F811
    with mock.patch.object(
        projects_router, "set_deletion_protection", new=mock.AsyncMock()
    ) as write:
        result = put(db, _user("u1"))

    assert result == {"enabled": True}
    write.assert_awaited_once_with("alpha", True)


def test_a_platform_admin_may_change_protection(db, roles):  # noqa: F811
    with (
        mock.patch.object(projects_router, "is_cnp_admin", return_value=True),
        mock.patch.object(
            projects_router, "set_deletion_protection", new=mock.AsyncMock()
        ) as write,
    ):
        result = put(db, _user("root"), enabled=False)

    assert result == {"enabled": False}
    write.assert_awaited_once_with("alpha", False)


def test_protection_of_a_project_without_record_is_off(
    db, roles
):  # noqa: F811
    with mock.patch.object(
        project_registry, "read_record", new=mock.AsyncMock(return_value=None)
    ):
        read = asyncio.run(
            projects_router.get_deletion_protection(
                SimpleNamespace(project_name="alpha")
            )
        )
        with pytest.raises(HTTPException) as exc:
            put(db, _user("u1"))

    assert read == {"enabled": False}
    assert exc.value.status_code == 404


def test_deleting_an_app_of_a_protected_project_is_refused(
    db, roles
):  # noqa: F811
    background = BackgroundTasks()

    with registry(return_value=True), pytest.raises(HTTPException) as exc:
        delete_app(db, background=background)

    assert exc.value.status_code == 409
    assert "Deletion protection is on" in exc.value.detail
    assert background.tasks == []


def test_deleting_an_app_is_refused_while_the_label_is_lifted(
    db, roles
):  # noqa: F811
    background = BackgroundTasks()

    with (
        registry(return_value=False),
        mock.patch.object(
            deletion_protection, "still_labelled", return_value=True
        ) as labelled,
        pytest.raises(HTTPException) as exc,
    ):
        delete_app(db, background=background)

    assert exc.value.status_code == 409
    assert "being turned off" in exc.value.detail
    assert background.tasks == []
    labelled.assert_called_once_with("alpha", TargetCloud.ONPREM, "alpha-web")


def test_deleting_an_app_of_an_unprotected_project_proceeds(
    db, roles
):  # noqa: F811
    with (
        registry(return_value=False),
        mock.patch.object(deletion_protection, "kube_list", return_value=[]),
    ):
        background = delete_app(db)

    assert [t.func for t in background.tasks] == [
        terraform_orchestrator.run_deletion
    ]


def test_the_label_check_targets_the_cloud_of_the_project(
    db, roles
):  # noqa: F811
    db.add(
        Project(
            project_name="alpha",
            owner_username="x",
            target_cloud=TargetCloud.AWS,
        )
    )
    db.commit()

    with (
        registry(return_value=False),
        mock.patch.object(
            deletion_protection, "still_labelled", return_value=False
        ) as labelled,
    ):
        delete_app(db)

    labelled.assert_called_once_with("alpha", TargetCloud.AWS, "alpha-web")


def test_deleting_a_protected_project_is_refused(db, roles):  # noqa: F811
    with (
        registry(return_value=True),
        mock.patch.object(
            projects_router, "remove_record", new=mock.AsyncMock()
        ) as remove,
        pytest.raises(HTTPException) as exc,
    ):
        asyncio.run(
            projects_router.delete_project(
                SimpleNamespace(project_name="gamma"),
                BackgroundTasks(),
                db,
            )
        )

    assert exc.value.status_code == 409
    assert "Deletion protection is on" in exc.value.detail
    remove.assert_not_called()


def test_an_unreadable_registry_refuses_deletion(db, roles):  # noqa: F811
    background = BackgroundTasks()

    with (
        registry(side_effect=RegistryError("down")),
        pytest.raises(HTTPException) as exc,
    ):
        delete_app(db, background=background)

    assert exc.value.status_code == 502
    assert background.tasks == []


@pytest.mark.parametrize(
    "error",
    [KubeUnavailableError("down"), ClusterNotConnectedError(TargetCloud.AWS)],
)
def test_an_unreachable_cluster_does_not_block(error):
    with mock.patch.object(
        deletion_protection, "kube_list", side_effect=error
    ):
        assert not deletion_protection.still_labelled("alpha", TargetCloud.AWS)


def test_the_label_check_scopes_to_the_project_and_the_app():
    label = deletion_protection.LABEL
    argocd = "/apis/argoproj.io/v1alpha1/namespaces/argocd"
    labelled = [{"metadata": {"name": "alpha-web"}}]

    def kube_list(path, params=None, cloud=TargetCloud.ONPREM):
        return labelled if path == "/api/v1/namespaces" else []

    with mock.patch.object(
        deletion_protection, "kube_list", side_effect=kube_list
    ) as listed:
        assert deletion_protection.still_labelled("alpha", TargetCloud.GCP)
        assert deletion_protection.still_labelled(
            "alpha", TargetCloud.GCP, "web"
        )
        assert not deletion_protection.still_labelled(
            "alpha", TargetCloud.GCP, "api"
        )

    selector = f"cnp.3istor.com/project=alpha,{label}=true"
    calls = listed.call_args_list
    assert calls[0] == mock.call(
        f"{argocd}/applications", {"labelSelector": selector}
    )
    assert calls[1] == mock.call(
        f"{argocd}/appprojects", {"labelSelector": selector}
    )
    assert calls[2] == mock.call(
        "/api/v1/namespaces",
        {"labelSelector": selector},
        cloud=TargetCloud.GCP,
    )
    # One app: Applications narrowed to it, AppProjects not asked at all.
    assert calls[3] == mock.call(
        f"{argocd}/applications",
        {"labelSelector": f"{selector},cnp.3istor.com/app=web"},
    )
    assert all(c.args[0] != f"{argocd}/appprojects" for c in calls[3:])


RECORD = """\
apiVersion: cnp.3istor.com/v1alpha1
kind: ProjectRecord
metadata:
  name: alpha
spec:
  targetCloud: onprem
{extra}"""


def written(enabled, extra):
    record = YAML().load(RECORD.format(extra=extra))
    with (
        mock.patch.object(
            project_registry,
            "read_record",
            new=mock.AsyncMock(return_value=(record, "sha1")),
        ),
        mock.patch.object(
            project_registry,
            "_installation_token",
            new=mock.AsyncMock(return_value="t"),
        ),
        mock.patch.object(
            project_registry, "put_file_content", new=mock.AsyncMock()
        ) as put_file,
    ):
        asyncio.run(project_registry.set_deletion_protection("alpha", enabled))
    args, kwargs = put_file.call_args
    assert kwargs["sha"] == "sha1"
    spec = YAML(typ="safe").load(StringIO(args[3]))["spec"]
    return spec, kwargs["message"]


def test_turning_protection_off_removes_the_field():
    spec, message = written(False, "  deletionProtection: true\n")

    assert "deletionProtection" not in spec
    assert spec["targetCloud"] == "onprem"
    assert message == (
        "feat(registry): turn deletion protection off for alpha [skip ci]"
    )

    spec, message = written(True, "")

    assert spec["deletionProtection"] is True
    assert message == (
        "feat(registry): turn deletion protection on for alpha [skip ci]"
    )
