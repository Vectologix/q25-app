from dataclasses import FrozenInstanceError, replace
import hashlib
import unittest

from q25.core.documents import BackendError, DocumentElement, ExtractedDocument
from q25.core.provenance import (
    CorpusReference, CorpusSnapshot, ResolvedCitation, RevisionReference, SourceSpan,
)


def document(text="PERSONNE_FICTIVE_A : Québec e\u0301 🦊\r\nFin", *, bom=False,
             document_id="a" * 32, split=None):
    parts = (text,) if split is None else (text[:split], text[split:])
    parts = tuple(part for part in parts if part)
    char_pos, byte_pos = 0, 3 if bom else 0
    elements = []
    for ordinal, part in enumerate(parts):
        end, byte_end = char_pos + len(part), byte_pos + len(part.encode())
        elements.append(DocumentElement(ordinal, char_pos, end, byte_pos, byte_end, part))
        char_pos, byte_pos = end, byte_end
    raw = (b"\xef\xbb\xbf" if bom else b"") + text.encode()
    return ExtractedDocument(document_id, hashlib.sha256(raw).hexdigest(), len(raw), bom, tuple(elements))


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.doc = document(bom=True)
        self.corpus = CorpusReference("b" * 32)
        self.revision = RevisionReference.from_document(self.doc)
        self.snapshot = CorpusSnapshot(self.corpus, (self.doc,))
        self.span = self.snapshot.anchor(self.revision, 0, len(self.doc.text))

    def assert_code(self, code, action):
        with self.assertRaises(BackendError) as error:
            action()
        self.assertEqual(error.exception.code, code)
        self.assertNotIn("PERSONNE_FICTIVE_A", str(error.exception))

    def test_identifiers_and_hashes_are_strict(self):
        for value in (None, 1, True, "", "a" * 31, "A" * 32, "../PERSONNE_FICTIVE_A"):
            with self.subTest(value=value):
                self.assert_code("PROVENANCE_INVALID", lambda: CorpusReference(value))
                self.assert_code("PROVENANCE_INVALID", lambda: RevisionReference(value, self.doc.sha256))
        for sha in (None, 1, "a" * 63, "A" * 64, "g" * 64, "PERSONNE_FICTIVE_A"):
            with self.subTest(sha=sha):
                self.assert_code("PROVENANCE_INVALID", lambda: RevisionReference(self.doc.document_id, sha))
        self.assert_code("PROVENANCE_INVALID", lambda: RevisionReference.from_document("PERSONNE_FICTIVE_A"))

    def test_containers_and_reference_types_are_strict(self):
        for corpus, docs in (("b" * 32, (self.doc,)), (self.corpus, [self.doc]),
                             (self.corpus, ("PERSONNE_FICTIVE_A",)), (self.corpus, None)):
            with self.subTest():
                self.assert_code("PROVENANCE_INVALID", lambda: CorpusSnapshot(corpus, docs))
        self.assert_code("PROVENANCE_INVALID", lambda: replace(self.span, corpus="b" * 32))
        self.assert_code("PROVENANCE_INVALID", lambda: replace(self.span, revision=self.doc))
        self.assert_code("PROVENANCE_INVALID", lambda: self.snapshot.anchor(self.doc, 0, 1))
        self.assert_code("PROVENANCE_INVALID", lambda: self.snapshot.resolve("PERSONNE_FICTIVE_A"))

    def test_duplicate_document_ids_rejected_even_if_revisions_differ(self):
        for other in (self.doc, document("autre source")):
            with self.subTest():
                self.assert_code("PROVENANCE_INVALID", lambda: CorpusSnapshot(self.corpus, (self.doc, other)))

    def test_full_source_matches_raw_bytes_and_excludes_bom(self):
        result = self.snapshot.resolve(self.span, expected_text=self.doc.text)
        self.assertEqual(result.text, self.doc.text)
        self.assertEqual((result.span.byte_start, result.span.byte_end), (3, self.doc.byte_size))
        self.assertEqual(result.span.revision, self.revision)

    def test_every_unicode_range_preserves_characters_and_utf8_offsets(self):
        text = "Ée\u0301🦊\r\nZ"
        for bom in (False, True):
            doc = document(text, bom=bom)
            snap = CorpusSnapshot(self.corpus, (doc,))
            revision = RevisionReference.from_document(doc)
            raw = (b"\xef\xbb\xbf" if bom else b"") + text.encode()
            for start in range(len(text)):
                for end in range(start + 1, len(text) + 1):
                    with self.subTest(bom=bom, start=start, end=end):
                        span = snap.anchor(revision, start, end)
                        result = snap.resolve(span, expected_text=text[start:end])
                        self.assertEqual(result.text, raw[span.byte_start:span.byte_end].decode())
                        self.assertEqual(span.start, start)
                        self.assertEqual(span.end, end)

    def test_combining_characters_are_not_normalized(self):
        doc = document("e\u0301")
        snap = CorpusSnapshot(self.corpus, (doc,))
        span = snap.anchor(RevisionReference.from_document(doc), 0, 2)
        self.assert_code("CITATION_MISMATCH", lambda: snap.resolve(span, expected_text="é"))
        self.assertEqual(snap.resolve(span).text, "e\u0301")

    def test_ranges_can_cross_extraction_elements(self):
        doc = document("début\r\nfin🦊", bom=True, split=6)
        snap = CorpusSnapshot(self.corpus, (doc,))
        span = snap.anchor(RevisionReference.from_document(doc), 4, 9)
        self.assertEqual(snap.resolve(span).text, doc.text[4:9])
        self.assertLess(doc.elements[0].end, span.end)
        self.assertGreater(doc.elements[0].end, span.start)

    def test_invalid_character_and_byte_bounds_rejected(self):
        for start, end in ((True, 2), (0, False), (0.0, 1), (0, "1"), (-1, 2), (0, 0), (2, 1)):
            with self.subTest(start=start, end=end):
                self.assert_code("SOURCE_SPAN_INVALID", lambda: self.snapshot.anchor(self.revision, start, end))
                self.assert_code("SOURCE_SPAN_INVALID", lambda: replace(self.span, start=start, end=end))
                self.assert_code("SOURCE_SPAN_INVALID", lambda: replace(self.span, byte_start=start, byte_end=end))
        self.assert_code("SOURCE_SPAN_INVALID", lambda: self.snapshot.anchor(self.revision, 0, 10**100))
        self.assert_code("SOURCE_SPAN_INVALID", lambda: self.snapshot.resolve(replace(self.span, end=10**100)))

    def test_byte_offsets_must_match_both_boundaries(self):
        span = self.snapshot.anchor(self.revision, 20, 25)
        for forged in (replace(span, byte_start=span.byte_start + 1),
                       replace(span, byte_end=span.byte_end - 1),
                       replace(self.span, byte_start=0),
                       replace(span, byte_start=span.start, byte_end=span.end)):
            with self.subTest(span=forged):
                self.assert_code("SOURCE_SPAN_INVALID", lambda: self.snapshot.resolve(forged))

    def test_byte_end_in_middle_of_multibyte_character_rejected(self):
        doc = document("🦊Z")
        snap = CorpusSnapshot(self.corpus, (doc,))
        span = snap.anchor(RevisionReference.from_document(doc), 0, 1)
        self.assertEqual(span.byte_end, 4)
        self.assert_code("SOURCE_SPAN_INVALID", lambda: snap.resolve(replace(span, byte_end=3)))

    def test_empty_documents_have_no_nonempty_citation(self):
        for bom in (False, True):
            doc = document("", bom=bom)
            snap = CorpusSnapshot(self.corpus, (doc,))
            with self.subTest(bom=bom):
                self.assert_code("SOURCE_SPAN_INVALID", lambda: snap.anchor(RevisionReference.from_document(doc), 0, 1))

    def test_missing_and_removed_documents_rejected(self):
        removed = replace(self.snapshot, documents=())
        self.assert_code("CORPUS_DOCUMENT_MISSING", lambda: removed.resolve(self.span))
        self.assert_code("CORPUS_DOCUMENT_MISSING", lambda: removed.anchor(self.revision, 0, 1))
        # La suppression logique n'efface pas rétroactivement un ancien snapshot.
        self.assertEqual(self.snapshot.resolve(self.span).text, self.doc.text)

    def test_changed_content_invalidates_old_revision_in_current_snapshot(self):
        changed = document("nouvelle source fictive")
        current = replace(self.snapshot, documents=(changed,))
        self.assert_code("REVISION_MISMATCH", lambda: current.resolve(self.span))
        self.assert_code("REVISION_MISMATCH", lambda: current.anchor(self.revision, 0, 1))
        new_span = current.anchor(RevisionReference.from_document(changed), 0, 8)
        self.assertEqual(current.resolve(new_span).text, "nouvelle")

    def test_same_content_reimport_has_new_identity(self):
        imported = replace(self.doc, document_id="c" * 32)
        current = replace(self.snapshot, documents=(imported,))
        self.assertEqual(imported.sha256, self.doc.sha256)
        self.assert_code("CORPUS_DOCUMENT_MISSING", lambda: current.resolve(self.span))

    def test_wrong_hash_rejected(self):
        wrong = replace(self.revision, sha256="0" * 64)
        self.assert_code("REVISION_MISMATCH", lambda: self.snapshot.resolve(replace(self.span, revision=wrong)))

    def test_foreign_corpus_rejected_even_for_identical_documents(self):
        foreign = CorpusSnapshot(CorpusReference("d" * 32), (self.doc,))
        self.assert_code("CORPUS_MISMATCH", lambda: foreign.resolve(self.span))
        foreign_empty = replace(foreign, documents=())
        self.assert_code("CORPUS_MISMATCH", lambda: foreign_empty.resolve(self.span))

    def test_authorization_is_explicit_not_just_document_existence(self):
        second = document("AUTRE_CORPUS_FICTIF", document_id="c" * 32)
        outside = CorpusSnapshot(CorpusReference("d" * 32), (second,))
        external_span = outside.anchor(RevisionReference.from_document(second), 0, 5)
        # Même avec un identifiant de corpus changé, le document reste absent.
        self.assert_code("CORPUS_DOCUMENT_MISSING", lambda: self.snapshot.resolve(replace(external_span, corpus=self.corpus)))
        admitted = replace(self.snapshot, documents=(self.doc, second))
        self.assertEqual(admitted.resolve(replace(external_span, corpus=self.corpus)).text, "AUTRE")

    def test_invented_or_inexact_quote_rejected(self):
        for quote in ("citation inventée", self.doc.text + " ", self.doc.text.lower(), 3, b"fictif"):
            with self.subTest():
                self.assert_code("CITATION_MISMATCH", lambda: self.snapshot.resolve(self.span, expected_text=quote))

    def test_constructed_result_is_not_proof_of_matching_source(self):
        # Un objet de transfert n'est jamais une preuve d'authenticité.
        forged = ResolvedCitation(self.span, self.doc.text.replace("Fin", "NON"))
        self.assert_code("CITATION_MISMATCH", lambda: self.snapshot.resolve(forged.span, expected_text=forged.text))
        self.assert_code("PROVENANCE_INVALID", lambda: ResolvedCitation("invalide", "fictif"))
        self.assert_code("PROVENANCE_INVALID", lambda: ResolvedCitation(self.span, 3))
        self.assert_code("CITATION_MISMATCH", lambda: ResolvedCitation(self.span, "fictif"))
        self.assert_code("CITATION_MISMATCH", lambda: ResolvedCitation(self.span, "\ud800"))

    def test_repr_and_error_args_do_not_contain_source(self):
        result = self.snapshot.resolve(self.span)
        for value in (self.snapshot, self.span, self.revision, result):
            self.assertNotIn("PERSONNE_FICTIVE_A", repr(value))
            self.assertNotIn("Québec", repr(value))
        with self.assertRaises(BackendError) as error:
            self.snapshot.resolve(self.span, expected_text="PERSONNE_FICTIVE_A inventée")
        self.assertNotIn("PERSONNE_FICTIVE_A", repr(error.exception.args))
        self.assertNotIn("PERSONNE_FICTIVE_A", repr(error.exception))

    def test_contracts_are_immutable(self):
        result = self.snapshot.resolve(self.span)
        for value, field, replacement in ((self.corpus, "corpus_id", "e" * 32),
                                           (self.revision, "sha256", "0" * 64),
                                           (self.span, "start", 1),
                                           (self.snapshot, "documents", ()),
                                           (result, "text", "autre")):
            with self.subTest(field=field), self.assertRaises(FrozenInstanceError):
                setattr(value, field, replacement)
