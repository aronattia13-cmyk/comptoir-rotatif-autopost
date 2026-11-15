import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Tests must never write to the real data/ files (log, queue, history)."""
    from comptoir import history, run
    monkeypatch.setattr(run, "LOG", tmp_path / "log.jsonl")
    monkeypatch.setattr(run, "QUEUE", tmp_path / "queue.json")
    monkeypatch.setattr(history, "HISTORY_FILE", tmp_path / "history.json")
