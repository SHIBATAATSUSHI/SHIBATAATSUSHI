"""モデル比較ベンチ。

同じタスクを別のモデルにやらせて、速さ・費用・出来を実測する。
「どのモデルが安いか」は価格表だけでは決まらない。安いモデルが
何往復もして結局高くつくこともあるので、往復回数まで含めて測る。
"""

from .report import ModelSummary, format_markdown, format_text, summarize, to_json
from .runner import TaskOutcome, run_suite, run_task, self_check
from .tasks import DEFAULT_ORDER, TASKS, TaskSpec, resolve_tasks

__all__ = [
    "DEFAULT_ORDER",
    "TASKS",
    "ModelSummary",
    "TaskOutcome",
    "TaskSpec",
    "format_markdown",
    "format_text",
    "resolve_tasks",
    "run_suite",
    "run_task",
    "self_check",
    "summarize",
    "to_json",
]
