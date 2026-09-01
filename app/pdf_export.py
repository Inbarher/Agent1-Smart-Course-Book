from __future__ import annotations
import re
import textwrap
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.colors import HexColor
from .config import DATA_DIR
from .rtl import visual_rtl_for_pdf

FONT = "Hebrew"
INK = HexColor("#18212f")
ACCENT = HexColor("#be2bb9")
BRAND = HexColor("#08756e")

def normalise_formula(value: str) -> str:
    symbols = {r"\alpha":"α",r"\beta":"β",r"\gamma":"γ",r"\theta":"θ",r"\lambda":"λ",r"\mu":"μ",r"\phi":"φ",r"\varphi":"φ",r"\psi":"ψ",r"\sigma":"σ",r"\chi":"χ",r"\leq":"≤",r"\le":"≤",r"\geq":"≥",r"\ge":"≥",r"\in":"∈",r"\iff":"⇔",r"\to":"→",r"\subseteq":"⊆",r"\forall":"∀",r"\exists":"∃",r"\cup":"∪",r"\cap":"∩",r"\mid":"|"}
    value = re.sub(r"\\text\{([^}]*)\}", r"\1", value)
    value = re.sub(r"\\bar\{([^}]*)\}", r"\1̄", value)
    for latex, symbol in symbols.items(): value = value.replace(latex, symbol)
    return value.replace(r"\{", "{").replace(r"\}", "}").replace(r"\langle", "⟨").replace(r"\rangle", "⟩").replace("{", "").replace("}", "").replace(r"\,", " ")
def register_font():
    for candidate in (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")):
        if candidate.exists():
            pdfmetrics.registerFont(TTFont(FONT, str(candidate))); return
    raise RuntimeError("A Hebrew-capable TrueType font is required. Windows Arial was not found.")

def export_notebook(lecture_id: str, title: str, markdown: str) -> Path:
    register_font(); directory=DATA_DIR/"generated"/lecture_id; directory.mkdir(parents=True,exist_ok=True)
    path=directory/"notebook.pdf"; c=Canvas(str(path),pagesize=A4); width,height=A4; y=height-48
    c.setTitle(title)
    in_code = False; in_formula = False
    def new_page():
        nonlocal y
        c.showPage(); y=height-48
    def right_lines(line: str, size: int, color, gap: int):
        nonlocal y
        for part in textwrap.wrap(line, width=78, break_long_words=False, break_on_hyphens=False) or [""]:
            if y < 54: new_page()
            c.setFillColor(color); c.setFont(FONT, size); c.drawRightString(width-44, y, visual_rtl_for_pdf(part)); y -= gap
    for raw in markdown.splitlines():
        if raw.startswith("```"):
            in_code = not in_code
            continue
        if raw.strip().startswith("$$") and raw.strip().endswith("$$") and len(raw.strip()) > 4:
            if y < 64: new_page()
            c.setFillColor(BRAND); c.setFont(FONT, 13); c.drawCentredString(width/2, y, normalise_formula(raw.strip()[2:-2].strip())); y -= 22
            continue
        if raw.strip() == "$$": in_formula = not in_formula; continue
        if not raw.strip(): y -= 5; continue
        heading = raw.lstrip().startswith("#"); code = in_code or raw.startswith("    ")
        line = re.sub(r"[*`#>|]", "", raw).strip()
        if heading:
            level = len(raw) - len(raw.lstrip("#")); y -= 8
            right_lines(line, 18 if level == 1 else (14 if level == 2 else 12), INK if level < 3 else ACCENT, 24 if level < 3 else 19)
        elif in_formula:
            if y < 64: new_page()
            c.setFillColor(BRAND); c.setFont(FONT, 13); c.drawCentredString(width/2, y, normalise_formula(line)); y -= 22
        elif code:
            if y < 54: new_page()
            c.setFillColor(INK); c.setFont(FONT, 9); c.drawString(44, y, line[:105]); y -= 14
        else:
            right_lines(line, 11, INK, 16)
    c.setFillColor(HexColor("#7a8691")); c.setFont(FONT,8); c.drawCentredString(width/2,28,"The Smart Course Book")
    c.save(); return path
