#!/usr/bin/env bash
# 携帯からSSHで使うための起動スクリプト。
#
# tmux セッションに入り(無ければ作り)、狭い画面向けの設定で
# Shibata Code を起動する。回線が切れても作業は生き残り、
# 再接続すれば同じ画面に戻れる。
#
# 使い方:
#   ./scripts/sc-mobile.sh                 # $SHIBATA_WORKDIR で起動
#   ./scripts/sc-mobile.sh -m deepseek     # 追加の引数はそのまま渡る
#
# 環境変数:
#   SHIBATA_TMUX_SESSION  tmux セッション名(既定: shibata)
#   SHIBATA_WORKDIR       作業ディレクトリ(既定: $HOME)

set -euo pipefail

SESSION="${SHIBATA_TMUX_SESSION:-shibata}"
WORKDIR="${SHIBATA_WORKDIR:-$HOME}"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux が入っていない。携帯回線は切れるので tmux 無しでは実用にならない。" >&2
  echo "  macOS: brew install tmux / Debian系: sudo apt install tmux" >&2
  exit 1
fi

if [ ! -d "$WORKDIR" ]; then
  echo "作業ディレクトリが無い: $WORKDIR" >&2
  exit 1
fi

# すでに動いていれば、そこへ戻るだけ。
# (前回の作業が走っている場合もあるので、新しく起動し直さない)
if tmux has-session -t "$SESSION" 2>/dev/null; then
  exec tmux attach-session -t "$SESSION"
fi

# エージェント終了後もシェルを残す。誤操作で切れても
# tmux セッションごと消えないようにするため。
CMD=$(printf '%q ' python3 -m shibata_code --compact "$@")

exec tmux new-session -s "$SESSION" -c "$WORKDIR" \
  "$CMD; exec \${SHELL:-/bin/bash}"
