from __future__ import annotations
import re

HEBREW = re.compile(r"[\u0590-\u05ff]")
LTR_TOKEN = re.compile(r"(?:[A-Za-z0-9]|[()\[\]{}<>+*/=._-])")

def rtl_html(text: str) -> str:
    """Wrap potentially bidi-unstable technical runs in directional isolation."""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"(?<![\w])([A-Za-z][\w.+#/-]*|\d+(?:\.\d+)?|O\([^)]*\))", r'<bdi dir="ltr">\1</bdi>', escaped)

def visual_rtl_for_pdf(text: str) -> str:
    """Small dependency-free visual bidi transform for ReportLab's LTR text painter.
    LTR runs remain intact; Hebrew runs and run order are positioned visually RTL.
    """
    parts = re.findall(r"[\u0590-\u05ff]+|[A-Za-z0-9][A-Za-z0-9._+*/=()\[\]{}<> -]*|\s+|[^\s]", text)
    transformed = []
    for p in parts:
        transformed.append(p[::-1] if HEBREW.search(p) else p)
    return "".join(reversed(transformed))
