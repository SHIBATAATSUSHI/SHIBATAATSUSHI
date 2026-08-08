"""Shibata Code のコマンドラインインターフェース。

対話モード(REPL)と、1発実行のモードを提供する。
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
    TRANSPORTS,
    Config,
    build_config,
)
from .providers import PROVIDERS
from .session import Session
from .tools import Toolbox
from .ui import UI
from .workspace import Workspace, WorkspaceError

HELP_TEXT = """\
使えるコマンド:
  /help              このヘルプ
  /model [名前]      モデルを表示・変更(短縮名、または プロバイダ:モデルID)
  /models            使えるモデルの短縮名一覧
  /providers         プロバイダと必要な環境変数の一覧
  /effort [段階]     effort を表示・変更 (low / medium / high / xhigh / max)
  /mode [モード]     権限モードを表示・変更 (ask / auto / yolo)
  /max-tokens [N]    1リクエストの出力上限を表示・変更
  /tools             利用できるツールの一覧
  /cost              このセッションのトークン利用量と概算コスト
  /clear             会話履歴を消す(設定と利用量は残る)
  /save              セッションを .shibata-code/sessions/ に保存
  /exit              終了 (Ctrl-D でも同じ)

そのほかの入力はすべてエージェントへの指示として扱う。
会話の途中で /model を変えても、履歴は引き継がれる。"""


def build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数の定義。"""
    parser = argparse.ArgumentParser(
        prog="shibata-code",
        description="端末で動くコーディングエージェント(複数モデル対応)",
    )
    parser.add_argument(
        "prompt", nargs="*", help="指示。指定すると1回だけ実行して終了する"
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
        default=os.environ.get("SHIBATA_CODE_MODEL"),
        help=(
            "モデル。短縮名(" + ", ".join(list(MODEL_PRESETS)[:6]) + " ほか)、"
            "または プロバイダ:モデルID。既定は opus"
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI 互換の接続先を上書きする(ローカルモデル等)",
    )
    parser.add_argument(
        "-C", "--workspace", default=None, help="作業ディレクトリ(既定: カレント)"
    )
    parser.add_argument(
        "-e",
        "--effort",
        default=os.environ.get("SHIBATA_CODE_EFFORT", "high"),
        choices=EFFORT_LEVELS,
        help="思考と作業の深さ(既定: high)",
    )
    parser.add_argument(
        "--mode",
        default=os.environ.get("SHIBATA_CODE_MODE", "ask"),
        choices=PERMISSION_MODES,
        help="権限モード(既定: ask)",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=None, help="1リクエストの出力上限(既定 32000)"
    )
    parser.add_argument(
        "--thinking",
        default=None,
        choices=THINKING_DISPLAYS,
        help="思考の表示(既定: summarized、--compact 時は omitted)",
    )
    parser.add_argument(
        "--reasoning-effort",
        action="store_true",
        help="OpenAI 互換モデルにも reasoning_effort を送る",
    )
    parser.add_argument(
        "--transport",
        default=os.environ.get("SHIBATA_CODE_TRANSPORT", "auto"),
        choices=TRANSPORTS,
        help=(
            "通信経路。auto(既定)は SDK があればそれを使う。"
            "http は SDK 不要で標準ライブラリだけで動く(iOS 等)"
        ),
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        default=bool(os.environ.get("SHIBATA_CODE_COMPACT")),
        help="出力を絞る(携帯など細い画面向け。思考を隠し、プレビューを短くする)",
    )
    parser.add_argument(
        "--width", type=int, default=None, help="端末幅を明示する(自動検出の上書き)"
    )
    parser.add_argument(
        "--resume", action="store_true", help="最後に保存したセッションを再開する"
    )
    parser.add_argument("--models", action="store_true", help="モデル短縮名の一覧を出して終了")
    parser.add_argument("--no-color", action="store_true", help="色を使わない")
    parser.add_argument("--version", action="version", version=f"shibata-code {__version__}")
    return parser


def compose_prompt(prompt_args: list[str], piped: str) -> str:
    """引数で渡された指示と、標準入力の内容を1つの依頼にまとめる。"""
    instruction = " ".join(prompt_args).strip()
    body = (piped or "").strip()
    if instruction and body:
        return f"{instruction}\n\n{body}"
    return instruction or body


def print_models(ui: UI) -> None:
    """モデル短縮名の一覧を出す。"""
    ui.notice("短縮名 → プロバイダ:モデルID")
    for key, spec in MODEL_PRESETS.items():
        price = (
            f"${spec.input_price:g}/${spec.output_price:g}" if spec.has_pricing else "価格未設定"
        )
        ui.info(f"  {key:<18} {spec.provider}:{spec.id}  ({price})")
    ui.info("")
    ui.info("一覧に無いモデルは プロバイダ:モデルID で直接指定できる。")
    ui.info("  例: --model openrouter:z-ai/glm-5.2 / --model deepseek:deepseek-reasoner")


def print_providers(ui: UI) -> None:
    """プロバイダと必要な環境変数を出す。"""
    ui.notice("プロバイダ / 必要な環境変数 / 接続先")
    for spec in PROVIDERS.values():
        got = "設定済" if spec.find_api_key() else "未設定"
        ui.info(f"  {spec.key:<11} {spec.env_hint():<38} [{got}]")
        if spec.base_url:
            ui.info(f"              {spec.base_url}")


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
            "Shibata Code",
            f"{cfg.model.display()} / effort={cfg.effort} / {cfg.permission_mode} モード"
            f" / {cfg.workspace}",
        )
        warning = self.agent.check_credentials()
        if warning:
            self.ui.warn(warning)
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

        if command == "/models":
            print_models(self.ui)
            return False

        if command == "/providers":
            print_providers(self.ui)
            return False

        if command == "/model":
            if not arg:
                self.ui.notice(f"現在のモデル: {self.config.model.display()}")
                self.ui.info("/models で一覧。プロバイダ:モデルID でも指定できる。")
                return False
            try:
                self.agent.config = self.config.with_model(arg)
            except ValueError as exc:
                self.ui.error(str(exc))
                return False
            # 設定が変わったのでバックエンドを作り直させる
            self.agent.set_backend(None)
            self.ui.notice(
                f"モデルを {self.config.model.display()} に変更した"
                f"(出力上限 {self.config.max_tokens:,})"
            )
            warning = self.agent.check_credentials()
            if warning:
                self.ui.warn(warning)
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
            self.agent.set_backend(None)
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
            self.agent.set_backend(None)
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
            self.agent.set_backend(None)
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
    ui = UI(color=not args.no_color, width=args.width, compact=args.compact)

    if args.models:
        print_models(ui)
        return 0

    try:
        config = build_config(
            model=args.model,
            workspace=args.workspace,
            effort=args.effort,
            max_tokens=args.max_tokens,
            # compact では思考を表示しないので、取り寄せないほうが安く済む
            thinking_display=args.thinking or ("omitted" if args.compact else "summarized"),
            permission_mode=args.mode,
            color=not args.no_color,
            base_url=args.base_url,
            force_reasoning_effort=args.reasoning_effort,
            transport=args.transport,
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
            ui.info(
                f"セッション {session.session_id} を再開する({len(session.messages)} メッセージ)"
            )
    if session is None:
        session = Session(workspace=config.workspace)

    toolbox = Toolbox(workspace, bash_timeout=config.bash_timeout)
    agent = Agent(config=config, toolbox=toolbox, session=session, ui=ui)

    piped = "" if sys.stdin.isatty() else sys.stdin.read()
    prompt = compose_prompt(args.prompt, piped)

    if prompt:
        warning = agent.check_credentials()
        if warning:
            ui.warn(warning)
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
            ui.usage(session.usage.summary(config.model))
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
