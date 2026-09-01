# Architecture

`app/main.py` is a thin local HTTP UI/API layer. `services.py` holds course, storage, processing and AI-provider boundaries. `db.py` owns SQLite schema/migrations-by-initialization; `LocalStorage` owns paths beneath `data/`; `GeminiProvider` is replaceable; `pdf_export.py` is a document-rendering boundary. Gemini uses the current Interactions API with strict JSON schemas; source files are uploaded only for an explicit processing request and deleted from the Files API when analysis finishes.

SQLite uses foreign keys for courses, lectures, materials, jobs, outputs, slides, visual elements, alignments and source references. The data layout is `data/database`, `data/materials/<lecture-id>`, `data/generated/<lecture-id>`, `data/temp`, and `data/logs`.

Pipeline stages are recorded as jobs: validate, analyze presentation, process transcript/recording, align, inspect visuals, build knowledge, generate notebook/exam focus, save. The offline implementation intentionally makes no unsupported lecturer claims; a Gemini-backed adapter is the extension point for structured slide/visual/alignment results.

RTL: the HTML is RTL-first and uses `bdi dir=ltr` for identifiers, code and numbers. Code uses LTR fenced blocks. PDF export registers Windows Arial and applies a narrowly-scoped visual bidi transform before right-aligned drawing, keeping English technical runs intact. A mixed Hebrew/English/code/math test covers the transform. Server migration replaces only `LocalStorage` and `db` implementations.
