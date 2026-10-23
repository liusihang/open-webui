"""Bounded input admission and a disk-backed chunk spool; no DB or app imports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


class AdmissionError(ValueError):
    """The original is retained, but this input is not admitted to document indexing."""


@dataclass(frozen=True)
class AdmissionLimits:
    max_text_bytes: int = 16 * 1024 * 1024
    max_chunks: int = 20_000
    max_spool_bytes: int = 128 * 1024 * 1024
    max_assets: int = 500
    max_asset_bytes: int = 128 * 1024 * 1024

    @classmethod
    def from_env(cls):
        values = {}
        for name in cls.__dataclass_fields__:
            value = int(os.getenv('INDEXING_' + name.upper(), getattr(cls(), name)))
            if value <= 0:
                raise ValueError('indexing admission limits must be positive')
            values[name] = value
        return cls(**values)


def inspect_source(stream, *, size):
    """Sample a seekable original before any expensive parsing. Never trusts its name.

    Reject only a large, predominantly rectangular numeric payload, not papers
    containing numbers, formulas, small tables or code with dimension comments.
    """
    position = stream.tell()
    try:
        samples = []
        for offset in sorted({0, max(0, size // 2 - 32768), max(0, size - 65536)}):
            stream.seek(offset)
            samples.append(stream.read(65536).decode('utf-8', errors='replace'))
        if size < 8 * 1024 * 1024:
            return
        lines = '\n'.join(samples).splitlines()
        rows = []
        for line in lines:
            cells = re.split(r'[\s,;\t]+', line.strip())
            if len(cells) < 16:
                continue
            numeric = sum(bool(re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', c)) for c in cells)
            if numeric >= 0.95 * len(cells):
                rows.append(len(cells))
        if len(rows) >= 12 and len(rows) >= 0.75 * max(1, len(lines)):
            raise AdmissionError(
                'Large numeric matrix/data array: original saved, automatic document indexing refused. '
                'Use the terminal for scientific data processing.'
            )
    finally:
        stream.seek(position)


class ChunkSpool:
    """Admit the complete chunk stream before any body/vector write, then batch it.

    Only one bounded chunk and one bounded embedding batch need be resident.
    Temp storage belongs to the worker's quota, not the persistent upload volume.
    """

    def __init__(self, root: Path, limits: AdmissionLimits):
        self.root, self.limits = root, limits
        self.count = self.text_bytes = self.spool_bytes = 0
        self.digest = None
        self.path = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix='chunks-', suffix='.jsonl', dir=self.root)
        os.close(fd)
        self.path = Path(path)
        return self

    def __exit__(self, *_):
        if self.path is not None:
            self.path.unlink(missing_ok=True)

    def write(self, chunks, *, allow_empty=False):
        digest = hashlib.sha256()
        with self.path.open('wb') as stream:
            for chunk in chunks:
                if not isinstance(chunk.get('text'), str):
                    raise AdmissionError('invalid document text')
                if not chunk['text'].strip():
                    continue
                self.count += 1
                self.text_bytes += len(chunk['text'].encode('utf-8'))
                if self.count > self.limits.max_chunks or self.text_bytes > self.limits.max_text_bytes:
                    raise AdmissionError('document exceeds text/chunk indexing budget; original retained')
                row = json.dumps(chunk, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
                self.spool_bytes += len(row)
                if self.spool_bytes > self.limits.max_spool_bytes:
                    raise AdmissionError('document exceeds temporary storage budget; original retained')
                stream.write(row)
                digest.update(row)
        if not self.count and not allow_empty:
            raise AdmissionError('empty document')
        self.digest = digest.hexdigest()

    def batches(self, size):
        if not 1 <= size <= 64:
            raise ValueError('indexing batches must contain 1..64 chunks')
        batch = []
        with self.path.open('rb') as stream:
            for line in stream:
                batch.append(json.loads(line))
                if len(batch) == size:
                    yield batch
                    batch = []
        if batch:
            yield batch


def stream_hash(stream):
    position = stream.tell()
    digest, size = hashlib.sha256(), 0
    try:
        stream.seek(0)
        while block := stream.read(1024 * 1024):
            digest.update(block)
            size += len(block)
        return digest.hexdigest(), size
    finally:
        stream.seek(position)
