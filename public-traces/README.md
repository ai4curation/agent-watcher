# Public Agent Traces

This directory is a compact publishable copy of agent traces mined from GitHub-visible execution surfaces.

- Source: `build/full-agent-history`
- Trace files: 3046
- Unique stored trace files: 2699
- Context files: 7421
- Trace payload bytes: 218728624
- Unique stored trace payload bytes: 124378689
- Stored trace payload bytes after deduplication and compression: 80961640
- Stored context bytes: 13718631
- Gzipped files: 3

`traces/` is organized by GitHub repository path (`owner/repo`), then by retrieval surface (`actions`, `copilot`, `dragon-prs`).
`repos/` contains one manifest per GitHub repository path for easier browsing.
`manifest.json` and `MANIFEST.tsv` list every retrieved file with repository, source size, stored size, source SHA-256 digest, stored SHA-256 digest, source path, and stored path.
When the same trace payload was retrieved through both Actions and Dragon-PR mining, it is stored once and the duplicate manifest entry points to `duplicate_of`.
Trace payloads at or above the packaging threshold are stored as `.gz`.

Bulky workflow-run metadata, unzipped raw Action log directories, and transient working files are intentionally excluded.
Before publishing, run a final secret scan over this directory even when traces came from public GitHub surfaces.
