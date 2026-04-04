# Agent Watcher

`agent-watcher` runs scheduled scans against selected GitHub repositories where we deploy agents, builds a neutral activity digest for the last N days or weeks, and then asks Codex to write a qualitative watcher summary in this repository.

The current shape is:

- target repos are configured in [`config/targets.json`](/Users/cjm/repos/agent-watcher/config/targets.json)
- collection uses the GitHub REST API
- the collector produces neutral context, not a score
- the scheduled workflow uses `codex exec --profile yolo`
- Codex creates or updates one dated multi-repo watcher issue per report date

## Initial Scope

- Watch selected repos such as `geneontology/go-ontology`, `monarch-initiative/dismech`, `ai4curation/ai-gene-review`, `obophenotype/cell-ontology`, and `obophenotype/uberon`
- Inspect recently updated issues and PRs in a lookback window
- Detect agent involvement from author logins, comments, PR reviews, and common agent markers in text
- Generate a context file per watched repo with lightweight counts and event timelines
- Select only repos due for the current schedule slot based on config
- Ask Codex to summarize what appears to be working, not working, and where the friction is
- Publish findings back into this repo as dated issues such as `Dragon AI watcher summary for 2026-04-04`

## Repo Layout

- [`config/targets.json`](/Users/cjm/repos/agent-watcher/config/targets.json): watched repos and match heuristics
- [`docs/design.md`](/Users/cjm/repos/agent-watcher/docs/design.md): watcher and Codex review architecture
- [`scripts/run_watch.py`](/Users/cjm/repos/agent-watcher/scripts/run_watch.py): local and workflow entrypoint
- [`src/agent_watcher`](/Users/cjm/repos/agent-watcher/src/agent_watcher): scanner and Markdown context generation code
- [`.github/workflows/scan-watched-repos.yml`](/Users/cjm/repos/agent-watcher/.github/workflows/scan-watched-repos.yml): scheduled and manual Codex review workflow
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

Run against all configured targets and write context artifacts:

```bash
export WATCHER_SOURCE_TOKEN=ghp_xxx
python3 scripts/run_watch.py \
  --config config/targets.json \
  --output-dir build/context
```

Dry-run a selected batch and emit a multi-repo `summary.md`:

```bash
export WATCHER_SOURCE_TOKEN=ghp_xxx
python3 scripts/run_watch.py \
  --config config/targets.json \
  --target monarch-initiative/dismech \
  --target ai4curation/ai-gene-review \
  --target geneontology/go-ontology \
  --output-dir build/context \
  --dry-run
```

## Required Secrets

The scheduled workflow expects:

- `WATCHER_SOURCE_TOKEN`: token with read access to watched repositories
- one of `OPENAI_API_KEY` or `CODEX_API_KEY`: used by `codex exec`
- `GITHUB_TOKEN`: the default workflow token is used by Codex to create or append issues in this repo

`WATCHER_SOURCE_TOKEN` can be a fine-grained PAT or GitHub App token. The collector only needs read access to issues, pull requests, metadata, comments, and reviews on watched repos.

## Publishing Model

The workflow maintains one issue per report date:

- title: `Dragon AI watcher summary for {report_date}`
- reruns on the same date update that issue
- issue body: concise cross-repo current status written by Codex for that report date
- comments: one appended qualitative review per run

The collector still emits per-repo dossiers and a local `summary.md`. Codex uses those local artifacts as its evidence base for the posted summary.

## How Scheduling Works

The workflow is a single YAML file. Target selection still happens in [`config/targets.json`](/Users/cjm/repos/agent-watcher/config/targets.json), not by cloning workflows.

Each target can declare:

- `short_name`
- `cadence`: `weekly`, `daily`, or `manual`
- `preferred_weekday_utc`: `0` for Monday through `6` for Sunday
- `preferred_hour_utc`
- `issue_title_template`

The selector script [`scripts/select_targets.py`](/Users/cjm/repos/agent-watcher/scripts/select_targets.py) reads that config and builds the target list dynamically. The scheduled workflow then scans all selected repos in one batch so the existing `summary.md` can drive a multi-repo Codex review.

## Adding A Repo

Add one object to [`config/targets.json`](/Users/cjm/repos/agent-watcher/config/targets.json), for example:

```json
{
  "repo": "obophenotype/human-developmental-anatomy-ontology",
  "display_name": "EHDAA",
  "short_name": "ehdaa",
  "preferred_weekday_utc": 2
}
```

That is enough for the same workflow to pick it up. No new YAML is required.

## Manual Workflow Runs

The workflows are designed to be runnable from the Actions tab or via `gh`:

```bash
gh workflow run scan-watched-repos.yml \
  -f target_repo=geneontology/go-ontology \
  -f lookback_days=14 \
  -f max_items=25

gh workflow run validate.yml \
  -f target_repo=geneontology/go-ontology
```

## Notes

- The collector deliberately stays simple and deterministic; Codex is used for the actual qualitative judgment.
- The context artifacts in `build/` are for debugging and for giving Codex enough evidence to write a useful review.
