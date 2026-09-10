#!/usr/bin/env python3
"""Renders USER_GUIDE.he.md into a printable Hebrew PDF.

    python docs/build_pdf.py

The Markdown file stays the single source of truth - the PDF is output, the
same way intercom_A.py is output of build_single_file.py.

Rendering goes through headless Chromium rather than a PDF library because the
guide is right-to-left: the browser does the bidi reordering and the shaping
correctly, while the usual Python PDF writers place Hebrew runs backwards.
"""

import os
import shutil
import subprocess
import sys
import tempfile

try:
    import markdown
except ImportError:
    raise SystemExit("Missing dependency: pip install markdown")

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "USER_GUIDE.he.md")
OUTPUT = os.path.join(HERE, "USER_GUIDE.he.pdf")

# מקומות מוכרים של Chromium; אפשר לעקוף עם CHROME_PATH
CHROME_CANDIDATES = (
    os.environ.get("CHROME_PATH", ""),
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)

STYLE = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body {
    direction: rtl; text-align: right;
    font-family: "DejaVu Sans", "Segoe UI", Arial, sans-serif;
    font-size: 10.5pt; line-height: 1.65; color: #1a1a1a; margin: 0;
}
h1 { font-size: 20pt; margin: 0 0 4pt; color: #0b3d5c;
     border-bottom: 2.5pt solid #0b3d5c; padding-bottom: 6pt; }
h2 { font-size: 14pt; margin: 20pt 0 6pt; color: #0b3d5c;
     border-bottom: 0.7pt solid #c4d4de; padding-bottom: 3pt;
     page-break-after: avoid; }
h3 { font-size: 11.5pt; margin: 13pt 0 4pt; color: #23506b;
     page-break-after: avoid; }
p, li { orphans: 2; widows: 2; }
/* טבלה ארוכה מותר לה להימשך לעמוד הבא; שורה בודדת לא נשברת באמצע */
table { border-collapse: collapse; width: 100%; margin: 8pt 0;
        page-break-inside: auto; font-size: 9.5pt; }
tr { page-break-inside: avoid; }
thead { display: table-header-group; }
th, td { border: 0.6pt solid #b9c7d0; padding: 4.5pt 7pt;
         text-align: right; vertical-align: top; }
th { background: #e8eff4; font-weight: bold; color: #0b3d5c; }
tr:nth-child(even) td { background: #f7fafc; }
/* קוד ונתיבים הם תמיד LTR, גם בתוך פסקה בעברית */
code, pre { font-family: "DejaVu Sans Mono", Consolas, monospace;
            direction: ltr; unicode-bidi: embed; }
code { background: #eef2f5; padding: 1pt 4pt; border-radius: 2pt;
       font-size: 9pt; white-space: nowrap; }
pre { background: #f4f7f9; border: 0.6pt solid #d5dfe6;
      border-right: 3pt solid #0b3d5c; padding: 7pt 10pt;
      border-radius: 3pt; text-align: left; overflow-wrap: break-word;
      white-space: pre-wrap; page-break-inside: avoid; font-size: 9pt; }
pre code { background: none; padding: 0; white-space: pre-wrap; }
blockquote { border-right: 3pt solid #e0a030; background: #fdf7ea;
             margin: 8pt 0; padding: 6pt 12pt; page-break-inside: avoid; }
blockquote p { margin: 0; }
hr { border: none; border-top: 0.6pt solid #d5dfe6; margin: 16pt 0; }
ul, ol { padding-right: 20pt; padding-left: 0; margin: 6pt 0; }
strong { color: #0b3d5c; }
"""


def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return candidate
    found = shutil.which("chromium") or shutil.which("google-chrome")
    if found:
        return found
    raise SystemExit("Chromium/Chrome not found. Set CHROME_PATH to its executable.")


def build_html(md_text: str) -> str:
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "sane_lists"])
    return (f'<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">'
            f"<title>אינטרקום P2P - מדריך משתמש</title><style>{STYLE}</style>"
            f"</head><body>{body}</body></html>")


def main() -> int:
    with open(SOURCE, encoding="utf-8") as f:
        html = build_html(f.read())

    workdir = tempfile.mkdtemp(prefix="guide_pdf_")
    html_path = os.path.join(workdir, "guide.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    chrome = find_chrome()
    result = subprocess.run(
        [chrome, "--headless", "--disable-gpu", "--no-sandbox",
         "--no-pdf-header-footer", f"--print-to-pdf={OUTPUT}",
         "file://" + html_path],
        capture_output=True, text=True, timeout=180)

    if result.returncode != 0 or not os.path.exists(OUTPUT):
        sys.stderr.write(result.stderr[-2000:] + "\n")
        raise SystemExit("Chromium failed to produce the PDF.")

    shutil.rmtree(workdir, ignore_errors=True)
    print(f"Wrote {OUTPUT} ({os.path.getsize(OUTPUT) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
