import re

from app.services.observability import slo

APP = slo.AppSlo(
    project="shop", app="web", app_id=12, namespace="shop-web", period_days=7
)

SELECTOR = re.compile(r"\{([^{}]*)\}")


def _rules():
    return [r for g in slo.groups(APP) for r in g["rules"]]


def test_every_raw_selector_names_the_apps_namespace_or_route():
    selectors = [
        s
        for rule in _rules()
        for s in SELECTOR.findall(rule["expr"])
        if s and "cnp_project" not in s
    ]

    assert selectors and all(
        'envoy_cluster_name=~"httproute/shop-web/.+"' in s
        or 'exported_namespace="shop-web"' in s
        for s in selectors
    )


def test_every_recorded_series_reads_back_only_this_apps_series():
    selectors = [
        s
        for rule in _rules()
        for s in SELECTOR.findall(rule["expr"])
        if "cnp_project" in s
    ]

    assert all(
        'cnp_project="shop"' in s
        and 'cnp_app="web"' in s
        and 'namespace="shop-web"' in s
        for s in selectors
    )


def test_each_group_pins_extra_filters_to_the_app():
    params = [g["params"] for g in slo.groups(APP)]

    assert (
        params
        == [
            {
                "extra_filters": [
                    '{namespace=~"shop-web|shop-system"}',
                    '{namespace="envoy-gateway-system", '
                    'envoy_cluster_name=~"httproute/shop-web/.+"}',
                ]
            }
        ]
        * 2
    )


def test_a_name_prefix_does_not_match_another_apps_route():
    shop = slo.AppSlo(
        project="shop", app="w", app_id=1, namespace="shop-w", period_days=7
    )

    filters = slo.extra_filters(shop)

    assert "httproute/shop-w/.+" in filters[1]


def test_alerts_carry_the_routing_labels_and_the_links():
    alerts = [r for r in _rules() if "alert" in r]

    assert (
        all(
            {"cnp_project", "cnp_app", "severity"} <= set(a["labels"])
            and {"summary", "runbook_url", "portal_url", "silence_url"}
            <= set(a["annotations"])
            for a in alerts
        )
        and len(alerts) == 6
    )


def test_the_three_tiers_use_the_workbook_factors_on_the_budget():
    alerts = [
        r for r in _rules() if r.get("alert") == "SloAvailabilityBurnRate"
    ]

    thresholds = [
        re.search(r"ratio_rate1h\{[^}]*\} > ([0-9.]+)", a["expr"])
        or re.search(r"ratio_rate6h\{[^}]*\} > ([0-9.]+)", a["expr"])
        or re.search(r"ratio_rate3d\{[^}]*\} > ([0-9.]+)", a["expr"])
        for a in alerts
    ]

    assert [t.group(1) for t in thresholds] == ["0.072", "0.03", "0.005"]


def test_the_owner_target_changes_the_budget():
    strict = slo.AppSlo(
        project="shop",
        app="web",
        app_id=12,
        namespace="shop-web",
        period_days=28,
        availability=99.9,
    )

    assert strict.budget("availability") == 0.001


def test_demo_projects_get_a_seven_day_window():
    assert [slo.period_days(level) for level in slo.DEMO_LEVELS] + [
        slo.period_days("standard"),
        slo.period_days("critical"),
    ] == [7, 7, 28, 28]


def test_the_period_window_follows_the_period():
    records = {r.get("record") for r in _rules()}

    assert "slo:sli_error:ratio_rate7d" in records


def test_the_object_is_owned_by_the_cmp_in_its_namespace():
    meta = slo.render(APP)["metadata"]

    assert (meta["namespace"], meta["labels"]["cnp.3istor.com/owner"]) == (
        "cnp-observability",
        "cmp",
    )


def test_ui_strings_have_no_em_dash():
    assert "—" not in str(slo.render(APP))
