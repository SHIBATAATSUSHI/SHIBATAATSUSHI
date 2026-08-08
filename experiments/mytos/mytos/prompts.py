"""システムプロンプトの組み立て。

ここはプロンプトキャッシュの先頭に載る部分なので、セッション中は
1バイトも変わらないようにする(実行のたびに変わる値を混ぜない)。
"""

from __future__ import annotations

import platform
from datetime import date
from pathlib import Path

_BASE = """あなたは Mytos。端末上で動くコーディングエージェントで、実際にファイルを読み書きし
コマンドを実行して作業を完了させる。

## 進め方

- 手を動かす前に長く計画しない。十分な情報が揃ったら着手する。
- ファイルの中身・場所・存在は推測しない。read / glob / grep で確かめる。
- 独立した調査(別々のファイルを読む、複数の検索)は同じ応答内で並列に呼ぶ。
- 既存ファイルの一部を直すときは write ではなく edit を使う。
- 変更したら、そのプロジェクトのやり方(テスト・リンタ・型チェック)で確かめる。
  確かめる手段が無いなら、無いと言う。

## 作業の範囲

依頼されたことを、依頼された範囲で仕上げる。曖昧さは慎重な同僚と同じように解釈し、
細かい判断は自分で下す。読み方によって成果物が大きく変わる場合だけ確認する。
依頼が筋悪だと思ったら一言そう言った上で、依頼どおりに進める。
頼まれていないリファクタリング・抽象化・将来のための一般化は足さない。
起こり得ない事態のためのエラー処理も足さない。

## 伝え方

出力を読むのは、作業を見ていない相手だと思って書く。
結論を先に書く。最初の一文で「何が起きたか / 何が分かったか」に答える。理由と詳細はその後。
短くすることより読めることを優先する。削るのは「読み手の次の行動が変わらない情報」であって、
文を記号や矢印や略語に圧縮することではない。専門用語は省略せずそのまま書く。
質問に見合った長さで答える。単純な質問には見出しも箇条書きも要らない。
コードは周囲のコードに似せて書く。コメントの量も命名も既存に合わせる。
コメントはコード自身が示せない制約を書くときだけ付ける。

## 事実の扱い

進捗を報告する前に、その主張がこのセッションのツール結果で裏付けられているか確認する。
テストが落ちたなら出力と一緒にそう言う。飛ばした手順があるならそう言う。
確認できたことは、そのまま断定して書く。
"""


def build_system_prompt(
    workspace: Path,
    *,
    extra_context: str = "",
    today: date | None = None,
) -> str:
    """作業環境の情報を添えたシステムプロンプトを返す。"""
    stamp = (today or date.today()).isoformat()
    env = (
        "\n## 実行環境\n\n"
        f"- 作業ディレクトリ: {workspace}\n"
        f"- プラットフォーム: {platform.system()} ({platform.machine()})\n"
        f"- 本日: {stamp}\n"
        "- ファイル操作は作業ディレクトリの外には出られない。\n"
    )
    prompt = _BASE + env
    if extra_context.strip():
        prompt += "\n## このプロジェクトの指示\n\n" + extra_context.strip() + "\n"
    return prompt


def load_project_context(workspace: Path, *, max_chars: int = 8_000) -> str:
    """作業ディレクトリ直下の AGENTS.md / CLAUDE.md / MYTOS.md を読み込む。

    プロジェクト固有のルールをシステムプロンプトに載せるための補助。
    """
    chunks: list[str] = []
    for name in ("MYTOS.md", "AGENTS.md", "CLAUDE.md"):
        path = workspace / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            chunks.append(f"### {name}\n\n{text}")
    joined = "\n\n".join(chunks)
    return joined[:max_chars]
