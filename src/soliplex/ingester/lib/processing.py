import logging
import re

from .config import get_settings
from .models import Document

logger = logging.getLogger(__name__)
H1_RE = r"^# (.+)$"
H2_RE = r"^## (.+)$"


def find_regex(markdown_content: str, pattern: str):
    try:
        env = get_settings()
        IGNORES = env.stop_phrases
        matches = re.findall(pattern, markdown_content, re.MULTILINE)

        # find the first match not in the ignore list
        for m in matches:
            if m.strip() not in IGNORES:
                return m

    except Exception as e:
        logger.warning(f"Error finding title: {e}")
        return None


def find_title(doc: Document, markdown_content: str) -> str | None:
    """
    Find the title of a document based on document meta and markdown content.

    Checks document meta for possible titles and then scans the markdown
    content for a title. Adds title to document metadata if found.
    :param doc: The document to check
    :param markdown_content: The markdown string to scan
    :return: The found title or None if no title is found
    """
    meta = doc.doc_meta
    if meta is None:
        meta = {}
        doc.doc_meta = meta

    h1_match = find_regex(markdown_content, H1_RE)
    h2_match = find_regex(markdown_content, H2_RE)
    meta["md_h1_title"] = h1_match
    meta["md_h2_title"] = h2_match
    pdf_title = meta.get("pdf_title")
    meta_title = meta.get("title")
    sel_title = meta_title or pdf_title or h1_match or h2_match
    meta["title"] = sel_title
    return sel_title
