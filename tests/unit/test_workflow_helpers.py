"""Unit tests for the sync CPU helpers in ``soliplex.ingester.lib.workflow``.

These are the helpers that callers hand to ``asyncio.to_thread`` so
pure-Python loops over big pydantic / JSON / pypdf structures don't
block the event loop. The functions themselves are sync, so the tests
exercise them directly without any async machinery.
"""

import json
from io import BytesIO

import pypdf
import pytest
from haiku.rag.store.models.chunk import Chunk
from pypdf import PdfWriter

from soliplex.ingester.lib import workflow

# ---------------------------------------------------------------------
# _chunks_from_bytes / _chunks_to_bytes
# ---------------------------------------------------------------------


def _sample_chunks() -> list[Chunk]:
    return [
        Chunk(content="first chunk", order=0, metadata={"page": 1}),
        Chunk(content="second chunk", order=1, metadata={"page": 2}),
        Chunk(content="third chunk", order=2),
    ]


class TestChunksFromBytes:
    def test_empty_array_returns_empty_list(self):
        assert workflow._chunks_from_bytes(b"[]") == []

    def test_single_chunk_roundtrip(self):
        chunks = workflow._chunks_from_bytes(b'[{"content": "hello"}]')
        assert len(chunks) == 1
        assert chunks[0].content == "hello"

    def test_multiple_chunks_preserve_order_and_fields(self):
        payload = json.dumps(
            [
                {"content": "a", "order": 0, "metadata": {"k": "v"}},
                {"content": "b", "order": 1},
            ]
        ).encode("utf-8")
        chunks = workflow._chunks_from_bytes(payload)
        assert [c.content for c in chunks] == ["a", "b"]
        assert chunks[0].metadata == {"k": "v"}
        assert chunks[0].order == 0
        assert chunks[1].order == 1

    def test_accepts_bytes_directly(self):
        """``json.loads`` accepts ``bytes`` in 3.6+, so the helper
        skips an explicit ``decode`` step. Verify that contract holds."""
        chunks = workflow._chunks_from_bytes(b'[{"content": "x"}]')
        assert chunks[0].content == "x"

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            workflow._chunks_from_bytes(b"not json")

    def test_missing_required_field_raises(self):
        # Chunk requires ``content``; missing it should fail validation.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            workflow._chunks_from_bytes(b'[{"order": 0}]')


class TestChunksToBytes:
    def test_empty_list_serializes_to_empty_array(self):
        assert workflow._chunks_to_bytes([]) == b"[]"

    def test_returns_bytes(self):
        out = workflow._chunks_to_bytes(_sample_chunks())
        assert isinstance(out, bytes)

    def test_roundtrip_preserves_fields(self):
        original = _sample_chunks()
        encoded = workflow._chunks_to_bytes(original)
        decoded = workflow._chunks_from_bytes(encoded)
        assert len(decoded) == len(original)
        for o, d in zip(original, decoded, strict=True):
            assert o.content == d.content
            assert o.order == d.order
            assert o.metadata == d.metadata

    def test_accepts_anything_with_model_dump(self):
        """The helper is duck-typed on ``model_dump``; verify a stand-in
        with that single method works (so embedding-result types from
        haiku are accepted regardless of their concrete class)."""

        class FakeChunk:
            def __init__(self, payload):
                self._payload = payload

            def model_dump(self):
                return self._payload

        out = workflow._chunks_to_bytes([FakeChunk({"a": 1}), FakeChunk({"b": 2})])
        assert json.loads(out) == [{"a": 1}, {"b": 2}]


# ---------------------------------------------------------------------
# _extract_pdf_metadata
# ---------------------------------------------------------------------


def _make_pdf_bytes(num_pages: int = 1, metadata: dict | None = None) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=72, height=72)
    if metadata is not None:
        writer.add_metadata(metadata)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestExtractPdfMetadata:
    def test_returns_page_count(self):
        pdf_bytes = _make_pdf_bytes(num_pages=3)
        meta = workflow._extract_pdf_metadata(pdf_bytes)
        assert meta["page_count"] == 3

    def test_extracts_info_keys_with_pdf_prefix(self):
        pdf_bytes = _make_pdf_bytes(
            num_pages=1,
            metadata={
                "/Author": "Alice",
                "/Title": "Test Doc",
                "/Subject": "Subj",
                "/Keywords": "kw1, kw2",
            },
        )
        meta = workflow._extract_pdf_metadata(pdf_bytes)
        assert meta["pdf_author"] == "Alice"
        assert meta["pdf_title"] == "Test Doc"
        assert meta["pdf_subject"] == "Subj"
        assert meta["pdf_keywords"] == "kw1, kw2"

    def test_no_pdf_info_returns_only_page_count(self):
        pdf_bytes = _make_pdf_bytes(num_pages=1, metadata=None)
        meta = workflow._extract_pdf_metadata(pdf_bytes)
        # No info dict was set, so no pdf_* keys.
        assert meta == {"page_count": 1} or set(meta) == {"page_count"}

    def test_only_set_keys_are_emitted(self):
        pdf_bytes = _make_pdf_bytes(num_pages=2, metadata={"/Title": "Only Title"})
        meta = workflow._extract_pdf_metadata(pdf_bytes)
        assert meta["page_count"] == 2
        assert meta["pdf_title"] == "Only Title"
        assert "pdf_author" not in meta
        assert "pdf_subject" not in meta

    def test_subject_listed_once(self):
        """Original implementation iterated ``["/Subject", "/Subject"]``
        — the helper dedupes to one entry."""
        pdf_bytes = _make_pdf_bytes(num_pages=1, metadata={"/Subject": "X"})
        meta = workflow._extract_pdf_metadata(pdf_bytes)
        # The value is captured once. (Order-independent check.)
        assert list(meta.keys()).count("pdf_subject") == 1

    def test_invalid_bytes_raises(self):
        """Junk input bubbles up — the caller (``validate_document``)
        catches the exception and marks the doc invalid."""
        with pytest.raises(pypdf.errors.PdfReadError):
            workflow._extract_pdf_metadata(b"not a pdf")


# ---------------------------------------------------------------------
# _md5_hex
# ---------------------------------------------------------------------


class TestMd5Hex:
    def test_empty_bytes(self):
        # MD5 of empty input.
        assert workflow._md5_hex(b"") == "d41d8cd98f00b204e9800998ecf8427e"

    def test_known_value(self):
        # MD5 of "hello" — stable reference.
        assert workflow._md5_hex(b"hello") == "5d41402abc4b2a76b9719d911017c592"

    def test_returns_str(self):
        assert isinstance(workflow._md5_hex(b"x"), str)

    def test_length_is_32_hex_chars(self):
        assert len(workflow._md5_hex(b"some bytes")) == 32
