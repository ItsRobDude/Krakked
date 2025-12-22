## 2024-05-24 - Python Version Mismatch **Gap:** README.md stated "Python 3.10+" but `pyproject.toml` enforces ">=3.11,<4.0". This could cause installation failures for users following the README. **Fix:** Updated README.md to specify Python 3.11+.

## 2025-12-22 - Incorrect CLI Commands in README **Gap:** README referenced deprecated `migrate-db` and non-existent `schema-version` commands. **Fix:** Updated README to use canonical `migrate` and `db-schema-version`, and added missing DB tools (`db-backup`, `db-info`).
