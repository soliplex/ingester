import logging
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from soliplex.ingester.lib.models import Document
from soliplex.ingester.lib.processing import H1_RE
from soliplex.ingester.lib.processing import H2_RE
from soliplex.ingester.lib.processing import find_regex
from soliplex.ingester.lib.processing import find_title

logger = logging.getLogger(__name__)


# Tests for find_regex function


def test_find_regex_h1_match():
    """Test finding an H1 header in markdown."""
    markdown = "# Hello World\n\nSome content here."
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        result = find_regex(markdown, H1_RE)
        assert result == "Hello World"


def test_find_regex_h2_match():
    """Test finding an H2 header in markdown."""
    markdown = "## Section Title\n\nSome content here."
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        result = find_regex(markdown, H2_RE)
        assert result == "Section Title"


def test_find_regex_no_match():
    """Test when no regex match is found."""
    markdown = "Just plain text without headers."
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        result = find_regex(markdown, H1_RE)
        assert result is None


def test_find_regex_filters_stop_phrases():
    """Test that stop phrases are filtered out."""
    markdown = "# Ignored Title\n\n# Real Title"
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=["Ignored Title"])
        result = find_regex(markdown, H1_RE)
        assert result == "Real Title"


def test_find_regex_all_matches_are_stop_phrases():
    """Test when all matches are in stop phrases list."""
    markdown = "# Ignored\n\n# Also Ignored"
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=["Ignored", "Also Ignored"])
        result = find_regex(markdown, H1_RE)
        assert result is None


def test_find_regex_returns_first_valid_match():
    """Test that the first non-ignored match is returned."""
    markdown = "# First\n\n# Second\n\n# Third"
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        result = find_regex(markdown, H1_RE)
        assert result == "First"


def test_find_regex_strips_whitespace_for_comparison():
    """Test that whitespace is stripped when comparing to stop phrases."""
    markdown = "# Title With Spaces  \n\n# Good Title"
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=["Title With Spaces"])
        result = find_regex(markdown, H1_RE)
        assert result == "Good Title"


def test_find_regex_multiline_content():
    """Test regex matching across multiline markdown content."""
    markdown = """Some intro text.

# Main Title

More content here.

## Subsection

Even more content."""
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        h1_result = find_regex(markdown, H1_RE)
        h2_result = find_regex(markdown, H2_RE)
        assert h1_result == "Main Title"
        assert h2_result == "Subsection"


def test_find_regex_exception_handling():
    """Test that exceptions are caught and None is returned."""
    markdown = "# Title"
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.side_effect = Exception("Settings error")
        result = find_regex(markdown, H1_RE)
        assert result is None


# Tests for find_title function


def test_find_title_from_meta_title():
    """Test that meta title takes priority."""
    doc = Document(hash="test-hash", doc_meta={"title": "Meta Title", "pdf_title": "PDF Title"})
    markdown = "# Markdown Title"
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        result = find_title(doc, markdown)
        assert result == "Meta Title"


def test_find_title_from_pdf_title():
    """Test that pdf_title is used when meta title is absent."""
    doc = Document(hash="test-hash", doc_meta={"pdf_title": "PDF Title"})
    markdown = "# Markdown Title"
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        result = find_title(doc, markdown)
        assert result == "PDF Title"


def test_find_title_from_h1():
    """Test that H1 is used when metadata titles are absent."""
    doc = Document(hash="test-hash", doc_meta={})
    markdown = "# H1 Title\n\n## H2 Title"
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        result = find_title(doc, markdown)
        assert result == "H1 Title"


def test_find_title_from_h2():
    """Test that H2 is used when all other sources are absent."""
    doc = Document(hash="test-hash", doc_meta={})
    markdown = "Some content\n\n## H2 Title"
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        result = find_title(doc, markdown)
        assert result == "H2 Title"


def test_find_title_no_title_found():
    """Test when no title can be found."""
    doc = Document(hash="test-hash", doc_meta={})
    markdown = "Just plain content without headers."
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        result = find_title(doc, markdown)
        assert result is None


def test_find_title_updates_metadata():
    """Test that document metadata is updated with found titles."""
    doc = Document(hash="test-hash", doc_meta={})
    markdown = "# H1 Title\n\n## H2 Title"
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        find_title(doc, markdown)
        assert doc.doc_meta["md_h1_title"] == "H1 Title"
        assert doc.doc_meta["md_h2_title"] == "H2 Title"
        assert doc.doc_meta["title"] == "H1 Title"


def test_find_title_initializes_none_metadata():
    """Test that None metadata is initialized to empty dict."""
    doc = Document(hash="test-hash")
    doc.doc_meta = None
    markdown = "# Title"
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        result = find_title(doc, markdown)
        assert result == "Title"
        assert doc.doc_meta is not None
        assert doc.doc_meta["title"] == "Title"


def test_find_title_preserves_existing_metadata():
    """Test that existing metadata is preserved."""
    doc = Document(hash="test-hash", doc_meta={"existing_key": "existing_value"})
    markdown = "# New Title"
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        find_title(doc, markdown)
        assert doc.doc_meta["existing_key"] == "existing_value"
        assert doc.doc_meta["title"] == "New Title"


@pytest.mark.parametrize(
    "meta, markdown, expected",
    [
        ({"title": "Meta", "pdf_title": "PDF"}, "# H1\n## H2", "Meta"),
        ({"pdf_title": "PDF"}, "# H1\n## H2", "PDF"),
        ({}, "# H1\n## H2", "H1"),
        ({}, "## H2", "H2"),
        ({}, "plain text", None),
    ],
)
def test_find_title_priority_order(meta, markdown, expected):
    """Test the complete priority order: meta_title > pdf_title > h1 > h2."""
    doc = Document(hash="test-hash", doc_meta=meta.copy())
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=[])
        result = find_title(doc, markdown)
        assert result == expected


def test_find_title_with_stop_phrases():
    """Test that stop phrases affect H1/H2 detection in title finding."""
    doc = Document(hash="test-hash", doc_meta={})
    markdown = "# Ignored Title\n\n# Real Title"
    with patch("soliplex.ingester.lib.processing.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(stop_phrases=["Ignored Title"])
        result = find_title(doc, markdown)
        assert result == "Real Title"
