import gzip
import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_public_traces import (
    compact_public_index_payload,
    load_existing_manifest,
    merge_index_payload,
)


class PreparePublicTracesTestCase(unittest.TestCase):
    def test_dragon_index_merge_prepends_new_prs(self):
        existing = {
            "repo": "geneontology/go-ontology",
            "pr_count": 2,
            "trace_pr_count": 1,
            "missing_trace_count": 1,
            "prs": [
                {"number": 32048, "trace_summaries": [{"run_id": "25498837064"}]},
                {"number": 32047, "trace_summaries": []},
            ],
        }
        incoming = {
            "repo": "geneontology/go-ontology",
            "pr_count": 2,
            "trace_pr_count": 2,
            "missing_trace_count": 0,
            "prs": [
                {"number": 32116, "trace_summaries": [{"run_id": "25947155418"}]},
                {"number": 32115, "trace_summaries": [{"run_id": "25939366989"}]},
            ],
        }

        merged = merge_index_payload(existing, incoming)

        self.assertEqual([pr["number"] for pr in merged["prs"]], [32116, 32115, 32048, 32047])
        self.assertEqual(merged["pr_count"], 4)
        self.assertEqual(merged["trace_pr_count"], 3)
        self.assertEqual(merged["missing_trace_count"], 1)

    def test_action_index_merge_preserves_existing_order(self):
        existing = {
            "repo": "ai4curation/ai-gene-review",
            "candidate_run_count": 3908,
            "from_artifacts": False,
            "trace_run_count": 2,
            "trace_summaries": [
                {"run_id": "23415530555", "created_at": "2026-03-22T23:52:46Z"},
                {"run_id": "23415534239", "created_at": "2026-03-22T23:53:01Z"},
            ],
        }
        incoming = {
            "repo": "ai4curation/ai-gene-review",
            "candidate_run_count": 2,
            "from_artifacts": True,
            "sample_count": 2,
            "samples": [
                {"run_id": "23415534239", "created_at": "2026-03-22T23:53:01Z"},
                {"run_id": "23415530555", "created_at": "2026-03-22T23:52:46Z"},
            ],
            "trace_run_count": 2,
            "trace_summaries": [
                {"run_id": "23415534239", "created_at": "2026-03-22T23:53:01Z"},
                {"run_id": "23415530555", "created_at": "2026-03-22T23:52:46Z"},
            ],
        }

        merged = merge_index_payload(existing, incoming)

        self.assertEqual(
            [summary["run_id"] for summary in merged["trace_summaries"]],
            ["23415530555", "23415534239"],
        )
        self.assertEqual(merged["trace_run_count"], 2)
        self.assertEqual(merged["candidate_run_count"], 3908)
        self.assertFalse(merged["from_artifacts"])
        self.assertNotIn("samples", merged)
        self.assertNotIn("sample_count", merged)

    def test_compact_public_index_payload_drops_full_diagnostics(self):
        raw = {
            "repo": "monarch-initiative/dismech",
            "sample_count": 1,
            "samples": [{"run_id": "123"}],
            "trace_run_count": 1,
            "trace_summaries": [
                {
                    "run_id": "123",
                    "run_url": "https://github.com/monarch-initiative/dismech/actions/runs/123",
                    "title": "Long workflow title",
                    "trace_record_count": 2,
                    "trace_job": {
                        "id": 456,
                        "name": "claude-review",
                        "conclusion": "success",
                        "html_url": "https://github.com/monarch-initiative/dismech/actions/jobs/456",
                    },
                    "type_counts": {"result": 1, "system": 1},
                }
            ],
            "skipped_run_count": 2,
            "skipped_runs": [
                {
                    "run_id": "125",
                    "created_at": "2026-06-02T12:00:00Z",
                    "title": "Skipped title",
                    "skipped_reason": "no trace artifact and no trace-like job",
                },
                {"run_id": "124", "skipped_reason": "no trace artifact"},
            ],
            "fetch_error_count": 1,
            "fetch_errors": [{"run_id": "126", "error": "HTTP 502"}],
        }

        compact = compact_public_index_payload(raw)

        self.assertTrue(compact["public_index_compacted"])
        self.assertNotIn("samples", compact)
        self.assertNotIn("sample_count", compact)
        self.assertNotIn("skipped_runs", compact)
        self.assertNotIn("fetch_errors", compact)
        self.assertEqual(compact["skipped_run_ids"], ["125", "124"])
        self.assertEqual(compact["fetch_error_keys"], ["run_id:126"])
        self.assertEqual(len(compact["recent_skipped_runs"]), 2)
        self.assertNotIn("title", compact["recent_skipped_runs"][0])

        trace_summary = compact["trace_summaries"][0]
        self.assertNotIn("title", trace_summary)
        self.assertNotIn("run_url", trace_summary)
        self.assertEqual(
            trace_summary["trace_job"],
            {"id": 456, "name": "claude-review", "conclusion": "success"},
        )

    def test_compact_existing_action_index_keeps_cumulative_ids(self):
        existing = {
            "repo": "monarch-initiative/dismech",
            "trace_run_count": 1,
            "trace_summaries": [{"run_id": "101"}],
            "skipped_run_count": 2,
            "skipped_run_ids": ["103", "102"],
            "fetch_error_count": 1,
            "fetch_error_keys": ["run_id:201"],
        }
        incoming = {
            "repo": "monarch-initiative/dismech",
            "trace_run_count": 2,
            "trace_summaries": [{"run_id": "104"}, {"run_id": "101"}],
            "skipped_run_count": 2,
            "skipped_runs": [{"run_id": "105"}, {"run_id": "102"}],
            "fetch_error_count": 2,
            "fetch_errors": [
                {"run_id": "202", "error": "HTTP 502"},
                {"run_id": "201", "error": "HTTP 502"},
            ],
        }

        merged = merge_index_payload(existing, incoming)
        compact = compact_public_index_payload(merged)

        self.assertEqual(
            [summary["run_id"] for summary in compact["trace_summaries"]],
            ["104", "101"],
        )
        self.assertEqual(compact["skipped_run_ids"], ["105", "103", "102"])
        self.assertEqual(compact["skipped_run_count"], 3)
        self.assertEqual(compact["fetch_error_keys"], ["run_id:202", "run_id:201"])
        self.assertEqual(compact["fetch_error_count"], 2)
        self.assertNotIn("skipped_runs", compact)
        self.assertNotIn("fetch_errors", compact)

    def test_existing_manifest_fallback_scans_trace_tree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "public-traces"
            dragon_trace = (
                root
                / "traces/geneontology/go-ontology/dragon-prs/pr-1/run-22/log-trace.jsonl"
            )
            dragon_trace.parent.mkdir(parents=True)
            dragon_trace.write_text('{"type":"result"}\n', encoding="utf-8")

            artifact_payload = b'{"type":"assistant"}\n'
            artifact_trace = (
                root
                / "traces/geneontology/go-ontology/actions/go-ontology/22/artifact/"
                / "claude-response-22/claude-execution-output.json.gz"
            )
            artifact_trace.parent.mkdir(parents=True)
            with gzip.open(artifact_trace, "wb") as handle:
                handle.write(artifact_payload)

            manifest = load_existing_manifest(
                root,
                {"go-ontology": "geneontology/go-ontology"},
            )
            entries_by_path = {entry["path"]: entry for entry in manifest["files"]}

        self.assertEqual(manifest["trace_file_count"], 2)
        self.assertIn(
            "traces/geneontology/go-ontology/dragon-prs/pr-1/run-22/log-trace.jsonl",
            entries_by_path,
        )
        self.assertEqual(
            entries_by_path[
                "traces/geneontology/go-ontology/dragon-prs/pr-1/run-22/log-trace.jsonl"
            ]["source_relative_path"],
            "dragon-prs/go-ontology/pr-1/run-22/log-trace.jsonl",
        )
        gz_entry = entries_by_path[
            "traces/geneontology/go-ontology/actions/go-ontology/22/artifact/"
            "claude-response-22/claude-execution-output.json.gz"
        ]
        self.assertTrue(gz_entry["compressed"])
        self.assertEqual(
            gz_entry["logical_path"],
            "traces/geneontology/go-ontology/actions/go-ontology/22/artifact/"
            "claude-response-22/claude-execution-output.json",
        )
        self.assertEqual(gz_entry["sha256"], hashlib.sha256(artifact_payload).hexdigest())


if __name__ == "__main__":
    unittest.main()
