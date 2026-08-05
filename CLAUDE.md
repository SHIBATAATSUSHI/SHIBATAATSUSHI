# このリポジトリについて

柴田篤志の実験・スクリプト置き場(monorepo的運用)。文章コンテンツ(日本語)と、Claude Code の生産性設定(`.claude/`)も管理する。

## ディレクトリ構成

- `/scripts` — 単発Python
- `/analysis` — 動作分析・HealthKit
- `/experiments` — 試行
- `/tests` — テスト(`python -m unittest discover tests`)
- `/docs` — ドキュメント・文章コンテンツ。note の方針の正は `docs/note-strategy.md`(投稿予定は `note-content-calendar.md`、実績は `note-publication-log.md`)
- `/posts` — note 投稿用のMarkdown原稿。note.com/19770104 専用(`docs/note-auto-post.md` 参照)
- `/config` — 設定ファイル。実データは `.gitignore`、`*.example.json` のみコミットする

## コーディング規約

- Pythonは3.11想定
- コメント・docstringは日本語で書く

## 原則(スリムに保つこと)

- 回答・コミットメッセージ・ドキュメントは日本語で書く。
- 曖昧な依頼は着手前にゴール・制約・完了条件を1度だけ確認する(`/goal` スキル参照)。
- 3つ以上の独立した調査・作業は並列化を検討する(`/parallel` スキル参照)。
- ユーザーから修正・訂正を受けたら `/self-improve` でこのファイルの「学習ログ」に反映する。
- 認証情報(cookie・パスワード)はコミットしない。環境変数か `.gitignore` 済みのファイルに置く。

## ブランチ運用

- 破壊的変更は必ずブランチを切り、mainに直接pushしない。

## 学習ログ

(ユーザーからの修正・好みをここに1行ずつ追記していく。古く一般化できたものは上の「原則」に昇格させ、ここからは削除する)

- note の原稿は主語「私は」を使わない。誰の判断かは文脈で示す(`docs/note-strategy.md` の文体ルール参照)。
- note の記事は AIらしい定型句・対比構文・均質なリズムを避け、専門的で長さのある文章にする。
- 医療テーマの記事は下書き保存までを自動化し、公開は必ず人が判断する。
