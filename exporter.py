import io, zipfile

def make_zip(page_html: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.html", page_html)
    return buf.getvalue()