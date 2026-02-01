## 2024-05-24 - Python Version Mismatch **Gap:** README.md stated "Python 3.10+" but `pyproject.toml` enforces ">=3.11,<4.0". This could cause installation failures for users following the README. **Fix:** Updated README.md to specify Python 3.11+.

## 2024-05-25 - API Type Mismatch **Gap:** `PortfolioSummary.last_snapshot_ts` is typed as `string` in TypeScript but the API returns a `number` (Unix seconds). This discrepancy was undocumented. **Fix:** Added JSDoc warning about the type definition vs runtime reality.

## 2024-05-25 - Risk Control Clarity **Gap:** No documentation explained that `setKillSwitch` only halts new orders while `flattenAllPositions` is the true panic button. **Fix:** Clarified the exact scope of both functions in their TSDoc.
