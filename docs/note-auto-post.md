# note 自動投稿(複数アカウント対応)

`scripts/note_post.py` で、Markdown ファイルを note.com に下書き保存／公開する。
アカウントごとにログイン済みセッションをファイルで持つため、複数アカウントを同じ仕組みで運用できる。

- 対象アカウント例
  - `19770104` → https://note.com/19770104(このリポジトリでの主対象)
  - `akutagawa_tora` → https://note.com/akutagawa_tora(別コードで運用中。設定を足せばこちらからも投げられる)

## なぜブラウザ操作なのか

note には公開された投稿APIが無い。内部APIを直接叩く方法もあるが、非公式仕様のため予告なく壊れる。
実ブラウザ(Playwright)でエディタを操作する方式にして、UI変更にはセレクタ定義の修正で追随する方針にした。
DOM に依存する箇所は `scripts/note_post.py` 冒頭の `SEL_*` 定数に集約してあるので、壊れたらそこだけ直す。

## セットアップ

```bash
pip install -r requirements.txt
playwright install chromium

cp config/note_accounts.example.json config/note_accounts.json
```

`config/note_accounts.json` にアカウントを定義する。パスワードは書かず、環境変数名だけを書く。

```json
{
  "default_account": "19770104",
  "accounts": {
    "19770104": {
      "urlname": "19770104",
      "storage_state": "secrets/note_19770104_state.json",
      "email_env": "NOTE_19770104_EMAIL",
      "password_env": "NOTE_19770104_PASSWORD"
    }
  }
}
```

## 1. ログイン(アカウントごとに一度)

```bash
python scripts/note_post.py login --account 19770104 --manual
```

ブラウザが立ち上がるので、手動で note にログインする(2段階認証もここで通す)。
完了したらターミナルで Enter を押すと `secrets/note_19770104_state.json` にセッションが保存される。

メール／パスワードでの自動ログインもできる。

```bash
export NOTE_19770104_EMAIL="..."
export NOTE_19770104_PASSWORD="..."
python scripts/note_post.py login --account 19770104
```

ただし note 側の追加認証(2段階認証・reCAPTCHA)が出ると通らないため、基本は `--manual` を推奨。
セッションが切れたら同じコマンドを再実行する。

> `secrets/` 配下は実質パスワード。`.gitignore` 済みだが、共有・コミットは絶対にしないこと。

## 2. 記事を書く

`posts/` に front matter 付きの Markdown を置く。

```markdown
---
title: 記事タイトル
account: 19770104
tags: [タグ1, タグ2]
publish: false
---

本文をここに書く。
```

| キー | 意味 |
| --- | --- |
| `title` | 記事タイトル。省略時は本文冒頭の `# 見出し` を使う |
| `account` | 投稿先アカウントキー。`--account` の方が優先される |
| `tags` | ハッシュタグ。`[a, b]` でも改行した `- a` でも可 |
| `publish` | `true` で公開、`false`(既定)で下書き保存 |

## 3. 投稿する

```bash
# 内容確認だけ(ブラウザを起動しない)
python scripts/note_post.py post --account 19770104 --file posts/example.md --dry-run

# 下書きとして保存
python scripts/note_post.py post --account 19770104 --file posts/example.md

# そのまま公開
python scripts/note_post.py post --account 19770104 --file posts/example.md --publish

# 別アカウントに投げる場合はキーを変えるだけ
python scripts/note_post.py post --account akutagawa_tora --file posts/example.md
```

設定済みアカウントとログイン状態の確認:

```bash
python scripts/note_post.py accounts
```

## デバッグ

初回や UI 変更時は、ブラウザを表示して挙動を目視するのが早い。

```bash
python scripts/note_post.py post --account 19770104 --file posts/example.md \
  --headed --slow-mo 300 --screenshot-dir screenshots --keep-open
```

- `--headed` … ブラウザを表示する
- `--slow-mo` … 操作をゆっくりにする(ms)
- `--screenshot-dir` … 各段階と失敗時のスクリーンショットを保存する
- `--keep-open` … 完了後もブラウザを閉じない

## 既知の制約

- **セレクタは実環境での検証が済んでいない。** note の UI は変わりやすく、この実装のセレクタは
  想定に基づく候補リスト。初回は必ず `--headed --screenshot-dir` を付けて下書きモードで走らせ、
  ズレていたら `SEL_*` を実際の DOM に合わせて直すこと。
- 本文はキーボード入力で流し込むため、対応する書式は見出し・箇条書き・引用など
  note のエディタが Markdown ショートカットとして解釈するものに限られる。
  画像・埋め込み・有料エリアの設定には対応していない。
- 見出し画像(サムネイル)は設定しない。必要なら公開前に手動で付ける。
- 実行環境から note.com に到達できる必要がある(手元 or 外向き通信ができる CI)。
