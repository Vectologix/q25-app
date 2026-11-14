"""Le double en mémoire ne constitue jamais un repli de production."""

import importlib.util
from secrets import token_bytes
import socket
import unittest
from unittest.mock import patch
from uuid import uuid4

from q25.app.ingestion import IngestionService
from q25.core.documents import BackendError
from q25.core.keys import KeyReference
from q25.persistence.database import open_database
from q25.persistence.keyed import open_keyed_database, backup_keyed_database, restore_keyed_database
from q25.persistence.repository import DocumentRepository
from q25.persistence.workspace import SyntheticWorkspace


class MemoryKeys:
    def __init__(self):self.values={}
    def create(self):
        reference=KeyReference(uuid4().hex);self.values[reference]=token_bytes(32)
        return reference
    def load(self,reference):
        if reference not in self.values:raise BackendError("KEY_NOT_FOUND")
        return self.values[reference]
    def delete(self,reference):del self.values[reference]


class KeyedPolicyTests(unittest.TestCase):
    def test_workspace_required_before_provider_access(self):
        with SyntheticWorkspace() as workspace:
            provider=MemoryKeys()
            with patch.object(provider,"load") as loader:
                with self.assertRaises(BackendError) as error:
                    open_keyed_database(workspace.path("state.db"),provider=provider,
                                        reference=KeyReference(uuid4().hex))
                self.assertEqual(error.exception.code,"ENCRYPTION_REQUIRED")
                loader.assert_not_called()
            self.assertEqual(list(workspace.root.iterdir()),[])

    def test_missing_reference_never_creates_or_falls_back(self):
        with SyntheticWorkspace() as workspace:
            provider=MemoryKeys()
            with self.assertRaises(BackendError) as error:
                open_keyed_database(workspace.path("state.db"),provider=provider,
                                    reference=KeyReference(uuid4().hex),synthetic_workspace=workspace)
            self.assertEqual(error.exception.code,"KEY_NOT_FOUND")
            self.assertEqual(list(workspace.root.iterdir()),[])

    def test_provider_exception_and_invalid_material_are_neutral_before_creation(self):
        with SyntheticWorkspace() as workspace:
            provider=MemoryKeys();reference=provider.create()
            for value in [RuntimeError("PRIVATE SECRET PATH"),b"bad"]:
                with self.subTest(kind=type(value).__name__):
                    kwargs={"side_effect":value} if isinstance(value,Exception) else {"return_value":value}
                    with patch.object(provider,"load",**kwargs):
                        with self.assertRaises(BackendError) as error:
                            open_keyed_database(workspace.path("state.db"),provider=provider,
                                                reference=reference,synthetic_workspace=workspace)
                    self.assertNotIn("PRIVATE",str(error.exception))
                    self.assertEqual(list(workspace.root.iterdir()),[])

    def test_foreign_path_and_plain_backup_refused_before_loading_key(self):
        with SyntheticWorkspace() as workspace,open_database(synthetic_workspace=workspace) as plain:
            provider=MemoryKeys();reference=provider.create()
            with patch.object(provider,"load") as loader:
                with self.assertRaises(BackendError):
                    open_keyed_database(workspace.root.parent/"escape.db",provider=provider,
                                        reference=reference,synthetic_workspace=workspace)
                with self.assertRaises(BackendError):
                    backup_keyed_database(plain,workspace.path("backup.db"),provider=provider,reference=reference)
                loader.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec("sqlcipher3"),"binding SQLCipher facultatif absent")
class KeyedCipherTests(unittest.TestCase):
    def test_document_backup_restore_with_three_key_references_offline(self):
        provider=MemoryKeys();source_ref=provider.create();backup_ref=provider.create();restored_ref=provider.create()
        with SyntheticWorkspace() as workspace,patch.object(socket,"socket",side_effect=AssertionError("réseau interdit")):
            raw="PERSONNE_FICTIVE_2B2 Québec\r\nDonnées inventées".encode()
            source=workspace.write_fixture("source.txt",raw)
            with open_keyed_database(workspace.path("state.db"),provider=provider,reference=source_ref,
                                     synthetic_workspace=workspace) as database:
                result=IngestionService(database).ingest(source)
                self.assertEqual(result.state,"completed")
                expected=DocumentRepository(database).get_document(result.document_id)
                backup=backup_keyed_database(database,workspace.path("backup.db"),provider=provider,reference=backup_ref)
                restored=restore_keyed_database(backup,workspace.path("restored.db"),workspace=workspace,
                                                provider=provider,source_reference=backup_ref,
                                                destination_reference=restored_ref)
            for path,reference in [(backup,backup_ref),(restored,restored_ref)]:
                self.assertNotIn(raw,path.read_bytes())
                with open_keyed_database(path,provider=provider,reference=reference,synthetic_workspace=workspace) as db:
                    self.assertEqual(DocumentRepository(db).get_document(result.document_id),expected)
            provider.delete(source_ref)
            before=workspace.path("state.db").read_bytes()
            with self.assertRaises(BackendError) as error:
                open_keyed_database(workspace.path("state.db"),provider=provider,reference=source_ref,
                                    synthetic_workspace=workspace)
            self.assertEqual(error.exception.code,"KEY_NOT_FOUND")
            self.assertEqual(workspace.path("state.db").read_bytes(),before)

    def test_missing_restore_key_refused_without_destination_or_overwrite(self):
        provider=MemoryKeys();reference=provider.create()
        with SyntheticWorkspace() as workspace:
            with open_keyed_database(workspace.path("state.db"),provider=provider,reference=reference,
                                     synthetic_workspace=workspace) as db:
                source=workspace.write_fixture("source.txt",b"PERSONNE_FICTIVE")
                IngestionService(db).ingest(source)
                backup=backup_keyed_database(db,workspace.path("backup.db"),provider=provider,reference=reference)
            before=backup.read_bytes()
            with self.assertRaises(BackendError):
                restore_keyed_database(backup,workspace.path("target.db"),workspace=workspace,
                                      provider=provider,source_reference=reference,
                                      destination_reference=KeyReference(uuid4().hex))
            self.assertFalse(workspace.path("target.db").exists())
            self.assertEqual(backup.read_bytes(),before)
