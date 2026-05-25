from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import pickle
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from krakked.config import StrategyConfig
from krakked.strategy.base import StrategyContext
from krakked.strategy.features import (
    ML_FEATURE_CLIP_RANGES,
    ML_FEATURE_CLIPPING_VERSION,
    ML_FEATURE_NAMES,
    ML_FEATURE_SCHEMA_VERSION,
    _clip_feature,
    feature_model_key_suffix,
    feature_names_for_profile,
)
from krakked.strategy.ml_labels import (
    FEE_ADJUSTED_EDGE_PREDICTION_TARGET,
    NO_POSITIVE_EDGE_PREDICTION,
    POSITIVE_EDGE_PREDICTION,
)
from krakked.strategy.ml_models import (
    DEFAULT_REGRESSION_MODEL_BACKEND,
    DEFAULT_REGRESSION_EPSILON_PCT,
    DEFAULT_SGD_L2_ALPHA,
    DEFAULT_SGD_LEARNING_RATE_INITIAL,
    MLOnlineModelBundle,
    PassiveAggressiveClassifier,
    PassiveAggressiveRegressor,
    StandardScaler,
    classifier_model_config_key,
    create_regression_model_bundle,
    is_regression_model_for_backend,
    regression_model_backend,
    regression_model_config_key,
    regression_model_framework,
    supports_partial_fit_sample_weight,
)
from krakked.strategy.strategies.ml_alt_strategy import AIPredictorAltStrategy
from krakked.strategy.strategies.ml_regression_strategy import AIRegressionStrategy
from krakked.strategy.strategies.ml_strategy import AIPredictorStrategy


@dataclass
class MockBar:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 100.0
    trades: int = 10


def _make_bars(start_ts, prices):
    bars = []
    for i, p in enumerate(prices):
        bars.append(
            MockBar(
                timestamp=start_ts + i * 3600,
                open=p,
                high=p + 1,
                low=p - 1,
                close=p,
            )
        )
    return bars


@pytest.fixture
def strategy():
    cfg = StrategyConfig(
        name="ai_test",
        type="ai_predictor",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
        },
    )
    return AIPredictorStrategy(cfg)


@pytest.fixture
def regression_strategy():
    cfg = StrategyConfig(
        name="reg_test",
        type="ai_regression",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
            "min_edge_pct": 0.05,  # 5% threshold
        },
    )
    return AIRegressionStrategy(cfg)


@pytest.fixture
def mock_ctx():
    ctx = MagicMock(spec=StrategyContext)
    ctx.now = datetime.now(timezone.utc)
    ctx.market_data = MagicMock()
    ctx.portfolio = MagicMock()
    ctx.portfolio.app_config = SimpleNamespace(
        execution=SimpleNamespace(max_slippage_bps=50)
    )
    ctx.portfolio.store = None
    ctx.portfolio.get_positions.return_value = []
    ctx.universe = ["XBT/USD"]
    ctx.timeframe = None
    return ctx


class FakeCheckpointStore:
    def __init__(self) -> None:
        self.live_models: dict[tuple[str, str], tuple[object, datetime]] = {}
        self.training_checkpoints: dict[
            tuple[str, str, str], tuple[object, datetime, str, dict[str, Any]]
        ] = {}

    def load_ml_model(self, strategy_id: str, model_key: str):
        return self.live_models.get((strategy_id, model_key))

    def load_ml_model_checkpoint(
        self, strategy_id: str, model_key: str, *, checkpoint_kind: str
    ):
        return self.training_checkpoints.get((strategy_id, model_key, checkpoint_kind))


def test_extract_training_example(strategy, mock_ctx):
    start_ts = 1000000
    prices = [100.0] * 8 + [100.0, 104.0]
    bars = _make_bars(start_ts, prices)
    mock_ctx.market_data.get_ohlc.return_value = bars

    features, label = strategy._extract_training_example(mock_ctx, "XBT/USD", "1h")

    assert label == 1.0
    assert len(features) == len(ML_FEATURE_NAMES)
    mock_ctx.market_data.get_ohlc.assert_called()


def test_extract_training_example_filters_micro_move_after_costs(strategy, mock_ctx):
    start_ts = 1000000
    prices = [100.0] * 8 + [100.0, 101.0]
    bars = _make_bars(start_ts, prices)
    mock_ctx.market_data.get_ohlc.return_value = bars

    features, label = strategy._extract_training_example(mock_ctx, "XBT/USD", "1h")

    assert label == 0.0
    assert len(features) == len(ML_FEATURE_NAMES)


def test_extract_training_example_down(strategy, mock_ctx):
    start_ts = 1000000
    prices = [100.0] * 8 + [110.0, 105.0]
    bars = _make_bars(start_ts, prices)
    mock_ctx.market_data.get_ohlc.return_value = bars

    features, label = strategy._extract_training_example(mock_ctx, "XBT/USD", "1h")

    assert label == 0.0


def test_catch_up_model(strategy, mock_ctx):
    strategy.model = MagicMock()
    strategy.model_initialized = True
    strategy.classes = [0, 1]

    now = datetime.fromtimestamp(1000000 + 10 * 3600, tz=timezone.utc)
    last_updated = now - timedelta(hours=5)
    mock_ctx.now = now

    bars = _make_bars(1000000, [100 + i for i in range(20)])
    mock_ctx.market_data.get_ohlc.return_value = bars

    strategy._catch_up_model(mock_ctx, "1h", last_updated)

    assert strategy.model.partial_fit.called
    assert strategy.model.partial_fit.call_count >= 1


def test_generate_intents_trains_and_predicts(strategy, mock_ctx):
    strategy.model = MagicMock()
    strategy.model_initialized = True
    strategy.model.predict.return_value = [1]
    strategy.model.decision_function.return_value = [1.0]

    bars = _make_bars(1000000, [100 + i for i in range(20)])
    mock_ctx.market_data.get_ohlc.return_value = bars
    mock_ctx.market_data.get_latest_price.return_value = 120.0

    intents = strategy.generate_intents(mock_ctx)

    assert strategy.model.partial_fit.called
    assert strategy.model.predict.called
    assert len(intents) == 1
    assert intents[0].side == "long"
    assert intents[0].metadata["prediction"] == POSITIVE_EDGE_PREDICTION
    assert (
        intents[0].metadata["prediction_target"] == FEE_ADJUSTED_EDGE_PREDICTION_TARGET
    )
    assert intents[0].metadata["predicted_positive_edge"] is True
    assert intents[0].metadata["confidence_source"] == "decision_function_magnitude"
    assert intents[0].metadata["feature_schema_version"] == ML_FEATURE_SCHEMA_VERSION


def test_generate_intents_reports_no_positive_edge_as_flat(strategy, mock_ctx):
    strategy.model = MagicMock()
    strategy.model_initialized = True
    strategy.model.predict.return_value = [0]
    strategy.model.decision_function.return_value = [-1.0]

    bars = _make_bars(1000000, [100 + i for i in range(20)])
    mock_ctx.market_data.get_ohlc.return_value = bars
    mock_ctx.market_data.get_latest_price.return_value = 120.0

    intents = strategy.generate_intents(mock_ctx)

    assert len(intents) == 1
    assert intents[0].side == "flat"
    assert intents[0].metadata["prediction"] == NO_POSITIVE_EDGE_PREDICTION
    assert (
        intents[0].metadata["prediction_target"] == FEE_ADJUSTED_EDGE_PREDICTION_TARGET
    )
    assert intents[0].metadata["predicted_positive_edge"] is False


def test_regression_extract_training_example(regression_strategy, mock_ctx):
    # Regression label is (Close(T) - Close(T-1)) / Close(T-1)
    start_ts = 1000000
    prices = [100.0] * 8 + [100.0, 110.0]  # T-1=100, T=110. Return = 0.1
    bars = _make_bars(start_ts, prices)
    mock_ctx.market_data.get_ohlc.return_value = bars

    features, label = regression_strategy._extract_training_example(
        mock_ctx, "XBT/USD", "1h"
    )

    assert label == pytest.approx(0.1)
    assert len(features) == len(ML_FEATURE_NAMES)


def test_regression_min_edge_pct(regression_strategy, mock_ctx):
    # Threshold is 0.05
    regression_strategy.model = MagicMock()
    regression_strategy.model_initialized = True

    bars = _make_bars(1000000, [100 + i for i in range(20)])
    mock_ctx.market_data.get_ohlc.return_value = bars

    # 1. Prediction below threshold (0.04) -> Flat
    regression_strategy.model.predict.return_value = [0.04]
    intents = regression_strategy.generate_intents(mock_ctx)
    assert intents[0].side == "flat"

    # 2. Prediction above threshold (0.06) -> Long
    regression_strategy.model.predict.return_value = [0.06]
    intents = regression_strategy.generate_intents(mock_ctx)
    assert intents[0].side == "long"
    assert intents[0].metadata["effective_min_edge_pct"] == pytest.approx(0.05)


def test_regression_cost_hurdle_blocks_sub_cost_prediction(mock_ctx):
    cfg = StrategyConfig(
        name="reg_cost_test",
        type="ai_regression",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
            "min_edge_pct": 0.001,
        },
    )
    strategy = AIRegressionStrategy(cfg)
    strategy.model = MagicMock()
    strategy.model_initialized = True
    mock_ctx.market_data.get_ohlc.return_value = _make_bars(
        1000000, [100 + i for i in range(20)]
    )

    strategy.model.predict.return_value = [0.014]
    intents = strategy.generate_intents(mock_ctx)
    assert intents[0].side == "flat"
    assert intents[0].metadata["effective_min_edge_pct"] == pytest.approx(0.015)
    assert intents[0].metadata["confidence_source"] == "predicted_delta_magnitude"

    strategy.model.predict.return_value = [0.016]
    intents = strategy.generate_intents(mock_ctx)
    assert intents[0].side == "long"


def test_classifier_bootstrap_prefers_newer_training_checkpoint(strategy, mock_ctx):
    store = FakeCheckpointStore()
    live_model = PassiveAggressiveClassifier(max_iter=1000, tol=1e-3)
    live_model.partial_fit([[0.0, 0.0, 0.0]], [0], classes=[0, 1])
    checkpoint_model = PassiveAggressiveClassifier(max_iter=1000, tol=1e-3)
    checkpoint_model.partial_fit([[1.0, 1.0, 1.0]], [1], classes=[0, 1])

    strategy_id = strategy.id
    model_key = strategy._model_key("1h")
    store.live_models[(strategy_id, model_key)] = (
        live_model,
        datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    store.training_checkpoints[(strategy_id, model_key, "training")] = (
        checkpoint_model,
        datetime.now(timezone.utc),
        "training",
        {"model_initialized": True},
    )

    mock_ctx.portfolio.store = store
    strategy._maybe_bootstrap_from_history(mock_ctx, "1h")

    assert strategy.model_initialized is True
    assert strategy.model.predict([[0.2, 0.1, 0.3]])[0] == 1


def test_classifier_model_key_versions_fee_adjusted_labels(strategy, mock_ctx):
    label_config = strategy._label_config(mock_ctx)

    assert (
        strategy._model_key("1h", label_config)
        == "global|1h|features_ohlc_v5|fee_adj_fee25_slip50_x2|"
        "pa_cls_scalerstdv1"
    )


def test_regression_model_key_versions_feature_schema(regression_strategy):
    assert (
        regression_strategy._model_key("1h")
        == "global|1h|features_ohlc_v5|pa_reg_eps0p001_scalerstdv1"
    )


def test_regression_epsilon_changes_model_key():
    cfg = StrategyConfig(
        name="reg_test",
        type="ai_regression",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
            "regression_epsilon_pct": 0.0025,
        },
    )
    strategy = AIRegressionStrategy(cfg)

    assert (
        strategy._model_key("1h")
        == "global|1h|features_ohlc_v5|pa_reg_eps0p0025_scalerstdv1"
    )


def test_feature_profile_changes_feature_names_metadata_and_model_key():
    cfg = StrategyConfig(
        name="reg_profile_test",
        type="ai_regression",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
            "feature_profile": "drop_weakest",
        },
    )
    strategy = AIRegressionStrategy(cfg)

    assert strategy.params.feature_profile == "drop_weakest"
    assert strategy._model_key("1h") == (
        "global|1h|features_ohlc_v5_profile_drop_weakest|"
        "pa_reg_eps0p001_scalerstdv1"
    )
    assert feature_model_key_suffix("drop_weakest") == (
        "features_ohlc_v5_profile_drop_weakest"
    )
    assert "pct_change" not in feature_names_for_profile("drop_weakest")
    assert "trend_diff" in feature_names_for_profile("drop_weakest")


def test_ml_feature_values_are_shared_across_strategies(
    strategy, regression_strategy, mock_ctx
):
    cfg = StrategyConfig(
        name="ai_alt_test",
        type="ai_predictor_alt",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
        },
    )
    alt_strategy = AIPredictorAltStrategy(cfg)
    bars = _make_bars(1000000, [100.0, 102.0, 101.0, 103.0, 105.0])
    mock_ctx.market_data.get_ohlc.return_value = bars

    classifier_vector = strategy._extract_feature_vector(mock_ctx, "XBT/USD", "1h")
    alt_vector = alt_strategy._extract_feature_vector(mock_ctx, "XBT/USD", "1h")
    regression_vector = regression_strategy._extract_feature_vector(
        mock_ctx, "XBT/USD", "1h"
    )

    assert classifier_vector is not None
    assert alt_vector is not None
    assert regression_vector is not None
    assert classifier_vector.names == list(ML_FEATURE_NAMES)
    assert classifier_vector.schema_version == ML_FEATURE_SCHEMA_VERSION
    assert classifier_vector.values == pytest.approx(alt_vector.values)
    assert classifier_vector.values == pytest.approx(regression_vector.values)


def test_feature_profile_vectors_use_configured_subset(
    strategy, regression_strategy, mock_ctx
):
    strategy.config.params["feature_profile"] = "drop_time"
    strategy.params.feature_profile = "drop_time"
    regression_strategy.config.params["feature_profile"] = "drop_time"
    regression_strategy.params.feature_profile = "drop_time"
    bars = _make_bars(1000000, [100.0, 102.0, 101.0, 103.0, 105.0])
    mock_ctx.market_data.get_ohlc.return_value = bars

    classifier_vector = strategy._extract_feature_vector(mock_ctx, "XBT/USD", "1h")
    regression_vector = regression_strategy._extract_feature_vector(
        mock_ctx, "XBT/USD", "1h"
    )

    assert classifier_vector is not None
    assert regression_vector is not None
    assert classifier_vector.names == list(feature_names_for_profile("drop_time"))
    assert classifier_vector.schema_version == ML_FEATURE_SCHEMA_VERSION
    assert classifier_vector.profile == "drop_time"
    assert "hour_sin" not in classifier_vector.names
    metadata = classifier_vector.to_metadata()
    assert metadata["feature_profile"] == "drop_time"
    assert metadata["feature_names"] == list(feature_names_for_profile("drop_time"))
    assert "hour_sin" in metadata["feature_profile_excluded_features"]
    assert classifier_vector.values == pytest.approx(regression_vector.values)


def test_ohlc_v5_feature_values_include_normalized_context_fields(
    strategy, mock_ctx
):
    bars = [
        MockBar(1000000, open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0),
        MockBar(1003600, open=101.0, high=103.0, low=100.0, close=102.0, volume=110.0),
        MockBar(1007200, open=102.0, high=103.0, low=100.0, close=101.0, volume=90.0),
        MockBar(1010800, open=101.0, high=104.0, low=100.0, close=103.0, volume=130.0),
        MockBar(1014400, open=103.0, high=108.0, low=100.0, close=105.0, volume=170.0),
    ]
    mock_ctx.market_data.get_ohlc.return_value = bars

    vector = strategy._extract_feature_vector(mock_ctx, "XBT/USD", "1h")

    assert vector is not None
    features = dict(zip(vector.names, vector.values))
    assert vector.schema_version == "ohlc_v5"
    atr = 4.0
    atr_pct = atr / 105.0
    assert features["return_atr_1"] == pytest.approx(((105.0 - 103.0) / 103.0) / atr_pct)
    assert features["return_atr_3"] == pytest.approx(((105.0 - 102.0) / 102.0) / atr_pct)
    assert features["range_atr"] == pytest.approx((108.0 - 100.0) / atr)
    assert features["upper_wick_atr"] == pytest.approx((108.0 - 105.0) / atr)
    assert "body_atr" not in features
    assert "lower_wick_atr" not in features
    assert features["return_zscore"] > 0.0
    assert features["volatility_ratio"] > 0.0
    assert features["volume_change"] == pytest.approx(math.log(170.0 / 130.0))
    assert features["volume_log_ratio"] == pytest.approx(math.log(170.0 / 120.0))
    observed_at = datetime.fromtimestamp(1014400, tz=timezone.utc)
    hour_value = observed_at.hour + observed_at.minute / 60.0 + observed_at.second / 3600.0
    assert features["hour_sin"] == pytest.approx(
        math.sin(2.0 * math.pi * hour_value / 24.0)
    )
    assert features["hour_cos"] == pytest.approx(
        math.cos(2.0 * math.pi * hour_value / 24.0)
    )
    assert features["weekday_sin"] == pytest.approx(
        math.sin(2.0 * math.pi * observed_at.weekday() / 7.0)
    )
    assert features["weekday_cos"] == pytest.approx(
        math.cos(2.0 * math.pi * observed_at.weekday() / 7.0)
    )


def test_ohlc_v5_clipping_caps_apply_exactly_per_feature():
    for name, (cap_min, cap_max) in ML_FEATURE_CLIP_RANGES.items():
        clipped_low, low_metadata = _clip_feature(name, cap_min - 1.0)
        clipped_high, high_metadata = _clip_feature(name, cap_max + 1.0)

        assert clipped_low == pytest.approx(cap_min)
        assert clipped_high == pytest.approx(cap_max)
        assert low_metadata is not None
        assert high_metadata is not None
        assert low_metadata["was_clipped"] is True
        assert high_metadata["was_clipped"] is True


def test_ohlc_v5_unclipped_features_remain_unchanged():
    for name in set(ML_FEATURE_NAMES) - set(ML_FEATURE_CLIP_RANGES):
        value, metadata = _clip_feature(name, 42.0)

        assert value == pytest.approx(42.0)
        assert metadata is None


def test_ohlc_v5_metadata_records_raw_and_clipped_values(strategy, mock_ctx):
    bars = _make_bars(1000000, [100.0, 100.0, 100.0, 100.0, 200.0])
    mock_ctx.market_data.get_ohlc.return_value = bars

    vector = strategy._extract_feature_vector(mock_ctx, "XBT/USD", "1h")

    assert vector is not None
    metadata = vector.to_metadata()
    clipping = metadata["feature_clipping"]
    assert metadata["feature_clipping_version"] == ML_FEATURE_CLIPPING_VERSION
    assert metadata["pct_change"] == pytest.approx(0.15)
    assert clipping["pct_change"]["raw_value"] == pytest.approx(1.0)
    assert clipping["pct_change"]["clipped_value"] == pytest.approx(0.15)
    assert clipping["pct_change"]["cap_min"] == pytest.approx(-0.15)
    assert clipping["pct_change"]["cap_max"] == pytest.approx(0.15)
    assert clipping["pct_change"]["was_clipped"] is True
    assert "trend_diff" not in clipping


def test_passive_aggressive_models_do_not_support_sample_weight_guard():
    assert supports_partial_fit_sample_weight(PassiveAggressiveClassifier()) is False
    assert supports_partial_fit_sample_weight(PassiveAggressiveRegressor()) is False
    assert supports_partial_fit_sample_weight(regression_strategy_model()) is False


def regression_strategy_model() -> MLOnlineModelBundle:
    return MLOnlineModelBundle(
        model=PassiveAggressiveRegressor(
            max_iter=1000,
            tol=1e-3,
            epsilon=DEFAULT_REGRESSION_EPSILON_PCT,
        ),
        scaler=StandardScaler(),
    )


def test_online_model_bundle_persists_scaler_state():
    bundle = regression_strategy_model()

    bundle.partial_fit(
        [[1.0, 10.0, 100.0], [2.0, 20.0, 200.0]],
        [0.01, 0.02],
    )
    restored = pickle.loads(pickle.dumps(bundle))

    assert isinstance(restored, MLOnlineModelBundle)
    assert restored.scaler_initialized is True
    assert restored.scaler_schema_version == "standard_v1"
    assert restored.scaler.mean_[0] == pytest.approx(1.5)
    assert len(restored.predict([[3.0, 30.0, 300.0]])) == 1


def test_model_config_key_helpers_are_stable():
    assert classifier_model_config_key() == "pa_cls_scalerstdv1"
    assert (
        regression_model_config_key(DEFAULT_REGRESSION_EPSILON_PCT)
        == "pa_reg_eps0p001_scalerstdv1"
    )
    assert regression_model_backend("unknown") == DEFAULT_REGRESSION_MODEL_BACKEND
    assert regression_model_framework("pa") == "sklearn_passive_aggressive_regressor"
    assert (
        regression_model_config_key(
            DEFAULT_REGRESSION_EPSILON_PCT,
            model_backend="sgd_huber",
            sgd_l2_alpha=DEFAULT_SGD_L2_ALPHA,
            sgd_learning_rate_initial=DEFAULT_SGD_LEARNING_RATE_INITIAL,
        )
        == "sgd_huber_alpha0p0001_eta0p001_eps0p001_scalerstdv1"
    )
    assert (
        regression_model_config_key(
            DEFAULT_REGRESSION_EPSILON_PCT,
            model_backend="sgd_squared_error",
            sgd_l2_alpha=DEFAULT_SGD_L2_ALPHA,
            sgd_learning_rate_initial=DEFAULT_SGD_LEARNING_RATE_INITIAL,
        )
        == "sgd_squared_error_alpha0p0001_eta0p001_scalerstdv1"
    )
    assert (
        regression_model_config_key(
            DEFAULT_REGRESSION_EPSILON_PCT,
            model_backend="sgd_huber",
            sgd_l2_alpha=DEFAULT_SGD_L2_ALPHA,
            sgd_learning_rate_initial=0.0,
        )
        == "sgd_huber_alpha0p0001_eta0p001_eps0p001_scalerstdv1"
    )


def test_regression_backend_factory_and_keys_are_backend_specific():
    cfg = StrategyConfig(
        name="reg_sgd_test",
        type="ai_regression",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
            "model_backend": "sgd_huber",
            "regression_epsilon_pct": 0.0025,
            "sgd_l2_alpha": 0.0002,
            "sgd_learning_rate_initial": 0.002,
        },
    )
    strategy = AIRegressionStrategy(cfg)

    assert strategy.params.model_backend == "sgd_huber"
    assert strategy._model_framework() == "sklearn_sgd_regressor_huber"
    assert (
        strategy._model_key("1h")
        == "global|1h|features_ohlc_v5|"
        "sgd_huber_alpha0p0002_eta0p002_eps0p0025_scalerstdv1"
    )
    assert is_regression_model_for_backend(strategy.model, "sgd_huber") is True
    assert is_regression_model_for_backend(strategy.model, "pa") is False
    metadata = strategy._checkpoint_metadata()
    assert metadata["model_backend"] == "sgd_huber"
    assert metadata["model_framework"] == "sklearn_sgd_regressor_huber"
    assert metadata["sgd_l2_alpha"] == pytest.approx(0.0002)
    assert metadata["sgd_learning_rate_initial"] == pytest.approx(0.002)


def test_regression_restore_accepts_matching_backend_checkpoint(mock_ctx):
    cfg = StrategyConfig(
        name="reg_sgd_test",
        type="ai_regression",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
            "model_backend": "sgd_huber",
        },
    )
    strategy = AIRegressionStrategy(cfg)
    store = FakeCheckpointStore()
    checkpoint_model = create_regression_model_bundle(model_backend="sgd_huber")
    checkpoint_model.partial_fit([[0.0] * len(ML_FEATURE_NAMES)], [0.01])
    model_key = strategy._model_key("1h")
    store.training_checkpoints[(strategy.id, model_key, "training")] = (
        checkpoint_model,
        datetime.now(timezone.utc),
        "ready",
        {"model_initialized": True},
    )

    mock_ctx.portfolio.store = store
    strategy._maybe_bootstrap_from_history(mock_ctx, "1h")

    assert strategy.model_initialized is True
    assert strategy.model is checkpoint_model


def test_regression_restore_rejects_mismatched_backend_checkpoint(mock_ctx):
    strategy = AIRegressionStrategy(
        StrategyConfig(
            name="reg_pa_test",
            type="ai_regression",
            enabled=True,
            params={
                "pairs": ["XBT/USD"],
                "timeframe": "1h",
                "lookback_bars": 5,
                "short_window": 2,
                "long_window": 5,
                "continuous_learning": True,
            },
        )
    )
    store = FakeCheckpointStore()
    checkpoint_model = create_regression_model_bundle(model_backend="sgd_huber")
    checkpoint_model.partial_fit([[0.0] * len(ML_FEATURE_NAMES)], [0.01])
    store.training_checkpoints[(strategy.id, strategy._model_key("1h"), "training")] = (
        checkpoint_model,
        datetime.now(timezone.utc),
        "ready",
        {"model_initialized": True},
    )

    mock_ctx.portfolio.store = store
    strategy._maybe_bootstrap_from_history(mock_ctx, "1h")

    assert strategy.model_initialized is False


def test_alt_strategy_restores_last_observation_from_checkpoint(mock_ctx):
    cfg = StrategyConfig(
        name="ai_alt_test",
        type="ai_predictor_alt",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
        },
    )
    strategy = AIPredictorAltStrategy(cfg)
    store = FakeCheckpointStore()
    checkpoint_model = PassiveAggressiveClassifier(max_iter=1000, tol=1e-3)

    model_key = strategy._model_key("XBT/USD", "1h")
    store.training_checkpoints[(strategy.id, model_key, "training")] = (
        checkpoint_model,
        datetime.now(timezone.utc),
        "ready",
        {
            "model_initialized": False,
            "last_observation": {
                "features": [0.1, -0.2, 0.3],
                "price": 123.45,
            },
        },
    )

    mock_ctx.portfolio.store = store
    key = ("XBT/USD", "1h")
    strategy._maybe_bootstrap_from_history(mock_ctx, key, strategy._get_model(key))

    assert strategy.model_initialized[key] is False
    assert strategy._last_observation[key] == ([0.1, -0.2, 0.3], 123.45)


def test_alt_strategy_trains_micro_up_move_as_flat(mock_ctx):
    cfg = StrategyConfig(
        name="ai_alt_test",
        type="ai_predictor_alt",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": True,
        },
    )
    strategy = AIPredictorAltStrategy(cfg)
    key = ("XBT/USD", "1h")
    model = MagicMock()
    model.predict.return_value = [0]
    model.decision_function.return_value = [-1.0]
    strategy.models[key] = model
    strategy.model_initialized[key] = False
    strategy._last_observation[key] = ([0.1, 0.0, 0.01], 100.0)

    mock_ctx.market_data.get_latest_price.return_value = 101.0
    mock_ctx.market_data.get_ohlc.return_value = _make_bars(
        1000000, [100.0] * 8 + [101.0, 101.0]
    )

    strategy.generate_intents(mock_ctx)

    model.partial_fit.assert_called()
    assert model.partial_fit.call_args.args[1] == [0]


def test_alt_strategy_freeze_does_not_record_or_update_training(mock_ctx):
    cfg = StrategyConfig(
        name="ai_alt_test",
        type="ai_predictor_alt",
        enabled=True,
        params={
            "pairs": ["XBT/USD"],
            "timeframe": "1h",
            "lookback_bars": 5,
            "short_window": 2,
            "long_window": 5,
            "continuous_learning": False,
        },
    )
    strategy = AIPredictorAltStrategy(cfg)
    key = ("XBT/USD", "1h")
    model = MagicMock()
    model.predict.return_value = [0]
    model.decision_function.return_value = [-1.0]
    strategy.models[key] = model
    strategy.model_initialized[key] = True
    strategy._last_observation[key] = ([0.1, 0.0, 0.01], 100.0)
    store = MagicMock()

    mock_ctx.portfolio.store = store
    mock_ctx.market_data.get_latest_price.return_value = 101.0
    mock_ctx.market_data.get_ohlc.return_value = _make_bars(
        1000000, [100.0] * 8 + [101.0, 101.0]
    )

    intents = strategy.generate_intents(mock_ctx)

    model.partial_fit.assert_not_called()
    store.record_ml_example.assert_not_called()
    checkpoint_states = [
        call.kwargs["checkpoint_state"]
        for call in store.save_ml_model_checkpoint.call_args_list
    ]
    assert "training" not in checkpoint_states
    assert intents
    assert intents[0].metadata["learning_enabled"] is False
