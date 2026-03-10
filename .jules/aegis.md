
## 2024-03-10 - Constant-time comparison for auth token validation
**Vulnerability:** String inequality comparison was used to validate bearer tokens, potentially exposing the token character by character via a timing attack.
**Learning:** Python's string equality operators short-circuit, which is dangerous for cryptographic or authentication token comparisons.
**Prevention:** Always use `secrets.compare_digest()` with correctly encoded bytes for validating tokens, secrets, or passwords to ensure constant-time comparison.
