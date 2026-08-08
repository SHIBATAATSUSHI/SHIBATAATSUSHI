"""Shibata Code がモデルに渡すツール群の定義と実行。

ツールの説明文は「何をするか」だけでなく「いつ呼ぶか」まで書く。
最近のモデルは説明に書かれた呼び出し条件をよく読むので、
ここが曖昧だと呼ばれるべき場面で呼ばれない。
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .workspace import Workspace, WorkspaceError

# 1回のツール結果としてモデルに返す最大文字数。
# これを超える場合は切り詰めて、続きの取り方を書き添える。
MAX_RESULT_CHARS = 30_000
MAX_READ_LINES = 2_000
MAX_GLOB_RESULTS = 200
MAX_GREP_RESULTS = 200

# 検索・一覧から常に除外するディレクトリ。
IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".shibata_code",
}


class ToolError(Exception):
    """ツール実行の失敗。モデルには is_error 付きの結果として返す。"""


@dataclass(frozen=True)
class ToolSpec:
    """1つのツールの定義と実装。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]
    # 読み取り専用なら権限確認をスキップできる
    read_only: bool
    # 端末に出す1行サマリの作り方
    summarize: Callable[[dict[str, Any]], str]

    def to_anthropic(self) -> dict[str, Any]:
        """Anthropic Messages API に渡す形へ変換する。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_openai(self) -> dict[str, Any]:
        """OpenAI 互換の tools 形式へ変換する。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


def _truncate(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    """長すぎる結果を切り詰め、切り詰めた旨を明記する。"""
    if len(text) <= limit:
        return text
    return (
        text[:limit]
        + f"\n\n[... {len(text) - limit} 文字を省略。範囲を絞って読み直すこと ...]"
    )


def _is_probably_binary(path: Path) -> bool:
    """先頭ブロックにNULバイトがあればバイナリとみなす。"""
    try:
        with path.open("rb") as handle:
            chunk = handle.read(4096)
    except OSError:
        return False
    return b"\x00" in chunk


def _iter_files(root: Path) -> list[Path]:
    """無視ディレクトリを除いて、配下のファイルを列挙する。"""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".git")]
        for name in filenames:
            found.append(Path(dirpath) / name)
    return found


class Toolbox:
    """ツールの登録・スキーマ提供・実行を担う。"""

    def __init__(self, workspace: Workspace, *, bash_timeout: int = 120) -> None:
        self.workspace = workspace
        self.bash_timeout = bash_timeout
        # read したファイルの更新時刻を覚えておき、編集時の陳腐化を検出する。
        self._read_state: dict[Path, float] = {}
        self._specs: dict[str, ToolSpec] = {}
        self._register_all()

    # ------------------------------------------------------------------
    # 登録・参照
    # ------------------------------------------------------------------
    def _register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec

    @property
    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def get(self, name: str) -> ToolSpec:
        if name not in self._specs:
            raise ToolError(f"未知のツール: {name}")
        return self._specs[name]

    def api_schemas(self) -> list[dict[str, Any]]:
        """Anthropic の tools パラメータに渡すリスト。

        並びを固定しておかないとプロンプトキャッシュが無効化されるため、
        登録順(=決定的)をそのまま使う。
        """
        return [spec.to_anthropic() for spec in self._specs.values()]

    def run(self, name: str, payload: dict[str, Any]) -> str:
        """ツールを実行して結果文字列を返す。失敗時は ToolError。"""
        spec = self.get(name)
        try:
            return spec.handler(payload or {})
        except WorkspaceError as exc:
            raise ToolError(str(exc)) from exc

    # ------------------------------------------------------------------
    # ツール実装
    # ------------------------------------------------------------------
    def _register_all(self) -> None:
        self._register(
            ToolSpec(
                name="read",
                description=(
                    "作業ディレクトリ内のファイルを行番号付きで読む。"
                    "ファイルの中身を知る必要があるときは、推測せず必ずこれを呼ぶ。"
                    "既存ファイルを edit で書き換える前にも一度読むこと。"
                    "大きなファイルは offset と limit で範囲を絞れる。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "作業ディレクトリからの相対パス、または絶対パス",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "読み始める行番号(1始まり)。省略時は先頭から",
                        },
                        "limit": {
                            "type": "integer",
                            "description": f"読む行数。既定 {MAX_READ_LINES}",
                        },
                    },
                    "required": ["path"],
                },
                handler=self._read,
                read_only=True,
                summarize=lambda p: str(p.get("path", "")),
            )
        )

        self._register(
            ToolSpec(
                name="write",
                description=(
                    "ファイルを新規作成する、または全文を置き換える。"
                    "新しいファイルを作るときに使う。"
                    "既存ファイルの一部を直すだけなら write ではなく edit を使うこと。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "書き込み先のパス"},
                        "content": {"type": "string", "description": "ファイルの全内容"},
                    },
                    "required": ["path", "content"],
                },
                handler=self._write,
                read_only=False,
                summarize=lambda p: str(p.get("path", "")),
            )
        )

        self._register(
            ToolSpec(
                name="edit",
                description=(
                    "既存ファイル内の文字列を厳密一致で置換する。"
                    "ファイルの一部を修正するときは write ではなくこれを使う。"
                    "old_string はファイル内で一意になるだけの前後行を含めること。"
                    "一致が0件または複数件なら失敗する(replace_all を指定した場合を除く)。"
                    "呼ぶ前に必ず read でそのファイルを読んでおくこと。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "編集するファイルのパス"},
                        "old_string": {
                            "type": "string",
                            "description": "置換対象。インデントを含め完全一致させる",
                        },
                        "new_string": {"type": "string", "description": "置換後の文字列"},
                        "replace_all": {
                            "type": "boolean",
                            "description": "true なら一致箇所をすべて置換する。既定 false",
                        },
                    },
                    "required": ["path", "old_string", "new_string"],
                },
                handler=self._edit,
                read_only=False,
                summarize=lambda p: str(p.get("path", "")),
            )
        )

        self._register(
            ToolSpec(
                name="glob",
                description=(
                    "ファイル名のパターンでファイルを探す(例: '**/*.py')。"
                    "どこにファイルがあるか分からないときの最初の一手。"
                    "中身を検索したいときは glob ではなく grep を使う。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "glob パターン。'**' で再帰",
                        },
                        "path": {
                            "type": "string",
                            "description": "検索の起点ディレクトリ。省略時は作業ディレクトリ",
                        },
                    },
                    "required": ["pattern"],
                },
                handler=self._glob,
                read_only=True,
                summarize=lambda p: str(p.get("pattern", "")),
            )
        )

        self._register(
            ToolSpec(
                name="grep",
                description=(
                    "ファイルの中身を正規表現で検索する。"
                    "関数名・変数名・エラーメッセージなど、コードのどこかにある文字列を"
                    "探すときは推測せずこれを呼ぶ。"
                    "output_mode で一致行そのもの / ファイル名のみ / 件数を切り替えられる。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "正規表現"},
                        "path": {
                            "type": "string",
                            "description": "検索対象のディレクトリまたはファイル。省略時は作業ディレクトリ",
                        },
                        "glob": {
                            "type": "string",
                            "description": "対象を絞るファイル名パターン(例: '*.py')",
                        },
                        "output_mode": {
                            "type": "string",
                            "enum": ["content", "files_with_matches", "count"],
                            "description": "既定は files_with_matches",
                        },
                        "case_insensitive": {
                            "type": "boolean",
                            "description": "true で大文字小文字を無視する",
                        },
                    },
                    "required": ["pattern"],
                },
                handler=self._grep,
                read_only=True,
                summarize=lambda p: str(p.get("pattern", "")),
            )
        )

        self._register(
            ToolSpec(
                name="bash",
                description=(
                    "作業ディレクトリでシェルコマンドを実行し、標準出力と標準エラーを返す。"
                    "テスト実行・ビルド・git 操作・パッケージ導入など、"
                    "他のツールで表現できない操作に使う。"
                    "ファイルの読み書き・検索は cat/sed/grep ではなく "
                    "read / write / edit / grep を使うこと。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "実行するコマンド"},
                        "timeout": {
                            "type": "integer",
                            "description": "タイムアウト秒。省略時は既定値",
                        },
                    },
                    "required": ["command"],
                },
                handler=self._bash,
                read_only=False,
                summarize=lambda p: str(p.get("command", ""))[:120],
            )
        )

    # -- read ----------------------------------------------------------
    def _read(self, payload: dict[str, Any]) -> str:
        path = self.workspace.resolve(str(payload.get("path", "")))
        if path.is_dir():
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
            listing = "\n".join(entries[:MAX_GLOB_RESULTS])
            return f"{self.workspace.display(path)} はディレクトリ:\n{listing}"
        if not path.exists():
            raise ToolError(f"ファイルが存在しない: {self.workspace.display(path)}")
        if _is_probably_binary(path):
            size = path.stat().st_size
            raise ToolError(
                f"バイナリファイルのため読めない: {self.workspace.display(path)} ({size} bytes)"
            )

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ToolError(f"読み込みに失敗: {exc}") from exc

        # 読んだ時点の更新時刻を記録し、edit 時の陳腐化検出に使う。
        self._read_state[path] = path.stat().st_mtime

        lines = text.splitlines()
        offset = max(1, int(payload.get("offset") or 1))
        limit = int(payload.get("limit") or MAX_READ_LINES)
        limit = max(1, min(limit, MAX_READ_LINES))
        window = lines[offset - 1 : offset - 1 + limit]

        if not window:
            return f"(空、または offset={offset} が行数 {len(lines)} を超えている)"

        numbered = "\n".join(
            f"{offset + i:>6}\t{line}" for i, line in enumerate(window)
        )
        footer = ""
        end = offset - 1 + len(window)
        if end < len(lines):
            footer = f"\n\n[{end + 1} 行目以降は省略。全 {len(lines)} 行]"
        return _truncate(numbered + footer)

    # -- write ---------------------------------------------------------
    def _write(self, payload: dict[str, Any]) -> str:
        path = self.workspace.resolve(str(payload.get("path", "")))
        content = payload.get("content")
        if content is None:
            raise ToolError("content が指定されていない")
        if path.is_dir():
            raise ToolError(f"ディレクトリには書き込めない: {self.workspace.display(path)}")

        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(str(content), encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"書き込みに失敗: {exc}") from exc

        self._read_state[path] = path.stat().st_mtime
        verb = "上書き" if existed else "作成"
        lines = str(content).count("\n") + 1
        return f"{self.workspace.display(path)} を{verb}した({lines} 行)"

    # -- edit ----------------------------------------------------------
    def _edit(self, payload: dict[str, Any]) -> str:
        path = self.workspace.resolve(str(payload.get("path", "")))
        old = payload.get("old_string")
        new = payload.get("new_string")
        replace_all = bool(payload.get("replace_all", False))

        if old is None or new is None:
            raise ToolError("old_string と new_string の両方が必要")
        if old == new:
            raise ToolError("old_string と new_string が同一")
        if not path.exists():
            raise ToolError(f"ファイルが存在しない: {self.workspace.display(path)}")

        mtime = path.stat().st_mtime
        known = self._read_state.get(path)
        if known is None:
            raise ToolError(
                f"編集の前に read が必要: {self.workspace.display(path)}"
            )
        if known < mtime:
            raise ToolError(
                f"read 以降にファイルが変更されている: {self.workspace.display(path)}。"
                "read で読み直してから編集し直すこと"
            )

        text = path.read_text(encoding="utf-8")
        occurrences = text.count(str(old))
        if occurrences == 0:
            raise ToolError(
                "old_string がファイル内に見つからない。"
                "行番号の接頭辞を含めていないか、インデントが一致しているか確認すること"
            )
        if occurrences > 1 and not replace_all:
            raise ToolError(
                f"old_string が {occurrences} 箇所に一致した。"
                "前後の行を足して一意にするか、replace_all を指定すること"
            )

        updated = text.replace(str(old), str(new)) if replace_all else text.replace(str(old), str(new), 1)
        path.write_text(updated, encoding="utf-8")
        self._read_state[path] = path.stat().st_mtime

        count = occurrences if replace_all else 1
        return f"{self.workspace.display(path)} を編集した({count} 箇所を置換)"

    # -- glob ----------------------------------------------------------
    def _glob(self, payload: dict[str, Any]) -> str:
        pattern = str(payload.get("pattern", "")).strip()
        if not pattern:
            raise ToolError("pattern が空")
        base = self.workspace.resolve(str(payload.get("path") or "."))
        if not base.is_dir():
            raise ToolError(f"ディレクトリではない: {self.workspace.display(base)}")

        matches: list[Path] = []
        for candidate in base.glob(pattern):
            if not candidate.is_file():
                continue
            if any(part in IGNORED_DIRS for part in candidate.parts):
                continue
            matches.append(candidate)

        if not matches:
            return f"'{pattern}' に一致するファイルはなかった"

        # 更新が新しい順のほうが、作業中のファイルを見つけやすい。
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        shown = matches[:MAX_GLOB_RESULTS]
        body = "\n".join(self.workspace.display(p) for p in shown)
        if len(matches) > len(shown):
            body += f"\n[... 他 {len(matches) - len(shown)} 件]"
        return _truncate(body)

    # -- grep ----------------------------------------------------------
    def _grep(self, payload: dict[str, Any]) -> str:
        pattern = str(payload.get("pattern", ""))
        if not pattern:
            raise ToolError("pattern が空")
        target = self.workspace.resolve(str(payload.get("path") or "."))
        mode = str(payload.get("output_mode") or "files_with_matches")
        if mode not in {"content", "files_with_matches", "count"}:
            raise ToolError(f"未知の output_mode: {mode}")
        glob_filter = payload.get("glob")
        ignore_case = bool(payload.get("case_insensitive", False))

        if shutil.which("rg"):
            result = self._grep_with_rg(pattern, target, mode, glob_filter, ignore_case)
            if result is not None:
                return result
        return self._grep_with_python(pattern, target, mode, glob_filter, ignore_case)

    def _grep_with_rg(
        self,
        pattern: str,
        target: Path,
        mode: str,
        glob_filter: str | None,
        ignore_case: bool,
    ) -> str | None:
        """ripgrep があればそれを使う。異常終了したら None を返して自前実装に落とす。"""
        args = ["rg", "--no-heading", "--color", "never"]
        if mode == "files_with_matches":
            args.append("--files-with-matches")
        elif mode == "count":
            args.append("--count")
        else:
            args.extend(["--line-number", "--max-columns", "300"])
        if ignore_case:
            args.append("--ignore-case")
        if glob_filter:
            args.extend(["--glob", str(glob_filter)])
        args.extend(["--regexp", pattern, str(target)])

        try:
            completed = subprocess.run(
                args,
                cwd=str(self.workspace.root),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        # 終了コード 1 は「一致なし」で正常。2 以上は実行エラー。
        if completed.returncode >= 2:
            return None
        if completed.returncode == 1 or not completed.stdout.strip():
            return f"'{pattern}' に一致する箇所はなかった"

        lines = completed.stdout.strip().splitlines()
        shown = lines[:MAX_GREP_RESULTS]
        body = "\n".join(
            line.replace(str(self.workspace.root) + os.sep, "") for line in shown
        )
        if len(lines) > len(shown):
            body += f"\n[... 他 {len(lines) - len(shown)} 行]"
        return _truncate(body)

    def _grep_with_python(
        self,
        pattern: str,
        target: Path,
        mode: str,
        glob_filter: str | None,
        ignore_case: bool,
    ) -> str:
        """ripgrep が使えない環境向けの純Python実装。"""
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            raise ToolError(f"正規表現が不正: {exc}") from exc

        files = [target] if target.is_file() else _iter_files(target)
        hits: list[str] = []
        counts: dict[str, int] = {}

        for file in files:
            if glob_filter and not fnmatch.fnmatch(file.name, str(glob_filter)):
                continue
            if _is_probably_binary(file):
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            matched = 0
            for number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matched += 1
                    if mode == "content":
                        hits.append(f"{self.workspace.display(file)}:{number}:{line[:300]}")
            if matched:
                counts[self.workspace.display(file)] = matched
                if mode == "files_with_matches":
                    hits.append(self.workspace.display(file))

        if mode == "count":
            hits = [f"{name}:{count}" for name, count in sorted(counts.items())]

        if not hits:
            return f"'{pattern}' に一致する箇所はなかった"

        shown = hits[:MAX_GREP_RESULTS]
        body = "\n".join(shown)
        if len(hits) > len(shown):
            body += f"\n[... 他 {len(hits) - len(shown)} 件]"
        return _truncate(body)

    # -- bash ----------------------------------------------------------
    def _bash(self, payload: dict[str, Any]) -> str:
        command = str(payload.get("command", "")).strip()
        if not command:
            raise ToolError("command が空")
        timeout = int(payload.get("timeout") or self.bash_timeout)
        timeout = max(1, min(timeout, 900))

        started = time.monotonic()
        try:
            completed = subprocess.run(
                ["bash", "-c", command],
                cwd=str(self.workspace.root),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"{timeout} 秒でタイムアウトした: {command}") from None
        except OSError as exc:
            raise ToolError(f"コマンドを起動できない: {exc}") from exc

        elapsed = time.monotonic() - started
        parts: list[str] = []
        if completed.stdout.strip():
            parts.append(completed.stdout.rstrip())
        if completed.stderr.strip():
            parts.append("[stderr]\n" + completed.stderr.rstrip())
        if not parts:
            parts.append("(出力なし)")
        parts.append(f"[終了コード {completed.returncode} / {elapsed:.1f}s]")

        output = _truncate("\n".join(parts))
        if completed.returncode != 0:
            raise ToolError(output)
        return output
