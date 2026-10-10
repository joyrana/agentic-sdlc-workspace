"""Object storage abstraction. Local backend is content-addressed by sha256."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentic_sdlc.core.hashing import sha256_bytes
from agentic_sdlc.core.settings import get_settings


class ObjectStorage(Protocol):
    def put(self, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...


class LocalObjectStorage:
    """Writes objects to <root>/<aa>/<sha256>. Keys are the hash; callers never supply paths."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("invalid object key")
        return self.root / key[:2] / key

    def put(self, data: bytes) -> str:
        key = sha256_bytes(data)
        path = self._path(key)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.chmod(0o600)
            tmp.replace(path)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


def get_storage() -> ObjectStorage:
    return LocalObjectStorage(get_settings().objects_dir)
