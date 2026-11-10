"""Document ingestion: PDF (text + OCR fallback + tables), DOCX, TXT/MD and HTML.

``load_document`` turns a file into ``[(page_number, text), ...]``; ``chunk_pages`` turns pages
into retrieval chunks. Chunking is structure-aware: it splits at headings, keeps tables as
separate chunks (header row repeated), and only falls back to overlapping word windows for long
runs of text.
"""

import logging
import re
import time
from html.parser import HTMLParser
from pathlib import Path

from .config import MAX_OCR_PAGES, MAX_PDF_PAGES, TABLE_EXTRACT_SECONDS

log = logging.getLogger(__name__)



class IngestLimitError(ValueError):
    """The document is valid but too big to process on this server (message is user-facing)."""


TABLE_RE = re.compile(r"\[TABLE\]\n(.*?)\n\[/TABLE\]", re.S)
_NUMBERED = re.compile(r"^(\d+(\.\d+){0,3}[.)]?|[A-Z][.)]|(Section|Article|Chapter)\s+\d+[.:]?)\s+")


# ----------------------------------------------------------------------------- PDF
def _table_to_text(table) -> str:
    rows = []
    for row in table:
        cells = [re.sub(r"\s+", " ", str(c or "")).strip() for c in row]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _drop_table_duplicates(text: str, table_texts: list[str]) -> str:
    """pypdf also emits table cells as plain lines. Remove those lines from the page text, since
    the table is indexed separately and the duplicate would only add noise."""
    norm = lambda s: re.sub(r"\W+", " ", s).strip().lower()  # noqa: E731
    table_blob = " ".join(norm(t) for t in table_texts)
    kept = []
    for line in text.splitlines():
        n = norm(line)
        if n and len(n) >= 3 and f" {n} " in f" {table_blob} ":
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _ocr_page(path: str, page_no: int, dpi: int = 150) -> str:
    try:
        import pytesseract
        from pdf2image import convert_from_path

        images = convert_from_path(path, first_page=page_no, last_page=page_no, dpi=dpi)
        return pytesseract.image_to_string(images[0]).strip() if images else ""
    except Exception as exc:  # OCR is optional; report it instead of hiding it
        log.warning("OCR unavailable/failed on page %s of %s: %s", page_no, path, exc)
        return ""


def load_pdf(path, use_ocr: bool = True, extract_tables: bool = True) -> list[tuple[int, str]]:
    from pypdf import PdfReader

    path = Path(path)
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        # Password-protected: pypdf would fail page by page with obscure errors.
        try:
            if not reader.decrypt(""):  # PDFs encrypted with an empty user password still open
                raise IngestLimitError("This PDF is password-protected. Remove the password and try again.")
        except IngestLimitError:
            raise
        except Exception:  # noqa: BLE001
            raise IngestLimitError(
                "This PDF is password-protected. Remove the password and try again."
            ) from None
    n_pages = len(reader.pages)
    if n_pages > MAX_PDF_PAGES:
        raise IngestLimitError(
            f"This PDF has {n_pages} pages; the limit on this server is {MAX_PDF_PAGES}. "
            "Split it into smaller files."
        )
    ocr_used = 0
    plumber = None
    table_time = 0.0  # seconds spent on table detection so far (bounded by TABLE_EXTRACT_SECONDS)
    if extract_tables and TABLE_EXTRACT_SECONDS > 0:
        try:
            import pdfplumber

            plumber = pdfplumber.open(str(path))  # opened ONCE for the whole document
        except Exception as exc:
            log.warning("Table extraction disabled for %s: %s", path.name, exc)
    pages = []
    try:
        for i, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if len(text) < 40 and use_ocr and ocr_used < MAX_OCR_PAGES:
                ocr_used += 1
                ocr_text = _ocr_page(str(path), i)
                if len(ocr_text) > len(text):
                    text = ocr_text
            if plumber is not None and table_time >= TABLE_EXTRACT_SECONDS:
                log.warning(
                    "Table extraction stopped after %.0f s at page %s of %s; the remaining pages are "
                    "indexed as text only (see TABLE_EXTRACT_SECONDS).",
                    table_time,
                    i,
                    path.name,
                )
                plumber.close()
                plumber = None
            if plumber is not None and i <= len(plumber.pages):
                started = time.monotonic()
                try:
                    plumber_page = plumber.pages[i - 1]
                    tables = [
                        t for t in (_table_to_text(x) for x in plumber_page.extract_tables()) if t
                    ]
                    if hasattr(plumber_page, "flush_cache"):
                        plumber_page.flush_cache()  # keep memory flat on very long documents
                except Exception as exc:
                    log.warning("Table extraction failed on page %s of %s: %s", i, path.name, exc)
                    tables = []
                table_time += time.monotonic() - started
                if tables:
                    text = _drop_table_duplicates(text, tables)
                    text += "".join(f"\n\n[TABLE]\n{t}\n[/TABLE]" for t in tables)
            if text.strip():
                pages.append((i, text.strip()))
    finally:
        if plumber is not None:
            plumber.close()
    return pages


# ------------------------------------------------------------------ other formats
def _paginate(blocks: list[str], words_per_page: int = 450) -> list[tuple[int, str]]:
    """Plain-text formats have no pages: group paragraphs into ~450-word "pages" so citations
    still point to a stable location (shown as p.N)."""
    pages, cur, count = [], [], 0
    for b in blocks:
        n = len(b.split())
        if cur and count + n > words_per_page:
            pages.append("\n\n".join(cur))
            cur, count = [], 0
        cur.append(b)
        count += n
    if cur:
        pages.append("\n\n".join(cur))
    return [(i, t) for i, t in enumerate(pages, start=1) if t.strip()]


def load_text(path) -> list[tuple[int, str]]:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", raw) if b.strip()]
    return _paginate(blocks)


def load_docx(path) -> list[tuple[int, str]]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    blocks = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, doc)
            text = p.text.strip()
            if not text:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            blocks.append(f"# {text}" if style.startswith(("Heading", "Title")) else text)
        elif tag == "tbl":
            t = _table_to_text([[c.text for c in row.cells] for row in Table(child, doc).rows])
            if t:
                blocks.append(f"[TABLE]\n{t}\n[/TABLE]")
    return _paginate(blocks)


class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "head", "svg"}
    BLOCK = {"p", "div", "li", "br", "section", "article", "ul", "ol", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self._skip, self._buf = [], 0, []
        self._heading = False
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text:
            self.out.append(("# " + text) if self._heading else text)

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush()
            self._heading = True
        elif tag == "table":
            self._flush()
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._buf = []
        elif tag in self.BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush()
            self._heading = False
        elif tag in {"td", "th"} and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._buf)).strip())
            self._buf = []
        elif tag == "tr" and self._table is not None and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            text = _table_to_text(self._table)
            if text:
                self.out.append(f"[TABLE]\n{text}\n[/TABLE]")
            self._table = None
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def html_to_blocks(raw: str) -> list[str]:
    parser = _HTMLText()
    parser.feed(raw)
    parser.close()
    return parser.out


def load_html(path) -> list[tuple[int, str]]:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    return _paginate(html_to_blocks(raw))


def load_document(path, use_ocr: bool = True, extract_tables: bool = True):
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return load_pdf(path, use_ocr=use_ocr, extract_tables=extract_tables)
    if ext == ".docx":
        return load_docx(path)
    if ext in {".html", ".htm"}:
        return load_html(path)
    if ext in {".txt", ".md"}:
        return load_text(path)
    raise ValueError(f"Unsupported file type: {ext}")


# --------------------------------------------------------------------- chunking
def is_heading(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if s.startswith("#"):
        return True
    if len(s) > 80 or len(s.split()) > 10 or s.endswith((".", ",", ";")):
        return False
    letters = [ch for ch in s if ch.isalpha()]
    if not letters or not s[0].isupper() and not _NUMBERED.match(s):
        return False
    if s.isupper():
        return True
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", s) if len(w) > 3]
    return bool(words) and sum(w[0].isupper() for w in words) / len(words) >= 0.6


def _heading_text(line: str) -> str:
    return line.strip().lstrip("#").strip()


def _split_blocks(text: str):
    """Yield ('text', str) / ('table', rows_list) segments of one page, in order."""
    pos = 0
    for m in TABLE_RE.finditer(text):
        if text[pos : m.start()].strip():
            yield "text", text[pos : m.start()]
        yield "table", [r for r in m.group(1).splitlines() if r.strip()]
        pos = m.end()
    if text[pos:].strip():
        yield "text", text[pos:]


def _table_chunks(rows: list[str], chunk_words: int) -> list[str]:
    """Pack table rows into chunks of about ``chunk_words`` words, repeating the header row."""
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    if not body:
        return [header]
    out, cur, count = [], [], len(header.split())
    for r in body:
        n = len(r.split())
        if cur and count + n > chunk_words:
            out.append("\n".join([header, *cur]))
            cur, count = [], len(header.split())
        cur.append(r)
        count += n
    if cur:
        out.append("\n".join([header, *cur]))
    return out


def chunk_pages(pages, source: str, chunk_words: int = 180, overlap: int = 40) -> list[dict]:
    if chunk_words < 20 or overlap < 0 or overlap >= chunk_words:
        raise ValueError("Require chunk_words >= 20 and 0 <= overlap < chunk_words")
    step = chunk_words - overlap
    chunks: list[dict] = []
    heading = ""  # the active section heading carries across pages

    for page_no, text in pages:
        counter = 0

        def emit(body: str, head: str, page_no: int = page_no):
            nonlocal counter
            chunks.append(
                {
                    "id": f"{source}::p{page_no}::c{counter}",
                    "text": body,
                    "source": source,
                    "page": int(page_no),
                    "heading": head,
                }
            )
            counter += 1

        for kind, payload in _split_blocks(text):
            if kind == "table":
                for piece in _table_chunks(payload, chunk_words):
                    emit(piece, heading)
                continue
            # --- text: cut into sections at headings, then pack small sections together
            sections: list[tuple[str, list[str]]] = []  # (heading_at_start, lines)
            cur_head, cur_lines = heading, []
            for line in payload.splitlines():
                if is_heading(line):
                    if cur_lines:
                        sections.append((cur_head, cur_lines))
                    cur_head, cur_lines = _heading_text(line), [line.strip().lstrip("# ").strip()]
                    heading = cur_head
                elif line.strip():
                    cur_lines.append(line.strip())
            if cur_lines:
                sections.append((cur_head, cur_lines))

            pack_head, pack_words = "", []
            for sec_head, lines in sections:
                words = " ".join(lines).split()
                if len(words) > chunk_words:  # long section: flush, then overlapping windows
                    if pack_words:
                        emit(" ".join(pack_words), pack_head)
                        pack_words = []
                    for start in range(0, len(words), step):
                        piece = words[start : start + chunk_words]
                        if start > 0 and len(piece) <= overlap:
                            break
                        emit(" ".join(piece), sec_head)
                elif pack_words and len(pack_words) + len(words) <= chunk_words:
                    pack_words += words
                else:
                    if pack_words:
                        emit(" ".join(pack_words), pack_head)
                    pack_head, pack_words = sec_head, list(words)
            if pack_words:
                emit(" ".join(pack_words), pack_head)
    return chunks
