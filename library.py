"""Reading documents and finding the passages that matter, for the companion's library.

Tkinter-free, like core.py, so it can be tested without a display. Nothing here talks to a
model: whole books never go to the API. Documents are split into passages once, and each turn
only the few passages that share words with the conversation are sent along.
"""

import html
import math
import os
import re
import zipfile
from collections import Counter
from html.parser import HTMLParser
from xml.etree import ElementTree

try:
    from pypdf import PdfReader     # optional: only PDFs need it
except Exception:                   # not just ImportError: a broken install must not stop the app starting
    PdfReader = None

SUPPORTED = (".txt", ".md", ".pdf", ".docx", ".epub")
PASSAGE_CHARS = 1200         # a passage is roughly a few paragraphs
MAX_DOC_CHARS = 2_000_000    # about a thousand-page book; anything longer is cut, and says so
SEARCH_BUDGET = 3500         # characters of material sent with one turn, at most

_WORD = re.compile(r"\w+")
STOPWORDS = set("""
a an and are as at be been but by can could did do does for from had has have he her hers him his
how i if in into is it its just me my no not of on or our so than that the their them then there
these they this to too up us was we were what when where which who why will with would you your
yours about also any some all more most very really get got like one two
""".split())


class DocumentError(ValueError):
    """A file that cannot be added, with a reason fit to show the user."""


def tokenize(text):
    return [w for w in _WORD.findall(text.lower()) if len(w) > 1]


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _html_text(markup):
    parser = _TextOnly()
    parser.feed(markup)
    return html.unescape("".join(parser.parts))


def _docx_text(path):
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs = ("".join(t.text or "" for t in p.iter(namespace + "t")) for p in root.iter(namespace + "p"))
    return "\n\n".join(paragraphs)


def _epub_text(path):
    with zipfile.ZipFile(path) as archive:
        pages = sorted(n for n in archive.namelist() if n.lower().endswith((".xhtml", ".html", ".htm")))
        return "\n\n".join(_html_text(archive.read(n).decode("utf-8", errors="replace")) for n in pages)


def extract_text(path):
    """Plain text from a supported file. Raises DocumentError with a reason a person can act on."""
    extension = os.path.splitext(path)[1].lower()
    if extension not in SUPPORTED:
        raise DocumentError(f"{extension or 'this file type'} is not supported — use {', '.join(SUPPORTED)}")
    try:
        if extension in (".txt", ".md"):
            with open(path, "rb") as f:
                text = f.read().decode("utf-8-sig", errors="replace")
        elif extension == ".pdf":
            if PdfReader is None:
                raise DocumentError("reading PDFs needs pypdf — run install.py")
            text = "\n\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        elif extension == ".docx":
            text = _docx_text(path)
        else:
            text = _epub_text(path)
    except DocumentError:
        raise
    except Exception as e:
        raise DocumentError(f"could not be read ({str(e)[:60]})")

    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text).strip()
    if not text:
        hint = " — a scanned PDF is only pictures of pages" if extension == ".pdf" else ""
        raise DocumentError(f"has no text in it{hint}")
    return text


def split_passages(text):
    """Paragraph-aligned passages of about PASSAGE_CHARS; a paragraph longer than that is cut at sentences."""
    pieces = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        while len(paragraph) > PASSAGE_CHARS:
            cut = max(paragraph.rfind(". ", 0, PASSAGE_CHARS), paragraph.rfind("? ", 0, PASSAGE_CHARS),
                      paragraph.rfind("! ", 0, PASSAGE_CHARS))
            cut = cut + 1 if cut > PASSAGE_CHARS // 3 else PASSAGE_CHARS
            pieces.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if paragraph:
            pieces.append(paragraph)

    passages, current = [], ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > PASSAGE_CHARS:
            passages.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        passages.append(current)
    return passages


def read_document(path):
    """(passages, truncated) for a file, ready to store."""
    text = extract_text(path)
    truncated = len(text) > MAX_DOC_CHARS
    return split_passages(text[:MAX_DOC_CHARS]), truncated


class SearchIndex:
    """BM25 over passages: rare words shared with the query count most, common ones barely at all."""

    K1, B = 1.5, 0.75

    def __init__(self, passages):
        self.passages = passages                    # [(title, text)]
        self.counts = [Counter(tokenize(text)) for _, text in passages]
        self.lengths = [sum(c.values()) for c in self.counts]
        self.average = sum(self.lengths) / len(self.lengths) if self.lengths else 0
        self.frequency = Counter()
        for counts in self.counts:
            self.frequency.update(counts.keys())

    def search(self, query, limit=3, budget=SEARCH_BUDGET):
        terms = set(tokenize(query)) - STOPWORDS
        if not terms or not self.passages:
            return []
        total = len(self.passages)
        scored = []
        for i, counts in enumerate(self.counts):
            score = 0.0
            for term in terms:
                tf = counts.get(term)
                if not tf:
                    continue
                df = self.frequency[term]
                idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                norm = self.K1 * (1 - self.B + self.B * self.lengths[i] / (self.average or 1))
                score += idf * tf * (self.K1 + 1) / (tf + norm)
            if score > 0:
                scored.append((score, i))
        scored.sort(reverse=True)

        found, used = [], 0
        for _, i in scored[:limit]:
            title, text = self.passages[i]
            if found and used + len(text) > budget:
                break
            found.append((title, text))
            used += len(text)
        return found
