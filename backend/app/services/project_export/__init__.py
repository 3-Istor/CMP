"""
Project export (EU Data Act Art. 25 and 30, "réversibilité").

An export is one ``.tar.gz`` with ``manifest.json`` at its root and the data
in open formats next to it. ``inventory.py`` is the exhaustive list of what
the platform holds for a project and whether it is exported; ``bundle.py``
writes the archive; ``collectors.py`` fills it from ``sources.py``, the
adapters to the live platform; ``runner.py`` runs one export end to end.
"""
