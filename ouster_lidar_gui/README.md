# Ouster Digital Lidar — GUI Control & Visualization (Python / Ubuntu 24.04)

תוכנית פייתון עם ממשק גרפי (GUI) לשליטה והצגת נתונים של חיישן **Ouster Digital Lidar**,
בהתבסס על ה-SDK הרשמי של Ouster — כפי שמודגם בסרטון
[Ouster Digital Lidar SDK: Setup and Visualization](https://www.youtube.com/watch?v=m0ANVFunObU).

![GUI](https://img.shields.io/badge/GUI-Tkinter-blue) ![OS](https://img.shields.io/badge/OS-Ubuntu%2024.04-orange) ![SDK](https://img.shields.io/badge/SDK-ouster--sdk-green)

## יכולות

- **חיבור לחיישן** לפי שם מארח או כתובת IP (למשל `os-122xxxxxxxxxx.local`)
- **קריאת מטא-דאטה** — דגם, מספר סידורי, גרסת קושחה, רזולוציה ומצב עבודה
- **קונפיגורציה של החיישן** — Lidar Mode‏ (512x10 עד 2048x10), Timestamp Mode, פורטים של UDP
- **סטרימינג חי** — תצוגת תמונות דו-ממדיות (destaggered) של השדות:
  RANGE‏ (טווח), SIGNAL‏ (עוצמת אות), REFLECTIVITY‏ (החזריות), NEAR_IR‏ (אור סביבתי)
- **מציג תלת-ממד** — פתיחת ה-Viewer הרשמי של Ouster‏ (SimpleViz) בלחיצת כפתור
- **הקלטה וניגון חוזר** — שמירת הזרם לקובץ PCAP וניגון קבצי PCAP / OSF ללא חיישן פיזי

## התקנה (Ubuntu 24.04)

הדרך המהירה — סקריפט ההתקנה:

```bash
cd ouster_lidar_gui
chmod +x install.sh
./install.sh
```

או ידנית:

```bash
# חבילות מערכת (אובונטו 24.04 מחייבת סביבה וירטואלית — PEP 668)
sudo apt-get update
sudo apt-get install -y python3-venv python3-tk

# סביבה וירטואלית + תלויות
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## חיבור החיישן לרשת

1. חברו את החיישן ל-Interface Box ואת כבל הרשת למחשב, וחברו מתח.
2. החיישן מקבל כתובת אוטומטית (DHCP או link-local). ניתן לגשת אליו לפי שם:
   `os-<serial-number>.local` (למשל `os-122204001234.local`).
3. בדיקה מהירה שהחיישן נגיש:

```bash
ping os-122xxxxxxxxxx.local
# או פתחו בדפדפן את דף הבית של החיישן:
# http://os-122xxxxxxxxxx.local
```

> טיפ: אם השם לא נפתר, ודאו ש-avahi מותקן (`sudo apt-get install avahi-daemon`)
> או השתמשו ישירות בכתובת ה-IP של החיישן.

## הרצה

```bash
source venv/bin/activate
python ouster_gui.py
```

### שימוש בסיסי

1. הזינו את שם המארח / כתובת ה-IP של החיישן ולחצו **Get Sensor Info**.
2. בחרו מצב עבודה (למשל `1024x10`) ולחצו **Apply Configuration** — החיישן יאתחל את עצמו (כ-10 שניות).
3. לחצו **▶ Start Stream** לתצוגת ה-2D החיה בתוך החלון.
4. לחצו **Open 3D Viewer** לענן נקודות תלת-ממדי (SimpleViz של Ouster).
5. **● Start Recording** מקליט לקובץ PCAP; **Open PCAP / OSF File** מנגן הקלטה — גם בלי חיישן מחובר.

### אין לכם חיישן? נסו הקלטת דוגמה

Ouster מפרסמת הקלטות לדוגמה באתר שלה
([Sample Data](https://ouster.com/resources/lidar-sample-data)).
הורידו קובץ PCAP + JSON, ופתחו את ה-PCAP דרך **Open PCAP File** בתוכנה
(קובץ ה-JSON של המטא-דאטה צריך להיות באותה תיקייה ועם אותו שם בסיס).

## מבנה הפרויקט

```
ouster_lidar_gui/
├── ouster_gui.py      # האפליקציה הראשית (Tkinter + ouster-sdk + matplotlib)
├── requirements.txt   # תלויות פייתון
├── install.sh         # התקנה אוטומטית לאובונטו 24.04
└── README.md
```

## פתרון תקלות

| בעיה | פתרון |
|------|--------|
| `ouster-sdk missing` | הפעילו את הסביבה הווירטואלית: `source venv/bin/activate` |
| אין נתונים בסטרימינג | ודאו שהקונפיגורציה הוחלה עם `udp_dest_auto` (קורה אוטומטית ב-Apply) ושחומת האש לא חוסמת את פורטים 7502/7503 (`sudo ufw allow 7502/udp && sudo ufw allow 7503/udp`) |
| `no module named tkinter` | `sudo apt-get install python3-tk` |
| השם `.local` לא נפתר | `sudo apt-get install avahi-daemon` או השתמשו ב-IP ישירות |
