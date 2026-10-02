"""Input virtual lewat /dev/uinput (tanpa ydotool): klik, scroll, keyboard.

Posisi kursor TIDAK diatur di sini (pakai `hyprctl dispatch movecursor`).
"""
import fcntl, os, struct, threading, time

UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT = 0x40045564, 0x40045565, 0x40045566
UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x405C5503, 0x5501, 0x5502
EV_SYN, EV_KEY, EV_REL = 0, 1, 2
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0, 1, 6, 8
BUTTONS = {"left": 0x110, "right": 0x111, "middle": 0x112}

KEYS = {"esc": 1, "backspace": 14, "tab": 15, "enter": 28, "space": 57, "capslock": 58,
        "home": 102, "up": 103, "pageup": 104, "left": 105, "right": 106, "end": 107,
        "down": 108, "pagedown": 109, "insert": 110, "delete": 111}
for i in range(1, 11):
    KEYS["f%d" % i] = 58 + i
KEYS["f11"], KEYS["f12"] = 87, 88
MODS = {"ctrl": 29, "shift": 42, "alt": 56, "super": 125, "meta": 125}

# karakter -> (kode, perlu_shift), layout US
CHARS = {}
for row, start in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
    for i, c in enumerate(row):
        CHARS[c] = (start + i, False)
        CHARS[c.upper()] = (start + i, True)
for i, c in enumerate("1234567890"):
    CHARS[c] = (2 + i, False)
for i, c in enumerate("!@#$%^&*()"):
    CHARS[c] = (2 + i, True)
for c, k in {"-": 12, "=": 13, "[": 26, "]": 27, ";": 39, "'": 40, "`": 41, "\\": 43,
             ",": 51, ".": 52, "/": 53, " ": 57, "\n": 28, "\t": 15}.items():
    CHARS[c] = (k, False)
for c, k in {"_": 12, "+": 13, "{": 26, "}": 27, ":": 39, '"': 40, "~": 41, "|": 43,
             "<": 51, ">": 52, "?": 53}.items():
    CHARS[c] = (k, True)


class Device:
    def __init__(self, name, keys, rels):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for k in keys:
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, k)
        if rels:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
            for r in rels:
                fcntl.ioctl(self.fd, UI_SET_RELBIT, r)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("HHHH80sI", 3, 0x1234, 0x5678, 1, name.encode(), 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(0.5)  # beri waktu compositor mendeteksi perangkat

    def emit(self, etype, code, value):
        t = time.time()
        os.write(self.fd, struct.pack("llHHi", int(t), int((t % 1) * 1e6), etype, code, value))

    def key(self, code, down):
        self.emit(EV_KEY, code, 1 if down else 0)
        self.emit(EV_SYN, 0, 0)


class Injector:
    def __init__(self):
        self.lock = threading.Lock()
        self.mouse = Device("luffy-mouse", BUTTONS.values(), (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL))
        allkeys = set(KEYS.values()) | set(MODS.values()) | {k for k, _ in CHARS.values()}
        self.kbd = Device("luffy-keyboard", sorted(allkeys), ())

    def button(self, name, down):
        with self.lock:
            self.mouse.key(BUTTONS[name], down)

    def click(self, name="left", count=1):
        for _ in range(count):
            self.button(name, True)
            time.sleep(0.02)
            self.button(name, False)
            time.sleep(0.04)

    def rel_move(self, dx, dy):
        with self.lock:
            self.mouse.emit(EV_REL, REL_X, int(dx))
            self.mouse.emit(EV_REL, REL_Y, int(dy))
            self.mouse.emit(EV_SYN, 0, 0)

    def wheel(self, n=0, h=0):
        with self.lock:
            if n:
                self.mouse.emit(EV_REL, REL_WHEEL, int(n))
            if h:
                self.mouse.emit(EV_REL, REL_HWHEEL, int(h))
            self.mouse.emit(EV_SYN, 0, 0)

    def _tap(self, code, shift=False, mods=()):
        held = [MODS[m] for m in mods]
        if shift and MODS["shift"] not in held:
            held.append(MODS["shift"])
        with self.lock:
            for m in held:
                self.kbd.key(m, True)
            time.sleep(0.008)
            self.kbd.key(code, True)
            time.sleep(0.008)
            self.kbd.key(code, False)
            for m in reversed(held):
                self.kbd.key(m, False)
            time.sleep(0.004)

    def combo(self, spec):
        """'ctrl+shift+v', 'enter', 'a' ..."""
        *mods, last = [p for p in spec.lower().split("+") if p] or [""]
        if spec.endswith("++"):  # tombol '+'
            last = "+"
        for m in mods:
            if m not in MODS:
                raise ValueError("modifier tidak dikenal: " + m)
        if last in KEYS:
            self._tap(KEYS[last], mods=mods)
        elif last in CHARS:
            code, shift = CHARS[last]
            self._tap(code, shift, mods)
        else:
            raise ValueError("tombol tidak dikenal: " + last)

    @staticmethod
    def can_type(text):
        return all(c in CHARS for c in text)

    def type_text(self, text):
        for c in text:
            code, shift = CHARS[c]
            self._tap(code, shift)
