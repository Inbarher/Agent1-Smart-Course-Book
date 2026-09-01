from __future__ import annotations
import cgi, json, mimetypes, os, re, traceback
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from . import db
from .config import DATA_DIR, ROOT
from .pdf_export import export_notebook
from .services import (create_course, create_lecture, delete_course_record,
    delete_lecture_record, delete_material_record, generate_course_exam_focus,
    generate_course_notebook, generate_exam_focus, process,
    update_app_settings, update_course, update_lecture, upload_material, app_settings,
    get_notebook_manual_edit, resolve_notebook_manual_edit, save_notebook_manual_edit,
    suggest_formula_repair, get_exam_manual_edit, resolve_exam_manual_edit, save_exam_manual_edit)
from .config import LOGGER

STATIC = ROOT / "app" / "static"
class App(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(STATIC),**kwargs)
    def json(self, data, status=200):
        raw=json.dumps(data,ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def read_json(self): return json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))).decode() or "{}")
    def do_GET(self):
        parsed=urlparse(self.path); p=parsed.path
        if p=="/api/settings": return self.json(app_settings())
        if p=="/api/courses": return self.json(db.rows("SELECT * FROM courses ORDER BY updated_at DESC"))
        if p.startswith("/api/courses/"):
            cid=p.split("/")[3]; return self.json({"course":db.row("SELECT * FROM courses WHERE id=?",(cid,)),"lectures":db.rows("SELECT * FROM lectures WHERE course_id=? ORDER BY CASE WHEN number IS NULL THEN 1 ELSE 0 END, number, lecture_date, created_at",(cid,)),"course_outputs":db.rows("SELECT * FROM course_outputs WHERE course_id=? ORDER BY created_at DESC",(cid,))})
        if p.startswith("/api/lectures/"):
            pieces=p.split("/"); lid=pieces[3]
            if len(pieces)>5 and pieces[4]=="diagrams":
                diagram_id=pieces[5]
                if not re.fullmatch(r"[a-z0-9-]+", diagram_id): return self.json({"error":"Diagram not found"},404)
                path=DATA_DIR / "generated" / lid / f"{diagram_id}.svg"
                if not path.exists(): return self.json({"error":"Diagram not found"},404)
                raw=path.read_bytes(); self.send_response(200); self.send_header("Content-Type","image/svg+xml"); self.send_header("Content-Length",len(raw)); self.end_headers(); return self.wfile.write(raw)
            if len(pieces)>4 and pieces[4]=="pdf":
                output=db.row("SELECT * FROM outputs WHERE lecture_id=? AND kind='notebook' ORDER BY created_at DESC",(lid,)); lecture=db.row("SELECT * FROM lectures WHERE id=?",(lid,))
                if not output: return self.json({"error":"Generate a notebook first"},404)
                path=export_notebook(lid,lecture["title"],output["content"]); raw=path.read_bytes(); self.send_response(200); self.send_header("Content-Type","application/pdf");self.send_header("Content-Disposition","attachment; filename=notebook.pdf");self.send_header("Content-Length",len(raw));self.end_headers();return self.wfile.write(raw)
            return self.json({"lecture":db.row("SELECT * FROM lectures WHERE id=?",(lid,)),"materials":db.rows("SELECT * FROM materials WHERE lecture_id=?",(lid,)),"outputs":db.rows("SELECT * FROM outputs WHERE lecture_id=? ORDER BY created_at DESC",(lid,)),"slides":db.rows("SELECT * FROM slides WHERE lecture_id=? ORDER BY slide_number",(lid,)),"transcript_segments":db.rows("SELECT * FROM transcript_segments WHERE lecture_id=? ORDER BY material_id,segment_number",(lid,)),"alignments":db.rows("SELECT * FROM alignments WHERE lecture_id=? ORDER BY segment_id",(lid,)),"knowledge":db.row("SELECT content_json,updated_at FROM lecture_knowledge WHERE lecture_id=?",(lid,)),"notebook_edit":get_notebook_manual_edit(lid),"exam_edit":get_exam_manual_edit(lid),"jobs":db.rows("SELECT * FROM jobs WHERE lecture_id=? ORDER BY created_at",(lid,))})
        if p.startswith("/api/materials/") and p.endswith("/file"):
            material=db.row("SELECT * FROM materials WHERE id=?",(p.split("/")[3],))
            if not material: return self.json({"error":"Material not found"},404)
            target=DATA_DIR / material["stored_path"]
            if target.exists() and target.is_file():
                raw=target.read_bytes(); self.send_response(200);self.send_header("Content-Type",material["mime_type"] or "application/octet-stream");self.send_header("Content-Length",len(raw));self.end_headers();return self.wfile.write(raw)
            return self.json({"error":"Source file no longer exists"},404)
        if p.startswith("/materials/"):
            target=DATA_DIR/p.removeprefix("/materials/")
            if target.exists() and target.is_file():
                raw=target.read_bytes(); self.send_response(200);self.send_header("Content-Type",mimetypes.guess_type(str(target))[0] or "application/octet-stream");self.send_header("Content-Length",len(raw));self.end_headers();return self.wfile.write(raw)
        return super().do_GET()
    def do_POST(self):
        try:
            parsed=urlparse(self.path); path=parsed.path
            if self.path=="/api/courses": return self.json(create_course(self.read_json()),201)
            if path.startswith("/api/courses/") and path.endswith("/process"):
                return self.json(generate_course_notebook(path.split("/")[3]))
            if path.startswith("/api/courses/") and path.endswith("/exam-focus"):
                return self.json(generate_course_exam_focus(path.split("/")[3]))
            if path.startswith("/api/courses/") and path.endswith("/lectures"):
                return self.json(create_lecture(path.split("/")[3],self.read_json()),201)
            if path.startswith("/api/lectures/") and path.endswith("/process"):
                return self.json(process(path.split("/")[3]))
            if path.startswith("/api/lectures/") and path.endswith("/exam-focus"):
                regenerate=parse_qs(parsed.query).get("regenerate", ["0"])[0] == "1"
                return self.json(generate_exam_focus(path.split("/")[3], regenerate))
            if path.startswith("/api/lectures/") and path.endswith("/formula-repair"):
                return self.json(suggest_formula_repair(self.read_json().get("selected_text", "")))
            if path.startswith("/api/lectures/") and path.endswith("/notebook-edit/resolve"):
                return self.json(resolve_notebook_manual_edit(path.split("/")[3], self.read_json().get("choice", "")))
            if path.startswith("/api/lectures/") and path.endswith("/exam-edit/resolve"):
                return self.json(resolve_exam_manual_edit(path.split("/")[3], self.read_json().get("choice", "")))
            if path.startswith("/api/lectures/") and path.endswith("/materials"):
                form=cgi.FieldStorage(fp=self.rfile,headers=self.headers,environ={"REQUEST_METHOD":"POST","CONTENT_TYPE":self.headers["Content-Type"]})
                return self.json(upload_material(path.split("/")[3],form.getvalue("kind"),form["file"]),201)
            self.json({"error":"Not found"},404)
        except Exception as exc:
            LOGGER.exception("POST %s failed",self.path); self.json({"error":str(exc)},400)
    def do_PUT(self):
        try:
            if self.path=="/api/settings": return self.json(update_app_settings(self.read_json()))
            if self.path.startswith("/api/lectures/") and self.path.endswith("/notebook-edit"):
                body = self.read_json()
                return self.json(save_notebook_manual_edit(self.path.split("/")[3], body.get("base_content", ""), body.get("html_content", "")))
            if self.path.startswith("/api/lectures/") and self.path.endswith("/exam-edit"):
                body = self.read_json()
                return self.json(save_exam_manual_edit(self.path.split("/")[3], body.get("base_content", ""), body.get("html_content", "")))
            if self.path.startswith("/api/courses/"): return self.json(update_course(self.path.split("/")[3],self.read_json()))
            if self.path.startswith("/api/lectures/"): return self.json(update_lecture(self.path.split("/")[3],self.read_json()))
            self.json({"error":"Not found"},404)
        except Exception as exc: LOGGER.exception("PUT %s failed",self.path); self.json({"error":str(exc)},400)
    def do_DELETE(self):
        try:
            if self.path.startswith("/api/courses/"):
                delete_course_record(self.path.split("/")[3])
                return self.json({"deleted":True})
            if self.path.startswith("/api/lectures/"):
                delete_lecture_record(self.path.split("/")[3]); return self.json({"deleted":True})
            if self.path.startswith("/api/materials/"):
                delete_material_record(self.path.split("/")[3]); return self.json({"deleted":True})
            self.json({"error":"Not found"},404)
        except Exception as exc: LOGGER.exception("DELETE %s failed",self.path); self.json({"error":str(exc)},400)

def run(port=8000):
    db.init_db(); print(f"Smart Course Book running at http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1",port),App).serve_forever()
if __name__=="__main__": run(int(os.getenv("PORT","8000")))
