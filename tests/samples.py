"""Builds small real documents of each supported type, for the library tests."""

import os
import zipfile


def make_txt(folder, name, text):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def make_docx(folder, name, paragraphs):
    path = os.path.join(folder, name)
    body = "".join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs)
    document = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
    return path


def make_epub(folder, name, chapters):
    path = os.path.join(folder, name)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        for i, chapter in enumerate(chapters, 1):
            archive.writestr(f"OEBPS/ch{i:02d}.xhtml",
                             f"<html><head><style>p{{color:red}}</style></head><body>"
                             f"<h1>Chapter {i}</h1><p>{chapter}</p></body></html>")
    return path


def make_pdf(folder, name, line):
    """A one-page PDF with a single line of real (extractable) text."""
    stream = f"BT /F1 18 Tf 72 720 Td ({line}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(out)
    return path


def make_empty_pdf(folder, name):
    """A PDF page with no text on it — what a scanned book looks like to a text extractor."""
    return make_pdf(folder, name, "")
