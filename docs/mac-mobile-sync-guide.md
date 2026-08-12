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

## 連携手段と使い分け

| 手段 | Claude が動く場所 | ケータイから触れる範囲 | Mac の状態 |
|---|---|---|---|
| **Remote Control(常駐)** | Mac | ローカル全部。ケータイから新規セッションも起こせる | 起動+ネット接続が必要 |
| **Remote Control(都度)** | Mac | ローカル全部。Mac 側で起こしたセッションに入るだけ | 同上 |
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

**制約**: ローカルプロセスが生きている間だけ。ターミナルを閉じると切れる。打たなくても常に繋がっている状態にしたいなら、次章の常駐化を行う。

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
claude --resume
```

一覧から続きをやりたい会話を選び、開いたら中で `/rc` と打つ。

つまり「履歴が自動同期される」のではなく「ケータイに出したい会話を Mac 側で1つ選んで送り出す」という操作モデル。ケータイ側から新規セッションを何個も起こしたいなら、サーバーモード `claude remote-control` を使う。

### 2. 常駐させて自動で繋がるようにする(Mac 側・1回だけ)

`remoteControlAtStartup` は「`claude` と打ったとき」に繋がる設定であって、打たなければ何も起きない。**何もしなくても常にケータイから見える**状態にするには、macOS の常駐機構 launchd に **サーバーモード**を登録する。

`claude --remote-control`(通常)と `claude remote-control`(サーバーモード)は別物:

| | `claude --remote-control` | `claude remote-control`(サーバー) |
|---|---|---|
| 起動 | 打つたび | ログイン時に自動、常駐 |
| ケータイから新規セッション | 作れない(Mac 側で起こしたものに入るだけ) | **作れる** |
| 同時セッション数 | 1 | 最大32 |
| 落ちたとき | 手で再起動 | 自動で復活 |

サーバーモードにすると、**iPhone の「新規セッション」から Mac 上の作業を起こせる**ようになる。これがローカル連携の完成形。

#### 手順

> **貼り付けの注意**: 対話シェルの zsh は `#` をコメントとして扱わない(既定で `interactive_comments` が off)。以下のコード欄に説明用のコメントを書いていないのはそのため。コメント付きの行をターミナルに貼ると `command not found: #` になる。

まず前面で動くことを確認する。ここで動かなければ常駐化しても動かない。QR コードが出たら `Ctrl+C` で止めてよい。

```bash
cd ~/claude/claudecode
claude remote-control --name "MacBook"
```

確認できたらリポジトリを最新にして、セットアップスクリプトを実行する。LaunchAgent の作成・登録・状態確認まで行う。

```bash
git clone https://github.com/SHIBATAATSUSHI/SHIBATAATSUSHI.git ~/SHIBATAATSUSHI
cd ~/SHIBATAATSUSHI
./scripts/setup-remote-control.sh
```

既に clone 済みなら `git clone` は失敗するので、代わりに `cd ~/SHIBATAATSUSHI && git pull` で更新する。

#### プロジェクトごとに登録する

引数で作業ディレクトリと表示名を渡すと、**プロジェクトごとに別々の常駐を登録できる**。ラベルは作業ディレクトリ名から自動で導出されるので衝突しない。触るプロジェクトを全部登録しておくと、ケータイのディレクトリ選択にまとめて並ぶ。

対象の洗い出しは、Claude Code で開いたことのあるフォルダの一覧から:

```bash
ls ~/.claude/projects/
```

**先にワークスペース信頼を通しておくこと。** 未承認のディレクトリは launchd 下で信頼ダイアログを出せずに即死し、`KeepAlive` による再起動を延々と繰り返す。各ディレクトリで一度 `claude` を起動して承認する:

```bash
cd ~/SHIBATAATSUSHI && claude
```

承認したら `Ctrl+C` で抜ける。スクリプトは `~/.claude/projects/` を見て未承認の疑いがあれば警告する。

登録する:

```bash
./scripts/setup-remote-control.sh ~/SHIBATAATSUSHI "SHIBATAATSUSHI"
./scripts/setup-remote-control.sh ~/Documents/investment-dashboard "投資"
./scripts/setup-remote-control.sh ~/Documents/shibata-os "shibata-os"
```

> `--list` の「稼働中」は起動に失敗して再起動を繰り返している状態でも出ることがある。登録時に `✗` が出たらエラーログを信じること。

一覧と解除:

```bash
./scripts/setup-remote-control.sh --list
./scripts/setup-remote-control.sh --remove investment-dashboard
```

> 常駐が増えるとメモリと接続数を食う。実際に触るものだけに絞り、使わなくなったら `--remove` で外す。

#### 旧ラベルの後始末

2026-08 の初版スクリプトはラベルを `com.shibata.claude-rc` に固定していた。当時登録したものが残っていると、同じディレクトリの常駐が二重に立つ。`--list` に出てくるので、一度だけ外しておく:

```bash
launchctl bootout gui/$(id -u)/com.shibata.claude-rc
rm ~/Library/LaunchAgents/com.shibata.claude-rc.plist
```

#### 成功したときの見え方と、ケータイからの入口

ケータイの Code タブの **「デバイス」欄に Mac が緑の点付きで出る**(常駐前は「最近接続したデバイスはありません」と表示される場所)。

**入口を間違えないこと**:

- ✗ 右下の **「+ 新規セッション」** → これは**クラウドセッション**を作る。`SHIBATAATSUSHI · Default` という見出しになり、Mac のファイルは見えない
- ○ 上部の **デバイスカード(`MacBook-Pro-SA`)をタップ** → 「ディレクトリを選択」が開き、常駐時に指定した作業ディレクトリ(`/Users/atsushi/claude/claudecode`)が `1/32` のように空き枠付きで出る。これを選ぶと Mac 上でセッションが立つ

見分け方は `pwd` を実行させるのが速い。`/Users/atsushi/...` なら Mac、`/home/user/...` ならクラウド。

なお立ったセッションは Mac のローカルセッションなので、指定した作業ディレクトリの中だけに縛られない。`~/Documents` でも `~/Library` でも読める。あくまで初期の作業ディレクトリという意味。

#### 動かないとき

ログを見る:

```bash
tail -20 ~/Library/Logs/claude-rc.err.log
```

> 2026-08 に MacBook Pro / Claude Code v2.1.228 で検証したときは、launchd 直下でそのまま起動した。以下は転けた場合の保険。

launchd の下には端末(TTY)が無いため、サーバーモードが端末を要求して起動しない可能性がある。その場合だけ `tmux` を噛ませて疑似端末を与える:

```bash
brew install tmux
```

該当する plist(`--list` で出るラベルに対応する `~/Library/LaunchAgents/com.shibata.claude-rc-<スラッグ>.plist`)の `exec claude remote-control ...` の行を次に差し替えて再登録する:

```
exec tmux new -s cc "claude remote-control --name MacBook"
```

> tmux は「ターミナルを閉じてもプロセスが死なない仮想画面」を作るツール。本来は手で起動するものなので自動化には向かないが、ここでは疑似端末を用意する目的だけに使う。

#### 常駐でも解決しないこと

- **スリープ中は使えない**。復帰時に自動再接続はするが、蓋を閉じている間はケータイから触れない。外出中も使うなら電源接続＋蓋を開けたまま、または `caffeinate -s` で包む。
- **起きているのにネット断が約10分続くとプロセスが落ちる**。ただし `KeepAlive` により自動で復活するので、常駐させておけば実害は小さい。
- 蓋を閉じて持ち歩く運用なら、そもそも Remote Control ではなくクラウドセッション側に寄せる。

### 3. プッシュ通知を on にする(Mac + iPhone)

通知は4段構えで、どれか1つ欠けても届かない。

1. **iPhone に Claude アプリが入っている**(導入済み)
2. **Mac と同じアカウントでサインインしている**(済み)
3. **iOS 側で通知を許可している** — 設定 → 通知 → Claude →「通知を許可」on。「ロック画面」「通知センター」「バナー」も on にし、バナースタイルは「持続的」が見逃しにくい。さらに通知を握りつぶす2つの機能から外す:
   - 設定 → 通知 → **時刻指定要約** → Claude を off(要約に回されると即時に届かない)
   - 設定 → **集中モード** → 使っているモード →「通知を許可」に Claude を追加
4. **Mac の Claude Code で `/config`**:
   - `Push when Claude decides` — 長い作業が終わったら通知
   - `Push when actions required` — 許可待ち・質問のときに通知

   ここで `No mobile registered` と出ていたら、iPhone で Claude アプリを一度開いてから繋ぎ直す。

#### 検証するときの注意

**ターミナルを触っている間、通知は仕様上抑制される**(「端末の前にいるなら通知は不要」という設計)。机で試すと届かないので「壊れている」と誤認しやすい。

机に座ったまま試すと必ず失敗する。打った直後は必ず「アクティブ」判定になるため。**遅延を挟んで、その隙に離席する**のが唯一確実なテスト方法(2026-08 に実機で確認):

```
90秒待ってから、PushNotification ツールで「テスト送信」という通知を iPhone に送って。
```

送信したら**すぐに `Control + Command + Q` で画面をロック**する。90秒後にはターミナルが非アクティブなので抑制されずに飛ぶ。2分待ってロックを解除し、iPhone のロック画面か通知センターを見る。

集中モード(ステータスバーに三日月やベッドのアイコン)が有効だと、届いても音も表示もなく通知センターに静かに積まれる。バナーが出なくても通知センターは必ず確認すること。

> この抑制は不具合ではなく「Mac の前にいるなら通知は要らない」という設計。実際の使い方(長い作業を投げて席を立つ)では自然に条件を満たすので、普段は意識しなくてよい。

長めのタスクを投げてから、**別アプリにフォーカスを移して**待つこと。

#### 届かないとき — ツールの戻り値を読むのが最短

`PushNotification` ツールの戻り値に理由がそのまま出る。当てずっぽうで設定をいじる前にこれを見る。

Mac のセッションで:

```
PushNotification ツールを使って、iPhone にテスト通知を送って。送信結果も教えて。
```

| 戻り値 | 意味 | 対処 |
|---|---|---|
| `Not sent because you're active in this terminal.` | フォーカス抑制。端末の前にいると送られない | 送信直後に `Control + Command + Q` で画面ロックしてから待つ |
| `Remote Control inactive` | **そのセッションが Remote Control に繋がっていない**。通知の経路が無い | そのセッションで `/rc` を打つ。footer に `/rc active` が出れば OK |
| `Terminal notification sent.` のみ | Mac のローカル通知は出たがモバイルには飛んでいない | 上の2つのどちらか |

**間違えやすい点**:

- `pgrep -fl "claude remote-control"` で常駐サーバーが見えても、**キーボードで打っているセッションとは別プロセス**。常駐が生きていても、そのセッションが繋がっていなければ通知は飛ばない
- `/config` の「Enable Remote Control for all sessions」は**次に起動するセッションから**効く。今動いているセッションには遡って適用されない。その場で繋ぐには `/rc`

その他:

- `/config` に `No mobile registered` と出る → iPhone で Claude アプリを一度開く(プッシュトークンが更新される)。次に Remote Control が繋がったときに解消する
- iOS の集中モード(ステータスバーに三日月やベッドのアイコンが出ている状態)・通知要約に飲まれていないか確認する

### 4. Mac がオフでも触りたいものは GitHub に置く

Mac を閉じた状態でも触りたい**普通のコード・文章**は、private リポジトリとして GitHub に上げればケータイ単独で開ける:

```bash
cd <フォルダ>
git init && git add -A && git commit -m "初期コミット"
gh repo create <名前> --private --source=. --push
```

上げた時点で iPhone のリポジトリ選択に出てくる。

> 上げる前に、鍵・トークン・個人情報が混ざっていないか確認し、必要なら `.gitignore` を先に書くこと。

ただし `claudecode` は前述のとおりこれでは解決しない。ローカル資産に依存するものと、単なるコード・文章は分けて考える。

### 5. 引き継ぎメモ(`/handoff`)

Remote Control が切れている状態でも文脈を渡すための仕組み。作業の区切りで `/handoff` と打つと `docs/handoff.md` に「今どこまで/次に何を」が書かれて push される。次にどの端末で開いても、最初に `/handoff` と打てば続きから入れる。詳細は `.claude/skills/handoff/SKILL.md`。

## よく使う導線

常駐の一覧を見る / 個別に解除する:

```bash
./scripts/setup-remote-control.sh --list
./scripts/setup-remote-control.sh --remove <スラッグ>
```

Mac のローカル作業をケータイに繋ぐ(都度):

```bash
claude --remote-control
```

Mac から「クラウドで走らせておく」(ケータイで結果を見る):

```bash
claude --cloud "docs/impulse-definitions.md に神経科学の章を追加して"
```

ケータイで進めたクラウドセッションを Mac のターミナルに引き取る:

```bash
claude --teleport
```
