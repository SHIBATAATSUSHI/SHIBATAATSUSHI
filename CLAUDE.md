@AGENTS.md

# Claude Code 固有の設定

共有の指示(リポジトリの前提・ディレクトリ構成・コーディング規約・原則・ブランチ運用・エージェントの役割分担)は `AGENTS.md` にある。上の行でインポートしているので、ここには **Claude Code だけに関係すること**を書く。

> Claude Code は `AGENTS.md` を自動では読まない。先頭の `@AGENTS.md` が唯一の読み込み経路なので消さないこと。

## スキルの使いどころ

- 曖昧な依頼のゴール確認と完遂ループ → `/goal`
- 独立した作業の並列化 → `/parallel`
- ユーザーからの修正を次に活かす → `/self-improve`(学習ログは下記)
- 端末(Mac / ケータイ)をまたぐ再開・中断 → `/handoff`(`docs/mac-mobile-sync-guide.md`)
- Codex にセカンドオピニオンを求める → `/second-opinion`(`docs/codex-integration-guide.md`)
- 指示の棚卸し → `/audit-instructions`(月1回程度)

## 学習ログ

(ユーザーからの修正・好みをここに1行ずつ追記していく。古く一般化できたものは `AGENTS.md` の「原則」に昇格させ、ここからは削除する)

- 手順をコード欄に書くときは説明コメントを混ぜない。対話シェルの zsh は `#` をコメントとして扱わず、貼り付けると `command not found: #` になる。説明は欄外の散文に置く。
- 不具合の原因は設定を推測で疑う前に、ツールやコマンドの戻り値・エラーメッセージを先に読む。答えがそこに書かれていることが多い。
- コード欄を出すときは**どこに打つ/書くものかを毎回明示する**。ターミナル(`%`)か、Claude Code の中(`❯`、`/` で始まるもの)か、ファイルの中身(TOML・JSON など)か。省くと取り違えて `zsh: no such file or directory: /plugin` や `no matches found: [...]` になる。
