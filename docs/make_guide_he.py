from bidi.algorithm import get_display

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

OUT = "/home/user/claude/docs/P2P_Intercom_Guide_HE.pdf"
FONTS = "/usr/share/fonts/truetype/dejavu"

pdfmetrics.registerFont(TTFont("HE", f"{FONTS}/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("HE-B", f"{FONTS}/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("MONO", f"{FONTS}/DejaVuSansMono.ttf"))

ACCENT = colors.HexColor("#ff7300")
INK = colors.HexColor("#111827")
GREY = colors.HexColor("#64748b")
RULE = colors.HexColor("#dfe3e8")
BOX = colors.HexColor("#fff7ed")
CODEBG = colors.HexColor("#f5f6f8")
HEAD = colors.HexColor("#eef1f5")

PW, PH = A4
M = 20 * mm
RIGHT = PW - M
LEFT = M
WIDTH = RIGHT - LEFT


def rtl(s):
    return get_display(s, base_dir="R")


def wrap(text, font, size, maxw):
    lines, cur = [], ""
    for w in text.split():
        trial = (cur + " " + w).strip()
        if not cur or pdfmetrics.stringWidth(trial, font, size) <= maxw:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


class Guide:
    def __init__(self, path):
        self.c = canvas.Canvas(path, pagesize=A4)
        self.c.setTitle("P2P Intercom - Operating Guide")
        self.c.setAuthor("oT")
        self.page = 0
        self.new_page()

    def new_page(self):
        if self.page:
            self.c.showPage()
        self.page += 1
        self.y = PH - M
        c = self.c
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        c.line(LEFT, M - 5 * mm, RIGHT, M - 5 * mm)
        c.setFont("HE", 7.5)
        c.setFillColor(GREY)
        c.drawRightString(RIGHT, M - 9 * mm, rtl("אינטרקום P2P — מדריך הפעלה"))
        c.drawString(LEFT, M - 9 * mm, f"{self.page}")
        c.drawCentredString(PW / 2, M - 9 * mm, "(c) oT")

    def room(self, h):
        if self.y - h < M + 4 * mm:
            self.new_page()

    def gap(self, h=4):
        self.y -= h

    def title(self, text, sub=""):
        self.room(30 * mm)
        self.c.setFont("HE-B", 24)
        self.c.setFillColor(INK)
        self.y -= 24
        self.c.drawRightString(RIGHT, self.y, rtl(text))
        if sub:
            self.c.setFont("HE", 12)
            self.c.setFillColor(GREY)
            self.y -= 19
            self.c.drawRightString(RIGHT, self.y, rtl(sub))
        self.y -= 12

    def h1(self, text):
        self.room(45 * mm)
        self.y -= 20
        self.c.setFont("HE-B", 14)
        self.c.setFillColor(INK)
        self.c.drawRightString(RIGHT, self.y, rtl(text))
        self.y -= 5
        self.c.setStrokeColor(ACCENT)
        self.c.setLineWidth(1.6)
        self.c.line(RIGHT - 26 * mm, self.y, RIGHT, self.y)
        self.y -= 9

    def h2(self, text):
        self.room(14 * mm)
        self.y -= 15
        self.c.setFont("HE-B", 10.5)
        self.c.setFillColor(ACCENT)
        self.c.drawRightString(RIGHT, self.y, rtl(text))
        self.y -= 5

    def body(self, text, indent=0, color=INK, size=10, font="HE"):
        maxw = WIDTH - indent
        for line in wrap(text, font, size, maxw):
            self.room(8 * mm)
            self.y -= size * 1.45
            self.c.setFont(font, size)
            self.c.setFillColor(color)
            self.c.drawRightString(RIGHT - indent, self.y, rtl(line))
        self.y -= 3

    def bullet(self, text, marker="•"):
        maxw = WIDTH - 8 * mm
        lines = wrap(text, "HE", 10, maxw)
        for i, line in enumerate(lines):
            self.room(8 * mm)
            self.y -= 14.5
            self.c.setFillColor(INK)
            if i == 0:
                self.c.setFont("HE-B" if marker != "•" else "HE", 10)
                self.c.setFillColor(ACCENT if marker != "•" else INK)
                self.c.drawRightString(RIGHT, self.y, marker)
                self.c.setFillColor(INK)
            self.c.setFont("HE", 10)
            self.c.drawRightString(RIGHT - 8 * mm, self.y, rtl(line))
        self.y -= 2

    def step(self, n, text):
        self.bullet(text, marker=f"{n}.")

    def code(self, text):
        lines = text.split("\n")
        h = len(lines) * 12 + 12
        self.room(h + 6 * mm)
        self.y -= 6
        self.c.setFillColor(CODEBG)
        self.c.setStrokeColor(RULE)
        self.c.rect(LEFT, self.y - h, WIDTH, h, fill=1, stroke=1)
        self.c.setFont("MONO", 8.5)
        self.c.setFillColor(INK)
        ty = self.y - 16
        for line in lines:
            self.c.drawString(LEFT + 8, ty, line)
            ty -= 12
        self.y -= h + 8

    def note(self, title, text):
        maxw = WIDTH - 14 * mm
        lines = wrap(text, "HE", 9.6, maxw)
        h = 15 + len(lines) * 14 + 8
        self.room(h + 6 * mm)
        self.y -= 6
        self.c.setFillColor(BOX)
        self.c.setStrokeColor(colors.HexColor("#fed7aa"))
        self.c.rect(LEFT, self.y - h, WIDTH, h, fill=1, stroke=1)
        ty = self.y - 17
        self.c.setFont("HE-B", 9.8)
        self.c.setFillColor(colors.HexColor("#9a3412"))
        self.c.drawRightString(RIGHT - 7 * mm, ty, rtl(title))
        ty -= 15
        self.c.setFont("HE", 9.6)
        self.c.setFillColor(INK)
        for line in lines:
            self.c.drawRightString(RIGHT - 7 * mm, ty, rtl(line))
            ty -= 14
        self.y -= h + 8

    def table(self, header, rows, widths):
        pad = 5
        size = 9.2

        def row_h(cells):
            n = 1
            for i, cell in enumerate(cells):
                n = max(n, len(wrap(cell, "HE", size, widths[i] - 2 * pad)))
            return n * 13 + 8

        def draw_row(cells, h, bold=False, bg=None):
            x = RIGHT
            if bg:
                self.c.setFillColor(bg)
                self.c.setStrokeColor(RULE)
                self.c.rect(LEFT, self.y - h, WIDTH, h, fill=1, stroke=0)
            for i, cell in enumerate(cells):
                w = widths[i]
                self.c.setStrokeColor(RULE)
                self.c.setLineWidth(0.35)
                self.c.rect(x - w, self.y - h, w, h, fill=0, stroke=1)
                self.c.setFont("HE-B" if bold else "HE", size)
                self.c.setFillColor(INK)
                ty = self.y - 14
                for line in wrap(cell, "HE", size, w - 2 * pad):
                    self.c.drawRightString(x - pad, ty, rtl(line))
                    ty -= 13
                x -= w
            self.y -= h

        self.y -= 6
        h = row_h(header)
        self.room(h + 20 * mm)
        draw_row(header, h, bold=True, bg=HEAD)
        for r in rows:
            h = row_h(r)
            if self.y - h < M + 4 * mm:
                self.new_page()
                draw_row(header, row_h(header), bold=True, bg=HEAD)
            draw_row(r, h)
        self.y -= 8

    def save(self):
        self.c.showPage()
        self.c.save()


g = Guide(OUT)

# ----------------------------------------------------------- page 1
g.title("אינטרקום P2P", "מדריך הפעלה למשתמש")

g.body("המערכת מאפשרת שיחת קול בין שני מחשבים. הקול עובר ישירות בין שני המחשבים, "
       "בלי לעבור דרך שרת חיצוני.")

g.h2("שלושת הקבצים")
g.bullet("server.exe — התוכנה עם החלון. מופעלת במחשב הראשי, ומשם שולטים על הכל.")
g.bullet("client.exe — הקליינט. נפתח חלון שחור עם שורות טקסט בלבד. מופעל במחשב השני.")
g.bullet("setup.exe — אשף הגדרה. מריצים אותו פעם אחת במחשב של הקליינט כדי לזהות "
         "את האוזניות ולהגדיר את איכות הקול.")

g.h2("מה צריך לפני שמתחילים")
g.bullet("אוזניות עם מיקרופון מחוברות לשני המחשבים.")
g.bullet("שני המחשבים מחוברים לרשת (LAN מקומי או אינטרנט).")
g.bullet("כתובת ה-IP של המחשב הראשי.")

g.h1("שלב 1 — הגדרה במחשב הקליינט")
g.body("במחשב שבו ירוץ client.exe, מריצים קודם את setup.exe. זהו אשף עם חלון "
       "שמזהה את האוזניות ויוצר את קובץ ההגדרות אוטומטית.")

g.step(1, "ודא שהאוזניות מחוברות, והפעל את setup.exe.")
g.step(2, "לחץ Start. האשף יבקש לנתק את האוזניות.")
g.step(3, "נתק את האוזניות, המתן שתי שניות, ולחץ Detect.")
g.step(4, "האשף מזהה את ההתקן שנעלם ומציג את שמו. חבר בחזרה ולחץ Save.")

g.note("אם ההתקן לא נעלם",
       "אוזניות שמחוברות לשקע של כרטיס הקול המובנה (Realtek) לא תמיד נעלמות "
       "מהרשימה בזמן ניתוק - זו מגבלה של Windows. במקרה כזה האשף יעבור אוטומטית "
       "לזיהוי דרך Windows, ואם גם זה לא עוזר - יציג רשימת התקנים לבחירה ידנית.")

g.h1("שלב 2 — הפעלה")

g.h2("במחשב הראשי")
g.step(1, "הפעל את server.exe. נפתח חלון בשם Intercom Server.")
g.step(2, "ודא שהריבוע Use Local Mode מסומן (לרשת מקומית).")
g.step(3, "לחץ START INTERNAL SERVER. השורה SERVER CONNECTION תידלק בירוק.")
g.step(4, "רשום לעצמך את ה-Port (ברירת מחדל 9999) ואת ה-IP של המחשב.")

g.h2("במחשב הקליינט")
g.step(5, "פתח את קובץ settings.txt (נוצר על ידי setup.exe) עם Notepad, וודא "
          "שכתובת ה-ip היא של המחשב הראשי, וש-my_id שונה מזה של השרת.")
g.step(6, "הפעל את client.exe. ייפתח חלון שחור שמתחיל לרוץ.")

# ----------------------------------------------------------- audio quality
g.h1("איכות קול וכוונון")
g.body("את כל הגדרות הקול קובעים במחשב הקליינט דרך setup.exe. הן נשמרות אוטומטית, "
       "ואפשר לשנות אותן גם תוך כדי שיחה.")

g.h2("קצב דגימה (Audio quality)")
g.body("קובע כמה הקול פתוח לעומת כמה רוחב פס הוא צורך. שני הצדדים חייבים להיות "
       "מוגדרים לאותו קצב.")
g.table(["האפשרות", "מתי לבחור"], [
    ["16k  Narrow", "אינטרנט איטי. הקול צר יותר, אבל חוסך רוחב פס."],
    ["24k  Balanced", "ברירת המחדל המומלצת. קול פתוח על אינטרנט ביתי רגיל."],
    ["48k  Wide", "רשת מהירה בלבד. האיכות הגבוהה ביותר, צורך פי שלושה."],
], [34 * mm, WIDTH - 34 * mm])

g.note("שני הצדדים חייבים אותו קצב",
       "בשרת בוחרים את הקצב מהשורה Audio: בחלון. אם הקליינט מוגדר 24k, בחר 24k גם "
       "בשרת. אם הקצב שונה בין הצדדים - הקול יישמע מעוות, והקליינט יציג אזהרה "
       "בחלון השחור.")

g.h2("עוצמת מיקרופון ורמקול")
g.body("שני מחוונים בתחתית setup.exe, בטווח 0 עד 200 אחוז (100% = ללא שינוי). "
       "אפשר לגרור אותם גם באמצע שיחה, והשינוי נתפס תוך כמה שניות.")
g.table(["המחוון", "מה הוא עושה"], [
    ["Microphone gain", "כמה חזק הצד השני שומע אותך. העלה אם אתה חלוש אצלו."],
    ["Speaker level", "כמה חזק אתה שומע את הצד השני. העלה אם הוא חלוש אצלך."],
], [42 * mm, WIDTH - 42 * mm])

g.note("אל תעלה gain יותר מדי",
       "מעל בערך 160% הקול מתחיל להתעוות ולהישמע מתכתי - זו תופעה של רוויה, לא "
       "תקלה. אם צריך יותר עוצמה, עדיף להגביר את המיקרופון בהגדרות Windows.")

# ----------------------------------------------------------- start a call
g.h1("להתחיל שיחה")
g.step(1, "כשהאוזניות במחשב הקליינט מזוהות, במחשב הראשי יישמע ביפ חוזר "
          "והשורה CLIENT HEADPHONES תידלק בירוק.")
g.step(2, "לחץ בכל מקום על הכרטיס CLIENT HEADPHONES כדי להפסיק את הביפ.")
g.step(3, "לחץ START INTERCOM. תוך כמה שניות השיחה מתחילה "
          "והשורה CLIENT CONNECTION תידלק בירוק.")
g.step(4, "לסיום השיחה לחץ על אותו כפתור, שהפך ל-DISCONNECT.")

g.h1("שלוש הנוריות שבמסך")
g.table(["השורה", "הצבע", "המשמעות"], [
    ["SERVER CONNECTION", "אפור", "השרת כבוי."],
    ["", "ירוק", "השרת פועל ומוכן."],
    ["", "כתום", "מנסה להתחבר."],
    ["", "אדום", "השרת לא הצליח לעלות. בדרך כלל הפורט תפוס."],
    ["CLIENT HEADPHONES", "אפור", "המחשב השני עדיין לא דיווח."],
    ["", "ירוק", "האוזניות במחשב השני מחוברות."],
    ["", "אדום", "האוזניות במחשב השני נותקו."],
    ["CLIENT CONNECTION", "אפור", "אין שיחה."],
    ["", "כתום", "מחפש את הצד השני או מתחבר."],
    ["", "ירוק", "השיחה פעילה. מוצג גם משך השיחה."],
], [40 * mm, 18 * mm, WIDTH - 58 * mm])

g.h1("הכפתורים בשרת")
g.table(["הכפתור", "מה הוא עושה"], [
    ["START INTERNAL SERVER", "מדליק את השרת. חייב להיות דלוק כדי שהמערכת תעבוד."],
    ["START INTERCOM", "מתחיל שיחה. בזמן שיחה הכפתור הופך ל-DISCONNECT."],
    ["SHUTDOWN CLIENT", "סוגר את client.exe במחשב השני מרחוק. תופיע בקשת אישור."],
    ["Audio (16k/24k/48k)", "קצב הדגימה של השרת. חייב להתאים לקליינט."],
    ["Help", "הסבר קצר בתוך התוכנה."],
    ["Quit", "סוגר את התוכנה."],
], [46 * mm, WIDTH - 46 * mm])

g.note("לפני שאתה לוחץ SHUTDOWN CLIENT",
       "הכפתור סוגר לגמרי את client.exe במחשב השני. כדי להפעיל אותו שוב צריך גישה "
       "פיזית לאותו מחשב - אין דרך להדליק אותו בחזרה מרחוק.")

g.h1("תקלות נפוצות")
g.table(["התופעה", "מה לעשות"], [
    ["הקול נשמע צר או רובוטי",
     "העלה את קצב הדגימה ל-24k או 48k בשני הצדדים דרך setup.exe והשרת."],
    ["הקול נשמע מתכתי או מעוות",
     "ה-gain כנראה גבוה מדי. הורד את Microphone gain מתחת ל-150%."],
    ["הקול מקרקש או נקטע",
     "רוחב הפס לא מספיק. הורד את קצב הדגימה ל-16k בשני הצדדים."],
    ["הנורית של האוזניות לא משתנה כשמנתקים אותן",
     "הרץ שוב את setup.exe. אם ההתקן לא נעלם, בחר אותו מהרשימה הידנית."],
    ["בחלון השחור כתוב SERVER: DISCONNECTED",
     "השרת כבוי, או שכתובת ה-IP או הפורט לא נכונים. בדוק גם את חומת האש."],
    ["השיחה לא מתחילה, נשאר כתום",
     "שני הצדדים חייבים my_id שונה. אם בשרת node_A, אז בקליינט חייב להיות אחר."],
    ["הקליינט מזהיר על sample_rate שונה",
     "קצב הדגימה שונה בין הצדדים. כוון את שניהם לאותו ערך."],
], [54 * mm, WIDTH - 54 * mm])

g.gap(6)
g.body("כל הזכויות שמורות ל-oT.", color=GREY, size=9)

g.save()
print("written:", OUT)
