from __future__ import annotations
import html, json, mimetypes, os, re, shutil, ssl, time, uuid
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from . import db
from pypdf import PdfReader
from .config import DATA_DIR, ALLOWED, LOGGER, MAX_UPLOAD_BYTES

def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return str(uuid.uuid4())
def ensure(value, message):
    if not value: raise ValueError(message)

class LocalStorage:
    def save(self, lecture_id: str, kind: str, uploaded) -> tuple[str, str]:
        ensure(kind in ALLOWED, "Unsupported material type")
        ensure(uploaded.filename, "Choose a file before uploading")
        suffix = Path(uploaded.filename).suffix.lower(); ensure(suffix in ALLOWED[kind], f"Unsupported {kind} format: {suffix or 'no extension'}")
        target = DATA_DIR / "materials" / lecture_id / f"{uid()}{suffix}"; target.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with open(target, "wb") as stream: shutil.copyfileobj(uploaded.file, stream)
        size = target.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            target.unlink(missing_ok=True); raise ValueError("The file exceeds the 250 MB local upload limit")
        if size == 0:
            target.unlink(missing_ok=True); raise ValueError("The selected file is empty")
        return str(target.relative_to(DATA_DIR)), mimetypes.guess_type(uploaded.filename)[0] or "application/octet-stream"

storage = LocalStorage()

@lru_cache(maxsize=1)
def trusted_ca_bundle() -> str:
    """Build a local CA bundle from certifi plus Windows' trusted root store.

    This keeps HTTPS verification enabled while allowing a locally trusted
    school/company proxy certificate to be recognized by Gemini's HTTP client.
    """
    import certifi
    target = DATA_DIR / "runtime" / "trusted-ca-bundle.pem"
    target.parent.mkdir(parents=True, exist_ok=True)
    bundle = Path(certifi.where()).read_text(encoding="ascii")
    if os.name == "nt":
        try:
            windows_roots = ssl.enum_certificates("ROOT")
            for certificate, encoding, _trust in windows_roots:
                if encoding == "x509_asn":
                    bundle += "\n" + ssl.DER_cert_to_PEM_cert(certificate)
        except OSError:
            LOGGER.warning("Could not read the Windows trusted root store; using certifi only")
    target.write_text(bundle, encoding="ascii")
    return str(target)

class GeminiProvider:
    """Current Gemini Interactions API provider with temporary Files API inputs."""
    def __init__(self): self.model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    @property
    def available(self): return bool(os.getenv("GEMINI_API_KEY")) and os.getenv("SMART_COURSE_DISABLE_GEMINI") != "1"
    def analyze(self, prompt: str, files: list[Path], schema: dict) -> dict:
        if not self.available: raise RuntimeError("Gemini is not configured. Add GEMINI_API_KEY to .env to enable AI analysis.")
        try:
            from google import genai
            import certifi
        except ModuleNotFoundError as exc: raise RuntimeError("Gemini SDK is missing. Start the app with .venv\\Scripts\\python.exe run.py.") from exc
        ssl_context = ssl.create_default_context(cafile=trusted_ca_bundle())
        client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options={"timeout": 600000, "client_args": {"verify": ssl_context}},
        )
        uploaded = []
        try:
            for path in files:
                uploaded_file = client.files.upload(file=str(path))
                uploaded.append(uploaded_file)
                # Gemini processes media asynchronously, while documents can be
                # provided to Interactions immediately. Waiting on document
                # preparation can otherwise leave the local request stalled.
                if (uploaded_file.mime_type or "").startswith(("audio/", "video/")):
                    self._wait_until_ready(client, uploaded_file)
            parts = [{"type":"text", "text":prompt}]
            for uploaded_file in uploaded:
                parts.append({"type": self._input_type(uploaded_file.mime_type), "uri": uploaded_file.uri, "mime_type": uploaded_file.mime_type})
            response = client.interactions.create(model=self.model, input=parts, response_format={"type":"text", "mime_type":"application/json", "schema":schema})
            return json.loads(response.output_text)
        except Exception as exc:
            LOGGER.exception("Gemini analysis failed with model %s", self.model)
            raise RuntimeError(f"Gemini analysis failed: {exc}") from exc
        finally:
            for uploaded_file in uploaded:
                try: client.files.delete(name=uploaded_file.name)
                except Exception: LOGGER.warning("Could not delete temporary Gemini file %s", getattr(uploaded_file,"name","unknown"))

    @staticmethod
    def _input_type(mime_type: str | None) -> str:
        mime_type = mime_type or "application/octet-stream"
        if mime_type == "application/pdf" or mime_type.startswith("text/"): return "document"
        if mime_type.startswith("audio/"): return "audio"
        if mime_type.startswith("video/"): return "video"
        return "document"

    @staticmethod
    def _wait_until_ready(client, uploaded_file):
        state = getattr(uploaded_file, "state", None)
        deadline = time.monotonic() + 120
        while state and getattr(state, "name", str(state)) not in {"ACTIVE", "SUCCEEDED"}:
            if time.monotonic() >= deadline: raise RuntimeError("Gemini file processing timed out")
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)
            state = getattr(uploaded_file, "state", None)
        if state and getattr(state, "name", str(state)) == "FAILED": raise RuntimeError("Gemini could not process an uploaded source file")

LECTURE_ANALYSIS_SCHEMA = {
    "type":"object", "additionalProperties":False,
    "properties": {
        "sections":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"title":{"type":"string"},"summary":{"type":"string"},"explanation":{"type":"string"},"prerequisite_background":{"type":"string"},"examples":{"type":"array","items":{"type":"string"}},"key_points":{"type":"array","items":{"type":"string"}},"formulas":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"latex":{"type":"string"},"explanation":{"type":"string"}},"required":["latex","explanation"]}},"algorithms":{"type":"array","items":{"type":"string"}},"proof_outline":{"type":"array","items":{"type":"string"}},"slide_numbers":{"type":"array","items":{"type":"integer"}},"transcript_segment_ids":{"type":"array","items":{"type":"string"}},"certainty":{"type":"string","enum":["source_fact","inference","uncertain"]}},"required":["title","summary","explanation","prerequisite_background","examples","key_points","formulas","algorithms","proof_outline","slide_numbers","transcript_segment_ids","certainty"]}},
        "lecturer_notes":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"note":{"type":"string"},"transcript_segment_ids":{"type":"array","items":{"type":"string"}},"kind":{"type":"string","enum":["emphasis","exam_comment","warning","tip"]}},"required":["note","transcript_segment_ids","kind"]}},
        "visual_findings":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"slide_number":{"type":"integer"},"kind":{"type":"string"},"description":{"type":"string"},"related_section_title":{"type":"string"},"certainty":{"type":"string","enum":["source_fact","inference","uncertain"]}},"required":["slide_number","kind","description","related_section_title","certainty"]}},
        "generated_diagrams":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"title":{"type":"string"},"description":{"type":"string"},"related_section_title":{"type":"string"},"nodes":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"id":{"type":"string"},"label":{"type":"string"}},"required":["id","label"]}},"edges":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{"from_id":{"type":"string"},"to_id":{"type":"string"},"label":{"type":"string"}},"required":["from_id","to_id","label"]}},"slide_numbers":{"type":"array","items":{"type":"integer"}},"transcript_segment_ids":{"type":"array","items":{"type":"string"}},"certainty":{"type":"string","enum":["source_fact","inference","uncertain"]}},"required":["title","description","related_section_title","nodes","edges","slide_numbers","transcript_segment_ids","certainty"]}},
        "uncertainties":{"type":"array","items":{"type":"string"}}
    }, "required":["sections","lecturer_notes","visual_findings","generated_diagrams","uncertainties"]
}

EXAM_FOCUS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "sections": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {
                "title": {"type": "string"},
                "recall_points": {"type": "array", "items": {"type": "string"}},
                "formulas": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                    "properties": {"latex": {"type": "string"}, "explanation": {"type": "string"}},
                    "required": ["latex", "explanation"]}},
                "algorithms": {"type": "array", "items": {"type": "string"}},
                "common_confusions": {"type": "array", "items": {"type": "string"}},
                "slide_numbers": {"type": "array", "items": {"type": "integer"}},
                "certainty": {"type": "string", "enum": ["source_fact", "inference", "uncertain"]}
            },
            "required": ["title", "recall_points", "formulas", "algorithms", "common_confusions", "slide_numbers", "certainty"]
        }},
        "lecturer_exam_notes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {"note": {"type": "string"}, "transcript_segment_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["note", "transcript_segment_ids"]
        }},
        "uncertainties": {"type": "array", "items": {"type": "string"}}
    }, "required": ["sections", "lecturer_exam_notes", "uncertainties"]
}

RECORDING_TRANSCRIPTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "segments": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {
                "start_seconds": {"type": "number", "minimum": 0},
                "end_seconds": {"type": "number", "minimum": 0},
                "timestamp_status": {"type": "string", "enum": ["exact", "estimated", "unavailable"]},
                "text_content": {"type": "string"}
            }, "required": ["start_seconds", "end_seconds", "timestamp_status", "text_content"]
        }}
    }, "required": ["segments"]
}

ALIGNMENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "segments": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {
                "segment_id": {"type": "string"},
                "slide_numbers": {"type": "array", "items": {"type": "integer"}},
                "topic": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "relationship": {"type": "string", "enum": ["explains", "introduces", "example", "references_visual", "multiple_slides", "no_specific_slide", "uncertain"]}
            },
            "required": ["segment_id", "slide_numbers", "topic", "confidence", "relationship"]
        }}
    }, "required": ["segments"]
}

FORMULA_REPAIR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"latex": {"type": "string"}}, "required": ["latex"]
}

MATH_AUDIT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"repairs": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "properties": {"index": {"type": "integer"}, "latex": {"type": "string"}, "status": {"type": "string", "enum": ["valid", "repaired"]}},
        "required": ["index", "latex", "status"]}}}, "required": ["repairs"]
}

# Content decisions deliberately live apart from the rendering contract below.
# This lets us evolve the notebook's writing style without changing the rules
# that decide whether a string is displayed as LaTeX.
CONTENT_LANGUAGE_POLICY = """Write the notebook and the exam review in clear, natural Hebrew. Hebrew is the default language for titles, explanations, examples, algorithm steps, labels, and prose. Minimize English: retain an English term only when it is a genuinely useful established technical name, code identifier, or notation. On its first useful appearance, write the Hebrew name followed by the English term in parentheses, for example 'רדוקציה עצמית (Self-Reduction)'; after that, prefer the Hebrew name. Do not use English merely for style, headings, connective prose, or a translation that Hebrew can express clearly. Keep formal course names, code, and standard mathematical symbols exact."""

MATH_RENDERING_CONTRACT = """Mathematical rendering is a separate contract from writing style. Put every complete or stand-alone equation, definition, or algorithmic notation only in a formulas object. Its latex value contains valid LaTeX notation only and never prose, English clauses, a course name, or a technical term. Its explanation is one short Hebrew sentence outside the formula. In normal Hebrew prose, use single dollar delimiters only for a compact symbolic expression that is grammatically part of that sentence, for example $x \\in L$, $O(n^2)$ or $G_\\phi$. Do not delimit names or technical terms such as BFS, Self-Reduction, or polynomial-time algorithm. Every JSON backslash in LaTeX must be escaped as \\\\ so it remains a literal backslash after JSON parsing."""

def create_course(data):
    ensure(data.get("name", "").strip(), "Course name is required"); item = {"id": uid(), "name": data["name"].strip(), "code": data.get("code", ""), "semester": data.get("semester", ""), "academic_year": data.get("academic_year", ""), "description": data.get("description", ""), "created_at": now(), "updated_at": now()}
    with db.connection() as c: c.execute("INSERT INTO courses VALUES (:id,:name,:code,:semester,:academic_year,:description,:created_at,:updated_at)", item)
    return item

def create_lecture(course_id, data):
    ensure(db.row("SELECT id FROM courses WHERE id=?", (course_id,)), "Course not found"); ensure(data.get("title", "").strip(), "Title is required")
    item = {"id": uid(), "course_id": course_id, "title": data["title"].strip(), "type": data.get("type", "lecture"), "lecture_date": data.get("lecture_date", ""), "number": data.get("number") or None, "status":"ready", "created_at":now(), "updated_at":now()}
    with db.connection() as c: c.execute("INSERT INTO lectures VALUES (:id,:course_id,:title,:type,:lecture_date,:number,:status,:created_at,:updated_at)", item)
    return item

def update_course(course_id, data):
    current=db.row("SELECT * FROM courses WHERE id=?", (course_id,)); ensure(current, "Course not found")
    item={key: (data.get(key, current[key]) or "").strip() for key in ("name","code","semester","academic_year","description")}
    ensure(item["name"], "Course name is required")
    with db.connection() as c: c.execute("UPDATE courses SET name=?,code=?,semester=?,academic_year=?,description=?,updated_at=? WHERE id=?", (*item.values(),now(),course_id))
    return db.row("SELECT * FROM courses WHERE id=?", (course_id,))

def update_lecture(lecture_id, data):
    current=db.row("SELECT * FROM lectures WHERE id=?", (lecture_id,)); ensure(current, "Lecture not found")
    title=(data.get("title",current["title"]) or "").strip(); ensure(title,"Title is required")
    kind=data.get("type",current["type"]); ensure(kind in ("lecture","exercise"),"Invalid lecture type")
    with db.connection() as c: c.execute("UPDATE lectures SET title=?,type=?,lecture_date=?,number=?,updated_at=? WHERE id=?",(title,kind,data.get("lecture_date",current["lecture_date"]),data.get("number",current["number"]) or None,now(),lecture_id))
    return db.row("SELECT * FROM lectures WHERE id=?", (lecture_id,))

def delete_lecture_record(lecture_id):
    ensure(db.row("SELECT * FROM lectures WHERE id=?",(lecture_id,)),"Lecture not found")
    # Delete app records only. Original stored files deliberately remain untouched.
    with db.connection() as c:
        for table in ("visual_elements",): c.execute(f"DELETE FROM {table} WHERE slide_id IN (SELECT id FROM slides WHERE lecture_id=?)",(lecture_id,))
        for table in ("slides","alignments","source_references","outputs","jobs","transcript_segments","materials","lecture_knowledge","notebook_manual_edits","exam_manual_edits"): c.execute(f"DELETE FROM {table} WHERE lecture_id=?",(lecture_id,))
        c.execute("DELETE FROM lectures WHERE id=?",(lecture_id,))
    LOGGER.info("Deleted lecture records %s; source files retained", lecture_id)

def delete_course_record(course_id):
    ensure(db.row("SELECT * FROM courses WHERE id=?",(course_id,)),"Course not found")
    for lecture in db.rows("SELECT id FROM lectures WHERE course_id=?",(course_id,)): delete_lecture_record(lecture["id"])
    with db.connection() as c:
        c.execute("DELETE FROM course_outputs WHERE course_id=?", (course_id,))
        c.execute("DELETE FROM courses WHERE id=?",(course_id,))
    LOGGER.info("Deleted course records %s; source files retained", course_id)

def app_settings() -> dict:
    """Return persistent local preferences with safe defaults for new installs."""
    row = db.row("SELECT value FROM app_settings WHERE key='auto_reprocess_on_upload'")
    return {"auto_reprocess_on_upload": not row or row["value"].lower() == "true"}

def update_app_settings(data: dict) -> dict:
    enabled = bool(data.get("auto_reprocess_on_upload", True))
    with db.connection() as c:
        c.execute("INSERT INTO app_settings (key,value,updated_at) VALUES ('auto_reprocess_on_upload',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (str(enabled).lower(), now()))
    return app_settings()

def get_notebook_manual_edit(lecture_id: str) -> dict | None:
    return db.row("SELECT * FROM notebook_manual_edits WHERE lecture_id=?", (lecture_id,))

def _sanitize_manual_notebook_html(value: str) -> str:
    """Keep a local rich-text edit while removing executable browser content."""
    value = str(value or "").strip()
    ensure(value, "לא ניתן לשמור מחברת ריקה")
    ensure(len(value) <= 750_000, "המחברת הערוכה גדולה מדי")
    value = re.sub(r"<\s*(script|style|iframe|object|embed)\b[^>]*>[\s\S]*?<\s*/\s*\1\s*>", "", value, flags=re.I)
    value = re.sub(r"<\s*(script|style|iframe|object|embed)\b[^>]*?/?>", "", value, flags=re.I)
    value = re.sub(r"\son\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", "", value, flags=re.I)
    value = re.sub(r"javascript\s*:", "", value, flags=re.I)
    return value

def save_notebook_manual_edit(lecture_id: str, base_content: str, html_content: str) -> dict:
    output = db.row("SELECT content FROM outputs WHERE lecture_id=? AND kind='notebook'", (lecture_id,))
    ensure(output, "יש ליצור מחברת לפני עריכה")
    ensure(base_content == output["content"], "המחברת השתנתה מאז תחילת העריכה; רענני ובחרי איזו גרסה לשמור")
    html_content = _sanitize_manual_notebook_html(html_content)
    with db.connection() as c:
        c.execute("INSERT INTO notebook_manual_edits (lecture_id,base_content,html_content,pending_content,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(lecture_id) DO UPDATE SET base_content=excluded.base_content,html_content=excluded.html_content,pending_content=NULL,updated_at=excluded.updated_at", (lecture_id, base_content, html_content, None, now()))
    return get_notebook_manual_edit(lecture_id)

def resolve_notebook_manual_edit(lecture_id: str, choice: str) -> dict:
    edit = get_notebook_manual_edit(lecture_id)
    ensure(edit and edit.get("pending_content"), "אין עדכון שממתין להחלטה")
    ensure(choice in {"keep", "adopt"}, "בחירה לא תקינה")
    if choice == "adopt":
        with db.connection() as c: c.execute("DELETE FROM notebook_manual_edits WHERE lecture_id=?", (lecture_id,))
        return {"choice": "adopt"}
    with db.connection() as c:
        c.execute("UPDATE notebook_manual_edits SET base_content=?,pending_content=NULL,updated_at=? WHERE lecture_id=?", (edit["pending_content"], now(), lecture_id))
    return {"choice": "keep", "edit": get_notebook_manual_edit(lecture_id)}

def get_exam_manual_edit(lecture_id: str) -> dict | None:
    return db.row("SELECT * FROM exam_manual_edits WHERE lecture_id=?", (lecture_id,))

def save_exam_manual_edit(lecture_id: str, base_content: str, html_content: str) -> dict:
    output = db.row("SELECT content FROM outputs WHERE lecture_id=? AND kind='exam_focus'", (lecture_id,))
    ensure(output, "יש ליצור סיכום ממוקד לפני עריכה")
    ensure(base_content == output["content"], "הסיכום השתנה מאז תחילת העריכה; רענני ובחרי איזו גרסה לשמור")
    html_content = _sanitize_manual_notebook_html(html_content)
    with db.connection() as c:
        c.execute("INSERT INTO exam_manual_edits (lecture_id,base_content,html_content,pending_content,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(lecture_id) DO UPDATE SET base_content=excluded.base_content,html_content=excluded.html_content,pending_content=NULL,updated_at=excluded.updated_at", (lecture_id, base_content, html_content, None, now()))
    return get_exam_manual_edit(lecture_id)

def resolve_exam_manual_edit(lecture_id: str, choice: str) -> dict:
    edit = get_exam_manual_edit(lecture_id)
    ensure(edit and edit.get("pending_content"), "אין עדכון שממתין להחלטה")
    ensure(choice in {"keep", "adopt"}, "בחירה לא תקינה")
    if choice == "adopt":
        with db.connection() as c: c.execute("DELETE FROM exam_manual_edits WHERE lecture_id=?", (lecture_id,))
        return {"choice": "adopt"}
    with db.connection() as c:
        c.execute("UPDATE exam_manual_edits SET base_content=?,pending_content=NULL,updated_at=? WHERE lecture_id=?", (edit["pending_content"], now(), lecture_id))
    return {"choice": "keep", "edit": get_exam_manual_edit(lecture_id)}

def upload_material(lecture_id, kind, uploaded):
    ensure(db.row("SELECT id FROM lectures WHERE id=?", (lecture_id,)), "Lecture not found")
    had_exam_focus = bool(db.row("SELECT 1 FROM outputs WHERE lecture_id=? AND kind='exam_focus'", (lecture_id,)))
    path, mime = storage.save(lecture_id, kind, uploaded); item={"id":uid(),"lecture_id":lecture_id,"kind":kind,"original_name":uploaded.filename,"stored_path":path,"mime_type":mime,"created_at":now()}
    with db.connection() as c: c.execute("INSERT INTO materials VALUES (:id,:lecture_id,:kind,:original_name,:stored_path,:mime_type,:created_at)",item)
    if app_settings()["auto_reprocess_on_upload"]:
        try:
            process(lecture_id)
            if had_exam_focus:
                generate_exam_focus(lecture_id, regenerate=True)
            item["auto_processing"] = "completed"
        except Exception as exc:
            # The upload itself remains successful and recoverable. The user can
            # run processing manually after fixing an AI/provider issue.
            LOGGER.exception("Automatic processing after upload failed for %s", lecture_id)
            item["auto_processing"] = "failed"
            item["auto_processing_error"] = str(exc)
    else:
        item["auto_processing"] = "disabled"
    return item

def delete_material_record(material_id):
    material=db.row("SELECT * FROM materials WHERE id=?",(material_id,)); ensure(material,"Material not found")
    with db.connection() as c:
        c.execute("DELETE FROM transcript_segments WHERE material_id=?",(material_id,))
        c.execute("DELETE FROM materials WHERE id=?",(material_id,))
    LOGGER.info("Removed material record %s; source file retained", material_id)

def extract_presentation(lecture_id, material):
    source=DATA_DIR / material["stored_path"]
    try: reader=PdfReader(str(source))
    except Exception as exc:
        LOGGER.exception("Could not parse PDF %s", source); raise ValueError("Could not read this PDF presentation") from exc
    with db.connection() as c:
        c.execute("DELETE FROM visual_elements WHERE slide_id IN (SELECT id FROM slides WHERE lecture_id=?)",(lecture_id,))
        c.execute("DELETE FROM slides WHERE lecture_id=?",(lecture_id,))
        for number,page in enumerate(reader.pages,1):
            text=(page.extract_text() or "").strip()
            lines=[line.strip() for line in text.splitlines() if line.strip()]
            title=lines[0][:180] if lines else f"Slide {number}"
            slide_id=uid()
            metadata={"material_id":material["id"],"page":number,"text_available":bool(text)}
            c.execute("INSERT INTO slides VALUES (?,?,?,?,?,?,?)",(slide_id,lecture_id,number,title,text,None,json.dumps(metadata,ensure_ascii=False)))
            c.execute("INSERT INTO visual_elements VALUES (?,?,?,?,?,?)",(uid(),slide_id,"source_slide","Original presentation page",f"Slide {number}","Slide viewer"))
    LOGGER.info("Extracted %s slides from material %s",len(reader.pages),material["id"])
    return len(reader.pages)

TIMESTAMP = re.compile(r"(?:(\d{1,2}):)?(\d{2}):(\d{2})(?:[,.](\d{1,3}))?")
SRT_BLOCK = re.compile(r"(?:^|\n)\s*(?:\d+\s*\n)?\s*([^\n]+?)\s*-->\s*([^\n]+?)\s*\n(.*?)(?=\n\s*\n|\Z)", re.DOTALL)

def timestamp_seconds(value: str) -> float | None:
    match = TIMESTAMP.search(value)
    if not match: return None
    hours, minutes, seconds, millis = match.groups()
    return (int(hours or 0) * 3600) + (int(minutes) * 60) + int(seconds) + int((millis or "0").ljust(3, "0")[:3]) / 1000

def parse_transcript_text(text: str, suffix: str) -> list[dict]:
    """Parse regular text plus SRT/VTT cues without inventing timestamps."""
    text = text.replace("\ufeff", "").replace("\r\n", "\n").strip()
    if suffix in {".srt", ".vtt"}:
        text = re.sub(r"^WEBVTT[^\n]*\n", "", text, flags=re.IGNORECASE)
        cues = []
        for number, match in enumerate(SRT_BLOCK.finditer(text), 1):
            body = re.sub(r"<[^>]+>", "", match.group(3)).strip()
            if body:
                cues.append({"segment_number": number, "start_seconds": timestamp_seconds(match.group(1)), "end_seconds": timestamp_seconds(match.group(2)), "text_content": body})
        if cues: return cues
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    if not paragraphs and text: paragraphs = [text]
    return [{"segment_number": number, "start_seconds": None, "end_seconds": None, "text_content": paragraph} for number, paragraph in enumerate(paragraphs, 1)]

def extract_transcript(lecture_id: str, material: dict) -> list[dict]:
    source = DATA_DIR / material["stored_path"]
    try: raw = source.read_text(encoding="utf-8", errors="replace")
    except OSError as exc: raise ValueError(f"Could not read transcript: {material['original_name']}") from exc
    segments = parse_transcript_text(raw, Path(material["original_name"]).suffix.lower())
    with db.connection() as c:
        c.execute("DELETE FROM transcript_segments WHERE material_id=?", (material["id"],))
        for segment in segments:
            locator = f"{material['original_name']} · segment {segment['segment_number']}"
            if segment["start_seconds"] is not None: locator += f" · {segment['start_seconds']:.3f}s"
            c.execute("INSERT INTO transcript_segments VALUES (?,?,?,?,?,?,?,?,?)", (uid(), lecture_id, material["id"], segment["segment_number"], segment["start_seconds"], segment["end_seconds"], segment["text_content"], locator, now()))
    LOGGER.info("Extracted %s transcript segments from material %s", len(segments), material["id"])
    return segments

def inspect_recording(lecture_id: str, material: dict) -> dict:
    source = DATA_DIR / material["stored_path"]
    ensure(source.exists(), f"Source recording not found: {material['original_name']}")
    info = {"material_id": material["id"], "filename": material["original_name"], "mime_type": material["mime_type"], "bytes": source.stat().st_size, "status": "ready_for_transcription"}
    LOGGER.info("Recording %s is ready for provider transcription", material["id"])
    return info

def validate_recording_transcript(raw: dict) -> list[dict]:
    """Normalize provider transcript cues without manufacturing timestamps."""
    segments = []
    for item in raw.get("segments", []):
        text = str(item.get("text_content") or "").strip()
        if not text:
            continue
        status = item.get("timestamp_status")
        if status not in {"exact", "estimated", "unavailable"}:
            status = "unavailable"
        try:
            start, end = float(item.get("start_seconds", 0)), float(item.get("end_seconds", 0))
        except (TypeError, ValueError):
            start, end, status = 0, 0, "unavailable"
        if start < 0 or end < start:
            start, end, status = 0, 0, "unavailable"
        segments.append({"segment_number": len(segments) + 1, "start_seconds": None if status == "unavailable" else start,
            "end_seconds": None if status == "unavailable" else end, "timestamp_status": status, "text_content": text})
    return segments

def extract_recording_transcript(lecture_id: str, material: dict) -> dict:
    """Transcribe one audio/video source with Gemini and persist source-addressable cues."""
    source = DATA_DIR / material["stored_path"]
    ensure(source.exists(), f"Source recording not found: {material['original_name']}")
    prompt = """Transcribe only the spoken content of this lecture recording/video into Hebrew when spoken in Hebrew; preserve technical terms and code exactly. Split it into coherent, readable segments. Do not add summaries, speaker labels, claims, explanations, or exam emphasis. Use exact timestamps only when the media supports them; use estimated only when clearly approximate; otherwise set timestamp_status unavailable and set both timestamp numbers to 0."""
    raw = GeminiProvider().analyze(prompt, [source], RECORDING_TRANSCRIPTION_SCHEMA)
    segments = validate_recording_transcript(raw)
    ensure(segments, "The recording could not be transcribed into usable text")
    with db.connection() as c:
        c.execute("DELETE FROM transcript_segments WHERE material_id=?", (material["id"],))
        for segment in segments:
            locator = f"{material['original_name']} · segment {segment['segment_number']}"
            if segment["start_seconds"] is not None:
                marker = "~" if segment["timestamp_status"] == "estimated" else ""
                locator += f" · {marker}{segment['start_seconds']:.3f}s"
            c.execute("INSERT INTO transcript_segments VALUES (?,?,?,?,?,?,?,?,?)", (uid(), lecture_id, material["id"], segment["segment_number"], segment["start_seconds"], segment["end_seconds"], segment["text_content"], locator, now()))
    LOGGER.info("Transcribed %s segments from recording %s", len(segments), material["id"])
    return {"material_id": material["id"], "filename": material["original_name"], "mime_type": material["mime_type"], "status": "transcribed", "segment_count": len(segments)}

def analyze_lecture_with_gemini(lecture: dict, materials: list[dict], transcript_segments: list[dict]) -> dict:
    source_paths = [DATA_DIR / material["stored_path"] for material in materials]
    ensure(all(path.exists() for path in source_paths), "One or more source files are missing")
    transcript_context = "\n".join(f"- segment_id={segment['id']} | {segment['source_locator']}: {segment['text_content']}" for segment in transcript_segments[:150]) or "No transcript was supplied."
    prompt = f"""You are a careful university lecture-analysis service. Analyze only the supplied lecture sources.
Lecture title: {lecture['title']}

Known transcript segments (may be incomplete):
{transcript_context}

Return Hebrew structured data according to the JSON schema. Coverage is more important than brevity: preserve every distinct source-supported definition, qualification, algorithm step, proof idea, example, formula, and lecturer emphasis in the appropriate section. Write a substantial teaching chapter, not a summary: each section's summary should orient the reader; explanation should develop intuition, the formal idea, and why it works in connected paragraphs; examples and proofs should retain the meaningful intermediate steps rather than only their conclusion. Explain prerequisite concepts briefly when they are needed for comprehension. A student should be able to learn from the notebook without repeatedly returning to the raw slides. Start with the intuition and why the subject matters, then make the formal detail understandable; add only small pedagogical bridges that help understanding, never new academic facts.

Content-language policy:
{CONTENT_LANGUAGE_POLICY}

Mathematical-rendering contract:
{MATH_RENDERING_CONTRACT}

Use only slide numbers visible in the supplied presentation and only transcript_segment_ids listed above. Never invent a lecturer statement, exam claim, slide number, formula, definition, proof, or visual detail. A lecturer note is allowed only when its transcript_segment_ids directly support it. Use certainty 'source_fact' only for directly supported claims; otherwise use 'inference' or 'uncertain'. A visual finding must describe an actual visual on the stated slide and name the section where it helps learning. If a source is insufficient, leave the relevant list empty and record the limitation in uncertainties."""
    return GeminiProvider().analyze(prompt, source_paths, LECTURE_ANALYSIS_SCHEMA)

def _valid_numbers(values, known):
    return sorted({value for value in values if isinstance(value, int) and value in known})

def _valid_ids(values, known):
    return [value for value in values if isinstance(value, str) and value in known]

def normalize_latex(value) -> str:
    """Canonicalize source notation into safe, renderable LaTeX."""
    latex = str(value or "").strip().strip("$").strip()
    # A model can accidentally emit JSON escape characters (\b, \t, \v)
    # instead of escaped LaTeX backslashes. Repair known command fragments
    # before applying ordinary notation normalization.
    control_repairs = {
        "\x08ar": r"\bar", "\x08igwedge": r"\bigwedge", "\x08igvee": r"\bigvee",
        "\x08le": r"\le", "\x08in": r"\in", "\x08subseteq": r"\subseteq",
        "\x08dots": r"\dots", "\x08v": r"\vee", "\text": r"\text",
        "\theta": r"\theta", "\to": r"\to", "\varphi": r"\varphi",
    }
    for broken, repaired in control_repairs.items():
        latex = latex.replace(broken, repaired)
    # JSON/Markdown can preserve an escaped underscore or duplicate the
    # backslash before a LaTeX command. Both forms are source encoding noise,
    # not part of the mathematical notation.
    latex = latex.replace(r"\_", "_")
    latex = re.sub(r"\\{2,}(?=(?:langle|rangle|phi|varphi|psi|alpha|beta|gamma|delta|chi|sigma|in|iff|leq|geq|le|ge|to|text|exists|forall|land|lor|vee|wedge|overline|subseteq|setminus|dots)\b)", lambda _match: "\\", latex)
    protected_text = []
    def keep_text(match):
        protected_text.append(match.group(0))
        return f"@@TEXT{len(protected_text) - 1}@@"
    latex = re.sub(r"\\text\{(?:[^{}]|\{[^{}]*\})*\}", keep_text, latex)
    latex = latex.replace(r"\*", "*").replace(r"\<", "<").replace(r"\>", ">")
    latex = re.sub(r"\*([A-Za-z])\*", r"\1", latex)
    latex = re.sub(r"^\\-\s*(?=\|)", "", latex)
    latex = re.sub(r"\\\\(iff|in|exists|forall|leq|geq|le|ge|to|mid|alpha|beta|gamma|delta|phi|psi|sigma|text)\b", r"\\\1", latex)
    latex = re.sub(r"\\lep\b", r"\\le_p", latex)
    latex = re.sub(r"<=_p\b", r"\\le_p", latex)
    latex = re.sub(r"\b([A-Z])(\d+)\b", r"\1_{\2}", latex)
    latex = re.sub(r"\bGphi\b", r"G_\\phi", latex)
    for word, command in (("alpha", r"\\alpha"), ("beta", r"\\beta"), ("gamma", r"\\gamma"), ("delta", r"\\delta"), ("phi", r"\\phi"), ("psi", r"\\psi"), ("sigma", r"\\sigma")):
        latex = re.sub(rf"(?<!\\)\b{word}\b", command, latex, flags=re.I)
    latex = re.sub(r"(?<!\\)\biff\b", r"\\iff", latex)
    latex = re.sub(r"(?<!\\)\bin\b", r"\\in", latex)
    latex = latex.replace(">=", r"\geq").replace("<=", r"\leq")
    latex = re.sub(r"(?<!\\)<\s*([^<>\n]*,[^<>\n]*)\s*>", r"\\langle \1 \\rangle", latex)
    latex = re.sub(r"\{\s*L\s*\|\s*(?:there exists a polynomial-time algorithm for\s+L|L\s+is solvable by a polynomial-time algorithm)\s*\}", r"\\{ L \\mid L \\text{ is solvable by a polynomial-time algorithm} \\}", latex, flags=re.I)
    latex = re.sub(r"\bthere exists\b", r"\\text{there exists}", latex, flags=re.I)
    latex = re.sub(r"\bf\s+runs in polynomial time\b", r"f \\text{ runs in polynomial time}", latex, flags=re.I)
    latex = re.sub(r"@@TEXT(\d+)@@", lambda match: protected_text[int(match.group(1))], latex)
    return latex

def normalize_formula_items(values) -> list[dict]:
    """Accept legacy formula strings while persisting one stable formula shape."""
    items = []
    for value in values or []:
        raw_latex = value.get("latex") if isinstance(value, dict) else value
        latex = normalize_latex(raw_latex)
        if not latex:
            continue
        explanation = str(value.get("explanation") or "").strip() if isinstance(value, dict) else ""
        items.append({"latex": latex, "explanation": explanation or formula_caption(latex)})
    return items

def suggest_formula_repair(selected_text: str) -> dict:
    """Return one previewable LaTeX repair for a user-selected fragment."""
    selected_text = str(selected_text or "").strip()
    ensure(selected_text, "יש לסמן קטע לפני תיקון נוסחה")
    ensure(len(selected_text) <= 1500, "הקטע המסומן ארוך מדי לתיקון נוסחה")
    local = normalize_latex(selected_text)
    if GeminiProvider().available:
        prompt = f"""Convert only the selected fragment below into one valid LaTeX mathematical expression. Do not add facts, words, explanations, or delimiters such as $ or $$. Keep only notation that is necessary for the expression. The response must be JSON matching the schema.

Selected fragment:
{selected_text}"""
        try:
            proposed = GeminiProvider().analyze(prompt, [], FORMULA_REPAIR_SCHEMA).get("latex", "")
            if proposed:
                return {"latex": normalize_latex(proposed), "provider": "Gemini"}
        except Exception:
            LOGGER.exception("Formula repair AI request failed; using local notation repair")
    return {"latex": local, "provider": "local"}

_LATEX_COMMANDS = {
    "alpha", "beta", "gamma", "delta", "epsilon", "theta", "lambda", "mu", "nu", "pi", "rho", "tau", "phi", "varphi", "psi", "sigma", "omega", "chi",
    "in", "notin", "iff", "implies", "exists", "forall", "le", "leq", "ge", "geq", "neq", "equiv", "approx", "sim", "propto", "le_p", "to", "mapsto", "mid",
    "langle", "rangle", "text", "mathrm", "mathbf", "operatorname", "subset", "subseteq", "supset", "supseteq", "setminus", "cup", "cap", "land", "lor", "vee", "wedge", "emptyset",
    "overline", "underline", "bar", "hat", "vec", "frac", "binom", "sqrt", "left", "right", "bigl", "bigr", "Bigl", "Bigr", "vert", "Vert", "dots", "ldots", "cdots",
    "times", "cdot", "div", "pm", "sum", "prod", "bigcup", "bigcap", "log", "ln", "exp", "sin", "cos", "tan", "max", "min", "infty", "partial", "nabla", "quad", "qquad", "pmod", "mod",
}

def _latex_is_structurally_safe(value: str) -> bool:
    """Conservative preflight before a formula reaches the browser renderer."""
    if not value or "$" in value or re.search(r"[\u0590-\u05ff\x00-\x1f]", value):
        return False
    depth = 0
    for char in value:
        if char == "{": depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0: return False
    if depth: return False
    commands = re.findall(r"\\([A-Za-z]+)", value)
    # Unknown commands are sent through the repair gate when AI is available;
    # common legitimate mathematical commands remain safe locally as well.
    return all(command in _LATEX_COMMANDS for command in commands)

def audit_math_markdown(content: str) -> tuple[str, dict]:
    """Normalize every marked formula and repair exceptional fragments in one QA pass."""
    candidates = []
    def capture(match, display):
        candidates.append({"raw": match.group(1), "display": display})
        return f"@@MATH_AUDIT_{len(candidates) - 1}@@"
    protected = re.sub(r"\$\$\s*([\s\S]*?)\s*\$\$", lambda match: capture(match, True), content)
    protected = re.sub(r"\$([^$\n]+)\$", lambda match: capture(match, False), protected)
    normalized = [normalize_latex(item["raw"]) for item in candidates]
    suspicious = [index for index, value in enumerate(normalized) if not _latex_is_structurally_safe(value)]
    ai_repairs = {}
    if suspicious and GeminiProvider().available:
        fragments = "\n".join(f"{index}: {normalized[index]}" for index in suspicious)
        prompt = f"""You are a strict LaTeX quality gate. For each numbered fragment below, return the same expression if it is valid; otherwise repair only LaTeX syntax, escaping, delimiters, brackets, and standard commands. Do not add mathematical facts, labels, prose, or dollar delimiters. Return every numbered fragment in the JSON schema.

Fragments:
{fragments}"""
        try:
            result = GeminiProvider().analyze(prompt, [], MATH_AUDIT_SCHEMA)
            for repair in result.get("repairs", []):
                index = repair.get("index")
                if index in suspicious:
                    candidate = normalize_latex(repair.get("latex", ""))
                    if _latex_is_structurally_safe(candidate): ai_repairs[index] = candidate
        except Exception:
            LOGGER.exception("Math quality-gate AI pass failed; retaining deterministic repairs")
    normalized = [ai_repairs.get(index, value) for index, value in enumerate(normalized)]
    def restore(match):
        index = int(match.group(1)); item = candidates[index]; value = normalized[index]
        # An unrecoverable fragment is kept as visible prose rather than being
        # placed in a broken KaTeX box.
        if not _latex_is_structurally_safe(value): return item["raw"]
        return f"$$ {value} $$" if item["display"] else f"${value}$"
    repaired = re.sub(r"@@MATH_AUDIT_(\d+)@@", restore, protected)
    return repaired, {"formula_count": len(candidates), "repaired_count": sum(1 for index, value in enumerate(normalized) if value != candidates[index]["raw"]), "flagged_count": len(suspicious)}

def build_lecture_knowledge(lecture: dict, slides: list[dict], transcript_segments: list[dict], alignments: list[dict], ai_analysis: dict, recordings: list[dict]) -> dict:
    """Build the single source of truth used by every downstream learning output."""
    slide_numbers = {slide["slide_number"] for slide in slides}
    segment_ids = {segment["id"] for segment in transcript_segments}
    sections = []
    for item in ai_analysis.get("sections", []):
        sources = _valid_numbers(item.get("slide_numbers", []), slide_numbers)
        segment_sources = _valid_ids(item.get("transcript_segment_ids", []), segment_ids)
        if not sources and not segment_sources:
            continue
        sections.append({
            "title": str(item.get("title") or "נושא ללא כותרת"),
            "summary": str(item.get("summary") or ""),
            "explanation": str(item.get("explanation") or ""),
            "prerequisite_background": str(item.get("prerequisite_background") or ""),
            "examples": [str(value) for value in item.get("examples", []) if str(value).strip()],
            "key_points": [str(value) for value in item.get("key_points", []) if str(value).strip()],
            "formulas": normalize_formula_items(item.get("formulas", [])),
            "algorithms": [str(value) for value in item.get("algorithms", []) if str(value).strip()],
            "proof_outline": [str(value) for value in item.get("proof_outline", []) if str(value).strip()],
            "slide_numbers": sources, "transcript_segment_ids": segment_sources,
            "certainty": item.get("certainty") if item.get("certainty") in {"source_fact", "inference", "uncertain"} else "uncertain",
        })
    # A no-key run stays useful and traceable: it exposes source material but does not fabricate explanations.
    if not sections:
        for slide in slides:
            sections.append({"title": slide["title"] or f"שקופית {slide['slide_number']}", "summary": slide["text_content"] or "שקופית חזותית ללא טקסט שניתן לחלץ.", "explanation": "", "prerequisite_background": "", "examples": [], "key_points": [], "formulas": [], "algorithms": [], "proof_outline": [], "slide_numbers": [slide["slide_number"]], "transcript_segment_ids": [], "certainty": "source_fact"})
        for segment in transcript_segments:
            if not any(segment["id"] in section["transcript_segment_ids"] for section in sections):
                sections.append({"title": f"הסבר מהתמלול — {segment['source_locator']}", "summary": segment["text_content"], "explanation": "", "prerequisite_background": "", "examples": [], "key_points": [], "formulas": [], "algorithms": [], "proof_outline": [], "slide_numbers": [], "transcript_segment_ids": [segment["id"]], "certainty": "source_fact"})
    lecturer_notes = []
    for note in ai_analysis.get("lecturer_notes", []):
        sources = _valid_ids(note.get("transcript_segment_ids", []), segment_ids)
        if sources and str(note.get("note") or "").strip():
            lecturer_notes.append({"note": str(note["note"]), "transcript_segment_ids": sources, "kind": note.get("kind") if note.get("kind") in {"emphasis", "exam_comment", "warning", "tip"} else "emphasis"})
    visuals = []
    for visual in ai_analysis.get("visual_findings", []):
        number = visual.get("slide_number")
        if number in slide_numbers and str(visual.get("description") or "").strip():
            visuals.append({"slide_number": number, "kind": str(visual.get("kind") or "visual"), "description": str(visual["description"]), "related_section_title": str(visual.get("related_section_title") or ""), "certainty": visual.get("certainty") if visual.get("certainty") in {"source_fact", "inference", "uncertain"} else "uncertain"})
    section_titles = {section["title"] for section in sections}
    generated_diagrams = []
    for diagram in ai_analysis.get("generated_diagrams", []):
        related_section = str(diagram.get("related_section_title") or "")
        nodes = diagram.get("nodes", [])
        node_ids = [str(node.get("id") or "") for node in nodes]
        source_slides = _valid_numbers(diagram.get("slide_numbers", []), slide_numbers)
        source_segments = _valid_ids(diagram.get("transcript_segment_ids", []), segment_ids)
        if related_section not in section_titles or not (2 <= len(nodes) <= 10) or len(set(node_ids)) != len(node_ids) or not all(node_ids) or not (source_slides or source_segments):
            continue
        clean_nodes = [{"id": node_id, "label": str(node.get("label") or "").strip()[:120]} for node_id, node in zip(node_ids, nodes)]
        if not all(node["label"] for node in clean_nodes):
            continue
        clean_edges = [{"from_id": str(edge.get("from_id") or ""), "to_id": str(edge.get("to_id") or ""), "label": str(edge.get("label") or "").strip()[:100]} for edge in diagram.get("edges", [])]
        clean_edges = [edge for edge in clean_edges if edge["from_id"] in node_ids and edge["to_id"] in node_ids]
        if not clean_edges:
            continue
        generated_diagrams.append({"title": str(diagram.get("title") or "תרשים הסבר")[:160], "description": str(diagram.get("description") or "")[:500], "related_section_title": related_section, "nodes": clean_nodes, "edges": clean_edges, "slide_numbers": source_slides, "transcript_segment_ids": source_segments, "certainty": diagram.get("certainty") if diagram.get("certainty") in {"source_fact", "inference", "uncertain"} else "uncertain"})
    return {"version": 4, "lecture_title": lecture["title"], "sections": sections, "lecturer_notes": lecturer_notes, "visuals": visuals, "generated_diagrams": generated_diagrams, "uncertainties": [str(value) for value in ai_analysis.get("uncertainties", []) if str(value).strip()], "source_index": {"slides": [{"number": slide["slide_number"], "title": slide["title"]} for slide in slides], "transcript_segments": [{"id": segment["id"], "locator": segment["source_locator"]} for segment in transcript_segments]}, "alignments": alignments, "recordings": recordings}

def _diagram_svg(diagram: dict) -> str:
    nodes = diagram["nodes"]
    height = max(220, 70 + len(nodes) * 105)
    positions = {node["id"]: (330, 55 + index * 105) for index, node in enumerate(nodes)}
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="660" height="{height}" viewBox="0 0 660 {height}" role="img" aria-label="{html.escape(diagram["title"])}">', '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#0d766e"/></marker></defs>', '<rect width="100%" height="100%" fill="#f7fbfa" rx="12"/>']
    for edge in diagram["edges"]:
        start, end = positions[edge["from_id"]], positions[edge["to_id"]]
        if start == end:
            continue
        y1, y2 = start[1] + 28, end[1] - 28
        svg.append(f'<path d="M {start[0]} {y1} L {end[0]} {y2}" stroke="#0d766e" stroke-width="2" fill="none" marker-end="url(#arrow)"/>')
        if edge["label"]: svg.append(f'<text x="{start[0] + 12}" y="{(y1 + y2) / 2 - 5}" font-family="Arial" font-size="13" fill="#31545a">{html.escape(edge["label"])}</text>')
    for node in nodes:
        x, y = positions[node["id"]]
        svg.extend([f'<rect x="{x - 230}" y="{y - 28}" width="460" height="56" rx="10" fill="#e6f5f2" stroke="#0d766e"/>', f'<text x="{x}" y="{y + 5}" text-anchor="middle" font-family="Arial" font-size="16" fill="#12233d">{html.escape(node["label"])}</text>'])
    svg.append('</svg>')
    return "".join(svg)

def materialize_generated_diagrams(lecture_id: str, knowledge: dict):
    directory = DATA_DIR / "generated" / lecture_id
    for index, diagram in enumerate(knowledge["generated_diagrams"], 1):
        diagram_id = f"diagram-{index}"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{diagram_id}.svg").write_text(_diagram_svg(diagram), encoding="utf-8")
        diagram["id"] = diagram_id

def _slide_links(numbers: list[int]) -> str:
    return " ".join(f"[עמוד {number}](#slide-{number})" for number in numbers)

def formula_caption(formula: str) -> str:
    """A short, source-preserving label for formulas saved before formula explanations existed."""
    normalized = formula.replace(" ", "").lower()
    if normalized.startswith("p:="): return "הגדרה: המחלקה P כוללת בעיות שניתן לפתור בזמן פולינומי."
    if "np-hard" in normalized: return "הגדרה: כל בעיה ב-NP ניתנת לרדוקציה פולינומית לבעיה זו."
    if "np-complete" in normalized: return "הגדרה: בעיה שהיא גם ב-NP וגם NP-Hard."
    if "\\le_p" in formula or "<=_p" in formula: return "הסימון מתאר רדוקציה פולינומית מבעיה אחת לאחרת."
    if normalized.startswith("is:="): return "הגדרה: זוגות של גרף וגודל יעד שעבורם קיימת קבוצה בלתי תלויה מתאימה."
    if "max-cut" in normalized: return "הגדרה: זוגות של גרף ויעד חתך שעבורם קיימת חלוקה מתאימה."
    if "e_g" in normalized: return "הנוסחה סופרת את הקשתות שחוצות בין שני צדי החתך."
    if "sigma" in normalized: return "הנוסחה מגדירה את גודל החתך המקסימלי בגרף."
    if normalized.startswith("f("): return "הפונקציה f מתארת את ההמרה ברדוקציה."
    if "\\chi" in formula or "chi(" in normalized: return "הנוסחה מתארת את תנאי הצביעה של הגרף."
    if "\\in" in formula or " iff " in formula: return "הנוסחה מתארת תנאי שייכות או שקילות בין טענות."
    return "נוסחה זו מסכמת את הסימון המתמטי המשמש בנושא זה."

def is_formula_notation(value) -> bool:
    """Classify a complete formula, never a sentence that merely contains one."""
    text = str(value or "").strip()
    if not text or "$" in text or re.search(r"[\u0590-\u05ff]", text):
        return False
    return bool(re.search(r"\\[A-Za-z]+|:=|<=_p|\\le_p|(?:^|\s)(?:iff|in)(?:\s|$)|[∈⇔≤≥∀∃]|(?:^|\s)[A-Za-z][A-Za-z0-9_]*\s*(?:=|<|>)", text))

def render_notebook_from_knowledge(knowledge: dict) -> str:
    """Create a complete, source-grounded teaching chapter from Lecture Knowledge."""
    lines = [f"# {knowledge['lecture_title']}", "", "## מחברת לימוד מלאה", "", "המחברת כוללת את כל המידע שעובד ממקורות ההרצאה, ובנויה לקריאה רציפה וללמידה מעמיקה. קישורי העמודים מובילים למצגת המקורית; הערות מרצה מופיעות רק כשהן מגובות במקטע מקור."]
    notes_by_segment = {}
    for note in knowledge["lecturer_notes"]:
        for segment_id in note["transcript_segment_ids"]:
            notes_by_segment.setdefault(segment_id, []).append(note)
    for section in knowledge["sections"]:
        lines.extend(["", f"## {section['title']}"])
        if section["summary"]: lines.extend(["", section["summary"]])
        if section["explanation"]: lines.extend(["", "### הרעיון", "", section["explanation"]])
        if section["prerequisite_background"]: lines.extend(["", f"[[learning-bridge:לפני שממשיכים|{section['prerequisite_background']}]]"])
        if section["examples"]:
            lines.extend(["", "### דוגמה מודרכת", ""])
            lines.extend(f"[[guided-example:דוגמה|{value}]]" for value in section["examples"])
        if section["algorithms"]:
            lines.extend(["", "### נבנה את זה צעד־צעד", ""])
            lines.extend(f"- {value}" for value in section["algorithms"])
        if section["proof_outline"]:
            lines.extend(["", "### למה זה נכון", ""])
            lines.extend(f"- {value}" for value in section["proof_outline"])
        if section["key_points"]:
            lines.extend(["", "### מה כדאי לזכור מהנושא", ""])
            lines.extend(f"- {value}" for value in section["key_points"])
        formula_items = normalize_formula_items(section.get("formulas", []))
        if formula_items:
            lines.extend(["", "### נוסחאות", ""])
            for formula in formula_items:
                lines.extend([f"$$ {formula['latex']} $$", f"> **הסבר קצר:** {formula['explanation']}", ""])
        if section["slide_numbers"]:
            lines.extend(["", f"📑 **מקור במצגת:** {_slide_links(section['slide_numbers'])}"])
        related_visuals = [visual for visual in knowledge["visuals"] if visual["related_section_title"] == section["title"]]
        for visual in related_visuals:
            lines.extend(["", f"> **תרשים / רכיב חזותי — מקור: {_slide_links([visual['slide_number']])}**", f"> {visual['description']}", f"[[slide-preview:{visual['slide_number']}]]"])
        for diagram in [item for item in knowledge["generated_diagrams"] if item["related_section_title"] == section["title"]]:
            lines.extend(["", "> **תרשים הסבר שנוצר על ידי המערכת**", f"> {diagram['description']}", f"[[generated-diagram:{diagram['id']}]]"])
        for segment_id in section["transcript_segment_ids"]:
            for note in notes_by_segment.get(segment_id, []):
                label = {"emphasis": "דגש המרצה", "exam_comment": "הערת מרצה על מבחן", "warning": "אזהרת המרצה", "tip": "טיפ מהמרצה"}[note["kind"]]
                lines.extend(["", f"> **🎓 {label}:** {note['note']}"])
        if section["certainty"] != "source_fact": lines.extend(["", "> **הערת ודאות:** חלק זה כולל פרשנות או אי־ודאות המסומנת על בסיס חומר המקור."])
    if knowledge["uncertainties"]:
        lines.extend(["", "## מגבלות ואי־ודאויות", ""])
        lines.extend(f"- {item}" for item in knowledge["uncertainties"])
    return "\n".join(lines)

def validate_alignment_results(raw: dict, slides: list[dict], transcript_segments: list[dict]) -> list[dict]:
    """Keep only links that point to persisted source segments and slide pages."""
    known_segments = {segment["id"]: segment for segment in transcript_segments}
    known_slides = {slide["slide_number"] for slide in slides}
    results = {}
    for item in raw.get("segments", []):
        segment_id = item.get("segment_id")
        if segment_id not in known_segments or segment_id in results:
            continue
        numbers = sorted({number for number in item.get("slide_numbers", []) if isinstance(number, int) and number in known_slides})
        try: confidence = max(0, min(1, float(item.get("confidence", 0))))
        except (TypeError, ValueError): confidence = 0
        relationship = item.get("relationship", "uncertain")
        if relationship not in {"explains", "introduces", "example", "references_visual", "multiple_slides", "no_specific_slide", "uncertain"}:
            relationship = "uncertain"
        if not numbers:
            confidence, relationship = 0, "no_specific_slide"
        results[segment_id] = {"segment_id": segment_id, "slide_numbers": numbers,
            "topic": str(item.get("topic") or "לא זוהה נושא"), "confidence": confidence, "relationship": relationship}
    for segment in transcript_segments:
        results.setdefault(segment["id"], {"segment_id": segment["id"], "slide_numbers": [],
            "topic": "לא זוהה נושא", "confidence": 0, "relationship": "no_specific_slide"})
    return list(results.values())

def align_transcript_to_slides(slides: list[dict], transcript_segments: list[dict]) -> list[dict]:
    """Align stored text metadata only; this never uploads the source files again."""
    if not transcript_segments:
        return []
    if not slides or not GeminiProvider().available:
        return validate_alignment_results({}, slides, transcript_segments)
    slide_context = "\n\n".join(
        f"Slide {slide['slide_number']} | title: {slide['title']}\n{(slide['text_content'] or '')[:1600]}"
        for slide in slides
    )
    segment_context = "\n".join(
        f"segment_id={segment['id']} | {segment['source_locator']}\n{segment['text_content'][:1200]}"
        for segment in transcript_segments[:200]
    )
    prompt = f"""You are aligning already-extracted lecture metadata. Return Hebrew topics in the JSON schema.
Only use slide numbers present below. A link is allowed only when the stored segment text clearly explains, introduces, exemplifies, or refers to that slide. If there is no reliable specific slide, return an empty slide_numbers array and relationship no_specific_slide. Never infer a slide number, speaker intent, or material not present below.

Stored slides:
{slide_context}

Stored transcript segments:
{segment_context}
"""
    return validate_alignment_results(GeminiProvider().analyze(prompt, [], ALIGNMENT_SCHEMA), slides, transcript_segments)

def save_alignments(lecture_id: str, alignments: list[dict], transcript_segments: list[dict]):
    locators = {segment["id"]: segment["source_locator"] for segment in transcript_segments}
    with db.connection() as c:
        c.execute("DELETE FROM alignments WHERE lecture_id=?", (lecture_id,))
        for alignment in alignments:
            c.execute("INSERT INTO alignments VALUES (?,?,?,?,?,?,?,?)", (uid(), lecture_id, alignment["segment_id"],
                locators[alignment["segment_id"]], json.dumps(alignment["slide_numbers"]), alignment["topic"],
                alignment["confidence"], alignment["relationship"]))

def update_processing_stage(lecture_id: str, stage: str, status: str, detail: str):
    with db.connection() as c:
        c.execute("UPDATE jobs SET status=?,detail=?,updated_at=? WHERE lecture_id=? AND stage=?", (status, detail, now(), lecture_id, stage))

def process(lecture_id):
    lecture=db.row("SELECT * FROM lectures WHERE id=?", (lecture_id,)); ensure(lecture,"Lecture not found")
    materials=db.rows("SELECT * FROM materials WHERE lecture_id=?", (lecture_id,)); ensure(materials,"Upload at least one source material first")
    recording_materials = [material for material in materials if material["kind"] == "recording"]
    stages=["Validating", "Analyzing presentation", "Processing recording/transcript", "Aligning lecture and slides", "Analyzing visuals", "Building lecture knowledge", "Generating notebook", "Mathematical quality assurance", "Saving"]
    with db.connection() as c:
        c.execute("UPDATE lectures SET status='processing',updated_at=? WHERE id=?",(now(),lecture_id))
        c.execute("DELETE FROM jobs WHERE lecture_id=?", (lecture_id,))
        for stage in stages:
            c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)",(uid(),lecture_id,stage,"queued","ממתין להתחלה",now(),now()))
    update_processing_stage(lecture_id, "Validating", "running", "בודק את חומרי המקור")
    update_processing_stage(lecture_id, "Validating", "completed", "חומרי המקור תקינים")
    update_processing_stage(lecture_id, "Processing recording/transcript", "running", "מחלץ ומכין את התמלול")
    transcripts = [material for material in materials if material["kind"] == "transcript"]
    for material in transcripts: extract_transcript(lecture_id, material)
    recordings = []
    if recording_materials and GeminiProvider().available:
        try:
            recordings = [extract_recording_transcript(lecture_id, material) for material in recording_materials]
            with db.connection() as c:
                c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (uid(), lecture_id, "Gemini recording transcription", "completed", f"Transcribed {sum(item['segment_count'] for item in recordings)} segments", now(), now()))
        except Exception:
            with db.connection() as c:
                c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (uid(), lecture_id, "Gemini recording transcription", "failed", "See local logs for technical details", now(), now()))
                c.execute("UPDATE lectures SET status='error',updated_at=? WHERE id=?", (now(), lecture_id))
            raise
    else:
        recordings = [inspect_recording(lecture_id, material) for material in recording_materials]
    if recording_materials and not GeminiProvider().available:
        update_processing_stage(lecture_id, "Processing recording/transcript", "waiting_for_ai", "ההקלטה נשמרה וממתינה להגדרת Gemini לתמלול")
    else:
        update_processing_stage(lecture_id, "Processing recording/transcript", "completed", "התמלול וההקלטות הוכנו")
    transcript_segments = db.rows("SELECT * FROM transcript_segments WHERE lecture_id=? ORDER BY material_id,segment_number", (lecture_id,))
    transcript_text = "\n\n".join(segment["text_content"] for segment in transcript_segments)[:8000]
    presentation=next((m for m in materials if m["kind"]=="presentation"),None)
    slide_title = presentation["original_name"] if presentation else "חומרי ההרצאה"
    update_processing_stage(lecture_id, "Analyzing presentation", "running", "מחלץ את עמודי המצגת")
    slide_count = extract_presentation(lecture_id, presentation) if presentation else 0
    update_processing_stage(lecture_id, "Analyzing presentation", "completed", "המצגת נותחה")
    ai_analysis = {}
    update_processing_stage(lecture_id, "Analyzing visuals", "running", "מנתח את תוכן ההרצאה והמצגת")
    if GeminiProvider().available:
        try:
            ai_analysis = analyze_lecture_with_gemini(lecture, materials, transcript_segments)
            update_processing_stage(lecture_id, "Analyzing visuals", "completed", f"הניתוח הושלם באמצעות {GeminiProvider().model}")
        except Exception:
            with db.connection() as c:
                c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (uid(), lecture_id, "Gemini structured analysis", "failed", "See local logs for technical details", now(), now()))
                c.execute("UPDATE lectures SET status='error',updated_at=? WHERE id=?", (now(), lecture_id))
            raise
    else:
        update_processing_stage(lecture_id, "Analyzing visuals", "completed", "ניתוח מקומי הושלם")
    slides = db.rows("SELECT * FROM slides WHERE lecture_id=? ORDER BY slide_number", (lecture_id,))
    update_processing_stage(lecture_id, "Aligning lecture and slides", "running", "מקשר בין התמלול לעמודי המצגת")
    try:
        alignments = align_transcript_to_slides(slides, transcript_segments)
        save_alignments(lecture_id, alignments, transcript_segments)
        with db.connection() as c:
            detail = f"Aligned {len(alignments)} stored transcript segments to extracted slides" if slides and transcript_segments else "No transcript-to-slide alignment was applicable"
            c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (uid(), lecture_id, "Semantic transcript-slide alignment", "completed", detail, now(), now()))
    except Exception:
        # The lecture remains usable and links remain explicitly unassigned rather than guessed.
        alignments = validate_alignment_results({}, slides, transcript_segments)
        save_alignments(lecture_id, alignments, transcript_segments)
        with db.connection() as c:
            c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (uid(), lecture_id, "Semantic transcript-slide alignment", "failed", "No links were guessed; see local logs for technical details", now(), now()))
        LOGGER.exception("Semantic transcript-slide alignment failed for lecture %s", lecture_id)
    update_processing_stage(lecture_id, "Aligning lecture and slides", "completed", "הקישורים למצגת הוכנו")
    evidence = transcript_text[:1200] if transcript_text else "לא סופק תמלול. הסיכום מבוסס על המצגת בלבד; אין לייחס הערות למרצה ללא מקור."
    notebook = f"""# {lecture['title']}

## מטרת הלימוד

מחברת זו נבנתה ממקורות ההרצאה הזמינים. כל טענה מסומנת לפי מקור או אי-ודאות.

## מקור: שקופית 1

המצגת **{slide_title}** זמינה במציג השקופיות. [פתח/י את שקופית 1](#slide-1).

## תוכן התמלול / המקור

{evidence}

> **הערת מקור:** לא נוצרה הערת מרצה ללא ציטוט מפורש בתמלול.

## מונחים טכניים

הביטוי `Python` והסיבוכיות $O(n \\log n)$ נשמרים בכיוון LTR בתוך טקסט עברי.

```python
def example(items):
    return sorted(items)
```

| מושג | משמעות |
| --- | --- |
| מקור | Slide 1 |
| ודאות | מסומן במפורש |
"""
    exam = f"""# Exam Focus: {lecture['title']}

- **מקור מרכזי:** Slide 1 ({slide_title})
- **סטטוס ראיות:** {'תמלול סופק' if transcripts else 'אין תמלול; אין להסיק הערות מרצה'}
- **מונח:** `Python`
- **נוסחה לדוגמה:** $O(n \\log n)$
"""
    update_processing_stage(lecture_id, "Building lecture knowledge", "running", "בונה בסיס ידע להרצאה")
    knowledge = build_lecture_knowledge(lecture, slides, transcript_segments, alignments, ai_analysis, recordings)
    materialize_generated_diagrams(lecture_id, knowledge)
    update_processing_stage(lecture_id, "Building lecture knowledge", "completed", "בסיס הידע מוכן")
    update_processing_stage(lecture_id, "Generating notebook", "running", "כותב את המחברת")
    notebook = render_notebook_from_knowledge(knowledge)
    update_processing_stage(lecture_id, "Generating notebook", "completed", "המחברת נוצרה")
    update_processing_stage(lecture_id, "Mathematical quality assurance", "running", "בודק ומתקן נוסחאות וביטויים מתמטיים")
    notebook, math_audit = audit_math_markdown(notebook)
    update_processing_stage(lecture_id, "Mathematical quality assurance", "completed", f"נבדקו {math_audit['formula_count']} ביטויים; תוקנו או נורמלו {math_audit['repaired_count']}")
    update_processing_stage(lecture_id, "Saving", "running", "שומר את התוצאה")
    with db.connection() as c:
        manual_edit = c.execute("SELECT base_content FROM notebook_manual_edits WHERE lecture_id=?", (lecture_id,)).fetchone()
        if manual_edit and manual_edit["base_content"] != notebook:
            # Keep the student's rendered edit visible until they explicitly
            # choose between it and the newly generated notebook.
            c.execute("UPDATE notebook_manual_edits SET pending_content=?,updated_at=? WHERE lecture_id=?", (notebook, now(), lecture_id))
        c.execute("DELETE FROM source_references WHERE lecture_id=?", (lecture_id,))
        knowledge["notebook"] = notebook
        knowledge["has_transcript"] = bool(transcripts)
        knowledge["transcript_segment_count"] = len(transcript_segments)
        knowledge["slide_title"] = slide_title
        c.execute("INSERT OR REPLACE INTO lecture_knowledge VALUES (?,?,COALESCE((SELECT created_at FROM lecture_knowledge WHERE lecture_id=?),?),?)",(lecture_id,json.dumps(knowledge,ensure_ascii=False),lecture_id,now(),now()))
        c.execute("DELETE FROM outputs WHERE lecture_id=? AND kind='notebook'", (lecture_id,))
        for kind,content in (("notebook",notebook),):
            c.execute("INSERT INTO outputs VALUES (?,?,?,?,?)",(uid(),lecture_id,kind,content,now()))
        segment_locators = {segment["id"]: segment["source_locator"] for segment in transcript_segments}
        for section in knowledge["sections"]:
            for number in section["slide_numbers"]:
                c.execute("INSERT INTO source_references VALUES (?,?,?,?,?,?)", (uid(), lecture_id, section["title"], "slide", f"Slide {number}", section["certainty"]))
            for segment_id in section["transcript_segment_ids"]:
                c.execute("INSERT INTO source_references VALUES (?,?,?,?,?,?)", (uid(), lecture_id, section["title"], "transcript", segment_locators[segment_id], section["certainty"]))
        for note in knowledge["lecturer_notes"]:
            for segment_id in note["transcript_segment_ids"]:
                c.execute("INSERT INTO source_references VALUES (?,?,?,?,?,?)", (uid(), lecture_id, note["note"], "lecturer_note", segment_locators[segment_id], "source_fact"))
        for visual in knowledge["visuals"]:
            slide = db.row("SELECT id FROM slides WHERE lecture_id=? AND slide_number=?", (lecture_id, visual["slide_number"]))
            if slide:
                c.execute("INSERT INTO visual_elements VALUES (?,?,?,?,?,?)", (uid(), slide["id"], visual["kind"], visual["description"], f"Slide {visual['slide_number']}", "AI analysis"))
        c.execute("UPDATE lectures SET status='completed',updated_at=? WHERE id=?",(now(),lecture_id))
    update_processing_stage(lecture_id, "Saving", "completed", "המחברת נשמרה")
    return {"status":"completed","provider":"offline-safe" if not GeminiProvider().available else f"Gemini structured analysis completed with {GeminiProvider().model}", "knowledge_sections": len(knowledge["sections"])}

def validate_exam_focus(raw: dict, knowledge: dict) -> dict:
    known_slides = {item["number"] for item in knowledge.get("source_index", {}).get("slides", [])}
    known_segments = {item["id"] for item in knowledge.get("source_index", {}).get("transcript_segments", [])}
    sections = []
    for item in raw.get("sections", []):
        points = [str(point) for point in item.get("recall_points", []) if str(point).strip()]
        if not str(item.get("title") or "").strip() or not points:
            continue
        # Legacy summaries kept formulas and algorithm prose in one list.
        # Preserve them on read, but separate them before rendering.
        legacy_points = [str(point) for point in item.get("formula_or_algorithm", []) if str(point).strip()]
        formulas = normalize_formula_items(item.get("formulas", []))
        algorithms = [str(point) for point in item.get("algorithms", []) if str(point).strip()]
        for point in legacy_points:
            if is_formula_notation(point): formulas.extend(normalize_formula_items([point]))
            else: algorithms.append(point)
        sections.append({"title": str(item["title"]), "recall_points": points,
            "formulas": formulas, "algorithms": algorithms,
            "common_confusions": [str(point) for point in item.get("common_confusions", []) if str(point).strip()],
            "slide_numbers": _valid_numbers(item.get("slide_numbers", []), known_slides),
            "certainty": item.get("certainty") if item.get("certainty") in {"source_fact", "inference", "uncertain"} else "uncertain"})
    notes = []
    for note in raw.get("lecturer_exam_notes", []):
        source_ids = _valid_ids(note.get("transcript_segment_ids", []), known_segments)
        if source_ids and str(note.get("note") or "").strip():
            notes.append({"note": str(note["note"]), "transcript_segment_ids": source_ids})
    return {"sections": sections, "lecturer_exam_notes": notes,
        "uncertainties": [str(item) for item in raw.get("uncertainties", []) if str(item).strip()]}

def fallback_exam_focus(knowledge: dict) -> dict:
    """A source-preserving exam review when AI is unavailable."""
    sections = []
    for section in knowledge.get("sections", []):
        points = list(section.get("key_points", [])) or ([section.get("summary")] if section.get("summary") else [])
        if points:
            sections.append({"title": section["title"], "recall_points": points,
                "formulas": normalize_formula_items(section.get("formulas", [])), "algorithms": list(section.get("algorithms", [])),
                "common_confusions": [], "slide_numbers": list(section.get("slide_numbers", [])), "certainty": section.get("certainty", "source_fact")})
    notes = [{"note": note["note"], "transcript_segment_ids": note["transcript_segment_ids"]} for note in knowledge.get("lecturer_notes", []) if note["kind"] == "exam_comment"]
    return {"sections": sections, "lecturer_exam_notes": notes, "uncertainties": list(knowledge.get("uncertainties", []))}

def build_exam_focus(knowledge: dict) -> dict:
    """Create a fast review from Lecture Knowledge, never from raw uploads."""
    if not GeminiProvider().available:
        return fallback_exam_focus(knowledge)
    source = json.dumps({"lecture_title": knowledge.get("lecture_title"), "sections": knowledge.get("sections", []), "lecturer_notes": knowledge.get("lecturer_notes", []), "uncertainties": knowledge.get("uncertainties", [])}, ensure_ascii=False)
    prompt = f"""You are preparing a concise Hebrew university exam review from this already verified Lecture Knowledge.
Do not add facts, formulas, warnings, exam claims, or source references that are not present in the supplied knowledge. This is a revision aid, not a shorter notebook: prefer scan-friendly recall points, key algorithms/formulas, and genuine distinctions. Include a lecturer_exam_note only when the supplied lecturer_notes explicitly have kind exam_comment. Use only slide numbers and transcript IDs already present in the knowledge. If the sources do not support a 'common confusion', leave it empty.

Put a complete mathematical statement only in formulas, as an object with latex and one short Hebrew explanation. Put algorithm steps or prose explanations only in algorithms, as ordinary Hebrew strings; an algorithm may include short $...$ expressions but must never be placed in formulas. Never put a label such as 'הגדרה:' or a Hebrew/English sentence inside latex. If a mathematical expression is part of an algorithm sentence, keep it inline in that sentence rather than turning the entire sentence into a formula.

Content-language policy:
{CONTENT_LANGUAGE_POLICY}

Mathematical-rendering contract:
{MATH_RENDERING_CONTRACT}

Lecture Knowledge:
{source}
"""
    try:
        return validate_exam_focus(GeminiProvider().analyze(prompt, [], EXAM_FOCUS_SCHEMA), knowledge)
    except Exception:
        LOGGER.exception("Exam-focus AI generation failed; using source-preserving fallback")
        return fallback_exam_focus(knowledge)

def render_exam_focus(lecture_title: str, exam: dict, knowledge: dict) -> str:
    lines = [f"# 🎯 סיכום ממוקד למבחן: {lecture_title}", "", "## חזרה מהירה", "", "התוצר מבוסס על הידע שעובד כבר מההרצאה; הוא מיועד לחזרה ולא מחליף את המחברת המלאה."]
    segment_locators = {item["id"]: item["locator"] for item in knowledge.get("source_index", {}).get("transcript_segments", [])}
    for section in exam["sections"]:
        lines.extend(["", f"## {section['title']}", ""])
        lines.extend(f"- {point}" for point in section["recall_points"])
        if section["formulas"]:
            lines.extend(["", "### נוסחאות", ""])
            for formula in normalize_formula_items(section["formulas"]):
                lines.extend([f"$$ {formula['latex']} $$", f"> **הסבר קצר:** {formula['explanation']}", ""])
        if section["algorithms"]:
            lines.extend(["", "### אלגוריתמים ושלבים", ""])
            lines.extend(f"- {point}" for point in section["algorithms"])
        if section["common_confusions"]:
            lines.extend(["", "### נקודות שכדאי להבחין ביניהן", ""])
            lines.extend(f"- {point}" for point in section["common_confusions"])
        if section["slide_numbers"]:
            lines.extend(["", f"📑 **שקפים רלוונטיים:** {_slide_links(section['slide_numbers'])}"])
        if section["certainty"] != "source_fact": lines.extend(["", "> **הערת ודאות:** נקודה זו כוללת פרשנות או אי־ודאות שמסומנת במקור."])
    if exam["lecturer_exam_notes"]:
        lines.extend(["", "## ⚠️ דגשים מפורשים של המרצה למבחן", ""])
        for note in exam["lecturer_exam_notes"]:
            sources = "; ".join(segment_locators[segment_id] for segment_id in note["transcript_segment_ids"])
            lines.extend([f"> **🎓 דגש מרצה:** {note['note']}", f"> מקור: {sources}"])
    if exam["uncertainties"]:
        lines.extend(["", "## מגבלות ואי־ודאויות", ""])
        lines.extend(f"- {item}" for item in exam["uncertainties"])
    return "\n".join(lines)

def generate_exam_focus(lecture_id: str, regenerate: bool = False):
    """Generate the revision output from persisted knowledge, never from raw uploads."""
    lecture = db.row("SELECT * FROM lectures WHERE id=?", (lecture_id,)); ensure(lecture, "Lecture not found")
    knowledge_row = db.row("SELECT * FROM lecture_knowledge WHERE lecture_id=?", (lecture_id,))
    ensure(knowledge_row, "יש לעבד את ההרצאה לפני יצירת הסיכום הממוקד למבחן.")
    existing = db.row("SELECT * FROM outputs WHERE lecture_id=? AND kind='exam_focus' ORDER BY created_at DESC", (lecture_id,))
    if existing and not regenerate: return {"status":"existing", "output_id": existing["id"]}
    knowledge = json.loads(knowledge_row["content_json"])
    exam = f"""# 🎯 סיכום ממוקד למבחן: {lecture['title']}

## חזרה מהירה

- **מקור מרכזי:** [שקופית 1](#slide-1) — {knowledge['slide_title']}
- **סטטוס ראיות:** {'תמלול סופק; יש לקרוא את ההסברים בהקשרם.' if knowledge['has_transcript'] else 'אין תמלול; לא מיוחסות הערות למרצה.'}
- **מונח טכני:** `Python`
- **סיבוכיות לדוגמה:** $O(n \\log n)$

## מה לזכור

| נושא | דגש לחזרה |
| --- | --- |
| מקור | שקופית 1 |
| ודאות | עובדה הנתמכת במקור |

> **ארגון ללמידה:** פריט זה נבחר לסיכום מהיר על בסיס מבנה ההרצאה, ולא כהבטחה או דגש למבחן מצד המרצה.
"""
    exam_data = build_exam_focus(knowledge)
    exam = render_exam_focus(lecture["title"], exam_data, knowledge)
    exam, math_audit = audit_math_markdown(exam)
    with db.connection() as c:
        manual_edit = c.execute("SELECT base_content FROM exam_manual_edits WHERE lecture_id=?", (lecture_id,)).fetchone()
        if manual_edit and manual_edit["base_content"] != exam:
            c.execute("UPDATE exam_manual_edits SET pending_content=?,updated_at=? WHERE lecture_id=?", (exam, now(), lecture_id))
        if existing: c.execute("DELETE FROM outputs WHERE id=?", (existing["id"],))
        output_id = uid()
        c.execute("INSERT INTO outputs VALUES (?,?,?,?,?)", (output_id, lecture_id, "exam_focus", exam, now()))
        c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (uid(), lecture_id, "Generating exam focus", "completed", f"Generated from persisted lecture knowledge; mathematical QA checked {math_audit['formula_count']} expressions", now(), now()))
    return {"status":"generated", "output_id":output_id}

def ordered_course_lectures(course_id: str) -> list[dict]:
    return db.rows("SELECT * FROM lectures WHERE course_id=? ORDER BY CASE WHEN number IS NULL THEN 1 ELSE 0 END, number, lecture_date, created_at", (course_id,))

def ensure_course_knowledge(course_id: str) -> list[dict]:
    """Reuse processed lectures and process only the missing ones before course aggregation."""
    lectures = ordered_course_lectures(course_id)
    ensure(lectures, "Create at least one lecture before generating a course output")
    for lecture in lectures:
        if not db.row("SELECT lecture_id FROM lecture_knowledge WHERE lecture_id=?", (lecture["id"],)):
            process(lecture["id"])
    return lectures

def _course_slide_links(content: str, lecture_id: str) -> str:
    content = re.sub(r"\(#slide-(\d+)\)", rf"(#course-{lecture_id}-slide-\1)", content)
    return re.sub(r"\[\[generated-diagram:([a-z0-9-]+)]]", rf"[[course-diagram:{lecture_id}:\1]]", content)

def _course_lecture_link(lecture: dict) -> str:
    return f"[פתיחת ההרצאה](#lecture-{lecture['id']})"

def _save_course_output(course_id: str, kind: str, content: str) -> str:
    output_id = uid()
    with db.connection() as c:
        c.execute("DELETE FROM course_outputs WHERE course_id=? AND kind=?", (course_id, kind))
        c.execute("INSERT INTO course_outputs VALUES (?,?,?,?,?)", (output_id, course_id, kind, content, now()))
    return output_id

def generate_course_notebook(course_id: str) -> dict:
    course = db.row("SELECT * FROM courses WHERE id=?", (course_id,)); ensure(course, "Course not found")
    lectures = ensure_course_knowledge(course_id)
    lines = [f"# 📖 מחברת קורס: {course['name']}", "", "מחברת זו מאגדת את המחברות של כל ההרצאות לפי סדר הקורס. כל קישור מוביל להרצאה ולשקף המקוריים."]
    for lecture in lectures:
        knowledge = db.row("SELECT content_json FROM lecture_knowledge WHERE lecture_id=?", (lecture["id"],))
        content = _course_slide_links(json.loads(knowledge["content_json"])["notebook"], lecture["id"])
        lines.extend(["", f"# הרצאה: {lecture['title']}", "", _course_lecture_link(lecture), "", content])
    output_id = _save_course_output(course_id, "course_notebook", "\n".join(lines))
    return {"status": "generated", "output_id": output_id, "lecture_count": len(lectures)}

def generate_course_exam_focus(course_id: str) -> dict:
    course = db.row("SELECT * FROM courses WHERE id=?", (course_id,)); ensure(course, "Course not found")
    lectures = ensure_course_knowledge(course_id)
    lines = [f"# 🎯 סיכום קורס ממוקד למבחן: {course['name']}", "", "הסיכום מאגד חזרה מהירה מכל ההרצאות, תוך שמירה על הקישורים למקורות."]
    for lecture in lectures:
        output = db.row("SELECT content FROM outputs WHERE lecture_id=? AND kind='exam_focus' ORDER BY created_at DESC", (lecture["id"],))
        if not output:
            generate_exam_focus(lecture["id"])
            output = db.row("SELECT content FROM outputs WHERE lecture_id=? AND kind='exam_focus' ORDER BY created_at DESC", (lecture["id"],))
        lines.extend(["", f"# הרצאה: {lecture['title']}", "", _course_lecture_link(lecture), "", _course_slide_links(output["content"], lecture["id"])])
    output_id = _save_course_output(course_id, "course_exam_focus", "\n".join(lines))
    return {"status": "generated", "output_id": output_id, "lecture_count": len(lectures)}
