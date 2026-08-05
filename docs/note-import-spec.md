# 外部ツールからのデータ受け入れ仕様

note への投稿(下書き保存)は別ツール(`/Users/atsushi/Documents/note 自動化/`)が担当し、
このリポジトリは**原稿の管理・公開前チェック・履歴**を担当する、という分担にした場合の受け渡し仕様。

- 向こうの担当: note へのログイン、下書き保存、既存記事の上書き
- こちらの担当: 原稿の保管(git)、公開前チェック(`scripts/note_lint.py`)、方針・投稿順・実績の記録

このファイルの後半に、**そのまま貼れる依頼文**を置いてある。

## 受け取るもの

| 出力先 | 内容 | 受け入れ先 |
| --- | --- | --- |
| `export/posts/*.md` | 記事原稿(front matter 付き) | `posts/` |
| `export/articles.json` | 全記事の状態一覧 | `docs/note-content-calendar.md` |
| `export/practice-knowledge.md` | 安全化済みの実践知 | `docs/note-strategy.md` に統合 |
| `export/profile.md` | プロフィール文と固定記事 | `docs/` |
| `export/published.md` | 公開済み記事の実績 | `docs/note-publication-log.md` |

## front matter の制約

`scripts/note_post.py` の front matter パーサは PyYAML を使わない簡易実装のため、
扱える書き方が限られる。**壊れる書き方**を先に挙げる。

| 書き方 | 結果 |
| --- | --- |
| `title: \|` のブロックスカラー | 値が `\|` になる。**使用不可** |
| 入れ子(`meta:` の下に字下げした `author:`) | 階層が潰れる。**使用不可** |
| 複数行にまたがる値 | 2行目以降が捨てられる。**使用不可** |

扱えるのは次の形だけ。

```
---
title: 記事タイトル
account: 19770104
tags: [タグ1, タグ2]
publish: false
note_url: https://note.com/19770104/n/nxxxxxxxxxxxx
---
```

| キー | 必須 | 内容 |
| --- | --- | --- |
| `title` | ○ | タイトル。`:` を含んでもそのまま書いてよい |
| `account` | ○ | 固定で `19770104` |
| `tags` | | `[a, b]` または `- a` の行形式。先頭の `#` は自動で外れる |
| `publish` | ○ | 常に `false`。公開は人が note 上で判断する |
| `note_url` | | すでに note に下書き/公開がある場合のみ。無ければ行ごと省く |

本文は front matter の直後から。`<!-- -->` で囲んだ注記は取り込み時に除去される。

---

# そのまま貼れる依頼文

以下を「note 自動化」側のツールへ渡す。

---

## 依頼: note 記事データの書き出し

別リポジトリ(git管理)で、note 原稿の保管と公開前チェックを行うことにしました。
そちらで作成済みのデータを、下記の形式で `/Users/atsushi/Documents/note 自動化/export/` へ
書き出してください。**note への公開操作は行わないでください。**

### 1. 記事原稿 → `export/posts/`

1記事1ファイル。ファイル名は `YYYY-MM-DD-内容がわかる短い名前.md`(半角英数とハイフン推奨)。
各ファイルの1行目から、次の front matter を付けてください。

```
---
title: 記事タイトル
account: 19770104
tags: [タグ1, タグ2]
publish: false
note_url: https://note.com/19770104/n/nxxxxxxxxxxxx
---
```

**受け取り側は簡易パーサで読むため、次の制約があります。**

- front matter は1行目の `---` で始め、`---` で閉じる
- **`キー: 値` の1行形式のみ。入れ子、ブロックスカラー(`|` `>`)、複数行の値は使わない**
- 配列は `[a, b]` か、次の行形式のどちらか

  ```
  tags:
    - タグ1
    - タグ2
  ```

- `publish` は必ず `false`
- `note_url` は、その記事がすでに note に存在する場合のみ書く。無ければ**行ごと省く**
- タイトルに `:` が含まれていてもそのまま書いてよい(クォート不要)

本文について。

- front matter の直後から本文を書く。**本文の冒頭にタイトルを再掲しない**(見出しは `##` から)
- 使える書式は 見出し(`##` `###`)、箇条書き(`-`)、引用(`>`)、リンクのみ
- 画像・埋め込み・有料エリアの指定は入れない
- 出典URLは本文中にそのまま書く
- 補足したい注記は `<!-- -->` で囲む(取り込み時に除去されます)
- 文字コードは UTF-8、改行は LF

### 2. 索引 → `export/articles.json`

全記事の状態を1ファイルにまとめてください。

```json
{
  "account": "19770104",
  "pen_name": "夏目漱右",
  "exported_at": "2026-08-05",
  "articles": [
    {
      "file": "posts/2026-08-04-ai-boundary.md",
      "title": "医療職が生成AIを使う前に決めたい境界線",
      "pillar": "AI活用",
      "order": 1,
      "price": "free",
      "status": "draft",
      "note_url": "https://note.com/19770104/n/nxxxxxxxxxxxx",
      "tags": ["生成AI", "医療"]
    }
  ]
}
```

| フィールド | 値 |
| --- | --- |
| `pillar` | `臨床判断` / `若手教育` / `組織運営` / `AI活用` / `固定`(固定記事) |
| `order` | 投稿予定の順番。未定なら `null` |
| `price` | `free` または `paid` |
| `status` | `idea`(構想) / `writing`(執筆中) / `draft`(note に下書き保存済み) / `published`(公開済み) |
| `note_url` | 無ければ `null` |

**構想段階の記事も含めてください**(`file` は `null` で構いません)。投稿順の全体像を移したいためです。

### 3. 実践知 → `export/practice-knowledge.md`

安全化済みの実践知ライブラリをそのまま出力してください。判断原則と、記事に使ってよい実体験を含みます。
**患者・職員の個別エピソード、所属施設が特定できる記述は含めないでください。**

### 4. プロフィールと固定記事 → `export/profile.md`

現行のプロフィール文と、固定記事の原稿。

### 5. 公開実績 → `export/published.md`

すでに公開した記事があれば、公開日 / タイトル / URL / ビュー / スキ / コメント / 購入数。
無ければ「公開済みの記事はなし」と書いてください。

### やらないこと

- note への公開操作
- 記事内容の書き換え(今回は現状の書き出しのみ)
- 公開前確認票(`-review.md`)の書き出し(受け取り側で生成します)

### 完了したら

`export/` 配下のファイル一覧と、記事の本数を教えてください。

---

## 受け取ったあとの手順

1. `export/posts/*.md` を、このリポジトリの `posts/` にコピーする
2. 公開前チェックを通す

   ```bash
   for f in posts/*.md; do python scripts/note_lint.py "$f"; done
   ```

3. `export/articles.json` の内容を `docs/note-content-calendar.md` に反映する
4. `export/practice-knowledge.md` を `docs/note-strategy.md` の「本人性」節に統合する
5. `export/published.md` を `docs/note-publication-log.md` に反映する
6. コミットする(以後、原稿の変更履歴が git に残る)

以降、原稿を直したら公開前チェックを通し、note への反映は向こうのツールに任せる。
このリポジトリから直接 note を操作したい場合は `docs/note-auto-post.md` を参照。
