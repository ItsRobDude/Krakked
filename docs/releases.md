# Release Flow

Krakked now has a tag-driven release workflow in [`.github/workflows/release.yml`](../.github/workflows/release.yml).

## What The Release Workflow Does

On every `v*` git tag push, the workflow:

- builds the Python wheel and source distribution with Poetry
- uploads those build artifacts to the GitHub workflow run
- creates a GitHub Release and attaches the built artifacts
- builds a Docker image from the repo root
- publishes the image to `ghcr.io/itsrobdude/krakked` with version, `sha-*`,
  and `latest` tags
- embeds build provenance so the running app can report the git SHA/ref, image
  name/tag, runtime source, and expected deployment values through health
  endpoints

On `workflow_dispatch`, the same workflow performs a dry-run style build so we can validate release packaging without publishing.

## Recommended Versioning

Use simple semantic versions:

- `v0.1.0` for the first public Docker/self-hosted release
- patch releases for bug fixes and operational improvements
- minor releases for new user-facing features or operator workflows
- major releases only when install, config, or database compatibility changes in a breaking way

The Git tag should match the version in [`pyproject.toml`](../pyproject.toml).

## Release Checklist

1. Update the version in [`pyproject.toml`](../pyproject.toml).
2. Review [`docs/upgrades.md`](upgrades.md) and add migration notes for anything operator-visible.
3. Run the local verification suite:
   - `poetry run pytest -q`
   - `poetry run flake8 src tests`
   - `poetry run mypy src tests`
   - `poetry run pyright src ui`
   - `cd ui && npm ci && npm run lint && npm run typecheck:tests && npm run test:run && npm run build`
   - `poetry build`
4. If possible, run one real Docker smoke test:
   - local build via `docker compose -f compose.yaml -f compose.dev.yaml up --build`
   - published-image smoke test via `docker compose pull && docker compose up -d`
5. Commit the release prep.
6. Create and push the version tag:

```bash
git tag v0.1.0
git push origin v0.1.0
```

7. Confirm the GitHub Release and GHCR image both published successfully.
8. Run the Unraid image-mode proof for the new tag, using
   `--expected-build-git-sha`.
9. Before promoting an operator-facing tag, run the pinned-image
   upgrade/rollback drill against the previous trusted tag:

```bash
bash scripts/unraid_image_upgrade_rollback_drill.sh \
  --image ghcr.io/itsrobdude/krakked \
  --from-tag <previous-trusted-tag> \
  --to-tag <candidate-tag> \
  --from-sha <previous-trusted-sha> \
  --to-sha <candidate-sha> \
  --host-url http://<unraid-ip>:8088
```

The drill must report `IMAGE_UPGRADE_ROLLBACK_RESULT=PASS`, `fail=0`, and
phase summaries with `DEPLOYMENT_PROOF_RESULT=PASS`, `skip_run_once=false`, and
`skip_restore=false`.

## Image Naming

The workflow publishes to:

- `ghcr.io/itsrobdude/krakked:vX.Y.Z`
- `ghcr.io/itsrobdude/krakked:sha-<gitsha>`
- `ghcr.io/itsrobdude/krakked:latest`

For operators, pin `KRAKKED_IMAGE_TAG` to the explicit version tag in `.env` rather than `latest`.

The latest recorded pinned-image drill used `v0.1.1-rc.3` ->
`v0.1.1-rc.4` -> `v0.1.1-rc.3`, then a final image-mode proof left the host
running `v0.1.1-rc.4` with exact SHA provenance. See
[`docs/deployment-proof.md`](deployment-proof.md) for the evidence paths.

## Human Follow-Up

The automation handles build/publish mechanics, but a human should still:

- review release notes before tagging
- verify Docker startup against a real daemon
- decide when a release is commercially ready
