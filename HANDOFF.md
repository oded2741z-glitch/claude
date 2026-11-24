# המשך פרויקט: קיוסק Ubuntu 22.04 על טאבלט Panasonic CF-33

## רקע
- מאגר: `oded2741z-glitch/claude`, ענף `claude/dazzling-hypatia-kbyrxu`.
- טאבלט Panasonic CF-33 (Intel, 64 ביט, מסך מגע, בלי מקלדת), Ubuntu Desktop 22.04, **בלי אינטרנט**.
- מחשב נפרד עם Ubuntu 22.04 ואינטרנט (לא יציב) משמש להורדות. ההעברה בדיסק-און-קי.
- משתמשים: `user` (מנהל, עם סיסמה) ו-`kiosk` (בלי סיסמה, נוצר ע"י `setup.sh`).
- הקיוסק מציג אתר משרת ברשת המקומית, למשל `http://192.168.1.10`.

## קבצים במאגר
| קובץ | תפקיד |
|---|---|
| `download.sh` | במחשב עם אינטרנט: מוריד לקיוסק (Chromium snap, unclutter, evtest, ffmpeg, x11vnc, vlc, onboard, gir1.2-atspi-2.0) ל-`kiosk-offline/`. ממשיך מאיפה שנעצר, ניסיונות חוזרים, רק amd64 |
| `setup.sh` | בטאבלט: מתקין את הקיוסק. `sudo bash setup.sh <URL>` |
| `setup_explained.sh` | אותו קוד כמו `setup.sh` עם הסברים בעברית. חייב להישאר זהה בקוד |
| `download_update.sh` | במחשב עם אינטרנט: מוריד עדכון של Ubuntu + `ubuntu-desktop` + `gdm3` ל-`ubuntu-update/` |
| `update_offline.sh` | בטאבלט: מעדכן מ-`ubuntu-update/` בלי להסיר חבילות (`--no-remove`), ומגדיר `graphical.target` |
| `test_server.py` | שרת בדיקה על port 8000 עם מונה טעינות (לבדיקת רענון ב-A1) |
| `KIOSK_GUIDE.md` | מדריך משתמש בעברית |
| `backup/`, `backup/v2/`, `backup/Chrome +/` | גרסאות שמורות. v2 = הגרסה הנוכחית (Chromium). "Chrome +" = ניסיון עם Chrome שנזנח |

## איך הקיוסק עובד (setup.sh)
- GDM עם התחברות אוטומטית ל-`kiosk`, לסשן X בשם Kiosk (`/usr/share/xsessions/kiosk.desktop` → `/usr/local/bin/kiosk-session`).
- `kiosk-session`: מכבה שומר מסך, מריץ unclutter, הקלטת ffmpeg בלולאה, x11vnc בלולאה, ו-Chromium `--kiosk` בלולאה. גודל החלון לפי `xrandr`, כי אין מנהל חלונות.
- תמונת פתיחה: אם יש `splash.png` ליד `setup.sh`, הוא מועתק ל-`/home/kiosk/kiosk-splash/` עם `splash.html`. בעליית הקיוסק Chromium פותח את הדף (התמונה מתוחה למסך מלא), ואחרי 30 שניות עובר לכתובת שאחרי `#`. אחרי קריסה של Chromium: ישר לאתר.
- מקלדת וירטואלית: Onboard ב-`kiosk-session`, בלולאה. קופצת אוטומטית בשדה טקסט (auto-show דרך at-spi, ולכן Chromium רץ עם `--force-renderer-accessibility`, וגם `gir1.2-atspi-2.0` נדרש). צמודה לתחתית ברוחב מלא. שפה: אנגלית בלבד (`setxkbmap -layout us`). עברית (מקלדת וגופנים) נדחתה בינתיים: `culmus` לא ירד במחשב ההורדה, הסיבה לא ידועה. ההגדרות ב-gsettings של kiosk בכל עלייה. **עוד לא נבדק בטאבלט.**
- VLC מותקן לצפייה בהקלטות בתור `user`.
- הכתובת נשמרת ב-`/etc/kiosk/url`. סיסמת VNC ב-`/etc/kiosk/vncpasswd`.
- שירות `kiosk-corner` (Python, root, קורא מ-`/dev/input`), פועל רק כשהקיוסק פעיל:
  - לחיצה של 5 שניות על הפינה השמאלית העליונה: `loginctl terminate-user kiosk`, חזרה למסך ההתחברות.
  - A1 (שולח Ctrl+Alt+Del): רענון. F5 נשלח דרך מקלדת uinput בשם `kiosk-keys`, רק אחרי שחרור Ctrl ו-Alt.
  - KEY_BRIGHTNESSUP/DOWN: שינוי של 10%, מינימום 5%. כותב ל-`/sys/class/backlight`, בסדר העדיפות firmware, platform, raw.
- הקלטה: `/var/kiosk-recordings`, חצי רזולוציה, 5fps, קבצי MKV של 6 שעות. timer מוחק קבצים ישנים מעל 70% מהדיסק, או כשנשארים פחות מ-2GB פנויים.
- VNC: x11vnc, port 5900, צפייה ושליטה. נבדק עם TigerVNC ועובד.
- נעילות Chromium: `/etc/chromium-browser/policies/managed/kiosk.json`. ב-Xorg: DontVTSwitch ו-DontZap.
- מקלדת על המסך מופעלת במסך ההתחברות דרך dconf.

## היסטוריה חשובה
- הניסיון לעבור ל-Chrome כ-deb: אחריו המסך הגרפי לא עלה. הוחזר ל-v2.
- בטאבלט שולחן העבודה (gdm3, gnome-shell) הוסר מסיבה לא ידועה. `setup.sh` בודק את זה עכשיו ועוצר. תוקן ע"י חיבור זמני לאינטרנט, `apt upgrade` ו-`apt install ubuntu-desktop gdm3`. לא להריץ `apt autoremove`.
- במחשב ההורדה מופעל multiarch i386. לכן ההורדות מסננות ל-amd64 ו-all בלבד.

## בעיות פתוחות
1. **כפתורי התאורה +/- לא הגיבו אחרי `update_offline.sh`.** עובדים אחרי הרצה מחדש של `setup.sh`. חשד: race, השירות סורק מכשירים רק בעלייה. אם חוזר אחרי reboot: להוסיף סריקה חוזרת של `/dev/input`.
2. **קבצי הקלטה בגודל 0.** לא ברור אם זה קובץ אחד שעוד לא נכתב (buffering) או ש-ffmpeg קורס (הרבה קבצים). ביקשתי `ls -la /var/kiosk-recordings`. תיקון אפשרי: כתיבה תכופה לדיסק (flush) ב-ffmpeg.

## העדפות עבודה של עודד
- שיחה בעברית. כל טקסט בתוכנה, בממשק ובפלט באנגלית.
- תשובות קצרות וממוקדות. עודד חוזר לתכנות אחרי 30 שנה, אז להסביר בבהירות.
- לפני כתיבת קוד: תוכנית קצרה ושאלות, ולחכות ל-**"נכתוב קוד"**. "שאלה" = הסבר בלי קוד.
- בלי הערות בקוד, אלא אם ביקש (חריג: `setup_explained.sh`).
- רק מה שביקש, ולא לשבור דברים שעובדים.
- "שמור גירסה X": להעתיק את הקבצים הנוכחיים ל-`backup/X/`.
- כל שינוי: commit ו-push לענף `claude/dazzling-hypatia-kbyrxu`.
