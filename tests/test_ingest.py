import pytest
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table

from app.ingest import (
    chunk_pages,
    html_to_blocks,
    is_heading,
    load_document,
    load_pdf,
)

LEAVE = """ANNUAL LEAVE
Employees get 24 days of paid leave each year.
Carry Forward
Up to 10 days may be carried forward.

[TABLE]
Type | Days
Annual | 24
Sick | 12
[/TABLE]"""


def test_invalid_chunk_parameters():
    with pytest.raises(ValueError):
        chunk_pages([(1, "hello")], "x", chunk_words=20, overlap=20)


def test_page_boundaries_are_kept():
    pages = [(1, " ".join(["alpha"] * 60)), (2, " ".join(["beta"] * 60))]
    chunks = chunk_pages(pages, "x.pdf", chunk_words=30, overlap=5)
    assert {c["page"] for c in chunks} == {1, 2}
    assert all("alpha" not in c["text"] for c in chunks if c["page"] == 2)


def test_headings_detected_and_stored():
    assert is_heading("ANNUAL LEAVE") and is_heading("Carry Forward") and is_heading("# Intro")
    assert is_heading("2.1 Sick Leave Rules")
    assert not is_heading("Employees get 24 days of paid leave each year.")
    assert not is_heading("w0 w1 w2 w3 w4 w5 w6 w7 w8 w9 w10 w11")
    chunks = chunk_pages(
        [(1, "INTRO\nHello there friend.\n\nSick Leave\n" + "word " * 200)], "d.pdf", 50, 10
    )
    assert chunks[0]["heading"] == "INTRO"
    assert any(c["heading"] == "Sick Leave" for c in chunks)


def test_heading_carries_over_to_next_page():
    chunks = chunk_pages(
        [(1, "Sick Leave\nTwelve days."), (2, "Continued text without heading.")], "d.pdf"
    )
    assert [c["heading"] for c in chunks] == ["Sick Leave", "Sick Leave"]


def test_tables_become_their_own_chunks_with_header_repeated():
    rows = "\n".join(f"Item{i} | {i}" for i in range(60))
    text = f"Prices\nSome intro.\n\n[TABLE]\nName | Qty\n{rows}\n[/TABLE]"
    chunks = chunk_pages([(1, text)], "d.pdf", chunk_words=40, overlap=5)
    table_chunks = [c for c in chunks if c["text"].startswith("Name | Qty")]
    assert len(table_chunks) > 1  # long table split across chunks...
    assert all(c["text"].splitlines()[0] == "Name | Qty" for c in table_chunks)  # ...header on each
    assert not any("Name | Qty" in c["text"] for c in chunks if c not in table_chunks)


def test_chunk_ids_are_unique_and_parseable():
    chunks = chunk_pages([(1, LEAVE), (2, LEAVE)], "Acme.pdf", 30, 5)
    assert len({c["id"] for c in chunks}) == len(chunks)
    assert all(int(c["id"].rsplit("::c", 1)[1]) >= 0 for c in chunks)


def _make_pdf(path):
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    table = Table([["Type", "Days"], ["Annual", "24"], ["Sick", "12"]])
    table.setStyle([("GRID", (0, 0), (-1, -1), 0.5, "black")])
    doc.build(
        [
            Paragraph("Leave Policy", styles["Heading1"]),
            Paragraph(
                "Employees receive twenty four days of annual leave every calendar year.",
                styles["Normal"],
            ),
            table,
            PageBreak(),
            Paragraph(
                "Sick leave requires a medical certificate after two consecutive days of absence.",
                styles["Normal"],
            ),
        ]
    )


def test_load_pdf_reads_pages_and_tables(tmp_path):
    pdf = tmp_path / "leave.pdf"
    _make_pdf(pdf)
    pages = load_pdf(pdf, use_ocr=False)
    assert [p for p, _ in pages] == [1, 2]
    assert "twenty four days" in pages[0][1] and "[TABLE]" in pages[0][1]
    assert "Annual | 24" in pages[0][1]
    # table cells must not ALSO appear as stray text lines outside the table block
    outside = pages[0][1].split("[TABLE]")[0]
    assert "Annual" not in outside


def test_load_pdf_without_tables(tmp_path):
    pdf = tmp_path / "leave.pdf"
    _make_pdf(pdf)
    pages = load_document(pdf, use_ocr=False, extract_tables=False)
    assert "[TABLE]" not in pages[0][1]


def test_load_text_and_markdown(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# Title\n\nFirst paragraph.\n\nSecond paragraph.", encoding="utf-8")
    pages = load_document(f)
    assert pages[0][0] == 1 and "# Title" in pages[0][1] and "Second paragraph." in pages[0][1]


def test_text_is_paginated_by_size(tmp_path):
    f = tmp_path / "long.txt"
    f.write_text("\n\n".join(" ".join(["word"] * 200) for _ in range(6)), encoding="utf-8")
    assert len(load_document(f)) >= 3


def test_load_docx_headings_and_tables(tmp_path):
    from docx import Document

    d = Document()
    d.add_heading("Travel Policy", level=1)
    d.add_paragraph("Flights must be booked 14 days ahead.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "City", "Limit"
    t.cell(1, 0).text, t.cell(1, 1).text = "Delhi", "5000"
    f = tmp_path / "travel.docx"
    d.save(f)
    text = load_document(f)[0][1]
    assert "# Travel Policy" in text and "14 days" in text and "City | Limit" in text
    chunks = chunk_pages(load_document(f), "travel.docx")
    assert chunks[0]["heading"] == "Travel Policy"


def test_html_text_headings_tables_and_scripts_stripped():
    raw = (
        "<html><head><title>x</title><style>p{}</style></head><body><script>evil()</script>"
        "<h1>Policy</h1><p>Be <b>kind</b>.</p><table><tr><th>A</th><th>B</th></tr>"
        "<tr><td>1</td><td>2</td></tr></table></body></html>"
    )
    blocks = html_to_blocks(raw)
    joined = "\n".join(blocks)
    assert "# Policy" in blocks and "Be kind." in blocks
    assert "evil" not in joined and "p{}" not in joined
    assert "[TABLE]\nA | B\n1 | 2\n[/TABLE]" in joined


def test_unsupported_extension(tmp_path):
    f = tmp_path / "x.exe"
    f.write_bytes(b"MZ")
    with pytest.raises(ValueError):
        load_document(f)


# ------------------------------------------------------------------ bounded table extraction
def _table_pdf(path, pages=4):
    styles = getSampleStyleSheet()
    flow = []
    for n in range(pages):
        table = Table([["Type", "Days"], [f"Annual{n}", "24"], [f"Sick{n}", "12"]])
        table.setStyle([("GRID", (0, 0), (-1, -1), 0.5, "black")])
        flow += [
            Paragraph(
                f"Policy page {n + 1} with enough ordinary text to be read.", styles["Normal"]
            ),
            table,
            PageBreak(),
        ]
    SimpleDocTemplate(str(path), pagesize=A4).build(flow)


def test_table_extraction_stops_when_its_time_budget_is_used_up(tmp_path, monkeypatch):
    from app import ingest

    pdf = tmp_path / "many.pdf"
    _table_pdf(pdf)
    monkeypatch.setattr(
        ingest, "TABLE_EXTRACT_SECONDS", 1e-9
    )  # page 1 is processed, then the budget is gone
    pages = dict(load_pdf(pdf, use_ocr=False))
    assert "[TABLE]" in pages[1]
    assert all("[TABLE]" not in pages[n] for n in (2, 3, 4))  # text only from then on
    assert "Policy page 4" in pages[4]  # nothing was lost, only the tables


def test_a_zero_budget_turns_table_extraction_off(tmp_path, monkeypatch):
    from app import ingest

    pdf = tmp_path / "t.pdf"
    _table_pdf(pdf, pages=2)
    monkeypatch.setattr(ingest, "TABLE_EXTRACT_SECONDS", 0)
    assert all("[TABLE]" not in text for _, text in load_pdf(pdf, use_ocr=False))


def test_the_default_budget_extracts_every_table(tmp_path):
    pdf = tmp_path / "t.pdf"
    _table_pdf(pdf, pages=3)
    assert all("[TABLE]" in text for _, text in load_pdf(pdf, use_ocr=False))
