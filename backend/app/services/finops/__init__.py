"""
FinOps package — simulated cost tracking, recommendations and budget alerting.

The whole module is built on a **deterministic, time-based cost simulation**
(there is no Prometheus / metrics-server in this platform yet). Everything a
graph or a recommendation needs is derived from:

  * the *requested* quotas of each application (replicas, CPU, RAM, storage), and
  * a deterministic pseudo-usage function of ``(app, resource, day)``.

To retune the money numbers, edit **``pricing.py``** — it is the single source
of truth for the time↔cost ratio. To plug real metrics later, implement a new
:class:`~app.services.finops.provider.CostProvider` and swap the default one.
"""

from app.services.finops.provider import get_cost_provider

__all__ = ["get_cost_provider"]
