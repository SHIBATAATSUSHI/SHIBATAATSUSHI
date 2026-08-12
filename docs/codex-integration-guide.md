# Claude Code × Codex 連携ガイド

性質の異なる2つのエージェントを併用するための設計と設定。Mac からもケータイからも同じように使える状態を目指す。

## 結論:1対1で役割を分ける。5対5にはしない

「両側に5人ずつチームを組ませたら効率的か」を調べた結果、**逆効果**という結論になった。

| 調査結果 | 出典 |
|---|---|
| 5エージェントを超えると多くのタスクで収穫逓減 | Google Research |
| 3エージェントのパイプラインは約29,000トークン、単一は約10,000トークン(**約3倍**) | 同上 |
| 中央集権的なマルチエージェントは **+285%** のトークンオーバーヘッド。協調なしの独立並列でも +58% | 同上 |
| **逐次的な仕事では性能が落ちる**(PlanCraft で -70%) | 同上 |
| 単一エージェントのベースラインが約45%を超えると、協調コストが利得を上回る | 同上 |

さらに実務上、Claude Pro はレート制限を全用途で共有する。10エージェント同時稼働は制限を即座に食い潰す。

**2社を併用する価値はスループットではなく視点の違いにある。** 数を増やすのではなく役割を分ける。

## 役割分担

| | 役割 |
|---|---|
| **Claude Code** | メインループ。計画・実装・統合・最終判断 |
| **Codex** | セカンドオピニオン。批判的レビュー・独立検証・詰まったときの別解 |

**最終判断は常に Claude Code 側**。Codex の指摘は「採用/不採用を理由つきで決める入力」であって、自動的に従うものではない。ここを固定しないとレビューのたびに方針がぶれる。

同じ作業を両者に並行させない。並列化するのは**互いに依存しない独立作業**のみで、同時に走らせるのは最大3〜5(`/parallel` スキル参照)。

## 共有指示ファイルの構成

両者が同じ前提で動くよう、指示は1箇所に集約する。

```
AGENTS.md   ← 共有指示(単一の情報源)。Codex が読む
CLAUDE.md   ← 先頭で @AGENTS.md をインポート + Claude Code 固有だけ
```

`AGENTS.md` は Linux Foundation が管理する業界標準で、30以上のツールが対応し6万以上のリポジトリで使われている。

**重要**: Claude Code は `AGENTS.md` を自動では読まない。`CLAUDE.md` 先頭の `@AGENTS.md` が唯一の読み込み経路なので、消すと Claude Code だけ指示ゼロで動き出す。しかもエラーは出ない。

新しいリポジトリを作るときも同じ形にする。

## 双方向 MCP の設定(Mac)

どちらを外側のループにしても相手を呼べる状態にしておく。

### Claude Code → Codex(公式プラグイン)

Mac のターミナルで Claude Code を開き、上から順に打つ。`/plugin` はターミナル専用で、ケータイやクラウドセッションからは実行できない。

```
/plugin marketplace add openai/codex-plugin-cc
```
```
/plugin install codex@openai-codex
```
```
/reload-plugins
```
```
/codex:setup
```

前提は ChatGPT の契約(無料枠でも可)または OpenAI API キーと、Node.js 18.18 以上。ログインを求められたら `!codex login`。

使えるようになるコマンド:

| コマンド | 用途 |
|---|---|
| `/codex:review` | 通常のレビュー(読み取りのみ) |
| `/codex:adversarial-review` | 設計判断そのものを問い直す批判的レビュー |
| `/codex:rescue <指示>` | タスクを委譲(バグ調査など) |
| `/codex:transfer` | 今の会話ごと Codex に引き継ぐ |
| `/codex:status` / `/codex:result` / `/codex:cancel` | 実行中ジョブの確認・結果取得・中止 |

### Codex → Claude Code(逆方向)

`~/.codex/config.toml` に次の内容を**追記する**。これはファイルの中身であって、ターミナルに打つコマンドではない。`[...]` を直接シェルに貼ると zsh がワイルドカードと解釈して `no matches found` になる。

```toml
[mcp_servers.claude-code]
command = "claude"
args = ["mcp", "serve"]
startup_timeout_sec = 30
tool_timeout_sec = 120
```

ターミナルから安全に追記するには、次をまとめて貼る(既存の内容は消えない):

```bash
mkdir -p ~/.codex
cat >> ~/.codex/config.toml <<'TOML_END'

[mcp_servers.claude-code]
command = "claude"
args = ["mcp", "serve"]
startup_timeout_sec = 30
tool_timeout_sec = 120
TOML_END
```

追記後に `cat ~/.codex/config.toml` で確認する。**2回実行すると同じセクションが重複し TOML として不正になる**ので、既に `[mcp_servers.claude-code]` があるときは実行しない。

呼べるツールを絞りたいときは `enabled_tools = ["Read", "GrepTool", "GlobTool"]`、一時的に止めたいときは `enabled = false`。

## ケータイから使う

**両者の構造は同じ**。「クラウドで動くもの」と「自分の Mac で動くもの」の2系統があり、後者は接続作業が要る。

| | クラウド側 | ローカル(Mac)側 |
|---|---|---|
| **Claude Code** | クラウドセッション(GitHub リポジトリ) | Remote Control 常駐 → デバイスカードから起こす |
| **Codex** | Codex cloud(GitHub リポジトリ。接続作業なしで ChatGPT アプリに出る) | Codex for Mac と QR でペアリング |

### Codex をケータイと繋ぐ手順(多くの場合は不要)

**先に確認**: この手順は「Codex を**単独で**ケータイから操作したい」場合にだけ必要。以下の経路が既にあるので、通常はやらなくてよい。

```
ケータイ → (Remote Control) → Mac の Claude Code → (/codex:* プラグイン) → Codex CLI
```

Claude Code が Codex を呼べて、その Claude Code セッションがケータイから触れる以上、**ケータイから Codex に仕事を投げる導線は既に通っている**。役割分担も「Claude 主導・Codex レビュー」なので、Codex を単独で叩く場面はそもそも少ない。

前提: **Codex for Mac(デスクトップアプリ)** と ChatGPT モバイルアプリの最新版。両方で**同じ ChatGPT アカウント・ワークスペース**にサインインしていること。CLI だけでは繋がらない。**Apple Silicon 必須**。

アプリが入っているかの確認:

```bash
ls /Applications | grep -i codex
```

無ければ導入する:

```bash
brew install --cask codex-app
```

1. **Mac**: Codex アプリを開き、サイドバーの **Set up Codex mobile** から QR コードを表示する
2. **iPhone**: その QR をカメラで読む。ChatGPT が開き、ワークスペースの確認と(設定していれば)SSO・MFA・パスキーの手順を経て、Mac が接続済みホストとして一覧に出る
3. **Mac**: Codex アプリの **Settings > Connections** で接続先を管理する

接続後、**Keep this Mac awake** を有効にしておく。Mac がスリープしたりネットワークが切れたり Codex アプリが閉じると、モバイル側のセッションはホストに再到達できるまで止まる。Claude Code の Remote Control と同じ制約。

必要なら **Computer Use** と Chrome 拡張も有効にできる。ローカルアプリやブラウザ操作まで Codex の守備範囲が広がる。

### できること

ケータイからタスクの作成・検索・フォーク・差分確認・コマンド承認ができる。Codex はクラウド環境でタスクを非同期実行できるので、投げて離れる使い方に向く。

## 使い分けの指針

**Codex に見せるべきとき**

- 実装が一通り終わり、出荷前に別の目を入れたいとき → `/codex:review`
- 設計判断に自信がないとき、または自分の案に固執している自覚があるとき → `/codex:adversarial-review`
- 同じバグで2回以上つまずいたとき(視点を変えたほうが速い) → `/codex:rescue`

**見せなくていいとき**

- 単純な追加・修正。レビューのコストが利得を上回る
- 既に方針が確定していて、迷いがないとき
- 探索的な調査の途中。結論が固まる前に外の意見を入れても発散するだけ

判断を定型化したものが `/second-opinion` スキル。

## 落とし穴

- **`AGENTS.md` だけ置いても Claude Code には効かない**。`CLAUDE.md` の `@AGENTS.md` が必須
- **Codex の利用は ChatGPT 側の上限を消費する**。レビューを機械的に全変更へかけると上限に当たる
- **Codex の指摘を無条件に採用しない**。別モデルの意見であって正解ではない。採否を理由つきで決める
- **両者に同じ作業をさせない**。マージ時に矛盾を解消するコストが、並列で得た時間を上回る
