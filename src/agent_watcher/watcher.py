from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from .github_api import GitHubClient
from .models import Event, RepoReport, TargetRepo, TrackedItem


def scan_target(client: GitHubClient, target: TargetRepo, *, generated_at: datetime) -> RepoReport:
    window_start = generated_at - timedelta(days=target.lookback_days)
    report = RepoReport(
        target=target,
        generated_at=generated_at,
        window_start=window_start,
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
        for item in items:
            tracked = _scan_item(client, target, item)
            if tracked is not None:
                tracked_items.append(tracked)

        tracked_items.sort(key=lambda item: item.updated_at, reverse=True)
        report.tracked_items = tracked_items
    except Exception as exc:  # pragma: no cover - operational safety
        report.errors.append(str(exc))

    report.metrics = _build_metrics(report)
    report.assessment = _select_assessment(report)
    report.headline = _build_headline(report)
    report.recommendations = _build_recommendations(report)
    return report


def _scan_item(
    client: GitHubClient,
    target: TargetRepo,
    item: dict,
) -> TrackedItem | None:
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
    if not any(event.agent_actor or event.agent_reference for event in events):
        return None

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
            not event.agent_actor and event.created_at > last_agent_event_at for event in events
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
        "errors": len(report.errors),
    }


def _select_assessment(report: RepoReport) -> str:
    metrics = report.metrics
    if report.errors and not report.tracked_items:
        return "error"
    if metrics["agent_items"] == 0:
        return "no_signal"
    if metrics["stalled_items"] >= 2 or (
        metrics["agent_items"] >= 4 and metrics["stalled_items"] * 2 >= metrics["agent_items"]
    ):
        return "needs_attention"
    if metrics["closed_items"] >= max(1, metrics["agent_items"] - metrics["stalled_items"]) and (
        metrics["stalled_items"] == 0
    ):
        return "strong"
    return "mixed"


def _build_headline(report: RepoReport) -> str:
    metrics = report.metrics
    days = report.target.lookback_days
    if report.assessment == "error":
        return f"Collection failed for {report.target.repo}; see errors below before trusting this run."
    if report.assessment == "no_signal":
        return (
            f"No agent-related issues or PRs were detected in {report.target.repo} "
            f"within the last {days} days."
        )
    if report.assessment == "needs_attention":
        return (
            f"Needs attention: {metrics['stalled_items']} open item(s) show human follow-up after "
            f"the latest agent action in {report.target.repo}."
        )
    if report.assessment == "strong":
        return (
            f"Strong: {metrics['closed_items']} of {metrics['agent_items']} agent-touched item(s) "
            f"closed or merged in {report.target.repo} with no obvious stalled follow-up."
        )
    return (
        f"Mixed: {metrics['closed_items']} of {metrics['agent_items']} agent-touched item(s) "
        f"closed or merged in {report.target.repo}; {metrics['stalled_items']} may need attention."
    )


def _build_recommendations(report: RepoReport) -> list[str]:
    metrics = report.metrics
    recommendations: list[str] = []

    if report.errors:
        recommendations.append("Resolve API or permissions failures before relying on the score.")
    if metrics["agent_items"] == 0:
        recommendations.append(
            "If this repo should have agent traffic, widen the lookback window or add repo-specific match strings."
        )
    if metrics["stalled_items"] > 0:
        recommendations.append(
            "Inspect open items with human follow-up after the latest agent action; they are the clearest drag signal."
        )
    if metrics["mention_only_items"] > 0:
        recommendations.append(
            "Some items mention agents without agent-authored artifacts. Confirm the automation still responds in this repo."
        )
    if metrics["merged_prs"] > 0:
        recommendations.append(
            "Merged agent-authored PRs are showing concrete value; keep logging which workflow or harness produced them."
        )
    if not recommendations:
        recommendations.append("No urgent changes suggested from this run.")
    return recommendations


def _build_item_signals(item: TrackedItem) -> list[str]:
    signals: list[str] = []

    if item.author_is_agent:
        signals.append(f"opened by `{item.author}`")
    elif item.agent_actor_logins:
        logins = ", ".join(f"`{login}`" for login in item.agent_actor_logins)
        signals.append(f"agent activity from {logins}")

    if item.agent_reference_hits:
        signals.append(f"{item.agent_reference_hits} agent mention(s)")

    signals.append(f"latest actor `{item.latest_actor}`")
    signals.append(item.status)

    if item.status == "open" and item.human_follow_up_after_agent:
        signals.append("human follow-up after latest agent action")
    elif item.status == "open" and item.latest_actor_is_agent:
        signals.append("waiting on human response")

    return signals


def _matches_actor(actor: str, fragments: Iterable[str]) -> bool:
    normalized = actor.strip().lower()
    return bool(normalized) and any(fragment in normalized for fragment in fragments)


def _matches_text(text: str, patterns: Iterable[str]) -> bool:
    normalized = text.lower()
    return any(pattern in normalized for pattern in patterns)


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
