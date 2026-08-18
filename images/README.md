# images — note の見出し画像(サムネ)

`posts/` の原稿から front matter で参照する。記事の一部なので `.gitignore` しない。

```yaml
eyecatch: images/2026-08-11-workload.png
```

## 決めごと

- ファイル名は原稿と対応させる(`posts/2026-08-11-workload-not-headcount.md` → `images/2026-08-11-workload.png`)
- 形式は `.png` / `.jpg` / `.jpeg` / `.webp`
- 1枚 2MB を目安にする。超えると `note_post.py` が警告を出す。リポジトリを重くしないため
- 画風の仕様は `docs/note-strategy.md` の「見出し画像の仕様」節を正とする

## 誰が作るか

生成は GPT(Codex)が担当する。既存記事の画風に合わせたうえで、このディレクトリへ
置いて push してもらう。このリポジトリのツールは、置かれた画像を note へ貼るだけ。
