# -*- coding: utf-8 -*-
"""Companero movil: ver la letra del PC en el telefono (misma red Wi-Fi).

Servidor HTTP minimo (stdlib) en el puerto 5119: sirve una pagina oscura que
consulta /state cada 700 ms y muestra titulo, linea actual, siguiente y
traduccion. Sin dependencias, sin internet: todo en la LAN.
"""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 5119

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lyrio</title><style>
body{background:#0d1117;color:#e9e7e2;font-family:'Segoe UI',system-ui,sans-serif;
margin:0;min-height:100vh;display:flex;flex-direction:column;justify-content:center;
align-items:center;text-align:center;padding:24px;box-sizing:border-box}
#head{color:#8b939b;font-size:15px;margin-bottom:34px;font-weight:600}
#prev,#next{color:#5b6167;font-size:19px;margin:14px 0;min-height:24px}
#cur{color:#1db954;font-size:32px;font-weight:800;margin:10px 0;line-height:1.25}
#extra{color:#9fd6b4;font-size:18px;min-height:22px;margin-bottom:8px}
#brand{position:fixed;bottom:12px;color:#3a4046;font-size:12px}
</style></head><body>
<div id="head">Lyrio</div><div id="prev"></div><div id="cur">...</div>
<div id="extra"></div><div id="next"></div>
<div id="brand">Lyrio &middot; by EazyHood</div>
<script>
async function tick(){try{
 const r=await fetch('/state');const s=await r.json();
 head.textContent=(s.artist?s.artist+' — ':'')+(s.title||'Lyrio');
 prev.textContent=s.prev||'';cur.textContent=s.current||'...';
 extra.textContent=s.extra||'';next.textContent=s.next||'';
}catch(e){}}
setInterval(tick,700);tick();
</script></body></html>"""


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class PhoneServer:
    def __init__(self, state_getter):
        self._get = state_getter
        self._httpd = None

    @property
    def running(self):
        return self._httpd is not None

    @property
    def url(self):
        return f"http://{lan_ip()}:{PORT}"

    def start(self):
        if self._httpd:
            return True
        get = self._get

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/state"):
                    body = json.dumps(get()).encode("utf-8")
                    ctype = "application/json"
                else:
                    body = _PAGE.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        try:
            self._httpd = ThreadingHTTPServer(("0.0.0.0", PORT), H)
        except OSError:
            self._httpd = None
            return False
        threading.Thread(target=self._httpd.serve_forever, daemon=True,
                         name="phone-server").start()
        return True

    def stop(self):
        if self._httpd:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None


def make_qr_png(url, path):
    """PNG del QR para abrir la pagina en el telefono (segno, sin internet)."""
    try:
        import segno
        segno.make(url).save(path, scale=7, border=2, dark="#1db954",
                             light="#0d1117")
        return True
    except Exception:
        return False
