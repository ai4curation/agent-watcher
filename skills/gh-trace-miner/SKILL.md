---
name: gh-trace-miner
description: Retrieve agent execution traces for watched ontology repositories. Use when Codex needs to inspect, download, sample, or summarize Claude Code, Goose, Copilot coding agent, or other AI-agent traces from GitHub Actions artifacts, retained Actions logs, or Copilot agent-task session logs.
---

# GH Trace Miner

## Workflow

Use `scripts/mine_traces.py` for repeatable retrieval. Prefer writing outputs under `build/trace-samples/` because traces may contain full model messages, tool output, repository context, or secrets echoed by tools.

```bash
python skills/gh-trace-miner/scripts/mine_traces.py \
  --config config/targets.json \
  --all-targets \
  --out-dir build/trace-samples \
  --limit-runs 40 \
  --max-samples 2
```

For one known run:

```bash
python skills/gh-trace-miner/scripts/mine_traces.py \
  --repo geneontology/go-ontology \
  --run-id 25568398161 \
  --slug go-ontology \
  --out-dir build/trace-samples
```

For traces associated with PRs authored by `dragon-ai-agent`:

```bash
python skills/gh-trace-miner/scripts/mine_dragon_pr_traces.py \
  --config config/targets.json \
  --all-targets \
  --out-dir build/dragon-pr-traces
```

For GitHub Copilot coding-agent PRs:

```bash
python skills/gh-trace-miner/scripts/mine_copilot_traces.py \
  --config config/targets.json \
  --target obophenotype/cell-ontology \
  --target EBISPOT/efo \
  --out-dir build/copilot-traces
```

For a full-history capture attempt, separate the output by retrieval surface:

```bash
# Retained artifact traces, efficient for GO.
python skills/gh-trace-miner/scripts/mine_traces.py \
  --repo geneontology/go-ontology \
  --slug go-ontology \
  --from-artifacts \
  --max-samples 0 \
  --out-dir build/full-agent-history/actions

# Log-only Actions workflows. Use --skip-artifact-check when the workflow is
# known not to upload trace artifacts.
python skills/gh-trace-miner/scripts/mine_traces.py \
  --repo obophenotype/cell-ontology \
  --slug cell-ontology \
  --workflow ai-agent.yml \
  --all-runs \
  --skip-artifact-check \
  --max-samples 0 \
  --out-dir build/full-agent-history/actions

python skills/gh-trace-miner/scripts/mine_traces.py \
  --repo obophenotype/uberon \
  --slug uberon \
  --workflow ai-agent.yml \
  --workflow claude-code-review.yml \
  --all-runs \
  --skip-artifact-check \
  --max-samples 0 \
  --out-dir build/full-agent-history/actions

python skills/gh-trace-miner/scripts/mine_traces.py \
  --repo monarch-initiative/mondo \
  --slug mondo \
  --workflow ai-agent.yml \
  --all-runs \
  --skip-artifact-check \
  --max-samples 0 \
  --out-dir build/full-agent-history/actions

python skills/gh-trace-miner/scripts/mine_copilot_traces.py \
  --config config/targets.json \
  --all-targets \
  --limit-prs 1000 \
  --out-dir build/full-agent-history/copilot

python skills/gh-trace-miner/scripts/mine_dragon_pr_traces.py \
  --config config/targets.json \
  --all-targets \
  --out-dir build/full-agent-history/dragon-prs
```

To prepare a compact public copy from the ignored full-history cache:

```bash
python scripts/prepare_public_traces.py \
  --source build/full-agent-history \
  --dest public-traces \
  --clean
```

For incremental refreshes, mine recent traces into a temporary full-history-shaped directory and merge them into the committed public bundle:

```bash
python scripts/prepare_public_traces.py \
  --source build/trace-refresh/full-agent-history \
  --dest public-traces \
  --config config/trace_targets.json \
  --merge-existing
```

`.github/workflows/refresh-public-traces.yml` runs this path on a schedule and opens or updates a PR rather than pushing trace updates directly to `main`. The scheduled path refreshes Actions and Dragon PR traces. Copilot agent-task logs are opt-in via manual workflow dispatch.

`public-traces/` is organized by GitHub repository path under `traces/OWNER/REPO/`, with per-repository indexes under `repos/OWNER/REPO/`. Exact duplicate trace payloads found through both Actions and Dragon-PR mining are stored once and represented with `duplicate_of` pointers in `manifest.json`. Run a final secret scan before publishing, even for traces retrieved from public GitHub surfaces.
By default, materialized trace payloads at or above 1 MiB are stored as `.gz`; use `--gzip-threshold-bytes 0` to disable that.

## Retrieval Paths

1. **Artifact path**: check each run for artifacts named like `claude-response-*` or `claude-execution-*`. Download them with `gh run download`. This is the cleanest path when workflows upload `/home/runner/work/_temp/claude-execution-output.json`.
   - For high-volume repos, prefer `mine_traces.py --from-artifacts` to enumerate retained trace artifacts repo-wide instead of checking every workflow run.
2. **Log path**: if no trace artifact exists, call the raw Actions logs endpoint:

```bash
gh api repos/OWNER/REPO/actions/runs/RUN_ID/logs > logs.zip
```

Unzip and parse the `respond-to-mention` job log. Claude Code workflows with `show_full_output: true` print JSON records containing `session_id`, `tool_use_result`, `message`, `usage`, and `total_cost_usd`.
3. **Copilot agent-task path**: Copilot setup workflows only bootstrap the environment; their Actions logs are not the coding-agent trace. Use GitHub CLI 2.80.0 or newer and retrieve session logs with:

```bash
gh agent-task view --repo OWNER/REPO SESSION_UUID --json id,name,state,pullRequestNumber,pullRequestUrl,repository,user
gh agent-task view --repo OWNER/REPO SESSION_UUID --log > agent.log
```

Find session UUIDs from Copilot commit trailers such as `Agent-Logs-Url: https://github.com/OWNER/REPO/sessions/<uuid>`, PR prompt links containing `session_id=<uuid>`, or user-visible sessions from `gh agent-task list`. Direct `curl` to `/sessions/<uuid>` can return 404 even when `gh agent-task view` succeeds.

## Expected Outputs

The miner creates one subfolder per ontology slug and one subfolder per sampled run:

```text
build/trace-samples/
  go-ontology/
    index.json
    25568398161/
      run.json
      summary.json
      artifact/
      logs/
      log-trace.jsonl
```

Read `index.json` first. `summary.json` records whether traces came from artifacts, logs, or both. `log-trace.jsonl` and downloaded artifact JSON are sensitive raw trace material; do not move them out of ignored folders unless they have been intentionally sanitized.

For PR-oriented runs, read each ontology `index.json`. PRs can be linked to traces through explicit `actions/runs/<id>` URLs in PR text/comments or through branch names ending in `runNNNN`, where `NNNN` is the workflow run number. Older PRs often no longer have retained logs or artifacts.

For Copilot PRs, `mine_copilot_traces.py` writes:

```text
build/copilot-traces/
  efo/
    index.json
    pr-2663/
      pr.json
      summary.json
      session-222f15b0-d475-49a0-979c-a5bb8041e60d/
        session.json
        agent.log
```

Copilot PR bodies can mention sessions for other PRs. Verify the `pullRequestNumber` returned by `gh agent-task view` before treating a session as the trace for the current PR.

## Practical Notes

- `gh run view --log` can return an empty stream even when the raw logs zip is available.
- Check-only runs usually have no useful trace because `respond-to-mention` is skipped.
- Old Actions logs may return `HTTP 410 Gone`; record those runs as known but not retrievable.
- Copilot setup workflows are not agent execution traces unless they explicitly upload or print trace-like output.
- `gh agent-task view <PR>` may require an interactive selector; non-interactive mining should use explicit session UUIDs.
- `gh agent-task list` is useful but scoped to sessions visible to the authenticated user, so combine it with session IDs extracted from PR commit messages.
- If a repo has no samples, keep its ontology subfolder with `index.json`; that documents that retrieval was attempted.
