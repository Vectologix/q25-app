import base64
import io
import json
import os
from pathlib import Path
from secrets import compare_digest, token_bytes
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from q25.core.documents import BackendError
from q25.core.keys import KeyReference
from q25.persistence.keyring_windows import (
    MAX_PACKET_BYTES, WindowsInteropKeyProvider, _WindowsBridge, _decode_packet,
)
from q25.persistence.workspace import SyntheticWorkspace


class FakeBridge:
    """Double de test seulement ; jamais fournisseur de repli du backend."""
    instances = []

    def __init__(self, *args, **kwargs):
        self.values = {}
        self.calls = []
        self.aborted = False
        self.instances.append(self)

    def request(self, operation, identifier=None):
        self.calls.append((operation, identifier))
        event = {"create": "created", "read": "loaded", "delete": "deleted", "close": "closed"}[operation]
        packet = {"v": 1, "status": "ok", "event": event}
        if identifier is not None:
            packet["id"] = identifier
        if operation == "create":
            if identifier in self.values:
                raise BackendError("KEY_EXISTS")
            self.values[identifier] = token_bytes(32)
        elif operation == "read":
            if identifier not in self.values:
                raise BackendError("KEY_NOT_FOUND")
            packet["material"] = base64.b64encode(self.values[identifier]).decode()
        elif operation == "delete":
            if identifier not in self.values:
                raise BackendError("KEY_NOT_FOUND")
            del self.values[identifier]
        elif operation == "close":
            self.values.clear()
        return _decode_packet(json.dumps(packet).encode())

    def abort(self):
        self.aborted = True
        self.values.clear()
        return True


class KeyReferenceTests(unittest.TestCase):
    def test_invalid_reference_never_copies_input_to_error(self):
        for value in (None, True, b"x" * 32, "PRIVATE/path", "a" * 31, "A" * 32, "g" * 32):
            with self.subTest(kind=type(value).__name__), self.assertRaises(BackendError) as error:
                KeyReference(value)
            self.assertEqual(error.exception.code, "KEY_REFERENCE_INVALID")
            self.assertNotIn("PRIVATE", str(error.exception))


class PacketTests(unittest.TestCase):
    def test_malformed_oversize_duplicate_or_secret_error_payload_is_neutral(self):
        packets = [b'PRIVATE',b'[]',b'{}',b'x'*(MAX_PACKET_BYTES+1),
                   b'{"v":1,"v":1,"status":"ok","event":"ready"}',
                   b'{"v":true,"status":"ok","event":"ready"}',
                   b'{"v":1,"status":"PRIVATE"}',
                   b'{"v":1,"status":"failure","message":"PRIVATE"}',
                   b'{"v":1,"status":"ok","event":"ready","extra":"PRIVATE"}',
                   b'{"v":1,"status":"ok","event":"loaded","id":"PRIVATE","material":"x"}',
                   b'{"v":1,"status":"ok","event":"created","id":false}']
        for raw in packets:
            with self.subTest(length=len(raw)), self.assertRaises(BackendError) as error:
                _decode_packet(raw)
            self.assertEqual(error.exception.code,"KEYRING_FAILURE")
            self.assertNotIn("PRIVATE",str(error.exception))

    def test_closed_native_error_codes_are_mapped(self):
        for status,expected in [("missing","KEY_NOT_FOUND"),("exists","KEY_EXISTS"),
                                ("unavailable","KEYRING_UNAVAILABLE"),
                                ("cleanup_failed","KEYRING_CLEANUP_FAILED")]:
            with self.subTest(status=status), self.assertRaises(BackendError) as error:
                _decode_packet(json.dumps({"v":1,"status":status}).encode())
            self.assertEqual(error.exception.code,expected)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("q25.persistence.keyring_windows._WindowsBridge", FakeBridge)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_opt_in_required_before_any_bridge(self):
        before=len(FakeBridge.instances)
        with SyntheticWorkspace() as workspace:
            for consent in (False,1,"yes"):
                with self.subTest(consent=consent),self.assertRaises(BackendError) as error:
                    WindowsInteropKeyProvider(workspace,allow_system_store=consent)
                self.assertEqual(error.exception.code,"KEYRING_CONSENT_REQUIRED")
        self.assertEqual(len(FakeBridge.instances),before)

    def test_create_load_delete_and_cleanup_without_secret_repr(self):
        with SyntheticWorkspace() as workspace:
            with WindowsInteropKeyProvider(workspace,allow_system_store=True) as provider:
                reference=provider.create()
                first=provider.load(reference); second=provider.load(reference)
                self.assertEqual(len(first),32)
                self.assertTrue(compare_digest(first,second))
                self.assertNotIn(first.hex(),repr(reference))
                self.assertNotIn(first.hex(),repr(provider))
                bridge=provider._bridge
                provider.delete(reference)
                self.assertFalse(bridge.values)
                with self.assertRaises(BackendError) as error:
                    provider.load(reference)
                self.assertEqual(error.exception.code,"KEY_NOT_FOUND")
                provider.create()  # Fermeture doit aussi nettoyer les clés restantes.
            self.assertFalse(bridge.values)
            self.assertEqual(provider.active_references,())
            self.assertTrue(bridge.aborted)
            before=len(bridge.calls);provider.close()
            self.assertEqual(len(bridge.calls),before)

    def test_foreign_references_are_rejected_without_ipc(self):
        with SyntheticWorkspace() as workspace:
            with WindowsInteropKeyProvider(workspace,allow_system_store=True) as first, \
                 WindowsInteropKeyProvider(workspace,allow_system_store=True) as second:
                reference=first.create()
                for foreign in (reference,KeyReference(uuid4().hex)):
                    before=len(second._bridge.calls)
                    for action in (second.load,second.delete):
                        with self.assertRaises(BackendError) as error:
                            action(foreign)
                        self.assertEqual(error.exception.code,"KEY_NOT_FOUND")
                    self.assertEqual(len(second._bridge.calls),before)

    def test_native_unavailable_is_not_replaced_with_memory_or_file(self):
        with SyntheticWorkspace() as workspace:
            with WindowsInteropKeyProvider(workspace,allow_system_store=True) as provider:
                reference=provider.create()
                original=provider._bridge.request
                def unavailable(op,identifier=None):
                    if op=="read":raise BackendError("KEYRING_UNAVAILABLE")
                    return original(op,identifier)
                with patch.object(provider._bridge,"request",side_effect=unavailable):
                    with self.assertRaises(BackendError) as error:provider.load(reference)
                self.assertEqual(error.exception.code,"KEYRING_UNAVAILABLE")
                self.assertEqual(list(workspace.root.iterdir()),[])

    def test_mismatched_reply_reference_is_refused(self):
        with SyntheticWorkspace() as workspace:
            with WindowsInteropKeyProvider(workspace,allow_system_store=True) as provider:
                reference=provider.create()
                packet={"v":1,"status":"ok","event":"loaded","id":uuid4().hex,
                        "material":base64.b64encode(token_bytes(32)).decode()}
                with patch.object(provider._bridge,"request",return_value=packet):
                    with self.assertRaises(BackendError) as error:provider.load(reference)
                self.assertEqual(error.exception.code,"KEYRING_FAILURE")

    def test_invalid_loaded_material_is_refused(self):
        with SyntheticWorkspace() as workspace:
            with WindowsInteropKeyProvider(workspace,allow_system_store=True) as provider:
                reference=provider.create()
                for material in ("!"*44,base64.b64encode(b"x"*31).decode()):
                    packet={"v":1,"status":"ok","event":"loaded","id":reference.identifier,
                            "material":material}
                    with patch.object(provider._bridge,"request",return_value=packet):
                        with self.assertRaises(BackendError) as error:provider.load(reference)
                    self.assertEqual(error.exception.code,"KEYRING_FAILURE")

    def test_cleanup_failure_is_visible_and_references_retained(self):
        with SyntheticWorkspace() as workspace:
            provider=WindowsInteropKeyProvider(workspace,allow_system_store=True)
            reference=provider.create()
            original=provider._bridge.request
            def failure(op,identifier=None):
                if op=="close":raise BackendError("KEYRING_FAILURE")
                return original(op,identifier)
            with patch.object(provider._bridge,"request",side_effect=failure):
                with self.assertRaises(BackendError) as error:provider.close()
            self.assertEqual(error.exception.code,"KEYRING_CLEANUP_FAILED")
            self.assertEqual(provider.active_references,(reference,))
            self.assertEqual(provider.active_targets,
                             ("Q25-TEST-2B2/"+provider._session_id+"/"+reference.identifier,))
            self.assertTrue(provider._bridge.aborted)

    def test_create_failure_triggers_owned_session_cleanup(self):
        with SyntheticWorkspace() as workspace:
            provider=WindowsInteropKeyProvider(workspace,allow_system_store=True)
            original=provider._bridge.request
            def failure(op,identifier=None):
                if op=="create":
                    original(op,identifier)
                    raise BackendError("KEYRING_FAILURE")
                return original(op,identifier)
            with patch.object(provider._bridge,"request",side_effect=failure):
                with self.assertRaises(BackendError):provider.create()
            self.assertTrue(provider._bridge.aborted)
            self.assertEqual(provider.active_references,())

    def test_closed_provider_cannot_create_or_load(self):
        with SyntheticWorkspace() as workspace:
            provider=WindowsInteropKeyProvider(workspace,allow_system_store=True)
            reference=provider.create();provider.close()
            for action in (provider.create,lambda:provider.load(reference)):
                with self.assertRaises(BackendError) as error:action()
                self.assertEqual(error.exception.code,"KEYRING_CLOSED")


@unittest.skipUnless(os.name == "posix", "transport de pipes WSL/POSIX seulement")
class TransportTests(unittest.TestCase):
    def test_pipe_packet_and_real_timeout_are_bounded(self):
        for payload in (b'{"v":1,"status":"ok","event":"ready"}\n',None):
            with self.subTest(has_packet=payload is not None):
                read_fd,write_fd=os.pipe()
                with os.fdopen(read_fd,'rb') as reader,os.fdopen(write_fd,'wb') as writer:
                    bridge=object.__new__(_WindowsBridge)
                    bridge.process=SimpleNamespace(stdout=reader);bridge._buffer=b''
                    if payload is not None:
                        writer.write(payload);writer.flush()
                        self.assertEqual(bridge._read()["event"],"ready")
                    else:
                        with patch("q25.persistence.keyring_windows.REQUEST_TIMEOUT",0.02):
                            with self.assertRaises(BackendError) as error:bridge._read()
                        self.assertEqual(error.exception.code,"KEYRING_FAILURE")

    def test_execution_policy_is_unchanged_by_default(self):
        for opt_in in (False,True):
            with self.subTest(opt_in=opt_in),SyntheticWorkspace() as workspace:
                read_fd,write_fd=os.pipe()
                with os.fdopen(read_fd,'rb') as reader,os.fdopen(write_fd,'wb') as writer:
                    writer.write(b'{"v":1,"status":"ok","event":"ready"}\n');writer.flush()
                    process=SimpleNamespace(stdin=io.BytesIO(),stdout=reader,wait=lambda **kw:0)
                    with patch("q25.persistence.keyring_windows.platform.release",return_value="microsoft-standard-WSL2"), \
                         patch("q25.persistence.keyring_windows.shutil.which",return_value="powershell.exe"), \
                         patch("q25.persistence.keyring_windows._windows_path",side_effect=lambda p:str(p)), \
                         patch("q25.persistence.keyring_windows.subprocess.Popen",return_value=process) as launcher:
                        bridge=_WindowsBridge(workspace,uuid4().hex,allow_unsigned_helper=opt_in)
                        command=launcher.call_args.args[0]
                        self.assertEqual("-ExecutionPolicy" in command,opt_in)
                        self.assertTrue(bridge.abort())
