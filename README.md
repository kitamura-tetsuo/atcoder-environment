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
| 入力サイズ・分布ごとの時間とRSSをCSVに保存 | `ac bench` |
| 遅い入力を自動縮小し、行単位の時間を計測 | `ac bench` / Hypothesis / line_profiler |
| 問題文から入出力コード・生成器の下書きを作成 | `ac template` / online-judge-template-generator |
| 木・乱数用の追加ヘルパー | CYaRon / onlinejudge_random |
| 提出 | `ac submit` / ojの対話的な確認付き |

実行時に生成AIを呼ぶ機能はありません。問題固有の解答と正しい愚直解は自分で記述します。入力制約は対応する形式または確認済みの問題なら自動で取り込み、未対応の場合は手動で記述します。AHCでは公式生成済み入力を利用できます。

## AtCoderのPython環境に合わせる

[公式のCPythonインストールスクリプト（2025年10月言語更新）](https://img.atcoder.jp/file/language-update/2025-10/082-3-13_cpython.toml) に基づき、CPython 3.13.7 と公式43ライブラリ（42件のバージョン指定、1件のGitコミット指定）を固定しています。`pyproject.toml` の `judge` グループを既定でインストールし、`uv.lock` で依存関係を解決済みの版に固定します。

- NumPy 2.2.6、SciPy 1.16.1、PuLP 3.2.2、pandas 2.3.2、Numba 0.61.2、PyTorch 2.8.0+cpu など。
- `ac-library-python` は公式のGitコミットに固定。PyTorchは公式CPU版の配布元を使用。
- Hypothesis、oj、line_profilerなどのローカル検証ツールは別の依存定義で管理し、同じ仮想環境にインストールします。公式ライブラリと重複する依存関係のバージョンは統一します。ローカル専用ライブラリが提出先で利用できるという意味ではありません。
- `ac doctor` はCPythonの版、公式ライブラリのインストール版、Gitコミットを検証します。実行ログの `environment.json` にも結果を保存します。

Pythonの最適化ビルド、OS、CPUなどは本番と同一ではありません。Numbaは同バージョンの通常のPyPI配布版であり、公式環境独自のCUDA削除パッチは適用していません。PyPyおよびCodonの環境はこの設定の対象外です。PuLP 3.2.2 は内部の `pulp.__version__` が古い値を返すため、インストール版は `importlib.metadata.version("PuLP")` で確認します。

既存のDev Containerを更新するには **Dev Containers: Rebuild Container** を実行してください。Pythonのバージョンが変わるため、単なる `git pull` では既存コンテナーのインタープリターは更新されません。

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

### VS Code のブレークポイントでデバッグする

1. 対象問題の `main.py` をエディターで開き、停止したい行にブレークポイントを置きます。
2. 「実行とデバッグ」（Ctrl+Shift+D）で **AtCoder: Debug latest failure or tests** を選び、F5 を押します。
3. 対象問題の最新の実行ログに応じて、次の入力が自動選択されます。ブレークポイントで停止し、変数の確認、ステップ実行、続行（F5）ができます。

| 最新の実行ログ | デバッグする入力 |
| --- | --- |
| `ac stress` で失敗 | 保存された反例。縮小後の `failure/input.txt` を優先 |
| `ac bench` で失敗 | 失敗した各分布の入力を順に実行。再現確認済みの縮小入力を優先し、なければ元の失敗入力 |
| `ac stress` の参照解がエラー | `oracle-error/input.txt` を現在の参照解へ渡す |
| 最新が成功・反例なし、`ac test` / `ac run`、または保存された失敗入力なし | `test/` のサンプル・回帰テスト |

「最新」は、その問題の `.ac/runs/` 内の実行開始日時で判定します。最新が成功していれば、それ以前の失敗へ戻りません。ベンチマークの `slow` 判定だけで正常終了した入力や、プロファイル用の実行結果は自動選択しません。解析中に中断した実行でも、失敗した入力が保存されていれば利用します。

失敗入力は、`atcoder.toml` の現在の解答コマンド（参照解の失敗なら現在の参照解コマンド）へ標準入力として渡します。保存当時のソースではなく、修正中のコードをデバッグできます。対象入力・分布・過去の判定はターミナルと `.ac/logs/ac.log` に表示します。デバッグ自体は新しい実行ログを作らないため、次の `ac stress` / `ac bench` / `ac test` などを行うまでは、F5で同じ入力を再利用できます。

サンプルへのフォールバックでは、以前の練習環境と同じ online-judge-tools を使います。`judge.py` がある場合は対話型ジャッジを優先します。`main.py` 単体を実行したい場合は **Python Debugger: Current File** を選びます。

Dev Container では設定済みの `/opt/venv/bin/python`、ローカルでは `uv sync --frozen` 後に **Python: Select Interpreter** で `.venv/bin/python` を選んでください。VS Code Python Debugger（debugpy）の subprocess 機能で解答へ追従します。ブレークポイントで停止できるよう、デバッグではacの時間・メモリ制限を適用しません。性能の再計測や修正後の合否確認には通常の `ac test` / `ac stress` / `ac bench` を使ってください。

## Dockerを使わない場合

Linux x86_64、CPython 3.13.7、[uv](https://docs.astral.sh/uv/getting-started/installation/) が必要です。その他のOS・アーキテクチャでは同じLinux x86_64のDocker環境を利用してください。

```bash
 uv sync --frozen
 uv run --frozen ac doctor
 uv run --frozen ac test examples/pair-sum
 uv run --frozen ac stress examples/pair-sum --seed 42
```

以下の説明では `ac` と略記します。ホストからは `uv run --frozen ac`、コンテナーを起動せず直接呼ぶなら `docker compose run --rm dev ac` に置き換えてください。ネイティブWindowsではDockerまたはWSLを使ってください。

## URLで問題を指定する

問題フォルダーを指定するコマンドは、**問題URLでも指定できる**ようになっています。リポジトリのルートでは、例えば次の2つは同じ作業フォルダーを指定します。

```bash
 ac test https://atcoder.jp/contests/abc485/tasks/abc485_c
 ac test problems/abc485/abc485_c
```

URLは、コマンドを実行した場所の `problems/<コンテストID>/<問題ID>` に対応します。すでにその問題の作業フォルダー内にいて、`atcoder.toml` のURLが一致している場合は、現在のフォルダーを使います。`?lang=en`、末尾の `/`、`#...` の有無で別の問題にはなりません。フォルダーの直接指定と、省略時の `.` も引き続き使えます。

別の保存先を使う場合は、URLと一緒に `--directory`（`-d`）を指定します。

```bash
 ac prepare https://atcoder.jp/contests/abc485/tasks/abc485_c -d work/different-neighbor
 ac stress https://atcoder.jp/contests/abc485/tasks/abc485_c -d work/different-neighbor
```

`ac new URL` はオフラインで作業フォルダーを作成します。`ac constraints URL`、`ac download URL`、`ac template URL` も作業フォルダーがなければ作成し、それぞれ制約取得・サンプル取得・コードの下書き生成を行います。`test`、`stress`、`bench`、`run`、`replay`、`promote`、`submit` は準備済みの作業フォルダーを使います。環境全体を確認する `ac doctor` に問題URLは不要です。

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

作成先は新規または空のフォルダーに限ります。取得に失敗しても作業フォルダーは残り、サンプルは `ac download <問題URL>`、制約は `ac constraints <問題URL>` で再試行できます。自分で `test/sample-1.in` と `test/sample-1.out` を置くこともできます。

```bash
 ac download https://atcoder.jp/contests/abc485/tasks/abc485_c
```

| ファイル | 自分で編集する内容 |
| --- | --- |
| `main.py` | 提出する高速な解答 |
| `brute.py` | 小さい入力に対する、単純で正しい参照解 |
| `constraints.toml` | 自動取り込みした公式制約と、小規模テスト用の上限 |
| `cases.py` | 自動生成器への接続、または手書きの入力形式・制約・境界ケース |
| `atcoder.toml` | 実行コマンド、時間制限、比較方法など |
| `test/*.in`, `test/*.out` | サンプルと回帰テスト |

標準の配列テンプレートでは `ac prepare` が制約の取り込みも試みます。対応形式は後述の「制約の自動取り込み」の範囲に限り、**未対応の条件を読み飛ばして成功扱いにはしません**。未対応ならサンプルを残して終了コード2になり、初期の `cases.py` を対象問題に合わせて編集する必要があります。サンプルだけ取得する場合は `--no-constraints`、木の例を使う場合は `--template tree` を指定してください。未実装の `main.py` / `brute.py` は `NotImplementedError` を出します。

**初期の配列・木の `cases.py` をそのまま使った `ac stress` / `ac bench` は、実行前にエラーになります。** 古い版で作った作業フォルダーも対象です。制約の自動取り込みに失敗した後、例の整数配列を別形式の問題に入力してしまうことを防ぎます。`ac constraints <問題URL>` で対応する生成器を作るか、`cases.py` の入力形式・範囲を問題に合わせて実装してください。コメントや空白だけの変更では設定済みとはみなしません。サンプルを使う `ac test` は生成器の設定前でも実行できます。

### 制約の自動取り込み：ABC485 Cの例

[ABC485 C — Different Neighbor](https://atcoder.jp/contests/abc485/tasks/abc485_c?lang=en) の入力は、要素数 `N` と長さ `N` の整数配列 `C` です。新規作成なら上の `ac prepare` でサンプルと一緒に取り込めます。制約だけを取得・更新する場合は次を実行します。

```bash
 ac constraints https://atcoder.jp/contests/abc485/tasks/abc485_c
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
 ac stress https://atcoder.jp/contests/abc485/tasks/abc485_c --max-examples 1000 --seed 42
 ac bench https://atcoder.jp/contests/abc485/tasks/abc485_c --sizes 1000,10000,300000 --seed 42
```

上の実行には `main.py` と `brute.py` の実装が必要です。差分テストの乱数・境界ケースは小規模上限を使い、`bench` は公式制約の範囲で指定サイズを使います。`bench` は愚直解を実行せず、既定で値の種類数・偏り・並び順を変えた複数の入力分布を調べます。

### 文字列の制約の自動取り込み：ABC485 Dの例

[ABC485 D — ABC|ABC|ABC](https://atcoder.jp/contests/abc485/tasks/abc485_d?lang=en) の入力は、整数 `N` と、`A`・`B`・`C` からなる長さ `N` の文字列 `S` です。既存の作業フォルダーも次のコマンドで生成器を更新できます。

```bash
 ac constraints https://atcoder.jp/contests/abc485/tasks/abc485_d
 ac bench https://atcoder.jp/contests/abc485/tasks/abc485_d
```

`main.py` と `brute.py` は変更しません。自動生成される `constraints.toml` は配列用の `[values]` の代わりに `[string]` を持ちます。

```toml
version = 1

[problem]
url = "https://atcoder.jp/contests/abc485/tasks/abc485_d"

[size]
name = "N"
min = 3
max = 300000

[string]
name = "S"
alphabet = "ABC"

[testing]
max_size = 30
```

生成器は `N` と `S` を別々の行に出力し、文字列長と文字集合を検証します。ベンチマークは `3,10,100,1000,10000,100000,300000` を自動選択し、同じ文字の連続、交互、周期的な文字列、ブロック、乱数などを試します。文字列でもHypothesisによる差分テストと資源超過入力の縮小に対応します。

### ラベル付きグラフの制約の自動取り込み：ABC485 Eの例

```bash
ac constraints https://atcoder.jp/contests/abc485/tasks/abc485_e
ac bench https://atcoder.jp/contests/abc485/tasks/abc485_e
```

`N M` の行と、続く `M` 行の `U_i V_i C_i` を持つ入力に対応します。ABC485 E の `constraints.toml` には次の設定が生成されます。

```toml
version = 1

[problem]
url = "https://atcoder.jp/contests/abc485/tasks/abc485_e"

[vertices]
name = "N"
min = 2
max = 200000

[edges]
name = "M"
min = 1
max = 200000
source = "U"
target = "V"
label = "C"
endpoint_min = 1
endpoint_max = "N"
label_min = 1
label_max = "M"
allow_self_loops = false
allow_parallel = true

[testing]
max_size = 30
```

頂点数と辺数は独立です。自己ループを生成せず、多重辺は許容します。差分テストでは両方の個数を `max_size` 以下に制限し、すべての辺の端点とラベルを再検証します。

ベンチマークの `size` は辺数 M を意味します。既定の `--pattern all` は鎖状（`default`）、閉路（`cycle`）、入出力スター（`star_in` / `star_out`）、ハブに異なる色・同じ色・2色を集中（`hub_distinct` / `hub_same` / `hub_two`）、多重辺（`parallel`）、少ない頂点に多数の辺（`dense`）、最大頂点数と指定辺数（`sparse`）、乱数（`random`）を試します。時間・メモリ超過時は Hypothesis で辺数、頂点数、色数、seed を縮小します。公式上限を超える `--sizes` の指定は M だけを緩め、N の公式範囲は維持します。

このモデルは端点がそれぞれ 1..N、ラベルが 1..M、自己ループ禁止が明示された3整数の辺リストに限ります。単純グラフ・連結性・木・追加の重み制約などを推測して読み飛ばすことはありません。

#### 再利用するパーサーと対応範囲

- 入力形式の解析：既存の [online-judge-template-generator](https://github.com/online-judge-tools/template-generator) の構文木を使用します。
- 数式の解析：既存の [SymPy `parse_latex`](https://docs.sympy.org/latest/modules/parsing.html#sympy.parsing.latex.parse_latex) を `backend="antlr", strict=True` で使用します。SymPy 1.14.0 / ANTLRランタイム4.11.1に固定し、`3 \times 10^5` などを解釈します。LaTeXパーサー自体は上流でexperimental扱いのため、受け入れる構文を限定してテストしています。
- この環境で追加した部分：不等式の上下限を入力変数に結び付ける処理、明示的に対応した自然言語条件、Hypothesisへの変換です。

汎用解析は従来の整数配列・文字列・ラベル付き辺リストに加え、複数の整数スカラー、独立した配列、固定幅の繰り返し行、先行変数に依存する上下限を扱います。

**ABC476〜ABC485の全70問**には、汎用解析に加えて、確認済みの構造化制約定義も利用できます。木、順列、文字列行列、異なる形式のクエリ、重複禁止、総和制約、複数テストケースなどを共通の生成器に変換します。特殊な条件は公式英語問題文の指紋（SHA-256）が一致する確認済み定義だけを使い、未知の問題や変更された問題文については推測による成功扱いをしません。解析方法は report.json の `method` に記録します。

構造化モデルの設定では `[size]` が主サイズと刻み幅、`[structured]` が入力生成の手順、`[testing]` が小規模テスト上限です。主サイズ以外の変数も制約に従って生成します。境界値、反復、偏り、乱数、木の形状などの代表的な分布を検査できます。任意の有効入力をすべて生成できる保証ではありません。サイズの範囲外指定では、構造を壊さず緩和できる独立した上限のみを緩和し、依存関係や刻み幅を破る指定は拒否します。

自動取り込みに失敗した場合、ログと `report.json` の `manual_edits` に、対象の `cases.py` のパス、問題の実際の入力形式、公式制約の全項目、編集する関数名と役割を表示します。未対応の条件は読み飛ばさず、設定済みの生成器がない場合は `strategy()`、`serialize(case)`、`edge_cases()`、`benchmark_case(size, seed)` を問題に合わせて記述してください。分布を増やすには `benchmark_patterns(size)`、縮小するには `benchmark_strategy(max_size, seed, pattern=...)` も実装します。

解析には英語版の問題文を使用します。任意の問題文を理解する機能ではなく、入力形式とConstraints欄の対応済み条件を変換する処理です。問題文の別の場所に条件がある場合も含め、生成した設定は確認してください。

`ac constraints` は解析に成功すると `constraints.toml` と `cases.py` を上書き更新します。解析元HTML、認識した条件、未解釈の条件、依存バージョンは `.ac/constraints/<ID>/report.json` と同じフォルダーに保存します。未解釈の条件があれば終了コード2となり、生成器は更新しません。

英語版ページをHTMLとして保存しておけば、通信せず再解析できます。

```bash
 ac constraints https://atcoder.jp/contests/abc485/tasks/abc485_c --html saved-task.html
```

### AHCの公式入力を取り込む

AHC065〜AHC074の各問題の公式ローカルツールZIPには、公式生成器で生成済みの入力が100件ずつ含まれます。`ac prepare` または `ac constraints` は、英語問題文の公式リンクからZIPを取得し、入力を取り込みます。システムテスト用の空のA-Finalページは元のA問題へ解決します。

```bash
ac prepare https://atcoder.jp/contests/ahc070/tasks/ahc070_a
ac constraints https://atcoder.jp/contests/ahc070/tasks/ahc070_a
ac bench https://atcoder.jp/contests/ahc070/tasks/ahc070_a --sizes 1,2,3 --repeat 1
```

AHCでは `--sizes` は入力の1始まりの番号（1〜100）です。省略時は全件を測ります。入力は `.ac/official-inputs/`、元のZIPも `.ac/` に保存され、ダウンロードしたプログラムを自動実行しません。公式ツールに含まれるRust生成器を手動で使うこともできます。保存済みのHTMLとZIPを使う場合は `ac constraints URL --html statement.html --tools-zip tools.zip` とします。

通常の8問は時間・メモリを計測できますが、AHC069・AHC074の対話型問題には公式ローカルテスターが必要です。AHCではスコア評価、任意サイズの生成、新しいseedの自動生成、Hypothesisによる入力内容の縮小、愚直解との出力一致判定は提供しません。

### 2. サンプルを確認する

```bash
 ac test https://atcoder.jp/contests/abc485/tasks/abc485_c
```

全入力に対して別々のプロセスを起動し、AC / WA / RE / TLE / OLEを表示します。テストが0件だった場合や `.out` が欠けている場合はエラーになります。

### 3. 愚直解と比較して反例を探す

```bash
 ac stress https://atcoder.jp/contests/abc485/tasks/abc485_c --max-examples 1000 --seed 42 --budget 120
```

`edge_cases()` を確認した後、Hypothesisの生成と縮小を実行します。境界ケースで失敗した場合も、同じ種類の失敗を起こす小さい入力を探します。

各ケースの解答と参照解は、標準で**2つの新しいプロセスを同時に起動**して比較します。ケース生成・探索・縮小の順序は変えず、プロセスの起動やimportにかかる待ち時間を重ねて検証件数を増やします。並列実行で時間切れになったケースは、両方のプロセスを終了してから逐次実行で再確認します。保存する反例も最後に逐次実行で再現確認します。Ctrl+Cでは実行中の両プロセスとその子孫を停止します。

CPUが強く制限されている環境や、解答と参照解が同じ一時ファイルを書き込む場合は、従来の逐次実行を選べます。

```bash
 ac stress problems/abc485/abc485_c --sequential --seed 42 --budget 120
```

ケースの評価が終わった時点で、前回の表示から5秒以上経過していれば、評価回数・毎秒の評価回数・経過時間を表示します。`summary.json` の `evaluations_per_second` に評価速度、`execution` に実行方式・解答と参照解の累積実行時間・時間切れの逐次再確認回数を保存します。累積時間はキャンセルされたプロセスを除き、最後の反例再現確認を含みます。同時実行した時間が重なるため、解答と参照解の時間の合計は全体の経過時間とは一致しません。

- `--max-examples` はHypothesisの探索設定です。境界ケース、縮小、再現確認を含めた総実行回数とは異なります。
- 評価回数には境界ケース・縮小・再現確認が含まれ、重複のない入力の個数ではありません。
- `--budget` は探索全体の時間予算です。進行中の1ケース、時間切れの逐次再確認、最後の反例再現確認は、予算を超えた後も各プロセスの実行時間制限まで動く場合があります。
- 時間予算を使い切って反例が見つからなければ `INCOMPLETE` / 終了コード2になり、成功とは扱いません。
- WA / REの縮小では最初の判定種別を維持します。TLE / OLEは保存と再現確認を行い、縮小しません。
- 参照解のRE / TLEは、解答のWAとは区別してエラーにします。
- 縮小は大域的に最小の反例を保証しません。seedのほか、ライブラリとソースのバージョンも結果に影響します。

生成器では、単純な `random` 呼び出しを `strategy()` の中に隠すより、Hypothesisの `st.integers` / `st.lists` / `@st.composite` で制約を表すと、縮小時にも構造を維持できます。木テンプレートは接続性・頂点数・辺数を維持しつつ、頂点のラベルも変化させます。

### 4. ログを見る・修正後に再現する

実行時に表示される `.ac/runs/<実行ID>/` を開きます。

| 保存先 | 内容 |
| --- | --- |
| `summary.json` | 結果、実行回数、seed、探索状態、評価速度と実行時間の内訳 |
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
 ac replay https://atcoder.jp/contests/abc485/tasks/abc485_c '<失敗ディレクトリー>'
 ac promote https://atcoder.jp/contests/abc485/tasks/abc485_c '<失敗ディレクトリー>' --name regression-001
 ac test https://atcoder.jp/contests/abc485/tasks/abc485_c
```

`replay` は保存した入力・期待値を**現在の解答**で実行します。保存したソースに自動で戻す処理はありません。`promote` 後は通常の `ac test` に含まれます。

単独の入力も問題URLを指定して実行できます。

```bash
 ac run https://atcoder.jp/contests/abc485/tasks/abc485_c --input input.txt
```

`--input` / `--expected` / `--html` と、`replay` / `promote` の保存先パスは、コマンドを呼び出したディレクトリーから解決します。設定した解答・参照解のスクリプトは問題フォルダーを作業ディレクトリーとして実行します。

アプリ自身のログはLoguruで標準出力と `.ac/logs/ac.log` の両方に出し、日単位でローテーションします。問題フォルダーと `.ac/` はGit管理対象外です。

### 5. 時間・メモリが増える入力を探し、縮小して行単位で調べる

```bash
 ac bench https://atcoder.jp/contests/abc485/tasks/abc485_c
```

追加のフラグなしで、入力サイズと分布を変えて測定し、遅い入力・時間超過・メモリ超過を見つけると **Hypothesisによる縮小と行プロファイル**を自動で行います。通常の測定と縮小判定には元のプログラムを使い、プロファイルの計測負荷を混ぜません。参照解は実行せず、出力の正しさは検証しません。

`--sizes` を省略すると、`constraints.toml` の `[size] min` / `max` から**公式の下限・上限と、その間の10の累乗**を自動選択します。ABC485 Cの `2..300000` なら `2,10,100,1000,10000,100000,300000` です。差分テスト用の `[testing] max_size` はベンチマークや資源超過の縮小の上限に使いません。

サイズを手動指定する場合は `--sizes` が優先され、指定順を維持します。**公式のサイズ制約を超える指定は警告を表示して実行します。** 自動取り込みした生成器では、ベンチマーク時のサイズ上限・下限だけを緩め、配列の値域や文字列の文字種といった制約は引き続き検証します。通常の自動サイズ選択や `ac stress` は公式制約内に留まります。独自の生成器がサイズを検証する場合は、範囲外サイズを扱えるようにその生成器も調整してください。制約ファイルがない独自の生成器では `100,1000,10000` を使います。未設定の初期テンプレートや不正な制約ファイルはエラーになります。

```bash
 ac bench problems/abc485/abc485_c --sizes 1000,10000,100000,300000
 # Deliberately exceed ABC485 D's official N <= 300000 for load testing.
 ac bench https://atcoder.jp/contests/abc485/tasks/abc485_d --sizes 3000000 --pattern default --repeat 1
```

VS Codeの **AtCoder: Benchmark** も自動選択を使います。**AtCoder: Benchmark custom sizes** ではサイズを入力できます。

#### 既定で試す分布

自動取り込みした整数配列では `--pattern all` が既定です。値の種類数、偏り、同じ値の連続区間、並び順、境界値を変えます。

自動取り込みした文字列も同じ分布を使い、値を許可された文字に対応付けます。例えば `alphabet = "ABC"` の `palette` は `ABCABC...`、`constant` は `AAA...` です。`distinct` は `N` が文字の種類数以下のときに限ります。

| 分布 | 生成する入力 | 主に調べる性質 |
| --- | --- | --- |
| `default` | N−1個が同じ値、1個だけ異なる | 極端な偏り、長い同値区間 |
| `balanced` | 2種類をほぼ半数ずつ連続させる | 値ごとの件数に依存する反復処理 |
| `alternating` | 2種類を交互に配置 | 連続区間と隣接条件 |
| `blocks` | 約√N種類を長い区間に配置 | 中程度の種類数と長い区間の組合せ |
| `palette` | 可能な最大種類数を周期的に配置 | 種類ごとに持つ配列・辞書の増大 |
| `distinct` | 全要素が異なる配列をシャッフル | 種類数がNになる時間・メモリ負荷 |
| `sorted` / `reversed` | 最大種類数の昇順／降順配列 | 並び順に依存する処理 |
| `random` | 許容値域内の一様乱数 | 規則的な配置以外の入力 |
| `constant` / `constant_max` | 全要素が下限値／上限値 | 同値・境界値に依存する処理 |

制約に反する分布は使いません。例えば「2種類以上」が必要な問題では定数分布を除き、値域が狭ければ全要素が異なる分布を除きます。同じサイズで全く同じ入力になった分布は測定を共有し、CSVの `duplicate_of` に記録します。

**ある分布でRE / TLE / OLE / MLEになっても、他の分布の検査は続けます。** 失敗した分布については、それ以上のサイズを省略します。これらは典型的な負荷の軸を調べるための分布であり、任意のアルゴリズムの最悪入力を完全に網羅する保証ではありません。

特定の分布だけを確認することもできます。

```bash
 ac bench problems/abc485/abc485_c --pattern balanced
 ac bench problems/abc485/abc485_c --pattern distinct --sizes 1000,5000,10000
```

#### 検出条件とメモリ計測

既定の検出条件は次のとおりです。

| 症状 | 条件 |
| --- | --- |
| `slow` | 正常終了した測定の中央値が通常のタイムアウトの80%以上 |
| `timeout` | 通常のタイムアウトに到達 |
| `memory_limit` | 観測したRSSがメモリ上限を超過（既定512 MiB） |

例えば通常のタイムアウトが2秒なら、1.6秒以上の成功ケースも診断します。`--slow-seconds`、`--timeout`、`--memory-limit` で変更できます。

```bash
 ac bench problems/abc485/abc485_c --slow-seconds 1.0 --timeout 2 --memory-limit 1024
```

CSVには起動を含む時間の最小値・中央値・最大値と `max_rss_kib` を保存します。RSSは既存の [psutil](https://psutil.readthedocs.io/stable/) で解答プロセスと子プロセスを約10msごとに観測した最大値です。短い一時的なピークを見逃すことがあり、OSが記録する厳密な最高値ではありません。親であるacや他の実行のピークを解答のメモリに混ぜません。共有ページは複数のプロセスに重複計上される場合があります。

メモリ上限は観測後にプロセス群を停止する仕組みで、カーネルが割り当てそのものを拒否する隔離ではありません。急な割り当てでは設定値を超えてから停止する場合があります。`MemoryError` や `SIGKILL` という理由だけで、観測していないメモリ超過をMLEとは判定しません。必要ならDocker自体にもメモリ上限を設定してください。

`[run] memory_limit_mib = 512` と設定するか、`ac run` / `ac test` / `ac stress` / `ac replay` に `--memory-limit 512` を付けると、その解答プロセスにも同じ計測・上限を適用できます。参照解には適用しません。

#### Hypothesisによる資源超過の縮小

分布・症状ごとに最初に見つかった入力を保存し、[Hypothesis](https://hypothesis.readthedocs.io/en/latest/) でより小さな再現入力を探します。自動生成器では、まず元のseedと分布を保ってNを縮小し、次にN・値の種類数・値域・配置に関わるパラメーターを縮小します。巨大な配列の全要素を独立にdrawせず、少数のパラメーターから生成するため、差分テストの小規模上限を超える入力も扱えます。

判定には**通常実行を少なくとも2回**使い、候補を採用する前と最終確認で同じ症状の再現を調べます。メモリ超過を時間超過に置き換えたり、時間超過を単なる遅い成功に置き換えたりしません。時間計測は環境負荷で揺れるため、再現しない候補は採用せず、最終確認で揺れた結果には `unstable` を残します。

探索時間の既定値は **分布・症状ごとに30秒**です。`--shrink-budget 60` で変更できます。時間制限は実行の間で確認し、最終の再現確認に最大4回の通常実行が追加されることがあります。実行回数にも上限を設けています。時間切れ・中断でも元入力と、それまでに確認できた候補を残します。`minimization.json` の `state` / `confirmed` / `evaluations` で結果を確認してください。結果は戦略と予算内で確認できた最小候補であり、全ての有効入力に対する数学的な最小値の保証ではありません。

通常測定を終えてから診断するため、ある分布の縮小に時間がかかっても他の分布の測定を妨げません。既に診断対象になった分布・症状の後続サイズは、同じ代表入力の診断への参照を残します。

#### 自動の行プロファイルと保存物

縮小した再現入力（縮小できなければ元入力）を使い、既存の [line_profiler](https://kernprof.readthedocs.io/en/latest/) で**別プロセス**を実行します。提出ファイルを変更せず、トップレベルと同じファイル内の関数の各行を計測し、時間の長い上位5行を表示します。速い入力にはプロファイルを実行しません。

実行フォルダー `.ac/runs/<日時>-bench-<ID>/` に次を保存します。

| 保存先 | 内容 |
| --- | --- |
| `benchmark.csv` / `summary.json` | 通常実行のサイズ・分布・時間・RSS・省略理由・診断への参照 |
| `size-<N>-<分布>-<症状>/initial/input.txt` | 最初に見つかった入力 |
| 同フォルダーの `failure/input.txt` | 保存した再現候補（確定状況は `result.json`） |
| 同フォルダーの `minimization.json` | 縮小の状態、試行数、再現確認、予算 |
| 同フォルダーの `profile/line-profile.csv` | 行番号、実行回数、累積秒数、1回の平均、ソース行 |
| 同フォルダーの `profile/profile-status.json` | プロファイルの終了状態、途中結果かどうか、RSS |

保存した資源超過の入力は、期待出力を必要としない `ac run` で再実行できます。

```bash
 ac run problems/abc485/abc485_c --input /path/to/failure/input.txt --timeout 2 --memory-limit 512
```

`ac replay` / `ac promote` は期待出力のある正誤比較用の保存物に使います。

`default` 分布ではパスの分布名を省略します。`profile/input.txt` にはプロファイルで実際に使った入力を保存します。行の時間には呼び出した関数の時間も含まれるため、呼び出し元・呼び出し先の合計を単純に足し合わせないでください。CSVは**行ごとの時間**であり、行ごとのメモリ割り当て量ではありません。

プロファイルの制限は `--profile-timeout 10`（既定10秒）で、通常のタイムアウトと独立しています。時間・メモリの制限で中断できれば途中の計測も保存します。NumPy等のネイティブ処理は呼び出し元のPython行に時間が付きますが、ネイティブ処理がシグナルを処理できない場合や外側のガードで強制終了した場合は、途中結果が残らないことがあります。プロファイルはacと同じCPythonで直接起動するスクリプトが対象で、PyPy・外部ファイルの各行・子プロセス・別スレッドは対象外です。未対応の場合も通常測定と入力を保存します。

必要な場合だけ、診断を個別に省略できます。

```bash
 ac bench problems/abc485/abc485_c --no-shrink
 ac bench problems/abc485/abc485_c --no-profile
```

通常測定で失敗を検出した場合は終了コード1です。通常測定が成功しても、最終確認が不安定、またはプロファイルが未完了の場合は2です。予算内の縮小が完了したかどうかは終了コードだけで判断せず、`minimization.json` を確認してください。本番とCPU・インタープリター・起動コストが違うため、ローカルの数点の測定で本番のACを保証しません。

#### 手書きの生成器

従来の2引数 `benchmark_case(size, seed)` もそのまま使えます。その場合は既定分布だけを測り、分布全体を検査したとは表示しません。独自形式で自動診断を使うには、`benchmark_case(size, seed, pattern=...)` に加えて次のフックを定義します。

```python
# Return only patterns valid at this size.
def benchmark_patterns(size):
    return ("default", "balanced")

# Return a Hypothesis strategy over valid inputs up to max_size.
# Keep the ordinary serialize(case) compatible with the returned cases.
def benchmark_strategy(max_size, seed, *, pattern="default"):
    ...
```

`benchmark_strategy` がない場合、資源超過入力は保存しますが縮小済みとは表示しません。自動取り込みした整数配列・文字列・ラベル付き辺リスト・構造化モデルは全フックに対応しており、以前生成した整数配列の `cases.py` も束縛済みモデル経由で新機能を使えます。

## 入力コードの自動生成と追加ヘルパー

```bash
 ac template https://atcoder.jp/contests/abs/tasks/abc086_a
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
 ac submit https://atcoder.jp/contests/abs/tasks/abc086_a --language '<AtCoderの言語ID>'
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

CIではAtCoderと同じCPython 3.13.7、Docker Compose、実際の子プロセスによる異常系、意図的なWAの検出・縮小を確認します。外部サイトに依存するダウンロードと実際の提出はCIで実行しません。

依存関係は `uv.lock` に固定しています。古いojの `distutils` 依存がPython 3.12以降でも起動するよう、互換性を提供するsetuptoolsも明示的に固定しています。

- [online-judge-tools](https://github.com/online-judge-tools/oj)
- [online-judge-template-generator](https://github.com/online-judge-tools/template-generator)
- [SymPy LaTeX parsing](https://docs.sympy.org/latest/modules/parsing.html)
- [Hypothesis](https://hypothesis.readthedocs.io/en/latest/)
- [CYaRon](https://github.com/luogu-dev/cyaron)

ライセンスは既存の [Apache License 2.0](LICENSE) を継続しています。
