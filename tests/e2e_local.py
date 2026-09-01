"""Local HTTP acceptance workflow: courses -> materials -> processing -> PDF."""
import json, tempfile, urllib.request
from pathlib import Path
from reportlab.pdfgen.canvas import Canvas

BASE="http://127.0.0.1:8000"
def request(path, data=None, content_type="application/json"):
    body=json.dumps(data,ensure_ascii=False).encode() if data is not None else None
    req=urllib.request.Request(BASE+path,data=body,headers={"Content-Type":content_type} if body else {},method="POST" if body else "GET")
    with urllib.request.urlopen(req) as res:return res.read()
def multipart(path, fields, file_path):
    boundary="----SmartCourseBoundary"; payload=[]
    for k,v in fields.items(): payload += [f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()]
    payload += [f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{file_path.name}\"\r\nContent-Type: application/pdf\r\n\r\n".encode(),file_path.read_bytes(),f"\r\n--{boundary}--\r\n".encode()]
    req=urllib.request.Request(BASE+path,data=b''.join(payload),headers={"Content-Type":f"multipart/form-data; boundary={boundary}"},method="POST")
    with urllib.request.urlopen(req) as res:return json.loads(res.read())
with tempfile.TemporaryDirectory() as td:
    source=Path(td)/"slides.pdf"; c=Canvas(str(source)); c.drawString(72,720,"Source Slide 1"); c.save()
    course=json.loads(request("/api/courses",{"name":"בדיקת RTL","code":"RTL-101"})); lecture=json.loads(request(f"/api/courses/{course['id']}/lectures",{"title":"Python ו-BFS","type":"lecture"}))
    multipart(f"/api/lectures/{lecture['id']}/materials",{"kind":"presentation"},source)
    request(f"/api/lectures/{lecture['id']}/process",{})
    view=json.loads(request(f"/api/lectures/{lecture['id']}")); assert view["outputs"] and view["slides"] and view["jobs"]
    pdf=request(f"/api/lectures/{lecture['id']}/pdf"); out=Path("data/generated/acceptance-rtl.pdf");out.write_bytes(pdf); assert pdf.startswith(b"%PDF")
    print(f"E2E OK: {out.resolve()} ({len(pdf)} bytes)")
