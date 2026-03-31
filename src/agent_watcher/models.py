from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class TargetRepo:
    repo: str
    display_name: str
    short_name: str
    lookback_days: int
    max_items: int
    max_comments_per_item: int
    report_timezone: str
    issue_mode: str
    issue_title_template: str
    cadence: str
    preferred_weekday_utc: int | None
    preferred_hour_utc: int | None
    extra_prompt: str
    agent_login_substrings: tuple[str, ...]
    agent_text_patterns: tuple[str, ...]


@dataclass(frozen=True)
class Event:
    actor: str
    created_at: datetime
    kind: str
    body: str
    url: str
    agent_actor: bool
    agent_reference: bool


@dataclass
class TrackedItem:
    repo: str
    number: int
    kind: str
    title: str
    url: str
    status: str
    created_at: datetime
    updated_at: datetime
    author: str
    author_is_agent: bool
    agent_actor_logins: list[str] = field(default_factory=list)
    agent_reference_hits: int = 0
    latest_actor: str = "unknown"
    latest_actor_is_agent: bool = False
    latest_excerpt: str = ""
    last_agent_event_at: datetime | None = None
    human_follow_up_after_agent: bool = False
    signals: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)


@dataclass
class RepoReport:
    target: TargetRepo
    generated_at: datetime
    window_start: datetime
    report_date: str
    issue_title: str
    recent_items_scanned: int
    tracked_items: list[TrackedItem] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value
