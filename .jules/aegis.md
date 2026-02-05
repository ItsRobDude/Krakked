# Aegis Journal

## 2026-02-05 - Path Traversal in Config Loader
**Vulnerability:** A helper function `dump_runtime_overrides` constructed file paths using unsanitized input (`profile_name`), allowing directory traversal if called with a malicious profile name.
**Learning:** The repo relies on callers to sanitize inputs before passing them to internal storage helpers, but this contract is not enforced at the helper level, creating fragile security.
**Prevention:** Enforce input sanitization (using `kraken_bot.utils.io.sanitize_filename`) within any function that writes to the filesystem, regardless of its visibility.
