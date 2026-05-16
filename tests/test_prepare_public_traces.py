import gzip
import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_public_traces import load_existing_manifest, merge_index_payload


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
