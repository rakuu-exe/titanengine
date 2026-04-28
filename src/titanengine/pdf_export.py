from datetime import datetime


PAGE_WIDTH = 595
PAGE_HEIGHT = 842
LEFT_MARGIN = 54
TOP_MARGIN = 54
LINE_HEIGHT = 14
MAX_CHARS_PER_LINE = 88


def plain_text_from_markdown(markdown_text):
    lines = []
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip().upper()
        elif stripped.startswith("- "):
            stripped = "- " + stripped[2:].strip()
        lines.append(stripped)
    return "\n".join(lines)


def wrap_line(line, width=MAX_CHARS_PER_LINE):
    if not line:
        return [""]
    words = line.split(" ")
    wrapped = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= width:
            current = candidate
            continue
        if current:
            wrapped.append(current)
        while len(word) > width:
            wrapped.append(word[:width])
            word = word[width:]
        current = word
    if current:
        wrapped.append(current)
    return wrapped


def pdf_escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def encode_pdf_text(text):
    return text.encode("cp1252", errors="replace").decode("cp1252")


def paginate_text(text):
    pages = []
    current = []
    max_lines = int((PAGE_HEIGHT - TOP_MARGIN * 2) / LINE_HEIGHT)
    for source_line in text.splitlines():
        for line in wrap_line(source_line):
            current.append(line)
            if len(current) >= max_lines:
                pages.append(current)
                current = []
    if current:
        pages.append(current)
    return pages or [[""]]


def build_content_stream(lines):
    parts = ["BT", "/F1 10 Tf", f"{LEFT_MARGIN} {PAGE_HEIGHT - TOP_MARGIN} Td"]
    first = True
    for line in lines:
        if first:
            first = False
        else:
            parts.append(f"0 -{LINE_HEIGHT} Td")
        parts.append(f"({pdf_escape(encode_pdf_text(line))}) Tj")
    parts.append("ET")
    return "\n".join(parts).encode("cp1252", errors="replace")


def write_text_pdf(path, title, markdown_text):
    text = plain_text_from_markdown(markdown_text)
    pages = paginate_text(text)
    objects = []

    def add_object(body):
        objects.append(body)
        return len(objects)

    font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for page_lines in pages:
        stream = build_content_stream(page_lines)
        content_id = add_object(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")
        page_id = add_object(
            (
                f"<< /Type /Page /Parent {{pages_id}} 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode("ascii")
        )
        page_ids.append(page_id)

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    pages_id = add_object(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii"))
    catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii"))
    info_id = add_object(
        (
            "<< "
            f"/Title ({pdf_escape(encode_pdf_text(title))}) "
            f"/Creator (Titan Engine) "
            f"/CreationDate (D:{datetime.now():%Y%m%d%H%M%S}) "
            ">>"
        ).encode("cp1252", errors="replace")
    )

    resolved_objects = []
    for body in objects:
        if b"{pages_id}" in body:
            body = body.replace(b"{pages_id}", str(pages_id).encode("ascii"))
        resolved_objects.append(body)

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(resolved_objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")

    xref_at = len(output)
    output.extend(f"xref\n0 {len(resolved_objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            "trailer\n"
            f"<< /Size {len(resolved_objects) + 1} /Root {catalog_id} 0 R /Info {info_id} 0 R >>\n"
            "startxref\n"
            f"{xref_at}\n"
            "%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(output)
