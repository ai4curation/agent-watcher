# Agent Watcher

`agent-watcher` runs scheduled scans against selected GitHub repositories where we deploy agents, looks for recent issues and pull requests with agent involvement, applies a simple qualitative rubric, and opens or appends rolling watcher issues in this repository.

The initial scaffold is deliberately conservative:

- target repos are configured in [`config/targets.json`](/Users/cjm/repos/agent-watcher/config/targets.json)
- collection uses the GitHub REST API
- evaluation is rule-based and transparent rather than LLM-dependent
- publishing appends comments to one rolling issue per watched repo in this repo

## Initial Scope

- Watch selected repos such as `geneontology/go-ontology`, `obophenotype/cell-ontology`, `obophenotype/uberon`, `monarch-initiative/mondo`, and `EBISPOT/efo`
- Inspect recently updated issues and PRs in a lookback window
- Detect agent involvement from author logins, comments, PR reviews, and common agent markers in text
- Score each repo as `strong`, `mixed`, `needs_attention`, `no_signal`, or `error`
- Publish findings back into this repo as rolling issues titled `Watcher: owner/repo`

## Repo Layout

- [`config/targets.json`](/Users/cjm/repos/agent-watcher/config/targets.json): watched repos and match heuristics
- [`docs/design.md`](/Users/cjm/repos/agent-watcher/docs/design.md): watcher and evaluation architecture
- [`scripts/run_watch.py`](/Users/cjm/repos/agent-watcher/scripts/run_watch.py): local and workflow entrypoint
- [`src/agent_watcher`](/Users/cjm/repos/agent-watcher/src/agent_watcher): scanner, scoring, markdown, and issue publishing code
- [`.github/workflows/scan-watched-repos.yml`](/Users/cjm/repos/agent-watcher/.github/workflows/scan-watched-repos.yml): scheduled scan and publish workflow
- [`.github/workflows/validate.yml`](/Users/cjm/repos/agent-watcher/.github/workflows/validate.yml): validation and dry-run workflow

## Local Usage

Dry-run one repo:

```bash
python3 scripts/run_watch.py \
  --config config/targets.json \
  --target geneontology/go-ontology \
  --lookback-days 3 \
  --max-items 5 \
  --output-dir build/local-run \
  --dry-run
```

Run against all configured targets and publish to this repo:

```bash
export WATCHER_SOURCE_TOKEN=ghp_xxx
export WATCHER_SINK_TOKEN=ghp_xxx
python3 scripts/run_watch.py \
  --config config/targets.json \
  --output-dir build/publish \
  --publish \
  --sink-repo cmungall/agent-watcher
```

## Required Secrets

The scheduled workflow expects:

- `WATCHER_SOURCE_TOKEN`: token with read access to watched repositories
- `GITHUB_TOKEN`: the default workflow token is used to create or append issues in this repo

`WATCHER_SOURCE_TOKEN` can be a fine-grained PAT or GitHub App token. The first pass only needs read access to issues, pull requests, metadata, and comments on watched repos.

## Publishing Model

For each watched repo, the workflow keeps one rolling issue in this repo:

- title: `Watcher: owner/repo`
- issue body: stable metadata plus the latest top-line assessment
- comments: one appended summary per scheduled run

This keeps longitudinal history in one place without creating a new issue every day.

## Manual Workflow Runs

The workflows are designed to be runnable from the Actions tab or via `gh`:

```bash
gh workflow run scan-watched-repos.yml \
  -f target_repo=geneontology/go-ontology \
  -f lookback_days=3 \
  -f max_items=5

gh workflow run validate.yml \
  -f target_repo=geneontology/go-ontology
```

## Notes

- The current evaluator is heuristic. That is intentional for the first pass because it is easier to audit and less brittle in CI.
- The design doc lays out where an LLM-based qualitative judgment layer can be inserted later without replacing the collection and publishing pipeline.
