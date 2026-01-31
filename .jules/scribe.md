## 2024-05-24 - Python Version Mismatch **Gap:** README.md stated "Python 3.10+" but `pyproject.toml` enforces ">=3.11,<4.0". This could cause installation failures for users following the README. **Fix:** Updated README.md to specify Python 3.11+.

## 2024-05-24 - Missing UI Build Instructions **Gap:** The README.md mentions the UI but fails to explain that users must manually build the frontend (`cd ui && npm ci && npm run build`) for the Python backend to serve it. **Fix:** Added a "UI Development" section to README.md covering setup, build, and dev workflows.
