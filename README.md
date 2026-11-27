# AtCoder Environment

[![CI](https://github.com/kitamura-tetsuo/atcoder-environment/actions/workflows/ci.yml/badge.svg)](https://github.com/kitamura-tetsuo/atcoder-environment/actions/workflows/ci.yml)

PythonでAtCoderに取り組むための、問題準備・サンプル実行・差分テスト・反例縮小・計測をまとめた環境です。Docker Compose、VS Code Dev Containers、ローカルのuvで同じ `ac` コマンドを使えます。

**「WAになったが理由が分からない」を、自分の手元で再現できる小さな入力に変える**のが目的です。公式の隠しテストや提出時の実行ログを取得する機能ではありません。

## できること

| 機能 | コマンド / 実装 |
| --- | --- |
| 配列・木の問題用フォルダー作成 | `ac new` |
| 問題URLから作業フォルダーとサンプルを準備 | `ac prepare` / online-judge-tools |
| サンプル・保存した回帰テストの実行 | `ac test` |
| 境界ケース・ランダム入力を愚直解と比較 | `ac stress` / Hypothesis |
| 入力の制約を保ちながら反例を縮小 | `ac stress` の標準動作 |
| 入力・期待値・標準出力・stderr・判定・時間を保存 | `.ac/runs/` |
| seed・主要ソースのスナップショット・バージョンを保存 | `environment.json` / `sources/` |
| 保存した反例を修正後の解答で再実行 | `ac replay` |
| 反例を継続的に実行する回帰テストに追加 | `ac promote` |
| 入力サイズごとの実行時間をCSVに保存 | `ac bench` |
| 問題文から入出力コード・生成器の下書きを作成 | `ac template` / online-judge-template-generator |
| 木・乱数用の追加ヘルパー | CYaRon / onlinejudge_random |
| 提出 | `ac submit` / ojの対話的な確認付き |

実行時に生成AIを呼ぶ機能はありません。問題固有の解答、正しい愚直解、入力制約は自分で記述します。

## まず動かす：Docker Compose

Docker EngineとCompose v2が使えるLinuxを想定しています。

```bash
 git clone https://github.com/kitamura-tetsuo/atcoder-environment.git
 cd atcoder-environment

 # Match ownership of files created through the workspace bind mount.
 export LOCAL_UID="$(id -u)"
 export LOCAL_GID="$(id -g)"
 docker compose build

 docker compose run --rm dev ac doctor
 docker compose run --rm dev ac test examples/pair-sum
 docker compose run --rm dev ac stress examples/pair-sum --seed 42
```

続けて、意図的に間違えた解答を試します。

```bash
 docker compose run --rm dev ac stress examples/pair-sum \
   --solution 'python buggy.py' --seed 42
```

最後のコマンドは **WAを発見して終了コード1を返すのが正常**です。例えば次の入力まで縮小されます。

```text
3 0
0 0 0
```

この例題は「和が0になる添字の組の個数」を数えます。期待値は `3`、重複を数え落とす `buggy.py` の出力は `2` です。実際に選ばれる反例は探索条件に依存します。

コマンドを何度も実行する場合は、コンテナーのシェルを開くと簡単です。

```bash
 docker compose run --rm dev bash
 ac test examples/pair-sum
 ac stress examples/pair-sum --seed 42
```

依存関係は `/opt/venv` にあり、作業フォルダーのマウントで隠れません。コンテナーは非rootユーザーで実行し、ホームディレクトリーは名前付きボリュームに保持します。

## VS Codeで使う

1. リポジトリをVS Codeで開く。
2. **Dev Containers: Reopen in Container** を実行する。
3. **Tasks: Run Task** から `AtCoder: Run sample tests`、`AtCoder: Stress test and shrink` などを選ぶ。

初回はDockerイメージをビルドします。LinuxでUID/GIDをVS Codeの起動環境に引き継げない場合は、次のようにローカルの `.env` に保存できます。このファイルはGit管理対象外です。

```bash
 tee .env > /dev/null <<EOF
LOCAL_UID=$(id -u)
LOCAL_GID=$(id -g)
EOF
```

タスクは `uv run --frozen ac ...` を呼ぶため、ホスト側でuvをセットアップした場合にも使えます。問題ディレクトリーを尋ねられたら、まず `examples/pair-sum` を指定してください。

## Dockerを使わない場合

Linux/macOS、Python 3.11〜3.13、[uv](https://docs.astral.sh/uv/getting-started/installation/) が必要です。標準はPython 3.12です。

```bash
 uv sync --frozen
 uv run --frozen ac doctor
 uv run --frozen ac test examples/pair-sum
 uv run --frozen ac stress examples/pair-sum --seed 42
```

以下の説明では `ac` と略記します。ホストからは `uv run --frozen ac`、コンテナーを起動せず直接呼ぶなら `docker compose run --rm dev ac` に置き換えてください。ネイティブWindowsではDockerまたはWSLを使ってください。

## 問題を解く流れ

### 1. 作業フォルダーを作る

オフラインで準備する場合：

```bash
 ac new problems/practice
 ac new problems/tree-practice --template tree
```

AtCoderのサンプルも取得する場合：

```bash
 ac prepare https://atcoder.jp/contests/abs/tasks/abc086_a
```

既存のファイルは上書きしません。取得に失敗しても作業フォルダーは残り、後で `ac download <問題フォルダー>` で再試行できます。自分で `test/sample-1.in` と `test/sample-1.out` を置くこともできます。

| ファイル | 自分で編集する内容 |
| --- | --- |
| `main.py` | 提出する高速な解答 |
| `brute.py` | 小さい入力に対する、単純で正しい参照解 |
| `cases.py` | 問題の入力形式・制約・境界ケース |
| `atcoder.toml` | 実行コマンド、時間制限、比較方法など |
| `test/*.in`, `test/*.out` | サンプルと回帰テスト |

**`ac prepare` は任意の問題の制約を推論しません。** 初期の `cases.py` は配列または木の例なので、対象問題に合わせて必ず修正してください。未実装の `main.py` / `brute.py` は `NotImplementedError` を出します。

### 2. サンプルを確認する

```bash
 ac test problems/practice
```

全入力に対して別々のプロセスを起動し、AC / WA / RE / TLE / OLEを表示します。テストが0件だった場合や `.out` が欠けている場合はエラーになります。

### 3. 愚直解と比較して反例を探す

```bash
 ac stress problems/practice --max-examples 1000 --seed 42 --budget 120
```

`edge_cases()` を確認した後、Hypothesisの生成と縮小を実行します。境界ケースで失敗した場合も、同じ種類の失敗を起こす小さい入力を探します。

- `--max-examples` はHypothesisの探索設定です。境界ケース、縮小、再現確認を含めた総実行回数とは異なります。
- `--budget` は探索全体の時間予算です。進行中の1ケースや最終の再現確認は、設定したプロセス単位の制限まで動く場合があります。
- 時間予算を使い切って反例が見つからなければ `INCOMPLETE` / 終了コード2になり、成功とは扱いません。
- WA / REの縮小では最初の判定種別を維持します。TLE / OLEは保存と再現確認を行い、縮小しません。
- 参照解のRE / TLEは、解答のWAとは区別してエラーにします。
- 縮小は大域的に最小の反例を保証しません。seedのほか、ライブラリとソースのバージョンも結果に影響します。

生成器では、単純な `random` 呼び出しを `strategy()` の中に隠すより、Hypothesisの `st.integers` / `st.lists` / `@st.composite` で制約を表すと、縮小時にも構造を維持できます。木テンプレートは接続性・頂点数・辺数を維持しつつ、頂点のラベルも変化させます。

### 4. ログを見る・修正後に再現する

実行時に表示される `.ac/runs/<実行ID>/` を開きます。

| 保存先 | 内容 |
| --- | --- |
| `summary.json` | 結果、実行回数、seed、探索状態 |
| `environment.json` | コマンド、比較条件、バージョン、ソースのSHA-256 |
| `sources/` | 主要なPythonファイルと設定のスナップショット |
| `initial/` | 最初に発見した反例 |
| `failure/` | 縮小または探索後に再現確認した反例 |
| `failure/input.txt` | 入力 |
| `failure/expected.txt` / `actual.txt` | 期待値 / 解答の標準出力 |
| `failure/stderr.txt` | 解答の標準エラー、Pythonの例外 |
| `failure/oracle.stdout.txt` / `oracle.stderr.txt` | 参照解の出力 |
| `failure/result.json` | 判定、終了コード、実行時間、実行コマンド |

`<失敗ディレクトリー>` を実際に表示された `.../failure` のパスに置き換えてください。

```bash
 ac replay problems/practice '<失敗ディレクトリー>'
 ac promote problems/practice '<失敗ディレクトリー>' --name regression-001
 ac test problems/practice
```

`replay` は保存した入力・期待値を**現在の解答**で実行します。保存したソースに自動で戻す処理はありません。`promote` 後は通常の `ac test` に含まれます。

単独の入力は `ac run problems/practice --input input.txt` で実行できます。`--input` / `--expected` と、`replay` / `promote` の保存先パスは、コマンドを呼び出したディレクトリーから解決します。設定した解答・参照解のスクリプトは問題フォルダーを作業ディレクトリーとして実行します。

アプリ自身のログはLoguruで標準出力と `.ac/logs/ac.log` の両方に出し、日単位でローテーションします。問題フォルダーと `.ac/` はGit管理対象外です。

### 5. 入力サイズを増やして時間を測る

```bash
 ac bench examples/pair-sum --sizes 100,1000,10000,100000 --repeat 3 --seed 42
```

`cases.py` の `benchmark_case(size, seed)` が生成した入力で、起動時間を含む実行時間の最小値・中央値・最大値をCSVとJSONに保存します。参照解は実行せず、出力の正しさは検証しません。大きな入力は問題の制約内で指定してください。

AtCoder本番とCPU、インタープリター、起動コストは異なります。数点の計測だけで厳密な計算量を推定したり、本番のAC/TLEを保証したりはしません。プロセスごとの正確なピークメモリは現版では計測せず、`max_rss_kib` は `null` です。

## 入力コードの自動生成と追加ヘルパー

```bash
 ac template problems/abs/abc086_a
```

`oj-template` で生成した `main.py` と `generate.py` は `.ac/templates/<ID>/` に保存されます。既存の解答を置き換えず、自動実行もしません。生成コードの構文、入力形式、`TODO` の範囲を確認してから利用してください。上流の生成器には仮の範囲が入り、解析失敗時に巨大な配列を作るフォールバックもあるためです。

既存の `oj`、`oj-template`、`oj-prepare` も同じ環境内から直接利用できます。`oj-prepare` が作るフォルダーはこの環境の `cases.py` を自動生成しないため、統合ワークフローでは `ac prepare` を使います。

CYaRonとonlinejudge_randomの使用例は [`examples/generators/optional_helpers.py`](examples/generators/optional_helpers.py) にあります。

```bash
 python examples/generators/optional_helpers.py
```

追加ヘルパーを利用した入力は、`edge_cases()` / `benchmark_case()` に組み込めます。Hypothesisで構造を縮小したい入力は、`strategy()` で表現してください。各ライブラリをインストールしただけで、任意の生成器が縮小可能になるわけではありません。

## 実行設定と比較方法

`atcoder.toml` の例：

```toml
[problem]
url = "https://atcoder.jp/contests/abs/tasks/abc086_a"

[run]
solution = ["python", "main.py"]
oracle = ["python", "brute.py"]
timeout = 2.0
oracle_timeout = 5.0
output_limit = 1048576
comparison = "tokens"
atol = 1e-9
rtol = 1e-9

[stress]
examples = 200
seed = 0
```

- `python` / `python3` は、`ac` を実行しているPythonに解決します。PyPyなどを別途導入していれば `solution = ["pypy3", "main.py"]` も指定できます。
- `tokens`：空白で分割したトークンが完全一致するか比較します。既定値です。
- `exact`：読み込んだテキストを改行・空白も含めて比較します。
- `float`：数値を `math.isclose` 相当の相対・絶対誤差で比較し、文字列トークンは完全一致させます。NaN / 無限大は不合格にします。
- `output_limit` はstdoutとstderrを合わせたバイト数です。超過時はOLEとして停止します。
- 時間制限・出力制限・中断時には、通常の子プロセスを同じプロセスグループごと停止します。

複数の異なる正解を許す構築問題やインタラクティブ問題には、専用のチェッカーが別途必要です。ローカルのテキスト比較だけでは公式判定を再現できません。実行器は自分の解答をテストする用途で、敵対的なコードを隔離するサンドボックスではありません。

| 終了コード | 意味 |
| --- | --- |
| `0` | 指定した検証・処理が成功 |
| `1` | 解答のWA / RE / TLE / OLEを検出 |
| `2` | 設定・生成器・参照解・外部ツールのエラー、探索未完了 |
| `130` | Ctrl+Cで中断 |

`ac submit` は委譲先のojの終了コードを返します。

## AtCoderへの提出

`main.py` は標準ライブラリだけで動くテンプレートです。HypothesisやCYaRonなどはローカル検証側で使い、提出ファイルには持ち込みません。

ログイン済みのoj環境なら、次で提出できます。

```bash
 ac submit problems/abs/abc086_a --language '<AtCoderの言語ID>'
```

ojの対話的な確認を残しており、自動承認はしません。言語IDはAtCoderの現在の言語一覧に合わせて指定してください。認証や提出に関する対話は、資格情報を保存しないよう実行ログには取り込みません。

AtCoderの認証方式とojの互換性には[既知の問題](https://github.com/online-judge-tools/oj/issues/934)があります。認証が通らない場合は、普段のブラウザーで `main.py` を提出してください。サンプル取得が使えてもログイン・提出が使えるとは限りません。このリポジトリの検証では実際の提出を行っていません。

## コンテスト中の利用について

このコードはAI支援で作成しています。[AtCoderの公式英語ルール（20261014版）](https://info.atcoder.jp/entry/llm-rules-en)では、非生成AIのツールによるサンプル保存・入出力コード生成は対象外とされます。一方、事前にAIで作成したコードを競技中にコピーして使う場合は、**競技開始前のWeb公開と出典URLの明記**が条件になります。

そのため、提出用テンプレートにはこの公開リポジトリのURLを入れています。コピー時にも残してください。競技中のAIによる問題固有の生成・修正まで許可するものではなく、参加する競技の最新ルールに従ってください。過去問練習にはこの競技中の制限は適用されません。

## 開発・検証

```bash
 uv sync --frozen
 uv run --frozen ruff check .
 uv run --frozen ruff format --check .
 uv run --frozen pytest
```

CIではPython 3.11 / 3.12 / 3.13、Docker Compose、実際の子プロセスによる異常系、意図的なWAの検出・縮小を確認します。外部サイトに依存するダウンロードと実際の提出はCIで実行しません。

依存関係は `uv.lock` に固定しています。古いojの `distutils` 依存がPython 3.12以降でも起動するよう、互換性を提供するsetuptoolsも明示的に固定しています。

- [online-judge-tools](https://github.com/online-judge-tools/oj)
- [online-judge-template-generator](https://github.com/online-judge-tools/template-generator)
- [Hypothesis](https://hypothesis.readthedocs.io/en/latest/)
- [CYaRon](https://github.com/luogu-dev/cyaron)

ライセンスは既存の [Apache License 2.0](LICENSE) を継続しています。
