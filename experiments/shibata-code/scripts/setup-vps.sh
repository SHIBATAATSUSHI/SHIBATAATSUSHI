#!/usr/bin/env bash
# クラウドVPS(または任意の Linux)を Shibata Code の実行環境に仕立てる。
#
# 手元にPCが無く、iPhone から使いたい場合を想定している。
# iPhone のキーボードで長い手順を打つのは辛いので、1コマンドで終わるようにした。
#
# 使い方(VPSにSSHで入ってから):
#   git clone <このリポジトリ> ~/repo
#   bash ~/repo/experiments/shibata-code/scripts/setup-vps.sh
#
# やること:
#   - python3 / git / tmux / ripgrep を入れる
#   - 依存パッケージを入れる(失敗しても標準ライブラリ経路で動くので続行する)
#   - APIキーを置く ~/.config/shibata-code/env を作る(パーミッション 600)
#   - シェルの起動ファイルに読み込みと `sc` エイリアスを足す
#   - tmux を携帯向けに設定する
#
# 何度実行しても同じ状態になる(重複して追記しない)。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="$HOME/.config/shibata-code"
ENV_FILE="$ENV_DIR/env"
MARKER="# >>> shibata-code >>>"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
note() { printf '    %s\n' "$1"; }
warn() { printf '\033[1;33m    ! %s\033[0m\n' "$1"; }

# root で動くコンテナには sudo が無いことがある。あるときだけ使う。
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    warn "root でも sudo でも無い。パッケージの導入は飛ばす"
  fi
fi

# ----------------------------------------------------------------------
# 1. パッケージを入れる
# ----------------------------------------------------------------------
install_packages() {
  local packages="python3 git tmux ripgrep curl"

  if [ "$(id -u)" -ne 0 ] && [ -z "$SUDO" ]; then
    warn "権限が無いのでパッケージ導入を飛ばす"
    return 0
  fi

  if command -v apt-get >/dev/null 2>&1; then
    say "パッケージを入れる (apt)"
    $SUDO apt-get update -qq || true
    # python3-pip は別名。無くても標準ライブラリ経路で動く。
    $SUDO apt-get install -y -qq $packages python3-pip || true
  elif command -v dnf >/dev/null 2>&1; then
    say "パッケージを入れる (dnf)"
    $SUDO dnf install -y -q $packages python3-pip || true
  elif command -v apk >/dev/null 2>&1; then
    say "パッケージを入れる (apk)"
    $SUDO apk add --quiet $packages py3-pip || true
  else
    warn "知っているパッケージマネージャが無い。python3 / git / tmux を自分で入れること"
  fi
}

# ----------------------------------------------------------------------
# 2. Python の依存を入れる(任意)
# ----------------------------------------------------------------------
install_python_deps() {
  say "Python の依存を入れる"
  if ! command -v pip3 >/dev/null 2>&1 && ! python3 -m pip --version >/dev/null 2>&1; then
    warn "pip が無い。--transport http なら SDK 無しでも動くので、このまま進める"
    return 0
  fi
  # 失敗しても止めない。SDK が無くても標準ライブラリ経路で動く。
  if python3 -m pip install --quiet --user -r "$REPO_ROOT/requirements.txt"; then
    note "SDK を入れた(通信は SDK 経路になる)"
  else
    warn "SDK を入れられなかった。--transport http で動かすこと"
  fi
}

# ----------------------------------------------------------------------
# 3. APIキーの置き場を作る
# ----------------------------------------------------------------------
create_env_file() {
  say "APIキーの置き場を作る"
  mkdir -p "$ENV_DIR"
  chmod 700 "$ENV_DIR"

  if [ -f "$ENV_FILE" ]; then
    note "すでにある: $ENV_FILE(上書きしない)"
    return 0
  fi

  cat > "$ENV_FILE" <<'ENVEOF'
# Shibata Code が使うAPIキー。使うものだけ有効にする。
# 編集: nano ~/.config/shibata-code/env

# export ANTHROPIC_API_KEY=sk-ant-...
# export OPENAI_API_KEY=sk-...
# export GEMINI_API_KEY=...
# export DEEPSEEK_API_KEY=sk-...
# export MOONSHOT_API_KEY=sk-...
# export DASHSCOPE_API_KEY=sk-...
# export OPENROUTER_API_KEY=sk-or-...

# 既定のモデルと権限モード(好みで)
# export SHIBATA_CODE_MODEL=opus
# export SHIBATA_CODE_MODE=auto
ENVEOF

  chmod 600 "$ENV_FILE"
  note "作った: $ENV_FILE (600)"
}

# ----------------------------------------------------------------------
# 4. シェルの起動ファイルに足す
# ----------------------------------------------------------------------
configure_shell() {
  say "シェルの設定"

  local rc
  if [ -n "${ZSH_VERSION:-}" ] || [ "$(basename "${SHELL:-}")" = "zsh" ]; then
    rc="$HOME/.zshrc"
  else
    rc="$HOME/.bashrc"
  fi
  touch "$rc"

  if grep -qF "$MARKER" "$rc"; then
    note "すでに設定済み: $rc"
    return 0
  fi

  cat >> "$rc" <<RCEOF

$MARKER
# APIキーを読み込む
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
# tmux に入って Shibata Code を起動する
alias sc='$REPO_ROOT/scripts/sc-mobile.sh'
export SHIBATA_WORKDIR="\${SHIBATA_WORKDIR:-\$HOME}"
# <<< shibata-code <<<
RCEOF

  note "足した: $rc"
}

# ----------------------------------------------------------------------
# 5. tmux を携帯向けに設定する
# ----------------------------------------------------------------------
configure_tmux() {
  say "tmux の設定"
  local conf="$HOME/.tmux.conf"
  touch "$conf"

  if grep -q "aggressive-resize" "$conf" 2>/dev/null; then
    note "すでに設定済み: $conf"
    return 0
  fi

  cat "$REPO_ROOT/scripts/tmux-mobile.conf" >> "$conf"
  note "足した: $conf"
}

# ----------------------------------------------------------------------
main() {
  if [ ! -f "$REPO_ROOT/scripts/sc-mobile.sh" ]; then
    echo "リポジトリの中から実行すること(scripts/ が見つからない)" >&2
    exit 1
  fi

  install_packages
  install_python_deps
  create_env_file
  configure_shell
  configure_tmux
  chmod +x "$REPO_ROOT/scripts/sc-mobile.sh"

  say "ここまで完了。あと2つ"
  cat <<NEXTEOF

  1) APIキーを書く
       nano ~/.config/shibata-code/env
     (行頭の # を外して、キーを貼る)

  2) 設定を読み込んで起動する
       source ~/.bashrc    # zsh なら ~/.zshrc
       sc

  次回からは、SSHで入って sc と打つだけ。
  回線が切れても作業は続いており、繋ぎ直して sc で同じ画面に戻る。

  セキュリティの仕上げ(推奨、別途):
    - 鍵認証を確認してからパスワード認証を切る
    - Tailscale を入れて SSH をインターネットに晒さない
    詳しくは MOBILE.md を参照。

NEXTEOF
}

main "$@"
