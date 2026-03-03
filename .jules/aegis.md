## 2024-03-03 - Insecure String Comparison (Timing Attack Vulnerability)
**Vulnerability:** The API `AuthMiddleware` verified bearer tokens using standard string equality (`==` / `!=`), allowing an attacker to deduce the token byte-by-byte by observing minute response time variations (timing attacks).
**Learning:** This repository uses a local `secrets` module (`src/kraken_bot/secrets.py`), which shadows the standard Python library `secrets` module.
**Prevention:** Always import the standard library `secrets` module securely (e.g., `import secrets as std_secrets`) and consistently use `std_secrets.compare_digest` for validating tokens, secrets, or MACs. Ensure inputs to `compare_digest` are always strings or bytes to avoid type errors.
