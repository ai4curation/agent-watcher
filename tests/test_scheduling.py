from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_watcher.models import TargetRepo
from agent_watcher.scheduling import build_target_run_metadata, target_is_due


def _target(**overrides) -> TargetRepo:
    payload = {
        "repo": "geneontology/go-ontology",
        "display_name": "GO",
        "short_name": "go-ontology",
        "lookback_days": 14,
        "max_items": 20,
        "max_comments_per_item": 25,
        "report_timezone": "America/Los_Angeles",
        "issue_mode": "dated",
        "issue_title_template": "{short_name} report for {report_date}",
        "cadence": "weekly",
        "preferred_weekday_utc": 0,
        "preferred_hour_utc": 13,
        "extra_prompt": "",
        "agent_login_substrings": ("dragon-ai-agent",),
        "agent_text_patterns": ("@dragon-ai-agent",),
    }
    payload.update(overrides)
    return TargetRepo(**payload)


class SchedulingTests(TestCase):
    def test_builds_dated_issue_title_in_local_timezone(self):
        target = _target()
        metadata = build_target_run_metadata(target, _dt("2026-03-31T03:05:00Z"))
        self.assertEqual(metadata.report_date, "2026-03-30")
        self.assertEqual(metadata.issue_title, "go-ontology report for 2026-03-30")

    def test_weekly_target_due_only_on_matching_weekday_and_hour(self):
        target = _target(preferred_weekday_utc=0, preferred_hour_utc=13)
        self.assertTrue(target_is_due(target, _dt("2026-03-30T13:17:00Z")))
        self.assertFalse(target_is_due(target, _dt("2026-03-31T13:17:00Z")))
        self.assertFalse(target_is_due(target, _dt("2026-03-30T12:17:00Z")))

    def test_daily_target_due_each_day_at_matching_hour(self):
        target = _target(cadence="daily", preferred_weekday_utc=None, preferred_hour_utc=13)
        self.assertTrue(target_is_due(target, _dt("2026-03-30T13:17:00Z")))
        self.assertTrue(target_is_due(target, _dt("2026-03-31T13:17:00Z")))
        self.assertFalse(target_is_due(target, _dt("2026-03-31T11:17:00Z")))


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
