"""Local, read-only scanner dashboard and outage notifications (stdlib only)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Dashboard:
    def __init__(self, root, port=8765, host='127.0.0.1'):
        self.root, self.port = root, port
        self.host = host
        self.lock = threading.Lock()
        self.data = dict(started=time.time(), last_success=None, last_scan=None,
                         last_error="", errors=0, pending=0, pairs=[], outage=False,
                         phase="Startet", processed=0, total=0, error_log=[])
        self.server = None

    def update(self, **values):
        with self.lock:
            self.data.update(values)

    def report_error(self, source, message, symbol=""):
        event = dict(time=time.time(), source=source, message=str(message)[:600], symbol=symbol)
        with self.lock:
            self.data["last_error"] = f"{symbol + ': ' if symbol else ''}{event['message']}"
            self.data["error_log"] = (self.data["error_log"] + [event])[-50:]

    def snapshot(self):
        with self.lock:
            result = dict(self.data)
        result["server_time"] = time.time()
        return result

    def start(self):
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == '/healthz':
                    body = b'{"status":"ok"}'
                    content_type = 'application/json; charset=utf-8'
                elif path == "/api/status":
                    body = json.dumps(dashboard.snapshot()).encode("utf-8")
                    content_type = "application/json; charset=utf-8"
                elif path in ("/", "/dashboard.css", "/dashboard.js"):
                    name = "dashboard.html" if path == "/" else path[1:]
                    try:
                        body = (dashboard.root / name).read_bytes()
                    except OSError:
                        self.send_error(404)
                        return
                    content_type = {"/": "text/html", "/dashboard.css": "text/css", "/dashboard.js": "text/javascript"}[path] + "; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        print(f"Browser-Dashboard: http://127.0.0.1:{self.server.server_port}")

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()


class HealthMonitor:
    def __init__(self, dashboard, outage_seconds, notify):
        self.dashboard = dashboard
        self.threshold = float(outage_seconds)
        self.notify = notify
        self.reported = False
        self.next_attempt = 0

    def check(self, now=None):
        now = time.time() if now is None else now
        with self.dashboard.lock:
            data = dict(self.dashboard.data)
        outage = now - (data["last_success"] or data["started"]) >= self.threshold
        self.dashboard.update(outage=outage)
        if outage == self.reported or now < self.next_attempt:
            return
        try:
            if outage:
                self.notify("OKX Scanner: Stoerung", "Seit mehreren Minuten kein erfolgreicher Scan. "
                            "Scanner und Internet pruefen. " + data["last_error"][:400])
            else:
                self.notify("OKX Scanner: Entwarnung", "Der Scanner erhaelt wieder Marktdaten.")
            self.reported = outage
        except Exception as err:
            self.dashboard.report_error("Störungsmeldung", str(err))
            self.next_attempt = now + 60

    def run(self, stop):
        while not stop.wait(5):
            self.check()
