# The Smart Course Book

Local-first Hebrew/RTL study notebook application. It manages many courses and lectures, stores original source files locally, and builds traceable notebook and Exam Focus outputs. A conservative offline pipeline works without a key; Gemini analysis is isolated in `app/services.py` and is enabled by `GEMINI_API_KEY`.

## Start

Install Python 3.11+ and dependencies: `python -m venv .venv` then `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`. Copy `.env.example` to `.env`, set `GEMINI_API_KEY`, then run `.\.venv\Scripts\python.exe run.py`. Open `http://127.0.0.1:8000`.

Create a course, add a lecture or exercise, upload a PDF, transcript, and/or recording, then press **עיבוד ויצירת מחברת**. Use the notebook slide link and slide tab to navigate both ways. Export with **ייצוא PDF**.

## Notes

The configured default is `gemini-3.6-flash`, used through Gemini's current Interactions API for multimodal, structured JSON analysis. Files are uploaded temporarily for an explicit processing request and deleted from Gemini after analysis. No AI request occurs without a key. Large or unsupported source files receive a clear validation error. Original materials are never deleted by app actions.

---

# ספר הקורס החכם

יישום מקומי־תחילה ליצירת מחברות לימוד בעברית ובממשק RTL. המערכת מנהלת קורסים והרצאות, שומרת את חומרי המקור במחשב המקומי, ובונה מחברת לימוד וסיכום ממוקד למבחן עם הפניות שקופות למקור. אפשר לעבוד גם ללא מפתח API באמצעות תהליך מקומי שמרני; ניתוח Gemini מבודד בקובץ `app/services.py` ומופעל באמצעות `GEMINI_API_KEY`.

## הפעלה

התקיני Python 3.11 ומעלה ואת התלויות: `python -m venv .venv` ולאחר מכן `\.venv\Scripts\python.exe -m pip install -r requirements.txt`. העתיקי את `.env.example` אל `.env`, הגדירי `GEMINI_API_KEY`, ואז הריצי `\.venv\Scripts\python.exe run.py`. פתחי בדפדפן את `http://127.0.0.1:8000`.

צרי קורס, הוסיפי הרצאה או תרגול, העלי PDF, תמלול ו/או הקלטה, ולחצי על **עיבוד ויצירת מחברת**. אפשר להשתמש בקישורים לעמודי המצגת ובטאב השקופיות כדי לעבור בין המחברת למצגת בשני הכיוונים. ניתן גם לייצא את המחברת באמצעות **ייצוא PDF**.

## הערות

מודל ברירת המחדל המוגדר הוא `gemini-3.6-flash`, דרך ממשק ה־Interactions העדכני של Gemini לניתוח רב־אופני ומובנה בפורמט JSON. קבצים מועלים זמנית רק לאחר בקשת עיבוד מפורשת ונמחקים מ־Gemini בסיום הניתוח. לא מתבצעת בקשת AI ללא מפתח. קבצים גדולים או לא נתמכים מקבלים הודעת שגיאה ברורה. פעולות במערכת אינן מוחקות את חומרי המקור המקוריים.
