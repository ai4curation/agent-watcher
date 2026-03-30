# Agent Watcher Design

## Goal

Run a scheduled watcher in GitHub Actions that inspects selected deployment repos, identifies recent issues and PRs involving agents, produces a practical qualitative assessment of how the agents are doing, and publishes the findings back into this repository.

## Non-Goals For The First Pass

- full conversational transcript analysis
- inline review comment harvesting
- deeply semantic or model-based grading
- automatic remediation in watched repos
- cross-run trend analytics beyond rolling issue history

## Architecture

The first-pass architecture has four phases:

1. `Select`
   - Load watched repos from [`config/targets.json`](/Users/cjm/repos/agent-watcher/config/targets.json)
   - Merge defaults with repo-specific overrides
   - Allow workflow or local CLI overrides for lookback window, max items, and single-target runs

2. `Collect`
   - Use the GitHub REST API to list recently updated issues and PRs per watched repo
   - Pull issue comments for all candidate items
   - Pull PR reviews and PR metadata for PRs
   - Keep the collector simple and token-friendly so it works in Actions without extra infrastructure

3. `Assess`
   - Mark an item as agent-related when any of the following are true:
     - an author or commenter login matches configured agent substrings
     - a body, comment, or review contains agent markers such as `@dragon-ai-agent` or `Claude Code`
   - Compute a transparent heuristic summary:
     - closed or merged agent-touched work is a positive signal
     - open items with human follow-up after the latest agent action are a negative signal
     - mentions without agent-authored artifacts indicate invocation without clear follow-through
   - Collapse those signals into a small rubric:
     - `strong`
     - `mixed`
     - `needs_attention`
     - `no_signal`
     - `error`

4. `Publish`
   - Maintain one rolling issue per watched repo in this repository
   - Update the issue body with the latest top-line assessment
   - Append a dated comment per run with the detailed summary
   - Write JSON and Markdown artifacts for later inspection and debugging

## Why Rule-Based First

The user goal is operational visibility, not a research-grade evaluator. A rule-based pass is the right initial shape because:

- it is understandable by maintainers
- it is deterministic in CI
- it avoids model and prompt drift as the repo is being bootstrapped
- it creates stable structured inputs for a later LLM evaluation layer

An LLM evaluator can be added later after the collector and publisher are trusted. The natural insertion point is after structured collection and before Markdown rendering.

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
 qualitative repo report
        |
        +--> build/reports/*.json
        +--> build/reports/*.md
        |
        v
 rolling issue upsert in this repo
```

## GitHub Actions Design

### Scheduled Workflow

[`.github/workflows/scan-watched-repos.yml`](/Users/cjm/repos/agent-watcher/.github/workflows/scan-watched-repos.yml)

- runs on cron and manual dispatch
- installs Python and the local package
- runs the watcher against all configured targets or one selected target
- uploads report artifacts
- publishes or appends watcher issues in this repo

### Validation Workflow

[`.github/workflows/validate.yml`](/Users/cjm/repos/agent-watcher/.github/workflows/validate.yml)

- runs on push and pull request
- compiles the Python source
- performs a dry-run against a small public target slice
- uploads the preview artifacts for inspection

## Permissions Model

Two token roles are enough for the first pass:

- `WATCHER_SOURCE_TOKEN`
  - read access on watched repos
  - used for listing issues, comments, PRs, and reviews
- `GITHUB_TOKEN`
  - write access on this repo issues
  - used for issue creation, reopening, body updates, and appended comments

This keeps publishing scoped to this repo while allowing watched repos to remain read-only.

## Current Limitations

- inline PR review comments are not fetched yet
- the heuristic does not understand ontology-specific correctness
- detection relies on configured agent names and textual markers
- there is no persistent data store besides rolling issue history and workflow artifacts

## Natural Next Steps

- add repo-specific match rules where agents have distinct identities
- add inline review comment collection for PR-heavy repos
- persist run summaries as JSON history for trend charts
- layer an optional LLM judgment step on top of the structured signals
- tag noteworthy findings, such as repeated duplicate agent PRs or human rework after merge
