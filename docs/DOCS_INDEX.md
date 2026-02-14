# Documentation Index

This file maps documentation entry points for the repository in its current 2-level model.

## Entry Points

- Root entry: [../README.md](../README.md)
  - Problem statement, scope, non-goals, reproducibility principles, and quick start.
- Deep-dive entry: [README.md](README.md)
  - Technical context and links to architecture, environment, diagrams, and archives.

## Core Documents

- [ADDON_ARCHITECTURE.md](ADDON_ARCHITECTURE.md)
  - Deterministic pipeline architecture, contracts, and invariants (binding reference).
- [ARCHITECTURE.md](ARCHITECTURE.md)
  - Plain-language operational pipeline: Terrain → CityGML → OSM → Linking → Materialize → Legend → Inspector.
- [ENVIRONMENT.md](ENVIRONMENT.md)
  - Blender Python version constraints (3.11.x), GDAL/raster dependency notes, and installation caveats.
- [DIAGRAMS.md](DIAGRAMS.md)
  - Mermaid pipeline source, SVG exports, graphical abstract, and inspector proof asset contract.

## Supporting Documents

- [audit/](audit/)
  - Technical validation and risk notes.
- [_archive/](_archive/)
  - Historical snapshots and older phase documents.
- [legacy/](legacy/)
  - Archived legacy code and provenance notes kept outside active import paths.

## Documentation Rule

- Keep conceptual/project framing in [../README.md](../README.md).
- Keep implementation detail in [README.md](README.md) and linked deep-dive files.
- Keep archived/historical material outside active runtime paths.
