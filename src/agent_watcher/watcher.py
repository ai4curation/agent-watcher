from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from .github_api import GitHubClient
from .models import Event, RepoReport, TargetRepo, TrackedItem
from .scheduling import build_target_run_metadata

MAX_OPPORTUNITY_ITEMS = 5


def scan_target(client: GitHubClient, target: TargetRepo, *, generated_at: datetime) -> RepoReport:
    window_start = generated_at - timedelta(days=target.lookback_days)
    run_metadata = build_target_run_metadata(target, generated_at)
    report = RepoReport(
        target=target,
        generated_at=generated_at,
        window_start=window_start,
        report_date=run_metadata.report_date,
        issue_title=run_metadata.issue_title,
        recent_items_scanned=0,
    )

    try:
        items = client.list_recent_issues_and_prs(
            target.repo,
            since=window_start.isoformat().replace("+00:00", "Z"),
            max_items=target.max_items,
        )
        report.recent_items_scanned = len(items)

        tracked_items: list[TrackedItem] = []
        opportunity_items: list[TrackedItem] = []
        for item in items:
            scanned = _scan_item(client, target, item)
            if _has_agent_activity(scanned):
                tracked_items.append(scanned)
            elif _is_opportunity_candidate(scanned):
                opportunity_items.append(scanned)

        tracked_items.sort(key=lambda item: item.updated_at, reverse=True)
        opportunity_items.sort(key=lambda item: item.updated_at, reverse=True)
        report.tracked_items = tracked_items
        report.opportunity_items = opportunity_items[:MAX_OPPORTUNITY_ITEMS]
    except Exception as exc:  # pragma: no cover - operational safety
        report.errors.append(str(exc))

    report.metrics = _build_metrics(report)
    return report


def _scan_item(
    client: GitHubClient,
    target: TargetRepo,
    item: dict,
) -> TrackedItem:
    number = int(item["number"])
    title = item.get("title", "")
    url = item.get("html_url", "")
    kind = "pr" if item.get("pull_request") else "issue"
    author = _actor_login(item.get("user"))
    body_text = "\n".join(part for part in [title, item.get("body", "")] if part)

    events: list[Event] = [
        _build_event(
            actor=author,
            created_at=_parse_datetime(item["created_at"]),
            kind="body",
            body=body_text,
            url=url,
            target=target,
        )
    ]

    comments = client.list_issue_comments(
        target.repo,
        number,
        max_comments=target.max_comments_per_item,
    )
    for comment in comments:
        events.append(
            _build_event(
                actor=_actor_login(comment.get("user")),
                created_at=_parse_datetime(comment["created_at"]),
                kind="comment",
                body=comment.get("body", ""),
                url=comment.get("html_url", url),
                target=target,
            )
        )

    merged = False
    if kind == "pr":
        pr = client.get_pr(target.repo, number)
        merged = bool(pr.get("merged_at"))
        reviews = client.list_pr_reviews(
            target.repo,
            number,
            max_reviews=target.max_comments_per_item,
        )
        for review in reviews:
            submitted_at = review.get("submitted_at")
            if not submitted_at:
                continue
            events.append(
                _build_event(
                    actor=_actor_login(review.get("user")),
                    created_at=_parse_datetime(submitted_at),
                    kind="review",
                    body=review.get("body", ""),
                    url=review.get("html_url", url),
                    target=target,
                )
            )

    events.sort(key=lambda event: event.created_at)

    latest_event = events[-1]
    author_is_agent = _matches_actor(author, target.agent_login_substrings)
    agent_actor_logins = sorted({event.actor for event in events if event.agent_actor})
    agent_reference_hits = sum(1 for event in events if event.agent_reference)
    last_agent_event_at = max(
        (event.created_at for event in events if event.agent_actor),
        default=None,
    )
    human_follow_up_after_agent = False
    if last_agent_event_at is not None:
        human_follow_up_after_agent = any(
            not event.agent_actor
            and not _is_bot_actor(event.actor)
            and event.created_at > last_agent_event_at
            for event in events
        )

    status = _status_label(state=item["state"], merged=merged)
    tracked = TrackedItem(
        repo=target.repo,
        number=number,
        kind=kind,
        title=title,
        url=url,
        status=status,
        created_at=_parse_datetime(item["created_at"]),
        updated_at=_parse_datetime(item["updated_at"]),
        author=author,
        author_is_agent=author_is_agent,
        agent_actor_logins=agent_actor_logins,
        agent_reference_hits=agent_reference_hits,
        latest_actor=latest_event.actor,
        latest_actor_is_agent=latest_event.agent_actor,
        latest_excerpt=_excerpt(latest_event.body),
        last_agent_event_at=last_agent_event_at,
        human_follow_up_after_agent=human_follow_up_after_agent,
        signals=[],
        events=events,
    )
    tracked.signals = _build_item_signals(tracked)
    return tracked


def _build_event(
    *,
    actor: str,
    created_at: datetime,
    kind: str,
    body: str,
    url: str,
    target: TargetRepo,
) -> Event:
    return Event(
        actor=actor,
        created_at=created_at,
        kind=kind,
        body=body,
        url=url,
        agent_actor=_matches_actor(actor, target.agent_login_substrings),
        agent_reference=_matches_text(body, target.agent_text_patterns),
    )


def _build_metrics(report: RepoReport) -> dict[str, int]:
    tracked = report.tracked_items
    return {
        "recent_items_scanned": report.recent_items_scanned,
        "agent_items": len(tracked),
        "agent_summons": sum(item.agent_reference_hits for item in tracked),
        "merged_prs": sum(1 for item in tracked if item.kind == "pr" and item.status == "merged"),
        "closed_items": sum(1 for item in tracked if item.status in {"closed", "merged"}),
        "open_items": sum(1 for item in tracked if item.status == "open"),
        "stalled_items": sum(
            1 for item in tracked if item.status == "open" and item.human_follow_up_after_agent
        ),
        "awaiting_human_items": sum(
            1 for item in tracked if item.status == "open" and item.latest_actor_is_agent
        ),
        "mention_only_items": sum(
            1 for item in tracked if not item.agent_actor_logins and item.agent_reference_hits > 0
        ),
        "agent_authored_items": sum(1 for item in tracked if item.author_is_agent),
        "potential_opportunities": len(report.opportunity_items),
        "errors": len(report.errors),
    }


def _build_item_signals(item: TrackedItem) -> list[str]:
    signals: list[str] = []

    if item.author_is_agent:
        signals.append(f"opened by `{item.author}`")
    elif item.agent_actor_logins:
        logins = ", ".join(f"`{login}`" for login in item.agent_actor_logins)
        signals.append(f"agent activity from {logins}")
    else:
        signals.append("no agent involvement detected")

    if item.agent_reference_hits:
        signals.append(f"{item.agent_reference_hits} agent mention(s)")

    signals.append(f"latest actor `{item.latest_actor}`")
    signals.append(item.status)

    if item.status == "open" and item.human_follow_up_after_agent:
        signals.append("human follow-up after latest agent action")
    elif item.status == "open" and item.latest_actor_is_agent:
        signals.append("waiting on human response")

    return signals


def _has_agent_activity(item: TrackedItem) -> bool:
    return item.author_is_agent or bool(item.agent_actor_logins) or item.agent_reference_hits > 0


def _is_opportunity_candidate(item: TrackedItem) -> bool:
    if item.kind != "issue" or item.status != "open":
        return False
    if _has_agent_activity(item):
        return False
    return not _is_bot_actor(item.latest_actor)


def _matches_actor(actor: str, fragments: Iterable[str]) -> bool:
    normalized = actor.strip().lower()
    return bool(normalized) and any(fragment in normalized for fragment in fragments)


def _matches_text(text: str, patterns: Iterable[str]) -> bool:
    normalized = text.lower()
    return any(pattern in normalized for pattern in patterns)


def _is_bot_actor(actor: str) -> bool:
    normalized = actor.strip().lower()
    return normalized.endswith("[bot]") or normalized == "github-actions"


def _status_label(*, state: str, merged: bool) -> str:
    if merged:
        return "merged"
    return "closed" if state == "closed" else "open"


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _actor_login(user: dict | None) -> str:
    if not user:
        return "unknown"
    return user.get("login") or "unknown"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _excerpt(text: str, *, limit: int = 120) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."
