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
INDEX_LIST_FIELDS = {
    "errors",
    "fetch_errors",
    "prs",
    "samples",
    "skipped_runs",
    "trace_summaries",
    "workflows",
}
INDEX_MAX_COUNT_FIELDS = {
    "candidate_run_count",
    "error_count",
    "fetch_error_count",
    "inspected_runs",
    "job_inspected_count",
    "log_count",
    "original_run_count",
    "session_count",
    "session_pr_count",
    "skipped_run_count",
    "trace_job_run_count",
    "visible_agent_task_count",
}
PUBLIC_INDEX_RECENT_DIAGNOSTIC_LIMIT = 20
PUBLIC_INDEX_TRACE_SUMMARY_FIELDS = (
    "run_id",
    "created_at",
    "event",
    "conclusion",
    "trace_record_count",
    "artifact_trace_files",
    "session_ids",
    "type_counts",
    "log_error",
)
PUBLIC_INDEX_TRACE_JOB_FIELDS = ("id", "name", "conclusion")
PUBLIC_INDEX_SKIPPED_RUN_FIELDS = (
    "run_id",
    "created_at",
    "event",
    "conclusion",
    "skipped_reason",
)
PUBLIC_INDEX_FETCH_ERROR_FIELDS = ("run_id", "error")
PUBLIC_INDEX_PR_FIELDS = (
    "number",
    "url",
    "state",
    "title",
    "created_at",
    "updated_at",
    "head_ref",
    "issue_number",
    "run_number",
    "run_ids",
    "missing_reason",
)
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
    catalog_dest = Path(args.catalog_dest) if args.catalog_dest else dest
    gzip_threshold = args.gzip_threshold_bytes

    if not source.exists():
        raise SystemExit(f"source does not exist: {source}")

    if dest.exists() and args.clean:
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if catalog_dest != dest and catalog_dest.exists():
        shutil.rmtree(catalog_dest)
    catalog_dest.mkdir(parents=True, exist_ok=True)

    repo_lookup = load_repo_lookup(source, Path(args.config) if args.config else None)
    existing_manifest = load_existing_manifest(dest, repo_lookup) if args.merge_existing else {}
    entries_by_key: dict[str, dict[str, Any]] = {}
    entry_order: list[str] = []
    canonical_trace_paths: dict[tuple[str, str, str, str], str] = {}
    if args.merge_existing:
        for entry in existing_manifest.get("files", []):
            key = entry_key(entry)
            entries_by_key[key] = entry
            entry_order.append(key)
            trace_key = trace_key_for_entry(entry)
            if trace_key and entry.get("materialized") and (dest / entry["path"]).exists():
                canonical_trace_paths.setdefault(trace_key, entry["path"])

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
        canonical_path = canonical_trace_paths.get(trace_key) if trace_key else None
        duplicate_of = canonical_path if canonical_path and canonical_path != stored_logical_path else None
        if duplicate_of:
            output_path = duplicate_of
            materialized = False
            stored_path = dest / output_path
        else:
            output = dest / stored_logical_path
            output.parent.mkdir(parents=True, exist_ok=True)
            if kind == "context" and path.name == "index.json":
                write_public_index_file(output, path, merge_existing=args.merge_existing)
            elif should_compress:
                gzip_copy(path, output)
            else:
                link_or_copy(path, output)
            output_path = stored_logical_path
            materialized = True
            stored_path = output
            if trace_key:
                canonical_trace_paths[trace_key] = output_path

        stored_size = stored_path.stat().st_size
        entry = {
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
        key = entry_key(entry)
        if key not in entries_by_key:
            entry_order.append(key)
        entries_by_key[key] = entry

    if args.merge_existing:
        entries = [entries_by_key[key] for key in entry_order if key in entries_by_key]
    else:
        entries = sorted(
            entries_by_key.values(),
            key=lambda entry: (entry["source_relative_path"], entry["path"]),
        )

    manifest_source = existing_manifest.get("source", str(source)) if args.merge_existing else str(source)
    manifest = build_manifest(manifest_source, entries)
    write_json(catalog_dest / "manifest.json", manifest)
    write_tsv(catalog_dest / "MANIFEST.tsv", entries)
    write_repo_indexes(catalog_dest / "repos", manifest)
    write_readme(catalog_dest / "README.md", manifest)
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
    parser.add_argument(
        "--catalog-dest",
        help=(
            "Directory for generated manifest, TSV, and repo catalogs. "
            "Defaults to --dest for backwards-compatible local packaging."
        ),
    )
    parser.add_argument(
        "--merge-existing",
        action="store_true",
        help="Merge newly prepared files into an existing destination manifest instead of replacing old entries.",
    )
    return parser.parse_args()


def should_skip(path: Path, source: Path) -> bool:
    relative = path.relative_to(source)
    return any(part in EXCLUDED_PARTS for part in relative.parts)


def load_existing_manifest(dest: Path, repo_lookup: dict[str, str]) -> dict[str, Any]:
    manifest_path = dest / "manifest.json"
    if manifest_path.exists():
        return dict(json.loads(manifest_path.read_text(encoding="utf-8")))
    return build_existing_manifest_from_tree(dest, repo_lookup)


def build_existing_manifest_from_tree(dest: Path, repo_lookup: dict[str, str]) -> dict[str, Any]:
    traces_root = dest / "traces"
    if not traces_root.exists():
        return {}

    entries = []
    for path in sorted(traces_root.rglob("*")):
        if not path.is_file():
            continue
        payload_name = trace_payload_name(path)
        if payload_name in TRACE_FILES:
            kind = "trace"
        elif payload_name in CONTEXT_FILES:
            kind = "context"
        else:
            continue
        entries.append(build_entry_from_public_path(dest, path, kind, payload_name, repo_lookup))
    return build_manifest(str(dest), entries)


def trace_payload_name(path: Path) -> str:
    name = path.name
    if name.endswith(".gz"):
        return name.removesuffix(".gz")
    return name


def build_entry_from_public_path(
    dest: Path,
    path: Path,
    kind: str,
    payload_name: str,
    repo_lookup: dict[str, str],
) -> dict[str, Any]:
    relative = path.relative_to(dest)
    location = classify_public_path(relative, repo_lookup)
    compressed = path.name.endswith(".gz")
    stored_size = path.stat().st_size
    stored_digest = sha256(path)
    if compressed:
        original_size, digest = gzip_payload_stats(path)
        logical_path = str(relative.with_name(payload_name))
    else:
        original_size = stored_size
        digest = stored_digest
        logical_path = str(relative)

    return {
        "kind": kind,
        "project": location["project"],
        "repository": location["repository"],
        "owner": location["owner"],
        "repo": location["repo"],
        "surface": location["surface"],
        "path": str(relative),
        "logical_path": logical_path,
        "source_relative_path": str(location["source_relative_path"]),
        "source": str(path),
        "size_bytes": original_size,
        "stored_size_bytes": stored_size,
        "sha256": digest,
        "stored_sha256": stored_digest,
        "compressed": compressed,
        "materialized": True,
        "duplicate_of": None,
    }


def classify_public_path(relative: Path, repo_lookup: dict[str, str]) -> dict[str, Any]:
    parts = relative.parts
    if len(parts) < 5 or parts[0] != "traces":
        raise ValueError(f"unexpected public trace path: {relative}")

    owner, repo_name, surface = parts[1], parts[2], parts[3]
    repository = f"{owner}/{repo_name}"
    project = project_for_repository(repository, repo_lookup)
    tail = Path(*parts[4:])
    run_id = ""

    if surface == "actions":
        workflow_slug = parts[4]
        project = project_from_slug(workflow_slug)
        tail = Path(*parts[5:]) if len(parts) > 5 else Path()
        run_id = parts[5] if len(parts) > 5 else ""
        source_relative_path = Path("actions") / workflow_slug / tail
    elif surface == "copilot":
        source_relative_path = Path("copilot") / project / tail
    elif surface == "dragon-prs":
        for part in parts[4:]:
            if part.startswith("run-") and part.removeprefix("run-").isdigit():
                run_id = part.removeprefix("run-")
                break
        source_relative_path = Path("dragon-prs") / project / tail
    else:
        source_relative_path = relative

    return {
        "surface": surface,
        "project": project,
        "repository": repository,
        "owner": owner,
        "repo": repo_name,
        "run_id": run_id,
        "source_relative_path": source_relative_path,
    }


def project_for_repository(repository: str, repo_lookup: dict[str, str]) -> str:
    repo_basename = repository.split("/", 1)[-1]
    for project in PROJECT_SLUGS:
        if repo_lookup.get(project) == repository:
            return project
    for slug, repo in repo_lookup.items():
        if repo == repository and slug != repo_basename:
            return slug
    return repo_basename


def entry_key(entry: dict[str, Any]) -> str:
    return "\t".join(
        [
            entry.get("kind", ""),
            entry.get("repository", ""),
            entry.get("source_relative_path", ""),
            entry.get("logical_path", ""),
        ]
    )


def trace_key_for_entry(entry: dict[str, Any]) -> tuple[str, str, str, str] | None:
    if entry.get("kind") != "trace":
        return None
    repository = entry.get("repository")
    digest = entry.get("sha256")
    logical_path = entry.get("logical_path") or entry.get("path")
    run_id = run_id_from_entry_path(entry)
    if not repository or not digest or not logical_path or not run_id:
        return None
    return (repository, run_id, Path(logical_path).name, digest)


def run_id_from_entry_path(entry: dict[str, Any]) -> str:
    parts = Path(entry.get("source_relative_path") or "").parts
    if parts:
        surface = parts[0]
        if surface == "actions" and len(parts) > 2:
            return parts[2]
        if surface == "dragon-prs" and len(parts) > 3 and parts[3].startswith("run-"):
            return parts[3].removeprefix("run-")

    path_parts = Path(entry.get("logical_path") or entry.get("path") or "").parts
    if "actions" in path_parts:
        index = path_parts.index("actions")
        if len(path_parts) > index + 2:
            return path_parts[index + 2]
    for part in path_parts:
        if part.startswith("run-") and part.removeprefix("run-").isdigit():
            return part.removeprefix("run-")
    return ""


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


def gzip_payload_stats(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with gzip.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def write_public_index_file(
    output_path: Path,
    source_path: Path,
    *,
    merge_existing: bool,
) -> None:
    incoming = json.loads(source_path.read_text(encoding="utf-8"))
    if merge_existing and output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        payload = merge_index_payload(existing, incoming)
    else:
        payload = incoming
    write_json(output_path, compact_public_index_payload(payload))


def merge_index_file(existing_path: Path, new_path: Path) -> None:
    write_public_index_file(existing_path, new_path, merge_existing=True)


def compact_public_index_payload(index: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in index.items():
        if key in {
            "errors",
            "fetch_errors",
            "sample_count",
            "samples",
            "skipped_runs",
        }:
            continue
        if key == "trace_summaries":
            compact[key] = [compact_trace_summary(summary) for summary in value]
        elif key == "prs":
            compact[key] = [compact_pr_summary(pr) for pr in value]
        elif key == "recent_skipped_runs":
            compact[key] = [
                compact_skipped_run(run)
                for run in value[:PUBLIC_INDEX_RECENT_DIAGNOSTIC_LIMIT]
            ]
        elif key == "recent_fetch_errors":
            compact[key] = [
                compact_fetch_error(error)
                for error in value[:PUBLIC_INDEX_RECENT_DIAGNOSTIC_LIMIT]
            ]
        else:
            compact[key] = value

    skipped_run_ids = merge_identity_values(
        string_values(index.get("skipped_run_ids", [])),
        item_field_values(index.get("skipped_runs", []), ("run_id", "id")),
    )
    if skipped_run_ids:
        compact["skipped_run_ids"] = skipped_run_ids
        compact["skipped_run_count"] = max_int(
            index.get("skipped_run_count"),
            len(skipped_run_ids),
        )

    fetch_error_keys = merge_identity_values(
        string_values(index.get("fetch_error_keys", [])),
        item_identity_values(index.get("fetch_errors", []), ("run_id", "id")),
    )
    if fetch_error_keys:
        compact["fetch_error_keys"] = fetch_error_keys
        compact["fetch_error_count"] = max_int(
            index.get("fetch_error_count"),
            len(fetch_error_keys),
        )

    errors = index.get("errors", [])
    if errors:
        compact["error_count"] = max_int(index.get("error_count"), len(errors))
        compact["recent_errors"] = errors[:PUBLIC_INDEX_RECENT_DIAGNOSTIC_LIMIT]

    if index.get("skipped_runs"):
        compact["recent_skipped_runs"] = [
            compact_skipped_run(run)
            for run in index["skipped_runs"][:PUBLIC_INDEX_RECENT_DIAGNOSTIC_LIMIT]
        ]
    if index.get("fetch_errors"):
        compact["recent_fetch_errors"] = [
            compact_fetch_error(error)
            for error in index["fetch_errors"][:PUBLIC_INDEX_RECENT_DIAGNOSTIC_LIMIT]
        ]

    if "trace_summaries" in compact:
        compact["trace_run_count"] = max_int(
            index.get("trace_run_count"),
            len(compact["trace_summaries"]),
        )

    compact["public_index_compacted"] = True
    return compact


def compact_pr_summary(pr: dict[str, Any]) -> dict[str, Any]:
    compact = compact_fields(pr, PUBLIC_INDEX_PR_FIELDS)
    if "trace_summaries" in pr:
        compact["trace_summaries"] = [
            compact_trace_summary(summary) for summary in pr.get("trace_summaries", [])
        ]
    if pr.get("errors"):
        compact["recent_errors"] = pr["errors"][:PUBLIC_INDEX_RECENT_DIAGNOSTIC_LIMIT]
    return compact


def compact_trace_summary(summary: dict[str, Any]) -> dict[str, Any]:
    compact = compact_fields(summary, PUBLIC_INDEX_TRACE_SUMMARY_FIELDS)
    trace_job = summary.get("trace_job")
    if isinstance(trace_job, dict):
        compact_trace_job = compact_fields(trace_job, PUBLIC_INDEX_TRACE_JOB_FIELDS)
        if compact_trace_job:
            compact["trace_job"] = compact_trace_job
    return compact


def compact_skipped_run(run: dict[str, Any]) -> dict[str, Any]:
    return compact_fields(run, PUBLIC_INDEX_SKIPPED_RUN_FIELDS)


def compact_fetch_error(error: dict[str, Any]) -> dict[str, Any]:
    return compact_fields(error, PUBLIC_INDEX_FETCH_ERROR_FIELDS)


def compact_fields(payload: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        field: payload[field]
        for field in fields
        if field in payload and payload[field] not in (None, "", [], {})
    }


def merge_index_payload(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if (
            key not in merged
            and key not in INDEX_LIST_FIELDS
            and key not in INDEX_MAX_COUNT_FIELDS
            and not key.endswith("_count")
        ):
            merged[key] = value

    if "workflows" in existing or "workflows" in incoming:
        merged["workflows"] = merge_unique_values(
            existing.get("workflows", []),
            incoming.get("workflows", []),
        )

    if "errors" in existing or "errors" in incoming:
        merged["errors"] = merge_unique_values(
            existing.get("errors", []),
            incoming.get("errors", []),
        )
        merged["error_count"] = max_int(
            existing.get("error_count"),
            incoming.get("error_count"),
            len(merged["errors"]),
        )

    if any(
        key in existing or key in incoming
        for key in ("fetch_errors", "fetch_error_keys", "fetch_error_count")
    ):
        fetch_errors = merge_unique_values(
            existing.get("fetch_errors", []),
            incoming.get("fetch_errors", []),
        )
        if fetch_errors:
            merged["fetch_errors"] = fetch_errors
        fetch_error_keys = merge_identity_values(
            identity_values_from_index(existing, "fetch_error_keys", "fetch_errors"),
            identity_values_from_index(incoming, "fetch_error_keys", "fetch_errors"),
        )
        if fetch_error_keys:
            merged["fetch_error_keys"] = fetch_error_keys
        merged["fetch_error_count"] = max_int(
            existing.get("fetch_error_count"),
            incoming.get("fetch_error_count"),
            len(fetch_errors),
            len(fetch_error_keys),
        )

    if any(
        key in existing or key in incoming
        for key in ("skipped_runs", "skipped_run_ids", "skipped_run_count")
    ):
        skipped_runs = merge_by_identity(
            existing.get("skipped_runs", []),
            incoming.get("skipped_runs", []),
            identity_fields=("run_id", "id"),
        )
        if skipped_runs:
            merged["skipped_runs"] = skipped_runs
        skipped_run_ids = merge_identity_values(
            run_id_values_from_index(existing, "skipped_run_ids", "skipped_runs"),
            run_id_values_from_index(incoming, "skipped_run_ids", "skipped_runs"),
        )
        if skipped_run_ids:
            merged["skipped_run_ids"] = skipped_run_ids
        merged["skipped_run_count"] = max_int(
            existing.get("skipped_run_count"),
            incoming.get("skipped_run_count"),
            len(skipped_runs),
            len(skipped_run_ids),
        )

    if "prs" in existing or "prs" in incoming:
        prs = merge_by_identity(
            existing.get("prs", []),
            incoming.get("prs", []),
            identity_fields=("number",),
        )
        merged["prs"] = prs
        if "copilot_pr_count" in merged or any(
            "sessions" in pr or "candidate_sessions" in pr for pr in merged["prs"]
        ):
            refresh_copilot_counts(merged)
        else:
            refresh_dragon_counts(merged)

    if (
        "samples" in existing
        or "samples" in incoming
        or "trace_summaries" in existing
        or "trace_summaries" in incoming
    ):
        summaries = merge_by_identity(
            existing.get("trace_summaries", existing.get("samples", [])),
            incoming.get("trace_summaries", incoming.get("samples", [])),
            identity_fields=("run_id", "id"),
        )
        merged_summaries = summaries
        merged["trace_summaries"] = merged_summaries
        keep_samples = "samples" in existing or (
            "samples" in incoming and "trace_summaries" not in existing
        )
        if keep_samples:
            merged["samples"] = merged_summaries
        else:
            merged.pop("samples", None)
        merged["trace_run_count"] = max_int(
            existing.get("trace_run_count"),
            incoming.get("trace_run_count"),
            len(merged_summaries),
        )
        if keep_samples or "sample_count" in existing:
            merged["sample_count"] = len(merged_summaries)
        skipped_run_count = merged.get("skipped_run_count")
        if not isinstance(skipped_run_count, int):
            skipped_run_count = len(merged.get("skipped_runs", []))
        known_runs = len(merged_summaries) + skipped_run_count
        if "candidate_run_count" in existing or "candidate_run_count" in incoming:
            merged["candidate_run_count"] = known_runs
        if "inspected_runs" in existing or "inspected_runs" in incoming:
            merged["inspected_runs"] = known_runs

    merge_max_count_fields(merged, existing, incoming)
    return merged


def merge_max_count_fields(
    merged: dict[str, Any],
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> None:
    for field in INDEX_MAX_COUNT_FIELDS:
        values = [
            value
            for value in (existing.get(field), incoming.get(field), merged.get(field))
            if isinstance(value, int)
        ]
        if values:
            merged[field] = max(values)


def merge_by_identity(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    identity_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    by_identity: dict[str, dict[str, Any]] = {}
    existing_order: list[str] = []
    incoming_new_order: list[str] = []
    # Preserve existing order to avoid full-index churn; prepend only newly seen items.
    for item in existing:
        identity = item_identity(item, identity_fields)
        if identity not in by_identity:
            existing_order.append(identity)
        by_identity[identity] = item
    for item in incoming:
        identity = item_identity(item, identity_fields)
        if identity not in by_identity:
            incoming_new_order.append(identity)
        by_identity[identity] = item
    return [by_identity[identity] for identity in [*incoming_new_order, *existing_order]]


def item_identity(item: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = item.get(field)
        if value not in (None, ""):
            return f"{field}:{value}"
    return json.dumps(item, sort_keys=True)


def identity_values_from_index(
    index: dict[str, Any],
    value_field: str,
    item_field: str,
) -> list[str]:
    return merge_identity_values(
        string_values(index.get(value_field, [])),
        item_identity_values(index.get(item_field, []), ("run_id", "id")),
    )


def run_id_values_from_index(
    index: dict[str, Any],
    value_field: str,
    item_field: str,
) -> list[str]:
    return merge_identity_values(
        string_values(index.get(value_field, [])),
        item_field_values(index.get(item_field, []), ("run_id", "id")),
    )


def item_field_values(items: Any, fields: tuple[str, ...]) -> list[str]:
    if not isinstance(items, list):
        return []
    values: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in fields:
            value = item.get(field)
            if value not in (None, ""):
                values.append(str(value))
                break
    return values


def item_identity_values(items: Any, fields: tuple[str, ...]) -> list[str]:
    if not isinstance(items, list):
        return []
    return [item_identity(item, fields) for item in items if isinstance(item, dict)]


def string_values(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, str) and value]


def merge_identity_values(existing: list[str], incoming: list[str]) -> list[str]:
    existing_unique: list[str] = []
    incoming_new: list[str] = []
    seen: set[str] = set()
    for value in existing:
        if value in seen:
            continue
        existing_unique.append(value)
        seen.add(value)
    for value in incoming:
        if value in seen:
            continue
        incoming_new.append(value)
        seen.add(value)
    return [*incoming_new, *existing_unique]


def max_int(*values: Any) -> int:
    return max((value for value in values if isinstance(value, int)), default=0)


def merge_unique_values(existing: list[Any], incoming: list[Any]) -> list[Any]:
    values: list[Any] = []
    seen: set[str] = set()
    for value in [*existing, *incoming]:
        key = json.dumps(value, sort_keys=True)
        if key not in seen:
            values.append(value)
            seen.add(key)
    return values


def refresh_dragon_counts(index: dict[str, Any]) -> None:
    prs = index.get("prs", [])
    index["pr_count"] = len(prs)
    index["trace_pr_count"] = sum(1 for pr in prs if pr.get("trace_summaries"))
    index["missing_trace_count"] = sum(1 for pr in prs if not pr.get("trace_summaries"))


def refresh_copilot_counts(index: dict[str, Any]) -> None:
    prs = index.get("prs", [])
    sessions = [session for pr in prs for session in pr.get("sessions", [])]
    index["copilot_pr_count"] = len(prs)
    index["session_pr_count"] = sum(1 for pr in prs if pr.get("sessions"))
    index["session_count"] = len(sessions)
    index["log_count"] = sum(
        1 for session in sessions if session.get("log_path") or session.get("log_bytes")
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(source: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
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
        "source": source,
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
