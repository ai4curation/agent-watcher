# Agent Watcher Design

## Goal

Run a scheduled watcher in GitHub Actions that inspects selected deployment repos, gathers recent issues and PRs involving agents, and asks Claude Code to write a practical qualitative assessment of what appears to be working or not working.

In addition, run a weekly cross-repo review over the marked ontology repos that compares their agentic setup: instruction files, workflow conventions, and whether repeated work is decomposed into skills, commands, or subagents.

## Non-Goals For The First Pass

- full conversational transcript analysis
- inline review comment harvesting
- autonomous remediation in watched repos
- cross-run trend analytics beyond dated issue history

## Architecture

The current architecture has four phases:

1. `Select`
   - Load watched repos from [`config/targets.json`](/Users/cjm/repos/agent-watcher/config/targets.json)
   - Merge defaults with repo-specific overrides
   - Compute which repos are due for the current schedule slot based on cadence and preferred weekday
   - Compute deterministic report metadata including `report_date` and exact issue title
   - Allow workflow or local CLI overrides for single-target runs and collection window

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
   - When a recent open issue has no detected agent involvement, surface it as a possible missed opportunity for reviewer consideration
   - Include event timelines and lightweight supporting counts such as summons, merged PRs, and open items
   - Avoid making the final judgment in code

4. `Review And Publish`
   - Run `anthropics/claude-code-action@v1`
   - Ask Claude to read the generated dossier as the evidence base for the review
   - Create or update one dated issue per watched repo and report date in this repository
   - Replace the issue body with a concise current-status summary
   - Append a dated comment per run with the fuller qualitative review
   - Use full Markdown links for watched-repo issues and PRs because the published report lives in a separate repository

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
 selector metadata
        |
        +--> report_date
        +--> exact issue title
        |
        v
 Claude Code Action
        |
        v
 dated issue update in this repo
```

## GitHub Actions Design

### Scheduled Workflow

[`.github/workflows/scan-watched-repos.yml`](/Users/cjm/repos/agent-watcher/.github/workflows/scan-watched-repos.yml)

- runs on cron and manual dispatch
- selects configured targets, optionally filtered by workflow input
- includes only repos due for the current schedule slot on scheduled runs
- generates one context artifact per target
- asks Claude to create or update the corresponding dated report issue in this repo
- uploads the generated context artifacts

### Weekly Setup Review Workflow

[`.github/workflows/review-agentic-setup.yml`](/Users/cjm/repos/agent-watcher/.github/workflows/review-agentic-setup.yml)

- runs weekly and on manual dispatch
- collects setup context across all targets with `include_in_setup_review=true`
- inspects repo-level instruction files, agent-related workflows, and detected skill/subagent directories
- asks Claude to compare repos against one another and recommend best-practice improvements
- creates or updates one dated cross-repo review issue in this repo with the `agent-watcher` label

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
- `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`
  - one of these must be present for the Claude Code Action
- `GITHUB_TOKEN`
  - write access on this repo issues
  - used by Claude for issue creation, reopening, body updates, and appended comments

This keeps publishing scoped to this repo while allowing watched repos to remain read-only.

## Matrix Model

The workflow uses a dynamic matrix, not one workflow per ontology.

The selector emits rows like:

```json
{
  "repo": "geneontology/go-ontology",
  "display_name": "GO",
  "short_name": "go-ontology",
  "slug": "geneontology__go-ontology",
  "report_date": "2026-03-31",
  "issue_title": "go-ontology report for 2026-03-31"
}
```

GitHub Actions then creates one job per row. Adding a new ontology means adding a new config row, not a new workflow.

## Current Limitations

- inline PR review comments are not fetched yet
- the qualitative review depends on the prompt and the available context
- detection relies on configured agent names and textual markers
- there is no persistent data store besides dated issue history and workflow artifacts

## Natural Next Steps

- add repo-specific match rules where agents have distinct identities
- add inline review comment collection for PR-heavy repos
- persist run summaries as JSON history for trend charts
- improve prompt guidance for repo-specific ontology expectations and opportunity triage
- tag noteworthy findings, such as repeated duplicate agent PRs or human rework after merge
