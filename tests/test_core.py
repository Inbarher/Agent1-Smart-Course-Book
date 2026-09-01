import io, json, os, tempfile, unittest
from pathlib import Path
os.environ["SMART_COURSE_DATA_DIR"] = tempfile.mkdtemp(prefix="smart-course-test-")
os.environ["SMART_COURSE_DISABLE_GEMINI"] = "1"
from app import db
from app.rtl import rtl_html, visual_rtl_for_pdf
from app.services import (GeminiProvider, create_course, create_lecture, delete_course_record,
    extract_presentation, generate_exam_focus, now, parse_transcript_text, process,
    uid, update_course, update_lecture, validate_alignment_results, build_lecture_knowledge,
    render_notebook_from_knowledge, validate_exam_focus, render_exam_focus,
    generate_course_notebook, generate_course_exam_focus, validate_recording_transcript,
    materialize_generated_diagrams, normalize_latex, app_settings, update_app_settings,
    trusted_ca_bundle, is_formula_notation, get_notebook_manual_edit,
    resolve_notebook_manual_edit, save_notebook_manual_edit, suggest_formula_repair,
    audit_math_markdown)
from app.pdf_export import export_notebook
from app.config import DATA_DIR
from reportlab.pdfgen.canvas import Canvas

class CoreTests(unittest.TestCase):
    def setUp(self): db.init_db()
    def test_auto_reprocess_setting_is_enabled_by_default_and_persists(self):
        self.assertTrue(app_settings()["auto_reprocess_on_upload"])
        self.assertFalse(update_app_settings({"auto_reprocess_on_upload": False})["auto_reprocess_on_upload"])
        self.assertFalse(app_settings()["auto_reprocess_on_upload"])
        self.assertTrue(update_app_settings({"auto_reprocess_on_upload": True})["auto_reprocess_on_upload"])
    def test_trusted_ca_bundle_exists_without_disabling_certificate_checks(self):
        bundle = Path(trusted_ca_bundle())
        self.assertTrue(bundle.exists())
        self.assertIn("BEGIN CERTIFICATE", bundle.read_text(encoding="ascii"))
    def test_manual_notebook_edit_is_preserved_until_user_resolves_a_regeneration(self):
        course = create_course({"name": "עריכות"}); lecture = create_lecture(course["id"], {"title": "הרצאה"})
        original, regenerated = "# מחברת\n\nטקסט מקורי", "# מחברת\n\nטקסט חדש"
        with db.connection() as con:
            con.execute("INSERT INTO outputs VALUES (?,?,?,?,?)", (uid(), lecture["id"], "notebook", original, now()))
        saved = save_notebook_manual_edit(lecture["id"], original, "<h1>מחברת</h1><p>תיקון אישי</p>")
        self.assertIn("תיקון אישי", saved["html_content"])
        with db.connection() as con:
            con.execute("UPDATE notebook_manual_edits SET pending_content=? WHERE lecture_id=?", (regenerated, lecture["id"]))
        self.assertEqual(resolve_notebook_manual_edit(lecture["id"], "keep")["choice"], "keep")
        self.assertIsNone(get_notebook_manual_edit(lecture["id"])["pending_content"])
        with db.connection() as con:
            con.execute("UPDATE notebook_manual_edits SET pending_content=? WHERE lecture_id=?", (regenerated, lecture["id"]))
        self.assertEqual(resolve_notebook_manual_edit(lecture["id"], "adopt")["choice"], "adopt")
        self.assertIsNone(get_notebook_manual_edit(lecture["id"]))
    def test_formula_repair_uses_safe_local_normalization_without_ai(self):
        result = suggest_formula_repair(r"\langle G\_\\\phi, m \rangle")
        self.assertEqual(result["latex"], r"\langle G_\phi, m \rangle")

    def test_math_audit_normalizes_all_marked_math_and_keeps_prose_outside_boxes(self):
        content = "הסבר בעברית עם $x in L1 iff f(x) in L2$.\n\n$$ \\langle G\\_\\\\\\phi, m \\rangle $$"
        audited, report = audit_math_markdown(content)
        self.assertIn(r"$x \in L_{1} \iff f(x) \in L_{2}$", audited)
        self.assertIn(r"$$ \langle G_\phi, m \rangle $$", audited)
        self.assertEqual(report["formula_count"], 2)
        self.assertNotIn("הסבר בעברית", audited.split("$$")[1])
    def test_course_and_lecture_persist(self):
        course=create_course({"name":"מבני נתונים","code":"234"}); lecture=create_lecture(course["id"],{"title":"עצים","type":"lecture"})
        self.assertEqual(db.row("SELECT name FROM courses WHERE id=?",(course["id"],))["name"],"מבני נתונים")
        self.assertEqual(lecture["type"],"lecture")
    def test_rtl_isolates_mixed_terms(self):
        rendered=rtl_html("למדנו Python עם O(n log n) בשקופית 12")
        self.assertIn('dir="ltr"',rendered); self.assertIn("Python",rendered)
        visual=visual_rtl_for_pdf("שקופית 12: Python (BFS)")
        self.assertIn("Python",visual); self.assertIn("12",visual)

    def test_math_normalizer_canonicalizes_legacy_notation(self):
        cases = {
            "x in L1 iff f(x) in L2": r"x \in L_{1} \iff f(x) \in L_{2}",
            "|Y|=k": r"|Y|=k",
            r"L ⊆ {0,1}\*": r"L ⊆ {0,1}*",
            "A(G-v)=1": "A(G-v)=1",
            "v(G) > k": "v(G) > k",
            "O(n^{c+1})": "O(n^{c+1})",
            r"\<G, k>": r"\langle G, k \rangle",
            "alpha(G) >= k": r"\alpha(G) \geq k",
            "k=3": "k=3",
            "<Gphi, m>": r"\langle G_\phi, m \rangle",
            r"\-|S|>=k": r"|S|\geqk",
        }
        for source, expected in cases.items():
            self.assertEqual(normalize_latex(source).replace(" ", ""), expected.replace(" ", ""))
        self.assertEqual(normalize_latex("Self-Reduction"), "Self-Reduction")
        self.assertEqual(normalize_latex("*P*:= {L | L is solvable by a polynomial-time algorithm}"), r"P:= \{ L \mid L \text{ is solvable by a polynomial-time algorithm} \}")
        self.assertEqual(normalize_latex(r"x \\in L \\iff \\exists y"), r"x \in L \iff \exists y")
        self.assertEqual(normalize_latex("L\x08le_p L' \text{and} \varphi"), r"L\le_p L' \text{and} \varphi")
        self.assertEqual(normalize_latex(r"\langle G\_\\\phi, m \rangle"), r"\langle G_\phi, m \rangle")
        self.assertEqual(normalize_latex(r"\\\phi \in 3\text{-CNF-SAT} \iff \\langle G\_\\\phi, m \rangle \in \text{IS}"), r"\phi \in 3\text{-CNF-SAT} \iff \langle G_\phi, m \rangle \in \text{IS}")
        self.assertTrue(is_formula_notation(r"\phi \in 3\text{-CNF-SAT} \iff \langle G_\phi, m \rangle \in \text{IS}"))
        self.assertFalse(is_formula_notation("הגדרה: $x \\in L$"))
    def test_missing_materials_fails_safely(self):
        course=create_course({"name":"קורס"}); lecture=create_lecture(course["id"],{"title":"א","type":"lecture"})
        with self.assertRaises(ValueError): process(lecture["id"])

    def test_pdf_export_with_mixed_rtl_content(self):
        pdf = export_notebook("rtl-fixture", "בדיקת RTL", "# עברית עם Python ו-12\n\n$O(n log n)$\n\n```python\nprint(12)\n```")
        self.assertTrue(pdf.exists())
        self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))

    def test_exam_focus_requires_and_reuses_processed_knowledge(self):
        course=create_course({"name":"בדיקת סיכום"}); lecture=create_lecture(course["id"],{"title":"גרפים","type":"lecture"})
        with self.assertRaisesRegex(ValueError, "יש לעבד"):
            generate_exam_focus(lecture["id"])
        source=DATA_DIR/"materials"/lecture["id"]/"fixture.txt"; source.parent.mkdir(parents=True,exist_ok=True); source.write_text("פסקה ראשונה\n\nPython בפסקה שנייה",encoding="utf-8")
        with db.connection() as con:
            con.execute("INSERT INTO materials VALUES (?,?,?,?,?,?,?)", (uid(),lecture["id"],"transcript","notes.txt",str(source.relative_to(DATA_DIR)),"text/plain",now()))
        process(lecture["id"])
        saved_knowledge = json.loads(db.row("SELECT content_json FROM lecture_knowledge WHERE lecture_id=?",(lecture["id"],))["content_json"])
        self.assertEqual(saved_knowledge["version"], 4)
        self.assertIn("sections", saved_knowledge)
        qa_job = db.row("SELECT status, detail FROM jobs WHERE lecture_id=? AND stage='Mathematical quality assurance'", (lecture["id"],))
        self.assertEqual(qa_job["status"], "completed")
        self.assertIn("נבדקו", qa_job["detail"])
        self.assertEqual(db.rows("SELECT * FROM outputs WHERE lecture_id=?",(lecture["id"],))[0]["kind"], "notebook")
        created=generate_exam_focus(lecture["id"])
        self.assertEqual(created["status"],"generated")
        exam_content = db.row("SELECT content FROM outputs WHERE lecture_id=? AND kind='exam_focus'", (lecture["id"],))["content"]
        self.assertIn("סיכום ממוקד למבחן", exam_content)
        self.assertNotIn("סיבוכיות לדוגמה", exam_content)
        existing=generate_exam_focus(lecture["id"])
        self.assertEqual(existing["status"],"existing")
        self.assertEqual(len(db.rows("SELECT * FROM outputs WHERE lecture_id=? AND kind='exam_focus'",(lecture["id"],))),1)
        self.assertEqual(len(db.rows("SELECT * FROM transcript_segments WHERE lecture_id=?",(lecture["id"],))),2)
        alignments = db.rows("SELECT * FROM alignments WHERE lecture_id=?", (lecture["id"],))
        self.assertEqual(len(alignments), 2)
        self.assertTrue(all(json.loads(alignment["slide_numbers_json"]) == [] for alignment in alignments))

    def test_srt_and_vtt_segments_keep_source_timestamps(self):
        segments=parse_transcript_text("1\n00:00:01,250 --> 00:00:03,500\nשלום Python\n\n2\n00:00:04,000 --> 00:00:05,000\nסיום", ".srt")
        self.assertEqual(len(segments),2)
        self.assertEqual(segments[0]["start_seconds"],1.25)
        self.assertEqual(segments[1]["end_seconds"],5.0)
        self.assertEqual(segments[0]["text_content"],"שלום Python")

    def test_recording_is_saved_as_waiting_for_provider_not_transcribed(self):
        course=create_course({"name":"Media"}); lecture=create_lecture(course["id"],{"title":"Audio","type":"lecture"})
        source=DATA_DIR/"materials"/lecture["id"]/"recording.mp3"; source.parent.mkdir(parents=True,exist_ok=True); source.write_bytes(b"test-audio")
        with db.connection() as con:
            con.execute("INSERT INTO materials VALUES (?,?,?,?,?,?,?)", (uid(),lecture["id"],"recording","recording.mp3",str(source.relative_to(DATA_DIR)),"audio/mpeg",now()))
        process(lecture["id"])
        job=db.row("SELECT * FROM jobs WHERE lecture_id=? AND stage='Processing recording/transcript' ORDER BY created_at DESC",(lecture["id"],))
        self.assertEqual(job["status"],"waiting_for_ai")
        knowledge=json.loads(db.row("SELECT content_json FROM lecture_knowledge WHERE lecture_id=?",(lecture["id"],))["content_json"])
        self.assertEqual(knowledge["recordings"][0]["status"],"ready_for_transcription")

    def test_gemini_media_inputs_are_classified_without_exposing_secrets(self):
        self.assertEqual(GeminiProvider._input_type("application/pdf"), "document")
        self.assertEqual(GeminiProvider._input_type("audio/mpeg"), "audio")
        self.assertEqual(GeminiProvider._input_type("video/mp4"), "video")

    def test_alignment_validation_keeps_only_real_segments_and_slides(self):
        segments = [{"id": "segment-a", "source_locator": "notes · segment 1"}, {"id": "segment-b", "source_locator": "notes · segment 2"}]
        slides = [{"slide_number": 1}, {"slide_number": 2}]
        aligned = validate_alignment_results({"segments": [
            {"segment_id": "segment-a", "slide_numbers": [2, 99], "topic": "נושא", "confidence": .8, "relationship": "explains"},
            {"segment_id": "invented", "slide_numbers": [1], "topic": "לא", "confidence": 1, "relationship": "explains"}
        ]}, slides, segments)
        self.assertEqual(len(aligned), 2)
        self.assertEqual(aligned[0]["slide_numbers"], [2])
        fallback = next(item for item in aligned if item["segment_id"] == "segment-b")
        self.assertEqual(fallback["relationship"], "no_specific_slide")

    def test_lecture_knowledge_and_notebook_are_grounded_in_real_sources(self):
        lecture = {"title": "BFS"}
        slides = [{"slide_number": 3, "title": "BFS", "text_content": "Queue"}]
        segments = [{"id": "segment-1", "source_locator": "lecture.vtt · segment 1", "text_content": "זה חשוב למבחן"}]
        analysis = {"sections": [{"title": "BFS", "summary": "מעבר לפי שכבות", "explanation": "משתמשים בתור.", "prerequisite_background": "גרף", "examples": ["מתחילים בצומת מקור"], "key_points": ["מסמנים בביקור"], "formulas": [], "algorithms": ["מכניסים לתור"], "proof_outline": [], "slide_numbers": [3, 999], "transcript_segment_ids": ["segment-1", "invented"], "certainty": "source_fact"}], "lecturer_notes": [{"note": "חשוב למבחן", "transcript_segment_ids": ["segment-1"], "kind": "exam_comment"}, {"note": "ללא מקור", "transcript_segment_ids": ["invented"], "kind": "exam_comment"}], "visual_findings": [{"slide_number": 3, "kind": "graph", "description": "גרף שכבות", "related_section_title": "BFS", "certainty": "source_fact"}], "generated_diagrams": [{"title": "תהליך BFS", "description": "סדר הביקור", "related_section_title": "BFS", "nodes": [{"id": "a", "label": "מקור"}, {"id": "b", "label": "תור"}], "edges": [{"from_id": "a", "to_id": "b", "label": "הכנסה"}], "slide_numbers": [3], "transcript_segment_ids": [], "certainty": "source_fact"}], "uncertainties": []}
        knowledge = build_lecture_knowledge(lecture, slides, segments, [], analysis, [])
        self.assertEqual(knowledge["version"], 4)
        self.assertEqual(knowledge["sections"][0]["slide_numbers"], [3])
        self.assertEqual(knowledge["sections"][0]["transcript_segment_ids"], ["segment-1"])
        self.assertEqual(len(knowledge["lecturer_notes"]), 1)
        self.assertEqual(len(knowledge["generated_diagrams"]), 1)
        materialize_generated_diagrams("diagram-test", knowledge)
        self.assertEqual(knowledge["generated_diagrams"][0]["id"], "diagram-1")
        self.assertTrue((DATA_DIR / "generated" / "diagram-test" / "diagram-1.svg").read_text(encoding="utf-8").startswith("<svg"))
        notebook = render_notebook_from_knowledge(knowledge)
        self.assertIn("[עמוד 3](#slide-3)", notebook)
        self.assertIn("[[slide-preview:3]]", notebook)
        self.assertIn("[[generated-diagram:diagram-1]]", notebook)
        self.assertIn("הערת מרצה על מבחן", notebook)
        self.assertIn("### הרעיון", notebook)
        self.assertIn("[[learning-bridge:לפני שממשיכים|גרף]]", notebook)
        self.assertIn("[[guided-example:דוגמה|מתחילים בצומת מקור]]", notebook)

    def test_exam_focus_keeps_only_known_sources_and_explicit_exam_notes(self):
        knowledge = {"lecture_title": "BFS", "source_index": {"slides": [{"number": 3, "title": "BFS"}], "transcript_segments": [{"id": "segment-1", "locator": "lecture.vtt · segment 1"}]}}
        raw = {"sections": [{"title": "BFS", "recall_points": ["Queue"], "formula_or_algorithm": ["O(V+E)"], "common_confusions": [], "slide_numbers": [3, 99], "certainty": "source_fact"}], "lecturer_exam_notes": [{"note": "חשוב למבחן", "transcript_segment_ids": ["segment-1", "invented"]}], "uncertainties": []}
        exam = validate_exam_focus(raw, knowledge)
        self.assertEqual(exam["sections"][0]["slide_numbers"], [3])
        self.assertEqual(exam["lecturer_exam_notes"][0]["transcript_segment_ids"], ["segment-1"])
        self.assertIn("דגשים מפורשים של המרצה למבחן", render_exam_focus("BFS", exam, knowledge))

    def test_course_outputs_combine_processed_lectures_without_raw_reuploads(self):
        course = create_course({"name": "Algorithms"})
        first = create_lecture(course["id"], {"title": "Week 1", "number": 1})
        second = create_lecture(course["id"], {"title": "Week 2", "number": 2})
        for lecture, text in ((first, "First source"), (second, "Second source")):
            source = DATA_DIR / "materials" / lecture["id"] / "source.txt"; source.parent.mkdir(parents=True, exist_ok=True); source.write_text(text, encoding="utf-8")
            with db.connection() as con: con.execute("INSERT INTO materials VALUES (?,?,?,?,?,?,?)", (uid(), lecture["id"], "transcript", "source.txt", str(source.relative_to(DATA_DIR)), "text/plain", now()))
            process(lecture["id"])
        self.assertEqual(generate_course_notebook(course["id"])["lecture_count"], 2)
        self.assertEqual(generate_course_exam_focus(course["id"])["lecture_count"], 2)
        outputs = db.rows("SELECT * FROM course_outputs WHERE course_id=? ORDER BY kind", (course["id"],))
        self.assertEqual([output["kind"] for output in outputs], ["course_exam_focus", "course_notebook"])
        self.assertIn(f"#lecture-{first['id']}", outputs[1]["content"])
        self.assertIn(f"#lecture-{second['id']}", outputs[1]["content"])

    def test_recording_transcript_does_not_claim_unavailable_timestamps(self):
        transcript = validate_recording_transcript({"segments": [
            {"start_seconds": 2.5, "end_seconds": 4.0, "timestamp_status": "exact", "text_content": "שלום"},
            {"start_seconds": 0, "end_seconds": 0, "timestamp_status": "unavailable", "text_content": "הסבר נוסף"},
            {"start_seconds": 5, "end_seconds": 3, "timestamp_status": "exact", "text_content": "זמן לא תקין"}
        ]})
        self.assertEqual(len(transcript), 3)
        self.assertEqual(transcript[0]["start_seconds"], 2.5)
        self.assertIsNone(transcript[1]["start_seconds"])
        self.assertIsNone(transcript[2]["end_seconds"])

    def test_management_updates_and_course_deletion_keep_source_files(self):
        course=create_course({"name":"Course","code":"C1"}); lecture=create_lecture(course["id"],{"title":"Week 1","type":"lecture"})
        self.assertEqual(update_course(course["id"],{"name":"Updated"})["name"],"Updated")
        self.assertEqual(update_lecture(lecture["id"],{"title":"Updated lecture","type":"exercise"})["type"],"exercise")
        source=DATA_DIR/"materials"/lecture["id"]/"source.txt"; source.parent.mkdir(parents=True,exist_ok=True); source.write_text("source",encoding="utf-8")
        with db.connection() as con: con.execute("INSERT INTO materials VALUES (?,?,?,?,?,?,?)",(uid(),lecture["id"],"transcript","source.txt",str(source.relative_to(DATA_DIR)),"text/plain",now()))
        delete_course_record(course["id"])
        self.assertIsNone(db.row("SELECT * FROM courses WHERE id=?",(course["id"],)))
        self.assertTrue(source.exists())

    def test_pdf_slide_extraction_preserves_page_numbers_and_text(self):
        course=create_course({"name":"PDF"}); lecture=create_lecture(course["id"],{"title":"Slides","type":"lecture"})
        source=DATA_DIR/"materials"/lecture["id"]/"slides.pdf"; source.parent.mkdir(parents=True,exist_ok=True)
        canvas=Canvas(str(source)); canvas.drawString(72,720,"First slide title"); canvas.showPage(); canvas.drawString(72,720,"Second slide title"); canvas.save()
        material={"id":uid(),"stored_path":str(source.relative_to(DATA_DIR))}
        self.assertEqual(extract_presentation(lecture["id"],material),2)
        slides=db.rows("SELECT * FROM slides WHERE lecture_id=? ORDER BY slide_number",(lecture["id"],))
        self.assertEqual([slide["slide_number"] for slide in slides],[1,2])
        self.assertIn("Second slide title",slides[1]["text_content"])

if __name__ == '__main__': unittest.main()
