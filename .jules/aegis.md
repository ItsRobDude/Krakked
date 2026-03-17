
## 2024-05-24 - Timing attack vulnerability in token validation
**Vulnerability:** Timing attack possible during token validation because regular string comparison was used.
**Learning:** Regular string comparison exits early when a mismatch is found, allowing an attacker to deduce the expected token character-by-character based on response times.
**Prevention:** Always use `secrets.compare_digest()` (with appropriate byte encoding to handle non-ASCII characters and prevent TypeErrors) for comparing security-sensitive strings like tokens, hashes, and passwords.
