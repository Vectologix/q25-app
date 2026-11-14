"""Coffre Windows via WSL : preuve synthétique explicite, sans adoption de clés.

Le helper reste local et reçoit seulement des opérations fixes par stdin. Les
secrets reviennent sur stdout capturé en mémoire, jamais dans un argument/log.
"""

import base64
import binascii
import json
import os
from pathlib import Path
import platform
import select
import shutil
import subprocess
import time
from uuid import uuid4

from q25.core.documents import BackendError
from q25.core.keys import KeyReference
from q25.persistence.workspace import SyntheticWorkspace

MAX_PACKET_BYTES = 2048
REQUEST_TIMEOUT = 15.0
_STATUS_CODES = {
    "missing": "KEY_NOT_FOUND", "exists": "KEY_EXISTS",
    "unavailable": "KEYRING_UNAVAILABLE", "failure": "KEYRING_FAILURE",
    "cleanup_failed": "KEYRING_CLEANUP_FAILED",
}


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate field")
        value[key] = item
    return value


def _decode_packet(raw: bytes) -> dict:
    try:
        if type(raw) is not bytes or len(raw) > MAX_PACKET_BYTES:
            raise ValueError("packet")
        packet = json.loads(raw, object_pairs_hook=_unique_object)
        if type(packet) is not dict or type(packet.get("v")) is not int or packet["v"] != 1:
            raise ValueError("version")
        status = packet.get("status")
        if type(status) is not str:
            raise ValueError("status")
        if status != "ok":
            if status not in _STATUS_CODES or set(packet) != {"v", "status"}:
                raise ValueError("status")
            raise BackendError(_STATUS_CODES[status])
        event = packet.get("event")
        if type(event) is not str or event not in {"ready", "created", "loaded", "deleted", "closed"}:
            raise ValueError("event")
        expected = {"v", "status", "event"}
        if event in {"created", "loaded", "deleted"}:
            expected.add("id")
            if type(packet.get("id")) is not str:
                raise ValueError("reference")
            KeyReference(packet["id"])
        if event == "loaded":
            expected.add("material")
            if type(packet.get("material")) is not str or len(packet["material"]) != 44:
                raise ValueError("material")
        if set(packet) != expected:
            raise ValueError("fields")
        return packet
    except BackendError as error:
        if error.code == "KEY_REFERENCE_INVALID":
            raise BackendError("KEYRING_FAILURE") from None
        raise
    except (ValueError, TypeError, UnicodeError):
        raise BackendError("KEYRING_FAILURE") from None


def _windows_path(path: Path) -> str:
    try:
        process = subprocess.run(["wslpath", "-w", str(path)], capture_output=True,
                                 text=True, timeout=3, check=True)
        result = process.stdout.strip()
        if not result or "\n" in result or "\r" in result:
            raise ValueError("path")
        return result
    except (OSError, ValueError, subprocess.SubprocessError):
        raise BackendError("KEYRING_UNAVAILABLE") from None


class _WindowsBridge:
    def __init__(self, workspace: SyntheticWorkspace, session: str, *, allow_unsigned_helper=False):
        self.process = None
        self._buffer = b""
        if os.name != "posix" or "microsoft" not in platform.release().lower():
            raise BackendError("KEYRING_UNAVAILABLE")
        executable = shutil.which("powershell.exe")
        if executable is None:
            raise BackendError("KEYRING_UNAVAILABLE")
        script = Path(__file__).with_name("windows_credentials.ps1")
        command = [executable, "-NoLogo", "-NoProfile", "-NonInteractive"]
        if allow_unsigned_helper is True:
            # Exception du seul processus enfant, à autoriser distinctement.
            command += ["-ExecutionPolicy", "Bypass"]
        command += ["-File", _windows_path(script), "-SessionId", session,
                   "-TempDirectory", _windows_path(workspace.root)]
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL)
            if self._read()["event"] != "ready":
                raise BackendError("KEYRING_UNAVAILABLE")
        except (OSError, BackendError, KeyError):
            self.abort()
            raise BackendError("KEYRING_UNAVAILABLE") from None

    def _read(self):
        deadline = time.monotonic() + REQUEST_TIMEOUT
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or len(self._buffer) > MAX_PACKET_BYTES:
                raise BackendError("KEYRING_FAILURE")
            ready, _, _ = select.select([self.process.stdout], [], [], remaining)
            if not ready:
                raise BackendError("KEYRING_FAILURE")
            chunk = os.read(self.process.stdout.fileno(), MAX_PACKET_BYTES + 1)
            if not chunk:
                raise BackendError("KEYRING_FAILURE")
            self._buffer += chunk
        raw, self._buffer = self._buffer.split(b"\n", 1)
        return _decode_packet(raw)

    def request(self, operation: str, identifier: str | None = None):
        if operation not in {"create", "read", "delete", "close"}:
            raise BackendError("KEYRING_FAILURE")
        packet = {"v": 1, "op": operation}
        if operation != "close":
            KeyReference(identifier)
            packet["id"] = identifier
        try:
            self.process.stdin.write(json.dumps(packet).encode("ascii") + b"\n")
            self.process.stdin.flush()
            return self._read()
        except (OSError, ValueError):
            raise BackendError("KEYRING_FAILURE") from None

    def abort(self) -> bool:
        """EOF permet le finally du helper ; un arrêt forcé ne prouve pas le nettoyage."""
        if self.process is None:
            return True
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
            try:
                code = self.process.wait(timeout=5)
                return code == 0
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
                return False
        except (OSError, ValueError, subprocess.SubprocessError):
            return False
        finally:
            if self.process.stdout is not None:
                self.process.stdout.close()


class WindowsInteropKeyProvider:
    """Clés de test créées dans cette session seulement ; aucun usage produit."""

    def __init__(self, workspace: SyntheticWorkspace, *, allow_system_store: bool = False,
                 allow_unsigned_helper: bool = False):
        workspace.root
        if allow_system_store is not True:
            raise BackendError("KEYRING_CONSENT_REQUIRED")
        self._owned: set[KeyReference] = set()
        self._closed = False
        self._session_id = uuid4().hex
        self._bridge = _WindowsBridge(workspace, self._session_id,
                                      allow_unsigned_helper=allow_unsigned_helper)
        workspace.register(self.close)

    @property
    def active_references(self) -> tuple[KeyReference, ...]:
        # Identifiants techniques uniquement, utiles au constat d'un nettoyage incomplet.
        return tuple(sorted(self._owned, key=lambda reference: reference.identifier))

    @property
    def active_targets(self) -> tuple[str, ...]:
        """Noms techniques exacts pour récupération bornée, jamais les valeurs secrètes."""
        return tuple("Q25-TEST-2B2/" + self._session_id + "/" + ref.identifier
                     for ref in self.active_references)

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, *_):
        self.close()

    def _ensure_open(self):
        if self._closed:
            raise BackendError("KEYRING_CLOSED")

    def _ensure_owned(self, reference):
        self._ensure_open()
        if not isinstance(reference, KeyReference):
            raise BackendError("KEY_REFERENCE_INVALID")
        if reference not in self._owned:
            raise BackendError("KEY_NOT_FOUND")

    def _expect(self, operation, reference=None):
        packet = self._bridge.request(operation, None if reference is None else reference.identifier)
        expected = {"create": "created", "read": "loaded", "delete": "deleted", "close": "closed"}
        if (packet["event"] != expected[operation]
                or (reference is not None and packet["id"] != reference.identifier)):
            raise BackendError("KEYRING_FAILURE")
        return packet

    def create(self) -> KeyReference:
        self._ensure_open()
        reference = KeyReference(uuid4().hex)
        self._owned.add(reference)  # Inclure le candidat si la réponse se perd après écriture.
        try:
            self._expect("create", reference)
            return reference
        except BackendError:
            self.close()
            raise

    def load(self, reference: KeyReference) -> bytes:
        self._ensure_owned(reference)
        packet = self._expect("read", reference)
        try:
            key = base64.b64decode(packet["material"], validate=True)
        except (ValueError, binascii.Error):
            raise BackendError("KEYRING_FAILURE") from None
        if len(key) != 32:
            raise BackendError("KEYRING_FAILURE")
        return key

    def delete(self, reference: KeyReference):
        self._ensure_owned(reference)
        self._expect("delete", reference)
        self._owned.remove(reference)

    def close(self):
        if self._closed:
            return
        verified = False
        try:
            self._expect("close")
            verified = True
        finally:
            exited_cleanly = self._bridge.abort()
            self._closed = True
            if verified and exited_cleanly:
                self._owned.clear()
            else:
                raise BackendError("KEYRING_CLEANUP_FAILED") from None
