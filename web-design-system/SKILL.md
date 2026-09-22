---
name: web-design-system
description: Oded's light Web Design System for single-file HTML pages (home pages, functional tool pages, content pages). Use whenever Oded asks for an HTML page, website, landing page, or web tool. Not for Python/tkinter desktop apps, which use the separate dark theme.
---

# Web Design System

Build every HTML page with this design system so all of Oded's sites look like one family: same structure, styling, and colors. Content is project-specific: replace every [PLACEHOLDER] with real content before delivering.

Output one self-contained .html file per page (inline CSS and JS, no build step). All visible text is in English. No code comments unless Oded asks.

## Placeholders

| Placeholder | Meaning |
|---|---|
| [BRAND NAME] | Full site/brand name (footer, title) |
| [BRAND BASE] | First part of the logo wordmark |
| [BRAND ACCENT] | Second part of the wordmark, in accent color |
| [TAGLINE] | Short uppercase overline label |
| [HERO LINE 1] / [HERO LINE 2] | Headline lines; line 2 is gray |
| [SUBTITLE] | One-paragraph hero subtitle |
| [PILL 1..4] | Short trust/feature pills |
| [SECTION LABEL] | Uppercase label above the card grid |
| [CARD N ICON / TAG / TITLE / DESC / LINK / HREF] | Card content; ICON is a 3-letter mono label |
| [BENEFIT 1..4] | Short benefit statements |
| [CONTACT EMAIL] | Contact email |
| [GA4 ID] | Google Analytics 4 ID (optional) |
| [FAVICON HREF] | Path to the SVG favicon |
| [PAGE TITLE] / [PAGE DESCRIPTION] / [CANONICAL URL] | Per-page head metadata |

If a placeholder's content is unknown, ask Oded rather than inventing brand details.

## 1. Design philosophy

- Light, clean, minimal: white cards on a soft gray page, a single accent color.
- Generous whitespace, soft shadows, rounded corners, hairline borders.
- Calm and professional. Wordmark = [BRAND BASE] + [BRAND ACCENT], with the accent part in `<span class="logo-accent">`.
- Avoid these generic defaults: cream/off-white backgrounds, italic accent words in headlines, numbered "01/02/03" section labels, gradients, extra accent colors, decorative illustrations, emojis.

## 2. Design tokens

```css
:root {
  --primary-accent: #2563eb;
  --primary-hover:  #1d4ed8;
  --bg-light:       #f9fafb;
  --text-dark:      #111827;
  --text-gray:      #64748b;
  --border-color:   #e2e8f0;
}
```

To re-theme a project, change only `--primary-accent` and `--primary-hover`.

Other fixed colors:
- Surface (cards, header, footer): #ffffff
- Body text on content pages: #475569
- Faint hairline (overline rule): #cbd5e1
- Status dot (pills): #10b981
- Card border on hover: #bfdbfe
- Tag: background #eff6ff, border #bfdbfe, text var(--primary-accent)
- Content card: box-shadow 0 10px 25px rgba(0,0,0,0.05); border #f1f5f9
- Callout box: background #f8fafc; left border 4px #64748b; text #1e293b

Typography:
- Font stack: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif
- h1 (home): 4.5rem / 800 / letter-spacing -2px / line-height 1.1
- h1 second line `.gray-text`: color var(--text-gray); weight 700
- h1 (content pages): 2.2rem / letter-spacing -1px
- Overline and section labels: 0.75rem / 700 / letter-spacing 2px / uppercase / var(--text-gray)
- Card title: 1.25rem / 800 / letter-spacing -0.5px
- Subtitle and body: 1.05–1.15rem / line-height 1.6 / var(--text-gray)
- Content-page paragraphs: 1.05rem / #475569 / line-height 1.6

Base:
```css
* { margin:0; padding:0; box-sizing:border-box; font-family:<stack>; }
body { background:var(--bg-light); color:var(--text-dark); min-height:100vh;
       display:flex; flex-direction:column; overflow-x:hidden; }
```

- Spacing scale (rem): 0.5 · 0.75 · 1 · 1.5 · 2 · 3 · 4 · 5
- Radius: 4px tags · 6px toggles · 8px icons/buttons · 12px panels/menus · 16px cards · 50% round button
- Container: max-width 1400px, horizontal padding 6%. Content pages: max-width 800px.

## 3. Breakpoints

```css
@media (max-width:1024px){ .cards-grid{grid-template-columns:repeat(2,1fr);} h1{font-size:3.5rem;} }
@media (max-width:768px){ main{margin:1.5rem;padding:1.5rem;} h1{font-size:1.8rem;} }
@media (max-width:600px){ .cards-grid{grid-template-columns:1fr;} h1{font-size:2.8rem;letter-spacing:-1.5px;}
                          .site-footer{flex-direction:column;text-align:center;} }
```

The 768px h1 rule applies to content pages; the 600px h1 rule applies to the home page.

## 4. Header (every page)

- Home: `padding:2rem 6%`; flex, `align-items:center`, `justify-content:space-between`; white background; `border-bottom:1px solid var(--border-color)`.
- Content pages: `padding:1.5rem 6%`; same border and background; logo only.
- Logo: `<a href="index.html" class="logo-text">[BRAND BASE]<span class="logo-accent">[BRAND ACCENT]</span></a>`
  - `.logo-text { font-weight:800; font-size:1.5rem; letter-spacing:-0.5px; color:var(--text-dark); text-decoration:none; }`
  - `.logo-accent { color:var(--primary-accent); }`
- Optional right-side action (home only): outlined button — `background:none; border:1px solid var(--border-color); border-radius:8px; padding:0.5rem 1rem; font-size:0.85rem; font-weight:600; color:var(--text-gray);` flex with 6px gap; optional 16×16 SVG (stroke currentColor, width 2).

## 5. Hero (home page)

- `.hero { max-width:1400px; margin:0 auto; padding:5rem 6% 3rem; width:100%; }`
- `.overline` ([TAGLINE]): overline style; flex gap 16px; 40px hairline before it (`::before { content:""; width:40px; height:1px; background:#cbd5e1; }`).
- `h1`: [HERO LINE 1] then `<span class="gray-text">[HERO LINE 2]</span>`.
- `p.subtitle` ([SUBTITLE]): 1.15rem; var(--text-gray); max-width 560px; margin-bottom 3rem.
- `.pills`: flex; gap 1.5rem; wrap; margin-bottom 4rem.
  - `.pill { display:flex; align-items:center; gap:8px; font-size:0.95rem; font-weight:600; color:var(--text-gray); }`
  - `.dot { width:6px; height:6px; background:#10b981; border-radius:50%; }`

## 6. Cards grid (home page)

- `.cards-section { max-width:1400px; margin:0 auto; padding:0 6% 5rem; width:100%; }`
- `.section-label` ([SECTION LABEL]): overline style; margin-bottom 2rem.
- `.cards-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:1.5rem; }`

```css
.card { background:white; border:1px solid var(--border-color); border-radius:16px; padding:2rem;
  text-decoration:none; color:var(--text-dark); display:flex; flex-direction:column; gap:1rem;
  box-shadow:0 4px 12px rgba(0,0,0,0.04); transition:box-shadow 0.2s, transform 0.2s, border-color 0.2s; }
.card:hover { box-shadow:0 12px 32px rgba(0,0,0,0.08); transform:translateY(-2px); border-color:#bfdbfe; }
```

Each card is an `<a href="[CARD N HREF]">`, top to bottom:
- `.card-icon`: 44×44; radius 8px; flex center; 0.75rem/800 mono label ([CARD N ICON]); letter-spacing 0.5px. Cycle through these pastel background/text pairs:
  - #dbeafe / #1d4ed8 (blue)
  - #d1fae5 / #065f46 (green)
  - #fce7f3 / #9d174d (pink)
  - #fef3c7 / #92400e (amber)
  - #ede9fe / #5b21b6 (violet)
  - #fee2e2 / #991b1b (red)
- `.card-tag`: 0.7rem/700; uppercase; letter-spacing 1px; color var(--primary-accent); background #eff6ff; border 1px solid #bfdbfe; padding 0.2rem 0.6rem; radius 4px; width fit-content.
- `.card-title`: 1.25rem/800/-0.5px.
- `.card-desc`: 0.95rem; var(--text-gray); line-height 1.5; flex 1.
- `.card-link`: 0.9rem/700; var(--primary-accent); flex gap 6px (e.g. "Open →").

## 7. Benefits row (home page, optional)

- `.benefits { max-width:1400px; margin:0 auto; padding:3rem 6%; border-top:1px solid var(--border-color); display:flex; gap:3rem; flex-wrap:wrap; }`
- `.benefit-item { display:flex; align-items:center; gap:12px; font-size:1.05rem; font-weight:600; }`
- Check icon: 22×22 SVG, stroke var(--primary-accent), stroke-width 3, `<polyline points="20 6 9 17 4 12"/>`.

## 8. Footer (every page)

- Home `.site-footer`: `padding:3rem 6%; border-top:1px solid var(--border-color); background:white; margin-top:auto; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1rem;`
  - `.footer-links { display:flex; gap:2rem; font-size:0.9rem; font-weight:600; }`; links var(--text-gray), no underline, hover var(--primary-accent).
  - Left: "© [YEAR] [BRAND NAME]" plus nav links. Right: `mailto:[CONTACT EMAIL]` link.
- Content pages: centered — `padding:2rem 6%; border-top:1px solid var(--border-color); text-align:center; color:var(--text-gray); font-size:0.9rem; background:transparent;` "© [YEAR] [BRAND NAME]. All rights reserved."

## 9. Accessibility widget (home and functional pages)

Paste as-is:

```html
<button class="acc-btn" onclick="document.getElementById('acc-menu').style.display=(document.getElementById('acc-menu').style.display==='flex'?'none':'flex')">
  <svg viewBox="0 0 24 24" fill="white" width="24"><path d="M12 2c1.1 0 2 .9 2 2s-.9 2-2 2-2-.9-2-2 .9-2 2-2zm9 7h-6v13h-2v-6h-2v6H9V9H3V7h18v2z"/></svg>
</button>
<div class="acc-menu" id="acc-menu">
  <h4 style="margin-bottom:0.5rem;">Accessibility Options</h4>
  <button class="acc-toggle" onclick="document.body.classList.toggle('acc-high-contrast')">High Contrast Mode</button>
  <button class="acc-toggle" onclick="document.body.classList.toggle('acc-large-text')">Increase Font Size</button>
  <button class="acc-toggle" onclick="document.body.classList.toggle('acc-highlight-links')">Highlight Links</button>
  <button class="acc-toggle" onclick="location.reload()" style="color:red;border-color:red;margin-top:10px;">Reset All</button>
</div>
```

```css
.acc-btn { position:fixed; bottom:30px; left:30px; width:50px; height:50px; border-radius:50%;
  background:var(--primary-accent); display:flex; align-items:center; justify-content:center;
  cursor:pointer; border:none; box-shadow:0 4px 12px rgba(0,0,0,0.15); z-index:1000; }
.acc-menu { position:fixed; bottom:90px; left:30px; width:260px; display:none; flex-direction:column;
  gap:0.8rem; padding:1.5rem; background:white; border:1px solid var(--border-color); border-radius:12px;
  box-shadow:0 10px 30px rgba(0,0,0,0.2); z-index:1000; }
.acc-toggle { width:100%; padding:0.6rem; border:1px solid var(--border-color); border-radius:6px;
  background:white; font-weight:700; text-align:left; cursor:pointer; }
.acc-toggle:hover { border-color:var(--primary-accent); }
body.acc-high-contrast { background:#000; color:#fff; }
body.acc-high-contrast * { background:#000 !important; color:#fff !important; border-color:#fff !important; }
body.acc-large-text * { font-size:120% !important; }
body.acc-highlight-links a { background:yellow !important; color:black !important; padding:2px !important; }
```

## 10. Head boilerplate (every page)

- `<meta charset="UTF-8">` and `<meta name="viewport" content="width=device-width, initial-scale=1.0">`
- `<link rel="icon" type="image/svg+xml" href="[FAVICON HREF]">` and `<link rel="shortcut icon" href="[FAVICON HREF]">`
- `<meta name="theme-color" content="#f9fafb">`
- `<title>[PAGE TITLE]</title>`, `<meta name="description" content="[PAGE DESCRIPTION]">`, `<link rel="canonical" href="[CANONICAL URL]">`, and og:title, og:description, og:type="website".
- GA4, only when [GA4 ID] is provided:
```html
<script async src="https://www.googletagmanager.com/gtag/js?id=[GA4 ID]"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','[GA4 ID]');</script>
```

## Page type A — Functional page (interactive tool)

Same header, footer, accessibility widget, favicon (and GA4 if provided). Between header and footer, a centered workspace:

- `.work-wrap { max-width:760px; margin:3rem auto; padding:0 6%; width:100%; }`
- Title block: h1 (2.2rem, letter-spacing -1px, var(--text-dark)) and a one-line subtitle (1.05rem, var(--text-gray)).
- Drop/input zone:
```css
.dropzone { background:white; border:2px dashed var(--border-color); border-radius:12px; padding:3rem;
  text-align:center; cursor:pointer; color:var(--text-gray); transition:border-color 0.2s, background 0.2s; }
.dropzone:hover, .dropzone.drag { border-color:var(--primary-accent); background:#eff6ff; }
```
- Controls panel:
```css
.panel { background:white; border:1px solid var(--border-color); border-radius:12px; padding:2rem;
  display:flex; flex-direction:column; gap:1.25rem; box-shadow:0 4px 12px rgba(0,0,0,0.04); }
```
  - Inputs/selects: white background; 1px var(--border-color) border; var(--text-dark) text; radius 8px; padding 0.6rem 0.8rem.
  - Labels: 0.85rem/700; var(--text-gray); uppercase optional.
  - Range sliders: `accent-color:var(--primary-accent)`.
- Primary button:
```css
.btn-primary { background:var(--primary-accent); color:#fff; border:none; border-radius:8px;
  padding:0.85rem 1.5rem; font-weight:700; cursor:pointer; transition:background 0.2s; }
.btn-primary:hover { background:var(--primary-hover); }
.btn-primary:disabled { opacity:0.5; cursor:not-allowed; }
```
- Secondary button: white background; var(--text-dark) text; 1px var(--border-color) border; hover border var(--primary-accent).
- Progress bar: track #eff6ff; fill var(--primary-accent); height 8px; radius 999px; percentage text in var(--text-gray).
- Output: result preview inside a `.panel` plus a download `.btn-primary`.

## Page type B — Content page (policy, about, article)

Content-page header (logo only), simple centered footer, favicon (and GA4 if provided). One centered reading card:

```css
main { flex:1; max-width:800px; margin:3rem auto; padding:3rem; background:white; border-radius:12px;
  box-shadow:0 10px 25px rgba(0,0,0,0.05); border:1px solid #f1f5f9; }
h1 { font-size:2.2rem; letter-spacing:-1px; margin-bottom:0.5rem; color:var(--text-dark); }
.effective-date { display:block; color:var(--text-gray); font-size:0.9rem; margin-bottom:2rem;
  padding-bottom:1rem; border-bottom:1px solid var(--border-color); }
h3 { font-size:1.25rem; margin:2rem 0 1rem; color:var(--text-dark); }
p, ul { color:#475569; font-size:1.05rem; margin-bottom:1rem; }
ul { padding-left:1.5rem; } li { margin-bottom:0.5rem; }
a { color:var(--primary-accent); font-weight:500; text-decoration:none; }
a:hover { text-decoration:underline; }
.callout-box { background:#f8fafc; border-left:4px solid #64748b; padding:1.5rem; margin:2rem 0;
  font-weight:600; text-transform:uppercase; font-size:0.9rem; color:#1e293b; }
```

## Class names

Use these names so pages stay consistent: logo-text, logo-accent, card, card-icon, card-tag, card-title, card-desc, card-link, overline, section-label, pill, dot, benefits, benefit-item, site-footer, footer-links, acc-btn, acc-menu, acc-toggle.

## Before delivering

- Every [PLACEHOLDER] is replaced.
- The 3 → 2 → 1 grid and the breakpoints work.
- Only the colors listed above are used.
