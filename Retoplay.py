"""
Retro OLED Video Player  (v2)
-----------------------------
Plays any video like a tiny old screen (OLED / CRT / VHS camcorder) with a cheap speaker.

Run:      python retro_oled.py
Build:    see README / build.bat

Player window controls:
    Space / left click : pause / resume
    T                  : cycle tint
    Esc                : close
"""
import json
import os
import subprocess
import sys
import time

import numpy as np

# ----------------------------------------------------------------------------
# Theme (launcher)
# ----------------------------------------------------------------------------
NAVY = "#030164"
INDIGO = "#363199"
TEAL = "#2D7495"
YELLOW = "#E8E085"
UI_FONT = "Consolas"

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
TINTS = {
    "Amber": (255, 176, 0),
    "Green": (60, 255, 100),
    "Blue-white": (175, 215, 255),
    "White": (235, 235, 235),
}
SCALES = {64: 10, 128: 5, 256: 3}       # real pixels per OLED pixel
GHOSTING = 0.28                          # how much of the previous frame lingers
FLICKER = 0.03

DEFAULTS = dict(
    tint="Amber", res=128, vbits=4, dither="Bayer", fps=15,
    bright=0, contrast=100, invert=False,
    abits=6, arate=8000, a_band=True, a_dist=True, a_hiss=True, a_mute=False,
    grid=True, crt=False, bloom=False, ghost=True, flicker=True,
    noise=False, tracking=False, rolling=False, rec=False,
)

PRESETS = {
    "2005 Phone OLED": dict(res=128, vbits=4, grid=True, crt=False, bloom=False, ghost=True,
                            flicker=True, noise=False, tracking=False, rolling=False, rec=False),
    "CRT Monitor": dict(res=256, vbits=6, grid=False, crt=True, bloom=True, ghost=True,
                        flicker=True, noise=False, tracking=False, rolling=False, rec=False),
    "VHS Camcorder": dict(res=128, vbits=5, grid=False, crt=True, bloom=True, ghost=True,
                          flicker=True, noise=True, tracking=True, rolling=True, rec=True),
    "Security Cam": dict(res=64, vbits=3, grid=False, crt=True, bloom=False, ghost=True,
                         flicker=True, noise=True, tracking=False, rolling=True, rec=True),
}

VBITS_LABELS = {1: "1-bit (2 shades)", 2: "2-bit (4 shades)", 3: "3-bit (8 shades)",
                4: "4-bit (16 shades)", 5: "5-bit (32 shades)", 6: "6-bit (64 shades)",
                8: "8-bit (256 shades)"}
ABITS = [4, 6, 8, 12, 16]
ARATES = [4000, 6000, 8000, 11025, 16000, 22050]
FPS_CHOICES = [10, 15, 24, 30]
DITHERS = ["Bayer", "None"]

EFFECTS_UI = [
    ("grid", "Pixel grid"),
    ("crt", "CRT screen (curve, scanlines)"),
    ("bloom", "Glow / bloom"),
    ("ghost", "Phosphor ghosting"),
    ("flicker", "Brightness flicker"),
    ("noise", "Tape noise / grain"),
    ("tracking", "VHS tracking glitches"),
    ("rolling", "Rolling interference bar"),
    ("rec", "Camcorder REC overlay"),
]

VIDEO_TYPES = [("Video files", "*.mp4 *.mkv *.avi *.mov *.webm *.wmv *.flv *.m4v"),
               ("All files", "*.*")]


def resource_path(name):
    """Find a bundled/side-by-side file (works from source and from the exe)."""
    bases = []
    if getattr(sys, "frozen", False):
        bases.append(getattr(sys, "_MEIPASS", ""))
        bases.append(os.path.dirname(sys.executable))
    bases.append(os.path.dirname(os.path.abspath(__file__)))
    for b in bases:
        if b:
            p = os.path.join(b, name)
            if os.path.isfile(p):
                return p
    return None


# ----------------------------------------------------------------------------
# Processing
# ----------------------------------------------------------------------------
def bayer_matrix(n):
    if n == 1:
        return np.zeros((1, 1), dtype=np.float32)
    m = bayer_matrix(n // 2)
    return np.block([[4 * m + 0, 4 * m + 2],
                     [4 * m + 3, 4 * m + 1]])


def threshold_map(res):
    b = (bayer_matrix(8) + 0.5) / 64.0
    reps = res // 8
    return np.tile(b, (reps, reps)).astype(np.float32)


def get_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def load_video(path, cfg, progress=None):
    """Return uint8 array (frames, res, res) of brightness levels 0..2^bits-1."""
    import cv2

    res = cfg["res"]
    levels = 2 ** cfg["vbits"]
    fps = cfg["fps"]
    contrast = cfg["contrast"] / 100.0
    bright = cfg["bright"] / 100.0

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("Could not open the video file.")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if src_fps <= 1 or src_fps > 240:
        src_fps = 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0

    thresh = threshold_map(res)
    frames = []
    i = 0
    while True:
        if not cap.grab():
            break
        if i / src_fps >= len(frames) / fps - 1e-9:
            ok, f = cap.retrieve()
            if ok:
                h, w = f.shape[:2]
                s = res / max(h, w)                         # keep proportions
                nw, nh = max(1, round(w * s)), max(1, round(h * s))
                small = cv2.resize(f, (nw, nh), interpolation=cv2.INTER_AREA)
                g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
                g = np.clip((g - 0.5) * contrast + 0.5 + bright, 0, 1)
                if cfg["invert"]:
                    g = 1.0 - g
                canvas = np.zeros((res, res), dtype=np.float32)
                y0, x0 = (res - nh) // 2, (res - nw) // 2
                canvas[y0:y0 + nh, x0:x0 + nw] = g
                q = canvas * (levels - 1)
                lv = np.floor(q + thresh) if cfg["dither"] == "Bayer" else np.round(q)
                frames.append(np.clip(lv, 0, levels - 1).astype(np.uint8))
        i += 1
        if progress and total and i % 30 == 0:
            progress(min(1.0, i / total) * 0.9)
    cap.release()
    if not frames:
        raise RuntimeError("No frames could be read from the video.")
    return np.stack(frames)


def load_audio(path, cfg):
    """int16 mono array at cfg['arate'], crushed. None if the video has no audio."""
    rate, bits = cfg["arate"], cfg["abits"]
    cmd = [get_ffmpeg(), "-v", "error", "-i", path, "-vn", "-ac", "1"]
    if cfg["a_band"]:
        hi = min(3400, int(rate * 0.45))
        cmd += ["-af", f"highpass=f=300,lowpass=f={hi}"]
    cmd += ["-ar", str(rate), "-f", "s16le", "-"]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000          # no console window
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, **kwargs).stdout
    except Exception:
        return None
    if len(out) < 2000:
        return None
    x = np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0
    peak = np.max(np.abs(x))
    if peak > 0:
        x = x / peak * 0.9
    if cfg["a_dist"]:
        x = np.clip(x * 1.6, -1, 1)
    steps = 2 ** (bits - 1) - 1                      # bit crush
    x = np.round(np.clip(x, -1, 1) * steps) / steps
    if cfg["a_hiss"]:
        x = np.clip(x + np.random.normal(0, 0.012, x.shape).astype(np.float32), -1, 1)
    return (x * 32767 * 0.8).astype(np.int16)


# ----------------------------------------------------------------------------
# Screen rendering (grid, bloom, CRT) - all done on one brightness channel
# ----------------------------------------------------------------------------
class Screen:
    def __init__(self, cfg):
        self.res = cfg["res"]
        self.scale = SCALES[self.res]
        self.n = self.res * self.scale
        n, scale = self.n, self.scale
        self.bloom = cfg["bloom"]
        self.grid = None
        self.mask = None
        self.curve = False

        if cfg["grid"]:
            g = np.ones((n, n), dtype=np.float32)
            for k in range(max(1, scale // 5)):
                g[k::scale, :] = 0
                g[:, k::scale] = 0
            self.grid = g

        if cfg["crt"]:
            c = (np.arange(n) + 0.5) / n * 2 - 1
            u, v = np.meshgrid(c, c)
            r2 = u * u + v * v
            f = (1 + 0.12 * r2) / 1.12                 # barrel curvature
            sx, sy = u * f, v * f
            valid = ((np.abs(sx) <= 1) & (np.abs(sy) <= 1)).astype(np.float32)
            self.iy = np.clip(((sy + 1) / 2 * n).astype(np.int32), 0, n - 1)
            self.ix = np.clip(((sx + 1) / 2 * n).astype(np.int32), 0, n - 1)
            phase = ((np.arange(n) % scale) + 0.5) / scale
            scan = (0.62 + 0.38 * np.sin(np.pi * phase)).astype(np.float32)
            vig = np.clip(1 - 0.30 * r2, 0, 1).astype(np.float32)
            self.mask = scan[:, None] * vig * valid
            self.curve = True

    def render(self, shown):
        """shown: float32 (res,res) 0..1  ->  float32 (n,n) brightness."""
        s = self.scale
        big = np.repeat(np.repeat(shown, s, axis=0), s, axis=1)
        if self.grid is not None:
            big = big * self.grid
        if self.bloom:
            import cv2
            small = cv2.resize(big, (self.n // 4, self.n // 4), interpolation=cv2.INTER_AREA)
            small = cv2.GaussianBlur(small, (0, 0), 2.5)
            glow = cv2.resize(small, (self.n, self.n), interpolation=cv2.INTER_LINEAR)
            big = big + 0.55 * glow
        if self.curve:
            big = big[self.iy, self.ix] * self.mask
        return big

    @staticmethod
    def colorize(big, tint):
        rgb = np.clip(big, 0, 1)[:, :, None] * np.array(tint, dtype=np.float32)
        return np.ascontiguousarray(rgb.astype(np.uint8))


def low_res_effects(base, t, cfg, rng):
    """Old-recording-device effects, applied on the small (res x res) image."""
    res = base.shape[0]
    shown = base.copy()
    if cfg["flicker"]:
        shown *= 1.0 - FLICKER * rng.random()
    if cfg["rolling"]:                                   # slow bright band scrolling down
        pos = ((t * 0.18) % 1.3) - 0.15
        ys = np.arange(res, dtype=np.float32) / res
        bar = np.exp(-((ys - pos) / 0.08) ** 2).astype(np.float32)
        shown *= 1.0 + 0.35 * bar[:, None]
    if cfg["tracking"]:
        if rng.random() < 0.15:                          # shifted horizontal band
            y = int(rng.integers(0, res))
            h = int(rng.integers(max(2, res // 64), max(3, res // 12)))
            sh = int(rng.integers(-(res // 6), res // 6))
            shown[y:y + h] = np.roll(shown[y:y + h], sh, axis=1)
        if rng.random() < 0.08:                          # tape dropout streak
            y = int(rng.integers(0, res))
            x = int(rng.integers(0, res // 2))
            ln = int(rng.integers(res // 8, res // 2))
            shown[y, x:x + ln] = 1.0
        b = max(2, res // 24)                            # head-switching noise at the bottom
        shown[-b:] = np.roll(shown[-b:], int(rng.integers(-(res // 10), res // 10)), axis=1)
    if cfg["noise"]:
        shown += rng.standard_normal(shown.shape, dtype=np.float32) * 0.07
    return shown


def draw_overlay(pygame, surface, font, col, n, t, fps, crt):
    """Camcorder-style on-screen display: REC dot, battery, timecode, date."""
    pad = n // 12 if crt else n // 40
    fs = font.get_height()
    if int(t * 2) % 2 == 0:
        pygame.draw.circle(surface, col, (pad + fs // 3, pad + fs // 3), max(3, fs // 3))
    surface.blit(font.render("REC", True, col), (pad + fs, pad))
    bw, bh = int(fs * 1.5), int(fs * 0.7)
    bx, by = n - pad - bw - 4, pad + 2
    pygame.draw.rect(surface, col, (bx, by, bw, bh), 2)
    pygame.draw.rect(surface, col, (bx + bw, by + bh // 4, 3, bh // 2))
    for k in range(3):
        pygame.draw.rect(surface, col, (bx + 4 + k * (bw - 6) // 3, by + 4,
                                        (bw - 6) // 3 - 2, bh - 8))
    m, s = int(t) // 60, int(t) % 60
    f = int((t - int(t)) * fps)
    tc = font.render(f"{m:02d}:{s:02d}:{f:02d}", True, col)
    surface.blit(tc, (pad, n - pad - fs))
    date = font.render(time.strftime("%b %d").upper() + " 2005", True, col)
    surface.blit(date, (n - pad - date.get_width(), n - pad - fs))


# ----------------------------------------------------------------------------
# Player (pygame window)
# ----------------------------------------------------------------------------
def run_player(path, cfg):
    import pygame

    res, fps = cfg["res"], cfg["fps"]
    levels = 2 ** cfg["vbits"]
    pygame.init()
    scr = Screen(cfg)
    n = scr.n
    try:
        ico = resource_path("logo.ico")
        if ico:
            pygame.display.set_icon(pygame.image.load(ico))
    except Exception:
        pass
    screen = pygame.display.set_mode((n, n))
    pygame.display.set_caption("Retro OLED - " + os.path.basename(path))
    font = pygame.font.Font(None, max(24, n // 22))
    clock = pygame.time.Clock()
    tint_names = list(TINTS)
    tint_i = tint_names.index(cfg["tint"]) if cfg["tint"] in tint_names else 0

    def splash(text, frac=None):
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit()
                sys.exit(0)
        col = TINTS[tint_names[tint_i]]
        screen.fill((0, 0, 0))
        img = font.render(text, True, col)
        screen.blit(img, img.get_rect(center=(n // 2, n // 2 - 20)))
        if frac is not None:
            pygame.draw.rect(screen, col, (n // 4, n // 2 + 10, n // 2, 14), 1)
            pygame.draw.rect(screen, col, (n // 4 + 2, n // 2 + 12, int((n // 2 - 4) * frac), 10))
        pygame.display.flip()

    splash("LOADING...", 0.0)
    try:
        frames = load_video(path, cfg, lambda f: splash("LOADING...", f))
        audio = None
        if not cfg["a_mute"]:
            splash("LOADING AUDIO...", 0.93)
            audio = load_audio(path, cfg)
    except Exception as exc:
        splash("ERROR: " + str(exc)[:40])
        time.sleep(3)
        pygame.quit()
        return

    # --- audio setup (allowedchanges=0 keeps the real sample rate; SDL converts) ---
    sound = None
    if audio is not None:
        try:
            rate = cfg["arate"]
            pygame.mixer.init(frequency=rate, size=-16, channels=1, allowedchanges=0)
            freq, _, chans = pygame.mixer.get_init()
            data = audio
            if freq != rate:                           # fallback: hold-resample
                idx = (np.arange(int(len(audio) * freq / rate)) * rate / freq).astype(int)
                data = audio[np.clip(idx, 0, len(audio) - 1)]
            if chans == 2:
                data = np.repeat(data, 2)
            sound = pygame.mixer.Sound(buffer=data.tobytes())
        except Exception:
            sound = None

    count = len(frames)
    rng = np.random.default_rng()
    prev = frames[0].astype(np.float32) / (levels - 1)
    last_big = None
    last_idx = -1
    dirty = True
    paused = False
    pause_at = 0.0
    t0 = time.perf_counter()
    if sound:
        sound.play()

    def now():
        return (pause_at if paused else time.perf_counter()) - t0

    def toggle_pause():
        nonlocal paused, pause_at, t0, dirty
        if paused:
            t0 += time.perf_counter() - pause_at
            paused = False
            if sound:
                pygame.mixer.unpause()
        else:
            paused = True
            pause_at = time.perf_counter()
            if sound:
                pygame.mixer.pause()
        dirty = True

    running = True
    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    running = False
                elif e.key == pygame.K_SPACE:
                    toggle_pause()
                elif e.key == pygame.K_t:
                    tint_i = (tint_i + 1) % len(tint_names)
                    dirty = True
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                toggle_pause()

        t = now()
        idx = int(t * fps)
        if idx >= count:                               # loop
            t0 = time.perf_counter()
            t, idx, last_idx = 0.0, 0, -1
            if sound:
                sound.stop()
                sound.play()

        if idx != last_idx:
            cur = frames[idx].astype(np.float32) / (levels - 1)
            if cfg["ghost"]:
                cur = cur * (1 - GHOSTING) + prev * GHOSTING
            prev = cur
            last_big = scr.render(low_res_effects(cur, t, cfg, rng))
            last_idx = idx
            dirty = True

        if dirty and last_big is not None:
            col = TINTS[tint_names[tint_i]]
            rgb = Screen.colorize(last_big, col)
            screen.blit(pygame.image.frombuffer(rgb.tobytes(), (n, n), "RGB"), (0, 0))
            if cfg["rec"]:
                draw_overlay(pygame, screen, font, col, n, t, fps, cfg["crt"])
            if paused:
                p = n // 12 if cfg["crt"] else n // 40
                pygame.draw.rect(screen, col, (p, p + font.get_height() + 6, 8, 24))
                pygame.draw.rect(screen, col, (p + 14, p + font.get_height() + 6, 8, 24))
            pygame.display.flip()
            dirty = False
        clock.tick(60)

    pygame.quit()


# ----------------------------------------------------------------------------
# Launcher (tkinter window)
# ----------------------------------------------------------------------------
def run_launcher():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Retro OLED Player")
    root.configure(bg=NAVY)
    root.resizable(False, False)
    ico = resource_path("logo.ico")
    if ico:
        try:
            root.iconbitmap(ico)
        except Exception:
            pass

    F = (UI_FONT, 10)
    FB = (UI_FONT, 10, "bold")
    root.option_add("*Font", F)
    root.option_add("*TCombobox*Listbox.background", INDIGO)
    root.option_add("*TCombobox*Listbox.foreground", YELLOW)
    root.option_add("*TCombobox*Listbox.selectBackground", TEAL)
    root.option_add("*TCombobox*Listbox.selectForeground", YELLOW)

    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=NAVY, foreground=YELLOW, font=F, bordercolor=TEAL,
                 lightcolor=NAVY, darkcolor=NAVY, troughcolor=INDIGO, focuscolor=NAVY)
    st.configure("TLabel", background=NAVY, foreground=YELLOW)
    st.configure("Title.TLabel", font=(UI_FONT, 20, "bold"))
    st.configure("TFrame", background=NAVY)
    st.configure("TLabelframe", background=NAVY, bordercolor=TEAL)
    st.configure("TLabelframe.Label", background=NAVY, foreground=YELLOW, font=FB)
    st.configure("TEntry", fieldbackground=INDIGO, foreground=YELLOW, insertcolor=YELLOW,
                 bordercolor=TEAL)
    st.configure("TCombobox", fieldbackground=INDIGO, background=INDIGO, foreground=YELLOW,
                 arrowcolor=YELLOW, bordercolor=TEAL, selectbackground=INDIGO,
                 selectforeground=YELLOW)
    st.map("TCombobox", fieldbackground=[("readonly", INDIGO)],
           foreground=[("readonly", YELLOW)], background=[("active", TEAL)])
    st.configure("TButton", background=INDIGO, foreground=YELLOW, bordercolor=TEAL,
                 padding=6, font=FB)
    st.map("TButton", background=[("active", TEAL), ("pressed", TEAL)])
    st.configure("Play.TButton", background=YELLOW, foreground=NAVY, font=(UI_FONT, 13, "bold"),
                 padding=8)
    st.map("Play.TButton", background=[("active", "#F4EFA8"), ("pressed", "#F4EFA8")],
           foreground=[("active", NAVY)])
    st.configure("TCheckbutton", background=NAVY, foreground=YELLOW)
    st.configure("TCheckbutton", indicatorbackground=INDIGO, indicatorforeground=NAVY,
                 indicatorcolor=INDIGO)
    st.map("TCheckbutton", background=[("active", NAVY)],
           indicatorbackground=[("selected", YELLOW), ("!selected", INDIGO)],
           indicatorcolor=[("selected", YELLOW), ("!selected", INDIGO)],
           foreground=[("active", YELLOW)])

    outer = ttk.Frame(root, padding=14)
    outer.grid()

    # variables ---------------------------------------------------------------
    d = DEFAULTS
    path_v = tk.StringVar()
    preset_v = tk.StringVar(value="2005 Phone OLED")
    tint_v = tk.StringVar(value=d["tint"])
    res_v = tk.StringVar(value=f"{d['res']} x {d['res']}")
    vbits_v = tk.StringVar(value=VBITS_LABELS[d["vbits"]])
    dither_v = tk.StringVar(value=d["dither"])
    fps_v = tk.StringVar(value=f"{d['fps']} fps")
    bright_v = tk.IntVar(value=d["bright"])
    contrast_v = tk.IntVar(value=d["contrast"])
    invert_v = tk.BooleanVar(value=d["invert"])
    abits_v = tk.StringVar(value=f"{d['abits']}-bit")
    arate_v = tk.StringVar(value=f"{d['arate']} Hz")
    a_flags = {k: tk.BooleanVar(value=d[k]) for k in ("a_band", "a_dist", "a_hiss", "a_mute")}
    fx = {k: tk.BooleanVar(value=d[k]) for k, _ in EFFECTS_UI}
    proc = {"p": None}

    # header -------------------------------------------------------------------
    ttk.Label(outer, text="RETRO OLED PLAYER", style="Title.TLabel").grid(
        row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(outer, text="Turn any video into a tiny old screen with a tiny old speaker.").grid(
        row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))
    tk.Frame(outer, bg=TEAL, height=2).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 10))

    # file row -----------------------------------------------------------------
    filerow = ttk.Frame(outer)
    filerow.grid(row=3, column=0, columnspan=3, sticky="ew")
    ttk.Label(filerow, text="Video:").grid(row=0, column=0, padx=(0, 8))
    ttk.Entry(filerow, textvariable=path_v, width=60).grid(row=0, column=1, sticky="ew")

    def browse():
        p = filedialog.askopenfilename(title="Choose a video", filetypes=VIDEO_TYPES)
        if p:
            path_v.set(p)

    ttk.Button(filerow, text="Browse...", command=browse).grid(row=0, column=2, padx=(8, 0))

    # preset row ---------------------------------------------------------------
    prow = ttk.Frame(outer)
    prow.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 8))
    ttk.Label(prow, text="Preset:").grid(row=0, column=0, padx=(0, 8))
    pc = ttk.Combobox(prow, textvariable=preset_v, values=list(PRESETS), state="readonly", width=20)
    pc.grid(row=0, column=1)
    ttk.Label(prow, text="  fills in the settings below, tweak anything after").grid(row=0, column=2)

    def apply_preset(_=None):
        p = PRESETS[preset_v.get()]
        res_v.set(f"{p['res']} x {p['res']}")
        vbits_v.set(VBITS_LABELS[p["vbits"]])
        for k, _label in EFFECTS_UI:
            fx[k].set(p[k])

    pc.bind("<<ComboboxSelected>>", apply_preset)

    # panels -------------------------------------------------------------------
    def combo(parent, r, label, var, values, width=18):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(parent, textvariable=var, values=values, state="readonly",
                     width=width).grid(row=r + 1, column=0, sticky="w")

    def slider(parent, r, label, var, lo, hi):
        text = tk.StringVar()
        text.set(f"{label}: {var.get()}")
        var.trace_add("write", lambda *a: text.set(f"{label}: {var.get()}"))
        ttk.Label(parent, textvariable=text).grid(row=r, column=0, sticky="w", pady=(6, 0))
        tk.Scale(parent, from_=lo, to=hi, orient="horizontal", variable=var, length=190,
                 showvalue=False, bg=YELLOW, troughcolor=INDIGO, activebackground="#F4EFA8",
                 highlightthickness=0, bd=0, sliderrelief="flat", sliderlength=16,
                 width=14).grid(row=r + 1, column=0, sticky="w", pady=(2, 0))

    vid = ttk.LabelFrame(outer, text=" VIDEO ", padding=10)
    vid.grid(row=5, column=0, sticky="nsew", padx=(0, 8))
    combo(vid, 0, "Screen tint", tint_v, list(TINTS))
    combo(vid, 2, "Resolution", res_v, [f"{r} x {r}" for r in SCALES])
    combo(vid, 4, "Bit depth", vbits_v, list(VBITS_LABELS.values()))
    combo(vid, 6, "Dithering", dither_v, DITHERS)
    combo(vid, 8, "Frame rate", fps_v, [f"{f} fps" for f in FPS_CHOICES])
    slider(vid, 10, "Brightness", bright_v, -50, 50)
    slider(vid, 12, "Contrast %", contrast_v, 50, 200)
    ttk.Checkbutton(vid, text="Invert (negative)", variable=invert_v).grid(
        row=14, column=0, sticky="w", pady=(6, 0))

    aud = ttk.LabelFrame(outer, text=" AUDIO ", padding=10)
    aud.grid(row=5, column=1, sticky="nsew", padx=(0, 8))
    combo(aud, 0, "Bit depth", abits_v, [f"{b}-bit" for b in ABITS])
    combo(aud, 2, "Sample rate", arate_v, [f"{r} Hz" for r in ARATES])
    for r, (k, lab) in enumerate([("a_band", "Tiny speaker filter"),
                                  ("a_dist", "Speaker distortion"),
                                  ("a_hiss", "Tape / line hiss"),
                                  ("a_mute", "Mute")]):
        ttk.Checkbutton(aud, text=lab, variable=a_flags[k]).grid(
            row=4 + r, column=0, sticky="w", pady=(8 if r == 0 else 2, 0))

    efx = ttk.LabelFrame(outer, text=" EFFECTS ", padding=10)
    efx.grid(row=5, column=2, sticky="nsew")
    for r, (k, lab) in enumerate(EFFECTS_UI):
        ttk.Checkbutton(efx, text=lab, variable=fx[k]).grid(row=r, column=0, sticky="w", pady=2)

    # play ---------------------------------------------------------------------
    def collect():
        cfg = dict(
            tint=tint_v.get(),
            res=int(res_v.get().split()[0]),
            vbits=int(vbits_v.get().split("-")[0]),
            dither=dither_v.get(),
            fps=int(fps_v.get().split()[0]),
            bright=int(bright_v.get()),
            contrast=int(contrast_v.get()),
            invert=bool(invert_v.get()),
            abits=int(abits_v.get().split("-")[0]),
            arate=int(arate_v.get().split()[0]),
        )
        cfg.update({k: bool(v.get()) for k, v in a_flags.items()})
        cfg.update({k: bool(v.get()) for k, v in fx.items()})
        return cfg

    def play():
        p = path_v.get().strip().strip('"')
        if not os.path.isfile(p):
            messagebox.showerror("Retro OLED Player", "Please choose a valid video file.")
            return
        if proc["p"] and proc["p"].poll() is None:
            proc["p"].terminate()
        cmd = [sys.executable] if getattr(sys, "frozen", False) \
            else [sys.executable, os.path.abspath(__file__)]
        cmd += ["--play", p, json.dumps(collect())]
        proc["p"] = subprocess.Popen(cmd)

    ttk.Button(outer, text="PLAY", style="Play.TButton", command=play).grid(
        row=6, column=0, columnspan=3, sticky="ew", pady=(14, 4))
    ttk.Label(outer, text="Video window:  Space / click = pause   T = change tint   Esc = close"
              ).grid(row=7, column=0, columnspan=3, sticky="w")

    def on_close():
        if proc["p"] and proc["p"].poll() is None:
            proc["p"].terminate()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--play":
        run_player(sys.argv[2], {**DEFAULTS, **json.loads(sys.argv[3])})
    else:
        run_launcher()
