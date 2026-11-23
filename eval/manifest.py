"""Manifest ve tahmin JSONL yükleyicileri."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ManifestRow:
    id: str
    image: Path
    doc_type: str
    right: dict | None
    left: dict | None


@dataclass
class Prediction:
    id: str
    response: dict | None
    error: str | None


def _jsonl(path: Path):
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_manifest(path: Path) -> list[ManifestRow]:
    path = Path(path)
    return [
        ManifestRow(
            id=d["id"],
            image=path.parent / d["image"],
            doc_type=d["doc_type"],
            right=d.get("right"),
            left=d.get("left"),
        )
        for d in _jsonl(path)
    ]


def load_predictions(path: Path) -> list[Prediction]:
    return [
        Prediction(id=d["id"], response=d.get("response"), error=d.get("error"))
        for d in _jsonl(Path(path))
    ]
