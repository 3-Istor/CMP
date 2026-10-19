"""
Staging environments and promotion to prod.

An app opts into staging: release candidates (vX.Y.Z-rc.N) then land there,
and prod changes only through a promotion that copies the image digests that
really ran in staging. See cnp-docs, "Environnements staging et promotion".
"""
