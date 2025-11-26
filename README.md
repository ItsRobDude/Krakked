# Krakked
Scripts n things

Kraken Connection Module – Phase 1 Design Contract
1. Purpose & Scope

The Kraken Connection Module is responsible for:

Managing API credentials securely (load, store, validate).

Providing a clean interface for Kraken REST API (public + private).

Handling region profile and capability flags (e.g., US_CA, supports_margin = false).

Enforcing basic safety rules (no secret leakage, no unauthorized features).

It is not responsible for:

Strategy logic, risk logic, or order sizing.

User interface.

Long‑term data storage (beyond its own config/secrets).

This module should be usable as a standalone library by later phases.

2. Configuration & Secrets Design

Config directory

Use an OS‑specific configuration directory:

Linux: ~/.config/kraken_bot/

macOS: ~/Library/Application Support/kraken_bot/

Windows: %APPDATA%\kraken_bot\

All module‑owned files live under this directory.

Files

Secrets file (encrypted)

Path: <config_dir>/secrets.enc

Contents: API key + API secret (and any encryption metadata like salt/nonce).

Must be encrypted at rest using a master password derived key.

Module is responsible for setting the most restrictive file permissions the OS allows (user‑only if possible).

Non‑secret config file

Path: <config_dir>/config.yaml (or .toml / .json, but one format only).

Contents include:

region (e.g., "US_CA").

capabilities flags (e.g., supports_margin, supports_futures, supports_staking).

Any other non‑sensitive behavior toggles relevant to the connection module (e.g., base URL overrides for testing).

Secret vs non‑secret

Secrets file: only API key, API secret, and encryption metadata.

Config file: region, capabilities, and other non‑sensitive settings.

3. Credential Loading & Precedence

The module exposes a clear concept of “credentials” (key + secret), and loads them with this precedence:

Environment variables

If both env vars for key and secret are present, they take precedence over everything else.

If only one is present, treat as incomplete → do not use.

Encrypted secrets file

If env vars are not usable, and the secrets file exists:

Prompt for master password.

Decrypt the file.

If decryption succeeds, use these credentials.

If decryption fails, surface a clear error (“Wrong password or corrupted secrets file”) and do not produce fake credentials.

No credentials available

If neither env vars nor a valid secrets file is available:

Signal “no credentials present” in a defined way that the calling code can detect (e.g., a specific exception or return status).

This can be used to trigger a first‑time setup flow.

4. First‑Time Setup & Credential Validation

On first run (or when no credentials are available), the calling code can invoke an interactive setup that uses the module’s APIs.

Required behavior for setup:

User enters API key and API secret.

The module performs a validation call to a private Kraken endpoint (e.g., a read‑only balance endpoint).

If the API call shows an authentication problem:

Example: invalid key, malformed signature, permissions missing.

The module must:

Return an error state clearly indicating an auth problem.

Not save the credentials.

If the API call fails due to a network or service problem:

Example: timeout, DNS failure, Kraken downtime.

The module must:

Distinguish this from auth failure (i.e., not label it “invalid key”).

Allow the caller to decide:

Retry validation, or

Save as “unvalidated” if desired (flag exposed in the response).

If validation succeeds:

The module encrypts and saves the credentials to secrets.enc.

It records enough metadata to know that this credential set has been validated at least once.

Key requirement:
The module must never silently persist unvalidated credentials after an apparent auth failure. Auth errors must block saving unless the caller explicitly overrides.

5. Region & Capability Profile

The module is responsible for exposing a region profile and basic capabilities.

Region and capabilities are stored in the non‑secret config file.

Minimum required fields:

region: string; e.g., "US_CA".

capabilities:

supports_margin: boolean.

supports_futures: boolean.

supports_staking: boolean.

The module provides a way to read this profile as a structured object.

The profile is read‑only from the module’s perspective in Phase 1:

It can assume the file is present and/or provide sensible defaults if missing.

Writing/updating the region file can be part of a later phase, but the read path must be stable.

6. REST API Interface & Error Handling

The module abstracts Kraken REST into a small, consistent surface:

A public call method (e.g., “perform a public GET with method and params”).

A private call method (e.g., “perform a signed POST with method and data”).

Signature generation

The module generates Kraken’s required API‑Sign header correctly, based on:

URL path.

Nonce.

Request body data.

This is part of what will be unit tested.

Error handling

If Kraken returns an error list, the module:

Interprets common categories (auth, rate limit, general).

Raises/returns structured errors that clearly differentiate:

Auth issues.

Rate‑limit or throttling issues.

Service/other errors.

Under no circumstances should any API key or secret appear in:

Exception messages.

Logs produced by this module.

7. Testing Expectations

A pytest test suite is part of the deliverable for Phase 1.

Required test coverage:

Credential loading

Env vars present: env credentials are used.

Only secrets file present: decrypted credentials are used.

Both present: env wins.

Invalid/missing env vars: handled cleanly.

Corrupted secrets file or wrong master password: clean, explicit failure.

Encryption/decryption

Given known test credentials and a test password:

Encrypt → decrypt results in identical values.

Wrong password does not silently produce wrong credentials.

API signature generation

Golden test:

Known input (URL path, nonce, data) → expected exact signature string.

Edge cases:

Empty payload case.

Parameter ordering is handled consistently.

Logging / secrecy

Tests verifying that logging/exception messages from this module do not include the raw API key or secret.

Config reading

Config file missing → defaults are applied and well‑defined.

Config file present → region and capabilities are read correctly.

Integration tests that actually call Kraken are optional in this phase and may use mocks/stubs instead. The key is correctness of local behavior.

8. Minimal Project Structure

A simple, extensible structure is enough for Phase 1:

pyproject.toml

Declares dependencies and test tooling (pytest, crypto lib, HTTP client, etc.).

src/kraken_bot/

connection.py (or equivalent) – main REST + auth interface.

secrets.py – encryption, decryption, secrets loading logic.

config.py – config directory resolution and region/capabilities loading.

tests/

test_secrets.py

test_config.py

test_connection_signing_and_loading.py

Naming can vary slightly, but the responsibilities must stay clearly separated.

9. Acceptance Checklist for Phase 1

The module is “done” for Phase 1 if all of the following are true:

Given valid env vars, the module can:

Load credentials.

Generate a valid signature for a known test case.

Given valid credentials and no env vars, the module can:

Perform an interactive setup flow:

Validate credentials against a private endpoint.

Encrypt and save them.

Load them on next run via secrets file + master password.

Given invalid credentials, the module:

Detects auth errors during validation.

Does not save them by default.

Region and capabilities are:

Readable from a non‑secret config file.

Available as a structured profile to callers.

Secrets are:

Never written in plaintext to disk.

Never logged or included in exception messages.

The pytest suite:

Runs successfully.

Covers the main behaviors listed in the Testing section.

If all of that is true, you have the “secure, tested Kraken connection module” that the later phases can safely build on.
