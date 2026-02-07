## 2025-02-18 - Custom Auth Middleware Timing Attack
**Vulnerability:** The custom `AuthMiddleware` in `src/kraken_bot/ui/api.py` used standard string comparison (`!=`) for bearer tokens, enabling potential timing attacks.
**Learning:** The repo implements custom authentication middleware rather than using a hardened library or framework feature, increasing the risk of subtle implementation flaws like timing leaks.
**Prevention:** Inspect all custom security controls (auth, signing, encryption) for standard cryptographic pitfalls (timing attacks, weak randomness) and prefer `secrets.compare_digest` for all token validations.
