"""Where the corpus comes from: public datasets with expert labels, each pinned to a revision and a checksum, each with
its licence. Fetched into data/corpus/raw/<name>/ (never committed: the texts are the publishers', redistributable
under their licences but not ours to vendor), verified, and read from the archive without extracting it."""
from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Source:
    name: str
    domain: str
    url: str
    file: str
    sha256: str
    licence: str
    citation: str


SOURCES = [
    Source("cuad", "legal",
           "https://github.com/The-Atticus-Project/cuad/raw/7aad649183846688381a147eeb3601aff590fe4a/data.zip",
           "data.zip", "f8161d18bea4e9c05e78fa6dda61c19c846fb8087ea969c172753bc2f45b999a", "CC BY 4.0",
           "Hendrycks, Burns, Chen and Ball, CUAD: An Expert-Annotated NLP Dataset for Legal Contract Review, "
           "NeurIPS 2021 Datasets and Benchmarks"),
    Source("contractnli", "legal", "https://stanfordnlp.github.io/contract-nli/resources/contract-nli.zip",
           "contract-nli.zip", "e03fc77bbf8b53e2976a250e81d8a294bc3d5e5fb014521e477dee9340d6287b", "CC BY 4.0",
           "Koreeda and Manning, ContractNLI: A Dataset for Document-level Natural Language Inference for "
           "Contracts, Findings of EMNLP 2021"),
    Source("tatqa", "finance",
           "https://raw.githubusercontent.com/NExTplusplus/TAT-QA/870accc41953dcde885aabeb963d94aabdc0fbc3/"
           "dataset_raw/tatqa_dataset_dev.json", "tatqa_dataset_dev.json",
           "8da095a819af6db3c14877c6df2d4d29960e41d1a63dd1fa853507bd2a616af5", "MIT",
           "Zhu, Lei, Huang, Wang, Zhang, Lv, Feng and Chua, TAT-QA: A Question Answering Benchmark on a Hybrid of "
           "Tabular and Textual Content in Finance, ACL 2021"),
    Source("pubmedqa", "biomedical",
           "https://raw.githubusercontent.com/pubmedqa/pubmedqa/1cbae8e92f72f20c8d3747cbb3bf5bc53554d997/data/"
           "ori_pqal.json", "ori_pqal.json", "8b3276be8942ebbd77f3ddcda12c1749bf0e490045a736fd8438ee40cf37a41d",
           "MIT", "Jin, Dhingra, Liu, Cohen and Lu, PubMedQA: A Dataset for Biomedical Research Question Answering, "
           "EMNLP 2019"),
    Source("pubmedqa-test", "biomedical",
           "https://raw.githubusercontent.com/pubmedqa/pubmedqa/1cbae8e92f72f20c8d3747cbb3bf5bc53554d997/data/"
           "test_ground_truth.json", "test_ground_truth.json",
           "939fe566f09017d13b1ca64d2ddfee0bc2374b366048152997669cccedc44d51", "MIT",
           "PubMedQA's official test split (500 of the 1,000 expert-labelled questions)"),
    Source("qasper", "science", "https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz",
           "qasper-train-dev-v0.3.tgz", "a28fdf966db827bcee3d873107d6b6669864fb7ca8fbf73a192f5e39191bdb5a",
           "CC BY 4.0", "Dasigi, Lo, Beltagy, Cohan, Smith and Gardner, A Dataset of Information-Seeking Questions "
           "and Answers Anchored in Research Papers, NAACL 2021"),
]
BY_NAME = {s.name: s for s in SOURCES}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def path_of(root: Path, s: Source) -> Path:
    return Path(root) / "raw" / s.name.split("-")[0] / s.file


def fetch(root: str | Path, only: list[str] | None = None) -> list[tuple[Source, Path, bool]]:
    """Download what is missing and verify every file against its checksum. Returns (source, path, downloaded)."""
    out = []
    for s in SOURCES:
        if only and s.name.split("-")[0] not in only:
            continue
        path = path_of(Path(root), s)
        downloaded = False
        if not path.exists() or sha256(path) != s.sha256:
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_suffix(path.suffix + ".part")
            with urllib.request.urlopen(s.url, timeout=300) as r, part.open("wb") as f:     # noqa: S310 (pinned)
                for chunk in iter(lambda: r.read(1 << 20), b""):
                    f.write(chunk)
            if sha256(part) != s.sha256:
                part.unlink()
                raise ValueError(f"{s.name}: the download does not match its pinned checksum")
            part.replace(path)
            downloaded = True
        out.append((s, path, downloaded))
    return out
