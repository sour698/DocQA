"""Render one PDF page to a PNG, optionally highlighting a cited passage.

Used for click-through citations: the UI shows the exact page a citation points to, with the
supporting text highlighted. Uses pypdfium2 (bundles its own PDFium, no system packages).
"""

import io
import re


def _phrases(quote: str, size: int = 5, limit: int = 6) -> list[str]:
    words = re.findall(r"\S+", quote or "")
    if not words:
        return []
    if len(words) <= size:
        return [" ".join(words)]
    step = max(1, (len(words) - size) // (limit - 1))
    return [" ".join(words[i : i + size]) for i in range(0, len(words) - size + 1, step)][:limit]


def render_page(source, page_no: int, quote: str = "", scale: float = 1.6) -> tuple[bytes, int]:
    """Return (png_bytes, number_of_highlights). ``page_no`` is 1-based."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw

    pdf = pdfium.PdfDocument(source if isinstance(source, bytes) else str(source))
    try:
        if not 1 <= page_no <= len(pdf):
            raise ValueError(f"Page {page_no} is out of range (1-{len(pdf)}).")
        page = pdf[page_no - 1]
        image = page.render(scale=scale).to_pil().convert("RGBA")
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        height = page.get_height()
        found = 0
        textpage = page.get_textpage()
        for phrase in _phrases(quote):
            searcher = textpage.search(phrase, match_case=False)
            while True:
                hit = searcher.get_next()
                if hit is None:
                    break
                index, count = hit
                for rect_i in range(textpage.count_rects(index, count)):
                    left, bottom, right, top = textpage.get_rect(rect_i)
                    draw.rectangle(
                        [
                            left * scale,
                            (height - top) * scale,
                            right * scale,
                            (height - bottom) * scale,
                        ],
                        fill=(255, 221, 0, 110),
                    )
                found += 1
        out = Image.alpha_composite(image, overlay).convert("RGB")
        buf = io.BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue(), found
    finally:
        pdf.close()
