# Deployment Preflight Checklist

Use this checklist before publishing a release candidate image, deploying to
Unraid, starting a deployment proof, or starting a decision/paper soak. The goal
is simple: never start an operator proof against the wrong commit, image, DB, or
evidence artifact.

This checklist is a gate. If any hard stop fails, do not start the proof or
soak. Fix the mismatch first.

## 1. Choose The Target

Record the exact target before tagging or deploying:

- PR or branch:
- target commit SHA:
- intended image tag:
- expected runtime source: `image` for release/proof/soak runs
- proof or soak name:
- operator profile name:
- portfolio DB path:

Before continuing:

```bash
git status --short --branch
git log -1 --oneline
git branch --show-current
```

For PR-backed work, confirm the PR has merged or deliberately choose the
unmerged branch as the target. Do not tag or deploy from "whatever is current"
without writing down the SHA.

## 2. Verify Gates Before Tagging

Run the verification appropriate to the change. For code, safety, portfolio,
strategy, UI, or API-shape changes, use the repo's normal gates:

```bash
poetry run pytest
poetry run pyright src ui
poetry run pre-commit run --all-files
cd ui && npm run build
git diff --check
```

For docs-only changes, at minimum run:

```bash
git diff --check
```

If GitHub CI is the release gate, wait for it to finish on the commit that will
be tagged. A later green commit does not prove an older tag.

## 3. Tag And Publish The Intended Commit

Create the tag against the recorded SHA:

```bash
git tag <tag> <target-sha>
git push origin <tag>
```

Watch the tag-driven release workflow and record:

- workflow run URL:
- GHCR image:
- published image tag:
- published commit SHA:

Do not deploy until the image for the intended tag exists. Use explicit RC tags
for operator validation builds; public release tags should match
`pyproject.toml`.

## 4. Deploy The Exact Image

On the deployment host, pin the image and expected provenance before starting:

```dotenv
KRAKKED_IMAGE=ghcr.io/itsrobdude/krakked
KRAKKED_IMAGE_TAG=<tag>
KRAKKED_EXPECTED_IMAGE=ghcr.io/itsrobdude/krakked
KRAKKED_EXPECTED_IMAGE_TAG=<tag>
KRAKKED_EXPECTED_RUNTIME_SOURCE=image
KRAKKED_EXPECTED_BUILD_GIT_SHA=<target-sha>
```

Then pull and recreate:

```bash
docker compose pull
docker compose up -d --force-recreate
```

For Unraid, use the documented compose file and host URL for that install.
Docker health can be noisy on Unraid; app HTTP health is the authority for this
check.

## 5. Verify Runtime Provenance

Before any session, proof, or soak starts, verify the running app reports the
target commit and no deployment drift:

```bash
curl -fsS http://<host>:8088/api/health
curl -fsS http://<host>:8088/api/system/health
```

Required values:

- `build_git_sha` equals the recorded target SHA.
- `build_git_ref` matches the intended tag or commit context.
- `runtime_source` is `image`.
- image name and image tag match the intended GHCR image and tag.
- expected image/tag/SHA/runtime fields match actual runtime fields.
- `deployment_drift_detected=false`.

Hard stop: if any of these values are missing or mismatched, do not start the
proof or soak.

## 6. Isolate Profile, DB, And Evidence

For paper validation, decision soaks, or proof runs, use a fresh dated profile
unless the run intentionally tests upgrade/restore behavior.

Record:

- active profile name:
- active profile config path:
- portfolio DB path:
- config dir:
- data dir:

Use the operator paths in `/api/system/health` or the UI advanced/operator area
as the source of truth for the active portfolio DB path. Do not assume the DB is
`/krakked/state/portfolio.db` when a profile-scoped paper session is active.

If a schema version changed, migrate and check the actual active DB path before
starting:

```bash
docker compose run -T --rm krakked migrate --db-path <active-db-path> < /dev/null
docker compose run -T --rm krakked db-schema-version --db-path <active-db-path> < /dev/null
docker compose run -T --rm krakked db-check --db-path <active-db-path> < /dev/null
```

## 7. Verify Replay Or Scope It Out

For decision-loop soaks, the replay panel and latest replay report must describe
the same strategy/profile/window that the soak is using.

Before starting:

- publish or copy the correct replay report into the runtime's latest replay
  path; or
- explicitly mark the replay panel as untrusted/out of scope for this soak.

Hard stop: do not let a stale replay report imply that the current image,
profile, or strategy slice was proved.

## 8. Start Monitoring Before The Session

Start the monitor first, then start the session.

Record:

- host URL and port:
- monitor command:
- monitor output path:
- monitor PID or process evidence:
- session start timestamp:
- target minimum duration:
- required boundary condition, such as crossing a 4h bar close:
- planned stop condition:

For a decision-useful paper soak, require at least four hours and at least one
4h bar boundary unless the written run plan says otherwise.

## 9. Post-Run Evidence

After stopping, collect evidence before changing the runtime:

- final `/api/health` and `/api/system/health` payloads;
- monitor JSONL/log path;
- active profile config;
- active portfolio DB path and backup/export path;
- replay report path if used;
- counts for decisions, actions, orders, fills, trades, blocks, and degraded
  samples;
- any deployment drift, stale data, account-truth, or profile mismatch events.

The dated report must state the exact image tag, build SHA, profile, DB path,
monitor path, official start/stop times, and proof scope boundary.

## Hard Stops

Stop before launch if any of these are true:

- target commit was not recorded;
- tag does not point at the intended commit;
- GHCR image for the intended tag is not published;
- `/api/health.build_git_sha` differs from the target SHA;
- expected image/tag/SHA/runtime values do not match actual runtime values;
- `deployment_drift_detected=true`;
- active profile or DB path is not the intended fresh run target;
- required schema migration/check was skipped;
- monitor is not running before session start;
- decision-soak replay evidence is stale or unscoped;
- setup is locked or health cannot be read;
- live order gates are unexpectedly open for a paper/proof run.
