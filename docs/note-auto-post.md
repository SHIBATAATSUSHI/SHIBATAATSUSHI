# note 自動投稿(note.com/19770104 専用)

Markdown 原稿を https://note.com/19770104 に**下書き保存**するツール。既存記事の上書きにも対応する。
運用の前提(何のためにこれを作っているか)は [note-strategy.md](note-strategy.md) を参照。

- `scripts/note_post.py` … note を操作する(login / post / update / accounts)
- `scripts/note_lint.py` … 公開前チェック(医療・個人情報・出典・文体)

> **このリポジトリは 19770104 専用。**
> 設定はアカウントキーで引く作りになっているが、公開前チェックのルールは
> 医療・リハビリのテーマ前提で書かれている。創作・エッセイなど別ジャンルのアカウントに
> 当てると、登場人物の年齢・性別の描写を患者情報と誤判定するなどして正しく動かない。
> 他アカウントを扱う場合は、チェックルールをジャンル別に分ける改修が必要。

## 設計方針

**公開は自動化しない。** 医療テーマのため、自動化するのは下書き保存までで、
公開ボタンは note 上で人が押す。`allow_publish` が `false` のアカウントでは
`--publish` を付けてもエラーになる。
対話できない環境(cron・CI・出力のリダイレクト)では、`--yes` を明示しない限り
公開に進まない。front matter の `publish: true` だけでは公開されない。

**間違ったアカウントには書き込まない。** 設定ファイルのアカウントキーと、note 側で
実際にログインしているアカウントは別物になりうる。取り違えたまま書き込むと他人の記事を
壊すため、書き込みの前に必ず照合する(→ [アカウントの照合](#アカウントの照合))。

note には公開された投稿APIが無いため、Playwright で実ブラウザを操作する。
DOM に依存する箇所は `scripts/note_post.py` 冒頭の `SEL_*` 定数に集約してあるので、
note の UI が変わったらそこだけ直す。

## 本文の入れ方

**Markdown を1文字ずつ打ち込むのではなく、HTML に変換してクリップボード経由で貼り付ける。**

note のエディタ(ProseMirror)が Markdown 記法を自動変換してくれるとは限らず、
`##` や `-` がそのまま文字として入る可能性がある。HTML で渡せば見出し・リスト・リンクが確実に残る。

```
原稿(Markdown) → markdown_to_note_html() → クリップボード(text/html + text/plain)
  → 本文欄をクリック → Ctrl+A → Ctrl+V
```

対応する記法は 見出し(`##` `###`)、引用(`>`)、箇条書き(`-`)、番号付きリスト(`1.`)、
段落、太字(`**強調**`)、リンク(`[text](url)`)。画像・表・コードブロックは非対応。

**1行 = 1段落として扱う。** 一般的な Markdown は連続する行を1つの段落に結合するが、
ここでは結合しない。note のエディタに段落内改行の概念が無く、Enter が常に新しい
ブロックを作るためで、原稿の1行がそのまま note の1ブロックになる。

```
制定：2020年11月14日      → 2つの段落として入る(結合しない)
最終改訂：2026年8月4日
```

**1つの段落にしたい文は、原稿でも1行にまとめること。** 長くなっても改行しない。

`innerHTML` の直接代入はしない。ProseMirror が変更を認識しないため。

| `--paste-mode` | 動作 |
| --- | --- |
| `auto`(既定) | クリップボードを試し、使えなければ paste イベントに切り替える |
| `clipboard` | クリップボードのみ。失敗したら中断 |
| `event` | `paste` イベントを直接起こす。クリップボード権限が要らない |

貼り付け後、内容を読み戻して**タイトルの一致・文字数・見出し数・リンク数・「私は」の残存数**を表示する。
原稿より見出しやリンクが少なければ警告が出る。

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

編集画面のURLは `https://editor.note.com/notes/{記事キー}/edit/` で、公開URLとはドメインが違う。
公開URLを渡せば自動で変換される。

### 保存時の競合ダイアログ

同じ記事を別の画面でも開いていると、保存時に
「複数画面で編集されています。どちらを保存しますか?」が出ることがある。

**初期選択は「別の画面」= 保存前の古い原稿**で、そのまま進めると今回の変更が捨てられる。
このツールは自動で「現在の画面」を選び直してから保存する。
選び直せなかった場合は、古い版を黙って残すより安全なので**中断する**。

## アカウントの照合

書き込む前に、note 側で実際にログインしているアカウントを取得し、設定と照合する。
照合は3箇所で走る。

| タイミング | 不一致だったら |
| --- | --- |
| `login` でセッションを保存する直前 | 保存せずに終了する。別アカウントのセッションがファイルに紛れ込まない |
| `post` / `update` でブラウザを開いた直後 | 本文を打ち込む前に中断する |
| `update` に渡された URL | URL のアカウント名が設定と違えば、ブラウザを開く前に中断する |

**判定できなかった場合も既定で中断する。** 警告を出すだけでは取り違えを防げないため。
note 側の仕様変更で判定できなくなったときの逃げ道として `--allow-unverified` がある。

同じ考え方を保存にも適用している。**「保存されました」の表示を確認できなければ中断する。**
保存できていないのに成功と報告すると、ブラウザを閉じた時点で書いた内容が失われるため。

設定とログイン状態の確認、および保存済みセッションの実地確認:

```bash
python scripts/note_post.py accounts            # 設定とログイン有無だけ
python scripts/note_post.py accounts --verify   # 実際にセッションを開いて照合する
```

```
- 19770104 (default) — 夏目漱右(大学病院の理学療法士)
    プロフィール: https://note.com/19770104
    セッション  : secrets/note_19770104_state.json [ログイン済み]
    検証        : OK (note 側のログインも 19770104)
```

照合には note の内部API(`/api/v2/current_user`)を第一手段に使い、失敗したら DOM から拾う。
非公式仕様のため壊れうるが、**読み取り専用の照合にしか使っていない**(投稿自体はブラウザ操作)。

## 画面診断(初回はここから)

セレクタが note の実物と合っているかを調べる。**書き込みは一切しない。**
初回セットアップ時と、投稿が要素不足で失敗したときに使う。

```bash
python scripts/note_post.py doctor                  # 新規作成画面を調べる
python scripts/note_post.py doctor --url https://note.com/19770104/n/nxxxxx  # 既存記事の編集画面
python scripts/note_post.py doctor --url <URL> --stage publish  # 公開設定画面(タグ・見出し画像)
python scripts/note_post.py doctor --headed         # ブラウザを見ながら
```

`--stage publish` は「公開に進む」を押して、ハッシュタグと見出し画像がある画面まで進む。
**投稿ボタンには触れない。** そこに実装を足されないよう、テストで固定してある。

ファイル入力(`input[type=file]`)は `display:none` のことが多いので、
可視判定を外して採取している。見出し画像のアップロード先を見つけるため。

`diagnostics/` に2つ出力される。

- `doctor-<日時>.md` — セレクタの当たり外れと、画面にある要素の一覧(tag / text / placeholder / data-testid / class)
- `doctor-<日時>.png` — 画面全体の写し

```
--- セレクタの当たり外れ ---
  `SEL_TITLE` → `textarea[placeholder='記事タイトル']`
  `SEL_BODY` → `div[contenteditable='true'][role='textbox']`
  `SEL_SAVE_DRAFT` → **なし**
```

「なし」があれば、レポートの要素一覧から該当しそうなものを探し、
`scripts/note_post.py` 冒頭のセレクタ定義に候補を足す。

投稿中に要素が見つからなかった場合も、エラーメッセージにその時点の画面の要素が出る。

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
| `--paste-mode` | 本文の貼り付け方式(auto / clipboard / event) |
| `--skip-check` | 公開前チェックを飛ばす |
| `--strict` | 「要確認」でも中断する |
| `--force` | 「重大」があっても続行する |
| `--allow-unverified` | ログイン中アカウント・保存の確認が取れなくても続行する |

## テスト

ブラウザを使わない部分(原稿の解釈、URLの検査、チェッカーの判定、アカウント照合、各ガード)を
60件のテストで固めてある。Playwright は不要。

```bash
python -m unittest discover tests
```

セレクタ定義やチェックルールを直したときは、これを通してからコミットする。

## 既知の制約

- セレクタとURLは、実際に下書き保存に成功した手順から移植した。ただし**このリポジトリからは
  一度も実行できていない**(note.com に到達できない環境のため)。
  **初回は必ず `doctor` で当たり外れを確認してから**投稿すること。
- 対応する書式は 見出し・箇条書き・番号付きリスト・引用・リンクのみ。
  画像・表・コードブロック・埋め込み・有料エリアの設定は非対応。
- 見出し画像(サムネイル)は設定しない。公開前に手動で付ける。
- 上書きは本文を全選択してから貼り付けて置き換える方式。実行前に note 側の内容を確認すること。
- 新規作成は `note.com/notes/new` から `https://editor.note.com/new` へ転送されるため、
  転送先を直接開いている。既存記事の編集画面(`editor.note.com/notes/{key}/edit/`)は
  実機でセレクタの一致を確認済み。新規作成画面は未確認。
- チェッカーは正規表現ベースで、意味を理解しているわけではない。見逃しも誤検出もある。
  **最終判断は必ず人間が行う。**
- 実行環境から note.com に到達できる必要がある。
