import unittest

from scripts.prepare_public_traces import merge_index_payload


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
            "trace_run_count": 2,
            "trace_summaries": [
                {"run_id": "23415530555", "created_at": "2026-03-22T23:52:46Z"},
                {"run_id": "23415534239", "created_at": "2026-03-22T23:53:01Z"},
            ],
        }
        incoming = {
            "repo": "ai4curation/ai-gene-review",
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


if __name__ == "__main__":
    unittest.main()
