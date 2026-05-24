import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from krakked.portfolio.store import MAX_ML_TRAINING_EXAMPLES, SQLitePortfolioStore


class DummyModel:
    def __init__(self, value: int) -> None:
        self.value = value


def _new_store(tmp_path: Path) -> SQLitePortfolioStore:
    db_path = tmp_path / "ml_test.db"
    return SQLitePortfolioStore(str(db_path))


def test_ml_examples_rolling_window(tmp_path):
    store = _new_store(tmp_path)

    now = datetime.now(timezone.utc)

    total = MAX_ML_TRAINING_EXAMPLES + 50
    for i in range(total):
        store.record_ml_example(
            strategy_id="ml_strategy",
            model_key="global|1h",
            created_at=now,
            source_mode="paper",
            label_type="classification",
            features=[float(i), 1.0, 2.0],
            label=float(i % 2),
        )

    X, y = store.load_ml_training_window(
        "ml_strategy", "global|1h", max_examples=MAX_ML_TRAINING_EXAMPLES
    )

    assert len(X) == MAX_ML_TRAINING_EXAMPLES
    assert len(y) == MAX_ML_TRAINING_EXAMPLES

    conn = sqlite3.connect(store.db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM ml_training_examples
            WHERE strategy_id = ? AND model_key = ?
            """,
            ("ml_strategy", "global|1h"),
        )
        (count,) = cursor.fetchone()
    finally:
        conn.close()

    assert count == MAX_ML_TRAINING_EXAMPLES


def test_ml_model_roundtrip(tmp_path):
    store = _new_store(tmp_path)

    model = DummyModel(42)

    store.save_ml_model(
        strategy_id="ml_strategy",
        model_key="global|1h",
        label_type="classification",
        framework="dummy",
        model=model,
    )

    result = store.load_ml_model("ml_strategy", "global|1h")
    assert result is not None
    loaded, updated_at = result
    assert isinstance(loaded, DummyModel)
    assert loaded.value == 42
    assert isinstance(updated_at, datetime)


def test_ml_checkpoint_roundtrip_preserves_state_and_metadata(tmp_path):
    store = _new_store(tmp_path)

    checkpoint_model = DummyModel(7)

    store.save_ml_model_checkpoint(
        strategy_id="ml_strategy",
        model_key="global|1h",
        checkpoint_kind="training",
        label_type="classification",
        framework="dummy",
        model=checkpoint_model,
        checkpoint_state="training",
        metadata={"model_initialized": True, "last_pair": "XBT/USD"},
    )

    result = store.load_ml_model_checkpoint(
        "ml_strategy",
        "global|1h",
        checkpoint_kind="training",
    )
    assert result is not None
    loaded, updated_at, checkpoint_state, metadata = result
    assert isinstance(loaded, DummyModel)
    assert loaded.value == 7
    assert checkpoint_state == "training"
    assert metadata["model_initialized"] is True
    assert metadata["last_pair"] == "XBT/USD"
    assert isinstance(updated_at, datetime)


def test_ml_checkpoint_and_live_model_are_stored_independently(tmp_path):
    store = _new_store(tmp_path)

    store.save_ml_model(
        strategy_id="ml_strategy",
        model_key="global|1h",
        label_type="classification",
        framework="dummy",
        model=DummyModel(42),
    )
    store.save_ml_model_checkpoint(
        strategy_id="ml_strategy",
        model_key="global|1h",
        checkpoint_kind="training",
        label_type="classification",
        framework="dummy",
        model=DummyModel(99),
        checkpoint_state="ready",
        metadata={"model_initialized": True},
    )

    live_result = store.load_ml_model("ml_strategy", "global|1h")
    checkpoint_result = store.load_ml_model_checkpoint(
        "ml_strategy",
        "global|1h",
        checkpoint_kind="training",
    )

    assert live_result is not None
    assert checkpoint_result is not None
    live_model, _ = live_result
    checkpoint_model, _, _, _ = checkpoint_result
    assert isinstance(live_model, DummyModel)
    assert isinstance(checkpoint_model, DummyModel)
    assert live_model.value == 42
    assert checkpoint_model.value == 99


def test_ml_artifact_group_listing_and_delete(tmp_path):
    store = _new_store(tmp_path)
    now = datetime.now(timezone.utc)

    store.record_ml_example(
        strategy_id="ai_regression",
        model_key="global|1h|features_ohlc_v1",
        created_at=now,
        source_mode="paper",
        label_type="regression",
        features=[1.0, 2.0, 3.0],
        label=0.01,
    )
    store.save_ml_model(
        strategy_id="ai_regression",
        model_key="global|1h|features_ohlc_v1",
        label_type="regression",
        framework="dummy",
        model=DummyModel(1),
    )
    store.save_ml_model_checkpoint(
        strategy_id="ai_regression",
        model_key="global|1h|features_ohlc_v1",
        checkpoint_kind="training",
        label_type="regression",
        framework="dummy",
        model=DummyModel(2),
    )

    groups = store.list_ml_artifact_groups()

    group = next(
        item
        for item in groups
        if item.strategy_id == "ai_regression"
        and item.model_key == "global|1h|features_ohlc_v1"
    )
    assert group.example_count == 1
    assert group.live_model_count == 1
    assert group.checkpoint_count == 1
    assert group.total_count == 3
    assert group.last_updated_at is not None

    deleted = store.delete_ml_artifact_group(
        "ai_regression",
        "global|1h|features_ohlc_v1",
    )

    assert deleted == {
        "example_count": 1,
        "live_model_count": 1,
        "checkpoint_count": 1,
        "total_count": 3,
    }
    assert store.list_ml_artifact_groups() == []
