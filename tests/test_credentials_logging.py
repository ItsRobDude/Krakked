import logging

import kraken_bot.bootstrap as bootstrap
from kraken_bot.credentials import CredentialResult, CredentialStatus


def test_unvalidated_credentials_log_does_not_include_secrets(caplog):
    caplog.set_level(logging.WARNING, logger="kraken_bot.bootstrap")

    fake_key = "FAKE_API_KEY_123"
    fake_secret = "FAKE_API_SECRET_456"

    result = CredentialResult(
        api_key=fake_key,
        api_secret=fake_secret,
        status=CredentialStatus.LOADED,
        source="secrets_file",
        validated=False,
        can_force_save=True,
        validation_error="service temporarily unavailable",
    )

    bootstrap._validate_credentials(result)

    assert caplog.records
    for record in caplog.records:
        msg = record.getMessage()
        assert fake_key not in msg
        assert fake_secret not in msg
        for value in record.__dict__.values():
            if isinstance(value, str):
                assert fake_key not in value
                assert fake_secret not in value
