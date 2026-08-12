---
title: 自動投稿のテスト記事
account: 19770104
tags: [テスト, 自動化]
publish: false
---

これは `scripts/note_post.py` の動作確認用のサンプル記事です。

## 使い方

front matter に `title` / `account` / `tags` / `publish` を書き、それ以降が本文になります。
`title` を省略した場合は、本文冒頭の `# 見出し` がタイトルとして使われます。

## 書式について

note のエディタは Markdown ショートカットに反応するため、以下はそのまま反映されます。

- 箇条書き
- 見出し(`##`)
- 引用(`>`)

記号をそのまま文字として入れたい場合は `--no-markdown-shortcut` を付けてください。
