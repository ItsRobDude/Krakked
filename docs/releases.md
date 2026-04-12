# Release Flow

Krakked now has a tag-driven release workflow in [`.github/workflows/release.yml`](../.github/workflows/release.yml).

## What The Release Workflow Does

On every `v*` git tag push, the workflow:

- builds the Python wheel and source distribution with Poetry
- uploads those build artifacts to the GitHub workflow run
- creates a GitHub Release and attaches the built artifacts
- builds a Docker image from the repo root
- publishes the image to `ghcr.io/<owner>/<repo>` with version, `sha-*`, and `latest` tags

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

## Image Naming

The workflow publishes to:

- `ghcr.io/<owner>/<repo>:vX.Y.Z`
- `ghcr.io/<owner>/<repo>:sha-<gitsha>`
- `ghcr.io/<owner>/<repo>:latest`

For operators, pin `KRAKKED_IMAGE_TAG` to the explicit version tag in `.env` rather than `latest`.

## Human Follow-Up

The automation handles build/publish mechanics, but a human should still:

- review release notes before tagging
- verify Docker startup against a real daemon
- decide when a release is commercially ready
