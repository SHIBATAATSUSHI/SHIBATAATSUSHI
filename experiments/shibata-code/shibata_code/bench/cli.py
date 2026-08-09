"""ベンチの入り口。`python3 -m shibata_code.bench` で動く。

使い方の例:

    # 検証条件そのものが機能しているか確かめる(APIを叩かない)
    python3 -m shibata_code.bench --self-check

    # 1モデルで全タスク
    python3 -m shibata_code.bench --models opus

    # 3モデルを比べて記録を残す
    python3 -m shibata_code.bench --models opus,sonnet,haiku \\
        --json bench.json --markdown bench.md
"""

from __future__ import annotations

import argparse
import platform
import sys
import time
from pathlib import Path

from ..config import resolve_model
from . import report as report_mod
from .runner import DEFAULT_MAX_ITERATIONS, run_suite, self_check
from .tasks import DEFAULT_ORDER, TASKS, resolve_tasks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shibata-code-bench",
        description="Shibata Code のモデル比較ベンチ(速さ・費用・出来を実測する)",
    )
    parser.add_argument(
        "--models",
        default="opus",
        help="比べるモデルをカンマ区切りで(既定: opus)",
    )
    parser.add_argument(
        "--tasks",
        default="all",
        help=f"走らせるタスク(既定: all)。使えるのは {', '.join(DEFAULT_ORDER)}",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="同じ組み合わせを何回走らせるか(既定: 1)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=f"1タスクで許すツール往復の上限(既定: {DEFAULT_MAX_ITERATIONS})",
    )
    parser.add_argument("--transport", default="auto", help="通信経路(auto/sdk/http)")
    parser.add_argument("--base-url", default=None, help="接続先を上書きする")
    parser.add_argument("-e", "--effort", default="high", help="思考量(low〜max)")
    parser.add_argument("--json", dest="json_path", default=None, help="結果をJSONで保存する")
    parser.add_argument(
        "--markdown", dest="markdown_path", default=None, help="結果をMarkdownで保存する"
    )
    parser.add_argument(
        "--keep",
        default=None,
        help="終わったあとの作業ディレクトリをこの場所へ残す(結果の目視確認用)",
    )
    parser.add_argument("--list", action="store_true", help="タスク一覧を出して終了")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="検証条件そのものを点検して終了(APIを叩かない)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="実行中の様子を表示する")
    return parser


def print_tasks() -> None:
    print("タスク一覧:")
    for key in DEFAULT_ORDER:
        task = TASKS[key]
        skills = ", ".join(task.skills) if task.skills else "-"
        print(f"  {key:<14} {task.title}  [{skills}]")


def run_self_check() -> int:
    """検証条件の点検。すべて通れば 0。"""
    failures = 0
    for key in DEFAULT_ORDER:
        for result in self_check(TASKS[key]):
            mark = "✓" if result.passed else "✗"
            detail = f" — {result.detail}" if result.detail and not result.passed else ""
            print(f"{mark} {result.label}{detail}")
            if not result.passed:
                failures += 1
    print()
    if failures:
        print(f"点検で {failures} 件落ちた。検証条件かタスク定義を直すこと")
        return 1
    print("検証条件は機能している(手つかずでは落ち、模範解答では通る)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list:
        print_tasks()
        return 0

    if args.self_check:
        return run_self_check()

    try:
        tasks = resolve_tasks(args.tasks)
    except ValueError as exc:
        print(f"✗ {exc}")
        return 2

    models = [name.strip() for name in args.models.split(",") if name.strip()]
    if not models:
        print("✗ モデルが指定されていない")
        return 2

    # 走らせる前にモデル指定の誤りを潰す。30分回してから落ちるのは無駄なので。
    for name in models:
        try:
            resolve_model(name)
        except ValueError as exc:
            print(f"✗ {exc}")
            return 2

    keep = Path(args.keep).expanduser().resolve() if args.keep else None

    started = time.time()
    try:
        outcomes = run_suite(
            tasks,
            models,
            repeat=max(1, args.repeat),
            progress=print,
            verbose=args.verbose,
            keep_workspace=keep,
            max_iterations=args.max_iterations,
            transport=args.transport,
            base_url=args.base_url,
            effort=args.effort,
        )
    except KeyboardInterrupt:
        print("\n中断した")
        return 130

    print()
    print(report_mod.format_text(outcomes))

    meta = {
        "started_at": started,
        "finished_at": time.time(),
        "models": models,
        "tasks": [task.key for task in tasks],
        "repeat": max(1, args.repeat),
        "effort": args.effort,
        "transport": args.transport,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }

    if args.json_path:
        path = Path(args.json_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report_mod.to_json(outcomes, meta=meta), encoding="utf-8")
        print(f"\nJSON を書き出した: {path}")

    if args.markdown_path:
        path = Path(args.markdown_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report_mod.format_markdown(outcomes), encoding="utf-8")
        print(f"Markdown を書き出した: {path}")

    # 1件でも落ちていれば非0で返す(CIから回せるように)
    return 0 if all(outcome.passed for outcome in outcomes) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
