"""ツール実行の権限判定。

読み取り専用ツールは常に自動実行し、副作用のあるツール(ファイル書き込み・
bash)は権限モードに応じて確認する。取り返しがつきにくいコマンドは
`auto` モードでも確認を挟む。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# `auto` モードでも必ず確認するコマンドのパターン。
# 完全な防御ではなく「事故りやすいものは一拍置く」ための保険。
RISKY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-?[a-zA-Z]*[rf]", re.I), "再帰的/強制削除"),
    (re.compile(r"\bsudo\b", re.I), "sudo による特権昇格"),
    (re.compile(r"\bgit\s+push\b", re.I), "リモートへの push"),
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.I), "作業ツリーの破棄"),
    (re.compile(r"\bgit\s+clean\b", re.I), "未追跡ファイルの削除"),
    (re.compile(r"\b(curl|wget)\b[^|]*\|\s*(ba)?sh", re.I), "ダウンロードしたスクリプトの実行"),
    (re.compile(r"\bchmod\s+(-R\s+)?777\b"), "権限の全開放"),
    (re.compile(r"\bmkfs|\bdd\s+if=", re.I), "ディスクへの直接書き込み"),
    (re.compile(r">\s*/dev/(sd|nvme)", re.I), "デバイスへの書き込み"),
)


@dataclass(frozen=True)
class Decision:
    """権限判定の結果。"""

    allowed: bool
    needs_confirmation: bool
    reason: str = ""


def classify_command(command: str) -> str | None:
    """危険パターンに当たれば、その説明を返す。当たらなければ None。"""
    for pattern, label in RISKY_PATTERNS:
        if pattern.search(command):
            return label
    return None


class PermissionGate:
    """権限モードに応じて、ツール実行の可否を判定する。"""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    def decide(self, *, tool_name: str, read_only: bool, command: str | None = None) -> Decision:
        """このツール実行に確認が必要かを判定する。"""
        if read_only:
            return Decision(allowed=True, needs_confirmation=False)

        if self.mode == "yolo":
            return Decision(allowed=True, needs_confirmation=False, reason="yolo モード")

        risky = classify_command(command) if command else None

        if self.mode == "auto":
            if tool_name == "bash":
                # bash は auto でも確認する(何が起きるか静的に判断できないため)
                return Decision(
                    allowed=True,
                    needs_confirmation=True,
                    reason=risky or "コマンド実行",
                )
            if risky:
                return Decision(allowed=True, needs_confirmation=True, reason=risky)
            return Decision(allowed=True, needs_confirmation=False, reason="auto モード")

        # ask モード
        return Decision(
            allowed=True,
            needs_confirmation=True,
            reason=risky or ("コマンド実行" if tool_name == "bash" else "ファイル変更"),
        )
