# iPhone だけで使う

PC を一切用意せず、iPhone の中だけで動かす構成。追加パッケージは
**1つも要らない**(`anthropic` / `openai` SDK 無しで動くようにしてある)。

ただし**できることに制限がある**。まずそこを確認してから始めること。

## できること / できないこと

| | iPhone だけ | PC + SSH([MOBILE.md](MOBILE.md)) |
| --- | --- | --- |
| ファイルを読む・書く・編集する | ○ | ○ |
| ファイル名・中身を検索する | ○ | ○ |
| **シェルコマンドの実行** | **×**(a-Shell の場合) | ○ |
| **テストの実行・git 操作** | **×**(同上) | ○ |
| 各社のモデル切り替え | ○ | ○ |
| 対象にできるファイル | アプリのサンドボックス内 | PC 全体 |
| セットアップ | 10分 | 30分 |

`bash` が使えないと、**テストを走らせて直す、といった一番おいしい使い方が
できません。** 文章を書く・設定ファイルを直す・コードを読んで説明させる、
あたりが現実的な用途になります。

本格的に使うなら PC か VPS を挟む [MOBILE.md](MOBILE.md) の構成を勧めます。
まず触ってみたい、という段階ならこちらで十分です。

---

## 手順(a-Shell)

**a-Shell** は無料でApp Storeにあり、Python 3 が同梱されたターミナルです。

### 1. a-Shell を入れる

App Store で「a-Shell」を検索して入れる。`a-Shell mini` ではなく
通常版のほうが Python を含んでいます。

### 2. ファイルを持ってくる

a-Shell を開いて、以下を実行します。

```sh
pip install --no-deps nothing 2>/dev/null   # pip の初期化(失敗してよい)
mkdir -p ~/Documents/sc
cd ~/Documents/sc
```

リポジトリを丸ごと持ってくる方法は2つあります。

**A. `curl` でZIPを取る**(GitHub が公開リポジトリの場合)

```sh
curl -L -o sc.zip https://github.com/SHIBATAATSUSHI/SHIBATAATSUSHI/archive/refs/heads/main.zip
unzip sc.zip
mv SHIBATAATSUSHI-main/experiments/shibata-code/* .
```

**B. Files アプリ経由**

iPhone の Files アプリで a-Shell のフォルダにコピーする。
a-Shell 側から `pickFolder` で Files のフォルダを開くこともできます。

### 3. APIキーを設定する

```sh
echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/Documents/.profile
source ~/Documents/.profile
```

安いモデルで試すなら DeepSeek のキーでも構いません。

```sh
export DEEPSEEK_API_KEY=sk-...
```

### 4. 動かす

```sh
cd ~/Documents/sc
python3 -m shibata_code --compact --transport http -m deepseek
```

- `--transport http` … SDK を使わず標準ライブラリだけで通信する
- `--compact` … 思考表示を省き、iPhone の画面幅に合わせて出力を切る

`--transport` は省略しても構いません。SDK が入っていなければ自動で
標準ライブラリ経路になります(`auto` が既定)。

シェルが見つからない環境では `bash` ツールは**自動的に外れます**。
`/tools` で今使えるツールを確認できます。

---

## 手順(iSH — シェルも使いたい場合)

**iSH** は iPhone 上で Alpine Linux を動かすアプリです(無料)。
x86 のエミュレーションなので**かなり遅い**ですが、本物の Linux なので
`bash`・`git`・テスト実行まで一通り動きます。

```sh
apk add python3 git
git clone https://github.com/SHIBATAATSUSHI/SHIBATAATSUSHI.git
cd SHIBATAATSUSHI/experiments/shibata-code
export ANTHROPIC_API_KEY=sk-ant-...
python3 -m shibata_code --compact --transport http
```

`pip install` は不要です(標準ライブラリだけで動くため)。むしろ
iSH で `anthropic` を入れようとすると、依存の `pydantic` のビルドで
延々待たされるので、`--transport http` のまま使うのが正解です。

体感速度は期待しないこと。起動に数十秒かかることがあります。

---

## iPhone だけで使うときのコツ

**モデルは安いものを選ぶ。** 移動中に少し試す用途なら `deepseek` や
`haiku` で十分です。`/model` でいつでも変えられます。

**画面を縦に使わない。** 横向きにすると1行が倍近く入って、格段に読みやすく
なります。外付けキーボードがあるとさらに快適です。

**長い出力は `--compact` に任せる。** ツール結果は3行に切り詰められます。
全部見たいときは `--compact` を外すか、`--width` で幅を指定します。

**セッションは保存される。** アプリを閉じても `.shibata-code/sessions/` に
残るので、`--resume` で続きから始められます。

**キーの扱いに注意。** `.profile` に平文で置くことになります。iPhone を
他人に渡す可能性があるなら、使い捨てにできるキーを発行しておくこと。

---

## うまくいかないとき

| 症状 | 見るところ |
| --- | --- |
| `ModuleNotFoundError: anthropic` | `--transport http` を付ける(または `auto` のまま使う) |
| `bash` が使えない | 仕様。`/tools` で確認。シェルが要るなら iSH か PC 構成へ |
| 通信できない | `--transport http` になっているか。キーが `export` されているか |
| 文字が潰れる | 横向きにする。`--width 60` などで明示指定する |
| 遅い | iSH は元々遅い。a-Shell のほうが速いがシェルが無い |
| ファイルが見えない | アプリのサンドボックス外は触れない。作業対象をサンドボックス内に置く |

---

## 補足:なぜ SDK 無しで動くのか

`anthropic` / `openai` SDK は `pydantic` のコンパイル済み拡張に依存していて、
iOS 向けのホイールが存在しません。そのため iPhone 上では入りません。

そこで、標準ライブラリの `urllib` だけで HTTP と Server-Sent Events を
扱う経路を用意しました(`backends/http_transport.py`)。リクエストの
組み立てと応答の解釈は SDK 版と共通で、通信部分だけ差し替えています。

この経路は、SDK の import を遮断した状態で1往復させるテスト
(`tests/test_no_sdk.py`)で検証してあります。
