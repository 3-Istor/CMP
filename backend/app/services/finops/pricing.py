"""
FinOps pricing — the single knob for the whole simulation.

Edit the constants below to change how much money the FinOps module reports.
Everything else (timeline graph, breakdown donut, budgets, recommendations,
potential savings) is derived proportionally from these values, so tuning the
demo is a one-file change.

Model: a resource's hourly cost is ``rate * allocated_amount``. The *allocated*
amount comes from the application's requested quota (replicas × per-replica
CPU/RAM, total storage, baseline network). The realised cost also varies with
time through a simulated usage multiplier (see ``usage.py``) so the timeline is
never a flat line.
"""

from __future__ import annotations

# ── Ratio temps ↔ coût : éditer ICI pour ajuster toute la simulation ──────────
# Prix horaires simulés, indexés (grossièrement) sur une infra réelle, en EUR.
COST_RATE: dict[str, float] = {
    "cpu": 0.0300,      # EUR par vCPU-heure (allouée)
    "ram": 0.0040,      # EUR par Go de RAM-heure (allouée)
    "storage": 0.00020,  # EUR par Go de stockage-heure
    "network": 0.0100,   # EUR par Go de trafic réseau-heure (baseline)
}

# Multiplicateur global temps → coût. 1.0 = temps réel. Augmenter pour une démo
# (les coûts s'accumulent plus vite), diminuer pour ralentir.
SIMULATION_SPEED: float = 1.0

# Part fixe de réservation d'une ressource (on paie une base même à usage nul),
# le reste variant avec l'usage simulé. 0.5 → coût entre 50 % et 100 % de
# l'allocation selon la charge. Garde le graphe temporel vivant.
RESERVATION_FLOOR: float = 0.5

# Ordre canonique + couleurs (alignées sur la charte UI du frontend).
RESOURCES: tuple[str, ...] = ("cpu", "ram", "storage", "network")
RESOURCE_COLORS: dict[str, str] = {
    "cpu": "#3B82F6",      # bleu
    "ram": "#10B981",      # vert
    "storage": "#F59E0B",  # ambre
    "network": "#8B5CF6",  # violet
}

CURRENCY = "EUR"

# Devise → symbole (affichage backend éventuel).
CURRENCY_SYMBOL = "€"


def resource_hours_cost(resource: str, allocated: float, hours: float,
                        usage_multiplier: float = 1.0) -> float:
    """
    Cost in EUR of holding ``allocated`` units of ``resource`` for ``hours``.

    ``usage_multiplier`` (0..1, from the usage simulation) scales the variable
    part of the bill on top of the reservation floor.
    """
    rate = COST_RATE.get(resource, 0.0)
    load = RESERVATION_FLOOR + (1.0 - RESERVATION_FLOOR) * usage_multiplier
    return rate * allocated * hours * load * SIMULATION_SPEED
