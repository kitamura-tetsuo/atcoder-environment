"""Use synthetic official-tool archives to test offline, safe AHC import."""

from __future__ import annotations

import io
import json
import sys
import zipfile

import pytest

from atcoder_env.benchmark_sizes import choose_benchmark_sizes
from atcoder_env.cli import main
from atcoder_env.config import Config
from atcoder_env.constraint_io import load_constraints
from atcoder_env.constraints import import_constraints
from atcoder_env.harness import case_module
from atcoder_env.official_corpus import official_archive, unpack_inputs

URL = "https://atcoder.jp/contests/ahc065/tasks/ahc065_a"
SOURCE = "https://img.atcoder.jp/ahc065/test.zip"


def html(interactive=False):
    return (
        '<span class="lang-en"><p>'
        + ("This is a reactive problem." if interactive else "Input")
        + f'</p><a href="{SOURCE}">Local version</a></span>'
    ).encode()


def archive(interactive=False):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("tools/in/0000.txt", "1\n")
        z.writestr("tools/in/0001.txt", "2\n")
        z.writestr("tools/in/0002.txt", "3\n")
        z.writestr("tools/../../not-input.txt", "never extract")
        z.writestr("tools/src/bin/gen.rs", "not executed")
        if interactive:
            z.writestr("tools/src/bin/tester.rs", "not executed")
    return data.getvalue()


def install(root, interactive=False):
    source = root / "statement.html"
    source.write_bytes(html(interactive))
    tools = root / "tools.zip"
    tools.write_bytes(archive(interactive))
    assert import_constraints(Config(root=root, url=URL), html_path=source, tools_zip=tools) == 0
    return load_constraints(root / "constraints.toml")


def test_offline_corpus_reads_exact_inputs_without_running_tools(tmp_path):
    model = install(tmp_path)
    assert model.count == 3 and not model.interactive
    assert choose_benchmark_sizes(tmp_path) == [1, 2, 3]
    with pytest.raises(ValueError, match="indices"):
        choose_benchmark_sizes(tmp_path, [4])
    with case_module(tmp_path) as module:
        assert module.official_corpus
        assert [module.serialize(i) for i in range(1, 4)] == ["1\n", "2\n", "3\n"]
        assert not hasattr(module, "benchmark_strategy")
    assert not (tmp_path / "not-input.txt").exists()
    assert not list(tmp_path.rglob("gen.rs"))
    manifest = json.loads((tmp_path / model.folder / "manifest.json").read_text())
    assert [item["original_name"] for item in manifest] == ["0000.txt", "0001.txt", "0002.txt"]


def test_archive_rejects_nonofficial_link_and_invalid_zip():
    with pytest.raises(ValueError, match="unique official"):
        official_archive(html().replace(b"img.atcoder.jp", b"example.com"), URL)
    with pytest.raises(ValueError, match="ZIP"):
        unpack_inputs(b"invalid")


def test_interactive_and_exact_answer_guard(tmp_path, capsys):
    model = install(tmp_path, interactive=True)
    assert model.interactive
    assert main(["bench", str(tmp_path), "--sizes", "1"]) == 2
    assert "official local tester" in capsys.readouterr().out
    assert main(["stress", str(tmp_path), "--max-examples", "1"]) == 2
    assert "official tester/visualizer" in capsys.readouterr().out


def test_bench_continues_independent_seed_after_failure(tmp_path):
    install(tmp_path)
    (tmp_path / "main.py").write_text(
        "import sys\nvalue=int(input())\nif value==1: sys.exit(1)\nprint(value)\n"
    )
    (tmp_path / "atcoder.toml").write_text(
        f'[run]\nsolution = [{json.dumps(sys.executable)}, "main.py"]\n'
    )
    assert main(["bench", str(tmp_path), "--repeat", "1", "--no-profile", "--no-shrink"]) == 1
    latest = sorted((tmp_path / ".ac" / "runs").iterdir())[-1]
    rows = json.loads((latest / "summary.json").read_text())["rows"]
    assert [row["size"] for row in rows] == [1, 2, 3]
    assert rows[0]["status"] != "ok"
    assert all(row["status"] == "ok" for row in rows[1:])


def test_empty_final_task_resolves_to_original(monkeypatch, tmp_path):
    from atcoder_env import constraints

    final_url = URL.replace("_a", "_b")
    seen = []

    def fetch(url):
        seen.append(url)
        if url == final_url:
            return b"<html><title>A-Final - System Test</title></html>"
        assert url == URL
        return html()

    monkeypatch.setattr(constraints, "fetch_statement", fetch)
    monkeypatch.setattr(constraints, "download_archive", lambda url: archive())
    assert import_constraints(Config(root=tmp_path, url=final_url)) == 0
    assert seen == [final_url, URL]
    model = load_constraints(tmp_path / "constraints.toml")
    assert model.url == final_url and model.count == 3
