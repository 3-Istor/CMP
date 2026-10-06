import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from app.services import project_security as ps
from app.services.kube_client import KubeUnavailableError

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def _report(namespace, kind, name, *results):
    return {
        "metadata": {"namespace": namespace},
        "scope": {"kind": kind, "name": name, "namespace": namespace},
        "results": [
            {
                "policy": policy,
                "result": result,
                "message": f"{policy} {result}",
            }
            for policy, result in results
        ],
    }


def _cluster(namespace, name, backup=True, archiving=None):
    cluster = {
        "metadata": {"namespace": namespace, "name": name},
        "spec": {"plugins": [{"name": ps.BARMAN_PLUGIN}] if backup else []},
        "status": {"conditions": []},
    }
    if archiving is not None:
        cluster["status"]["conditions"].append(
            {
                "type": "ContinuousArchiving",
                "status": archiving,
                "message": "boom",
            }
        )
    return cluster


def _backup(namespace, cluster, stopped_at, phase="completed"):
    return {
        "metadata": {"namespace": namespace},
        "spec": {"cluster": {"name": cluster}},
        "status": {"phase": phase, "stoppedAt": stopped_at.isoformat()},
    }


class TestKyvernoFindings:
    def test_counts_pods_only_not_their_controllers(self):
        reports = [
            _report(
                "p-web", "Pod", "web-1", ("pod-security-restricted", "fail")
            ),
            _report(
                "p-web",
                "Deployment",
                "web",
                ("pod-security-restricted", "fail"),
            ),
        ]

        findings = ps.kyverno_findings(reports, "pod-security-restricted")

        assert [f.subject for f in findings] == ["p-web/web-1"]

    def test_a_failure_wins_over_a_pass_for_the_same_pod(self):
        reports = [
            _report("p-web", "Pod", "web-1", ("image-tags", "pass")),
            _report("p-web", "Pod", "web-1", ("image-tags", "fail")),
        ]

        findings = ps.kyverno_findings(reports, "image-tags")

        assert findings[0].status is ps.Status.FAIL

    def test_ignores_other_policies(self):
        reports = [_report("p-web", "Pod", "web-1", ("other", "fail"))]

        assert ps.kyverno_findings(reports, "image-tags") == []


class TestBackupFindings:
    @pytest.mark.parametrize(
        "cluster,backups,expected",
        [
            pytest.param(
                _cluster("p-db", "db", backup=False),
                [],
                ps.Status.WARN,
                id="disabled",
            ),
            pytest.param(
                _cluster("p-db", "db"),
                [],
                ps.Status.FAIL,
                id="never-succeeded",
            ),
            pytest.param(
                _cluster("p-db", "db"),
                [_backup("p-db", "db", NOW - timedelta(hours=3))],
                ps.Status.OK,
                id="fresh",
            ),
            pytest.param(
                _cluster("p-db", "db"),
                [_backup("p-db", "db", NOW - timedelta(days=3))],
                ps.Status.FAIL,
                id="stale",
            ),
            pytest.param(
                _cluster("p-db", "db", archiving="False"),
                [_backup("p-db", "db", NOW - timedelta(hours=3))],
                ps.Status.FAIL,
                id="wal-archiving-broken",
            ),
            pytest.param(
                _cluster("p-db", "db"),
                [
                    _backup(
                        "p-db", "db", NOW - timedelta(hours=3), phase="failed"
                    )
                ],
                ps.Status.FAIL,
                id="only-failed-backups",
            ),
            pytest.param(
                _cluster("p-db", "db"),
                [_backup("other", "db", NOW - timedelta(hours=3))],
                ps.Status.FAIL,
                id="backup-of-another-namespace",
            ),
        ],
    )
    def test_status(self, cluster, backups, expected):
        findings = ps.backup_findings([cluster], backups, NOW)

        assert findings[0].status is expected


class TestAppFindings:
    @pytest.mark.parametrize(
        "exposure,expected",
        [
            (None, ps.Status.OK),
            ("public", ps.Status.WARN),
            ("custom", ps.Status.WARN),
            ("project_users", ps.Status.OK),
            ("project_members", ps.Status.OK),
        ],
    )
    def test_exposure(self, exposure, expected):
        assert ps.exposure_finding("web", exposure).status is expected

    @pytest.mark.parametrize(
        "report,expected",
        [
            (None, ps.Status.UNKNOWN),
            (
                {
                    "commit": "abc",
                    "scanners": {"gitleaks": {"status": "pass"}},
                },
                ps.Status.OK,
            ),
            (
                {
                    "commit": "abc",
                    "scanners": {
                        "gitleaks": {"status": "fail"},
                        "trivy_fs": {"status": "pass"},
                    },
                },
                ps.Status.FAIL,
            ),
            (
                {
                    "commit": "abc",
                    "scanners": {"trivy_image": {"status": "skipped"}},
                },
                ps.Status.OK,
            ),
        ],
    )
    def test_ci_scans(self, report, expected):
        assert ps.ci_scan_finding("web", report).status is expected


class TestScore:
    def test_ignores_unmeasured_controls(self):
        controls = [
            ps.Control(
                id="a",
                title="",
                description="",
                status=ps.Status.OK,
                summary="",
            ),
            ps.Control(
                id="b",
                title="",
                description="",
                status=ps.Status.FAIL,
                summary="",
            ),
            ps.Control(
                id="c",
                title="",
                description="",
                status=ps.Status.UNKNOWN,
                summary="",
            ),
        ]

        assert ps.score(controls) == 50

    def test_is_none_when_nothing_is_measured(self):
        assert ps.score([]) is None


def _fake_kube(namespaces, reports_by_ns, network_policy=True):
    def kube_list(path, params=None):
        if path == "/api/v1/namespaces":
            return [{"metadata": {"name": ns}} for ns in namespaces]
        for ns, reports in reports_by_ns.items():
            if (
                path
                == f"/apis/wgpolicyk8s.io/v1alpha2/namespaces/{ns}/policyreports"
            ):
                return reports
        return []

    def kube_get(path, params=None):
        return {"metadata": {}} if network_policy else None

    return kube_list, kube_get


def _build(kube_list, kube_get, deployments=()):
    async def exposure(dep):
        return dep.exposure, True

    async def ci(dep):
        return dep.ci

    with (
        mock.patch.object(ps, "kube_list", kube_list),
        mock.patch.object(ps, "kube_get", kube_get),
    ):
        return asyncio.run(
            ps.build_report("p", list(deployments), exposure, ci)
        )


class TestBuildReport:
    def test_platform_pods_are_reported_apart_and_not_scored(self):
        kube_list, kube_get = _fake_kube(
            ["p-system", "p-web"],
            {
                "p-system": [
                    _report(
                        "p-system",
                        "Pod",
                        "gatus",
                        ("pod-security-restricted", "fail"),
                    )
                ],
                "p-web": [
                    _report(
                        "p-web",
                        "Pod",
                        "web-1",
                        ("pod-security-restricted", "pass"),
                    )
                ],
            },
        )

        report = _build(kube_list, kube_get)

        project = {c.id: c for c in report.controls}
        platform = {c.id: c for c in report.platform_controls}
        assert (
            project["pod_security"].status,
            platform["pod_security"].status,
        ) == (
            ps.Status.OK,
            ps.Status.FAIL,
        )

    def test_missing_network_policy_fails(self):
        kube_list, kube_get = _fake_kube(
            ["p-system"], {}, network_policy=False
        )

        report = _build(kube_list, kube_get)

        assert {c.id: c for c in report.controls}[
            "network_isolation"
        ].status is ps.Status.FAIL

    def test_cluster_outage_marks_cluster_controls_unmeasured(self):
        def down(*_args, **_kwargs):
            raise KubeUnavailableError("down")

        report = _build(down, down)

        statuses = {c.id: c.status for c in report.controls}
        assert {
            statuses[i]
            for i in ("pod_security", "network_isolation", "backups")
        } == {ps.Status.UNKNOWN}

    def test_an_unreadable_app_does_not_break_the_report(self):
        kube_list, kube_get = _fake_kube(["p-system"], {})

        async def broken(_dep):
            raise RuntimeError("GitHub down")

        with (
            mock.patch.object(ps, "kube_list", kube_list),
            mock.patch.object(ps, "kube_get", kube_get),
        ):
            report = asyncio.run(
                ps.build_report(
                    "p", [SimpleNamespace(name="web")], broken, broken
                )
            )

        assert {c.id: c for c in report.controls}["exposure"].findings[
            0
        ].status is ps.Status.UNKNOWN
