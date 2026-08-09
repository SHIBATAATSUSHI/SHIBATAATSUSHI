"""ベンチの実行。1タスクを1モデルで走らせ、結果を測る。

測るのは4つ。

- 出来  … 用意した検証条件を全部満たせたか
- 速さ  … 指示を出してから終わるまでの実時間
- 量    … 往復回数、ツール呼び出し回数、トークン数
- 費用  … トークン数と価格表から出した概算(価格未設定のモデルは 0)

作業は必ず使い捨ての一時ディレクトリで行う。手元のリポジトリを
書き換えないため、また毎回同じ初期状態から始めるため。
"""

from __future__ import annotations

import io
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..agent import Agent, AgentError
from ..backends.base import Backend, BackendError
from ..config import Config, build_config
from ..messages import ROLE_ASSISTANT
from ..session import Session
from ..tools import Toolbox
from ..ui import UI
from ..workspace import Workspace
from .tasks import CheckResult, RunContext, TaskSpec

#: ベンチ中の1ターンで許すツール往復の上限。無限に粘られると測れないので絞る。
DEFAULT_MAX_ITERATIONS = 30

BackendFactory = Callable[[Config], Backend]


class BenchUI(UI):
    """ベンチ用の表示。既定では何も出さず、あとで読めるよう溜めておく。"""

    def __init__(self, *, verbose: bool = False, stream=None) -> None:
        super().__init__(
            color=False,
            stream=stream if verbose else io.StringIO(),
            width=100,
        )
        self.verbose = verbose

    def confirm(self, question: str, detail: str = "") -> bool:
        """ベンチは無人で回す。確認は出さず、必ず拒否として扱う。

        権限モードは yolo で走らせるのでここへは来ないが、
        設定を間違えたときに端末で止まってしまわないよう保険をかけておく。
        """
        return False


@dataclass
class TaskOutcome:
    """1タスク1モデル分の測定結果。"""

    task: str
    task_title: str
    model_key: str
    model_label: str
    provider: str
    passed: bool = False
    checks: list[CheckResult] = field(default_factory=list)
    elapsed: float = 0.0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost: float = 0.0
    # 費用の内訳(input / output / cache_write / cache_read)
    cost_parts: dict[str, float] = field(default_factory=dict)
    tool_calls: int = 0
    answer: str = ""
    error: str = ""
    # 続けても同じ結果にしかならないエラーか(認証・権限)
    fatal: bool = False

    @property
    def status(self) -> str:
        if self.error:
            return "エラー"
        return "成功" if self.passed else "失敗"

    def failed_checks(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.passed]

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "task_title": self.task_title,
            "model_key": self.model_key,
            "model_label": self.model_label,
            "provider": self.provider,
            "status": self.status,
            "passed": self.passed,
            "elapsed_sec": round(self.elapsed, 2),
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cost_usd": round(self.cost, 6),
            "cost_parts_usd": {k: round(v, 6) for k, v in self.cost_parts.items()},
            "tool_calls": self.tool_calls,
            "error": self.error,
            "fatal": self.fatal,
            "checks": [
                {"label": c.label, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
        }


def last_assistant_text(session: Session) -> str:
    """最後のアシスタント発言の本文。無ければ空文字。"""
    for message in reversed(session.messages):
        if message.role == ROLE_ASSISTANT and message.text.strip():
            return message.text
    return ""


def count_tool_calls(session: Session) -> int:
    """履歴に残ったツール呼び出しの総数。"""
    return sum(len(message.tool_calls or []) for message in session.messages)


def run_task(
    task: TaskSpec,
    *,
    model: str,
    verbose: bool = False,
    keep_workspace: Path | None = None,
    backend_factory: BackendFactory | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    **config_overrides,
) -> TaskOutcome:
    """1タスクを1モデルで走らせて測る。

    `backend_factory` を渡すと通信経路を差し替えられる(テスト用)。
    `keep_workspace` を渡すと、終わったあとの作業ディレクトリをそこへ残す。
    """
    root = Path(tempfile.mkdtemp(prefix=f"bench-{task.key}-"))
    try:
        task.materialize(root)

        config = build_config(
            model=model, workspace=root, permission_mode="yolo", **config_overrides
        )
        # 暴走の上限だけベンチ向けに締める
        config = replace(config, max_iterations=max_iterations, color=False)

        outcome = TaskOutcome(
            task=task.key,
            task_title=task.title,
            model_key=model,
            model_label=config.model.label,
            provider=config.model.provider,
        )

        ui = BenchUI(verbose=verbose)
        session = Session(workspace=root)
        agent = Agent(
            config=config,
            toolbox=Toolbox(Workspace(root), bash_timeout=config.bash_timeout),
            session=session,
            ui=ui,
            backend=backend_factory(config) if backend_factory else None,
        )

        started = time.perf_counter()
        try:
            agent.run_turn(task.prompt)
        except (AgentError, BackendError) as exc:
            outcome.error = str(exc)
            # AgentError は BackendError を包んで投げられる。元の例外まで見る。
            cause = exc.__cause__ if isinstance(exc, AgentError) else exc
            outcome.fatal = bool(getattr(cause, "is_auth_failure", False))
        except KeyboardInterrupt:
            outcome.error = "中断された"
            raise
        except Exception as exc:  # 想定外も記録して次のタスクへ進む
            outcome.error = f"{type(exc).__name__}: {exc}"
        finally:
            outcome.elapsed = time.perf_counter() - started

        usage = session.usage
        outcome.requests = usage.requests
        outcome.input_tokens = usage.input_tokens
        outcome.output_tokens = usage.output_tokens
        outcome.cache_read_tokens = usage.cache_read_tokens
        outcome.cache_creation_tokens = usage.cache_creation_tokens
        outcome.cost_parts = usage.cost_breakdown(config.model)
        outcome.cost = sum(outcome.cost_parts.values())
        outcome.tool_calls = count_tool_calls(session)
        outcome.answer = last_assistant_text(session)

        ctx = RunContext(workspace=root, answer=outcome.answer)
        outcome.checks = task.verify(ctx)
        outcome.passed = bool(outcome.checks) and all(c.passed for c in outcome.checks)
        if outcome.error:
            outcome.passed = False

        if keep_workspace is not None:
            destination = Path(keep_workspace) / f"{model}-{task.key}"
            if destination.exists():
                shutil.rmtree(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(root, destination)

        return outcome
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run_suite(
    tasks: Iterable[TaskSpec],
    models: Iterable[str],
    *,
    repeat: int = 1,
    progress: Callable[[str], None] | None = None,
    **kwargs,
) -> list[TaskOutcome]:
    """タスク × モデル × 回数を全部走らせる。

    モデルを外側にしているのは、同じモデルの結果が続けて出たほうが
    途中経過を眺めやすいため。
    """
    outcomes: list[TaskOutcome] = []
    task_list = list(tasks)
    for model in models:
        for attempt in range(1, repeat + 1):
            for task in task_list:
                if progress:
                    suffix = f" ({attempt}/{repeat})" if repeat > 1 else ""
                    progress(f"▶ {model} / {task.key}{suffix}")
                outcome = run_task(task, model=model, **kwargs)
                outcomes.append(outcome)
                if progress:
                    progress(
                        f"  {outcome.status} / {outcome.elapsed:.1f}s"
                        f" / {outcome.requests} 往復 / ${outcome.cost:.4f}"
                    )
                if outcome.fatal:
                    # 認証で落ちているなら、残りを走らせても全部同じ壁に当たる。
                    # 18回繰り返して同じエラーを並べても何も分からない。
                    if progress:
                        progress("  認証で落ちたので、ここで打ち切る")
                    return outcomes
    return outcomes


def self_check(task: TaskSpec) -> list[CheckResult]:
    """検証条件そのものを点検する。

    模範解答を当てれば全部通り、何もしなければ少なくとも1つ落ちること。
    これが崩れていると、ベンチは何も測っていないことになる。
    """
    results: list[CheckResult] = []

    untouched = Path(tempfile.mkdtemp(prefix=f"selfcheck-before-{task.key}-"))
    try:
        task.materialize(untouched)
        before = task.verify(RunContext(workspace=untouched, answer=""))
        results.append(
            CheckResult(
                f"{task.key}: 手つかずなら落ちる",
                any(not c.passed for c in before),
                "初期状態で全部通ってしまう(条件が緩い)",
            )
        )
    finally:
        shutil.rmtree(untouched, ignore_errors=True)

    solved = Path(tempfile.mkdtemp(prefix=f"selfcheck-after-{task.key}-"))
    try:
        task.materialize(solved)
        task.apply_solution(solved)
        after = task.verify(RunContext(workspace=solved, answer=task.solution_answer))
        broken = [c for c in after if not c.passed]
        results.append(
            CheckResult(
                f"{task.key}: 模範解答なら通る",
                not broken,
                "; ".join(f"{c.label}: {c.detail}" for c in broken),
            )
        )
    finally:
        shutil.rmtree(solved, ignore_errors=True)

    return results
