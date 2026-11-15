import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Tests must never read or write the real repository state: data/ files
    and config/LIVE_ENABLED are replaced by temporary ones. By default the
    LIVE flag is ABSENT; tests that need it create it in tmp_path."""
    from comptoir import history, run
    monkeypatch.setattr(run, "LIVE_FLAG", tmp_path / "LIVE_ENABLED")
    monkeypatch.setattr(run, "LOG", tmp_path / "log.jsonl")
    monkeypatch.setattr(run, "QUEUE", tmp_path / "queue.json")
    monkeypatch.setattr(history, "HISTORY_FILE", tmp_path / "history.json")
