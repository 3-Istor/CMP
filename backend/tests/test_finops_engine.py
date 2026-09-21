"""
Unit tests for the FinOps cost engine and recommendation logic.

Pure — no DB, no network, no config. Run from the backend directory with:

    python -m unittest tests.test_finops_engine
"""

import json
import unittest
from datetime import datetime, timedelta

from app.services.finops import engine, pricing
from app.services.finops.provider import SimulatedCostProvider, period_to_range
from app.services.finops.recommendations import recommendations_for_apps


class _FakeDeployment:
    def __init__(self, id, name, cfg=None, days_old=120, project_id="sandbox"):
        self.id = id
        self.name = name
        self.project_id = project_id
        self.app_config = json.dumps(cfg) if cfg else None
        self.created_at = datetime.utcnow() - timedelta(days=days_old)


def _sample_apps():
    deps = [
        _FakeDeployment(1, "api", {
            "replica_count": 5,
            "resources": {"limits": {"cpu": "4", "memory": "8Gi"}},
        }, days_old=120),
        _FakeDeployment(2, "web", None, days_old=40),
        _FakeDeployment(3, "worker", {"replica_count": 3}, days_old=200),
    ]
    return SimulatedCostProvider().specs(deps)


class QuotaParsingTests(unittest.TestCase):
    def test_explicit_config_wins(self):
        q = engine.quota_from_deployment(_FakeDeployment(1, "api", {
            "replica_count": 5,
            "resources": {"limits": {"cpu": "500m", "memory": "2Gi"}},
        }))
        self.assertEqual(q.replicas, 5)
        self.assertAlmostEqual(q.cpu_cores, 0.5)
        self.assertAlmostEqual(q.ram_gb, 2.0)

    def test_defaults_are_stable_and_sane(self):
        d = _FakeDeployment(42, "x", None)
        q1 = engine.quota_from_deployment(d)
        q2 = engine.quota_from_deployment(d)
        self.assertEqual(q1, q2)
        self.assertGreaterEqual(q1.replicas, 1)
        self.assertGreater(q1.cpu_cores, 0)


class EngineDeterminismTests(unittest.TestCase):
    def test_summary_is_deterministic(self):
        provider = SimulatedCostProvider()
        apps = _sample_apps()
        self.assertEqual(provider.summary(apps), provider.summary(apps))

    def test_timeline_has_expected_shape(self):
        provider = SimulatedCostProvider()
        apps = _sample_apps()
        start, end = period_to_range("30d")
        daily = provider.timeline(apps, start, end, "daily")
        self.assertTrue(daily)
        for p in daily:
            self.assertAlmostEqual(
                p["total"],
                sum(p[r] for r in pricing.RESOURCES),
                places=3,
            )
        weekly = provider.timeline(apps, start, end, "weekly")
        self.assertLessEqual(len(weekly), len(daily))

    def test_cost_scales_with_pricing(self):
        apps = _sample_apps()
        base = engine.month_to_date_cost(apps)
        original = pricing.COST_RATE["cpu"]
        try:
            pricing.COST_RATE["cpu"] = original * 2
            higher = engine.month_to_date_cost(apps)
        finally:
            pricing.COST_RATE["cpu"] = original
        self.assertGreater(higher, base)


class RecommendationTests(unittest.TestCase):
    def test_recommendations_are_non_destructive(self):
        apps = _sample_apps()
        recs = recommendations_for_apps(apps)
        self.assertTrue(recs)  # over-provisioned sample should yield some
        for r in recs:
            self.assertGreaterEqual(r.monthly_saving_eur, 0)
            self.assertTrue(60 <= r.confidence <= 98)
            if r.rec_type == "replicas":
                self.assertLessEqual(
                    r.recommended["replicas"], r.current["replicas"]
                )

    def test_recommendation_ids_are_stable(self):
        apps = _sample_apps()
        ids1 = {r.id for r in recommendations_for_apps(apps)}
        ids2 = {r.id for r in recommendations_for_apps(apps)}
        self.assertEqual(ids1, ids2)


if __name__ == "__main__":
    unittest.main()
