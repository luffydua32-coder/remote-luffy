#!/usr/bin/env python3
"""remote-luffy tahap 1: lihat layar Wayland/Hyprland dari browser HP (view-only).

Hanya butuh Python 3 (stdlib) dan `grim`.

  export LUFFY_PASSWORD='password-panjang-dan-kuat'
  python3 server.py                 # hanya localhost (cocok untuk cloudflared)
  python3 server.py --host 0.0.0.0  # satu Wi-Fi (wajib lewat jaringan tepercaya)
"""
import argparse, hashlib, hmac, os, secrets, shutil, subprocess, threading, time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
BOUNDARY = b"frame"
MAX_FAILS, LOCKOUT = 5, 60  # 5 salah -> kunci IP 60 detik

args = None
SECRET = secrets.token_bytes(32)  # token sesi hilang saat server restart
fails = {}  # ip -> (jumlah, waktu terakhir)


def make_token():
    return hmac.new(SECRET, b"luffy-session", hashlib.sha256).hexdigest()


class Capturer:
    """Satu thread memotret layar; berhenti sendiri kalau tidak ada penonton."""

    def __init__(self):
        self.cond = threading.Condition()
        self.frame, self.seq, self.viewers = None, 0, 0
        threading.Thread(target=self.loop, daemon=True).start()

    def grab(self):
        cmd = ["grim", "-t", "jpeg", "-q", str(args.quality)]
        if args.scale != 1.0:
            cmd += ["-s", str(args.scale)]
        if args.output:
            cmd += ["-o", args.output]
        r = subprocess.run(cmd + ["-"], capture_output=True, timeout=10)
        return r.stdout if r.returncode == 0 and r.stdout else None

    def loop(self):
        interval = 1.0 / args.fps
        while True:
            with self.cond:
                while self.viewers == 0:
                    self.cond.wait()
            t0 = time.time()
            try:
                data = self.grab()
            except Exception as e:
                print("grim gagal:", e)
                data = None
            if data:
                with self.cond:
                    self.frame, self.seq = data, self.seq + 1
                    self.cond.notify_all()
            time.sleep(max(0.0, interval - (time.time() - t0)))

    def join(self, delta):
        with self.cond:
            self.viewers += delta
            self.cond.notify_all()

    def wait_new(self, last):
        with self.cond:
            self.cond.wait_for(lambda: self.seq != last, timeout=5)
            return self.frame, self.seq


class Handler(BaseHTTPRequestHandler):
    server_version = "luffy"

    def log_message(self, fmt, *a):
        print("%s %s" % (self.client_address[0], fmt % a))

    def authed(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        m = c.get("luffy")
        return bool(m) and hmac.compare_digest(m.value, make_token())

    def send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def page(self, name):
        with open(os.path.join(HERE, "static", name), "rb") as f:
            return f.read()

    def do_GET(self):
        if self.path == "/login":
            return self.send(200, self.page("login.html"))
        if not self.authed():
            return self.send(302, headers=[("Location", "/login")])
        if self.path == "/":
            return self.send(200, self.page("index.html"))
        if self.path == "/stream":
            return self.stream()
        self.send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/login":
            return self.send(404, b"not found", "text/plain")
        ip = self.client_address[0]
        n, last = fails.get(ip, (0, 0))
        if n >= MAX_FAILS and time.time() - last < LOCKOUT:
            return self.send(429, b"Terlalu banyak percobaan. Tunggu sebentar.", "text/plain")
        length = min(int(self.headers.get("Content-Length", 0)), 4096)
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        pw = form.get("password", [""])[0]
        if hmac.compare_digest(pw.encode(), args.password.encode()):
            fails.pop(ip, None)
            flags = "; HttpOnly; SameSite=Strict; Path=/" + ("; Secure" if args.secure_cookie else "")
            return self.send(302, headers=[("Location", "/"), ("Set-Cookie", "luffy=%s%s" % (make_token(), flags))])
        fails[ip] = (n + 1 if time.time() - last < LOCKOUT else 1, time.time())
        time.sleep(1)
        self.send(302, headers=[("Location", "/login?err=1")])

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=" + BOUNDARY.decode())
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        cap.join(1)
        last = -1
        try:
            while True:
                frame, last = cap.wait_new(last)
                if frame is None:
                    continue
                self.wfile.write(b"--" + BOUNDARY + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            cap.join(-1)


def main():
    global args, cap
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--fps", type=float, default=8)
    p.add_argument("--quality", type=int, default=60, help="kualitas JPEG 1-100")
    p.add_argument("--scale", type=float, default=0.5, help="skala gambar (0.5 = setengah)")
    p.add_argument("--output", help="nama monitor (hyprctl monitors), default semua")
    p.add_argument("--secure-cookie", action="store_true", help="aktifkan jika diakses lewat HTTPS (cloudflared)")
    args = p.parse_args()
    args.password = os.environ.get("LUFFY_PASSWORD", "")
    if len(args.password) < 12:
        raise SystemExit("Set LUFFY_PASSWORD minimal 12 karakter.")
    if not shutil.which("grim"):
        raise SystemExit("grim belum terpasang (sudo apt install grim).")
    cap = Capturer()
    print("Berjalan di http://%s:%d" % (args.host, args.port))
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
