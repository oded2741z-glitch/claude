# YT Downloader

אתר נחיתה להורדת סרטונים מ-YouTube בסגנון מודרני (gradient סגול/ורוד, RTL).

## הפעלה

פתח את `index.html` בדפדפן, או הרץ שרת סטטי:

```
python3 -m http.server 8000
```

## חיבור backend

ה-frontend שולח POST ל-`/api/download` עם `{ videoId, format, quality }`
ומצפה לתשובה `{ downloadUrl, title }`.

להורדת YouTube אמיתית צריך שרת backend עם `yt-dlp` — לא ניתן לעשות זאת
ישירות בדפדפן בגלל CORS ומדיניות YouTube.

## קבצים

- `index.html` — מבנה הדף
- `styles.css` — עיצוב
- `script.js` — לוגיקה
