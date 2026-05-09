#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
from collections import Counter
from pathlib import Path
from typing import Any


TRACE_FILES = {"agent.log", "log-trace.jsonl", "claude-execution-output.json"}
CONTEXT_FILES = {"index.json", "summary.json", "pr.json", "run.json", "session.json"}
EXCLUDED_PARTS = {"metadata", "logs", "artifact.zip"}
PROJECT_SLUGS = (
    "ai-gene-review",
    "cell-ontology",
    "dismech",
    "efo",
    "go-ontology",
    "mondo",
    "uberon",
)
DEFAULT_REPOS_BY_PROJECT = {
    "ai-gene-review": "ai4curation/ai-gene-review",
    "cell-ontology": "obophenotype/cell-ontology",
    "dismech": "monarch-initiative/dismech",
    "efo": "EBISPOT/efo",
    "go-ontology": "geneontology/go-ontology",
    "mondo": "monarch-initiative/mondo",
    "uberon": "obophenotype/uberon",
}


def main() -> int:
    args = parse_args()
    source = Path(args.source)
    dest = Path(args.dest)
    gzip_threshold = args.gzip_threshold_bytes

    if not source.exists():
        raise SystemExit(f"source does not exist: {source}")

    if dest.exists() and args.clean:
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    repo_lookup = load_repo_lookup(source, Path(args.config) if args.config else None)
    entries: list[dict[str, Any]] = []
    canonical_trace_paths: dict[tuple[str, str, str, str], str] = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file() or should_skip(path, source):
            continue
        if path.name in TRACE_FILES:
            kind = "trace"
        elif path.name in CONTEXT_FILES:
            kind = "context"
        else:
            continue

        relative = path.relative_to(source)
        location = classify_source_path(relative, repo_lookup)
        original_size = path.stat().st_size
        digest = sha256(path)
        trace_key = None
        if kind == "trace" and location.get("run_id"):
            trace_key = (
                location["repository"],
                location["run_id"],
                path.name,
                digest,
            )

        logical_path = str(location["output"])
        should_compress = kind == "trace" and gzip_threshold > 0 and original_size >= gzip_threshold
        stored_logical_path = f"{logical_path}.gz" if should_compress else logical_path
        duplicate_of = canonical_trace_paths.get(trace_key) if trace_key else None
        if duplicate_of:
            output_path = duplicate_of
            materialized = False
            stored_path = dest / output_path
        else:
            output = dest / stored_logical_path
            output.parent.mkdir(parents=True, exist_ok=True)
            if should_compress:
                gzip_copy(path, output)
            else:
                link_or_copy(path, output)
            output_path = stored_logical_path
            materialized = True
            stored_path = output
            if trace_key:
                canonical_trace_paths[trace_key] = output_path

        stored_size = stored_path.stat().st_size
        entries.append(
            {
                "kind": kind,
                "project": location["project"],
                "repository": location["repository"],
                "owner": location["owner"],
                "repo": location["repo"],
                "surface": location["surface"],
                "path": output_path,
                "logical_path": logical_path,
                "source_relative_path": str(relative),
                "source": str(path),
                "size_bytes": original_size,
                "stored_size_bytes": stored_size,
                "sha256": digest,
                "stored_sha256": sha256(stored_path),
                "compressed": should_compress,
                "materialized": materialized,
                "duplicate_of": duplicate_of,
            }
        )

    manifest = build_manifest(source, entries)
    write_json(dest / "manifest.json", manifest)
    write_tsv(dest / "MANIFEST.tsv", entries)
    write_repo_indexes(dest / "repos", manifest)
    write_readme(dest / "README.md", manifest)
    print(
        f"prepared {dest}: traces={manifest['trace_file_count']} "
        f"unique_traces={manifest['unique_trace_file_count']} "
        f"context={manifest['context_file_count']} "
        f"bytes={manifest['trace_size_bytes']} "
        f"unique_bytes={manifest['unique_trace_size_bytes']} "
        f"stored_bytes={manifest['stored_size_bytes']}"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare a compact public trace bundle from full-history traces.")
    parser.add_argument("--source", default="build/full-agent-history", help="Full-history trace source directory.")
    parser.add_argument("--dest", default="public-traces", help="Publishable trace output directory.")
    parser.add_argument(
        "--gzip-threshold-bytes",
        type=int,
        default=1024 * 1024,
        help="Gzip materialized trace payloads at or above this byte size. Set to 0 to disable.",
    )
    parser.add_argument(
        "--config",
        default="config/targets.json",
        help="Optional target config used to resolve short slugs to OWNER/REPO paths.",
    )
    parser.add_argument("--clean", action="store_true", help="Delete destination before recreating it.")
    return parser.parse_args()


def should_skip(path: Path, source: Path) -> bool:
    relative = path.relative_to(source)
    return any(part in EXCLUDED_PARTS for part in relative.parts)


def load_repo_lookup(source: Path, config: Path | None) -> dict[str, str]:
    lookup = dict(DEFAULT_REPOS_BY_PROJECT)

    if config and config.exists():
        payload = json.loads(config.read_text(encoding="utf-8"))
        for target in payload.get("targets", []):
            repo = target.get("repo")
            short_name = target.get("short_name")
            if repo and short_name:
                lookup[short_name] = repo
                lookup[repo.split("/", 1)[-1]] = repo

    for index in source.glob("*/*/index.json"):
        try:
            payload = json.loads(index.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        repo = payload.get("repo")
        if not repo:
            continue
        source_slug = index.parent.name
        project = project_from_slug(source_slug)
        lookup[source_slug] = repo
        lookup.setdefault(project, repo)
        lookup.setdefault(repo.split("/", 1)[-1], repo)

    return lookup


def classify_source_path(relative: Path, repo_lookup: dict[str, str]) -> dict[str, Any]:
    parts = relative.parts
    if not parts:
        raise ValueError("empty relative path")

    surface = parts[0]
    if surface == "actions":
        if len(parts) < 2:
            raise ValueError(f"unexpected Actions path: {relative}")
        workflow_slug = parts[1]
        run_id = parts[2] if len(parts) > 2 else ""
        project = project_from_slug(workflow_slug)
        repository = repo_lookup.get(workflow_slug) or repo_lookup.get(project) or f"unknown/{project}"
        owner, repo_name = split_repository(repository)
        output = Path("traces") / owner / repo_name / surface / workflow_slug / Path(*parts[2:])
    elif surface == "copilot":
        if len(parts) < 2:
            raise ValueError(f"unexpected Copilot path: {relative}")
        project = parts[1]
        run_id = ""
        repository = repo_lookup.get(project) or f"unknown/{project}"
        owner, repo_name = split_repository(repository)
        output = Path("traces") / owner / repo_name / surface / Path(*parts[2:])
    elif surface == "dragon-prs":
        if len(parts) < 2:
            raise ValueError(f"unexpected Dragon PR path: {relative}")
        project = parts[1]
        run_part = parts[3] if len(parts) > 3 else ""
        run_id = run_part.removeprefix("run-") if run_part.startswith("run-") else ""
        repository = repo_lookup.get(project) or f"unknown/{project}"
        owner, repo_name = split_repository(repository)
        output = Path("traces") / owner / repo_name / surface / Path(*parts[2:])
    else:
        project = "unknown"
        run_id = ""
        repository = "unknown/unknown"
        owner, repo_name = split_repository(repository)
        output = Path("traces") / owner / repo_name / relative

    return {
        "surface": surface,
        "project": project,
        "repository": repository,
        "owner": owner,
        "repo": repo_name,
        "run_id": run_id,
        "output": output,
    }


def project_from_slug(slug: str) -> str:
    for project in sorted(PROJECT_SLUGS, key=len, reverse=True):
        if slug == project or slug.startswith(f"{project}-"):
            return project
    return slug


def split_repository(repository: str) -> tuple[str, str]:
    if "/" not in repository:
        return "unknown", repository
    owner, repo = repository.split("/", 1)
    return owner, repo


def link_or_copy(source: Path, dest: Path) -> None:
    if dest.exists():
        dest.unlink()
    try:
        os.link(source, dest)
    except OSError:
        shutil.copy2(source, dest)


def gzip_copy(source: Path, dest: Path) -> None:
    if dest.exists():
        dest.unlink()
    with source.open("rb") as input_handle, dest.open("wb") as output_handle:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=output_handle,
            mtime=0,
        ) as gzip_handle:
            shutil.copyfileobj(input_handle, gzip_handle, length=1024 * 1024)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(source: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_surface: Counter[str] = Counter()
    by_project: Counter[str] = Counter()
    by_repository: Counter[str] = Counter()
    trace_files_by_project: Counter[str] = Counter()
    trace_files_by_repository: Counter[str] = Counter()
    unique_trace_files_by_project: Counter[str] = Counter()
    unique_trace_files_by_repository: Counter[str] = Counter()
    trace_bytes_by_project: Counter[str] = Counter()
    trace_bytes_by_repository: Counter[str] = Counter()
    unique_trace_bytes_by_project: Counter[str] = Counter()
    unique_trace_bytes_by_repository: Counter[str] = Counter()
    trace_size = 0
    unique_trace_size = 0
    stored_size = 0
    stored_trace_size = 0
    stored_context_size = 0
    compressed_size = 0
    for entry in entries:
        by_surface[entry["surface"]] += 1
        by_project[entry["project"]] += 1
        by_repository[entry["repository"]] += 1
        if entry["materialized"]:
            stored_size += entry["stored_size_bytes"]
        if entry["kind"] == "trace":
            trace_size += entry["size_bytes"]
            trace_files_by_project[entry["project"]] += 1
            trace_files_by_repository[entry["repository"]] += 1
            trace_bytes_by_project[entry["project"]] += entry["size_bytes"]
            trace_bytes_by_repository[entry["repository"]] += entry["size_bytes"]
            if entry["materialized"]:
                unique_trace_size += entry["size_bytes"]
                stored_trace_size += entry["stored_size_bytes"]
                unique_trace_files_by_project[entry["project"]] += 1
                unique_trace_files_by_repository[entry["repository"]] += 1
                unique_trace_bytes_by_project[entry["project"]] += entry["size_bytes"]
                unique_trace_bytes_by_repository[entry["repository"]] += entry["size_bytes"]
                if entry["compressed"]:
                    compressed_size += entry["stored_size_bytes"]
        elif entry["materialized"]:
            stored_context_size += entry["stored_size_bytes"]

    return {
        "source": str(source),
        "file_count": len(entries),
        "materialized_file_count": sum(1 for entry in entries if entry["materialized"]),
        "trace_file_count": sum(1 for entry in entries if entry["kind"] == "trace"),
        "unique_trace_file_count": sum(
            1 for entry in entries if entry["kind"] == "trace" and entry["materialized"]
        ),
        "context_file_count": sum(1 for entry in entries if entry["kind"] == "context"),
        "trace_size_bytes": trace_size,
        "unique_trace_size_bytes": unique_trace_size,
        "stored_size_bytes": stored_size,
        "stored_trace_size_bytes": stored_trace_size,
        "stored_context_size_bytes": stored_context_size,
        "compressed_file_count": sum(
            1 for entry in entries if entry["materialized"] and entry["compressed"]
        ),
        "compressed_stored_size_bytes": compressed_size,
        "deduplicated_trace_file_count": sum(
            1 for entry in entries if entry["kind"] == "trace" and not entry["materialized"]
        ),
        "deduplicated_trace_size_bytes": sum(
            entry["size_bytes"]
            for entry in entries
            if entry["kind"] == "trace" and not entry["materialized"]
        ),
        "by_surface": dict(sorted(by_surface.items())),
        "by_project": dict(sorted(by_project.items())),
        "by_repository": dict(sorted(by_repository.items())),
        "trace_files_by_project": dict(sorted(trace_files_by_project.items())),
        "trace_files_by_repository": dict(sorted(trace_files_by_repository.items())),
        "unique_trace_files_by_project": dict(sorted(unique_trace_files_by_project.items())),
        "unique_trace_files_by_repository": dict(sorted(unique_trace_files_by_repository.items())),
        "trace_bytes_by_project": dict(sorted(trace_bytes_by_project.items())),
        "trace_bytes_by_repository": dict(sorted(trace_bytes_by_repository.items())),
        "unique_trace_bytes_by_project": dict(sorted(unique_trace_bytes_by_project.items())),
        "unique_trace_bytes_by_repository": dict(sorted(unique_trace_bytes_by_repository.items())),
        "files": entries,
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_tsv(path: Path, entries: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            "kind\trepository\towner\trepo\tproject\tsurface\tsize_bytes\tstored_size_bytes\tsha256\tstored_sha256\t"
            "compressed\tmaterialized\tpath\tlogical_path\tduplicate_of\tsource_relative_path\tsource\n"
        )
        for entry in entries:
            handle.write(
                f"{entry['kind']}\t{entry['repository']}\t{entry['owner']}\t{entry['repo']}\t"
                f"{entry['project']}\t{entry['surface']}\t"
                f"{entry['size_bytes']}\t{entry['stored_size_bytes']}\t"
                f"{entry['sha256']}\t{entry['stored_sha256']}\t"
                f"{entry['compressed']}\t{entry['materialized']}\t{entry['path']}\t"
                f"{entry['logical_path']}\t{entry['duplicate_of'] or ''}\t"
                f"{entry['source_relative_path']}\t{entry['source']}\n"
            )


def write_repo_indexes(path: Path, manifest: dict[str, Any]) -> None:
    by_repository: dict[str, list[dict[str, Any]]] = {}
    for entry in manifest["files"]:
        by_repository.setdefault(entry["repository"], []).append(entry)

    for repository, entries in sorted(by_repository.items()):
        owner, repo = split_repository(repository)
        repo_dir = path / owner / repo
        repo_dir.mkdir(parents=True, exist_ok=True)
        repo_manifest = {
            "repository": repository,
            "owner": owner,
            "repo": repo,
            "projects": sorted({entry["project"] for entry in entries}),
            "file_count": len(entries),
            "trace_file_count": sum(1 for entry in entries if entry["kind"] == "trace"),
            "unique_trace_file_count": sum(
                1 for entry in entries if entry["kind"] == "trace" and entry["materialized"]
            ),
            "context_file_count": sum(1 for entry in entries if entry["kind"] == "context"),
            "trace_size_bytes": sum(
                entry["size_bytes"] for entry in entries if entry["kind"] == "trace"
            ),
            "unique_trace_size_bytes": sum(
                entry["size_bytes"]
                for entry in entries
                if entry["kind"] == "trace" and entry["materialized"]
            ),
            "stored_size_bytes": sum(
                entry["stored_size_bytes"] for entry in entries if entry["materialized"]
            ),
            "stored_trace_size_bytes": sum(
                entry["stored_size_bytes"]
                for entry in entries
                if entry["kind"] == "trace" and entry["materialized"]
            ),
            "stored_context_size_bytes": sum(
                entry["stored_size_bytes"]
                for entry in entries
                if entry["kind"] == "context" and entry["materialized"]
            ),
            "compressed_file_count": sum(
                1 for entry in entries if entry["materialized"] and entry["compressed"]
            ),
            "files": entries,
        }
        write_json(repo_dir / "manifest.json", repo_manifest)
        (repo_dir / "README.md").write_text(
            f"""# {repository}

- Trace files: {repo_manifest['trace_file_count']}
- Unique stored trace files: {repo_manifest['unique_trace_file_count']}
- Context files: {repo_manifest['context_file_count']}
- Trace payload bytes: {repo_manifest['trace_size_bytes']}
- Unique stored trace bytes: {repo_manifest['unique_trace_size_bytes']}
- Stored trace bytes after compression: {repo_manifest['stored_trace_size_bytes']}
- Stored context bytes: {repo_manifest['stored_context_size_bytes']}
- Gzipped files: {repo_manifest['compressed_file_count']}

Trace files live under `../../../traces/{owner}/{repo}/`. Exact duplicate Action/Dragon payloads may be represented by a `duplicate_of` pointer in `manifest.json`. Trace payloads at or above the packaging threshold are stored as `.gz`.
""",
            encoding="utf-8",
        )


def write_readme(path: Path, manifest: dict[str, Any]) -> None:
    text = f"""# Public Agent Traces

This directory is a compact publishable copy of agent traces mined from GitHub-visible execution surfaces.

- Source: `{manifest['source']}`
- Trace files: {manifest['trace_file_count']}
- Unique stored trace files: {manifest['unique_trace_file_count']}
- Context files: {manifest['context_file_count']}
- Trace payload bytes: {manifest['trace_size_bytes']}
- Unique stored trace payload bytes: {manifest['unique_trace_size_bytes']}
- Stored trace payload bytes after deduplication and compression: {manifest['stored_trace_size_bytes']}
- Stored context bytes: {manifest['stored_context_size_bytes']}
- Gzipped files: {manifest['compressed_file_count']}

`traces/` is organized by GitHub repository path (`owner/repo`), then by retrieval surface (`actions`, `copilot`, `dragon-prs`).
`repos/` contains one manifest per GitHub repository path for easier browsing.
`manifest.json` and `MANIFEST.tsv` list every retrieved file with repository, source size, stored size, source SHA-256 digest, stored SHA-256 digest, source path, and stored path.
When the same trace payload was retrieved through both Actions and Dragon-PR mining, it is stored once and the duplicate manifest entry points to `duplicate_of`.
Trace payloads at or above the packaging threshold are stored as `.gz`.

Bulky workflow-run metadata, unzipped raw Action log directories, and transient working files are intentionally excluded.
Before publishing, run a final secret scan over this directory even when traces came from public GitHub surfaces.
"""
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
