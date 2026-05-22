# YT Downloader

Modern landing page for a YouTube downloader (purple/pink gradient, glassmorphism).

## Run

Open `index.html` in a browser, or serve statically:

```
python3 -m http.server 8000
```

## Backend hookup

The frontend POSTs to `/api/download` with `{ videoId, format, quality }`
and expects `{ downloadUrl, title }` back.

Real YouTube downloads require a backend with `yt-dlp` — this cannot be
done directly from the browser due to CORS and YouTube's policies.

## Files

- `index.html` — markup
- `styles.css` — styling
- `script.js` — logic
