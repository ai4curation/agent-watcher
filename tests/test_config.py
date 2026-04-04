from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_watcher.config import load_targets


class ConfigTests(TestCase):
    def test_load_targets_filters_to_requested_repos(self):
        payload = {
            "defaults": {
                "lookback_days": 14,
                "max_items": 20,
                "max_comments_per_item": 25,
                "report_timezone": "America/Los_Angeles",
                "issue_mode": "dated",
                "issue_title_template": "{short_name} report for {report_date}",
                "cadence": "weekly",
                "preferred_hour_utc": 13,
                "extra_prompt": "",
                "agent_login_substrings": ["dragon-ai-agent"],
                "agent_text_patterns": ["@dragon-ai-agent"],
            },
            "targets": [
                {
                    "repo": "org/one",
                    "display_name": "One",
                    "short_name": "one",
                    "preferred_weekday_utc": 0,
                },
                {
                    "repo": "org/two",
                    "display_name": "Two",
                    "short_name": "two",
                    "preferred_weekday_utc": 1,
                },
            ],
        }

        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "targets.json"
            config_path.write_text(json.dumps(payload))

            targets = load_targets(config_path, only_repos={"org/two"})

        self.assertEqual([target.repo for target in targets], ["org/two"])
