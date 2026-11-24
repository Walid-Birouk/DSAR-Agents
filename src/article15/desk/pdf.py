"""The copy of the data as a PDF (GDPR Art. 15(3): a commonly used electronic form).

The PDF is rendered from the pack, the exact JSON a person approved, so it shows nothing the approval did not
cover: the letter, then every record with its fields as they are in the pack (third parties already hidden).
Every page carries the pack's SHA-256, which ties the PDF to the approval.

It is written by hand, with no library: the standard Courier font needs no font file and no glyph metrics
(every character is 0.6 em wide), WinAnsiEncoding covers French, and the output is deterministic, so the same
pack always gives the same bytes. Characters outside that encoding (rare: emoji, some quotes) become "?".
"""

from __future__ import annotations

import textwrap
from typing import Any

from article15.desk.pack import hash_pack

PAGE_W, PAGE_H = 595, 842  # A4, in points
MARGIN = 50
SIZE = 9.5
LEADING = 12.0
COLUMNS = int((PAGE_W - 2 * MARGIN) / (0.6 * SIZE))
ROWS = int((PAGE_H - 2 * MARGIN - 2 * LEADING) / LEADING)

TITLES = {
    "fr": ("Copie de vos données personnelles", "Lettre", "Vos données", "enregistrement(s)", "page"),
    "en": ("Copy of your personal data", "Letter", "Your data", "record(s)", "page"),
}


def _encode(text: str) -> bytes:
    """A PDF literal string in WinAnsiEncoding: escapes for ( ) and backslash, octal for bytes >= 128."""
    raw = text.replace("\u2019", "'").replace("\u202f", " ").replace("\u00a0", " ")
    out = bytearray(b"(")
    for byte in raw.encode("cp1252", errors="replace"):
        if byte in b"()\\":
            out += b"\\" + bytes([byte])
        elif byte < 32 or byte >= 128:
            out += f"\\{byte:03o}".encode("ascii")
        else:
            out.append(byte)
    return bytes(out + b")")


def _wrap(text: str, indent: str = "") -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        lines += textwrap.wrap(
            paragraph,
            width=COLUMNS,
            initial_indent=indent,
            subsequent_indent=indent,
            break_long_words=True,
            break_on_hyphens=False,
        )
    return lines


def _lines(pack: dict[str, Any]) -> list[tuple[str, bool]]:
    """The document as (line, bold) pairs."""
    title, letter, data, records, _ = TITLES.get(pack.get("language", "fr"), TITLES["fr"])
    out: list[tuple[str, bool]] = [(title, True), (f"{pack['case_id']}", False), ("", False)]
    out += [(f"{letter}: {pack['letter']['subject_line']}", True), ("", False)]
    out += [(line, False) for line in _wrap(pack["letter"]["body"])]
    total = len(pack["records"])
    out += [("", False), (f"{data}: {total} {records}", True)]
    system = None
    for rec in pack["records"]:
        if rec["system"] != system:
            system = rec["system"]
            out += [("", False), (f"== {system} ({pack['record_counts'].get(system, 0)})", True)]
        out.append((f"{rec['table']} {rec['record_id']}", True))
        for name, value in rec["fields"].items():
            out += [(line, False) for line in _wrap(f"{name}: {'' if value is None else value}", "  ")]
    return out


def pack_pdf(pack: dict[str, Any]) -> bytes:
    lines = _lines(pack)
    pages = [lines[i : i + ROWS] for i in range(0, len(lines), ROWS)] or [[]]
    footer_label = TITLES.get(pack.get("language", "fr"), TITLES["fr"])[4]
    digest = hash_pack(pack)

    objects: list[bytes] = []  # object n is objects[n - 1]

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # filled in below
    pages_obj = add(b"")
    regular = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")
    bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold /Encoding /WinAnsiEncoding >>")
    kids: list[int] = []
    for n, page in enumerate(pages, start=1):
        ops = [b"BT", f"{LEADING:.1f} TL".encode(), f"{MARGIN} {PAGE_H - MARGIN} Td".encode()]
        current = None
        for text, is_bold in page:
            font = b"/F2" if is_bold else b"/F1"
            if font != current:
                ops.append(font + f" {SIZE} Tf".encode())
                current = font
            ops.append(_encode(text) + b" Tj T*")
        ops += [b"ET", b"BT", b"/F1 7 Tf", f"{MARGIN} {MARGIN - 20} Td".encode()]
        ops += [
            _encode(f"{pack['case_id']} - pack sha256 {digest} - {footer_label} {n}/{len(pages)}") + b" Tj"
        ]
        ops.append(b"ET")
        stream = b"\n".join(ops)
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_dict = (
            f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 {regular} 0 R /F2 {bold} 0 R >> >> /Contents {content} 0 R >>"
        )
        kids.append(add(page_dict.encode()))
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode()
    objects[pages_obj - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    )
    info = add(b"<< /Producer (Article 15 desk) /Title " + _encode(f"{pack['case_id']}") + b" >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n".encode()
    out += f"startxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
