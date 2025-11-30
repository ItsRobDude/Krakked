import io
import json
import logging

from kraken_bot.logging_config import DEFAULT_ENV, JsonFormatter, structured_log_extra


def _build_logger(stream: io.StringIO) -> logging.Logger:
    logger = logging.getLogger("kraken_bot.test.logging")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    return logger


def test_structured_log_extra_adds_common_identifiers():
    extra = structured_log_extra(
        event="order_submitted",
        plan_id="plan-123",
        strategy_id="strategy-abc",
        pair="XBTUSD",
        order_id="local-1",
        kraken_order_id="kraken-1",
        local_order_id="local-1",
        custom_field="value",
    )

    assert extra["event"] == "order_submitted"
    assert extra["env"] == DEFAULT_ENV
    assert extra["plan_id"] == "plan-123"
    assert extra["strategy_id"] == "strategy-abc"
    assert extra["pair"] == "XBTUSD"
    assert extra["order_id"] == "local-1"
    assert extra["kraken_order_id"] == "kraken-1"
    assert extra["local_order_id"] == "local-1"
    assert extra["custom_field"] == "value"

    minimal_extra = structured_log_extra()
    assert "pair" not in minimal_extra
    assert "order_id" not in minimal_extra
    assert "kraken_order_id" not in minimal_extra
    assert "local_order_id" not in minimal_extra


def test_json_formatter_preserves_extra_fields():
    stream = io.StringIO()
    logger = _build_logger(stream)

    logger.info(
        "log message",
        extra=structured_log_extra(
            event="order_submitted",
            plan_id="plan-123",
            strategy_id="strategy-abc",
            pair="XBTUSD",
            order_id="local-1",
            kraken_order_id="kraken-1",
            local_order_id="local-1",
            custom_field="value",
        ),
    )

    payload = json.loads(stream.getvalue())

    assert payload["event"] == "order_submitted"
    assert payload["plan_id"] == "plan-123"
    assert payload["strategy_id"] == "strategy-abc"
    assert payload["pair"] == "XBTUSD"
    assert payload["order_id"] == "local-1"
    assert payload["kraken_order_id"] == "kraken-1"
    assert payload["local_order_id"] == "local-1"
    assert payload["custom_field"] == "value"
    assert payload["env"] == DEFAULT_ENV
    assert payload["message"] == "log message"
