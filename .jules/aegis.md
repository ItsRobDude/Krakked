## 2024-05-17 - [Timing Attack in AuthMiddleware]
**Vulnerability:** [AuthMiddleware uses standard string equality for token validation, allowing for a timing attack.]
**Learning:** [Using standard string equality is vulnerable to timing attacks; use secrets.compare_digest instead.]
**Prevention:** [Always use secrets.compare_digest for security-sensitive token and credential comparisons.]
