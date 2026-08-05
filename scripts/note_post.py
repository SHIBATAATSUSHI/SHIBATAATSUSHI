#!/usr/bin/env python3
"""Markdown で書いた記事を note に投稿する。

既定は**下書き保存**まで。公開したいときだけ `--publish` を付ける。

使い方:
    uv run scripts/note_post.py docs/note/kiji.md --account tora
    uv run scripts/note_post.py docs/note/kiji.md --dry-run
    uv run scripts/note_post.py docs/note/kiji.md --publish --tag AI --tag 日記
    uv run scripts/note_post.py --list-accounts
"""

from __future__ import annotations

import argparse
import json
import sys

import note_lint
import note_markdown
from note_api import AuthError, NoteClient, NoteError, load_accounts, resolve_account


def build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数の定義を組み立てる。"""
    parser = argparse.ArgumentParser(
        prog="note_post.py",
        description="Markdown 記事を note に投稿する(既定は下書き保存)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("markdown", nargs="?", help="投稿する Markdown ファイル")
    parser.add_argument("--account", "-a", help="投稿先アカウント名(例: tora)")
    parser.add_argument("--title", "-t", help="タイトル(省略時はフロントマターか先頭の見出し)")
    parser.add_argument(
        "--tag", action="append", default=[], dest="tags", help="ハッシュタグ(複数指定可)"
    )
    parser.add_argument("--eyecatch", help="アイキャッチ画像のパス")
    parser.add_argument(
        "--publish", action="store_true", help="下書きで止めず公開まで行う(既定は下書き)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="note に接続せず、変換結果だけを表示する(アカウント設定も不要)",
    )
    parser.add_argument("--json", action="store_true", help="結果を JSON で出力する")
    parser.add_argument(
        "--skip-lint", action="store_true", help="投稿前の文体チェックを飛ばす"
    )
    parser.add_argument(
        "--list-accounts", action="store_true", help="設定済みのアカウント一覧を表示する"
    )
    return parser


def run_lint(path: str, *, quiet: bool) -> int:
    """投稿前に文体を検査する。エラーがあれば投稿を止めるため件数を返す。

    警告は止めない(見落としを拾う道具であって、縛る道具ではないため)。
    """
    report = note_lint.lint_file(path)
    if not quiet and report.findings:
        print(note_lint.format_report(report))
        print()
    if report.errors and not quiet:
        print("文体チェックでエラーがあるため投稿しません(--skip-lint で無視できます)。")
    return len(report.errors)


def cmd_list_accounts(as_json: bool) -> int:
    """設定済みアカウントを一覧表示する。Cookie は伏せて出す。"""
    accounts = load_accounts()
    if as_json:
        print(
            json.dumps(
                {
                    name: {"urlname": a.urlname, "source": a.source, "session": a.masked_session}
                    for name, a in sorted(accounts.items())
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not accounts:
        print("note のアカウント設定が見つかりません。\n")
        from note_api import COOKIE_HELP

        print(COOKIE_HELP)
        return 1

    print(f"設定済みアカウント: {len(accounts)} 件\n")
    for name, a in sorted(accounts.items()):
        print(f"  {name}")
        print(f"    note ユーザー名 : {a.urlname}")
        print(f"    プロフィール    : https://note.com/{a.urlname}")
        print(f"    Cookie          : {a.masked_session}")
        print(f"    設定元          : {a.source}")
    return 0


def cmd_dry_run(
    path: str, title_override: str | None, tags: list[str], as_json: bool, skip_lint: bool
) -> int:
    """note に接続せず、変換結果だけを表示する。"""
    if not skip_lint and not as_json:
        print(note_lint.format_report(note_lint.lint_file(path)))
        print()

    result = note_markdown.convert_file(path)
    title = title_override or result.title
    all_tags = tags or result.tags

    if as_json:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "title": title,
                    "tags": all_tags,
                    "body_length": result.text_length,
                    "body_html": result.body_html,
                    "warnings": result.warnings,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"タイトル : {title or '(未設定)'}")
    print(f"タグ     : {'、'.join(all_tags) if all_tags else '(なし)'}")
    print(f"本文長   : {result.text_length} 文字")
    for w in result.warnings:
        print(f"警告     : {w}")
    print("\n--- 生成される HTML ---")
    print(result.body_html)

    if not title:
        print("\n注意: タイトルが決まりません。--title を指定するか、先頭に `# 見出し` を書いてください。")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_accounts:
        return cmd_list_accounts(args.json)

    if not args.markdown:
        parser.error("投稿する Markdown ファイルを指定してください(または --list-accounts)")

    if args.dry_run:
        return cmd_dry_run(args.markdown, args.title, args.tags, args.json, args.skip_lint)

    # 投稿前に文体を検査する。ネットワークに触る前に落としたいので先に行う
    if not args.skip_lint and run_lint(args.markdown, quiet=args.json):
        return 1

    # --- ここから先は note に接続する ---
    account = resolve_account(args.account)

    client = NoteClient(account)
    if not args.json:
        print(f"アカウント '{account.name}' ({account.urlname}) で認証しています…")
    client.authenticate()

    # 画像は本文の変換中にアップロードする
    result = note_markdown.convert_file(args.markdown, image_resolver=client.upload_image)

    title = args.title or result.title
    if not title:
        raise NoteError(
            "タイトルが決まりません。--title を指定するか、Markdown の先頭に "
            "`# 見出し` かフロントマターの `title:` を書いてください。"
        )

    tags = args.tags or result.tags

    if not args.json:
        action = "公開" if args.publish else "下書き保存"
        print(f"「{title}」を{action}します({result.text_length} 文字)…")

    post = client.post(
        title=title,
        body_html=result.body_html,
        body_length=result.text_length,
        image_keys=result.image_keys,
        hashtags=tags,
        eyecatch=args.eyecatch,
        publish=args.publish,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "account": account.name,
                    "title": title,
                    "tags": tags,
                    "published": post.published,
                    "note_key": post.note_key,
                    "edit_url": post.edit_url,
                    "public_url": post.public_url,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"\n{'公開しました' if post.published else '下書きを保存しました'}")
        print(f"  編集画面 : {post.edit_url}")
        if post.public_url:
            print(f"  公開URL  : {post.public_url}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AuthError as e:
        # 認証まわりは対処法まで含めて出す。スタックトレースは邪魔なので出さない
        print(f"\n認証エラー: {e}", file=sys.stderr)
        sys.exit(2)
    except NoteError as e:
        print(f"\nエラー: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n中断しました。", file=sys.stderr)
        sys.exit(130)
