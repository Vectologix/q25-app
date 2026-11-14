"""Espaces éphémères créés et possédés par le harnais de tests."""

import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from typing import Callable

from q25.core.documents import BackendError


class SyntheticWorkspace:
    """Capacité explicite de test, pas une garantie que des octets sont fictifs.

    Aucune adoption de dossier existant ou import externe. L'appelant doit fournir
    exclusivement des fixtures inventées. Le constructeur n'ouvre aucun fichier.
    """

    def __init__(self):
        self._temporary = None
        self._root = None
        self._closers: list[Callable[[], None]] = []

    def __enter__(self):
        if self._temporary is not None:
            raise BackendError("WORKSPACE_CLOSED")
        self._temporary = TemporaryDirectory(prefix="q25-synthetic-")
        self._root = Path(self._temporary.name)
        return self

    @property
    def root(self) -> Path:
        if self._root is None:
            raise BackendError("WORKSPACE_CLOSED")
        return self._root

    def path(self, name: str) -> Path:
        if (type(name) is not str or not name or name in (".", "..")
                or "/" in name or "\\" in name or "\x00" in name):
            raise BackendError("PATH_INVALID")
        return self.root / name

    def check_path(self, path: Path, *, must_exist: bool = True) -> Path:
        path = Path(path)
        if not path.is_absolute() or path.parent != self.root or path.name in (".", ".."):
            raise BackendError("PATH_INVALID")
        try:
            status = path.lstat()
        except FileNotFoundError:
            if must_exist:
                raise BackendError("PATH_INVALID") from None
            return path
        except OSError:
            raise BackendError("PATH_INVALID") from None
        if not stat.S_ISREG(status.st_mode):
            raise BackendError("PATH_INVALID")
        return path

    def claim_file(self, path: Path) -> Path:
        path = self.check_path(path, must_exist=False)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        except FileExistsError:
            raise BackendError("DESTINATION_EXISTS") from None
        except OSError:
            raise BackendError("STORAGE_FAILURE") from None
        return path

    def write_fixture(self, name: str, data: bytes) -> Path:
        if type(data) is not bytes:
            raise ValueError("Une fixture doit être fournie en octets.")
        path = self.claim_file(self.path(name))
        try:
            with path.open("wb") as stream:
                stream.write(data)
        except OSError:
            raise BackendError("STORAGE_FAILURE") from None
        return path

    def register(self, close: Callable[[], None]):
        self.root  # Refuser une capacité fermée.
        self._closers.append(close)

    def __exit__(self, exception_type, *_):
        failure = False
        try:
            for close in reversed(self._closers):
                try:
                    close()
                except Exception:
                    failure = True
        finally:
            try:
                if self._temporary is not None:
                    try:
                        self._temporary.cleanup()
                    except OSError:
                        failure = True
            finally:
                self._root = None
        if failure and exception_type is None:
            raise BackendError("STORAGE_FAILURE")
