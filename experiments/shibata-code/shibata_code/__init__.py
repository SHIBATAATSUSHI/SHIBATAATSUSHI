"""Shibata Code — 端末で動くコーディングエージェント。

ツール利用ループを最小構成で組んだもの。ファイルの読み書き・検索・
シェル実行を、権限確認を挟みながら実行する。

Anthropic / OpenAI / Gemini / DeepSeek / Kimi / Qwen / OpenRouter /
ローカルモデルを切り替えて使える。会話の途中で切り替えても履歴は続く。
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
