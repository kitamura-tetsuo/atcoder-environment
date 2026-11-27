"""Import official heuristic-contest inputs without running downloaded programs."""

from __future__ import annotations

import hashlib
import io
import json
import re
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from hypothesis import strategies as st
from loguru import logger

from atcoder_env.external import task_parts

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_INPUTS = 10000


def is_heuristic(url: str | None) -> bool:
    if not url:
        return False
    try:
        contest, _ = task_parts(url)
    except ValueError:
        return False
    return re.fullmatch(r"ahc\d+", contest) is not None


def official_archive(html: bytes, url: str) -> tuple[str, bool]:
    contest, _ = task_parts(url)
    if not is_heuristic(url):
        raise ValueError("Official corpus import is supported for AHC tasks only")
    soup = BeautifulSoup(html, "html.parser")
    english = soup.select_one(".lang-en")
    if english is None:
        raise ValueError("No English AHC statement; use the contest's original problem page")
    links = []
    for link in english.select("a[href]"):
        href = link["href"]
        parsed = urlparse(href)
        if (
            parsed.scheme == "https"
            and parsed.netloc == "img.atcoder.jp"
            and re.fullmatch(r"/" + re.escape(contest) + r"/[A-Za-z0-9_-]+\.zip", parsed.path)
            and not parsed.query
            and not parsed.fragment
            and not parsed.path.endswith("_windows.zip")
            and "local version" in link.get_text(" ", strip=True).lower()
        ):
            links.append(href)
    if len(set(links)) != 1:
        raise ValueError("No unique official local-tools ZIP in the English AHC statement")
    text = " ".join(english.get_text(" ", strip=True).lower().split())
    interactive = "interactive task" in text or "reactive problem" in text
    return links[0], interactive


def download_archive(url: str) -> bytes:
    try:
        with requests.get(url, stream=True, timeout=(15, 45), allow_redirects=False) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError(f"Unexpected official-tools HTTP {response.status_code}")
            chunks = []
            size = 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise ValueError("Official archive exceeds local download cap")
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.RequestException as error:
        raise ValueError(f"Official tools download failed: {error}") from error


def unpack_inputs(data: bytes) -> tuple[list[tuple[str, str]], bool]:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Official archive exceeds local download cap")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [
                info
                for info in archive.infolist()
                if re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*in/[0-9]+\.txt", info.filename)
            ]
            if not 1 <= len(entries) <= MAX_INPUTS:
                raise ValueError("No supported numbered input corpus in the official tools ZIP")
            if sum(info.file_size for info in entries) > MAX_TOTAL_BYTES:
                raise ValueError("Official corpus exceeds local expanded size cap")
            result = []
            seen = set()
            for info in sorted(entries, key=lambda x: (int(Path(x.filename).stem), x.filename)):
                seed_name = Path(info.filename).name
                if seed_name in seen or info.file_size > MAX_INPUT_BYTES:
                    raise ValueError("Duplicate or oversized input in official corpus")
                seen.add(seed_name)
                raw = archive.read(info)
                text = raw.decode("utf-8")
                if not text.strip() or "\x00" in text:
                    raise ValueError("Empty or invalid text input in official corpus")
                result.append((seed_name, text))
            has_tester = any(name.endswith("/src/bin/tester.rs") for name in archive.namelist())
            return result, has_tester
    except (zipfile.BadZipFile, UnicodeDecodeError, RuntimeError) as error:
        raise ValueError(f"Cannot read official input ZIP: {error}") from error


@dataclass(frozen=True)
class OfficialCorpus:
    root: Path
    url: str
    source_url: str
    archive_sha256: str
    count: int
    interactive: bool
    folder: str

    @property
    def size_name(self):
        return "official_case_index"

    @property
    def size_min(self):
        return 1

    @property
    def size_max(self):
        return self.count

    def __post_init__(self):
        if type(self.count) is not int or not 1 <= self.count <= MAX_INPUTS:
            raise ValueError("Invalid official corpus size")
        if type(self.interactive) is not bool:
            raise ValueError("Invalid interactive flag")
        if not re.fullmatch(r"[a-f0-9]{64}", self.archive_sha256):
            raise ValueError("Invalid official archive checksum")
        if self.folder != f".ac/official-inputs/{self.archive_sha256}":
            raise ValueError("Invalid official corpus location")

    def _path(self, case):
        if type(case) is not int or not 1 <= case <= self.count:
            raise ValueError(f"Official input index must be in 1..{self.count}")
        return self.root / self.folder / f"{case:04d}.txt"

    def strategy(self):
        return st.integers(1, self.count)

    def serialize(self, case, *, allow_out_of_range=False):
        path = self._path(case)
        if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"Missing/invalid official input {path}; run ac constraints again")
        return path.read_text(encoding="utf-8")

    def edge_cases(self):
        return [1] if self.count == 1 else [1, self.count]

    def benchmark_patterns(self, size, *, allow_out_of_range=False):
        self._path(size)
        if self.interactive:
            raise ValueError(
                "Interactive AHC input requires the official local tester, not ac bench"
            )
        return ("default",)

    def benchmark_case(self, size, seed, *, pattern="default", allow_out_of_range=False):
        if pattern not in self.benchmark_patterns(size):
            raise ValueError("Official corpus uses only the default input distribution")
        return size

    def default_benchmark_sizes(self):
        return list(range(1, self.count + 1))

    def to_toml(self):
        return (
            "version = 1\n\n[problem]\nurl = "
            + json.dumps(self.url)
            + "\n\n[official_corpus]\nsource_url = "
            + json.dumps(self.source_url)
            + "\narchive_sha256 = "
            + json.dumps(self.archive_sha256)
            + f"\ncount = {self.count}\ninteractive = {str(self.interactive).lower()}"
            + "\nfolder = "
            + json.dumps(self.folder)
            + "\n"
        )

    @classmethod
    def from_toml(cls, path: Path):
        with path.open("rb") as f:
            data = tomllib.load(f)
        if (
            set(data) != {"version", "problem", "official_corpus"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or set(data["problem"]) != {"url"}
            or set(data["official_corpus"])
            != {"source_url", "archive_sha256", "count", "interactive", "folder"}
        ):
            raise ValueError("Invalid official corpus configuration")
        return cls(path.parent, data["problem"]["url"], **data["official_corpus"])


def install_corpus(root: Path, url: str, source_url: str, data: bytes, interactive: bool):
    inputs, has_tester = unpack_inputs(data)
    digest = hashlib.sha256(data).hexdigest()
    folder = f".ac/official-inputs/{digest}"
    target = root / folder
    target.mkdir(parents=True, exist_ok=True)
    manifest = []
    for index, (filename, text) in enumerate(inputs, 1):
        path = target / f"{index:04d}.txt"
        path.write_text(text, encoding="utf-8")
        manifest.append(
            {
                "index": index,
                "original_name": filename,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    archive_path = root / ".ac" / f"official-tools-{digest}.zip"
    archive_path.write_bytes(data)
    model = OfficialCorpus(
        root, url, source_url, digest, len(inputs), interactive or has_tester, folder
    )
    logger.info("Installed {} official AHC inputs in {}", len(inputs), target)
    logger.info("Original official tools: {}", archive_path)
    if model.interactive:
        logger.warning("Interactive problem: use the official local tester; ac bench is disabled")
    else:
        logger.info("ac bench measures runtime/memory for official input indices, not scores")
    return model
