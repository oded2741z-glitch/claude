#!/bin/bash
# =============================================================================
# setup_explained.sh
# זהה בהתנהגות ל-setup.sh, עם הסברים מפורטים בעברית.
# הקובץ נועד ללמידה. להתקנה משתמשים ב-setup.sh.
#
# מה הסקריפט עושה, בקצרה:
#   1. מתקין את Chromium (snap) מהדיסק-און-קי, בלי אינטרנט
#   2. מתקין חבילות apt (unclutter, ffmpeg, x11vnc) מהדיסק-און-קי
#   3. יוצר את המשתמש kiosk, בלי סיסמה
#   4. שומר את הגדרות הקיוסק: כתובת, סיסמת VNC ותיקיית הקלטות
#   5. יוצר "סשן" (סביבת עבודה) מיוחד לקיוסק: דפדפן, הקלטה ו-VNC
#   6. מגדיר התחברות אוטומטית ל-kiosk בהדלקה
#   7. מפעיל מקלדת על המסך במסך ההתחברות
#   8. נועל את הדפדפן וחוסם קיצורי מקשים של המערכת
#   9. מתקין שירות לפינה, ל-A1 ולתאורה
#  10. מתקין מחיקה אוטומטית של הקלטות ישנות
#
# הרצה: sudo bash setup_explained.sh http://192.168.1.10
# =============================================================================

# set -e : עוצר את הסקריפט בשגיאה הראשונה, כדי לא להמשיך במצב שבור
# set -u : שגיאה אם משתמשים במשתנה שלא הוגדר, כדי לתפוס טעויות כתיב
# set -o pipefail : צינור (a | b) נכשל אם אחד מחלקיו נכשל, ולא רק האחרון
set -euo pipefail

# -----------------------------------------------------------------------------
# בדיקות לפני שמתחילים
# -----------------------------------------------------------------------------

# id -u מחזיר את מספר המשתמש. 0 הוא root (מנהל), ו-sudo מריץ בתור root.
# בלי הרשאות root אי אפשר להתקין תוכנות או לשנות קבצי מערכת.
if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash setup.sh http://192.168.1.10"
    exit 1
fi

# $# הוא מספר הפרמטרים שהועברו לסקריפט. צריך בדיוק אחד: הכתובת.
if [ $# -ne 1 ]; then
    echo "Usage: sudo bash setup.sh <URL>"
    exit 1
fi

# -----------------------------------------------------------------------------
# שאלת סיסמת VNC
# שואלים כבר בהתחלה, לפני שמתחילה ההתקנה הארוכה, כדי שלא יצטרכו לחכות ליד המחשב.
# -----------------------------------------------------------------------------
VNC_PASS_FILE="/etc/kiosk/vncpasswd"
VNC_PASS=""
while true; do
    # read -r : קורא טקסט כמו שהוא, בלי לפרש לוכסנים
    # read -s : לא מציג את מה שמקלידים (silent), כמו בסיסמה
    # read -p : מציג הודעה (prompt) לפני הקלט
    read -rsp "VNC password (max 8 characters, Enter keeps current): " VNC_PASS
    echo
    # -z בודק אם המחרוזת ריקה, כלומר אם לחצו רק Enter
    if [ -z "${VNC_PASS}" ]; then
        # -f בודק אם הקובץ קיים. אם כבר יש סיסמה מהתקנה קודמת, משאירים אותה.
        if [ -f "${VNC_PASS_FILE}" ]; then
            break
        fi
        echo "A VNC password is required."
        continue
    fi
    # מבקשים את הסיסמה פעם שנייה, כדי לתפוס טעות הקלדה
    read -rsp "Repeat VNC password: " VNC_PASS2
    echo
    if [ "${VNC_PASS}" = "${VNC_PASS2}" ]; then
        break
    fi
    echo "Passwords do not match."
done

# מכאן והלאה, כל מה שמודפס על המסך נשמר גם בקובץ יומן.
# exec משנה את הפלט של הסקריפט כולו. tee -a מציג על המסך וגם מוסיף לקובץ.
# 2>&1 שולח גם הודעות שגיאה לאותו מקום.
# זה מופיע אחרי שאלת הסיסמה, כדי שהסיסמה לא תיכנס ליומן.
exec > >(tee -a /var/log/kiosk-setup.log) 2>&1

# -----------------------------------------------------------------------------
# משתנים: כל ההגדרות במקום אחד
# -----------------------------------------------------------------------------
URL="$1"                                  # הכתובת שהועברה בהרצה
KIOSK_USER="kiosk"                        # שם המשתמש של הקיוסק
APT_PACKAGES="unclutter ffmpeg x11vnc"    # הסתרת סמן, הקלטה, שליטה מרחוק
RECORDINGS="/var/kiosk-recordings"        # תיקיית ההקלטות
RECORDINGS_LIMIT_PERCENT=70               # כמה אחוז מהדיסק ההקלטות יכולות לתפוס
# התיקייה שבה נמצא הסקריפט (על הדיסק-און-קי), ובתוכה kiosk-offline.
# dirname "$0" נותן את התיקייה של הסקריפט, ו-pwd הופך אותה לנתיב מלא.
OFFLINE="$(cd "$(dirname "$0")" && pwd)/kiosk-offline"
# לכאן מעתיקים את חבילות ה-apt. ה-USB לא מתאים: בנתיב שלו יש לפעמים
# רווחים, ולתוכנת apt אין הרשאה לקרוא ממנו.
LOCAL_REPO="/var/tmp/kiosk-debs"

# dpkg -s בודק אם חבילה מותקנת. gdm3 הוא מסך ההתחברות של Ubuntu Desktop.
# בלעדיו הסקריפט לא יעבוד, כי הוא בנוי על מסך ההתחברות הזה.
if ! dpkg -s gdm3 >/dev/null 2>&1; then
    echo "Ubuntu Desktop (gdm3) is required."
    exit 1
fi

# download.sh יוצר את הקובץ "complete" רק כשההורדה הסתיימה בהצלחה.
# אם הוא חסר, ההורדה נכשלה באמצע, ואסור להתקין מתיקייה חלקית.
if [ ! -f "${OFFLINE}/complete" ]; then
    echo "Offline files missing or incomplete: ${OFFLINE}"
    echo "Run download.sh again on a computer with internet."
    exit 1
fi

# -----------------------------------------------------------------------------
# שלב 1: התקנת snaps (Chromium והתלויות שלו) בלי אינטרנט
# הקובץ order.txt נוצר ב-download.sh, ובו הסדר הנכון: קודם התלויות ואז Chromium.
# כל שורה בו נראית כך: chromium_3000 (שם, קו תחתון, מספר גרסה).
# -----------------------------------------------------------------------------
echo "[1/10] Installing snaps"
while read -r stem; do
    [ -z "${stem}" ] && continue          # מדלגים על שורות ריקות
    name="${stem%_*}"                     # מוחק מהסוף את "_מספר" ומשאיר את השם
    rev="${stem##*_}"                     # מוחק מההתחלה עד "_" ומשאיר את המספר
    # snap list מציג את הגרסה המותקנת. awk לוקח מהשורה השנייה את העמודה השלישית (Rev).
    # || true : אם ה-snap לא מותקן, הפקודה נכשלת. בלי זה set -e היה עוצר את הסקריפט.
    current="$(snap list "${name}" 2>/dev/null | awk 'NR==2 {print $3}' || true)"
    # מתקינים רק אם ה-snap לא מותקן, או שהגרסה בדיסק-און-קי חדשה יותר.
    # =~ ^[0-9]+$ בודק שהגרסה המותקנת היא מספר, כדי שההשוואה -gt לא תיכשל.
    if [ -z "${current}" ] || { [[ "${current}" =~ ^[0-9]+$ ]] && [ "${rev}" -gt "${current}" ]; }; then
        echo "  ${name}"
        # snap ack מייבא את "תעודת המקור" של החבילה, הקובץ assert.
        # בלעדיה snap מסרב להתקין קובץ מקומי, או מנסה לבדוק באינטרנט.
        snap ack "${OFFLINE}/snaps/${stem}.assert" < /dev/null
        snap install "${OFFLINE}/snaps/${stem}.snap" < /dev/null
        # < /dev/null : מונע מהפקודה לקרוא את order.txt, שה-while קורא ממנו.
    fi
done < "${OFFLINE}/snaps/order.txt"

# -----------------------------------------------------------------------------
# שלב 2: התקנת חבילות apt בלי אינטרנט
# הטריק: יוצרים "מאגר" מקומי מהקבצים שבדיסק-און-קי, ואומרים ל-apt להשתמש רק בו.
# ככה apt עדיין פותר תלויות לבד, ולא צריך להתקין כל קובץ ידנית.
# -----------------------------------------------------------------------------
echo "[2/10] Installing apt packages"
rm -rf "${LOCAL_REPO}"                            # מוחקים העתקה ישנה, אם יש
cp -r "${OFFLINE}/debs" "${LOCAL_REPO}"           # מעתיקים את החבילות לדיסק
chmod -R a+rX "${LOCAL_REPO}"                     # נותנים לכולם הרשאת קריאה
# שורת מקור בפורמט של apt. [trusted=yes] אומר לסמוך על המאגר בלי חתימה דיגיטלית.
echo "deb [trusted=yes] file:${LOCAL_REPO} ./" > "${LOCAL_REPO}/local.list"
# מערך של אפשרויות ל-apt:
#   sourcelist  : קובץ המקורות הוא הקובץ המקומי שלנו בלבד
#   sourceparts=- : להתעלם מהמקורות הרגילים (האינטרנט) שבתיקייה sources.list.d
#   List-Cleanup=0 : לא למחוק את רשימות החבילות הרגילות שכבר שמורות במחשב
APT_LOCAL=(-o "Dir::Etc::sourcelist=${LOCAL_REPO}/local.list" -o "Dir::Etc::sourceparts=-" -o "APT::Get::List-Cleanup=0")
apt-get "${APT_LOCAL[@]}" update                  # קורא את רשימת החבילות במאגר המקומי
# DEBIAN_FRONTEND=noninteractive : לא לעצור ולשאול שאלות במהלך ההתקנה
DEBIAN_FRONTEND=noninteractive apt-get "${APT_LOCAL[@]}" install -y ${APT_PACKAGES}

# -----------------------------------------------------------------------------
# שלב 3: יצירת המשתמש kiosk
# -----------------------------------------------------------------------------
echo "[3/10] Creating user ${KIOSK_USER}"
# יוצרים רק אם המשתמש לא קיים, כדי שאפשר יהיה להריץ את הסקריפט שוב
if ! id "${KIOSK_USER}" >/dev/null 2>&1; then
    # -m יוצר תיקיית בית, ו--s קובע את ה-shell (bash)
    useradd -m -s /bin/bash "${KIOSK_USER}"
fi
# passwd -d מוחק את הסיסמה, וכך אפשר להיכנס בלי סיסמה.
# זה בטוח: ל-kiosk אין הרשאות ניהול (sudo), ו-SSH לא מאפשר כניסה בלי סיסמה.
passwd -d "${KIOSK_USER}"

# -----------------------------------------------------------------------------
# שלב 4: שמירת הגדרות הקיוסק
# -----------------------------------------------------------------------------
echo "[4/10] Saving kiosk settings"
mkdir -p /etc/kiosk                               # -p: לא שגיאה אם כבר קיים
echo "${URL}" > /etc/kiosk/url                    # הכתובת בקובץ נפרד, כדי שקל לשנות
# שומרים סיסמה חדשה רק אם הוקלדה, כי Enter ריק משאיר את הקיימת.
# x11vnc -storepasswd מצפין את הסיסמה לפורמט ש-VNC מבין.
if [ -n "${VNC_PASS}" ]; then
    x11vnc -storepasswd "${VNC_PASS}" "${VNC_PASS_FILE}" >/dev/null
fi
# הרשאות לקובץ הסיסמה: הבעלים root, והקבוצה kiosk (כי x11vnc רץ בתור kiosk).
# 640 = הבעלים קורא וכותב, הקבוצה רק קוראת, ושאר המשתמשים לא רואים את הקובץ.
chown root:"${KIOSK_USER}" "${VNC_PASS_FILE}"
chmod 640 "${VNC_PASS_FILE}"
# תיקיית ההקלטות שייכת ל-kiosk, כי ffmpeg רץ בתור kiosk וכותב אליה.
# 755 = כולם יכולים לקרוא, ולכן גם user יכול לצפות בהקלטות.
mkdir -p "${RECORDINGS}"
chown "${KIOSK_USER}:${KIOSK_USER}" "${RECORDINGS}"
chmod 755 "${RECORDINGS}"

# -----------------------------------------------------------------------------
# שלב 5: סשן הקיוסק
# "סשן" הוא מה שרץ אחרי ההתחברות. בדרך כלל זה שולחן העבודה של GNOME,
# ובקיוסק במקומו רץ סקריפט שלנו: הדפדפן, ההקלטה ו-VNC. אין תפריטים ואין חלונות נוספים.
#
# cat > file <<'EOF' ... EOF כותב את כל הטקסט שעד EOF לתוך הקובץ.
# המירכאות סביב 'EOF' אומרות לא להחליף משתנים ($) עכשיו. הם יוחלפו
# רק כשהסקריפט שנוצר ירוץ בפועל.
# -----------------------------------------------------------------------------
echo "[5/10] Creating kiosk session"
cat > /usr/local/bin/kiosk-session <<'EOF'
#!/bin/bash
# ---- מסך: לא לכבות ולא להחשיך ----
xset s off          # מכבה את שומר המסך
xset s noblank      # לא להחשיך את המסך
xset -dpms          # מכבה את חיסכון החשמל של המסך
# ---- הסתרת סמן העכבר אחרי שנייה בלי תזוזה ----
# & בסוף מריץ את התוכנה ברקע, והסקריפט ממשיך הלאה.
unclutter -idle 1 -root &

# ---- הקלטת מסך ----
# ( ... ) & מריץ את כל הבלוק ברקע. הלולאה מפעילה את ffmpeg מחדש אם הוא נופל.
(
    while true; do
        # xrandr מציג את גודל המסך. sed מוציא מהשורה "current 2160 x 1440,"
        # רק את המספרים, בפורמט 2160x1440 ש-ffmpeg צריך.
        GEOM="$(xrandr --current 2>/dev/null | sed -n 's/.* current \([0-9]*\) x \([0-9]*\),.*/\1x\2/p' | head -n 1)"
        if [ -n "$GEOM" ]; then
            # -nostdin             : לא לקרוא מהמקלדת
            # -loglevel error      : להדפיס רק שגיאות
            # -f x11grab           : מקור הווידאו הוא המסך הגרפי (X)
            # -draw_mouse 0        : לא לצייר את הסמן בהקלטה
            # -framerate 5         : 5 תמונות לשנייה
            # -video_size / -i     : גודל המסך, ואיזה מסך ($DISPLAY, למשל :1)
            # -vf scale=...        : הקטנה לחצי. trunc(iw/4)*2 = חצי מהרוחב,
            #                        מעוגל למספר זוגי, כי הקידוד דורש מספרים זוגיים
            # -c:v libx264         : קידוד H.264, נפוץ וחסכוני במקום
            # -preset veryfast     : קידוד מהיר, פחות עומס על המעבד
            # -crf 30              : איכות. מספר גבוה = קובץ קטן ואיכות נמוכה יותר
            # -pix_fmt yuv420p     : פורמט צבע שכל נגן מכיר
            # -f segment           : לחלק את ההקלטה לקבצים נפרדים
            # -segment_time 21600  : כל קובץ 21600 שניות = 6 שעות
            # -reset_timestamps 1  : כל קובץ מתחיל מזמן 0, כדי שהנגנים יציגו אותו נכון
            # -strftime 1          : להחליף בשם הקובץ את %Y-%m-%d וכו' בתאריך ובשעה
            # MKV                  : קובץ שנשאר קריא גם אם החשמל נקטע באמצע הקלטה
            ffmpeg -nostdin -loglevel error \
                -f x11grab -draw_mouse 0 -framerate 5 -video_size "$GEOM" -i "$DISPLAY" \
                -vf "scale=trunc(iw/4)*2:trunc(ih/4)*2" \
                -c:v libx264 -preset veryfast -crf 30 -pix_fmt yuv420p \
                -f segment -segment_time 21600 -reset_timestamps 1 -strftime 1 \
                /var/kiosk-recordings/%Y-%m-%d_%H-%M-%S.mkv
        fi
        sleep 5     # אם ffmpeg נפל, מחכים 5 שניות לפני שמנסים שוב
    done
) &

# ---- שרת VNC: צפייה ושליטה מרחוק ----
(
    while true; do
        # -display     : איזה מסך לשתף (המסך של הקיוסק)
        # -rfbauth     : קובץ הסיסמה המוצפנת
        # -rfbport 5900: הפורט שאליו מתחברים
        # -forever     : לא לצאת אחרי שהצופה מתנתק
        # -shared      : לאפשר כמה צופים בבת אחת
        # -noxdamage   : מונע בעיות רענון תמונה במחשבים מסוימים
        # -quiet       : פחות הודעות
        # ברירת המחדל של x11vnc היא גם שליטה (עכבר ומקלדת), לא רק צפייה.
        x11vnc -display "$DISPLAY" -rfbauth /etc/kiosk/vncpasswd -rfbport 5900 \
            -forever -shared -noxdamage -quiet >/dev/null 2>&1
        sleep 2
    done
) &

# ---- הדפדפן ----
URL="$(cat /etc/kiosk/url)"     # הכתובת נקראת מהקובץ בכל פעם שהקיוסק עולה
# קובץ ההגדרות של Chromium. בגרסת snap הוא נמצא בתוך תיקיית snap.
PREFS="$HOME/snap/chromium/common/chromium/Default/Preferences"

# לולאה אינסופית: אם Chromium נסגר או קורס, הוא נפתח מחדש.
while true; do
    # אחרי קריסה, Chromium מציג "Restore pages?". כאן מסמנים בקובץ ההגדרות
    # שהוא נסגר כרגיל, כדי שהשאלה לא תופיע.
    if [ -f "$PREFS" ]; then
        sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$PREFS"
    fi
    # בקיוסק אין מנהל חלונות, ולכן Chromium לא תמיד מתמתח לבד על כל המסך.
    # כאן מודדים את המסך (בפורמט 2160,1440) ונותנים לדפדפן גודל ומיקום מדויקים.
    SIZE="$(xrandr --current 2>/dev/null | sed -n 's/.* current \([0-9]*\) x \([0-9]*\),.*/\1,\2/p' | head -n 1)"
    WINDOW=()
    if [ -n "$SIZE" ]; then
        WINDOW=(--window-position=0,0 --window-size="$SIZE")
    fi
    # --kiosk                         : מסך מלא, בלי שורת כתובת ובלי תפריטים
    # --noerrdialogs                  : בלי חלונות שגיאה
    # --disable-infobars              : בלי פסי הודעות בראש הדף
    # --disable-session-crashed-bubble: בלי הודעה על קריסה
    # --disable-features=Translate    : בלי הצעה לתרגם את הדף
    # --no-first-run                  : בלי מסך "ברוך הבא" בפעם הראשונה
    # --password-store=basic          : בלי בקשה לפתוח "מחזיק מפתחות" (keyring)
    # --check-for-update-interval=... : לא לבדוק עדכונים (פעם בשנה, בשניות)
    # --overscroll-history-navigation=0: החלקה הצידה לא חוזרת לדף הקודם
    # --disable-pinch                 : בלי הגדלה בשתי אצבעות
    /snap/bin/chromium \
        --kiosk \
        "${WINDOW[@]}" \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --disable-features=Translate \
        --no-first-run \
        --password-store=basic \
        --check-for-update-interval=31536000 \
        --overscroll-history-navigation=0 \
        --disable-pinch \
        "$URL"
    sleep 2     # מחכים 2 שניות לפני פתיחה מחדש
done
EOF
chmod 755 /usr/local/bin/kiosk-session     # הרשאת הרצה

# קובץ שמודיע למסך ההתחברות שקיים סשן בשם Kiosk, ואיזו תוכנה מפעילה אותו.
# הוא נכנס לתיקייה xsessions, וזה אומר שהסשן רץ על X (ולא על Wayland).
# ffmpeg, x11vnc ו-unclutter עובדים רק על X.
cat > /usr/share/xsessions/kiosk.desktop <<'EOF'
[Desktop Entry]
Name=Kiosk
Exec=/usr/local/bin/kiosk-session
Type=Application
EOF

# AccountsService שומר לכל משתמש איזה סשן הוא מקבל בהתחברות.
# כאן קובעים שהמשתמש kiosk תמיד מקבל את סשן הקיוסק ולא את GNOME.
# SystemAccount=false : להציג את kiosk ברשימת המשתמשים במסך ההתחברות,
# כדי שאפשר יהיה לחזור אליו בלחיצה.
mkdir -p /var/lib/AccountsService/users
cat > "/var/lib/AccountsService/users/${KIOSK_USER}" <<'EOF'
[User]
Session=kiosk
XSession=kiosk
SystemAccount=false
EOF

# -----------------------------------------------------------------------------
# שלב 6: התחברות אוטומטית ל-kiosk בהדלקה
# ההגדרות של מסך ההתחברות (GDM) נמצאות ב-/etc/gdm3/custom.conf, בחלק [daemon].
# -----------------------------------------------------------------------------
echo "[6/10] Enabling auto-login"
GDM_CONF="/etc/gdm3/custom.conf"
touch "${GDM_CONF}"                       # יוצר את הקובץ אם הוא לא קיים
# אם אין בקובץ חלק [daemon], מוסיפים אותו בסוף
if ! grep -q '^\[daemon\]' "${GDM_CONF}"; then
    printf '[daemon]\n' >> "${GDM_CONF}"
fi
# מוחקים הגדרות התחברות אוטומטית קודמות, גם כאלה שמסומנות כהערה (#).
# ככה, למשל, אם בהתקנת Ubuntu הוגדרה התחברות אוטומטית ל-user, היא לא תתנגש.
sed -i '/^#\?[[:space:]]*AutomaticLogin/d' "${GDM_CONF}"
# מוסיפים מיד אחרי השורה [daemon] את שתי השורות שלנו (a = append)
sed -i "/^\[daemon\]/a AutomaticLoginEnable=true\nAutomaticLogin=${KIOSK_USER}" "${GDM_CONF}"

# -----------------------------------------------------------------------------
# שלב 7: מקלדת על המסך במסך ההתחברות
# לטאבלט אין מקלדת, אז בלי זה user לא יכול להקליד סיסמה.
# dconf הוא "מאגר ההגדרות" של GNOME. למסך ההתחברות יש מאגר משלו בשם gdm.
# -----------------------------------------------------------------------------
echo "[7/10] Enabling on-screen keyboard on login screen"
mkdir -p /etc/dconf/profile /etc/dconf/db/gdm.d
# הפרופיל אומר ל-dconf מאילו מקורות לקרוא את ההגדרות של מסך ההתחברות.
# בדרך כלל הוא כבר קיים, ויוצרים אותו רק אם הוא חסר.
if [ ! -f /etc/dconf/profile/gdm ]; then
    printf 'user-db:user\nsystem-db:gdm\nfile-db:/usr/share/gdm/greeter-dconf-defaults\n' > /etc/dconf/profile/gdm
fi
# ההגדרה עצמה: להפעיל את המקלדת על המסך (screen keyboard)
cat > /etc/dconf/db/gdm.d/90-kiosk <<'EOF'
[org/gnome/desktop/a11y/applications]
screen-keyboard-enabled=true
EOF
dconf update                              # בונה מחדש את מאגר ההגדרות מהקבצים

# -----------------------------------------------------------------------------
# שלב 8: נעילת הדפדפן וחסימת קיצורים
# -----------------------------------------------------------------------------
echo "[8/10] Locking browser and shortcuts"
# "מדיניות" (policies) של Chromium: הגדרות שהמשתמש לא יכול לשנות.
#   DeveloperToolsAvailability 2 : חוסם את כלי המפתחים (F12)
#   IncognitoModeAvailability 1  : חוסם גלישה בסתר
#   BrowserGuestModeEnabled      : חוסם מצב אורח
#   BrowserAddPersonEnabled      : חוסם הוספת פרופיל
#   PasswordManagerEnabled       : לא להציע לשמור סיסמאות
#   TranslateEnabled             : לא להציע תרגום
#   DownloadRestrictions 3       : חוסם הורדת קבצים
#   PrintingEnabled              : חוסם הדפסה
# בקובץ JSON אסור לכתוב הערות, ולכן ההסבר נמצא כאן ולא בתוך הקובץ.
mkdir -p /etc/chromium-browser/policies/managed
cat > /etc/chromium-browser/policies/managed/kiosk.json <<'EOF'
{
    "DeveloperToolsAvailability": 2,
    "IncognitoModeAvailability": 1,
    "BrowserGuestModeEnabled": false,
    "BrowserAddPersonEnabled": false,
    "PasswordManagerEnabled": false,
    "TranslateEnabled": false,
    "DownloadRestrictions": 3,
    "PrintingEnabled": false
}
EOF

# הגדרות לשרת התצוגה X, שעליו רץ הקיוסק:
#   DontVTSwitch : חוסם Ctrl+Alt+F1..F12 (מעבר למסכי טקסט)
#   DontZap      : חוסם Ctrl+Alt+Backspace (סגירה מיידית של המסך הגרפי)
mkdir -p /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/10-kiosk.conf <<'EOF'
Section "ServerFlags"
    Option "DontVTSwitch" "true"
    Option "DontZap" "true"
EndSection
EOF
# mask = נטרול. Ctrl+Alt+Delete במסך טקסט לא יפעיל מחדש את המחשב.
systemctl mask ctrl-alt-del.target

# -----------------------------------------------------------------------------
# שלב 9: שירות הכפתורים (פינה, A1, תאורה)
# תוכנת Python שרצה ברקע בתור root מרגע ההדלקה, וקוראת ישירות מהחומרה:
#   - לחיצה של 5 שניות על הפינה השמאלית העליונה : יציאה מהקיוסק
#   - A1 (שולח Ctrl+Alt+Delete)                  : רענון הדף (F5)
#   - כפתורי תאורה + ו-                           : שינוי התאורה
# היא פועלת רק כשהקיוסק פעיל, כדי לא להפריע ל-user.
#
# למה Python בלי ספריות נוספות? כדי שלא יהיה צורך להוריד עוד חבילות.
# Python 3 מגיע עם Ubuntu.
# -----------------------------------------------------------------------------
echo "[9/10] Installing kiosk buttons service"
cat > /usr/local/bin/kiosk-corner <<'EOF'
import fcntl        # ioctl: שאלות ופקודות ישירות לחומרה
import os           # פתיחה וקריאה של קבצים, כולל קבצי חומרה
import select       # המתנה לנתונים מכמה מכשירים בבת אחת
import struct       # המרה בין נתונים בינאריים למספרים
import subprocess   # הרצת פקודות מערכת (loginctl)
import time         # מדידת זמן

# ---- הגדרות שאפשר לשנות ----
KIOSK_USER = "kiosk"
CORNER = 0.08       # גודל הפינה: 8% מרוחב המסך ומגובהו
HOLD = 5.0          # כמה שניות להחזיק בפינה
STEP = 0.10         # כל לחיצת תאורה משנה ב-10%
MIN_LEVEL = 0.05    # תאורה מינימלית 5%, כדי שהמסך לא יחשיך לגמרי
DEBOUNCE = 0.15     # התעלמות מלחיצה כפולה תוך 0.15 שניות
                    # (לפעמים שני מכשירים מדווחים על אותה לחיצה)

# ---- קבועים של Linux ----
# כל אירוע של מקלדת או מסך מגע מגיע כרשומה של 24 בתים:
#   זמן (2 מספרים ארוכים, l) + סוג (H) + קוד (H) + ערך (i)
EVENT = struct.Struct("llHHi")
EV_SYN = 0          # סוג "סוף קבוצת אירועים"
SYN_REPORT = 0
EV_KEY = 1          # סוג "מקש": ערך 1 = נלחץ, 0 = שוחרר, 2 = מוחזק (חזרה)
EV_ABS = 3          # סוג "מיקום מוחלט" (מסך מגע)
ABS_X = 0           # ציר X
ABS_Y = 1           # ציר Y
BTN_TOUCH = 0x14A   # "אצבע על המסך"
KEY_F5 = 63         # מספרי המקשים, כמו שהופיעו ב-evtest
KEY_LEFTCTRL = 29
KEY_LEFTALT = 56
KEY_RIGHTCTRL = 97
KEY_RIGHTALT = 100
KEY_DELETE = 111
KEY_BRIGHTNESSDOWN = 224
KEY_BRIGHTNESSUP = 225
CTRL_KEYS = {KEY_LEFTCTRL, KEY_RIGHTCTRL}
ALT_KEYS = {KEY_LEFTALT, KEY_RIGHTALT}
WATCHED_KEYS = (KEY_DELETE, KEY_BRIGHTNESSDOWN, KEY_BRIGHTNESSUP)
INPUT_PROP_DIRECT = 1                   # סימן שהמכשיר הוא מסך מגע ולא משטח מגע
BACKLIGHT = "/sys/class/backlight"      # כאן Linux חושף את בקרת התאורה
# אם יש כמה בקרי תאורה, זה סדר העדיפות. אותו סדר משמש את GNOME.
BACKLIGHT_TYPES = ("firmware", "platform", "raw")


# ioctl דורש "מספר פקודה" שמורכב מכמה חלקים: כיוון (קריאה/כתיבה), גודל,
# אות מזהה ומספר. שתי הפונקציות האלה בונות את המספר, כמו המאקרו _IOR/_IOW בשפת C.
def ioc_read(nr, size):
    return (2 << 30) | (size << 16) | (ord("E") << 8) | nr


def ioc_write(nr, size):
    return (1 << 30) | (size << 16) | (ord("U") << 8) | nr


# יוצר "מקלדת מדומה" דרך uinput, מנגנון של Linux ליצירת מכשירי קלט בתוכנה.
# המקלדת יודעת ללחוץ רק F5, ומשמשת לרענון הדף כשלוחצים A1.
def create_keyboard():
    try:
        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(fd, ioc_write(100, 4), EV_KEY)      # המכשיר שולח מקשים
        fcntl.ioctl(fd, ioc_write(101, 4), KEY_F5)      # ספציפית את F5
        # תיאור המכשיר: שם (80 בתים), מזהים (סוג חיבור 0x06 = וירטואלי),
        # ואחריהם טבלאות צירים ריקות (אין צירים במקלדת)
        name = b"kiosk-keys".ljust(80, b"\0")
        os.write(fd, name + struct.pack("4HI", 0x06, 1, 1, 1, 0) + bytes(256 * 4))
        fcntl.ioctl(fd, (ord("U") << 8) | 1)            # "צור את המכשיר"
        return fd
    except OSError:
        return None     # אם נכשל, רק הרענון לא יעבוד. כל השאר ממשיך


# לחיצה על מקש במקלדת המדומה: לחיצה (1), ואז שחרור (0),
# ואחרי כל אחד אירוע "סוף קבוצה" כדי שהמערכת תעבד אותו.
def press_key(keyboard, code):
    if keyboard is None:
        return
    try:
        for value in (1, 0):
            os.write(keyboard, EVENT.pack(0, 0, EV_KEY, code, value))
            os.write(keyboard, EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))
    except OSError:
        pass


# שואל את מסך המגע מה הטווח של ציר מסוים, למשל X מ-0 עד 4095.
# התשובה היא 6 מספרים: ערך נוכחי, מינימום, מקסימום ועוד. לוקחים מינימום ומקסימום.
def abs_range(fd, axis):
    buf = bytearray(24)
    fcntl.ioctl(fd, ioc_read(0x40 + axis, 24), buf)
    values = struct.unpack("6i", buf)
    return values[1], values[2]


# בודק אם המכשיר הוא "ישיר", כלומר מסך מגע שבו נוגעים ישירות בתמונה.
# משטח מגע (touchpad) אינו ישיר, ולכן לא נגיב לפינה שלו.
def is_direct(fd):
    buf = bytearray(4)
    fcntl.ioctl(fd, ioc_read(0x09, 4), buf)
    return bool(buf[0] & (1 << INPUT_PROP_DIRECT))


# בודק אם למכשיר יש לפחות אחד מהמקשים שמעניינים אותנו (Delete או תאורה).
# המערכת מחזירה "מפת ביטים": ביט לכל מקש, 1 = קיים.
def has_watched_keys(fd):
    buf = bytearray(96)
    fcntl.ioctl(fd, ioc_read(0x20 + EV_KEY, 96), buf)
    return any(buf[code // 8] & (1 << (code % 8)) for code in WATCHED_KEYS)


# "מצב" לכל מכשיר: מה אנחנו זוכרים עליו בין אירוע לאירוע
#   x, y      : טווחי הצירים (רק במסך מגע)
#   px, py    : מיקום האצבע האחרון
#   down      : האם יש אצבע על המסך
#   since     : מתי האצבע נכנסה לפינה
#   fired     : האם כבר יצאנו מהקיוסק בלחיצה הזו (כדי לא לצאת פעמיים)
#   held      : אילו מקשי Ctrl/Alt מוחזקים כרגע
#   refresh   : A1 נלחץ, וממתינים לשחרור Ctrl ו-Alt כדי לשלוח F5
def new_state(xr=None, yr=None):
    return {"x": xr, "y": yr, "px": None, "py": None, "down": False,
            "since": None, "fired": False, "held": set(), "refresh": False}


# עובר על כל מכשירי הקלט (/dev/input/event0, event1, ...) ושומר רק את אלה
# שמעניינים אותנו: מסכי מגע ומכשירים עם המקשים שלנו.
def open_devices():
    devices = {}
    for name in sorted(os.listdir("/dev/input")):
        if not name.startswith("event"):
            continue
        try:
            # O_NONBLOCK: קריאה לא תיתקע אם אין נתונים
            fd = os.open("/dev/input/" + name, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        state = None
        try:
            if is_direct(fd):
                xr = abs_range(fd, ABS_X)
                yr = abs_range(fd, ABS_Y)
                if xr[1] > xr[0] and yr[1] > yr[0]:     # טווח תקין
                    state = new_state(xr, yr)
        except OSError:
            pass
        if state is None:
            try:
                if has_watched_keys(fd):
                    state = new_state()
            except OSError:
                pass
        if state is None:
            os.close(fd)        # מכשיר שלא מעניין אותנו: סוגרים
        else:
            devices[fd] = state
    return devices


# האם האצבע בפינה השמאלית העליונה? כלומר, ב-8% הראשונים של X וב-8% הראשונים של Y
def in_corner(d):
    if d["x"] is None or d["px"] is None or d["py"] is None:
        return False
    xlo, xhi = d["x"]
    ylo, yhi = d["y"]
    return (d["px"] - xlo) <= CORNER * (xhi - xlo) and (d["py"] - ylo) <= CORNER * (yhi - ylo)


# מריץ את הפקודה loginctl (ניהול משתמשים מחוברים) ומחזיר את התשובה כטקסט
def loginctl(*args):
    result = subprocess.run(["loginctl", *args], capture_output=True, text=True)
    return result.stdout.strip()


# האם הקיוסק הוא מה שמוצג כרגע על המסך?
# seat0 הוא "עמדת העבודה" של המחשב. בודקים מי מחזיק בה כרגע.
def kiosk_active():
    session = loginctl("show-seat", "seat0", "-p", "ActiveSession", "--value")
    if not session:
        return False
    return loginctl("show-session", session, "-p", "Name", "--value") == KIOSK_USER


# יציאה מהקיוסק: סוגרים את כל התוכנות של kiosk, ו-GDM חוזר למסך ההתחברות
def exit_kiosk():
    if kiosk_active():
        subprocess.run(["loginctl", "terminate-user", KIOSK_USER])


# מוצא את בקר התאורה לפי סדר העדיפות
def backlight_path():
    try:
        names = sorted(os.listdir(BACKLIGHT))
    except OSError:
        return None
    for kind in BACKLIGHT_TYPES:
        for name in names:
            path = os.path.join(BACKLIGHT, name)
            try:
                with open(os.path.join(path, "type")) as f:
                    if f.read().strip() == kind:
                        return path
            except OSError:
                continue
    return None


# משנה את התאורה. direction הוא 1 להגברה או 1- להחלשה.
# ב-Linux, תאורה היא מספר בקובץ: brightness (נוכחי) מתוך max_brightness (מקסימום).
def change_brightness(direction):
    path = backlight_path()
    if path is None:
        return
    try:
        with open(os.path.join(path, "max_brightness")) as f:
            maximum = int(f.read())
        with open(os.path.join(path, "brightness")) as f:
            current = int(f.read())
        step = max(1, round(maximum * STEP))
        low = max(1, round(maximum * MIN_LEVEL))
        # הערך החדש, מוגבל בין המינימום למקסימום
        value = min(maximum, max(low, current + direction * step))
        with open(os.path.join(path, "brightness"), "w") as f:
            f.write(str(value))
    except (OSError, ValueError):
        pass


# הלולאה הראשית: מחכה לאירועים מכל המכשירים ומגיבה
def watch(devices, keyboard):
    last_brightness = 0.0
    while True:
        # select מחכה עד שלאחד המכשירים יש נתונים, או עד 0.2 שניות.
        # ההמתנה המוגבלת מאפשרת לבדוק את הזמן בפינה גם כשהאצבע לא זזה.
        ready, _, _ = select.select(list(devices), [], [], 0.2)
        for fd in ready:
            d = devices[fd]
            try:
                data = os.read(fd, EVENT.size * 64)     # עד 64 אירועים בבת אחת
            except BlockingIOError:
                continue
            # מפרקים את הנתונים לאירועים של 24 בתים
            for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _, _, etype, code, value = EVENT.unpack_from(data, offset)
                if etype == EV_ABS and code == ABS_X:
                    d["px"] = value                     # מיקום X של האצבע
                elif etype == EV_ABS and code == ABS_Y:
                    d["py"] = value                     # מיקום Y של האצבע
                elif etype != EV_KEY:
                    continue                            # לא מקש: לא מעניין
                elif code == BTN_TOUCH:
                    d["down"] = value == 1              # אצבע נגעה או הורמה
                elif code in CTRL_KEYS or code in ALT_KEYS:
                    # זוכרים אילו Ctrl/Alt מוחזקים
                    if value:
                        d["held"].add(code)
                    else:
                        d["held"].discard(code)
                    # A1 שולח: Alt, Ctrl, Delete, ואז משחרר הכול.
                    # שולחים F5 רק אחרי ש-Ctrl ו-Alt שוחררו, אחרת הדפדפן
                    # יקבל Ctrl+Alt+F5 במקום F5, וזה לא מרענן.
                    if d["refresh"] and not d["held"]:
                        d["refresh"] = False
                        if kiosk_active():
                            press_key(keyboard, KEY_F5)
                elif code == KEY_DELETE and value == 1:
                    # Delete עם Ctrl ו-Alt מוחזקים = A1. מסמנים שצריך לרענן.
                    if d["held"] & CTRL_KEYS and d["held"] & ALT_KEYS:
                        d["refresh"] = True
                elif code in (KEY_BRIGHTNESSUP, KEY_BRIGHTNESSDOWN) and value in (1, 2):
                    # כפתורי תאורה: 1 = לחיצה, 2 = החזקה (חזרה אוטומטית)
                    now = time.monotonic()
                    if now - last_brightness >= DEBOUNCE and kiosk_active():
                        last_brightness = now
                        change_brightness(1 if code == KEY_BRIGHTNESSUP else -1)
        # בדיקת הפינה, פעם בכל סיבוב (לפחות 5 פעמים בשנייה)
        now = time.monotonic()
        for d in devices.values():
            if d["down"] and in_corner(d):
                if d["since"] is None:
                    d["since"] = now                    # האצבע נכנסה לפינה עכשיו
                elif not d["fired"] and now - d["since"] >= HOLD:
                    d["fired"] = True                   # עברו 5 שניות: יוצאים
                    exit_kiosk()
            else:
                d["since"] = None                       # האצבע עזבה: מאפסים
                d["fired"] = False


# נקודת ההתחלה. אם משהו נכשל, למשל מכשיר נותק, פותחים הכול מחדש וממשיכים.
def main():
    keyboard = create_keyboard()
    while True:
        devices = open_devices()
        if not devices:
            time.sleep(5)           # עוד אין מכשירים (למשל, מיד בהדלקה): מחכים
            continue
        try:
            watch(devices, keyboard)
        except OSError:
            time.sleep(1)
        finally:
            for fd in devices:      # סוגרים את כל המכשירים לפני שפותחים מחדש
                try:
                    os.close(fd)
                except OSError:
                    pass


main()
EOF
chmod 755 /usr/local/bin/kiosk-corner

# קובץ שירות של systemd: מנהל השירותים של Linux.
#   After=systemd-logind.service : להתחיל אחרי שירות ניהול המשתמשים
#   ExecStart                    : הפקודה שמריצים
#   Restart=always, RestartSec=2 : אם השירות נופל, להפעיל אותו מחדש אחרי 2 שניות
#   WantedBy=graphical.target    : להפעיל בכל הדלקה, יחד עם המצב הגרפי
cat > /etc/systemd/system/kiosk-corner.service <<'EOF'
[Unit]
Description=Kiosk corner exit, refresh and brightness buttons
After=systemd-logind.service

[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/kiosk-corner
Restart=always
RestartSec=2

[Install]
WantedBy=graphical.target
EOF
systemctl daemon-reload                   # טוען מחדש את קבצי השירותים
systemctl enable kiosk-corner.service     # מפעיל את השירות בכל הדלקה

# -----------------------------------------------------------------------------
# שלב 10: מחיקה אוטומטית של הקלטות ישנות
# סקריפט קטן ש-systemd מריץ כל דקה. הוא מוחק את הקובץ הישן ביותר כל עוד:
#   - ההקלטות תופסות יותר מ-70% מגודל הדיסק, או
#   - נשארו בדיסק פחות מ-2GB פנויים
# הוא אף פעם לא מוחק את הקובץ האחרון, כי זו ההקלטה שרצה עכשיו.
#
# שימו לב: כאן <<EOF בלי מירכאות, ולכן ${RECORDINGS} ו-${RECORDINGS_LIMIT_PERCENT}
# מוחלפים כבר עכשיו בערכים שלהם. משתנים עם לוכסן לפניהם (\$) נשארים כמו שהם,
# ויחושבו רק כשהסקריפט ירוץ. מאותה סיבה ההסבר כאן, ולא בתוך הקובץ.
#   df --output=size -B1 : גודל הדיסק בבתים
#   du -sb               : כמה בתים תופסת התיקייה
#   df --output=avail    : כמה בתים פנויים
#   ls -1 ... | sort     : רשימת הקבצים לפי שם. השם הוא תאריך, אז הישן ראשון
# -----------------------------------------------------------------------------
echo "[10/10] Installing recordings cleanup"
cat > /usr/local/bin/kiosk-recordings-cleanup <<EOF
#!/bin/bash
DIR="${RECORDINGS}"
LIMIT_PERCENT=${RECORDINGS_LIMIT_PERCENT}
MIN_FREE=\$((2 * 1024 * 1024 * 1024))
TOTAL=\$(df --output=size -B1 "\$DIR" | tail -n 1)
LIMIT=\$((TOTAL * LIMIT_PERCENT / 100))
while true; do
    mapfile -t FILES < <(ls -1 "\$DIR"/*.mkv 2>/dev/null | sort)
    if [ "\${#FILES[@]}" -lt 2 ]; then
        break
    fi
    USED=\$(du -sb "\$DIR" | cut -f1)
    FREE=\$(df --output=avail -B1 "\$DIR" | tail -n 1)
    if [ "\$USED" -le "\$LIMIT" ] && [ "\$FREE" -ge "\$MIN_FREE" ]; then
        break
    fi
    rm -f "\${FILES[0]}"
done
EOF
chmod 755 /usr/local/bin/kiosk-recordings-cleanup

# שירות "חד-פעמי" (oneshot): רץ, מסיים ויוצא
cat > /etc/systemd/system/kiosk-recordings-cleanup.service <<'EOF'
[Unit]
Description=Delete old kiosk recordings

[Service]
Type=oneshot
ExecStart=/usr/local/bin/kiosk-recordings-cleanup
EOF

# טיימר: מפעיל את השירות למעלה דקה אחרי ההדלקה, ומשם כל דקה
cat > /etc/systemd/system/kiosk-recordings-cleanup.timer <<'EOF'
[Unit]
Description=Delete old kiosk recordings every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable kiosk-recordings-cleanup.timer

# -----------------------------------------------------------------------------
# סיכום
# -----------------------------------------------------------------------------
echo
echo "Done. Kiosk URL: ${URL}"
echo "Recordings: ${RECORDINGS} (limit ${RECORDINGS_LIMIT_PERCENT}% of disk)"
echo "VNC: port 5900, available while the kiosk is active"
echo "Reboot to start the kiosk: sudo reboot"
