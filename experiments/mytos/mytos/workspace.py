"""作業ディレクトリの境界を守るためのパス解決。

モデルが渡してくるパスは信頼できない入力なので、必ずここを通して
作業ディレクトリ配下に収まっているかを検証してから読み書きする。
"""

from __future__ import annotations

from pathlib import Path


class WorkspaceError(Exception):
    """作業ディレクトリの外に出ようとしたなど、パス解決に失敗したとき。"""


class Workspace:
    """作業ディレクトリと、その配下に限定したパス解決を提供する。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise WorkspaceError(f"作業ディレクトリが存在しない: {self.root}")

    def resolve(self, path: str) -> Path:
        """モデルが指定したパスを、作業ディレクトリ配下の絶対パスに解決する。

        `..` やシンボリックリンクで外に出る指定は `WorkspaceError` にする。
        まだ存在しないパス(新規作成)も許容するため、実在する祖先まで
        遡って解決してから境界を判定する。
        """
        if not path or not path.strip():
            raise WorkspaceError("パスが空")

        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate

        # 実在する祖先を resolve() でたどり、シンボリックリンクを展開する。
        # 未作成のパスでも判定できるように、存在しない末尾は後から連結する。
        existing = candidate
        tail: list[str] = []
        while not existing.exists():
            if existing.parent == existing:
                break
            tail.append(existing.name)
            existing = existing.parent

        resolved = existing.resolve()
        for name in reversed(tail):
            resolved = resolved / name

        if resolved != self.root and self.root not in resolved.parents:
            raise WorkspaceError(
                f"作業ディレクトリの外は操作できない: {path} → {resolved}"
            )
        return resolved

    def display(self, path: Path) -> str:
        """ログ表示用に、作業ディレクトリからの相対パスにする。"""
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)
