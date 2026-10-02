#!/usr/bin/env python3
"""remote-luffy: lihat dan kontrol laptop Hyprland/Wayland dari browser HP.

Butuh: python3, grim, wl-clipboard, python3-pil (opsional, gambar JPEG tajam)
Kontrol butuh akses tulis ke /dev/uinput (lihat README).

  export LUFFY_PASSWORD='password-panjang-dan-kuat'
  python3 server.py --host 0.0.0.0
"""
import argparse, signal, hashlib, hmac, io, json, os, secrets, shutil, subprocess, threading, time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

try:
    from PIL import Image
except ImportError:
    Image = None

HERE = os.path.dirname(os.path.abspath(__file__))
BOUNDARY = b"frame"
MAX_FAILS, LOCKOUT = 5, 60  # 5 salah -> kunci IP 60 detik
DEVNULL = subprocess.DEVNULL

args = None
inj = None  # Injector, atau None jika kontrol tidak tersedia
control_msg = ""
SECRET = secrets.token_bytes(32)  # token sesi hilang saat server restart
fails = {}  # ip -> (jumlah, waktu terakhir)


def make_token():
    return hmac.new(SECRET, b"luffy-session", hashlib.sha256).hexdigest()


class Screen:
    """Monitor yang dipantau + penggerak kursor (hyprctl)."""

    def __init__(self):
        self.cache, self.t = None, 0
        self.use_hypr = True

    def monitor(self):
        if self.cache and time.time() - self.t < 3:
            return self.cache
        try:
            out = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=3).stdout
            mons = json.loads(out)
            if args.output:
                mons = [m for m in mons if m["name"] == args.output] or mons
            m = next((m for m in mons if m.get("focused")), mons[0])
            w, h = m["width"] / m.get("scale", 1), m["height"] / m.get("scale", 1)
            if m.get("transform", 0) % 2:
                w, h = h, w
            self.cache = {"name": m["name"], "x": m["x"], "y": m["y"], "w": w, "h": h}
        except Exception:
            self.cache = None
        self.t = time.time()
        return self.cache

    @staticmethod
    def hypr(*a):
        r = subprocess.run(["hyprctl", *a], capture_output=True, text=True, timeout=3)
        return (r.stdout + r.stderr).strip()

    def cursor_pos(self):
        try:
            x, y = (float(v) for v in self.hypr("cursorpos").split(","))
            return x, y
        except Exception:
            return None

    def move(self, fx, fy):
        """Pindahkan kursor ke posisi relatif layar. Best-effort: tidak pernah melempar error."""
        m = self.monitor()
        if not m:
            return
        fx, fy = min(1.0, max(0.0, fx)), min(1.0, max(0.0, fy))
        x, y = m["x"] + fx * (m["w"] - 1), m["y"] + fy * (m["h"] - 1)
        if self.use_hypr:
            out = self.hypr("dispatch", "movecursor", str(int(x)), str(int(y)))
            if out != "ok":
                self.use_hypr = False
                print("hyprctl movecursor tidak berfungsi (%r); beralih ke gerakan relatif" % out[:120])
        gain = 1.0 if self.use_hypr else 0.5  # diredam: akselerasi libinput membuat gerakan besar overshoot
        for _ in range(8):
            pos = self.cursor_pos()
            if pos is None or not inj:
                return
            dx, dy = x - pos[0], y - pos[1]
            if abs(dx) <= 3 and abs(dy) <= 3:
                return
            inj.rel_move(int(round(dx * gain)), int(round(dy * gain)))
            time.sleep(0.02)


screen = Screen()


class Capturer:
    """Satu thread memotret layar; berhenti sendiri kalau tidak ada penonton."""

    def __init__(self):
        self.cond = threading.Condition()
        self.frame, self.seq, self.viewers = None, 0, 0
        self.fmt = "jpeg" if Image else "png"
        threading.Thread(target=self.loop, daemon=True).start()

    def grab(self):
        cmd = ["grim", "-c"]  # -c: sertakan kursor
        cmd += ["-t", "ppm"] if Image else ["-t", "png", "-l", "1"]
        if args.scale != 1.0:
            cmd += ["-s", str(args.scale)]
        m = screen.monitor()
        if m:
            cmd += ["-o", m["name"]]
        t0 = time.time()
        r = subprocess.run(cmd + ["-"], capture_output=True, timeout=10)
        if r.returncode != 0 or not r.stdout:
            print("grim error (kode %d): %s" % (r.returncode, r.stderr.decode("utf-8", "replace").strip()))
            return None
        data = r.stdout
        if Image:
            buf = io.BytesIO()
            Image.open(io.BytesIO(data)).save(buf, "JPEG", quality=args.quality)
            data = buf.getvalue()
        if args.debug:
            print("frame %d KB, %.2fs" % (len(data) // 1024, time.time() - t0))
        return data

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
                print("capture gagal:", e)
                data = None
                time.sleep(1)
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


def clip_set(text):
    subprocess.run(["wl-copy"], input=text.encode(), stdout=DEVNULL, stderr=DEVNULL, timeout=3)


def clip_get():
    r = subprocess.run(["wl-paste", "-n"], capture_output=True, timeout=3)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def goto(d):
    """Pindah ke koordinat absolut (pecahan layar) jika ada; tanpa x/y = posisi kursor sekarang."""
    if "x" in d:
        screen.move(float(d["x"]), float(d["y"]))


def do_api(path, d):
    """Jalankan aksi kontrol. Melempar exception jika gagal."""
    if path == "/api/move":
        screen.move(float(d["x"]), float(d["y"]))
    elif path == "/api/rel":
        inj.rel_move(max(-3000, min(3000, int(d["dx"]))), max(-3000, min(3000, int(d["dy"]))))
    elif path == "/api/click":
        goto(d)
        inj.click(d.get("button", "left"), 2 if d.get("double") else 1)
    elif path == "/api/down":
        goto(d)
        inj.button("left", True)
    elif path == "/api/up":
        try:
            goto(d)
        finally:
            inj.button("left", False)  # tombol harus selalu dilepas
    elif path == "/api/scroll":
        inj.wheel(int(d.get("wheel", 0)), int(d.get("hwheel", 0)))
    elif path == "/api/key":
        inj.combo(str(d["combo"]))
    elif path == "/api/type":
        text = str(d["text"])[:5000]
        if inj.can_type(text):
            inj.type_text(text)
        else:  # karakter non-US: lewat clipboard
            clip_set(text)
            inj.combo("ctrl+v")
    elif path == "/api/clipset":
        clip_set(str(d["text"])[:100000])
        if d.get("paste"):
            time.sleep(0.05)
            inj.combo(str(d["paste"]))
    else:
        return False
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "luffy"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        if args.debug or "/api/" not in (a[0] if a else ""):
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

    def json(self, code, obj):
        self.send(code, json.dumps(obj).encode(), "application/json")

    def page(self, name):
        with open(os.path.join(HERE, "static", name), "rb") as f:
            return f.read()

    def do_GET(self):
        if self.path == "/login" or self.path.startswith("/login?"):
            return self.send(200, self.page("login.html"))
        if not self.authed():
            return self.send(302, headers=[("Location", "/login")])
        if self.path == "/":
            return self.send(200, self.page("index.html"))
        if self.path == "/stream":
            return self.stream()
        if self.path == "/api/info":
            return self.json(200, {"control": inj is not None, "message": control_msg})
        if self.path == "/api/clip":
            try:
                return self.json(200, {"text": clip_get()})
            except Exception as e:
                return self.json(500, {"error": "wl-paste gagal: %s" % e})
        self.send(404, b"not found", "text/plain")

    def read_body(self):
        length = min(int(self.headers.get("Content-Length", 0)), 200000)
        return self.rfile.read(length)

    def do_POST(self):
        if self.path == "/login":
            return self.login()
        body = self.read_body()
        if not self.path.startswith("/api/"):
            return self.send(404, b"not found", "text/plain")
        if not self.authed():
            return self.json(401, {"error": "belum login"})
        # JSON-only: mencegah form lintas-situs memicu aksi
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.json(403, {"error": "content-type harus application/json"})
        if inj is None:
            return self.json(503, {"error": control_msg})
        try:
            ok = do_api(self.path, json.loads(body or b"{}"))
        except Exception as e:
            return self.json(400, {"error": "%s: %s" % (type(e).__name__, e)})
        self.json(200 if ok else 404, {"ok": ok})

    def login(self):
        ip = self.client_address[0]
        n, last = fails.get(ip, (0, 0))
        form = parse_qs(self.read_body().decode("utf-8", "replace"))
        if n >= MAX_FAILS and time.time() - last < LOCKOUT:
            return self.send(429, b"Terlalu banyak percobaan. Tunggu sebentar.", "text/plain")
        pw = form.get("password", [""])[0]
        if hmac.compare_digest(pw.encode(), args.password.encode()):
            fails.pop(ip, None)
            flags = "; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000" + ("; Secure" if args.secure_cookie else "")
            return self.send(302, headers=[("Location", "/"), ("Set-Cookie", "luffy=%s%s" % (make_token(), flags))])
        fails[ip] = (n + 1 if time.time() - last < LOCKOUT else 1, time.time())
        time.sleep(1)
        self.send(302, headers=[("Location", "/login?err=1")])

    def stream(self):
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=" + BOUNDARY.decode())
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        cap.join(1)
        last = -1
        try:
            while True:
                frame, last = cap.wait_new(last)
                if frame is None:
                    continue
                self.wfile.write(b"--" + BOUNDARY + b"\r\nContent-Type: image/" + cap.fmt.encode()
                                 + b"\r\nContent-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            cap.join(-1)


def main():
    global args, cap, inj, control_msg
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--fps", type=float, default=6)
    p.add_argument("--quality", type=int, default=70, help="kualitas JPEG 1-100 (butuh python3-pil)")
    p.add_argument("--scale", type=float, default=0.75, help="skala gambar (1.0 = tajam penuh)")
    p.add_argument("--output", help="nama monitor (hyprctl monitors); default monitor yang fokus")
    p.add_argument("--view-only", action="store_true", help="matikan kontrol")
    p.add_argument("--debug", action="store_true", help="cetak waktu frame dan semua request")
    p.add_argument("--secure-cookie", action="store_true", help="aktifkan jika diakses lewat HTTPS (cloudflared)")
    args = p.parse_args()
    args.password = os.environ.get("LUFFY_PASSWORD", "")
    if len(args.password) < 12:
        raise SystemExit("Set LUFFY_PASSWORD minimal 12 karakter.")
    if not shutil.which("grim"):
        raise SystemExit("grim belum terpasang (sudo apt install grim).")
    if not Image:
        print("Catatan: python3-pil belum terpasang -> gambar PNG (buram/berat). sudo apt install python3-pil")

    if args.view_only:
        control_msg = "Mode view-only."
    else:
        missing = [t for t in ("hyprctl", "wl-copy", "wl-paste") if not shutil.which(t)]
        if missing:
            control_msg = "Perintah belum ada: %s (sudo apt install wl-clipboard)." % ", ".join(missing)
        else:
            try:
                import inject
                inj = inject.Injector()
            except OSError as e:
                control_msg = ("Tidak bisa membuka /dev/uinput (%s). Jalankan: sudo modprobe uinput && "
                               "sudo setfacl -m u:$USER:rw /dev/uinput" % e)
    print("Kontrol:", "AKTIF" if inj else "NONAKTIF - " + control_msg)

    # Ctrl+C yang disuntikkan dari HP bisa mendarat di terminal ini; keluar butuh 2x Ctrl+C.
    last_int = [0.0]

    def on_int(sig, frm):
        if time.time() - last_int[0] < 1.5:
            raise KeyboardInterrupt
        last_int[0] = time.time()
        print("\nTekan Ctrl+C sekali lagi (dalam 1,5 detik) untuk keluar.")
    signal.signal(signal.SIGINT, on_int)

    cap = Capturer()
    print("Berjalan di http://%s:%d" % (args.host, args.port))
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
