from datetime import datetime, timedelta, timezone

import pytest

from app.services.security import sources
from app.services.security.model import Audience, Category, Source, Tier

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
API = sources.AppRef("api", "shop-api", "3-Istor/shop-api")


def _pod_report(namespace, pod, *results):
    return {
        "metadata": {"namespace": namespace},
        "scope": {"kind": "Pod", "name": pod, "namespace": namespace},
        "results": [
            {"policy": "p", "result": result, "message": message}
            for message, result in results
        ],
    }


def _vuln_report(namespace, repository, *vulns):
    return {
        "metadata": {
            "namespace": namespace,
            "creationTimestamp": "2026-10-09T10:00:00Z",
            "labels": {"trivy-operator.resource.name": "api"},
        },
        "report": {
            "registry": {"server": "ghcr.io"},
            "artifact": {"repository": repository, "tag": "1.0"},
            "vulnerabilities": list(vulns),
        },
    }


def _vuln(severity="CRITICAL", fixed="3.0.2", cls="lang-pkgs"):
    return {
        "vulnerabilityID": "CVE-2026-1",
        "severity": severity,
        "resource": "openssl",
        "installedVersion": "3.0.1",
        "fixedVersion": fixed,
        "class": cls,
    }


def test_kyverno_root_failure_is_for_the_developer():
    # Arrange
    reports = [
        _pod_report(
            "shop-api",
            "api-1",
            ("Containers must set runAsNonRoot to true.", "fail"),
        )
    ]

    # Act
    drafts = sources.kyverno_drafts("shop", [API], reports)

    # Assert
    assert [(d.audience, d.rule) for d in drafts] == [
        (Audience.DEVELOPER, "run-as-root")
    ]


def test_kyverno_chart_settings_are_for_the_platform():
    # Arrange
    reports = [
        _pod_report(
            "shop-api",
            "api-1",
            ("Containers must drop ALL capabilities.", "fail"),
        )
    ]

    # Act
    drafts = sources.kyverno_drafts("shop", [API], reports)

    # Assert
    assert drafts[0].audience is Audience.PLATFORM


def test_kyverno_counts_one_finding_per_rule_across_pods_and_ignores_controllers():
    # Arrange
    message = ("The :latest tag is not allowed.", "fail")
    reports = [
        _pod_report("shop-api", "api-1", message),
        _pod_report("shop-api", "api-2", message),
        {
            "metadata": {"namespace": "shop-api"},
            "scope": {"kind": "Deployment", "name": "api"},
            "results": [{"result": "fail", "message": message[0]}],
        },
    ]

    # Act
    drafts = sources.kyverno_drafts("shop", [API], reports)

    # Assert
    assert [d.location for d in drafts] == ["api-1, api-2"]


def test_kyverno_system_namespace_findings_belong_to_the_project():
    # Arrange
    reports = [
        _pod_report(
            "shop-system",
            "gatus-1",
            ("Containers must set runAsNonRoot", "fail"),
        )
    ]

    # Act
    drafts = sources.kyverno_drafts("shop", [API], reports)

    # Assert
    assert (drafts[0].app, drafts[0].audience) == (None, Audience.PLATFORM)


@pytest.mark.parametrize(
    "severity, fixed, running, expected",
    [
        ("CRITICAL", "1.1", True, Tier.CORE),
        ("CRITICAL", "1.1", False, Tier.IMPORTANT),
        ("HIGH", "1.1", True, Tier.IMPORTANT),
        ("CRITICAL", None, True, Tier.INFO),
        ("MEDIUM", "1.1", True, Tier.INFO),
    ],
)
def test_vulnerability_tier_counts_only_fixable_major_vulnerabilities(
    severity, fixed, running, expected
):
    assert sources.vulnerability_tier(severity, fixed, running) is expected


def test_trivy_operator_running_critical_in_app_image_is_core_for_the_developer():
    # Arrange
    reports = [_vuln_report("shop-api", "3-istor/shop-api/backend", _vuln())]

    # Act
    draft = sources.trivy_operator_drafts("shop", [API], reports, [])[0]

    # Assert
    assert (draft.tier, draft.audience) == (Tier.CORE, Audience.DEVELOPER)


def test_trivy_operator_third_party_image_is_for_the_platform():
    # Arrange
    reports = [_vuln_report("shop-api", "cloudnative-pg/postgresql", _vuln())]

    # Act
    draft = sources.trivy_operator_drafts("shop", [API], reports, [])[0]

    # Assert
    assert draft.audience is Audience.PLATFORM


def test_os_package_fix_points_to_the_base_image():
    # Arrange
    reports = [
        _vuln_report("shop-api", "3-istor/shop-api", _vuln(cls="os-pkgs"))
    ]

    # Act
    draft = sources.trivy_operator_drafts("shop", [API], reports, [])[0]

    # Assert
    assert "image de base" in draft.fix


def test_same_cve_from_ci_and_operator_shares_a_fingerprint():
    # Arrange
    operator = _vuln_report("shop-api", "3-istor/shop-api", _vuln())
    ci_report = {
        "scanners": {
            "trivy_image": {
                "vulnerabilities": [
                    {
                        "id": "CVE-2026-1",
                        "severity": "CRITICAL",
                        "package": "openssl",
                        "installed": "3.0.1",
                        "fixed": "3.0.2",
                        "image": "app",
                        "target": "cnp-scan/app",
                    }
                ]
            }
        }
    }

    # Act
    from_operator = sources.trivy_operator_drafts(
        "shop", [API], [operator], []
    )
    from_ci = sources.ci_drafts("shop", API, ci_report)

    # Assert
    assert from_operator[0].fingerprint == from_ci[0].fingerprint


def test_committed_secret_is_core_with_its_file_and_line():
    # Arrange
    report = {
        "run_url": "https://run",
        "scanners": {
            "gitleaks": {
                "leaks": [
                    {
                        "rule_id": "aws-key",
                        "file": "settings.py",
                        "line": 12,
                        "commit": "abcdef123",
                        "fingerprint": "abcdef123:settings.py:aws-key:12",
                    }
                ]
            }
        },
    }

    # Act
    draft = sources.ci_drafts("shop", API, report)[0]

    # Assert
    assert (draft.tier, draft.category, draft.location) == (
        Tier.CORE,
        Category.LEAKS,
        "settings.py ligne 12",
    )


def test_schema_1_ci_report_yields_no_findings():
    assert (
        sources.ci_drafts(
            "shop", API, {"scanners": {"gitleaks": {"status": "fail"}}}
        )
        == []
    )


def _cluster(backup=True, archiving=None, age=timedelta(days=3)):
    cluster = {
        "metadata": {
            "namespace": "shop-api",
            "name": "db",
            "creationTimestamp": (NOW - age).isoformat(),
        },
        "spec": {
            "plugins": ([{"name": sources.BARMAN_PLUGIN}] if backup else [])
        },
        "status": {"conditions": []},
    }
    if archiving is not None:
        cluster["status"]["conditions"].append(
            {
                "type": "ContinuousArchiving",
                "status": archiving,
                "message": "bucket unreachable",
            }
        )
    return cluster


def _backup(stopped):
    return {
        "metadata": {"namespace": "shop-api"},
        "spec": {"cluster": {"name": "db"}},
        "status": {"phase": "completed", "stoppedAt": stopped.isoformat()},
    }


@pytest.mark.parametrize(
    "cluster, backups, expected",
    [
        (_cluster(backup=False), [], [("backup-disabled", Tier.IMPORTANT)]),
        (_cluster(archiving="False"), [], [("backup-failing", Tier.CORE)]),
        (_cluster(), [], [("backup-missing", Tier.CORE)]),
        (_cluster(age=timedelta(hours=2)), [], []),
        (
            _cluster(),
            [_backup(NOW - timedelta(hours=30))],
            [("backup-stale", Tier.CORE)],
        ),
        (_cluster(), [_backup(NOW - timedelta(hours=3))], []),
    ],
    ids=["disabled", "failing", "never", "just-enabled", "stale", "fresh"],
)
def test_backup_findings_follow_the_state_of_each_database(
    cluster, backups, expected
):
    # Act
    drafts = sources.backup_drafts("shop", [API], [cluster], backups, NOW)

    # Assert
    assert [(d.rule, d.tier) for d in drafts] == expected


def test_public_app_is_a_recommendation_and_members_only_is_fine():
    # Act
    public = sources.exposure_drafts("shop", API, "public")
    members = sources.exposure_drafts("shop", API, "project_members")

    # Assert
    assert ([d.tier for d in public], members) == ([Tier.RECOMMENDED], [])


def test_missing_isolation_policy_is_core_for_the_platform():
    # Act
    drafts = sources.isolation_drafts("shop", None)

    # Assert
    assert [(d.tier, d.audience, d.source) for d in drafts] == [
        (Tier.CORE, Audience.PLATFORM, Source.CILIUM)
    ]
