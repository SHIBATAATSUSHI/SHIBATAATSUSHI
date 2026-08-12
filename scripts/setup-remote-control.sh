#!/bin/zsh
# Claude Code の Remote Control をサーバーモードで常駐させる LaunchAgent を設定する。
#
# ログイン時に自動起動し、落ちても自動で復活する。常駐すると、ケータイの
# Claude アプリのデバイスカードから、そのディレクトリで新規セッションを起こせる。
#
# プロジェクトごとに1つずつ登録できる。ラベルは作業ディレクトリ名から導出するので、
# 複数のプロジェクトを並べて常駐させても衝突しない。
#
# 使い方:
#   ./setup-remote-control.sh [作業ディレクトリ] [表示名]   登録する(既定: ~/claude/claudecode)
#   ./setup-remote-control.sh --list                        登録済みの一覧を出す
#   ./setup-remote-control.sh --remove <スラッグ>           登録を解除する
#
# 例:
#   ./setup-remote-control.sh ~/SHIBATAATSUSHI "SHIBATAATSUSHI"
#   ./setup-remote-control.sh ~/Documents/investment-dashboard "投資"
#   ./setup-remote-control.sh --remove investment-dashboard

set -euo pipefail

LABEL_PREFIX="com.shibata.claude-rc"
LA_DIR="$HOME/Library/LaunchAgents"
LOGDIR="$HOME/Library/Logs"
UID_NUM="$(id -u)"

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

# ディレクトリ名から LaunchAgent のラベルに使えるスラッグを作る。
# 表示名ではなくディレクトリ名を使うのは、日本語の表示名だと空になるため。
slugify() {
  local s
  s="$(basename "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g; s/--*/-/g; s/^-//; s/-$//')"
  if [[ -z "$s" ]]; then
    s="$(printf '%s' "$1" | cksum | cut -d' ' -f1)"
  fi
  printf '%s' "$s"
}

# --- サブコマンド ---------------------------------------------------------

case "${1:-}" in
  --help|-h)
    usage
    exit 0
    ;;
  --list|-l)
    found=0
    # zsh は一致しないグロブでエラーになるため find で列挙する
    while IFS= read -r plist; do
      [ -n "$plist" ] || continue
      label="$(basename "$plist" .plist)"
      workdir="$(sed -n 's|.*<key>WorkingDirectory</key><string>\(.*\)</string>.*|\1|p' "$plist" | head -1)"
      if launchctl print "gui/$UID_NUM/$label" >/dev/null 2>&1; then
        state="稼働中"
      else
        state="停止"
      fi
      printf '%-44s %-8s %s\n' "$label" "$state" "$workdir"
      found=1
    done <<EOF
$(find "$LA_DIR" -maxdepth 1 -name "$LABEL_PREFIX*.plist" 2>/dev/null | sort)
EOF
    if [ "$found" -eq 0 ]; then
      echo "登録済みの常駐はありません。"
    fi
    exit 0
    ;;
  --remove|-r)
    slug="${2:-}"
    if [[ -z "$slug" ]]; then
      echo "エラー: 解除するスラッグを指定してください。--list で確認できます。" >&2
      exit 1
    fi
    # 完全なラベルを渡された場合も受け付ける
    if [[ "$slug" == "$LABEL_PREFIX"* ]]; then
      label="$slug"
    else
      label="$LABEL_PREFIX-$slug"
    fi
    launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
    if [[ -f "$LA_DIR/$label.plist" ]]; then
      rm "$LA_DIR/$label.plist"
      echo "解除しました: $label"
    else
      echo "見つかりませんでした: $label" >&2
      exit 1
    fi
    exit 0
    ;;
esac

# --- 登録 -----------------------------------------------------------------

WORKDIR="${1:-$HOME/claude/claudecode}"
NAME="${2:-MacBook}"

if ! command -v claude >/dev/null 2>&1; then
  echo "エラー: claude コマンドが見つかりません。Claude Code をインストールしてください。" >&2
  exit 1
fi

# 引数はそのまま plist(XML)に埋め込むため、XML を壊す文字を弾く
case "$NAME" in
  *[\<\>\&\"]*)
    echo "エラー: 表示名に < > & \" は使えません: $NAME" >&2
    exit 1
    ;;
esac

if [[ ! -d "$WORKDIR" ]]; then
  echo "エラー: 作業ディレクトリがありません: $WORKDIR" >&2
  exit 1
fi
# シンボリックリンクを解決した絶対パスにする(launchd は相対パスを解釈しない)
WORKDIR="$(cd "$WORKDIR" && pwd -P)"

SLUG="$(slugify "$WORKDIR")"
LABEL="$LABEL_PREFIX-$SLUG"
PLIST="$LA_DIR/$LABEL.plist"

echo "作業ディレクトリ: $WORKDIR"
echo "表示名          : $NAME"
echo "ラベル          : $LABEL"
echo

mkdir -p "$LA_DIR" "$LOGDIR"

# 同じラベルが既に登録済みなら一度外す(未登録のときのエラーは無視する)
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true

# /bin/zsh -lc でログインシェルを通すので、claude の絶対パスを調べなくてよい
cat > "$PLIST" <<PLIST_END
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>exec claude remote-control --name "$NAME"</string>
  </array>
  <key>WorkingDirectory</key><string>$WORKDIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$LOGDIR/$LABEL.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/$LABEL.err.log</string>
</dict>
</plist>
PLIST_END

echo "作成: $PLIST"

launchctl bootstrap "gui/$UID_NUM" "$PLIST"
echo "登録: $LABEL"
echo
echo "起動を待っています..."
sleep 5

if launchctl print "gui/$UID_NUM/$LABEL" 2>/dev/null | grep -qE '^[[:space:]]*state = running'; then
  echo "✓ 常駐しています。"
  echo
  echo "  ケータイの Claude アプリ →「Code」タブ → 上部のデバイスカードをタップ。"
  echo "  「ディレクトリを選択」に $WORKDIR が並びます。"
  echo "  (右下の「+ 新規セッション」はクラウドセッションを作るので別物です)"
else
  echo "✗ 起動していないようです。ログを確認してください:"
  echo
  tail -20 "$LOGDIR/$LABEL.err.log" 2>/dev/null || echo "  (エラーログはまだありません)"
  echo
  echo "  端末(TTY)を要求するエラーが出ている場合は、tmux を噛ませる必要があります。"
  echo "  docs/mac-mobile-sync-guide.md の「動かないとき」を参照してください。"
  exit 1
fi
