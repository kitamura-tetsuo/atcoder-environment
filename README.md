# AtCoder Environment

[![CI](https://github.com/kitamura-tetsuo/atcoder-environment/actions/workflows/ci.yml/badge.svg)](https://github.com/kitamura-tetsuo/atcoder-environment/actions/workflows/ci.yml)

PythonでAtCoderに取り組むための、問題準備・サンプル実行・差分テスト・反例縮小・計測をまとめた環境です。Docker Compose、VS Code Dev Containers、ローカルのuvで同じ `ac` コマンドを使えます。

**「WAになったが理由が分からない」を、自分の手元で再現できる小さな入力に変える**のが目的です。公式の隠しテストや提出時の実行ログを取得する機能ではありません。

## できること

| 機能 | コマンド / 実装 |
| --- | --- |
| 配列・木の問題用フォルダー作成 | `ac new` |
| 問題URLから作業フォルダーとサンプルを準備 | `ac prepare` / online-judge-tools |
| 対応する問題の制約を取り込み、生成器を作成 | `ac prepare` / `ac constraints` |
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

実行時に生成AIを呼ぶ機能はありません。問題固有の解答と正しい愚直解は自分で記述します。入力制約は対応する形式なら自動で取り込み、未対応の場合は手動で記述します。

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

AtCoderのサンプルと、対応する入力制約も取得する場合：

```bash
 ac prepare https://atcoder.jp/contests/abc485/tasks/abc485_c
```

作成先は新規または空のフォルダーに限ります。取得に失敗しても作業フォルダーは残り、サンプルは `ac download <問題フォルダー>`、制約は `ac constraints <問題フォルダー>` で再試行できます。自分で `test/sample-1.in` と `test/sample-1.out` を置くこともできます。

| ファイル | 自分で編集する内容 |
| --- | --- |
| `main.py` | 提出する高速な解答 |
| `brute.py` | 小さい入力に対する、単純で正しい参照解 |
| `constraints.toml` | 自動取り込みした公式制約と、小規模テスト用の上限 |
| `cases.py` | 自動生成器への接続、または手書きの入力形式・制約・境界ケース |
| `atcoder.toml` | 実行コマンド、時間制限、比較方法など |
| `test/*.in`, `test/*.out` | サンプルと回帰テスト |

標準の配列テンプレートでは `ac prepare` が制約の取り込みも試みます。対応形式は後述の「制約の自動取り込み」の範囲に限り、**未対応の条件を読み飛ばして成功扱いにはしません**。未対応ならサンプルを残して終了コード2になり、初期の `cases.py` を対象問題に合わせて編集する必要があります。サンプルだけ取得する場合は `--no-constraints`、木の例を使う場合は `--template tree` を指定してください。未実装の `main.py` / `brute.py` は `NotImplementedError` を出します。

### 制約の自動取り込み：ABC485 Cの例

[ABC485 C — Different Neighbor](https://atcoder.jp/contests/abc485/tasks/abc485_c?lang=en) の入力は、要素数 `N` と長さ `N` の整数配列 `C` です。新規作成なら上の `ac prepare` だけで、既存の問題フォルダーなら次で取り込みます。

```bash
 ac constraints problems/abc485/abc485_c
```

公式制約は次の `constraints.toml` に変換されます。`max = "N"` は、そのケースの要素数を上限にする指定です。

```toml
version = 1

[problem]
url = "https://atcoder.jp/contests/abc485/tasks/abc485_c"

[size]
name = "N"
min = 2
max = 300000

[values]
name = "C"
min = 1
max = "N"
min_distinct = 2

[testing]
max_size = 30
```

| 問題に書かれた条件 | 設定 |
| --- | --- |
| `2 ≤ N ≤ 3 × 10^5` | `[size] min = 2`, `max = 300000` |
| `1 ≤ C_i ≤ N` | `[values] min = 1`, `max = "N"` |
| 色が2種類以上 | `[values] min_distinct = 2` |
| 入力はすべて整数 | 整数配列用の生成器を使用 |
| 愚直解との比較を小さい入力で行う | `[testing] max_size = 30`（公式制約ではなくテスト設定） |

`N` を選んだ後で `1..N` の値を生成するので、Hypothesisが `N` を縮小しても `C_i ≤ N` が保たれます。`min_distinct = 2` は「全要素が同じ」を除外します。先頭の2要素を異なる値に固定せず、長い同色区間も生成します。境界ケースには、端・中央の1個だけ色が違う配列、交互の配列、上限値を含む配列などを用意します。

通常は `constraints.toml` を確認・調整すればよく、生成された `cases.py` を直接書く必要はありません。愚直解が十分速い場合は `[testing] max_size` を増やしてください。初回の取り込み時にも `--max-size 100` のように指定できます。

```bash
 ac stress problems/abc485/abc485_c --max-examples 1000 --seed 42
 ac bench problems/abc485/abc485_c --sizes 1000,10000,300000 --seed 42
```

上の実行には `main.py` と `brute.py` の実装が必要です。差分テストの乱数・境界ケースは小規模上限を使い、`bench` は公式制約の範囲で指定サイズを使います。`bench` は愚直解を実行せず、生成器は長い同色区間を含む入力を作ります。

#### 再利用するパーサーと対応範囲

- 入力形式の解析：既存の [online-judge-template-generator](https://github.com/online-judge-tools/template-generator) の構文木を使用します。
- 数式の解析：既存の [SymPy `parse_latex`](https://docs.sympy.org/latest/modules/parsing.html#sympy.parsing.latex.parse_latex) を `backend="antlr", strict=True` で使用します。SymPy 1.14.0 / ANTLRランタイム4.11.1に固定し、`3 \times 10^5` などを解釈します。LaTeXパーサー自体は上流でexperimental扱いのため、受け入れる構文を限定してテストしています。
- この環境で追加した部分：不等式の上下限を入力変数に結び付ける処理、明示的に対応した自然言語条件、Hypothesisへの変換です。

現版の対象は、**1行目が要素数、2行目が1始まりの整数配列**という入力です。要素数の上下限は整数定数、各要素の上下限は整数定数または要素数への参照に対応します。「少なくとも2種類の値」と「すべて整数」という英語の条件にも対応します。配列以外の構造、総和の条件、複雑な式による変数間依存、未対応の自然言語は自動生成の対象外です。

解析には英語版の問題文を使用します。任意の問題文を理解する機能ではなく、入力形式とConstraints欄の対応済み条件を変換する処理です。問題文の別の場所に条件がある場合も含め、生成した設定は確認してください。

`ac constraints` は解析に成功すると `constraints.toml` と `cases.py` を上書き更新します。解析元HTML、認識した条件、未解釈の条件、依存バージョンは `.ac/constraints/<ID>/report.json` と同じフォルダーに保存します。未解釈の条件があれば終了コード2となり、生成器は更新しません。

英語版ページをHTMLとして保存しておけば、通信せず再解析できます。

```bash
 ac constraints problems/abc485/abc485_c --html saved-task.html
```

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
| `2` | 設定・生成器・参照解・外部ツールのエラー、探索未完了、制約の未対応・未反映 |
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
- [SymPy LaTeX parsing](https://docs.sympy.org/latest/modules/parsing.html)
- [Hypothesis](https://hypothesis.readthedocs.io/en/latest/)
- [CYaRon](https://github.com/luogu-dev/cyaron)

ライセンスは既存の [Apache License 2.0](LICENSE) を継続しています。
