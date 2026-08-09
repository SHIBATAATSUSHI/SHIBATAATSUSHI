"""ベンチ環境の検証。

ベンチ自体が壊れていると、出てくる数字を信じられない。ここでは
API を叩かずに、次の3点を確かめる。

1. 検証条件が機能していること(手つかずでは落ち、模範解答では通る)
2. 実行結果の測定値(往復・トークン・費用・時間)が正しく集まること
3. 失敗とエラーを取り違えないこと
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shibata_code.backends.base import (  # noqa: E402
    STOP_END_TURN,
    STOP_TOOL_USE,
    Backend,
    BackendError,
    TurnResult,
)
from shibata_code.bench import report as report_mod  # noqa: E402
from shibata_code.bench.cli import main as bench_main  # noqa: E402
from shibata_code.bench.runner import TaskOutcome, run_suite, run_task, self_check  # noqa: E402
from shibata_code.bench.tasks import (  # noqa: E402
    TASKS,
    AnswerContains,
    CommandSucceeds,
    FileEquals,
    FileLacks,
    RunContext,
    resolve_tasks,
)
from shibata_code.messages import ToolCall  # noqa: E402


class ScriptedBackend(Backend):
    """用意した結果を順に返すバックエンド。ベンチの配管を通すためのもの。"""

    name = "scripted"

    def __init__(self, results: list[TurnResult]) -> None:
        self.results = list(results)

    def stream_turn(self, *, system, history, tools, sink) -> TurnResult:
        if not self.results:
            return TurnResult(text="終わり", stop_reason=STOP_END_TURN, usage={})
        result = self.results.pop(0)
        if result.text:
            sink.assistant_chunk(result.text)
        return result

    def check_credentials(self) -> str | None:
        return None


class BrokenBackend(Backend):
    """必ず失敗するバックエンド。"""

    name = "broken"

    def stream_turn(self, *, system, history, tools, sink) -> TurnResult:
        raise BackendError("接続できない")

    def check_credentials(self) -> str | None:
        return None


def usage(input_tokens: int = 1_000, output_tokens: int = 200) -> dict[str, int]:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


class SelfCheckTest(unittest.TestCase):
    """検証条件そのものの点検。"""

    def test_全タスクで手つかずは落ち模範解答は通る(self) -> None:
        for key, task in TASKS.items():
            with self.subTest(task=key):
                for result in self_check(task):
                    self.assertTrue(result.passed, f"{result.label}: {result.detail}")


class CheckTest(unittest.TestCase):
    """個々の検証条件の振る舞い。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def ctx(self, answer: str = "") -> RunContext:
        return RunContext(workspace=self.root, answer=answer)

    def test_ファイル一致は余計な差分を弾く(self) -> None:
        (self.root / "a.txt").write_text("本文\n", encoding="utf-8")
        self.assertTrue(FileEquals("a.txt", "本文\n").verify(self.ctx()).passed)
        self.assertFalse(FileEquals("a.txt", "本文\n余計\n").verify(self.ctx()).passed)

    def test_残存チェックは無いファイルを失敗にする(self) -> None:
        result = FileLacks("無い.py", "x").verify(self.ctx())
        self.assertFalse(result.passed)
        self.assertIn("無い", result.detail)

    def test_回答チェック(self) -> None:
        self.assertTrue(AnswerContains("7").verify(self.ctx("答えは 7 行")).passed)
        self.assertFalse(AnswerContains("7").verify(self.ctx("わからない")).passed)

    def test_コマンドの成否を見る(self) -> None:
        self.assertTrue(CommandSucceeds("{python} -c 'pass'").verify(self.ctx()).passed)
        failed = CommandSucceeds("{python} -c 'raise SystemExit(3)'").verify(self.ctx())
        self.assertFalse(failed.passed)

    def test_止まらないコマンドは打ち切る(self) -> None:
        check = CommandSucceeds("{python} -c 'import time; time.sleep(30)'", timeout=1)
        result = check.verify(self.ctx())
        self.assertFalse(result.passed)
        self.assertIn("打ち切り", result.detail)


class RunTaskTest(unittest.TestCase):
    """1タスク分の実行と測定。"""

    def test_解けたら成功として測れる(self) -> None:
        task = TASKS["careful-edit"]
        expected = task.solution["config.ini"]
        backend = ScriptedBackend(
            [
                TurnResult(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="write",
                            arguments={"path": "config.ini", "content": expected},
                        )
                    ],
                    stop_reason=STOP_TOOL_USE,
                    usage=usage(),
                ),
                TurnResult(text="timeout を 90 にした", stop_reason=STOP_END_TURN, usage=usage(500, 50)),
            ]
        )
        outcome = run_task(task, model="opus", backend_factory=lambda config: backend)

        self.assertTrue(outcome.passed)
        self.assertEqual(outcome.status, "成功")
        self.assertEqual(outcome.requests, 2)
        self.assertEqual(outcome.tool_calls, 1)
        self.assertEqual(outcome.input_tokens, 1_500)
        self.assertEqual(outcome.output_tokens, 250)
        self.assertGreater(outcome.cost, 0)
        self.assertIn("90", outcome.answer)

    def test_解けなければ失敗として残る(self) -> None:
        backend = ScriptedBackend(
            [TurnResult(text="やめておく", stop_reason=STOP_END_TURN, usage=usage())]
        )
        outcome = run_task(
            TASKS["careful-edit"], model="opus", backend_factory=lambda config: backend
        )
        self.assertFalse(outcome.passed)
        self.assertEqual(outcome.status, "失敗")
        self.assertEqual(outcome.error, "")
        self.assertTrue(outcome.failed_checks())

    def test_通信エラーは失敗と区別して記録する(self) -> None:
        outcome = run_task(
            TASKS["count-lines"], model="opus", backend_factory=lambda config: BrokenBackend()
        )
        self.assertFalse(outcome.passed)
        self.assertEqual(outcome.status, "エラー")
        self.assertIn("接続できない", outcome.error)

    def test_作業は使い捨てのディレクトリで行う(self) -> None:
        # 成果物を残す指定をしたときだけ、指定先に残る
        backend = ScriptedBackend(
            [
                TurnResult(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="write",
                            arguments={"path": "置き土産.txt", "content": "あとで見る\n"},
                        )
                    ],
                    stop_reason=STOP_TOOL_USE,
                    usage=usage(),
                ),
                TurnResult(text="置いた", stop_reason=STOP_END_TURN, usage=usage()),
            ]
        )
        with tempfile.TemporaryDirectory() as keep:
            run_task(
                TASKS["count-lines"],
                model="opus",
                backend_factory=lambda config: backend,
                keep_workspace=Path(keep),
            )
            left = Path(keep) / "opus-count-lines" / "置き土産.txt"
            self.assertTrue(left.is_file())

    def test_価格未設定のモデルは費用が0になる(self) -> None:
        backend = ScriptedBackend(
            [TurnResult(text="7", stop_reason=STOP_END_TURN, usage=usage())]
        )
        outcome = run_task(
            TASKS["count-lines"], model="deepseek", backend_factory=lambda config: backend
        )
        self.assertTrue(outcome.passed)
        self.assertEqual(outcome.cost, 0.0)
        self.assertGreater(outcome.input_tokens, 0)

    def test_往復が上限に達しても結果を返す(self) -> None:
        # ずっとツールを呼び続けるバックエンド
        class LoopingBackend(Backend):
            name = "looping"

            def __init__(self) -> None:
                self.count = 0

            def stream_turn(self, *, system, history, tools, sink) -> TurnResult:
                self.count += 1
                return TurnResult(
                    tool_calls=[
                        ToolCall(id=f"c{self.count}", name="read", arguments={"path": "notes.md"})
                    ],
                    stop_reason=STOP_TOOL_USE,
                    usage=usage(10, 1),
                )

            def check_credentials(self) -> str | None:
                return None

        looping = LoopingBackend()
        outcome = run_task(
            TASKS["count-lines"],
            model="opus",
            backend_factory=lambda config: looping,
            max_iterations=3,
        )
        self.assertEqual(looping.count, 3)
        self.assertFalse(outcome.passed)


class RunSuiteTest(unittest.TestCase):
    def test_モデルとタスクの組み合わせを全部回す(self) -> None:
        def factory(config):
            return ScriptedBackend(
                [TurnResult(text="7", stop_reason=STOP_END_TURN, usage=usage())]
            )

        tasks = [TASKS["count-lines"], TASKS["careful-edit"]]
        steps: list[str] = []
        outcomes = run_suite(
            tasks,
            ["opus", "haiku"],
            backend_factory=factory,
            progress=steps.append,
        )
        self.assertEqual(len(outcomes), 4)
        self.assertEqual([o.model_key for o in outcomes], ["opus", "opus", "haiku", "haiku"])
        # count-lines は通り、careful-edit は落ちる
        self.assertEqual([o.passed for o in outcomes], [True, False, True, False])
        self.assertTrue(steps)

    def test_繰り返し回数を指定できる(self) -> None:
        def factory(config):
            return ScriptedBackend(
                [TurnResult(text="7", stop_reason=STOP_END_TURN, usage=usage())]
            )

        outcomes = run_suite(
            [TASKS["count-lines"]], ["opus"], repeat=3, backend_factory=factory
        )
        self.assertEqual(len(outcomes), 3)


class ResolveTasksTest(unittest.TestCase):
    def test_未指定なら全部(self) -> None:
        self.assertEqual(len(resolve_tasks(None)), len(TASKS))
        self.assertEqual(len(resolve_tasks("all")), len(TASKS))

    def test_カンマ区切りで選べる(self) -> None:
        selected = resolve_tasks("count-lines, rename")
        self.assertEqual([t.key for t in selected], ["count-lines", "rename"])

    def test_未知のタスクはエラー(self) -> None:
        with self.assertRaises(ValueError):
            resolve_tasks("そんなタスク")


def outcome(model: str, task: str, *, passed: bool, cost: float, elapsed: float) -> TaskOutcome:
    return TaskOutcome(
        task=task,
        task_title=task,
        model_key=model,
        model_label=model.upper(),
        provider="anthropic",
        passed=passed,
        elapsed=elapsed,
        requests=2,
        input_tokens=1_000,
        output_tokens=100,
        cost=cost,
        tool_calls=1,
    )


class TableWidthTest(unittest.TestCase):
    """日本語混じりでも表がずれないこと。"""

    def test_全角は2桁として数える(self) -> None:
        self.assertEqual(report_mod.display_width("モデル"), 6)
        self.assertEqual(report_mod.display_width("opus"), 4)

    def test_詰めたあとの表示幅が揃う(self) -> None:
        left = report_mod.pad("成功", 8)
        right = report_mod.pad("opus", 8)
        self.assertEqual(report_mod.display_width(left), 8)
        self.assertEqual(report_mod.display_width(right), 8)

    def test_見出しと本文の桁が合う(self) -> None:
        text = report_mod.format_text(
            [outcome("opus", "count-lines", passed=True, cost=0.01, elapsed=5.0)]
        )
        lines = text.splitlines()
        header = lines[1]
        body = lines[3]
        self.assertEqual(report_mod.display_width(header), report_mod.display_width(body))


class ReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.outcomes = [
            outcome("opus", "count-lines", passed=True, cost=0.01, elapsed=5.0),
            outcome("opus", "rename", passed=False, cost=0.03, elapsed=15.0),
            outcome("haiku", "count-lines", passed=True, cost=0.001, elapsed=2.0),
            outcome("haiku", "rename", passed=True, cost=0.002, elapsed=4.0),
        ]

    def test_モデルごとに集計する(self) -> None:
        summaries = {s.model_key: s for s in report_mod.summarize(self.outcomes)}
        self.assertEqual(summaries["opus"].passed, 1)
        self.assertEqual(summaries["opus"].runs, 2)
        self.assertAlmostEqual(summaries["opus"].avg_elapsed, 10.0)
        self.assertAlmostEqual(summaries["haiku"].pass_rate, 1.0)

    def test_成功1件あたりの費用を出す(self) -> None:
        summaries = {s.model_key: s for s in report_mod.summarize(self.outcomes)}
        # opus は 0.04 ドル使って1件成功
        self.assertAlmostEqual(summaries["opus"].cost_per_pass, 0.04)
        self.assertAlmostEqual(summaries["haiku"].cost_per_pass, 0.0015)

    def test_1件も通らなければ成功単価は出さない(self) -> None:
        summaries = report_mod.summarize(
            [outcome("opus", "rename", passed=False, cost=0.05, elapsed=9.0)]
        )
        self.assertIsNone(summaries[0].cost_per_pass)

    def test_価格未設定なら費用欄を伏せる(self) -> None:
        summaries = report_mod.summarize(
            [outcome("qwen", "rename", passed=True, cost=0.0, elapsed=9.0)]
        )
        self.assertFalse(summaries[0].priced)
        self.assertIsNone(summaries[0].cost_per_pass)

    def test_テキスト出力に失敗理由が載る(self) -> None:
        failing = outcome("opus", "rename", passed=False, cost=0.03, elapsed=15.0)
        failing.error = "上限に達した"
        text = report_mod.format_text([failing])
        self.assertIn("エラー", text)
        self.assertIn("上限に達した", text)

    def test_markdownが表になっている(self) -> None:
        markdown = report_mod.format_markdown(self.outcomes)
        self.assertIn("| モデル |", markdown)
        self.assertIn("count-lines", markdown)

    def test_jsonが読み戻せる(self) -> None:
        payload = json.loads(report_mod.to_json(self.outcomes, meta={"models": ["opus"]}))
        self.assertEqual(payload["meta"]["models"], ["opus"])
        self.assertEqual(len(payload["runs"]), 4)
        self.assertEqual(len(payload["summary"]), 2)


class BenchCliTest(unittest.TestCase):
    def test_listはタスクを出して終わる(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            code = bench_main(["--list"])
        self.assertEqual(code, 0)
        self.assertIn("count-lines", captured.getvalue())

    def test_self_checkは通信せずに0で終わる(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            code = bench_main(["--self-check"])
        self.assertEqual(code, 0)
        self.assertIn("検証条件は機能している", captured.getvalue())

    def test_未知のタスクは終了コード2(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            code = bench_main(["--tasks", "そんなの"])
        self.assertEqual(code, 2)
        self.assertIn("未知のタスク", captured.getvalue())

    def test_モデル指定が空なら終了コード2(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bench_main(["--models", " , "]), 2)


if __name__ == "__main__":
    unittest.main()
