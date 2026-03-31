# Agent Watcher Design

## Goal

Run a scheduled watcher in GitHub Actions that inspects selected deployment repos, gathers recent issues and PRs involving agents, and asks Claude Code to write a practical qualitative assessment of what appears to be working or not working.

## Non-Goals For The First Pass

- full conversational transcript analysis
- inline review comment harvesting
- autonomous remediation in watched repos
- cross-run trend analytics beyond rolling issue history

## Architecture

The current architecture has four phases:

1. `Select`
   - Load watched repos from [`config/targets.json`](/Users/cjm/repos/agent-watcher/config/targets.json)
   - Merge defaults with repo-specific overrides
   - Allow workflow or local CLI overrides for lookback window, max items, and single-target runs

2. `Collect`
   - Use the GitHub REST API to list recently updated issues and PRs per watched repo
   - Pull issue comments for all candidate items
   - Pull PR reviews and PR metadata for PRs
   - Keep the collector simple and token-friendly so it works in Actions without extra infrastructure

3. `Prepare Context`
   - Mark an item as agent-related when any of the following are true:
     - an author or commenter login matches configured agent substrings
     - a body, comment, or review contains agent markers such as `@dragon-ai-agent` or `Claude Code`
   - Produce a neutral Markdown dossier per watched repo
   - Include event timelines and lightweight supporting counts such as summons, merged PRs, and open items
   - Avoid making the final judgment in code

4. `Review And Publish`
   - Run `anthropics/claude-code-action@v1`
   - Ask Claude to read the generated dossier and, if needed, inspect a small number of linked GitHub items directly
   - Maintain one rolling issue per watched repo in this repository
   - Replace the issue body with a concise current-status summary
   - Append a dated comment per run with the fuller qualitative review

## Why Claude For The Final Review

The user goal is a qualitative operational readout, not just metrics. Claude is the right place for that judgment because:

- it can weigh patterns across issues and PRs instead of just counting states
- it can explain what seems to be working and not working in plain language
- it can use the structured collector output without the collector pretending to be the evaluator

The collector still matters because it gives Claude a bounded, auditable slice of evidence and keeps the workflow fast.

## Data Flow

```text
config/targets.json
        |
        v
  GitHub collector
        |
        v
 agent-related item signals
        |
        v
 neutral Markdown dossier
        |
        +--> build/context/*.json
        +--> build/context/*.md
        |
        v
 Claude Code Action
        |
        v
 rolling issue update in this repo
```

## GitHub Actions Design

### Scheduled Workflow

[`.github/workflows/scan-watched-repos.yml`](/Users/cjm/repos/agent-watcher/.github/workflows/scan-watched-repos.yml)

- runs on cron and manual dispatch
- selects configured targets, optionally filtered by workflow input
- generates one context artifact per target
- asks Claude to update the corresponding rolling issue in this repo
- uploads the generated context artifacts

### Validation Workflow

[`.github/workflows/validate.yml`](/Users/cjm/repos/agent-watcher/.github/workflows/validate.yml)

- runs on push and pull request
- compiles the Python source
- performs a dry-run collector pass against a small public target slice
- uploads the preview artifacts for inspection

## Permissions Model

Two token roles are enough for the first pass:

- `WATCHER_SOURCE_TOKEN`
  - read access on watched repos
  - used for listing issues, comments, PRs, and reviews in watched repos
- `ANTHROPIC_API_KEY`
  - used by the Claude Code Action
- `GITHUB_TOKEN`
  - write access on this repo issues
  - used by Claude for issue creation, reopening, body updates, and appended comments

This keeps publishing scoped to this repo while allowing watched repos to remain read-only.

## Current Limitations

- inline PR review comments are not fetched yet
- the qualitative review depends on the prompt and the available context
- detection relies on configured agent names and textual markers
- there is no persistent data store besides rolling issue history and workflow artifacts

## Natural Next Steps

- add repo-specific match rules where agents have distinct identities
- add inline review comment collection for PR-heavy repos
- persist run summaries as JSON history for trend charts
- improve prompt guidance for repo-specific ontology expectations
- tag noteworthy findings, such as repeated duplicate agent PRs or human rework after merge
