from kraken_bot.strategy.risk_profiles import RISK_PROFILES, profile_to_definition


def test_profile_to_definition_returns_expected_profile():
    aggressive = profile_to_definition("aggressive")

    assert aggressive == RISK_PROFILES["aggressive"]
    assert aggressive.max_per_strategy_pct == 20.0
    assert aggressive.risk_per_trade_pct == 1.0


def test_profile_to_definition_defaults_to_balanced():
    fallback = profile_to_definition("unknown")

    assert fallback == RISK_PROFILES["balanced"]
