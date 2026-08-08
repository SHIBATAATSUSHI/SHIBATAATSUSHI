# iPhone / iPad から使う

エージェント本体は**自宅(または会社)のPC**で動かし、iPhone からは
SSH ターミナルで繋ぐ構成。追加費用ゼロで、SSH をインターネットに
晒さずに済む。所要はだいたい30分。

```
iPhone (SSHアプリ) ──Tailscale──▶ 自宅PC (tmux + Shibata Code)
```

なぜこの形か。iOS では Python を実用的な形で常駐させられないので、
携帯側は表示と入力に徹する。そして**携帯回線は必ず切れる**ので、
切れても作業が死なないように tmux を挟む。

---

## 1. PC側:Shibata Code を動くようにする

```bash
git clone <このリポジトリ>
cd <リポジトリ>/experiments/shibata-code
pip install -r requirements.txt
python -m shibata_code --models   # 動作確認
```

APIキーはシェルの起動ファイルに書いておく(SSH で入ったときにも
読まれるよう、`.bashrc` や `.zshrc` に置く)。

```bash
# ~/.zshrc など
export ANTHROPIC_API_KEY=...
export DEEPSEEK_API_KEY=...
# 使うものだけでよい
```

## 2. PC側:SSH を有効にする

- **macOS** — システム設定 → 一般 → 共有 → リモートログイン をオン
- **Linux** — `sudo apt install openssh-server && sudo systemctl enable --now ssh`
- **Windows** — 設定 → アプリ → オプション機能 → OpenSSH サーバー

あわせて**スリープしない設定**にする。スリープすると携帯から繋がらない。
macOS ならシステム設定 → ロック画面 → ディスプレイオフ後に自動でスリープ:しない。
一時的でよければ `caffeinate -s` を走らせておく手もある。

## 3. Tailscale を入れる(重要)

PC と iPhone の両方に Tailscale を入れ、同じアカウントでログインするだけ。
これで**ポート開放も固定IPも不要**で、暗号化された経路でPCの名前を指定して
繋がる。

SSH をインターネットに直接開放するのは避けること。総当たり攻撃の的になる。
Tailscale なら公開ポートを1つも増やさずに済む。

入れたら、iPhone の Tailscale アプリでPCの名前(例 `my-mac`)を確認しておく。

## 4. 鍵認証にする

パスワード認証は切っておく。iPhone のSSHアプリで鍵を作り、公開鍵をPCに登録する。

```bash
# PC側:iPhoneアプリからコピーした公開鍵を貼る
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "ssh-ed25519 AAAA... iphone" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

## 5. iPhone側:SSHアプリを入れる

| アプリ | 備考 |
| --- | --- |
| **Blink Shell** | 有料。動作と再接続の扱いが一番よい。物理キーボードにも強い |
| **Termius** | 無料枠あり。設定画面が分かりやすい |
| **Prompt 3** | 有料。素直な作りで軽い |

接続先は Tailscale で見えるPC名、ユーザー名はPCのログイン名、認証は鍵。

## 6. tmux を入れて設定する

```bash
# PC側
brew install tmux              # macOS
sudo apt install tmux          # Debian系

cat experiments/shibata-code/scripts/tmux-mobile.conf >> ~/.tmux.conf
```

`tmux-mobile.conf` は携帯向けに、マウス操作(タップとスワイプでのスクロール)、
ステータス行の簡素化、再接続時の画面幅の追従などを設定している。

## 7. 起動する

PC側に用意した起動スクリプトを、iPhone から叩くだけ。

```bash
# PC側で1回だけ
chmod +x experiments/shibata-code/scripts/sc-mobile.sh

# 使うときはこれだけ(エイリアスにしておくと楽)
echo 'alias sc="~/path/to/experiments/shibata-code/scripts/sc-mobile.sh"' >> ~/.zshrc
```

iPhone のSSHアプリで繋いで `sc` と打つと、tmux セッションに入って
Shibata Code が `--compact` で起動する。**回線が切れても作業は続いており、
繋ぎ直して `sc` と打てば同じ画面に戻る。**

```bash
sc                    # 既定の作業ディレクトリで起動
sc -m deepseek        # 引数はそのまま渡る
SHIBATA_WORKDIR=~/work/myapp sc
```

---

## 携帯で使うときのコツ

**`--compact` を使う。** 起動スクリプトは自動で付けている。思考の表示を
省き、ツール結果のプレビューを3行に絞り、端末の実際の幅に合わせて
行を切る。細い画面で折り返しに埋もれるのを防ぐ。思考を取り寄せない分、
トークンも少し安くなる。

**権限モードを考える。** 既定の `ask` は毎回確認するので携帯では面倒だが、
安全側ではある。ファイル編集だけ自動にする `--mode auto` が現実的な妥協点。
`yolo` は携帯からの誤操作が怖いので勧めない。

**長い依頼を投げて放置する。** 携帯で細かく対話するのは向いていない。
「テストを直して」のような依頼を投げて、後で結果を見に行く使い方が合う。
tmux があるので、アプリを閉じても走り続ける。

**`/model` で切り替える。** 移動中の軽い調べ物は `deepseek` や `haiku`、
本気の作業は `opus` や `fable`、と使い分けると費用が抑えられる。

**セッションの再開。** `--resume` で前回の続きから始められる。tmux が
生きていればそもそも不要だが、PCを再起動したあとなどに効く。

---

## うまくいかないとき

| 症状 | 見るところ |
| --- | --- |
| 繋がらない | PCがスリープしていないか。Tailscale が両方でオンラインか |
| `sc` が見つからない | エイリアスを書いた rc ファイルが SSH ログイン時に読まれているか |
| APIキーが無いと言われる | キーを書いたのが `.zshrc` か `.zprofile` か。SSH の非対話シェルでは読まれないことがある |
| 文字化けする | SSHアプリ側のエンコーディングが UTF-8 か。`export LANG=ja_JP.UTF-8` |
| 画面幅が合わない | tmux の `aggressive-resize` が効いているか。`--width 50` で明示指定もできる |
| 切断で作業が消える | tmux を経由しているか(`sc` を使わず直接起動していないか) |

---

## 別案:クラウドの小さなサーバに置く

常時起動のPCが用意できない場合は、月数百円のVPS(さくらのVPS、
Lightsail、Hetzner など)に同じものを置く。手順は上とほぼ同じで、
Tailscale も同様に使える。PCの電源を気にしなくてよくなる代わりに、
手元のファイルはそこに置くことになる。

作業対象が GitHub のリポジトリなら、こちらのほうが素直かもしれない。
