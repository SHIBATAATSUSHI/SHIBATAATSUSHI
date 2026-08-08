"""Mythos のコマンドラインインターフェース。

対話モード(REPL)と、1発実行の `--print` モードを提供する。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .agent import Agent, AgentError
from .config import (
    EFFORT_LEVELS,
    MODEL_PRESETS,
    PERMISSION_MODES,
    THINKING_DISPLAYS,
    Config,
    build_config,
)
from .session import Session
from .tools import Toolbox
from .ui import UI
from .workspace import Workspace, WorkspaceError

HELP_TEXT = """\
使えるコマンド:
  /help              このヘルプ
  /model [名前]      モデルを表示・変更 (fable / mythos / opus / sonnet / haiku / 任意のID)
  /effort [段階]     effort を表示・変更 (low / medium / high / xhigh / max)
  /mode [モード]     権限モードを表示・変更 (ask / auto / yolo)
  /max-tokens [N]    1リクエストの出力上限を表示・変更
  /tools             利用できるツールの一覧
  /cost              このセッションのトークン利用量と概算コスト
  /clear             会話履歴を消す(設定と利用量は残る)
  /save              セッションを .mythos/sessions/ に保存
  /exit              終了 (Ctrl-D でも同じ)

そのほかの入力はすべてエージェントへの指示として扱う。"""


def build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数の定義。"""
    parser = argparse.ArgumentParser(
        prog="mythos",
        description="端末で動くミニ・コーディングエージェント",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="指示。指定すると1回だけ実行して終了する",
    )
    parser.add_argument(
        "-p",
        "--print",
        dest="print_mode",
        action="store_true",
        help="本文だけを出力する(利用量の行を出さない)。パイプ向け",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=os.environ.get("MYTHOS_MODEL"),
        help=f"モデル。短縮名: {', '.join(MODEL_PRESETS)}(既定: opus)",
    )
    parser.add_argument(
        "-C",
        "--workspace",
        default=None,
        help="作業ディレクトリ(既定: カレントディレクトリ)",
    )
    parser.add_argument(
        "-e",
        "--effort",
        default=os.environ.get("MYTHOS_EFFORT", "high"),
        choices=EFFORT_LEVELS,
        help="思考と作業の深さ(既定: high)",
    )
    parser.add_argument(
        "--mode",
        default=os.environ.get("MYTHOS_MODE", "ask"),
        choices=PERMISSION_MODES,
        help="権限モード(既定: ask)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="1リクエストの出力上限トークン(既定: 32000)",
    )
    parser.add_argument(
        "--thinking",
        default="summarized",
        choices=THINKING_DISPLAYS,
        help="思考の表示(既定: summarized)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="この作業ディレクトリで最後に保存したセッションを再開する",
    )
    parser.add_argument("--no-color", action="store_true", help="色を使わない")
    parser.add_argument("--version", action="version", version=f"mythos {__version__}")
    return parser


def compose_prompt(prompt_args: list[str], piped: str) -> str:
    """引数で渡された指示と、標準入力の内容を1つの依頼にまとめる。

    `git diff | mythos -p "この差分をレビューして"` のように、
    指示と対象データを別々の経路で受け取れるようにするため。
    """
    instruction = " ".join(prompt_args).strip()
    body = (piped or "").strip()
    if instruction and body:
        return f"{instruction}\n\n{body}"
    return instruction or body


def _make_client(ui: UI):
    """Anthropic クライアントを作る。認証情報が無ければ分かるように落とす。"""
    try:
        import anthropic
    except ImportError:  # pragma: no cover - 依存が無い環境向け
        ui.error("anthropic SDK が入っていない。`pip install anthropic` を実行すること")
        raise SystemExit(1)

    try:
        client = anthropic.Anthropic()
    except Exception as exc:  # 認証情報が見つからない等
        ui.error(f"Anthropic クライアントを作れなかった: {exc}")
        ui.info("ANTHROPIC_API_KEY を設定するか、`ant auth login` で認証すること")
        raise SystemExit(1)

    # SDK は認証情報が無くてもクライアント生成自体は成功し、最初のリクエストで
    # 落ちる。プロファイル認証の可能性もあるので、ここでは警告に留める。
    if not getattr(client, "api_key", None) and not getattr(client, "auth_token", None):
        ui.warn("APIキーが見つからない。ANTHROPIC_API_KEY か `ant auth login` が必要かもしれない")
    return client


class Repl:
    """対話ループとスラッシュコマンドの処理。"""

    def __init__(self, agent: Agent, ui: UI) -> None:
        self.agent = agent
        self.ui = ui

    @property
    def config(self) -> Config:
        return self.agent.config

    def run(self) -> int:
        """対話ループを回す。終了コードを返す。"""
        cfg = self.config
        self.ui.banner(
            "Mythos",
            f"{cfg.model.label} / effort={cfg.effort} / {cfg.permission_mode} モード"
            f" / {cfg.workspace}",
        )
        self.ui.info("/help でコマンド一覧。Ctrl-D で終了。")

        while True:
            try:
                raw = self.ui.prompt()
            except (EOFError, KeyboardInterrupt):
                self.ui.line()
                self.ui.info("終了する。")
                return 0

            text = raw.strip()
            if not text:
                continue
            if text.startswith("/"):
                if self.handle_command(text):
                    return 0
                continue

            try:
                self.agent.run_turn(text)
            except KeyboardInterrupt:
                self.ui.ensure_newline()
                self.ui.warn("中断した。")
            except AgentError as exc:
                self.ui.error(str(exc))

    def handle_command(self, text: str) -> bool:
        """スラッシュコマンドを処理する。終了すべきときに True を返す。"""
        parts = text.split()
        command = parts[0].lower()
        arg = " ".join(parts[1:]).strip()

        if command in {"/exit", "/quit"}:
            self.ui.info("終了する。")
            return True

        if command == "/help":
            for line in HELP_TEXT.splitlines():
                self.ui.info(line)
            return False

        if command == "/model":
            if not arg:
                self.ui.notice(f"現在のモデル: {self.config.model.label} ({self.config.model.id})")
                self.ui.info("短縮名: " + ", ".join(MODEL_PRESETS))
                return False
            try:
                self.agent.config = self.config.with_model(arg)
            except ValueError as exc:
                self.ui.error(str(exc))
                return False
            self.ui.notice(
                f"モデルを {self.config.model.label} に変更した"
                f"(出力上限 {self.config.max_tokens:,})"
            )
            self.ui.info("※ モデルを変えるとプロンプトキャッシュは作り直しになる")
            return False

        if command == "/effort":
            if not arg:
                self.ui.notice(f"現在の effort: {self.config.effort}")
                return False
            try:
                self.agent.config = self.config.with_effort(arg)
            except ValueError as exc:
                self.ui.error(str(exc))
                return False
            self.ui.notice(f"effort を {self.config.effort} に変更した")
            return False

        if command == "/mode":
            if not arg:
                self.ui.notice(f"現在の権限モード: {self.config.permission_mode}")
                return False
            try:
                self.agent.config = self.config.with_permission_mode(arg)
            except ValueError as exc:
                self.ui.error(str(exc))
                return False
            self.agent.gate.mode = self.config.permission_mode
            self.ui.notice(f"権限モードを {self.config.permission_mode} に変更した")
            return False

        if command == "/max-tokens":
            if not arg:
                self.ui.notice(f"現在の出力上限: {self.config.max_tokens:,}")
                return False
            try:
                value = int(arg)
            except ValueError:
                self.ui.error("整数を指定すること")
                return False
            limit = self.config.model.max_output
            self.agent.config = replace(self.config, max_tokens=max(1, min(value, limit)))
            self.ui.notice(f"出力上限を {self.config.max_tokens:,} にした(上限 {limit:,})")
            return False

        if command == "/tools":
            for spec in self.agent.toolbox.specs:
                kind = "読み取り" if spec.read_only else "変更あり"
                self.ui.info(f"  {spec.name:<6} [{kind}] {spec.description.splitlines()[0][:60]}")
            return False

        if command == "/cost":
            self.ui.notice(self.agent.session.usage.summary(self.config.model))
            return False

        if command == "/clear":
            self.agent.session.clear()
            self.ui.notice("会話履歴を消した。")
            return False

        if command == "/save":
            path = self.agent.session.save()
            self.ui.notice(f"保存した: {path}")
            return False

        self.ui.error(f"未知のコマンド: {command}(/help で一覧)")
        return False


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。"""
    args = build_parser().parse_args(argv)
    ui = UI(color=not args.no_color)

    try:
        config = build_config(
            model=args.model,
            workspace=args.workspace,
            effort=args.effort,
            max_tokens=args.max_tokens,
            thinking_display=args.thinking,
            permission_mode=args.mode,
            color=not args.no_color,
        )
    except ValueError as exc:
        ui.error(str(exc))
        return 2

    try:
        workspace = Workspace(config.workspace)
    except WorkspaceError as exc:
        ui.error(str(exc))
        return 2

    session: Session | None = None
    if args.resume:
        session = Session.latest(config.workspace)
        if session is None:
            ui.warn("再開できるセッションが無いので、新しく始める。")
        else:
            # 保存元と別の作業ディレクトリで再開されても、保存先は今の設定に合わせる。
            session.workspace = config.workspace
            ui.info(f"セッション {session.session_id} を再開する({len(session.messages)} メッセージ)")
    if session is None:
        session = Session(workspace=config.workspace)

    client = _make_client(ui)
    toolbox = Toolbox(workspace, bash_timeout=config.bash_timeout)
    agent = Agent(client=client, config=config, toolbox=toolbox, session=session, ui=ui)

    # 標準入力がパイプされていれば、指示と合わせて1つの依頼にする。
    piped = "" if sys.stdin.isatty() else sys.stdin.read()
    prompt = compose_prompt(args.prompt, piped)

    if prompt:
        try:
            agent.run_turn(prompt)
        except KeyboardInterrupt:
            ui.ensure_newline()
            ui.warn("中断した。")
            return 130
        except AgentError as exc:
            ui.error(str(exc))
            return 1
        ui.ensure_newline()
        if not args.print_mode:
            # -p のときは後続処理へ渡しやすいよう、本文以外を出さない。
            ui.usage(session.usage.summary(config.model))
        # 指示を引数で受け取ったときは1回実行して終わる(対話モードには入らない)。
        session.save()
        return 0

    if not sys.stdin.isatty():
        ui.error("対話モードには端末が必要。指示を引数で渡すか -p を使うこと")
        return 2

    code = Repl(agent, ui).run()
    session.save()
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
