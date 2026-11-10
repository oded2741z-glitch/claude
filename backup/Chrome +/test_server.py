import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8000

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#f9fafb">
<title>Kiosk Test</title>
<style>
:root {{
  --primary-accent: #2563eb;
  --primary-hover: #1d4ed8;
  --bg-light: #f9fafb;
  --text-dark: #111827;
  --text-gray: #64748b;
  --border-color: #e2e8f0;
}}
* {{ margin:0; padding:0; box-sizing:border-box; font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
body {{ background:var(--bg-light); color:var(--text-dark); min-height:100vh; display:flex; flex-direction:column; overflow-x:hidden; }}
header {{ padding:1.5rem 6%; background:#ffffff; border-bottom:1px solid var(--border-color); }}
.logo-text {{ font-weight:800; font-size:1.5rem; letter-spacing:-0.5px; color:var(--text-dark); text-decoration:none; }}
.logo-accent {{ color:var(--primary-accent); }}
.work-wrap {{ flex:1; max-width:760px; margin:3rem auto; padding:0 6%; width:100%; }}
.section-label {{ font-size:0.75rem; font-weight:700; letter-spacing:2px; text-transform:uppercase; color:var(--text-gray); margin-bottom:2rem; }}
.panel {{ background:#ffffff; border:1px solid var(--border-color); border-radius:12px; padding:3rem 2rem; display:flex; flex-direction:column; align-items:center; gap:1.25rem; box-shadow:0 4px 12px rgba(0,0,0,0.04); text-align:center; }}
.label {{ font-size:0.85rem; font-weight:700; text-transform:uppercase; color:var(--text-gray); letter-spacing:2px; }}
.counter {{ font-size:8rem; font-weight:800; letter-spacing:-4px; line-height:1; color:var(--primary-accent); }}
.time {{ font-size:2.2rem; font-weight:700; letter-spacing:-1px; color:var(--text-dark); }}
.site-footer {{ padding:2rem 6%; border-top:1px solid var(--border-color); text-align:center; color:var(--text-gray); font-size:0.9rem; }}
@media (max-width:600px) {{ .counter {{ font-size:5rem; }} }}
</style>
</head>
<body>
<header><span class="logo-text">Kiosk<span class="logo-accent">Test</span></span></header>
<div class="work-wrap">
  <div class="section-label">Refresh test</div>
  <div class="panel">
    <div class="label">Load</div>
    <div class="counter">#{count}</div>
    <div class="label">Loaded at</div>
    <div class="time">{time}</div>
  </div>
</div>
<footer class="site-footer">Each refresh increases the load number by one.</footer>
</body>
</html>
"""

count = 0
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global count
        if self.path != "/":
            self.send_error(404)
            return
        with lock:
            count += 1
            current = count
        body = PAGE.format(count=current, time=datetime.now().strftime("%H:%M:%S")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"{datetime.now().strftime('%H:%M:%S')}  {self.client_address[0]}  {format % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Test server running on http://localhost:{PORT}")
    server.serve_forever()
