# Aegis Journal

## 2025-05-22 - Timing Attack in UI Auth
**Vulnerability:** The custom `AuthMiddleware` used unsafe string comparison for bearer tokens, allowing potential timing side-channels.
**Learning:** The UI API implements bespoke authentication logic in `src/kraken_bot/ui/api.py` rather than using a standard auth library, increasing the risk of implementation flaws like non-constant time comparisons.
**Prevention:** Audit custom middleware in `ui/api.py` and `ui/middleware.py` for cryptographic primitives; prefer `secrets.compare_digest` for all token validations.
