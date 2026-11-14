"""Essai Windows explicite, exclu des tests ordinaires et de la CI."""

import importlib.util
import logging
import os
from secrets import compare_digest
import socket
import unittest
from unittest.mock import patch

from q25.app.ingestion import IngestionService
from q25.core.documents import BackendError
from q25.persistence.keyring_windows import WindowsInteropKeyProvider
from q25.persistence.keyed import open_keyed_database,backup_keyed_database,restore_keyed_database
from q25.persistence.repository import DocumentRepository
from q25.persistence.workspace import SyntheticWorkspace


@unittest.skipUnless(os.environ.get("Q25_RUN_OS_KEYRING_TESTS")=="1" and importlib.util.find_spec("sqlcipher3"),
                     "essai coffre OS explicitement désactivé ou SQLCipher absent")
class WindowsKeyringTests(unittest.TestCase):
    def test_owned_key_roundtrip_and_confirmed_deletion(self):
        provider = None
        try:
            with SyntheticWorkspace() as workspace,self.assertNoLogs(level=logging.DEBUG), \
                 patch.object(socket,"socket",side_effect=AssertionError("réseau Python interdit")):
                with WindowsInteropKeyProvider(workspace,allow_system_store=True,
                                               allow_unsigned_helper=os.environ.get("Q25_ALLOW_UNSIGNED_TEST_HELPER")=="1") as provider:
                    reference=provider.create()
                    first=provider.load(reference);second=provider.load(reference)
                    self.assertTrue(compare_digest(first,second),"Relecture de clé incohérente.")
                    self.assertEqual(len(first),32)
                    del first,second
                    source=workspace.write_fixture("source.txt","PERSONNE_FICTIVE_COFFRE Québec".encode())
                    with open_keyed_database(workspace.path("state.db"),provider=provider,reference=reference,
                                             synthetic_workspace=workspace) as db:
                        result=IngestionService(db).ingest(source)
                        self.assertEqual(result.state,"completed")
                        expected=DocumentRepository(db).get_document(result.document_id)
                        backup=backup_keyed_database(db,workspace.path("backup.db"),provider=provider,reference=reference)
                        restored=restore_keyed_database(backup,workspace.path("restored.db"),workspace=workspace,
                                                       provider=provider,source_reference=reference,
                                                       destination_reference=reference)
                    with open_keyed_database(restored,provider=provider,reference=reference,
                                             synthetic_workspace=workspace) as db:
                        self.assertEqual(DocumentRepository(db).get_document(result.document_id),expected)
                    provider.delete(reference)  # Le helper confirme aussi l'absence via CredReadW.
                    self.assertEqual(provider.active_references,())
                    with self.assertRaises(BackendError) as error:provider.load(reference)
                    self.assertEqual(error.exception.code,"KEY_NOT_FOUND")
        finally:
            if provider is not None and provider.active_targets:
                # Noms techniques seulement, pour nettoyer les seules entrées de cet essai.
                self.fail("Nettoyage non confirmé des cibles DE TEST : " +
                          ", ".join(provider.active_targets))
