"""
What the CMP writes for the observability stack of CNP-185 (cnp-docs,
10-observabilite): per-app SLO rules, per-project alert routing, silences.

The CMP is the only writer of the namespace the VictoriaMetrics operator
reads project objects from; tenants cannot create these objects (D9).
"""
