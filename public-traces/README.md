# Public Agent Traces

This directory is a compact publishable copy of agent traces mined from GitHub-visible execution surfaces.

`traces/` is organized by GitHub repository path (`owner/repo`), then by retrieval surface (`actions`, `copilot`, `dragon-prs`).
Trace payloads at or above the packaging threshold are stored as `.gz`.

Generated catalogs such as `manifest.json`, `MANIFEST.tsv`, and per-repository manifests are intentionally not committed here. They are rebuilt in CI under `build/trace-refresh/catalog/` for validation and workflow summaries.

Bulky workflow-run metadata, unzipped raw Action log directories, and transient working files are intentionally excluded.
The refresh workflow validates the generated catalog and runs a token-shaped secret scan before publishing trace updates.
