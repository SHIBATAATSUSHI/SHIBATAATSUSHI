# Shibata Code

端末で動くコーディングエージェント。Claude Code のようなものを、中身が
読み切れる分量で自作したもの。ファイルの読み書き・検索・シェル実行を
権限確認を挟みながら実行する。

**Anthropic だけでなく、OpenAI / Gemini / DeepSeek / Kimi / Qwen /
OpenRouter / ローカルモデルを切り替えて使える。** 会話の途中で
`/model` を変えても履歴は引き継がれる。

## できること

- ファイルを読む・書く・部分編集する(`read` / `write` / `edit`)
- ファイル名と中身を検索する(`glob` / `grep`。`rg` があれば使う)
- シェルコマンドを実行する(`bash`)
- 思考の要約と本文をストリーミング表示する
- 実行の直前に確認を挟む(モードで強さを変えられる)
- 会話を保存して後から再開する
- モデルとプロバイダを会話の途中で切り替える
- 履歴が長くなったら自動で要約して畳む(コンテキスト上限で止まらない)
- `.gitignore` を読んで検索対象から除外する

## セットアップ

```bash
cd experiments/shibata-code
pip install -r requirements.txt
```

使いたいプロバイダのキーだけ設定すればいい。

```bash
export ANTHROPIC_API_KEY=...     # Claude(または `ant auth login`)
export OPENAI_API_KEY=...        # GPT
export GEMINI_API_KEY=...        # Gemini
export DEEPSEEK_API_KEY=...      # DeepSeek
export MOONSHOT_API_KEY=...      # Kimi
export DASHSCOPE_API_KEY=...     # Qwen
export OPENROUTER_API_KEY=...    # OpenRouter(1本で多数のモデル)
```

`pip install -e .` すると `shibata-code` コマンドとして入る。入れずに
`python -m shibata_code` でもそのまま動く。

## 使い方

```bash
# 対話モード(カレントディレクトリが作業対象)
python -m shibata_code

# 1回だけ実行して終了
python -m shibata_code -p "tests/ のテストを実行して、落ちているものを直して"

# モデルを選ぶ
python -m shibata_code -m opus       # Claude Opus 5(既定)
python -m shibata_code -m fable      # Claude Fable 5
python -m shibata_code -m gemini     # Gemini 2.5 Pro
python -m shibata_code -m deepseek   # DeepSeek
python -m shibata_code -m kimi       # Kimi
python -m shibata_code -m qwen       # Qwen

# 一覧に無いモデルは プロバイダ:モデルID で直接指定する
python -m shibata_code -m openrouter:z-ai/glm-5.2
python -m shibata_code -m deepseek:deepseek-reasoner

# ローカルモデル(Ollama / LM Studio 等)
python -m shibata_code -m local:qwen3-coder --base-url http://localhost:11434/v1

# 標準入力から対象を渡す
git diff | python -m shibata_code -p "この差分をレビューして"

# 前回の続きから
python -m shibata_code --resume

# 使えるモデルの一覧
python -m shibata_code --models
```

### 主なオプション

| オプション | 説明 |
| --- | --- |
| `-m, --model` | 短縮名(`--models` で一覧)、または `プロバイダ:モデルID` |
| `--base-url` | OpenAI 互換の接続先を上書き(ローカルモデル等) |
| `-e, --effort` | `low` / `medium` / `high`(既定) / `xhigh` / `max` |
| `--mode` | 権限モード。`ask`(既定) / `auto` / `yolo` |
| `-C, --workspace` | 作業ディレクトリ。ファイル操作はこの外に出られない |
| `--max-tokens` | 1リクエストの出力上限(既定 32000) |
| `--thinking` | `summarized`(既定)で思考の要約を表示、`omitted` で非表示 |
| `--reasoning-effort` | OpenAI 互換モデルにも `reasoning_effort` を送る |
| `--transport` | `auto`(既定) / `sdk` / `http`。`http` は SDK 不要 |
| `--no-compaction` | 履歴が長くなっても自動で畳まない |
| `--context-window` | コンテキスト長を明示する(畳む閾値の計算に使う) |
| `--compact` | 出力を絞る(携帯など細い画面向け) |
| `--width` | 端末幅を明示する(自動検出の上書き) |
| `--resume` | 最後に保存したセッションを再開 |

環境変数 `SHIBATA_CODE_MODEL` / `SHIBATA_CODE_EFFORT` / `SHIBATA_CODE_MODE` /
`SHIBATA_CODE_BASE_URL` で既定値を変えられる。

### 対話中のコマンド

```
/help              コマンド一覧
/model [名前]      モデルの表示・変更(プロバイダを跨いでもよい)
/models            使えるモデルの短縮名一覧
/providers         プロバイダと必要な環境変数の一覧
/effort [段階]     effort の表示・変更
/mode [モード]     権限モードの表示・変更
/max-tokens [N]    出力上限の表示・変更
/tools             ツール一覧
/cost              トークン利用量と概算コスト
/compact           履歴をいま畳む(長い作業の区切りに)
/clear             会話履歴を消す
/save              セッションを保存
/exit              終了(Ctrl-D も同じ)
```

## 携帯から使う

2通りある。

**PCを挟む(推奨)** — 手順は **[MOBILE.md](MOBILE.md)**。
全機能が使えて速い。

**iPhone だけで完結** — 手順は **[IPHONE.md](IPHONE.md)**。
追加パッケージ不要(`--transport http` で標準ライブラリだけで通信する)。
ただし a-Shell ではシェルを起動できないため `bash` ツールが使えず、
テスト実行や git 操作はできない。

推奨構成は、エージェント本体を自宅PCで動かし、Tailscale 経由で繋ぎ、
tmux で回線切断に備えるもの。**手元にPCが無い場合は、クラウドに小さな
Linux を1台借りれば同じことができる**(月500〜1,100円程度。契約から
セットアップまで iPhone だけで完結する)。

```bash
# VPSにSSHで入ってから、この2行だけ
git clone <このリポジトリ> ~/repo
bash ~/repo/experiments/shibata-code/scripts/setup-vps.sh
```

起動用のスクリプトと tmux 設定も `scripts/` に置いてある。

```bash
chmod +x scripts/sc-mobile.sh
./scripts/sc-mobile.sh          # tmux に入って --compact で起動
```

`--compact` は思考の表示を省き、ツール結果を3行に絞り、端末の実際の幅に
合わせて行を切る。細い画面で折り返しに埋もれるのを防ぐためのモード。

## 対応モデル

短縮名は近道で、**モデルIDはプロバイダ側の都合で頻繁に変わる**。
`--models` の表示が古い場合は `プロバイダ:モデルID` で直接指定すること。

| プロバイダ | 短縮名 | 必要な環境変数 |
| --- | --- | --- |
| `anthropic` | `fable` `mythos` `opus` `sonnet` `haiku` | `ANTHROPIC_API_KEY` |
| `openai` | `gpt` `gpt-mini` | `OPENAI_API_KEY` |
| `google` | `gemini` `gemini-flash` | `GEMINI_API_KEY` |
| `deepseek` | `deepseek` `deepseek-reasoner` | `DEEPSEEK_API_KEY` |
| `moonshot` | `kimi` | `MOONSHOT_API_KEY` |
| `qwen` | `qwen` `qwen-max` | `DASHSCOPE_API_KEY` |
| `openrouter` | (IDで指定) | `OPENROUTER_API_KEY` |
| `local` | (IDで指定) | 不要 |

コスト概算は Anthropic のモデルだけ出る。他社は価格を入れていないので、
トークン数だけ表示して「価格未設定」と出る(古い価格を埋め込んで
誤解を招くより、出さないほうがましだと判断した)。

## 権限モード

| モード | ファイル変更 | シェル実行 |
| --- | --- | --- |
| `ask`(既定) | 毎回確認 | 毎回確認 |
| `auto` | 自動 | 毎回確認 |
| `yolo` | 自動 | 自動 |

`auto` でも、`rm -rf` / `sudo` / `git push` / `curl \| sh` のような
取り返しがつきにくいコマンドは確認を挟む(`permissions.py` の
`RISKY_PATTERNS`)。読み取り専用ツールはどのモードでも確認しない。

## 構成

```
shibata_code/
  cli.py          CLI と対話ループ
  agent.py        エージェントループ(プロバイダ非依存)
  backends/       プロバイダごとの送受信
    base.py            共通インターフェースと正規化した停止理由
    anthropic_backend.py  SDK版 + 標準ライブラリ版
    openai_backend.py     SDK版 + 標準ライブラリ版
    http_transport.py     urllib だけで喋る HTTP + SSE
  providers.py    接続先と環境変数の定義
  config.py       モデルの素性と実行設定
  messages.py     プロバイダ非依存の会話履歴表現
  compaction.py   履歴が長くなったときの要約と畳み込み
  gitignore.py    .gitignore の解釈
  session.py      履歴と利用量、保存・再開
  tools.py        ツール6種の定義と実行
  permissions.py  権限判定
  workspace.py    パス境界の検証
  prompts.py      システムプロンプト
  ui.py           端末表示
```

## 設計メモ

**履歴は中立表現で持つ。** Anthropic と OpenAI 互換ではメッセージの形が
違うので、履歴は `messages.Message` という自前の形で保持し、送信直前に
各バックエンドがそれぞれの形へ変換する。おかげで会話の途中でモデルを
乗り換えても続きから話せる。アシスタントの発言には元の生データも
添えてあり、同じモデルで続ける場合はそちらをそのまま送り直す
(Anthropic の thinking ブロックは改変せず返す必要があるため)。
別のモデルに移った時点で生データは使わず、中立表現から組み直す。

**互換の穴は握って埋める。** OpenAI 互換とはいえ細部は揃っていない。
`stream_options` を受けない、`reasoning_effort` を知らない、チャンクごとに
ツール名を丸ごと送り直す(素朴に連結すると `readread` になる)といった
差がある。前者はパラメータを外して1度だけ再試行し、後者は既知のツール名と
突き合わせて直している。

**作業ディレクトリの外に出さない。** モデルが渡してくるパスは信頼できない
入力なので、`Workspace.resolve()` を必ず通す。`..` と絶対パスに加えて、
シンボリックリンクを展開したあとの位置も見て境界を判定する。

**編集前に読ませる。** `edit` は、そのファイルを `read` していないと失敗する。
さらに `read` 以降にファイルが変わっていた場合も失敗させ、読み直しを促す。
専用ツールにしておくと、こうした不変条件をハーネス側で強制できる。

**ツール結果は1通にまとめる。** 並列に呼ばれた複数のツールの結果を別々の
メッセージに分けて返すと、モデルは並列呼び出しをやめてしまう
(OpenAI 形式は仕様上1件1メッセージなので、そちらは分けて送る)。

**プロンプトキャッシュを壊さない。** システムプロンプトはセッション中固定にし、
ツールの並びも登録順で固定する。`/model` でモデルを変えるとキャッシュは
作り直しになる。

**失敗はモデルに返す。** ツールが失敗しても落とさず、エラーとして返して
回復させる。ユーザーが実行を拒否した場合も同じ。

**履歴は畳んでも壊さない。** 長い作業ではコンテキスト長を超える前に、古い
履歴をモデル自身に要約させて1件に置き換える。ここで絶対にやってはいけない
のが、ツール呼び出しとその結果を分断することで、片方だけ残った履歴は
次のリクエストごと拒否される。そのため**分割位置はユーザーの発言に限って
いる**(ツール結果の直前では切らない)。要約に失敗しても履歴は変えずに
そのまま続ける。畳めないことは作業を止める理由にはならない。

同じ理由で、ツール実行中に中断された場合も、呼び出された分すべてに結果を
埋めてから中断を伝える。結果の欠けた `tool_call` を履歴に残すと、次に
話しかけた時点で API に拒否されるため。

**発動の判断は実測値でやる。** 履歴の大きさは自前で数えず、直前の応答の
`usage` から逆算する。`input_tokens` はキャッシュに載らなかった分しか
含まないので、キャッシュ読み書きの分を足して実際の送信量を出している。

**停止理由を正規化する。** `end_turn` / `tool_use` / `max_tokens` /
`refusal` / `pause_turn` に揃え、プロバイダごとの表記の違いは
バックエンドの中で吸収する。`finish_reason` を正しく返さない実装が
あるので、ツール呼び出しが実在する場合はそちらを優先する。

## テスト

```bash
cd experiments/shibata-code
python -m unittest discover -s tests
```

260件。外部通信は一切しない。

- `test_workspace.py` / `test_tools.py` — パス境界と各ツール
- `test_ui.py` — 細い画面での切り詰めと compact モード
- `test_compaction.py` — 畳む位置の安全性(ツール呼び出しと結果を分断しない)、
  要約失敗時に履歴を壊さないこと、中断時に結果が欠けないこと
- `test_gitignore.py` — パターンの解釈と、検索からの除外
- `test_no_sdk.py` — SDK の import を遮断した状態で1往復させ、
  追加パッケージ無しで動くことを確かめる(iOS 相当の検証)
- `test_config_session.py` — モデル解決、プロバイダ、権限判定、履歴の保存
- `test_backends.py` — 両バックエンドの変換と、ストリーミング集約
  (分割到着するツール引数、並列ツール呼び出し、壊れたJSON、互換エラー時の再試行)
- `test_agent.py` — フェイクのバックエンドで停止理由の分岐・ツール往復・
  権限拒否・プロバイダ切り替えを検証
- `test_cli.py` — 引数解釈とスラッシュコマンド
- `test_integration_sse.py` / `test_integration_openai.py` — ローカルに
  それぞれの API を模したサーバを立て、**本物の SDK** をそこへ向ける。
  SDK のストリーミング解析を通ったあとにループが動くところまで確認する

## 制限

- サブエージェント、MCP、Web検索には対応していない
- `.gitignore` の解釈は主要な書き方に絞った部分実装(入れ子の `.gitignore` は読まない)
- 実APIへの疎通確認はしていない(この環境に認証情報が無いため)。
  SDK 経路・標準ライブラリ経路とも、モックサーバで検証済み
- iOS 上での実機確認はしていない(手元にiPhoneが無いため)。
  SDK 無しで動くことはテストで確認済みだが、a-Shell 固有の挙動は未確認
- 他社モデルの価格は未設定。コスト概算は Anthropic のみ
- プリセットのモデルIDは古くなる。`プロバイダ:モデルID` での指定が確実
