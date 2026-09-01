const $ = selector => document.querySelector(selector);
const api = async (url, options = {}) => {
  const {timeoutMs = 0, ...fetchOptions} = options;
  const controller = timeoutMs ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  let response;
  try { response = await fetch(url, {...fetchOptions, signal:controller?.signal}); }
  catch (error) { if (error.name === "AbortError") throw Error("הפעולה לא קיבלה תשובה בזמן. בדקי שהאתר עדיין פתוח ונסי שוב."); throw error; }
  finally { if (timer) clearTimeout(timer); }
  const payload = await response.json();
  if (!response.ok) throw Error(payload.error || "אירעה שגיאה");
  return payload;
};
let selectedCourse;

function esc(value = "") { return String(value).replace(/[&<>]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[char])); }
let progressTimer;
function showProcessing(message = "מעבד את החומר…", lectureId = "") { $("#processing-message").textContent = message; $("#processing-indicator").hidden = false; clearInterval(progressTimer); if (lectureId) { const update = async () => { try { const data = await api(`/api/lectures/${lectureId}`); const jobs = data.jobs || [], total = jobs.length || 1, complete = jobs.filter(job => job.status === "completed" || job.status === "waiting_for_ai").length, current = jobs.find(job => job.status === "running"); const percent = Math.min(99, Math.round(complete / total * 100)); $("#processing-message").textContent = `${current ? current.detail : message} (${percent}%)`; if (data.lecture?.status !== "processing") hideProcessing(); } catch {} }; update(); progressTimer = setInterval(update, 1200); } }
function hideProcessing() { clearInterval(progressTimer); $("#processing-indicator").hidden = true; }
function normalizeLatex(value = "") {
  const protectedText = [];
  let latex = String(value).trim().replace(/^\$+|\$+$/g, "");
  // Some model responses contain JSON escape control characters instead of a
  // literal backslash (for example, \t + "ext" instead of "\\text").
  // Repair only known mathematical commands before rendering.
  latex = latex.replace(/\x08ar/g, "\\bar").replace(/\x08igwedge/g, "\\bigwedge")
    .replace(/\x08igvee/g, "\\bigvee").replace(/\x08le/g, "\\le")
    .replace(/\x08in/g, "\\in").replace(/\x08subseteq/g, "\\subseteq")
    .replace(/\x08dots/g, "\\dots").replace(/\x08v/g, "\\vee")
    .replace(/\text/g, "\\text").replace(/\theta/g, "\\theta")
    .replace(/\to/g, "\\to").replace(/\varphi/g, "\\varphi");
  latex = latex.replace(/\\_/g, "_").replace(/\\{2,}(?=(?:langle|rangle|phi|varphi|psi|alpha|beta|gamma|delta|chi|sigma|in|iff|leq|geq|le|ge|to|text|exists|forall|land|lor|vee|wedge|overline|subseteq|setminus|dots)\b)/g, "\\");
  latex = latex.replace(/\\text\{(?:[^{}]|\{[^{}]*\})*\}/g, match => `@@TEXT${protectedText.push(match) - 1}@@`);
  latex = latex.replace(/\\\*/g, "*").replace(/\\</g, "<").replace(/\\>/g, ">").replace(/\*([A-Za-z])\*/g, "$1").replace(/^\\-\s*(?=\|)/, "").replace(/\\\\(iff|in|exists|forall|leq|geq|le|ge|to|mid|alpha|beta|gamma|delta|phi|psi|sigma|text)\b/g, "\\$1").replace(/\\lep\b/g, "\\le_p").replace(/<=_p\b/g, "\\le_p");
  latex = latex.replace(/\b([A-Z])(\d+)\b/g, "$1_{$2}").replace(/\bGphi\b/g, "G_\\phi");
  [["alpha","\\alpha"],["beta","\\beta"],["gamma","\\gamma"],["delta","\\delta"],["phi","\\phi"],["psi","\\psi"],["sigma","\\sigma"]].forEach(([word, command]) => { latex = latex.replace(new RegExp(`\\b${word}\\b`, "gi"), command); });
  latex = latex.replace(/(?<!\\)\biff\b/gi, "\\iff").replace(/(?<!\\)\bin\b/gi, "\\in").replace(/>=/g, "\\geq").replace(/<=/g, "\\leq").replace(/(?<!\\)<\s*([^<>\n]*,[^<>\n]*)\s*>/g, "\\langle $1 \\rangle");
  latex = latex.replace(/\{\s*L\s*\|\s*(?:there exists a polynomial-time algorithm for\s+L|L\s+is solvable by a polynomial-time algorithm)\s*\}/i, "\\{ L \\mid L \\text{ is solvable by a polynomial-time algorithm} \\}");
  latex = latex.replace(/\bthere exists\b/gi, "\\text{there exists}").replace(/\bf\s+runs in polynomial time\b/gi, "f \\text{ runs in polynomial time}");
  return latex.replace(/@@TEXT(\d+)@@/g, (_, index) => protectedText[Number(index)]);
}
function looksLikeFormula(value = "") {
  return /\\[A-Za-z]+|:=|<=_p|\\le_p|\b(?:iff|in)\b|[∈⇔≤≥∀∃]|(?:^|\s)[A-Za-z][A-Za-z0-9_]*\s*(?:=|<|>)/.test(String(value));
}
function isExplicitInlineFormula(value = "") {
  // Inline math is opt-in: a dollar-delimited fragment must contain actual
  // notation. This prevents ordinary English terms from becoming equations.
  const latex = normalizeLatex(value);
  return /\\[A-Za-z]+|:=|[=<>∈⇔≤≥∀∃]|\^[{A-Za-z0-9]|_[{A-Za-z0-9]|\|[A-Za-z]|\bO\(/.test(latex);
}
function isDisplayFormula(value = "") {
  const formula = String(value).trim();
  // A display block is exclusively mathematical. If it contains Hebrew prose
  // or nested dollar delimiters, keep the prose in the paragraph and render
  // only its explicitly marked inline expressions.
  return !/[\u0590-\u05ff$]/.test(formula) && isExplicitInlineFormula(formula);
}
function mathMarkup(value = "", displayMode = false) {
  const latex = normalizeLatex(value);
  try {
    if (!window.katex) throw Error("KaTeX was not loaded");
    return window.katex.renderToString(latex, {displayMode, throwOnError:true, strict:"ignore", trust:false, output:"htmlAndMathml"});
  } catch (error) {
    console.warn("Invalid LaTex formula", latex, error);
    return `<span class="math-error" title="נוסחה שלא ניתן להציג">${esc(latex)}</span>`;
  }
}
function normalizeFormulaBlocks(value = "") {
  let formulaSection = false;
  return String(value).split("\n").map(line => {
    const heading = line.match(/^###\s+(.+)$/);
    if (heading) formulaSection = /נוסחאות|formula|equation/i.test(heading[1]);
    const bullet = formulaSection && line.match(/^[-•]\s+(.+)$/);
    return bullet && looksLikeFormula(bullet[1]) ? `$$ ${bullet[1]} $$` : line;
  }).join("\n");
}
function markdown(value, presentationId = "", lectureId = "") {
  const formulas = [];
  const token = (formula, display) => `@@MATH${formulas.push({formula, display}) - 1}@@`;
  const protectedValue = normalizeFormulaBlocks(value.replace(/שקופית (?=\d)/g,"עמוד "))
    .replace(/\$\$\s*([\s\S]*?)\s*\$\$/g, (_, formula) => isDisplayFormula(formula) ? token(formula, true) : formula)
    .replace(/\$([^$\n]+)\$/g, (_, formula) => isExplicitInlineFormula(formula) ? token(formula, false) : esc(formula));
  let html = esc(protectedValue).replace(/^### (.*)$/gm,"<h3>$1</h3>").replace(/^## (.*)$/gm,"<h2>$1</h2>").replace(/^# (.*)$/gm,"<h1>$1</h1>")
    .replace(/```\w*\n([\s\S]*?)```/g,"<pre dir=\"ltr\">$1</pre>").replace(/`([^`]+)`/g,"<code dir=\"ltr\">$1</code>")
    .replace(/\[([^\]]+)\]\(#slide-(\d+)\)/g,"<a href=\"#slide-$2\" onclick=\"showSlide($2); return false;\">$1</a>")
    .replace(/\[([^\]]+)\]\(#course-([a-f0-9-]+)-slide-(\d+)\)/gi,"<a href=\"#\" onclick=\"openLectureSlide('$2',$3); return false;\">$1</a>")
    .replace(/\[([^\]]+)\]\(#lecture-([a-f0-9-]+)\)/gi,"<a href=\"#\" onclick=\"openLecture('$2')\">$1</a>")
    .replace(/\[\[slide-preview:(\d+)]]/g, (_, number) => presentationId ? `<figure class="notebook-visual"><iframe title="שקופית מקור ${number}" class="slide-frame" src="/api/materials/${presentationId}/file#page=${number}"></iframe><figcaption>שקף המקור הרלוונטי — <a href="#" onclick="showSlide(${number})">פתיחה במצגת</a></figcaption></figure>` : `<button class="secondary" onclick="showSlide(${number})">פתיחת שקף המקור ${number}</button>`)
    .replace(/\[\[generated-diagram:([a-z0-9-]+)]]/g, (_, diagramId) => lectureId ? `<figure class="notebook-visual"><img style="max-width:100%;border:1px solid #dce5ea;border-radius:8px" alt="תרשים הסבר שנוצר על ידי המערכת" src="/api/lectures/${lectureId}/diagrams/${diagramId}"><figcaption>תרשים הסבר שנוצר על ידי המערכת</figcaption></figure>` : "")
    .replace(/\[\[course-diagram:([a-f0-9-]+):([a-z0-9-]+)]]/gi, (_, sourceLectureId, diagramId) => `<figure class="notebook-visual"><img style="max-width:100%;border:1px solid #dce5ea;border-radius:8px" alt="תרשים הסבר שנוצר על ידי המערכת" src="/api/lectures/${sourceLectureId}/diagrams/${diagramId}"><figcaption>תרשים הסבר שנוצר על ידי המערכת</figcaption></figure>`)
    .replace(/\[\[learning-bridge:([^|\]]+)\|([^\]]*)]]/g, (_, title, body) => `<aside class="learning-bridge"><b>${title}</b><span>${body}</span></aside>`)
    .replace(/\[\[guided-example:([^|\]]+)\|([^\]]*)]]/g, (_, title, body) => `<aside class="guided-example"><b>${title}</b><span>${body}</span></aside>`)
    .replace(/^> \*\*(.*?)\*\*:?\s*(.*)$/gm,"<div class=\"note\"><b>$1</b> $2</div>")
    .replace(/^[-•] (.*)$/gm,"<div class=\"bullet\">$1</div>").replace(/\*\*(.*?)\*\*/g,"<b>$1</b>").replace(/\n/g,"<br>");
  return html.replace(/@@MATH(\d+)@@/g, (_, index) => {
    const item = formulas[Number(index)];
    return item.display ? `<div class="equation" dir="ltr">${mathMarkup(item.formula, true)}</div>` : `<span class="math-box" dir="ltr">${mathMarkup(item.formula, false)}</span>`;
  });
}

async function loadCourses() {
  const courses = await api("/api/courses");
  $("#courses").innerHTML = courses.map(course => `<div class="course ${course.id === selectedCourse ? "active" : ""}" onclick="openCourse('${course.id}')"><b>${esc(course.name)}</b><br><small>${esc(course.code || "ללא קוד")}</small></div>`).join("") || "<p class=\"muted\">עדיין אין קורסים</p>";
}
function modal(html, onSubmit) {
  const dialog = $("#modal"), form = $("#form");
  form.innerHTML = html;
  form.onsubmit = event => { event.preventDefault(); onSubmit(form).catch(error => alert(error.message)); };
  dialog.showModal();
}
function courseForm(course) {
  const data = course || {};
  modal(`<h2>${course ? "עריכת קורס" : "קורס חדש"}</h2><label>שם הקורס<input name="name" required value="${esc(data.name || "")}"></label><label>קוד<input name="code" value="${esc(data.code || "")}"></label><label>סמסטר<input name="semester" value="${esc(data.semester || "")}"></label><label>שנה אקדמית<input name="academic_year" value="${esc(data.academic_year || "")}"></label><label>תיאור<textarea name="description">${esc(data.description || "")}</textarea></label><button>שמירה</button><button type="button" class="secondary" onclick="this.closest('dialog').close()">ביטול</button>`, async form => {
    await api(course ? `/api/courses/${course.id}` : "/api/courses", {method:course ? "PUT" : "POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(Object.fromEntries(new FormData(form)))});
    form.closest("dialog").close(); await loadCourses(); if (course) openCourse(course.id);
  });
}
async function settingsForm() {
  try {
    const settings = await api("/api/settings");
    modal(`<h2>הגדרות עיבוד</h2><label><input type="checkbox" name="auto_reprocess_on_upload" ${settings.auto_reprocess_on_upload ? "checked" : ""}> עבד מחדש את ההרצאה אוטומטית לאחר העלאת קובץ חדש</label><p class="muted">כאשר ההגדרה פעילה, המחברת נבנית שוב מכל חומרי המקור של ההרצאה לאחר כל העלאה.</p><button>שמירה</button><button type="button" class="secondary" onclick="this.closest('dialog').close()">ביטול</button>`, async form => {
      await api("/api/settings", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({auto_reprocess_on_upload:form.auto_reprocess_on_upload.checked})});
      form.closest("dialog").close();
    });
  } catch (error) { alert(error.message); }
}
function lectureForm(courseId, lecture) {
  const data = lecture || {};
  modal(`<h2>${lecture ? "עריכת יחידת לימוד" : "יחידת לימוד חדשה"}</h2><label>כותרת<input name="title" required value="${esc(data.title || "")}"></label><label>סוג<select name="type"><option value="lecture" ${data.type !== "exercise" ? "selected" : ""}>הרצאה</option><option value="exercise" ${data.type === "exercise" ? "selected" : ""}>תרגול</option></select></label><label>מספר<input name="number" type="number" value="${esc(data.number || "")}"></label><label>תאריך<input name="lecture_date" type="date" value="${esc(data.lecture_date || "")}"></label><button>שמירה</button><button type="button" class="secondary" onclick="this.closest('dialog').close()">ביטול</button>`, async form => {
    await api(lecture ? `/api/lectures/${lecture.id}` : `/api/courses/${courseId}/lectures`, {method:lecture ? "PUT" : "POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(Object.fromEntries(new FormData(form)))});
    form.closest("dialog").close(); lecture ? openLecture(lecture.id) : openCourse(courseId);
  });
}
async function openCourse(id) {
  selectedCourse = id; const data = await api(`/api/courses/${id}`); await loadCourses();
  const courseNotebook = (data.course_outputs || []).find(output => output.kind === "course_notebook"), courseExam = (data.course_outputs || []).find(output => output.kind === "course_exam_focus");
  const courseOutput = courseNotebook || courseExam ? `<div class="tabs"><button class="active" onclick="courseTab('course-notebook')">📖 מחברת קורס</button><button onclick="courseTab('course-exam')">🎯 סיכום קורס למבחן</button></div><article id="course-notebook" class="notebook">${courseNotebook ? markdown(courseNotebook.content) : "<p class=\"muted\">מחברת הקורס עדיין לא נוצרה.</p>"}</article><article id="course-exam" class="notebook" hidden>${courseExam ? markdown(courseExam.content) : "<p class=\"muted\">סיכום הקורס עדיין לא נוצר.</p>"}</article>` : "";
  $("#content").innerHTML = `<div class="panel"><small>${esc(data.course.code || "")}</small><h2>${esc(data.course.name)}</h2><p>${esc(data.course.description || "")}</p><div class="toolbar"><button onclick="lectureForm('${id}')">+ הרצאה / תרגול</button><button onclick="processCourse('${id}')">📖 יצירת מחברת קורס</button><button onclick="generateCourseExam('${id}')">🎯 יצירת סיכום קורס למבחן</button><button class="secondary" onclick='courseForm(${JSON.stringify(data.course)})'>עריכת קורס</button><button class="secondary" onclick="deleteCourse('${id}')">מחיקת קורס</button></div><div class="grid">${data.lectures.map(lecture => `<div class="card"><small>${lecture.type === "lecture" ? "הרצאה" : "תרגול"} • ${esc(lecture.status)}</small><h3>${esc(lecture.title)}</h3><button onclick="openLecture('${lecture.id}')">פתיחה</button></div>`).join("") || "<p class=\"muted\">צרו את יחידת הלימוד הראשונה.</p>"}</div>${courseOutput}</div>`;
}
async function deleteCourse(id) { if (confirm("למחוק את הקורס ואת רשומותיו? קבצי המקור נשמרים ולא נמחקים.")) { await api(`/api/courses/${id}`,{method:"DELETE"}); selectedCourse = null; $("#content").innerHTML = "<div class=\"hero\"><h2>הקורס נמחק</h2></div>"; loadCourses(); } }
async function deleteLecture(id, courseId) { if (confirm("למחוק את ההרצאה ואת רשומותיה? קבצי המקור נשמרים ולא נמחקים.")) { await api(`/api/lectures/${id}`,{method:"DELETE"}); openCourse(courseId); } }
async function deleteMaterial(id, lectureId) { if (confirm("להסיר את החומר מההרצאה? קובץ המקור נשמר בדיסק ולא יימחק.")) { await api(`/api/materials/${id}`,{method:"DELETE"}); openLecture(lectureId); } }

function studyActions(id, hasNotebook, hasExam) { return `<div class="grid study-actions"><div class="card" id="notebook-action"><h3>🧠 מחברת ההרצאה</h3><p class="muted">מחברת לימוד מלאה ומפורטת עם הסברים, הפניות לשקפים ותרשימים רלוונטיים.</p><button onclick="processLecture('${id}')">🧠 עיבוד ההרצאה ויצירת מחברת</button>${hasNotebook ? "<p class=\"muted\">המחברת מוכנה.</p>" : ""}</div><div class="card"><h3>🎯 סיכום ממוקד למבחן</h3><p class="muted">סיכום קצר וממוקד לחזרה למבחן, המבוסס על ההבנה המלאה של ההרצאה.</p><button class="${hasNotebook ? "" : "secondary"}" onclick="generateExamFocus('${id}',${hasExam})">${hasExam ? "יצירה מחדש של סיכום ממוקד למבחן" : "🎯 יצירת סיכום ממוקד למבחן"}</button>${hasExam ? "<p class=\"muted\">הסיכום למבחן מוכן.</p>" : ""}</div></div>`; }
function alignmentNumbers(alignment) { try { return JSON.parse(alignment.slide_numbers_json || "[]"); } catch { return []; } }
async function openLecture(id) {
  const data = await api(`/api/lectures/${id}`), lecture = data.lecture, notebook = data.outputs.find(item => item.kind === "notebook"), exam = data.outputs.find(item => item.kind === "exam_focus");
  const manualEdit = data.notebook_edit;
  const examEdit = data.exam_edit;
  if (lecture.status !== "processing") hideProcessing();
  let knowledge = null; try { knowledge = data.knowledge ? JSON.parse(data.knowledge.content_json) : null; } catch { knowledge = null; }
  const materials = data.materials.map(material => `<li><b>${esc(material.kind)}</b> — ${esc(material.original_name)} <button class="secondary" onclick="deleteMaterial('${material.id}','${id}')">הסרה</button></li>`).join("") || "<li>אין חומרים</li>";
  const alignmentsBySegment = new Map((data.alignments || []).map(alignment => [alignment.segment_id, alignment]));
const transcriptSummary = data.transcript_segments.length ? `<details class="transcript-source"><summary>תמלול ומקטעי מקור (${data.transcript_segments.length})</summary>${data.transcript_segments.map(segment => { const alignment = alignmentsBySegment.get(segment.id), numbers = alignment ? alignmentNumbers(alignment) : []; const links = numbers.length ? numbers.map(number => `<button class="secondary link-button" onclick="showSlide(${number})">שקופית ${number}</button>`).join("") : "ללא שיוך ודאי לשקופית"; return `<p id="segment-${segment.id}"><small>${esc(segment.source_locator)}</small><br>${esc(segment.text_content)}<br><span class="alignment">${alignment ? esc(alignment.topic) + ": " : ""}${links}</span></p>`; }).join("")}</details>` : "";
  const pdf = data.materials.find(item => item.kind === "presentation");
const slides = data.slides.map(slide => { const related = (data.alignments || []).filter(alignment => alignmentNumbers(alignment).includes(slide.slide_number)); const relatedSegments = related.length ? `<p class="alignment"><b>מקטעי תמלול קשורים:</b> ${related.map(alignment => `<button class="secondary link-button" onclick="showSourceSegment('${alignment.segment_id}')">${esc(alignment.topic)}</button>`).join("")}</p>` : ""; const relatedSections = (knowledge?.sections || []).map((section, index) => ({section,index})).filter(item => item.section.slide_numbers.includes(slide.slide_number)); const notebookLinks = relatedSections.length ? `<p class="alignment"><b>הסברים במחברת:</b> ${relatedSections.map(item => `<button class="secondary link-button" onclick="showNotebookSection(${item.index})">${esc(item.section.title)}</button>`).join("")}</p>` : ""; return `<div class="slide" id="slide-${slide.slide_number}"><h3>שקופית ${slide.slide_number}: ${esc(slide.title || "")}</h3>${pdf ? `<iframe title="שקופית ${slide.slide_number}" class="slide-frame" src="/api/materials/${pdf.id}/file#page=${slide.slide_number}"></iframe>` : ""}<p>${esc(slide.text_content || "לא נמצא טקסט בשקופית זו.")}</p>${relatedSegments}${notebookLinks}<button class="secondary" onclick="tab('notebook')">הסבר במחברת</button></div>`; }).join("") || "<p class=\"muted\">השקפים ינותחו לאחר עיבוד המצגת.</p>";
  const notebookHtml = manualEdit ? manualEdit.html_content : (notebook ? markdown(notebook.content,pdf?.id,id) : "");
  const examHtml = examEdit ? examEdit.html_content : (exam ? markdown(exam.content) : "<p class=\"muted\">הסיכום הממוקד עדיין לא נוצר.</p>");
  const editToolbar = manualEdit?.pending_content ? `<div class="manual-conflict"><b>המחברת עודכנה בעקבות עיבוד חדש.</b><span>מה תרצי לשמור?</span><button onclick="resolveNotebookEdit('${id}','keep')">לשמור את העריכה שלי</button><button class="secondary" onclick="resolveNotebookEdit('${id}','adopt')">לאמץ את הגרסה החדשה</button></div>` : `<div id="notebook-edit-toolbar" class="toolbar notebook-edit-toolbar"><button id="edit-document-button" class="secondary" onclick="startNotebookEdit('${id}')">✏️ עריכת מחברת</button><button id="repair-formula-button" class="secondary" onclick="repairSelectedFormula('${id}')">∑ תקן נוסחה מסומנת</button></div>`;
  const completedStages = data.jobs.filter(job => job.status === "completed" || job.status === "waiting_for_ai").length, progressPercent = data.jobs.length ? Math.round(completedStages / data.jobs.length * 100) : 0, runningStage = data.jobs.find(job => job.status === "running");
  const statusText = lecture.status === "processing" ? `מעבד מחדש (${progressPercent}%) — המחברת הקודמת זמינה בינתיים${runningStage ? ` • ${esc(runningStage.detail)}` : ""}` : notebook ? "המחברת מוכנה" : "טרם עובד";
  $("#content").innerHTML = `<div class="panel"><button class="secondary" onclick="openCourse('${lecture.course_id}')">חזרה לקורס</button><h2>${esc(lecture.title)}</h2><p class="muted">סטטוס: ${statusText}${exam ? " • סיכום למבחן מוכן" : ""}</p><div class="toolbar"><button onclick="uploadForm('${id}')">העלאת חומר</button><button class="secondary" onclick='lectureForm("${lecture.course_id}",${JSON.stringify(lecture)})'>עריכת הרצאה</button><button class="secondary" onclick="deleteLecture('${id}','${lecture.course_id}')">מחיקת הרצאה</button>${notebook ? `<a href="/api/lectures/${id}/pdf"><button>ייצוא PDF</button></a>` : ""}</div>${studyActions(id,!!notebook,!!exam)}<h3>חומרי מקור</h3><ul>${materials}</ul>${transcriptSummary}${data.jobs.length ? `<details class="progress"><summary>התקדמות העיבוד — ${progressPercent}% (${completedStages} מתוך ${data.jobs.length} שלבים)</summary><p>${data.jobs.map(job => `${job.status === "completed" ? "✓" : job.status === "running" ? "◌" : "○"} ${esc(job.stage)}`).join(" ← ")}</p></details>` : ""}${notebook ? `<div class="tabs"><button class="active" onclick="tab('notebook')">📖 מחברת</button><button onclick="tab('exam')">🎯 סיכום למבחן</button><button onclick="tab('slides')">שקופיות</button></div>${editToolbar}<article id="notebook" class="notebook" data-lecture-id="${id}" data-base-content="${encodeURIComponent(notebook.content)}">${notebookHtml}</article>${exam ? `<article id="exam" class="notebook" data-lecture-id="${id}" data-pending-edit="${examEdit?.pending_content ? "true" : "false"}" data-base-content="${encodeURIComponent(exam.content)}" hidden>${examHtml}</article>` : ""}<article id="slides" hidden>${slides}</article>` : "<p class=\"muted\">העלו מקור ואז עבדו את ההרצאה כדי ליצור מחברת לימוד מלאה.</p>"}</div>`;
}
function tab(id) { ["notebook","exam","slides"].forEach(name => { const panel = $(`#${name}`); if (panel) panel.hidden = name !== id; }); const editButton = $("#edit-document-button"), repairButton = $("#repair-formula-button"); if (editButton) { const lectureId = $("#notebook")?.dataset.lectureId; if (id === "exam") { editButton.textContent = "✏️ עריכת סיכום ממוקד"; editButton.onclick = () => startExamEdit(lectureId); if (repairButton) repairButton.onclick = () => repairSelectedFormula(lectureId, "exam"); } else { editButton.textContent = "✏️ עריכת מחברת"; editButton.onclick = () => startNotebookEdit(lectureId); if (repairButton) repairButton.onclick = () => repairSelectedFormula(lectureId); } } document.querySelectorAll(".tabs button").forEach(button => button.classList.toggle("active", (id === "notebook" && button.textContent.includes("מחברת")) || (id === "exam" && button.textContent.includes("סיכום")) || (id === "slides" && button.textContent.includes("שקופ")))); if (id === "exam") promptExamConflict(); }
function showSlide(number) { tab("slides"); requestAnimationFrame(() => requestAnimationFrame(() => $(`#slide-${number}`)?.scrollIntoView({behavior:"smooth",block:"start"}))); }
function showSourceSegment(id) { const source = $(`#segment-${id}`); if (!source) return; source.closest("details").open = true; source.scrollIntoView(); }
function showNotebookSection(index) { tab("notebook"); setTimeout(() => document.querySelectorAll("#notebook h2")[index + 1]?.scrollIntoView(), 0); }
let notebookEditing = false;
let editingTargetId = "notebook";
let savedEditorRange = null;
function rememberEditorCursor() {
  const notebook = $(`#${editingTargetId}`), selection = window.getSelection();
  if (!notebook || !selection?.rangeCount || !notebook.contains(selection.anchorNode)) return;
  savedEditorRange = selection.getRangeAt(0).cloneRange();
}
function startNotebookEdit(lectureId, targetId = "notebook", editPath = "notebook-edit", label = "מחברת") {
  const notebook = $(`#${targetId}`);
  if (!notebook || notebookEditing) return;
  notebookEditing = true; editingTargetId = targetId;
  notebook.contentEditable = "true";
  notebook.dataset.editPath = editPath;
  notebook.classList.add("notebook-editing");
  notebook.querySelectorAll(".katex").forEach(node => node.contentEditable = "false");
  notebook.addEventListener("keyup", rememberEditorCursor);
  notebook.addEventListener("mouseup", rememberEditorCursor);
  notebook.addEventListener("input", rememberEditorCursor);
  $("#notebook-edit-toolbar").innerHTML = `<button onclick="saveNotebookEdit('${lectureId}','${targetId}')">שמירת שינויים</button><button class="secondary" onclick="cancelNotebookEdit('${lectureId}')">ביטול</button><button class="secondary" onclick="addPersonalNote()">+ הערה אישית</button><span class="muted">אפשר לערוך טקסט, להוסיף הערה במקום הסמן ולסמן קטע לתיקון נוסחה.</span>`;
  notebook.focus();
  const range = document.createRange(); range.selectNodeContents(notebook); range.collapse(false);
  const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); savedEditorRange = range.cloneRange();
}
function startExamEdit(lectureId) { startNotebookEdit(lectureId, "exam", "exam-edit", "סיכום ממוקד"); }
function cancelNotebookEdit(lectureId) { notebookEditing = false; savedEditorRange = null; openLecture(lectureId); }
async function saveNotebookEdit(lectureId, targetId = editingTargetId) {
  const notebook = $(`#${targetId}`);
  showProcessing(targetId === "exam" ? "שומר את העריכה בסיכום הממוקד…" : "שומר את העריכה במחברת…");
  try {
    await api(`/api/lectures/${lectureId}/${notebook.dataset.editPath || "notebook-edit"}`, {method:"PUT", timeoutMs:15000, headers:{"Content-Type":"application/json"}, body:JSON.stringify({base_content:decodeURIComponent(notebook.dataset.baseContent), html_content:notebook.innerHTML})});
    notebookEditing = false; await openLecture(lectureId);
  } catch (error) { alert(error.message); } finally { hideProcessing(); }
}
function addPersonalNote() {
  const notebook = $(`#${editingTargetId}`);
  if (!notebookEditing) { alert("לחצי קודם על „עריכת מחברת”."); return; }
  const range = savedEditorRange && notebook.contains(savedEditorRange.commonAncestorContainer) ? savedEditorRange.cloneRange() : null;
  if (!range) { alert("מקמי את הסמן במקום שבו תרצי להוסיף את ההערה."); return; }
  const note = document.createElement("aside");
  note.className = "personal-note";
  note.innerHTML = "<b contenteditable=\"false\">הערה אישית</b><div>כתבי כאן את ההערה שלך…</div>";
  // A selected phrase must never be replaced by a note: place the box at the
  // active caret (or immediately after a selection) instead.
  range.collapse(false); range.insertNode(note);
  const editor = note.querySelector("div");
  const noteRange = document.createRange(); noteRange.selectNodeContents(editor); noteRange.collapse(false);
  const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(noteRange);
  editor.focus(); savedEditorRange = noteRange.cloneRange();
}
async function repairSelectedFormula(lectureId, targetId = "notebook") {
  const notebook = $(`#${targetId}`), selection = window.getSelection();
  if (!notebook || !selection?.rangeCount || selection.isCollapsed || !notebook.contains(selection.anchorNode)) { alert("סמני קודם את הקטע שברצונך להפוך לנוסחה."); return; }
  const selectedText = selection.toString().trim();
  if (!selectedText) return;
  const range = selection.getRangeAt(0).cloneRange();
  showProcessing("מכין תיקון לנוסחה המסומנת…");
  try {
    const suggestion = await api(`/api/lectures/${lectureId}/formula-repair`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({selected_text:selectedText})});
    const preview = mathMarkup(suggestion.latex, false);
    modal(`<h2>תיקון נוסחה</h2><p class="muted">המערכת הציעה תיקון לקטע שסימנת. בדקי אותו לפני שמירה.</p><p><b>המקור:</b> ${esc(selectedText)}</p><div class="formula-preview" dir="ltr">${preview}</div><button>החלת התיקון</button><button type="button" class="secondary" onclick="this.closest('dialog').close()">ביטול</button>`, async form => {
      if (!notebookEditing) startNotebookEdit(lectureId, targetId, targetId === "exam" ? "exam-edit" : "notebook-edit", targetId === "exam" ? "סיכום ממוקד" : "מחברת");
      const replacement = document.createElement("span");
      replacement.className = "math-box";
      replacement.dir = "ltr";
      replacement.contentEditable = "false";
      replacement.dataset.latex = suggestion.latex;
      replacement.innerHTML = mathMarkup(suggestion.latex, false);
      range.deleteContents(); range.insertNode(replacement); range.setStartAfter(replacement); range.collapse(true); selection.removeAllRanges(); selection.addRange(range);
      form.closest("dialog").close();
      await saveNotebookEdit(lectureId, targetId);
    });
  } catch (error) { alert(error.message); } finally { hideProcessing(); }
}
async function resolveNotebookEdit(lectureId, choice) {
  const label = choice === "keep" ? "שומר את העריכה האישית…" : "מאמץ את הגרסה החדשה…";
  showProcessing(label);
  try { await api(`/api/lectures/${lectureId}/notebook-edit/resolve`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({choice})}); notebookEditing = false; await openLecture(lectureId); }
  catch (error) { alert(error.message); } finally { hideProcessing(); }
}
async function resolveExamEdit(lectureId, choice) {
  const label = choice === "keep" ? "שומר את העריכה האישית…" : "מאמץ את הגרסה החדשה…";
  showProcessing(label);
  try { await api(`/api/lectures/${lectureId}/exam-edit/resolve`, {method:"POST", timeoutMs:15000, headers:{"Content-Type":"application/json"}, body:JSON.stringify({choice})}); notebookEditing = false; await openLecture(lectureId); }
  catch (error) { alert(error.message); } finally { hideProcessing(); }
}
function promptExamConflict() {
  const exam = $("#exam");
  if (!exam || exam.dataset.pendingEdit !== "true" || exam.dataset.conflictShown === "true") return;
  exam.dataset.conflictShown = "true";
  const lectureId = exam.dataset.lectureId;
  modal(`<h2>הסיכום הממוקד עודכן</h2><p>יש עריכה ידנית קודמת של הסיכום, ובמקביל נוצרה גרסה חדשה עם תיקוני LaTeX. מה תרצי להציג?</p><button type="button" onclick="this.closest('dialog').close(); resolveExamEdit('${lectureId}','adopt')">לאמץ את הגרסה החדשה</button><button type="button" class="secondary" onclick="this.closest('dialog').close(); resolveExamEdit('${lectureId}','keep')">להשאיר את העריכה הקודמת</button>`, async () => {});
}
function courseTab(id) { ["course-notebook","course-exam"].forEach(name => $(`#${name}`).hidden = name !== id); document.querySelectorAll(".tabs button").forEach(button => button.classList.toggle("active", (id === "course-notebook" && button.textContent.includes("מחברת קורס")) || (id === "course-exam" && button.textContent.includes("סיכום קורס")))); }
async function openLectureSlide(lectureId, slideNumber) { await openLecture(lectureId); showSlide(slideNumber); }
async function processCourse(id) { if (!confirm("ליצור מחברת קורס מכל ההרצאות? הרצאות שעדיין לא עובדו יעובדו כעת.")) return; showProcessing("יוצר מחברת קורס…"); try { await api(`/api/courses/${id}/process`,{method:"POST"}); await openCourse(id); } catch (error) { alert(error.message); } finally { hideProcessing(); } }
async function generateCourseExam(id) { if (!confirm("ליצור סיכום קורס ממוקד מכל ההרצאות? הרצאות שעדיין לא עובדו יטופלו כעת.")) return; showProcessing("יוצר סיכום קורס…"); try { await api(`/api/courses/${id}/exam-focus`,{method:"POST"}); await openCourse(id); } catch (error) { alert(error.message); } finally { hideProcessing(); } }
function uploadForm(id) { modal(`<h2>העלאת חומר מקור</h2><label>סוג<select name="kind"><option value="presentation">מצגת PDF</option><option value="transcript">תמלול</option><option value="recording">הקלטה / וידאו</option></select></label><input type="file" name="file" required><button>העלאה</button><button type="button" class="secondary" onclick="this.closest('dialog').close()">ביטול</button>`, async form => { const uploadData = new FormData(form); showProcessing("מעלה חומר ומעדכן את המחברת…", id); form.querySelectorAll("button,input,select").forEach(control => control.disabled = true); try { const result = await api(`/api/lectures/${id}/materials`,{method:"POST",body:uploadData}); form.closest("dialog").close(); if (result.auto_processing === "failed") alert(`הקובץ נשמר, אך העיבוד האוטומטי נכשל: ${result.auto_processing_error}`); await openLecture(id); } finally { hideProcessing(); } }); }
async function processLecture(id) { if (!confirm("להתחיל עיבוד?")) return; showProcessing("מעבד את ההרצאה ויוצר מחברת…", id); try { await api(`/api/lectures/${id}/process`,{method:"POST"}); await openLecture(id); } catch (error) { alert(error.message); } finally { hideProcessing(); } }
async function generateExamFocus(id, hasExam) { if (!document.getElementById("notebook")) { alert("יש לעבד את ההרצאה לפני יצירת הסיכום הממוקד למבחן."); document.getElementById("notebook-action")?.scrollIntoView({behavior:"smooth"}); return; } showProcessing("יוצר סיכום ממוקד למבחן…"); try { await api(`/api/lectures/${id}/exam-focus${hasExam ? "?regenerate=1" : ""}`,{method:"POST"}); await openLecture(id); } catch (error) { alert(error.message); } finally { hideProcessing(); } }
loadCourses();
