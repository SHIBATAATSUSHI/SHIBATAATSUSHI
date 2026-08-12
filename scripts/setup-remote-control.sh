#!/bin/zsh
# Claude Code の Remote Control をサーバーモードで常駐させる LaunchAgent を設定する。
#
# ログイン時に自動起動し、落ちても自動で復活する。常駐すると、ケータイの
# Claude アプリから「新規セッション」で Mac 上の作業を起こせるようになる。
#
# 使い方:
#   ./setup-remote-control.sh [作業ディレクトリ] [表示名]
#   例) ./setup-remote-control.sh ~/claude/claudecode "MacBook"
#
# 止めるとき:
#   launchctl bootout gui/$(id -u)/com.shibata.claude-rc

set -euo pipefail

WORKDIR="${1:-$HOME/claude/claudecode}"
NAME="${2:-MacBook}"
LABEL="com.shibata.claude-rc"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$HOME/Library/Logs"
UID_NUM="$(id -u)"

# --- 前提確認 -----------------------------------------------------------

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

# シンボリックリンクを解決した絶対パスにする(launchd は相対パスを解釈しない)
if [[ ! -d "$WORKDIR" ]]; then
  echo "エラー: 作業ディレクトリがありません: $WORKDIR" >&2
  exit 1
fi
WORKDIR="$(cd "$WORKDIR" && pwd -P)"

echo "作業ディレクトリ: $WORKDIR"
echo "表示名          : $NAME"
echo

# --- LaunchAgent を書き出す ---------------------------------------------

mkdir -p "$HOME/Library/LaunchAgents" "$LOGDIR"

# 既に登録済みなら一度外す(未登録のときのエラーは無視する)
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
  <key>StandardOutPath</key><string>$LOGDIR/claude-rc.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/claude-rc.err.log</string>
</dict>
</plist>
PLIST_END

echo "作成: $PLIST"

# --- 登録して状態を確認する ---------------------------------------------

launchctl bootstrap "gui/$UID_NUM" "$PLIST"
echo "登録: $LABEL"
echo
echo "起動を待っています..."
sleep 5

if launchctl print "gui/$UID_NUM/$LABEL" 2>/dev/null | grep -qE '^[[:space:]]*state = running'; then
  echo "✓ 常駐しています。"
  echo
  echo "  ケータイの Claude アプリ →「Code」タブに「$NAME」が出ているか確認してください。"
  echo "  そこから「新規セッション」で Mac 上の作業を起こせます。"
else
  echo "✗ 起動していないようです。ログを確認してください:"
  echo
  tail -20 "$LOGDIR/claude-rc.err.log" 2>/dev/null || echo "  (エラーログはまだありません)"
  echo
  echo "  端末(TTY)を要求するエラーが出ている場合は、tmux を噛ませる必要があります。"
  echo "  docs/mac-mobile-sync-guide.md の「動かないとき」を参照してください。"
  exit 1
fi
