# Data directory

This directory is populated by the pipeline and is **not** tracked in git.

- `raw/` — cached source downloads, never modified after they land:
  the Overture Maps street, place and address extracts, the twelve USGS
  3DEP 1 m lidar tiles that cover the City of Atlanta, the 1/3 arc-second
  cross-check tile, the city limits, the neighborhood boundaries and the
  regional bike facility inventory.
  Populate with `uv run python -m sf_flat_routes download` (~3.9 GB).
- `processed/` — cached intermediate products: the clipped DEM mosaic
  (2.6 GB), the edge table, per-edge elevation profiles, the directed edge
  metrics, the neighborhood pair matrix and the route store.
  Populate with `uv run python -m sf_flat_routes build-network` and
  `uv run python -m sf_flat_routes analyze`.
- `sensitivity/` — one rebuilt pipeline per perturbed configuration, written
  by `uv run python -m sf_flat_routes sensitivity`.

Nothing here needs to be kept: every file is rebuilt deterministically from
the sources listed by `uv run python -m sf_flat_routes sources`.
