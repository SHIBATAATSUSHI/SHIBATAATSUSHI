# Mac とケータイの連携ガイド

## なぜ iPhone に `SHIBATAATSUSHI` しか出てこないのか

iPhone の Claude Code(claude.ai/code / Claude アプリ)で新しく開けるのは**クラウドセッション**で、これは **GitHub のリポジトリを clone してクラウド上の VM で動く**仕組み。ローカルフォルダは clone できない。

| サイドバーの項目 | 実体 | iPhone から新規に開けるか |
|---|---|---|
| `SHIBATAATSUSHI` | GitHub リポジトリ (`SHIBATAATSUSHI/SHIBATAATSUSHI`) | ○ |
| `claudecode` | `/Users/atsushi/claude/claudecode`(git 管理外のローカルフォルダ) | ×(GitHub に無いため) |
| `docs` | `/Users/atsushi/Documents/shibata-os/docs` | ×(同上) |

> サイドバーの表示名はフォルダ名の末尾だけ。`docs` のように一般的な名前が並ぶのはそのため。

つまり「`claudecode` でしかやれないことがある」のは事実(ローカルのファイル・MCP サーバー・アプリを触れる)だが、**iPhone に出てこない理由は能力差ではなく置き場所の問題**。

## 3つの連携手段と使い分け

| 手段 | Claude が動く場所 | ケータイから触れる範囲 | Mac の状態 |
|---|---|---|---|
| **Remote Control** | Mac | ローカル全部(`claudecode` も含む) | 起動+ネット接続が必要 |
| **クラウドセッション** | クラウド VM | GitHub にあるリポジトリのみ | 不要(寝てても動く) |
| **Dispatch** | Mac(デスクトップアプリ) | ローカル全部 | 起動が必要 |

**基本方針:**

- **Mac が起きているとき** → Remote Control。`claudecode` を含めローカル環境をそのままケータイから操作できる。
- **Mac が寝ているとき** → クラウドセッション(`SHIBATAATSUSHI`)。ここに考えたことを push しておき、Mac 側で拾う。
- **その橋渡し** → `/handoff`(下記)。

## セットアップ

### 1. Remote Control を常時 on にする(Mac 側・1回だけ)

`~/.claude/settings.json` に追記:

```json
{
  "remoteControlAtStartup": true
}
```

これで Mac でどのフォルダから `claude` を起動しても、そのセッションが自動的にケータイの Claude アプリ「Code」タブに出てくる。`claudecode` フォルダで起動したセッションも出る。

単発でやるなら、セッション中に `/remote-control`(`/rc`)。QR コードが出るのでケータイで読み取ればそのまま繋がる。

**制約**: ローカルプロセスが生きている間だけ。ターミナルを閉じる/Mac をスリープさせると切れる。長時間走らせたいなら `tmux` 内で起動する。

### 2. プッシュ通知を on にする(Mac 側)

Claude Code 内で `/config` →

- **Push when Claude decides** — 長い作業が終わったら通知
- **Push when actions required** — 許可を求めるときに通知

これで「Mac で走らせて、終わったらケータイに通知が来て、そのまま続きをケータイで見る」が成立する。

### 3. Mac がオフでも触りたいものは GitHub に置く

`claudecode` は git 管理外のローカルフォルダ(2026-08 時点で確認済み)。ケータイ単独で触りたいなら、private リポジトリとして GitHub に上げる:

```bash
cd ~/claude/claudecode
git init && git add -A && git commit -m "初期コミット"
gh repo create claudecode --private --source=. --push
```

上げた時点で iPhone のリポジトリ選択に出てくる。

> 上げる前に、鍵・トークン・個人情報が混ざっていないか確認し、必要なら `.gitignore` を先に書くこと。

### 4. 引き継ぎメモ(`/handoff`)

Remote Control が切れている状態でも文脈を渡すための仕組み。作業の区切りで `/handoff` と打つと `docs/handoff.md` に「今どこまで/次に何を」が書かれて push される。次にどの端末で開いても、最初に `/handoff` と打てば続きから入れる。詳細は `.claude/skills/handoff/SKILL.md`。

## よく使う導線

```bash
# Mac のローカル作業をケータイに繋ぐ
claude --remote-control

# Mac から「クラウドで走らせておく」(ケータイで結果を見る)
claude --cloud "docs/impulse-definitions.md に神経科学の章を追加して"

# ケータイで進めたクラウドセッションを Mac のターミナルに引き取る
claude --teleport
```
