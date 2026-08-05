# note 自動投稿

Markdown で書いた記事を note に投稿するツール。**既定は下書き保存**で、`--publish` を付けたときだけ公開する。アカウントの切り替えは `--account` フラグ1つで済む。

## セットアップ(最初の1回だけ)

### 1. ネットワーク許可

Claude Code の実行環境から投稿する場合、既定では note.com への通信がブロックされている。claude.ai の環境設定でネットワーク許可リストに次を追加する。

- `note.com`
- `editor.note.com`
- `assets.st-note.com`(記事に画像を入れる場合のみ)

参照: https://code.claude.com/docs/en/claude-code-on-the-web

手元の Mac で実行する場合は不要。

### 2. Cookie の取得

note には公開 API が無いため、ブラウザのログインセッションを借りる。パスワードは不要。

1. ブラウザで note に投稿したいアカウントでログインする
2. DevTools を開く(Mac: `⌥⌘I`)→ **Application** タブ → 左の **Cookies** → `https://note.com`
3. `_note_session_v5` の **Value** をコピーする

有効期間は数ヶ月。切れたら同じ手順で取り直す。

### 3. 環境変数に登録

claude.ai の環境設定の環境変数に登録する(コンテナは毎回作り直されるので、ここに入れないと毎回貼り直しになる)。

```
NOTE_TORA_URLNAME=akutagawa_tora
NOTE_TORA_SESSION=<コピーした _note_session_v5 の値>
NOTE_DEFAULT_ACCOUNT=tora
```

`TORA` の部分が `--account tora` で指定する名前になる。アカウントを増やすときはこの3行のうち上2行を名前を変えて足すだけ。

```
NOTE_SUB_URLNAME=別のアカウントのユーザー名
NOTE_SUB_SESSION=<そのアカウントの _note_session_v5>
```

**この値はパスワード相当。** リポジトリにコミットしないこと(`.gitignore` で防いではいる)。

#### 手元の Mac で動かす場合

環境変数の代わりに、リポジトリ直下に `.note-accounts.json` を置いてもよい(`.gitignore` 済み)。

```json
{
  "tora": { "urlname": "akutagawa_tora", "session": "<_note_session_v5 の値>" },
  "sub":  { "urlname": "別のユーザー名",   "session": "<そのアカウントの値>" }
}
```

同じ名前が両方にある場合は環境変数が優先される。

## 使い方

```bash
# 下書き保存(既定)
uv run scripts/note_post.py docs/note/kiji.md --account tora

# note に接続せず、変換結果の HTML だけ確認する(アカウント設定も不要)
uv run scripts/note_post.py docs/note/kiji.md --dry-run

# 公開まで行う
uv run scripts/note_post.py docs/note/kiji.md --account tora --publish --tag AI --tag 日記

# アイキャッチ画像を付ける
uv run scripts/note_post.py docs/note/kiji.md --eyecatch docs/note/img/cover.png

# 設定済みアカウントの一覧
uv run scripts/note_post.py --list-accounts
```

| フラグ | 意味 |
|---|---|
| `--account` / `-a` | 投稿先アカウント。省略時は `NOTE_DEFAULT_ACCOUNT`、それも無く設定が1件ならそれ |
| `--title` / `-t` | タイトルを上書きする |
| `--tag` | ハッシュタグ。複数回指定できる |
| `--eyecatch` | アイキャッチ画像のパス |
| `--publish` | 公開まで行う(付けなければ下書き) |
| `--dry-run` | note に接続せず変換結果だけ表示 |
| `--skip-lint` | 投稿前の文体チェックを飛ばす |
| `--json` | 結果を JSON で出力(他のスクリプトから使うとき) |
| `--list-accounts` | 設定済みアカウントの一覧 |

## 文体チェック(note_lint.py)

投稿前に文体を機械的に検査する。`note_post.py` が自動で通すので普段は意識しなくてよいが、単体でも動く。

```bash
uv run scripts/note_lint.py docs/note/kiji.md
uv run scripts/note_lint.py docs/note/*.md --strict
```

```
docs/note/kiji.md
  本文 2480 字 / 一人称 2 件(うち「私は」2 件)

  docs/note/kiji.md:31: [warning] 「私は」: …私はこう考えて…
```

**このスクリプトの目的は、AI の自己申告を実測に置き換えること。** 「本文中0回です」と言わせるのではなく、実際に数えて行番号ごと出す。

検査するもの: 一人称の出現 / 本文の文字数 / h4 以降の見出し / note が解釈しない記法(表・脚注・生 HTML)/ タイトルの有無と長さ / 一文の長さ / 文末の単調さ。

`error` は投稿を止める。止まるのは「そのままでは投稿が成り立たないもの」だけで、現状はタイトルが決まらない場合のみ。文体上の指摘は `warning` で、投稿は止めない(見落としを拾う道具であって、書き手を縛る道具ではないため)。`--strict` を付けると warning も失敗扱いになる。

### 意図して一人称を残す場合

フロントマターで数を申告すると、その数に収まっている限り指摘されない。

```markdown
---
title: 記事のタイトル
allow_first_person: 2
---
```

文体ルールそのものは `docs/note/STYLE.md` にある。

## 記事を書かせる(/note-write)

`/note-write` スキルを使うと、STYLE.md を読む → 執筆 → lint → 通るまで修正、を自走する。ルールを毎回説明し直す必要がない。定義は `.claude/skills/note-write/SKILL.md`。

## 記事の書き方

`docs/note/` に Markdown で置く。

タイトルは次の優先順位で決まる。

1. `--title` フラグ
2. フロントマターの `title:`
3. 本文先頭の `# 見出し`(この行は本文から除かれる)

フロントマターを使う場合(1行目がちょうど `---` のときだけ有効):

```markdown
---
title: 記事のタイトル
tags: [AI, 日記]
---

本文をここから書く。
```

### 使える記法

| Markdown | note での表示 |
|---|---|
| `#` `##` | 大見出し (h2) |
| `###` 以降 | 小見出し (h3) |
| `- ` `* ` | 箇条書き(入れ子はスペース2つで1段) |
| `1. ` | 番号付きリスト |
| `> ` | 引用(連続行は1つにまとまる) |
| `---` | 区切り線 |
| ` ```lang ` | コードブロック |
| `**太字**` `*斜体*` `~~打消~~` `` `コード` `` | インライン装飾 |
| `[表示文字](URL)` | リンク |
| `![説明](画像パス)` | 画像(自動アップロード) |
| `<toc>` | 目次ブロック |

note には h1 も h4 以降も無いので、見出しは h2 と h3 に丸められる。本文中の `<` `&` などは自動でエスケープされるため、そのまま書いてよい。

## 困ったとき

### 「認証エラー」「Cookie が期限切れ」と出る

`_note_session_v5` の有効期限が切れている。セットアップ手順2をやり直して、環境変数を更新する。

### 「プロキシに拒否されました」と出る

実行環境のネットワーク許可リストに note.com が入っていない。セットアップ手順1を確認する。

### 表示が崩れる

`--dry-run` で生成される HTML を確認する。崩れが再現するなら `scripts/note_markdown.py` の変換規則の問題。下書きで確認してから公開する運用にしておけば事故にならない。

### 投稿した記事を取り消したい

下書きなら note の管理画面から削除する。公開済みなら記事ページから「下書きに戻す」または削除する。このツールに削除機能は無い。

## 仕組みと注意

note に公開 API は無いため、note のエディタ自身が使っている内部 API を利用している。

1. `POST /api/v1/text_notes` で空の記事を作る
2. `POST /api/v1/text_notes/draft_save` で本文を保存する(ここまでが下書き)
3. `--publish` のときだけ `PUT /api/v1/text_notes/{id}` で公開する

**非公式なので note 側の仕様変更で動かなくなることがある。** そのときはエラーにステータスコードとレスポンス本文が出るので、それを手がかりに直す。

自分のアカウントに自分の記事を投稿する用途を前提にしている。既定を下書き止まりにしているのも、機械的な連投を避けるため。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `scripts/note_post.py` | CLI 本体 |
| `scripts/note_api.py` | note 内部 API クライアント + アカウント設定の解決 |
| `scripts/note_markdown.py` | Markdown → note の HTML 変換(ネットワーク非依存) |
| `tests/test_note_markdown.py` | 変換のテスト |
| `tests/test_note_api.py` | アカウント解決のテスト |
| `docs/note/` | 投稿する記事の置き場 |

テストは `uv run pytest` で実行できる(ネットワーク不要)。
