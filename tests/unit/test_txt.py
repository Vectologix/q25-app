from dataclasses import replace
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from q25.core.documents import BackendError
from q25.extraction.txt import TxtExtractor
from q25.persistence.workspace import SyntheticWorkspace


class TxtTests(unittest.TestCase):
    def test_exact_unicode_and_byte_provenance(self):
        cases = (b"", b"\xef\xbb\xbf", "Personne_FICTIVE_A\r\nQuébec e\u0301 🦊\nEnglish".encode(),
                 b"\xef\xbb\xbf" + "Élément\nAutre\r\n".encode(), b"x" * 20000)
        with SyntheticWorkspace() as workspace:
            for index, raw in enumerate(cases):
                with self.subTest(index=index):
                    source = workspace.write_fixture(f"source-{index}.txt", raw)
                    document = TxtExtractor(workspace).extract(source)
                    prefix = 3 if raw.startswith(b"\xef\xbb\xbf") else 0
                    text = raw[prefix:].decode("utf-8")
                    self.assertEqual(document.text, text)
                    self.assertEqual(document.sha256, hashlib.sha256(raw).hexdigest())
                    self.assertEqual(document.byte_size, len(raw))
                    self.assertEqual(document.has_bom, bool(prefix))
                    for element in document.elements:
                        self.assertEqual(text[element.start:element.end], element.text)
                        self.assertEqual(raw[element.byte_start:element.byte_end].decode(), element.text)
                    self.assertEqual(source.read_bytes(), raw)

    def test_invalid_formats_are_rejected_without_content_in_errors(self):
        cases = (b"\xffPRIVATE", b"PRIVATE\x00", b"%PDF-1.7 PRIVATE", b"PK\x03\x04PRIVATE",
                 b"\x89PNG\r\nPRIVATE", b"MZPRIVATE")
        with SyntheticWorkspace() as workspace:
            for index, raw in enumerate(cases):
                source = workspace.write_fixture(f"source-{index}.txt", raw)
                with self.subTest(index=index), self.assertRaises(BackendError) as error:
                    TxtExtractor(workspace).extract(source)
                self.assertNotIn("PRIVATE", str(error.exception))
                self.assertNotIn(str(source), str(error.exception))

    def test_size_limit_boundary(self):
        with SyntheticWorkspace() as workspace:
            source = workspace.write_fixture("source.txt", b"12345")
            self.assertEqual(TxtExtractor(workspace, max_bytes=5).extract(source).text, "12345")
            with self.assertRaises(BackendError) as error:
                TxtExtractor(workspace, max_bytes=4).extract(source)
            self.assertEqual(error.exception.code, "TXT_TOO_LARGE")

    def test_permissions_and_external_paths_fail(self):
        with SyntheticWorkspace() as workspace:
            source = workspace.write_fixture("source.txt", b"fictif")
            with patch.object(Path, "open", side_effect=PermissionError("PRIVATE")):
                with self.assertRaises(BackendError) as error:
                    TxtExtractor(workspace).extract(source)
            self.assertNotIn("PRIVATE", str(error.exception))
            with self.assertRaises(BackendError):
                TxtExtractor(workspace).extract(Path("/outside-source.txt"))

    def test_forged_provenance_is_rejected(self):
        with SyntheticWorkspace() as workspace:
            source = workspace.write_fixture("source.txt", "Québec".encode())
            doc = TxtExtractor(workspace).extract(source)
            with self.assertRaises(BackendError):
                replace(doc, byte_size=1)
            with self.assertRaises(BackendError):
                replace(doc, sha256="a" * 64)
            with self.assertRaises(BackendError):
                replace(doc, elements=(replace(doc.elements[0], start=1),))

    def test_closed_workspace_cannot_be_used(self):
        with SyntheticWorkspace() as workspace:
            source = workspace.write_fixture("source.txt", b"fictif")
        with self.assertRaises(BackendError):
            TxtExtractor(workspace).extract(source)

    def test_many_lines_and_chunk_boundaries_remain_bounded_and_exact(self):
        cases = (b"\n" * 200000, ("a" * 65535 + "\r\nÉe\u0301🦊").encode())
        with SyntheticWorkspace() as workspace:
            for index, raw in enumerate(cases):
                source = workspace.write_fixture(f"chunk-{index}.txt", raw)
                doc = TxtExtractor(workspace).extract(source)
                self.assertLessEqual(len(doc.elements), 16)
                self.assertEqual(doc.text.encode(), raw)
                for element in doc.elements:
                    self.assertEqual(raw[element.byte_start:element.byte_end].decode(), element.text)
