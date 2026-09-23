import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "batch_generate", Path(__file__).parents[1] / "scripts" / "batch_generate.py"
)
assert SPEC and SPEC.loader
batch_generate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch_generate)


def test_extract_qa_pairs_normalizes_teacher_fields():
    response = '''```json
    [
      {"question": "What is torque?", "answer": "A turning force."},
      {"prompt": "What is speed?", "completion": "Distance over time."}
    ]
    ```'''

    assert batch_generate.extract_qa_pairs(response) == [
        {"prompt": "What is torque?", "completion": "A turning force."},
        {"prompt": "What is speed?", "completion": "Distance over time."},
    ]


def test_extract_qa_pairs_rejects_missing_answers():
    with pytest.raises(ValueError, match="had no answer"):
        batch_generate.extract_qa_pairs('[{"question":"What is torque?"}]')
