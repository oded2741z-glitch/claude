"""library.py: reading each file type, splitting into passages, and finding the relevant ones."""

import os
import sys
import tempfile

from _harness import check, report
from samples import make_docx, make_empty_pdf, make_epub, make_pdf, make_txt

import library

folder = tempfile.mkdtemp()

# ---------- reading every supported type ----------
check("txt", library.extract_text(make_txt(folder, "a.txt", "The kiln must cool slowly.")) == "The kiln must cool slowly.")
check("md", "# Glazes" in library.extract_text(make_txt(folder, "a.md", "# Glazes\n\nDip, don't brush.")))
check("txt with a byte-order mark", library.extract_text(make_txt(folder, "bom.txt", "﻿hello")) == "hello")
docx = library.extract_text(make_docx(folder, "a.docx", ["Centre the clay.", "Open it slowly."]))
check("docx paragraphs", docx == "Centre the clay.\n\nOpen it slowly.", repr(docx))
epub = library.extract_text(make_epub(folder, "a.epub", ["Wedge the clay &amp; rest it.", "Trim when leather-hard."]))
check("epub chapters in order, markup and styles gone",
      epub.index("Wedge the clay & rest it.") < epub.index("Trim when leather-hard.") and "color:red" not in epub, repr(epub))
check("pdf", library.extract_text(make_pdf(folder, "a.pdf", "Bisque fire at 1000 degrees")).strip()
      == "Bisque fire at 1000 degrees")

def refusal(path):
    try:
        library.extract_text(path)
    except library.DocumentError as e:
        return str(e)
    return None

check("an unsupported type is refused with the list of what works",
      "is not supported" in (refusal(make_txt(folder, "a.rtf", "x")) or ""))
check("an empty file is refused", "has no text" in (refusal(make_txt(folder, "empty.txt", "  \n\n ")) or ""))
check("a scanned-looking PDF says why", "scanned PDF" in (refusal(make_empty_pdf(folder, "scan.pdf")) or ""),
      refusal(make_empty_pdf(folder, "scan.pdf")))
check("a broken file is refused, not crashed on",
      "could not be read" in (refusal(make_txt(folder, "broken.docx", "not a zip")) or ""))

real_reader = library.PdfReader
library.PdfReader = None
check("without pypdf, PDFs say how to fix it", "run install.py" in (refusal(make_pdf(folder, "b.pdf", "x")) or ""))
library.PdfReader = real_reader

# ---------- passages ----------
paragraphs = [f"Paragraph {i}. " + "Clay needs patience and water. " * 8 for i in range(20)]
passages = library.split_passages("\n\n".join(paragraphs))
check("long text becomes several passages", len(passages) > 3, len(passages))
check("no passage much over the target size", max(len(p) for p in passages) <= library.PASSAGE_CHARS, max(map(len, passages)))
check("nothing is lost", "".join(passages).replace("\n", "").replace(" ", "")
      == "".join(paragraphs).replace(" ", ""))
giant = library.split_passages("This is one sentence. " * 300)
check("a single huge paragraph is cut at sentence ends",
      len(giant) > 1 and all(p.endswith(".") for p in giant[:-1]), [p[-20:] for p in giant[:2]])

big = make_txt(folder, "big.txt", "word " * (library.MAX_DOC_CHARS // 4))
_, truncated = library.read_document(big)
check("an enormous document is cut, and says so", truncated)

# ---------- finding what matters ----------
index = library.SearchIndex([
    ("Pottery guide", "Bisque firing comes first. Load the kiln and fire slowly to 1000 degrees."),
    ("Pottery guide", "Glazing: dip the bisqueware in glaze for three seconds, then wipe the foot."),
    ("Cook book", "Bread needs flour, water, salt and yeast. Knead it for ten minutes."),
])
found = index.search("how long do I dip it in the glaze?")
check("the passage about the question comes first", found and "dip the bisqueware" in found[0][1], found[:1])
check("unrelated passages are left out", all("Bread" not in text for _, text in found))
check("a question about nothing in the material finds nothing", index.search("what is the capital of France?") == [])
check("stopwords alone find nothing", index.search("what is the and you") == [])
check("the title travels with the passage", found[0][0] == "Pottery guide")
hebrew = library.SearchIndex([("מדריך", "כבשן צריך להתקרר לאט"), ("ספר", "לחם צריך קמח")])
check("Hebrew words are searchable too", hebrew.search("איך כבשן מתקרר")[0][0] == "מדריך")
budgeted = library.SearchIndex([("t", "kiln " + "x" * 2000)] * 5).search("kiln", limit=5, budget=3000)
check("the material sent with one turn stays within budget",
      sum(len(t) for _, t in budgeted) <= 3000 + 2005 and len(budgeted) < 5, len(budgeted))

report()
