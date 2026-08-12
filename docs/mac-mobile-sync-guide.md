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

## 資産はフォルダではなくホーム側にある

`claudecode` フォルダの中身は `.claude/` と空の `code test.md` だけ(2026-08 確認)。ローカル資産の実体は **`~/.claude/` 側**にある:

- **claude-mem** — `localhost:37701` で動くローカルの記憶システム。過去の研究・判断の蓄積(100k tokens 規模)。クラウド VM から localhost には届かない。
- **`~/.claude/skills/` の自作スキル** — `stop-slop` / `disk-audit` / `obsidian-save`。
- **Obsidian 連携** — iCloud 上の vault を直接読み書きする。
- **ディスク診断・VM 調査** — Mac の実ファイルシステムが対象。

**これらはフォルダに紐づいていないので、Mac のどのフォルダから `claude` を起動しても使える。** つまり本質は「`claudecode` でしかやれないこと」ではなく「**Mac でしかやれないこと**」。

帰結:

- `claudecode` を GitHub 化しても意味がない(中身が無く、資産はホーム側にあるため)
- 連携手段は Remote Control 一択
- どのフォルダで起動しても全機能が使えるので、`remoteControlAtStartup: true` の常時 on が効く

## 3つの連携手段と使い分け

| 手段 | Claude が動く場所 | ケータイから触れる範囲 | Mac の状態 |
|---|---|---|---|
| **Remote Control** | Mac | ローカル全部(claude-mem・自作スキル・Obsidian も) | 起動+ネット接続が必要 |
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

**制約**: ローカルプロセスが生きている間だけ。ターミナルを閉じる/Mac をスリープさせると切れる。外出中も維持したいなら `tmux` 内で起動する:

```bash
tmux new -s cc
cd ~/claude/claudecode && claude --remote-control
# Ctrl+B → D で切り離す(プロセスは生き続ける)
```

**過去のローカルセッションはケータイに出ない**(重要):

サイドバーの `claudecode` グループに並ぶ過去セッションは Mac の `~/.claude/projects/` にあるローカル履歴で、サーバーには上がっていない。ケータイの Code タブに出るのは次の2つだけ:

- クラウドセッション(GitHub リポジトリのもの)
- **今この瞬間** Remote Control で繋がっている生きたセッション

繋がっているセッションはノート PC のアイコンと緑の「接続済み」が付く。クラウドセッションのほうは `☁ SHIBATAATSUSHI/SHIBATAATSUSHI` と表示されるので、そこで見分ける。

自動生成名(`macbook-pro-sa-local-imperative-…`)は一覧で切れて読めないので、名前を付けておく:

```bash
claude --remote-control "claudecode"   # 起動時に付ける
```
```
/rename claudecode                     # 起動後に付け直す
```

**セッションを起動するたびケータイの一覧に新しいエントリが増える**(1プロセス = 1リモートセッション)。作業を始めるときに `/rename` で名前を付ける癖をつけると見分けがつく。使い終わったものは Claude Code が自動でアーカイブするので、一覧が無限に伸びることはない。

過去の会話をケータイで続けたいときは、**Mac 側で開き直してから送り出す**:

```bash
cd ~/claude/claudecode
claude --resume        # 一覧から続きをやりたい会話を選ぶ
# 開いたら中で /rc
```

つまり「履歴が自動同期される」のではなく「ケータイに出したい会話を Mac 側で1つ選んで送り出す」という操作モデル。ケータイ側から新規セッションを何個も起こしたいなら、サーバーモード `claude remote-control` を使う。

### 2. プッシュ通知を on にする(Mac 側)

Claude Code 内で `/config` →

- **Push when Claude decides** — 長い作業が終わったら通知
- **Push when actions required** — 許可を求めるときに通知

これで「Mac で走らせて、終わったらケータイに通知が来て、そのまま続きをケータイで見る」が成立する。

### 3. Mac がオフでも触りたいものは GitHub に置く

Mac を閉じた状態でも触りたい**普通のコード・文章**は、private リポジトリとして GitHub に上げればケータイ単独で開ける:

```bash
cd <フォルダ>
git init && git add -A && git commit -m "初期コミット"
gh repo create <名前> --private --source=. --push
```

上げた時点で iPhone のリポジトリ選択に出てくる。

> 上げる前に、鍵・トークン・個人情報が混ざっていないか確認し、必要なら `.gitignore` を先に書くこと。

ただし `claudecode` は前述のとおりこれでは解決しない。ローカル資産に依存するものと、単なるコード・文章は分けて考える。

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
