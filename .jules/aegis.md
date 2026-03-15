## 2025-05-24 - Timing attack vulnerability in custom auth middleware
**Vulnerability:** The API `AuthMiddleware` used a standard string equality operator `==` / `!=` to validate the `Authorization` header, making it vulnerable to a timing attack where an attacker could deduce a valid token by observing response times.
**Learning:** `AuthMiddleware` implementations built using `BaseHTTPMiddleware` bypass standard framework validation features like FastAPI's `HTTPBearer` that might otherwise handle this.
**Prevention:** Always use `secrets.compare_digest` with explicit `utf-8` bytes encoding to validate auth tokens, secrets, passwords, or signatures when verifying them manually.
