import pytest

from llm_gateway.core.task_routing import classify_task


@pytest.mark.parametrize(
    "text",
    [
        "Implement a Python function",
        "Please debug this class",
        "SELECT * FROM users",
        "Use this regex to validate input",
        "The problem is in app.tsx",
        "```python\nprint('hello')\n```",
        'Traceback\n  File "app.py", line 12',
    ],
)
def test_classify_task_detects_coding_prompts(text: str) -> None:
    assert classify_task(text) == "coding"


@pytest.mark.parametrize(
    "text",
    [
        "Summarize this paragraph",
        "Write a friendly birthday message",
        "What is the capital of France?",
    ],
)
def test_classify_task_defaults_to_general(text: str) -> None:
    assert classify_task(text) == "general"
