"""
bot.py
------
بوت تيليجرام تفاعلي لمراقبة شعب جامعة التكنولوجيا (JUST):

المحادثة:
  /start
    -> يعرض أزرار الفصل الدراسي
    -> يعرض أزرار الكلية
    -> يعرض أزرار القسم
    -> يبعت جدول الشعب مرقّم بالنص
    -> المستخدم يرد برقم السطر
    -> البوت يبلش يراقب هاي الشعبة بالخلفية (كل POLL_INTERVAL ثانية تقريبًا)
    -> لما تصير الحالة "مفتوحة" يبعث تنبيه فوري

  /stop  -> يوقف كل المراقبات الحالية لهاد المستخدم
  /mywatches -> يعرض شو عم يراقب حاليًا

التخزين: ملف watches.json بسيط (عشان لو انعمل ريستارت للسيرفس ما تضيع المراقبات).
"""

import asyncio
import json
import os
import random
import logging
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import scraper

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("just-bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
# إذا رح تشغله محليًا بدون webhook خليه فاضي، وإذا على Render حط رابط السيرفس + /webhook
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", "10000"))

POLL_INTERVAL_MIN = 20  # ثانية
POLL_INTERVAL_MAX = 35  # ثانية (تأخير عشوائي بينهم لتفادي الحظر)

WATCHES_FILE = Path("watches.json")
DEFAULTS_FILE = Path("last_filters.json")

# حالة مؤقتة بالذاكرة لكل مستخدم أثناء إعداد المراقبة (اختيار فصل/كلية/قسم/جدول)
user_sessions: dict[int, dict] = {}


def load_watches() -> dict:
    if WATCHES_FILE.exists():
        return json.loads(WATCHES_FILE.read_text(encoding="utf-8"))
    return {}


def save_watches(data: dict):
    WATCHES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_last_filters() -> dict:
    if DEFAULTS_FILE.exists():
        return json.loads(DEFAULTS_FILE.read_text(encoding="utf-8"))
    return {}


def save_last_filters(chat_id: int, semester: str, college: str, department: str):
    data = load_last_filters()
    data[str(chat_id)] = {
        "semester": semester,
        "college": college,
        "department": department,
    }
    DEFAULTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# /start -> اختيار الفصل الدراسي
# ---------------------------------------------------------------------------
async def _close_open_session(session: dict):
    """يسكر أي متصفح جلسة سابقة ما انسكر (مثلاً المستخدم عمل /start مرتين)."""
    if session.get("_playwright"):
        try:
            await scraper.close_session(session["_playwright"], session["_browser"])
        except Exception:
            pass
        session.pop("_playwright", None)
        session.pop("_browser", None)
        session.pop("_page", None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await _close_open_session(user_sessions.get(chat_id, {}))

    await update.message.reply_text("بفتح صفحة الجدول، لحظة...")
    p, browser, page = await scraper.open_session()
    semesters = await scraper.session_get_semesters(page)

    # منختار أول فصل دراسي بالقائمة تلقائيًا (غالبًا هو الفصل الحالي/الأقرب)
    # بدون ما نسأل المستخدم، ونروح على طول لاختيار الكلية.
    semester = semesters[0]

    user_sessions[chat_id] = {
        "_playwright": p,
        "_browser": browser,
        "_page": page,
        "semesters": semesters,
        "semester": semester,
    }

    await update.message.reply_text(f"الفصل: {semester}\nبجيب قائمة الكليات...")
    colleges = await scraper.session_get_colleges(page, semester)
    user_sessions[chat_id]["colleges"] = colleges
    buttons = [
        [InlineKeyboardButton(c, callback_data=f"col::{i}")]
        for i, c in enumerate(colleges)
    ]
    await update.message.reply_text(
        "اختار الكلية:", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def on_college_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    session = user_sessions.setdefault(chat_id, {})
    idx = int(query.data.split("::", 1)[1])
    college = session["colleges"][idx]
    session["college"] = college

    await query.edit_message_text(f"الكلية: {college}\nبجيب قائمة الأقسام...")
    departments = await scraper.session_get_departments(session["_page"], college)
    session["departments"] = departments
    buttons = [
        [InlineKeyboardButton(d, callback_data=f"dep::{i}")]
        for i, d in enumerate(departments)
    ]
    await context.bot.send_message(
        chat_id, "اختار القسم:", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def on_department_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    session = user_sessions.setdefault(chat_id, {})
    idx = int(query.data.split("::", 1)[1])
    department = session["departments"][idx]
    session["department"] = department

    await query.edit_message_text(f"القسم: {department}\nبجيب جدول الشعب...")

    rows = await scraper.session_get_table(session["_page"], department, status="الجميع")
    session["table"] = rows
    save_last_filters(chat_id, session["semester"], session["college"], department)

    # خلصنا من المتصفح التفاعلي - نسكره (المراقبة بعدين بتفتح متصفح لحالها كل فحص)
    await _close_open_session(session)

    if not rows:
        await context.bot.send_message(chat_id, "ما لقيت أي شعب لهاد القسم حاليًا.")
        return

    lines = []
    for i, row in enumerate(rows, start=1):
        lines.append(f"{i}. {' | '.join(row['raw'])}")

    text = "\n".join(lines)
    # تيليجرام بيحدد أقصى طول للرسالة ~4096 حرف، منقسمها لو طويلة
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > 3500:
            await context.bot.send_message(chat_id, chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        await context.bot.send_message(chat_id, chunk)

    await context.bot.send_message(
        chat_id, "ابعتلي رقم السطر أو رمز المادة يلي بدك أراقبه."
    )


# ---------------------------------------------------------------------------
# استقبال رقم السطر أو رمز المادة كنص عادي
# ---------------------------------------------------------------------------
async def on_row_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = user_sessions.setdefault(chat_id, {})

    if "table" not in session:
        # ما فيه جدول محمّل بهاي الجلسة - جرب نستخدم آخر فلاتر محفوظة
        # (فصل/كلية/قسم) بدل ما نطلب من المستخدم يعيد الاختيار من /start
        saved = load_last_filters().get(str(chat_id))
        if not saved:
            await update.message.reply_text(
                "أول مرة لازم تعمل /start وتختار الفصل/الكلية/القسم. "
                "بعدها بتقدر تبعت رمز المادة مباشرة بدون ما تعيد الاختيار."
            )
            return

        await update.message.reply_text("بجيب الجدول بنفس آخر فلاتر استخدمتها، لحظة...")
        rows = await scraper.get_course_table(
            saved["semester"], saved["college"], saved["department"], status="الجميع"
        )
        session["semester"] = saved["semester"]
        session["college"] = saved["college"]
        session["department"] = saved["department"]
        session["table"] = rows

    text = update.message.text.strip()
    rows = session["table"]
    idx = None

    # لو رقم وبمدى صفوف الجدول -> رقم سطر
    if text.isdigit() and 1 <= int(text) <= len(rows):
        idx = int(text) - 1
    else:
        # مش رقم سطر صالح -> جرب نطابقه كرمز مادة (أول عمود بالجدول)
        # ممكن يكون فيه أكتر من شعبة لنفس الرمز، فمنجمعهم كلهم
        matches = [
            i for i, r in enumerate(rows) if r["raw"] and r["raw"][0].strip() == text
        ]
        if len(matches) == 1:
            idx = matches[0]
        elif len(matches) > 1:
            lines = [f"{i + 1}. {' | '.join(rows[i]['raw'])}" for i in matches]
            await update.message.reply_text(
                "هاي المادة إلها أكتر من شعبة، ابعتلي رقم السطر بالظبط يلي بدك تراقبه:\n\n"
                + "\n".join(lines)
            )
            return

    if idx is None:
        await update.message.reply_text(
            f"ما لقيت رقم سطر (بين 1 و {len(rows)}) ولا رمز مادة يطابق '{text}'."
        )
        return

    row = rows[idx]
    watch_id = f"{chat_id}:{idx}:{random.randint(1000,9999)}"

    watches = load_watches()
    watches.setdefault(str(chat_id), []).append(
        {
            "watch_id": watch_id,
            "semester": session["semester"],
            "college": session["college"],
            "department": session["department"],
            "row_snapshot": row["raw"],
        }
    )
    save_watches(watches)

    await update.message.reply_text(
        "تمام ✅ بلشت أراقب هاي الشعبة:\n"
        + " | ".join(row["raw"])
        + "\n\nرح أبعتلك رسالة أول ما تفتح. لإيقاف كل المراقبات ابعت /stop"
    )

    context.job_queue.run_once(
        check_one_watch,
        when=random.randint(POLL_INTERVAL_MIN, POLL_INTERVAL_MAX),
        data={"chat_id": chat_id, "watch_id": watch_id},
        name=watch_id,
    )


# ---------------------------------------------------------------------------
# وظيفة الفحص الدوري (بتعيد جدولة نفسها لحالها كل مرة)
# ---------------------------------------------------------------------------
async def check_one_watch(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    watch_id = job_data["watch_id"]

    watches = load_watches()
    user_watches = watches.get(str(chat_id), [])
    watch = next((w for w in user_watches if w["watch_id"] == watch_id), None)
    if watch is None:
        return  # انوقفت المراقبة (مثلاً عن طريق /stop)

    rows = await scraper.get_course_table(
        watch["semester"], watch["college"], watch["department"], status="الجميع"
    )

    # منلاقي نفس الشعبة بمطابقة أغلب الأعمدة الثابتة (كود المادة + رقم الشعبة)
    # هون منفترض إنه أول عمودين (كود المادة، رقم الشعبة) ثابتين لا يتغيرو
    old = watch["row_snapshot"]
    match = None
    for r in rows:
        if r["raw"][:2] == old[:2]:
            match = r
            break

    if match is None:
        # ما لقيناها هالمرة (ممكن تغيّر بالجدول)، منعيد المحاولة بعدين
        pass
    else:
        status_text = " | ".join(match["raw"])
        was_open = "مفتوحة" in " ".join(old)
        is_open = "مفتوحة" in " ".join(match["raw"])
        if is_open and not was_open:
            await context.bot.send_message(
                chat_id, f"🔔 الشعبة فتحت الآن!\n{status_text}"
            )
            # منوقف المراقبة تلقائيًا بعد التنبيه (تقدر تشيل هاد الشرط لو بدك يستمر يراقب)
            watches[str(chat_id)] = [
                w for w in user_watches if w["watch_id"] != watch_id
            ]
            save_watches(watches)
            return
        else:
            watch["row_snapshot"] = match["raw"]
            save_watches(watches)

    # نجدول الفحص الجاي بتأخير عشوائي
    context.job_queue.run_once(
        check_one_watch,
        when=random.randint(POLL_INTERVAL_MIN, POLL_INTERVAL_MAX),
        data=job_data,
        name=watch_id,
    )


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await _close_open_session(user_sessions.get(chat_id, {}))
    watches = load_watches()
    if str(chat_id) in watches:
        del watches[str(chat_id)]
        save_watches(watches)
    for job in context.job_queue.jobs():
        if job.name and job.name.startswith(f"{chat_id}:"):
            job.schedule_removal()
    await update.message.reply_text("وقفت كل المراقبات. ابعت /start لو بدك تبلش من جديد.")


async def my_watches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    watches = load_watches().get(str(chat_id), [])
    if not watches:
        await update.message.reply_text("ما فيه أي شعبة عم تراقبها حاليًا.")
        return
    lines = [" | ".join(w["row_snapshot"]) for w in watches]
    await update.message.reply_text("\n\n".join(lines))


def start_healthcheck_server():
    """سيرفر HTTP بسيط بس عشان Render يشوف فيه بورت مفتوح. ما إله علاقة بالبوت نفسه."""
    port = int(os.environ.get("PORT", "10000"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):
            pass  # نتجاهل طباعة كل طلب عشان ما يزحم الـ logs

    server = HTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()


def ensure_browser_installed():
    """
    بدل ما نعتمد على تثبيت المتصفح وقت البناء (Build) وممكن يروح لمسار
    مختلف عن وقت التشغيل الفعلي، منثبته هون مباشرة أول ما يقلع البوت،
    بنفس البيئة تمامًا يلي رح يشتغل فيها. أبطأ شوي أول مرة بس أضمن.
    """
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    try:
        subprocess.run(
            ["playwright", "install", "chromium"],
            check=True,
            capture_output=True,
            text=True,
        )
        log.info("Playwright chromium ready.")
    except subprocess.CalledProcessError as e:
        log.error("فشل تثبيت المتصفح: %s", e.stderr)


def main():
    start_healthcheck_server()
    ensure_browser_installed()

    # حل بديل: بايثون 3.14 ألغى الإنشاء التلقائي للـ event loop، وهاد بيسبب
    # كراش داخل مكتبة python-telegram-bot. منعمله يدويًا هون قبل ما نبلش.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("mywatches", my_watches))
    app.add_handler(CallbackQueryHandler(on_college_chosen, pattern=r"^col::"))
    app.add_handler(CallbackQueryHandler(on_department_chosen, pattern=r"^dep::"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_row_number))

    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=f"{WEBHOOK_URL}/webhook",
        )
    else:
        app.run_polling()


if __name__ == "__main__":
    main()
