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
    يختار قيمة بقائمة select، وينتظر إعادة التحميل الكاملة للصفحة (ASP.NET
    postback) قبل ما يكمل. لو ما صارت أي navigation (نادرًا، لو كانت
    AJAX جزئية فعلاً)، منكتفي بـ networkidle.
    """
    select = page.locator("select").nth(index)
    try:
        async with page.expect_navigation(wait_until="networkidle", timeout=15000):
            await select.select_option(label=label)
    except Exception:
        # ما صارت navigation كاملة (مثلاً كان تحديث جزئي) - نكتفي بهاد
        await page.wait_for_load_state("networkidle")


async def get_semesters():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL, wait_until="networkidle")
        options = await _get_select_options(page, SELECT_SEMESTER)
        await browser.close()
        return options


async def get_colleges():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL, wait_until="networkidle")
        options = await _get_select_options(page, SELECT_COLLEGE)
        await browser.close()
        return options


async def get_departments(semester: str, college: str):
    """لازم نختار الفصل والكلية أول عشان قائمة الأقسام تترتب/تتحدث (postback)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL, wait_until="networkidle")

        await _select_and_wait(page, SELECT_SEMESTER, semester)
        await _select_and_wait(page, SELECT_COLLEGE, college)

        options = await _get_select_options(page, SELECT_DEPARTMENT)
        await browser.close()
        return options


async def get_course_table(semester: str, college: str, department: str, status: str = "الجميع"):
    """
    يرجع لستة من dict لكل صف بالجدول:
    {"course_code": ..., "course_name": ..., "section": ..., "status": ..., "raw": [كل الأعمدة]}
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL, wait_until="networkidle")

        await _select_and_wait(page, SELECT_SEMESTER, semester)
        await _select_and_wait(page, SELECT_COLLEGE, college)
        await _select_and_wait(page, SELECT_DEPARTMENT, department)
        await _select_and_wait(page, SELECT_STATUS, status)

        # نختار أكبر جدول بالصفحة (غالبًا هو جدول النتائج)
        tables = page.locator("table")
        count = await tables.count()
        best_rows = []
        for i in range(count):
            rows = tables.nth(i).locator("tr")
            rc = await rows.count()
            if rc > len(best_rows):
                best_table_index = i
                best_rows_count = rc

        rows = tables.nth(best_table_index).locator("tr")
        rc = await rows.count()

        results = []
        for i in range(1, rc):  # نتخطى صف العناوين
            cells = rows.nth(i).locator("td")
            cc = await cells.count()
            if cc == 0:
                continue
            texts = [(await cells.nth(j).inner_text()).strip() for j in range(cc)]
            results.append({"raw": texts})

        await browser.close()
        return results


async def debug_dump():
    """أداة مساعدة: تطبع كل الـ select وخياراتها الأولى، تستخدم لو تغيّر شكل الموقع."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL, wait_until="networkidle")
        selects = page.locator("select")
        count = await selects.count()
        print(f"عدد عناصر select: {count}")
        for i in range(count):
            opts = await selects.nth(i).locator("option").all_inner_texts()
            print(f"[{i}] أول 3 خيارات: {opts[:3]}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(debug_dump())
