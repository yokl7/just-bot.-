"""
scraper.py
----------
هاد الملف مسؤول عن التحدث مع موقع جامعة التكنولوجيا (JUST) لجلب:
- قائمة الفصول الدراسية
- قائمة الكليات
- قائمة الأقسام (بتتغير حسب الكلية المختارة)
- جدول الشعب (بعد اختيار الفصل/الكلية/القسم/الحالة)

يستخدم Playwright (متصفح Chromium بدون واجهة) لأن الموقع ASP.NET
ومبني على postback/viewstate، ومتصفح آلي حقيقي أسهل وأضمن من
محاكاة الطلبات يدويًا.

ملاحظة مهمة:
الترتيب المفترض لعناصر <select> بالصفحة هو:
  0 -> الفصل الدراسي
  1 -> الكلية
  2 -> القسم (تتحدث خياراته بعد اختيار الكلية)
  3 -> حالة الشعبة (الكل / مفتوحة / مغلقة)
إذا تغيّر شكل الموقع مستقبلاً، شغّل debug_dump() تحت للتأكد
من ترتيب العناصر الصحيح وعدّل الأرقام SELECT_* تبعت تحت.
"""

import asyncio
from playwright.async_api import async_playwright

URL = "https://services.just.edu.jo/courseschedule/"

SELECT_SEMESTER = 0
SELECT_COLLEGE = 1
SELECT_DEPARTMENT = 2
SELECT_STATUS = 3


async def _get_select_options(page, index):
    """يرجع قائمة (نص الخيار) لكل عنصر select بالترتيب المعطى، بعيدًا عن أول خيار (- اختر -)."""
    select = page.locator("select").nth(index)
    options = await select.locator("option").all_inner_texts()
    # نتجاهل أول خيار لأنه عادة نص placeholder متل "- اختر -"
    return [o.strip() for o in options if o.strip() and "اختر" not in o]


async def _select_and_wait(page, index, label):
    """
    يختار قيمة بقائمة select. الموقع أحيانًا بيعمل navigation كاملة
    وأحيانًا تحديث جزئي (AJAX) بس فيه نشاط شبكة خلفي مستمر بيمنع
    networkidle من الوصول أبدًا، فبدل ما نستنى "استقرار الشبكة" (غير
    موثوق هون)، منستنى فترة ثابتة بسيطة تكفي للتحديث.
    """
    select = page.locator("select").nth(index)
    try:
        async with page.expect_navigation(wait_until="load", timeout=5000):
            await select.select_option(label=label)
        await page.wait_for_timeout(1500)
    except Exception:
        # ما صارت navigation كاملة خلال 5 ثواني - غالبًا تحديث AJAX جزئي
        await page.wait_for_timeout(3000)


async def get_semesters():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL, wait_until="load")
        options = await _get_select_options(page, SELECT_SEMESTER)
        await browser.close()
        return options


async def get_colleges():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL, wait_until="load")
        options = await _get_select_options(page, SELECT_COLLEGE)
        await browser.close()
        return options


async def get_departments(semester: str, college: str):
    """لازم نختار الفصل والكلية أول عشان قائمة الأقسام تترتب/تتحدث (postback)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL, wait_until="load")

        await _select_and_wait(page, SELECT_SEMESTER, semester)
        await _select_and_wait(page, SELECT_COLLEGE, college)

        options = await _get_select_options(page, SELECT_DEPARTMENT)
        await browser.close()
        return options


async def _extract_table(page):
    all_tables = await page.evaluate(
        """
        () => Array.from(document.querySelectorAll('table')).map(table =>
            Array.from(table.querySelectorAll('tr')).map(tr =>
                Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim())
            )
        )
        """
    )
    best_table = max(all_tables, key=len, default=[])
    results = []
    for row in best_table[1:]:  # نتخطى صف العناوين
        if not row or all(cell == "" for cell in row):
            continue
        results.append({"raw": row})
    return results


async def get_course_table(semester: str, college: str, department: str, status: str = "الجميع"):
    """
    نسخة "دفعة وحدة" (تفتح متصفح، تختار كل شي، تجيب الجدول، تسكر) -
    مستخدمة بالمراقبة الدورية بالخلفية يلي بتصير مرة كل فترة لحالها.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL, wait_until="load")

        await _select_and_wait(page, SELECT_SEMESTER, semester)
        await _select_and_wait(page, SELECT_COLLEGE, college)
        await _select_and_wait(page, SELECT_DEPARTMENT, department)
        await _select_and_wait(page, SELECT_STATUS, status)

        results = await _extract_table(page)
        await browser.close()
        return results


# ---------------------------------------------------------------------------
# دوال "جلسة": بيتفتح متصفح واحد بس ويضل مفتوح طول رحلة الاختيار التفاعلية
# (فصل -> كلية -> قسم -> جدول)، بدل ما نفتح متصفح جديد بكل خطوة. أسرع بكتير.
# ---------------------------------------------------------------------------
async def open_session():
    p = await async_playwright().start()
    browser = await p.chromium.launch()
    page = await browser.new_page()
    await page.goto(URL, wait_until="load")
    return p, browser, page


async def close_session(p, browser):
    try:
        await browser.close()
    finally:
        await p.stop()


async def session_get_semesters(page):
    return await _get_select_options(page, SELECT_SEMESTER)


async def session_get_colleges(page, semester: str):
    await _select_and_wait(page, SELECT_SEMESTER, semester)
    return await _get_select_options(page, SELECT_COLLEGE)


async def session_get_departments(page, college: str):
    await _select_and_wait(page, SELECT_COLLEGE, college)
    return await _get_select_options(page, SELECT_DEPARTMENT)


async def session_get_table(page, department: str, status: str = "الجميع"):
    await _select_and_wait(page, SELECT_DEPARTMENT, department)
    await _select_and_wait(page, SELECT_STATUS, status)
    return await _extract_table(page)


async def debug_dump():
    """أداة مساعدة: تطبع كل الـ select وخياراتها الأولى، تستخدم لو تغيّر شكل الموقع."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL, wait_until="load")
        selects = page.locator("select")
        count = await selects.count()
        print(f"عدد عناصر select: {count}")
        for i in range(count):
            opts = await selects.nth(i).locator("option").all_inner_texts()
            print(f"[{i}] أول 3 خيارات: {opts[:3]}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(debug_dump())
