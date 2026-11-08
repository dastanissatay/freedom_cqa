import json
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# тесты никогда не ходят в реальную LLM и не трогают рабочую БД
import tempfile  # noqa: E402

os.environ["LLM_PROVIDER"] = "mock"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATASET_API_URL"] = ""

from app.schemas import ChecklistConfig  # noqa: E402


@pytest.fixture
def default_config() -> ChecklistConfig:
    raw = json.loads((BACKEND / "seed" / "default_checklist.json").read_text(encoding="utf-8"))
    return ChecklistConfig.model_validate(raw)
