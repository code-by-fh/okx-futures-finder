"""Local, read-only scanner dashboard and outage notifications (stdlib only)."""
import csv
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Dashboard:
    def __init__(self, root, port=8765):
        self.root, self.port = root, port
        self.lock = threading.Lock()
        self.data = dict(started=time.time(), last_success=None, last_scan=None,
                         last_error="", errors=0, pending=0, pairs=[], outage=False)
        self.server = None

    def update(self, **values):
        with self.lock:
            self.data.update(values)

    def snapshot(self):
        with self.lock:
            result = dict(self.data)
        result["signals"] = []
        try:
            with (self.root / "signals.csv").open(encoding="utf-8", newline="") as file:
                result["signals"] = list(csv.DictReader(file))[-100:][::-1]
        except (OSError, csv.Error):
            pass
        return result

    def start(self):
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/api/status":
                    body = json.dumps(dashboard.snapshot()).encode("utf-8")
                    content_type = "application/json; charset=utf-8"
                elif path == "/":
                    body = (dashboard.root / "dashboard.html").read_bytes()
                    content_type = "text/html; charset=utf-8"
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

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
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
            self.dashboard.update(last_error=f"Stoerungsmeldung nicht zugestellt: {err}")
            self.next_attempt = now + 60

    def run(self, stop):
        while not stop.wait(5):
            self.check()
