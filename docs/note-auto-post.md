# note 自動投稿(複数アカウント対応)

Markdown 原稿を note.com に**下書き保存**するツール。既存記事の上書きにも対応する。
運用の前提(何のためにこれを作っているか)は [note-strategy.md](note-strategy.md) を参照。

- `scripts/note_post.py` … note を操作する(login / post / update / accounts)
- `scripts/note_lint.py` … 公開前チェック(医療・個人情報・出典・文体)

対象アカウント:

| キー | プロフィール | 備考 |
| --- | --- | --- |
| `19770104` | https://note.com/19770104 | このリポジトリでの主対象 |
| `akutagawa_tora` | https://note.com/akutagawa_tora | 別コードで運用中。設定を足せばこちらからも投げられる |

## 設計方針

**公開は自動化しない。** 医療テーマのため、自動化するのは下書き保存までで、
公開ボタンは note 上で人が押す。`allow_publish` が `false` のアカウントでは
`--publish` を付けてもエラーになる。

note には公開された投稿APIが無いため、Playwright で実ブラウザを操作する。
DOM に依存する箇所は `scripts/note_post.py` 冒頭の `SEL_*` 定数に集約してあるので、
note の UI が変わったらそこだけ直す。

## セットアップ

```bash
pip install -r requirements.txt
playwright install chromium

cp config/note_accounts.example.json config/note_accounts.json
```

`config/note_accounts.json` にアカウントを定義する。パスワードは書かず、環境変数名だけを書く。

| キー | 意味 |
| --- | --- |
| `urlname` | note のURL名(`note.com/<urlname>`) |
| `storage_state` | ログイン済みセッションの保存先。`secrets/` 配下を推奨 |
| `email_env` / `password_env` | メール・パスワードを入れる環境変数の**名前** |
| `allow_publish` | `true` にしない限り公開操作を拒否する。既定 `false` |

## 1. ログイン(アカウントごとに一度)

```bash
python scripts/note_post.py login --account 19770104 --manual
```

ブラウザが立ち上がるので手動でログインする(2段階認証もここで通す)。
完了後にターミナルで Enter を押すと `secrets/note_19770104_state.json` にセッションが保存される。

メール／パスワードでの自動ログインもできるが、追加認証が出ると通らないため `--manual` を推奨。
セッションが切れたら同じコマンドを再実行する。

> `secrets/` 配下は実質パスワード。`.gitignore` 済みだが、共有・コミットは絶対にしない。

## 2. 原稿を書く

`posts/_template.md` をコピーして書く。

```markdown
---
title: 記事タイトル
account: 19770104
tags: [理学療法士, 生成AI]
publish: false
# note_url: https://note.com/19770104/n/nxxxxxxxxxxxx   ← 上書きする場合だけ
---

本文。
```

| キー | 意味 |
| --- | --- |
| `title` | タイトル。省略時は本文冒頭の `# 見出し` を使う |
| `account` | 投稿先アカウントキー。`--account` の方が優先 |
| `tags` | ハッシュタグ。`[a, b]` でも `- a` でも可 |
| `publish` | `true` で公開まで進む(`allow_publish` が必要)。既定は下書き |
| `note_url` | 上書き先の記事。書いてあると `post` は拒否され `update` に誘導される |

`<!-- -->` で囲んだメモは note に流し込まれない。原稿内に下書きメモを残せる。

## 3. 公開前チェック

```bash
python scripts/note_lint.py posts/example.md
python scripts/note_lint.py posts/example.md --review-sheet output/example-review.md
```

重大度は3段階。**重大が1件でもあると投稿は中断する**(`--force` で続行可)。

| 重大度 | 内容 | 例 |
| --- | --- | --- |
| 重大 | 公開を止める | 治癒の断定、服薬変更の示唆、個別症例、所属特定 |
| 要確認 | 公開前に必ず確認 | 断定・一般化、最上級表現、出典のない数値、タイトルの一人称 |
| 推敲 | 直すと良い | 定型句、対比構文の多用、同じ書き出しの連続、長すぎる文、絵文字 |

`--review-sheet` を付けると、自動チェック結果＋人間が確認する項目のチェックリスト
(公開前確認票)を Markdown で出力する。

## 4. note へ流し込む

### 新規記事

```bash
# 内容とチェック結果だけ確認(ブラウザを起動しない)
python scripts/note_post.py post --file posts/example.md --dry-run

# 下書きとして保存
python scripts/note_post.py post --file posts/example.md \
  --review-sheet-dir output --screenshot-dir screenshots
```

完了時に編集URLが表示される。これを原稿の `note_url` に控えておくと、次から上書きできる。

### 既存記事の上書き

下書き・公開済みのどちらも開いて本文を差し替えられる。

```bash
# front matter の note_url を使う
python scripts/note_post.py update --file posts/example.md

# URL を直接渡す(公開URLでも編集URLでも記事キーでも可)
python scripts/note_post.py update --file posts/example.md \
  --url https://note.com/19770104/n/n52aef685b03f
```

公開済みの記事は下書きとして保存できないため、note 側で下書きに戻してから実行するか、
内容を確認のうえ `--publish` を付けて更新する。

### 別アカウントに投げる

```bash
python scripts/note_post.py update --account akutagawa_tora --file posts/example.md
python scripts/note_post.py accounts   # 設定とログイン状態の確認
```

## デバッグ

初回や UI 変更時は、ブラウザを表示して目視するのが早い。

```bash
python scripts/note_post.py post --file posts/example.md \
  --headed --slow-mo 300 --screenshot-dir screenshots --keep-open
```

| オプション | 効果 |
| --- | --- |
| `--headed` | ブラウザを表示する |
| `--slow-mo` | 操作をゆっくりにする(ms) |
| `--screenshot-dir` | 各段階と失敗時のスクリーンショットを保存する |
| `--keep-open` | 完了後もブラウザを閉じない |
| `--typing-delay` | 1文字あたりの入力遅延(ms) |
| `--skip-check` | 公開前チェックを飛ばす |
| `--strict` | 「要確認」でも中断する |
| `--force` | 「重大」があっても続行する |

## 既知の制約

- **セレクタは実環境での検証が済んでいない。** note の UI は変わりやすく、現在のセレクタは
  想定に基づく候補リスト。初回は必ず `--headed --screenshot-dir` を付けて下書きモードで走らせ、
  ズレていたら `SEL_*` を実際の DOM に合わせて直すこと。
- 本文はキーボード入力で流し込むため、対応する書式は note のエディタが Markdown ショートカットとして
  解釈するもの(見出し・箇条書き・引用)に限られる。画像・埋め込み・有料エリアの設定は非対応。
- 見出し画像(サムネイル)は設定しない。公開前に手動で付ける。
- 上書きは本文を全消去してから入力し直す方式。実行前に note 側の内容を確認すること。
- チェッカーは正規表現ベースで、意味を理解しているわけではない。見逃しも誤検出もある。
  **最終判断は必ず人間が行う。**
- 実行環境から note.com に到達できる必要がある。
