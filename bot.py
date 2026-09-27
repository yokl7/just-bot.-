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

import json
import os
import random
import logging
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

# حالة مؤقتة بالذاكرة لكل مستخدم أثناء إعداد المراقبة (اختيار فصل/كلية/قسم/جدول)
user_sessions: dict[int, dict] = {}


def load_watches() -> dict:
    if WATCHES_FILE.exists():
        return json.loads(WATCHES_FILE.read_text(encoding="utf-8"))
    return {}


def save_watches(data: dict):
    WATCHES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# /start -> اختيار الفصل الدراسي
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("بجيب قائمة الفصول الدراسية، لحظة...")
    semesters = await scraper.get_semesters()
    user_sessions[chat_id] = {}
    buttons = [[InlineKeyboardButton(s, callback_data=f"sem::{s}")] for s in semesters]
    await update.message.reply_text(
        "اختار الفصل الدراسي:", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def on_semester_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    semester = query.data.split("::", 1)[1]
    user_sessions.setdefault(chat_id, {})["semester"] = semester

    await query.edit_message_text(f"الفصل: {semester}\nبجيب قائمة الكليات...")
    colleges = await scraper.get_colleges()
    buttons = [[InlineKeyboardButton(c, callback_data=f"col::{c}")] for c in colleges]
    await context.bot.send_message(
        chat_id, "اختار الكلية:", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def on_college_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    college = query.data.split("::", 1)[1]
    session = user_sessions.setdefault(chat_id, {})
    session["college"] = college

    await query.edit_message_text(f"الكلية: {college}\nبجيب قائمة الأقسام...")
    departments = await scraper.get_departments(session["semester"], college)
    buttons = [[InlineKeyboardButton(d, callback_data=f"dep::{d}")] for d in departments]
    await context.bot.send_message(
        chat_id, "اختار القسم:", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def on_department_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    department = query.data.split("::", 1)[1]
    session = user_sessions.setdefault(chat_id, {})
    session["department"] = department

    await query.edit_message_text(f"القسم: {department}\nبجيب جدول الشعب...")

    rows = await scraper.get_course_table(
        session["semester"], session["college"], department, status="الجميع"
    )
    session["table"] = rows

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

    await context.bot.send_message(chat_id, "ابعتلي رقم السطر يلي بدك أراقبه.")


# ---------------------------------------------------------------------------
# استقبال رقم السطر كنص عادي
# ---------------------------------------------------------------------------
async def on_row_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = user_sessions.get(chat_id)

    if not session or "table" not in session:
        await update.message.reply_text("ابدأ أول بـ /start.")
        return

    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("ابعت رقم السطر بس (مثال: 3).")
        return

    idx = int(text) - 1
    rows = session["table"]
    if idx < 0 or idx >= len(rows):
        await update.message.reply_text(f"الرقم لازم يكون بين 1 و {len(rows)}.")
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


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("mywatches", my_watches))
    app.add_handler(CallbackQueryHandler(on_semester_chosen, pattern=r"^sem::"))
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
