"""Unit tests for ``soliplex.ingester.lib.docling``.

The HTTP plumbing (POST + websocket + result GET) is exercised by the
functional tests in ``tests/functional/test_docling.py``; this file
covers the pure-Python helpers that surround it — ``is_html``,
``do_repl``, ``get_docling_sem``, and ``_process_result``.
"""

import asyncio
import json

import pytest

from soliplex.ingester.lib import docling

# ---------------------------------------------------------------------
# is_html
# ---------------------------------------------------------------------


class TestIsHtml:
    def test_doctype_at_start(self):
        assert docling.is_html(b"<!DOCTYPE html>\n<html><body>x</body></html>") is True

    def test_html_tag_within_first_8kb(self):
        payload = b"<!-- comment -->\n<html><body>x</body></html>"
        assert docling.is_html(payload) is True

    def test_requires_body_tag(self):
        # <html> in head but no <body> → not detected as HTML.
        assert docling.is_html(b"<!DOCTYPE html>\n<html>no body here</html>") is False

    def test_html_tag_past_first_100_bytes_but_body_within_8kb(self):
        """``<html`` must appear in the first 100 bytes OR doctype must
        match; ``<body`` must appear in the first 8 KB. The leading
        padding here defeats the 100-byte ``<html`` check, so the doc
        is not recognized."""
        payload = b"X" * 200 + b"<html><body>real html</body></html>"
        assert docling.is_html(payload) is False

    def test_body_past_first_8kb_is_not_html(self):
        """A body tag past the 8 KB window must not trigger detection
        — that's the regression the bound fixes."""
        payload = b"<!DOCTYPE html>\n<html>" + (b" " * 9000) + b"<body>late</body>"
        assert docling.is_html(payload) is False

    def test_pdf_header_not_html(self):
        assert docling.is_html(b"%PDF-1.7\n...") is False

    def test_empty_bytes(self):
        assert docling.is_html(b"") is False

    def test_large_non_html_payload_does_not_full_scan(self):
        """A multi-MB non-HTML payload (e.g. a PDF) should return
        False without scanning the entire buffer. We can't directly
        observe the scan, but we can verify the function returns
        quickly and correctly on a large blob."""
        payload = b"%PDF-1.7\n" + b"A" * (5 * 1024 * 1024)
        assert docling.is_html(payload) is False


# ---------------------------------------------------------------------
# do_repl
# ---------------------------------------------------------------------


class TestDoRepl:
    def test_data_image_string_is_replaced(self):
        assert docling.do_repl("data:image/png;base64,iVBORw0KGgo=") == docling.SMALLEST_PNG

    def test_non_image_string_preserved(self):
        assert docling.do_repl("hello") == "hello"

    def test_nested_dict_replacements(self):
        out = docling.do_repl(
            {
                "title": "x",
                "image": "data:image/png;base64,SOMETHING",
                "nested": {"deeper": "data:image/jpeg;base64,XYZ"},
            }
        )
        assert out["title"] == "x"
        assert out["image"] == docling.SMALLEST_PNG
        assert out["nested"]["deeper"] == docling.SMALLEST_PNG

    def test_nested_list_replacements(self):
        out = docling.do_repl(
            [
                "plain",
                "data:image/png;base64,FOO",
                [{"src": "data:image/png;base64,BAR"}],
            ]
        )
        assert out[0] == "plain"
        assert out[1] == docling.SMALLEST_PNG
        assert out[2][0]["src"] == docling.SMALLEST_PNG

    def test_non_string_scalars_preserved(self):
        assert docling.do_repl(42) == 42
        assert docling.do_repl(3.14) == 3.14
        assert docling.do_repl(True) is True
        assert docling.do_repl(None) is None


# ---------------------------------------------------------------------
# get_docling_sem
# ---------------------------------------------------------------------


class TestGetDoclingSem:
    @pytest.mark.asyncio
    async def test_returns_semaphore(self):
        sem = docling.get_docling_sem()
        assert isinstance(sem, asyncio.Semaphore)

    @pytest.mark.asyncio
    async def test_returns_same_instance(self):
        """Lazy singleton — subsequent calls return the cached
        semaphore so concurrent callers share the same gate."""
        a = docling.get_docling_sem()
        b = docling.get_docling_sem()
        assert a is b

    @pytest.mark.asyncio
    async def test_respects_settings_value(self, monkeypatch):
        """Capacity is read from ``settings.docling_concurrency`` at
        first use. Force a fresh init with a known value."""
        # Reset so the next call rebinds against our patched settings.
        docling._docling_sem = None

        class _FakeSettings:
            docling_concurrency = 7

        monkeypatch.setattr(docling, "get_settings", lambda: _FakeSettings())

        sem = docling.get_docling_sem()
        # ``asyncio.Semaphore``'s internal ``_value`` is the initial
        # capacity when nothing has been acquired.
        assert sem._value == 7


# ---------------------------------------------------------------------
# _process_result
# ---------------------------------------------------------------------


def _success_payload(json_content=None, md_content="# Title") -> dict:
    return {
        "status": "success",
        "processing_time": 1.23,
        "task_id": "abc",
        "document": {
            "json_content": json_content if json_content is not None else {"k": "v"},
            "md_content": md_content,
        },
    }


class TestProcessResult:
    def test_missing_status_raises(self):
        with pytest.raises(ValueError, match="no status in response"):
            docling._process_result({}, parameters={}, source_uri="x", output_formats=["json"])

    def test_non_success_status_raises_with_errors(self):
        res = {"status": "failure", "errors": ["boom", "bang"]}
        with pytest.raises(ValueError, match="boom"):
            docling._process_result(res, parameters={}, source_uri="x", output_formats=["json"])

    def test_success_json_output(self):
        res = _success_payload(json_content={"hello": "world"})
        out = docling._process_result(res, parameters={}, source_uri="x", output_formats=["json"])
        assert isinstance(out["json"], bytes)
        assert json.loads(out["json"]) == {"hello": "world"}

    def test_success_md_output(self):
        res = _success_payload(md_content="# Heading")
        out = docling._process_result(res, parameters={}, source_uri="x", output_formats=["md"])
        assert out["md"] == b"# Heading"

    def test_both_formats_returned(self):
        res = _success_payload(json_content={"x": 1}, md_content="# y")
        out = docling._process_result(res, parameters={}, source_uri="x", output_formats=["json", "md"])
        assert set(out.keys()) == {"json", "md"}

    def test_placeholder_mode_substitutes_images(self):
        """When ``image_export_mode == "placeholder"``, ``do_repl``
        walks the JSON and rewrites embedded image data-URIs to
        ``SMALLEST_PNG``."""
        res = _success_payload(
            json_content={
                "title": "doc",
                "img": "data:image/png;base64,REALPAYLOAD",
            },
        )
        out = docling._process_result(
            res,
            parameters={"image_export_mode": "placeholder"},
            source_uri="x",
            output_formats=["json"],
        )
        decoded = json.loads(out["json"])
        assert decoded["title"] == "doc"
        assert decoded["img"] == docling.SMALLEST_PNG

    def test_non_placeholder_mode_preserves_images(self):
        res = _success_payload(
            json_content={"img": "data:image/png;base64,KEEPME"},
        )
        out = docling._process_result(
            res,
            parameters={"image_export_mode": "embedded"},
            source_uri="x",
            output_formats=["json"],
        )
        decoded = json.loads(out["json"])
        assert decoded["img"] == "data:image/png;base64,KEEPME"

    def test_no_image_export_mode_key_treated_as_non_placeholder(self):
        res = _success_payload(json_content={"img": "data:image/png;base64,XYZ"})
        out = docling._process_result(res, parameters={}, source_uri="x", output_formats=["json"])
        decoded = json.loads(out["json"])
        # No replacement when the key is absent.
        assert decoded["img"] == "data:image/png;base64,XYZ"
