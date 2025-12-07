from datetime import datetime, timezone
from pathlib import Path

from kraken_bot.portfolio.store import MAX_ML_TRAINING_EXAMPLES, SQLitePortfolioStore


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

    loaded = store.load_ml_model("ml_strategy", "global|1h")
    assert isinstance(loaded, DummyModel)
    assert loaded.value == 42
