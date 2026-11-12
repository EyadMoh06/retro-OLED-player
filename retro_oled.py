"""
Retro OLED Video Player / made by Dodo_ 
-----------------------
Plays or exports any video as a tiny old screen (OLED / CRT / VHS camcorder)
with a cheap speaker.

Run:  python retro_oled.py

Player window:
    Space / click   pause / resume        Left / Right   skip 5 seconds
    T               next colours          F              fullscreen
    Esc             close                 Drag the bar at the bottom to seek
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from types import SimpleNamespace

import numpy as np

# ----------------------------------------------------------------------------
# Themes for the launcher window
# ----------------------------------------------------------------------------
UI_FONT = "Consolas"
THEMES = {
    "Navy": dict(bg="#030164", field="#363199", line="#2D7495", text="#E8E085",
                 hi="#F4EFA8", btn="#030164"),
    "Amber Terminal": dict(bg="#120A00", field="#2E1C05", line="#9A6200", text="#FFB000",
                           hi="#FFD27A", btn="#120A00"),
    "Synthwave": dict(bg="#1A0B2E", field="#3A1D63", line="#FF4FA3", text="#FFD9F2",
                      hi="#FF9AD1", btn="#1A0B2E"),
}

# ----------------------------------------------------------------------------
# Settings, colours and presets
# ----------------------------------------------------------------------------
SCALES = {64: 10, 128: 5, 256: 3}       # real pixels per screen pixel in the player
GHOSTING = 0.28
FLICKER = 0.03

# name -> (shadow colour, highlight colour). A black shadow = a classic one-colour screen.
PALETTES = {
    "Amber": ("#000000", "#FFB000"),
    "Green": ("#000000", "#3CFF64"),
    "Blue-white": ("#000000", "#AFD7FF"),
    "White": ("#000000", "#EBEBEB"),
    "Game Boy": ("#0F380F", "#9BBC0F"),
    "Sepia": ("#28160A", "#F0D2A0"),
    "Synthwave": ("#230546", "#FF6EC7"),
    "Retro Navy": ("#030164", "#E8E085"),
}

DEFAULTS = dict(
    dark="#000000", light="#FFB000",
    res=128, vbits=4, dither="Bayer", fps=15, bright=0, contrast=100, invert=False,
    abits=6, arate=8000, a_band=True, a_dist=True, a_hiss=True, a_mute=False,
    grid=True, crt=False, bloom=False, ghost=True, flicker=True,
    noise=False, tracking=False, rolling=False, rec=False,
)


def _preset(pal, **kw):
    d, l = PALETTES[pal]
    return {**DEFAULTS, "dark": d, "light": l, **kw}


# Every preset is a COMPLETE set of settings, so picking one resets everything.
BUILTIN_PRESETS = {
    "2005 Phone OLED": _preset("Amber"),
    "CRT Monitor": _preset("Green", res=256, vbits=6, grid=False, crt=True, bloom=True,
                           abits=8, arate=16000, a_dist=False),
    "VHS Camcorder": _preset("White", res=128, vbits=5, grid=False, crt=True, bloom=True,
                             noise=True, tracking=True, rolling=True, rec=True,
                             abits=6, arate=11025, contrast=110),
    "Security Cam": _preset("Green", res=64, vbits=3, fps=10, grid=False, crt=True,
                            noise=True, rolling=True, rec=True, abits=4, arate=6000,
                            contrast=130),
    "Game Boy": _preset("Game Boy", res=128, vbits=2, grid=True, flicker=False,
                        abits=4, arate=4000, a_hiss=False),
    "Old Film": _preset("Sepia", res=256, vbits=5, grid=False, bloom=True, noise=True,
                        abits=8, arate=8000, contrast=115),
    "Synthwave": _preset("Synthwave", res=128, vbits=4, grid=False, crt=True, bloom=True,
                         abits=8, arate=22050, a_band=False, a_hiss=False),
}

VBITS_LABELS = {1: "1-bit (2 shades)", 2: "2-bit (4 shades)", 3: "3-bit (8 shades)",
                4: "4-bit (16 shades)", 5: "5-bit (32 shades)", 6: "6-bit (64 shades)",
                8: "8-bit (256 shades)"}
ABITS = [4, 6, 8, 12, 16]
ARATES = [4000, 6000, 8000, 11025, 16000, 22050]
FPS_CHOICES = [10, 15, 24, 30]
DITHERS = ["Bayer", "None"]
EXPORT_SIZES = ["Export: Normal", "Export: Large HD"]

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
HISTORY_MAX = 5


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(c):
    return "#%02X%02X%02X" % tuple(int(v) for v in c)


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


def _nowin():
    return {"creationflags": 0x08000000} if os.name == "nt" else {}


def load_logo_rgba(size):
    """Load logo.ico (PNG-compressed or classic) or logo.png as a size x size RGBA array.
    Returns None if there is no usable logo."""
    try:
        import cv2
        p = resource_path("logo.ico") or resource_path("logo.png")
        if not p:
            return None
        with open(p, "rb") as f:
            raw = f.read()
        img = None
        if raw[:4] == b"\x00\x00\x01\x00":                   # .ico: take the biggest image
            best = None
            for i in range(int.from_bytes(raw[4:6], "little")):
                e = raw[6 + 16 * i: 22 + 16 * i]
                w = e[0] or 256
                if best is None or w > best[0]:
                    best = (w, int.from_bytes(e[8:12], "little"), int.from_bytes(e[12:16], "little"))
            data = raw[best[2]: best[2] + best[1]]
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
            else:                                            # classic bitmap icon
                import pygame
                s = pygame.image.load(p)
                a = np.frombuffer(pygame.image.tostring(s, "RGBA"), np.uint8)
                a = a.reshape(s.get_height(), s.get_width(), 4)
                img = np.ascontiguousarray(a[:, :, [2, 1, 0, 3]])        # to BGRA
        else:
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            return None
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        elif img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        img = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
        return np.ascontiguousarray(img[:, :, [2, 1, 0, 3]])          # to RGBA
    except Exception:
        return None


# ---- saved data (history, your presets, your colours, theme) -----------------
def store_dir():
    d = os.environ.get("RETRO_OLED_DIR")
    if not d:
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
        d = os.path.join(base, "RetroOLEDPlayer")
    return d


def load_store():
    try:
        with open(os.path.join(store_dir(), "settings.json"), "r", encoding="utf-8") as f:
            s = json.load(f)
        if not isinstance(s, dict):
            raise ValueError
    except Exception:
        s = {}
    if not isinstance(s.get("history"), list):
        s["history"] = []
    for k in ("presets", "colors"):
        if not isinstance(s.get(k), dict):
            s[k] = {}
    if s.get("theme") not in THEMES:
        s["theme"] = "Navy"
    s["history"] = [p for p in s["history"] if isinstance(p, str)][:HISTORY_MAX]
    return s


def save_store(s):
    try:
        os.makedirs(store_dir(), exist_ok=True)
        path = os.path.join(store_dir(), "settings.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(s, f, indent=1)
        os.replace(path + ".tmp", path)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Video / audio processing
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
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, **_nowin()).stdout
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
# Screen rendering (grid, bloom, CRT) - one brightness channel, coloured at the end
# ----------------------------------------------------------------------------
class Screen:
    def __init__(self, cfg, scale=None):
        self.res = cfg["res"]
        self.scale = scale or SCALES[self.res]
        self.n = self.res * self.scale
        n, scale = self.n, self.scale
        self.bloom = cfg["bloom"]
        self.sigma = 2.5 * n / 640.0
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
            small = cv2.GaussianBlur(small, (0, 0), self.sigma)
            glow = cv2.resize(small, (self.n, self.n), interpolation=cv2.INTER_LINEAR)
            big = big + 0.55 * glow
        if self.curve:
            big = big[self.iy, self.ix] * self.mask
        return big

    @staticmethod
    def colorize(big, dark, light):
        """Brightness 0..1 -> RGB between the shadow and highlight colours."""
        b = np.clip(big, 0, 1)[:, :, None]
        d = np.array(dark, dtype=np.float32)
        lt = np.array(light, dtype=np.float32)
        return np.ascontiguousarray((d + (lt - d) * b).astype(np.uint8))


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


def fit_side(n, sw, sh):
    """Window side that fits the screen (leaves room for the title bar and taskbar)."""
    if sw <= 0 or sh <= 0:
        return n
    return int(max(320, min(n, sh * 0.86 - 40, sw * 0.95)))


def fmt_time(sec):
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ----------------------------------------------------------------------------
# Player (pygame window)
# ----------------------------------------------------------------------------
BAR_H = 50


def run_player(path, cfg):
    os.environ.setdefault("SDL_VIDEO_CENTERED", "1")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame

    fps = cfg["fps"]
    levels = 2 ** cfg["vbits"]
    pygame.init()
    scr = Screen(cfg)
    n = scr.n
    try:
        sw, sh = pygame.display.get_desktop_sizes()[0]
    except Exception:
        sw, sh = 0, 0
    side = fit_side(n, sw, sh)
    try:
        rgba = load_logo_rgba(64)
        if rgba is not None:
            pygame.display.set_icon(pygame.image.frombuffer(rgba.tobytes(), (64, 64), "RGBA"))
    except Exception:
        pass
    pygame.display.set_mode((side, side), pygame.RESIZABLE)
    pygame.display.set_caption("Retro OLED - " + os.path.basename(path))
    font = pygame.font.Font(None, max(24, n // 22))      # overlay text (on the picture)
    ui_font = pygame.font.Font(None, 24)                 # seek bar text
    clock = pygame.time.Clock()

    pal_list = cfg.get("pal_list") or [["Custom", cfg["dark"], cfg["light"]]]
    S = SimpleNamespace(
        pal_i=int(cfg.get("pal_i", 0)) % len(pal_list),
        paused=False, pause_at=0.0, t0=0.0, scrubbing=False, scrub_t=0.0,
        last_idx=-1, dirty=True, last_motion=0.0, bar_prev=False,
        scaled=None, fsurf=None, win=(0, 0), offs=(0, 0), duration=1.0,
        cur_sound=None, recolor=False,
    )

    def colours():
        _, d, lt = pal_list[S.pal_i]
        return hex_to_rgb(d), hex_to_rgb(lt)

    def splash(text, frac=None):
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit()
                sys.exit(0)
        screen = pygame.display.get_surface()
        ww, wh = screen.get_size()
        dark, col = colours()
        screen.fill(dark)
        img = font.render(text, True, col)
        screen.blit(img, img.get_rect(center=(ww // 2, wh // 2 - 20)))
        if frac is not None:
            bw = ww // 2
            pygame.draw.rect(screen, col, (ww // 4, wh // 2 + 10, bw, 14), 1)
            pygame.draw.rect(screen, col, (ww // 4 + 2, wh // 2 + 12, int((bw - 4) * frac), 10))
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
    snd_data, snd_freq, snd_chans = None, cfg["arate"], 1
    if audio is not None:
        try:
            rate = cfg["arate"]
            pygame.mixer.init(frequency=rate, size=-16, channels=1, allowedchanges=0)
            snd_freq, _, snd_chans = pygame.mixer.get_init()
            data = audio
            if snd_freq != rate:                       # fallback: hold-resample
                idx = (np.arange(int(len(audio) * snd_freq / rate)) * rate / snd_freq).astype(int)
                data = audio[np.clip(idx, 0, len(audio) - 1)]
            if snd_chans == 2:
                data = np.repeat(data, 2)
            snd_data = np.ascontiguousarray(data)
        except Exception:
            snd_data = None

    count = len(frames)
    S.duration = count / fps
    rng = np.random.default_rng()
    prev = frames[0].astype(np.float32) / (levels - 1)
    last_big = None

    def mixer_call(name):
        if snd_data is not None:
            try:
                getattr(pygame.mixer, name)()
            except Exception:
                pass

    def start_audio(at=0.0):
        if snd_data is None:
            return
        try:
            if S.cur_sound is not None:
                S.cur_sound.stop()
            start = int(max(0.0, at) * snd_freq) * snd_chans
            arr = snd_data[start:]
            if len(arr) < snd_chans * 2:
                return
            S.cur_sound = pygame.mixer.Sound(buffer=arr.tobytes())
            S.cur_sound.play()
        except Exception:
            pass

    def now():
        if S.scrubbing:
            return S.scrub_t
        return (S.pause_at if S.paused else time.perf_counter()) - S.t0

    def set_time(target):
        ref = S.pause_at if S.paused else time.perf_counter()
        S.t0 = ref - target

    def toggle_pause():
        if S.paused:
            S.t0 += time.perf_counter() - S.pause_at
            S.paused = False
            mixer_call("unpause")
        else:
            S.paused = True
            S.pause_at = time.perf_counter()
            mixer_call("pause")
        S.dirty = True

    def seek_to(target):
        target = min(max(0.0, target), max(0.0, S.duration - 1.0 / fps))
        set_time(target)
        S.last_idx = -1
        S.dirty = True
        start_audio(target)
        if S.paused:
            mixer_call("pause")

    # the track starts after the icon and the "0:09 / 0:12" label
    label_w = ui_font.size(f"{fmt_time(S.duration)} / {fmt_time(S.duration)}")[0]
    track_x0 = 44 + label_w + 14

    def x_to_time(x, ww):
        x1 = max(track_x0 + 1, ww - 16)
        frac = min(max((x - track_x0) / (x1 - track_x0), 0.0), 1.0)
        return frac * max(0.0, S.duration - 1.0 / fps)

    shade_cache = {}

    def draw_bar(screen, ww, wh, t, acc):
        shade = shade_cache.get(ww)
        if shade is None:
            shade = pygame.Surface((ww, BAR_H), pygame.SRCALPHA)
            for k in range(BAR_H):
                shade.fill((0, 0, 0, int(190 * k / BAR_H)), (0, k, ww, 1))
            shade_cache.clear()
            shade_cache[ww] = shade
        screen.blit(shade, (0, wh - BAR_H))
        white = (240, 240, 240)
        ty = wh - 24                                     # everything sits on one line
        iy = ty - 10
        if S.paused:                                     # play / pause icon
            pygame.draw.polygon(screen, white, [(16, iy), (16, iy + 20), (32, iy + 10)])
        else:
            pygame.draw.rect(screen, white, (16, iy, 5, 20))
            pygame.draw.rect(screen, white, (26, iy, 5, 20))
        label = ui_font.render(f"{fmt_time(t)} / {fmt_time(S.duration)}", True, white)
        screen.blit(label, (44, ty - label.get_height() // 2))
        x0, x1 = track_x0, max(track_x0 + 1, ww - 16)    # track
        pygame.draw.line(screen, (100, 100, 100), (x0, ty), (x1, ty), 4)
        frac = min(max(t / max(S.duration, 0.001), 0.0), 1.0)
        kx = int(x0 + frac * (x1 - x0))
        pygame.draw.line(screen, acc, (x0, ty), (kx, ty), 4)
        pygame.draw.circle(screen, acc, (kx, ty), 8)

    S.t0 = time.perf_counter()
    S.last_motion = time.perf_counter()                  # show the bar briefly at start
    start_audio(0.0)

    running = True
    while running:
        screen = pygame.display.get_surface()
        ww, wh = screen.get_size()
        if (ww, wh) != S.win:
            S.win = (ww, wh)
            S.scaled = None
            S.dirty = True

        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.VIDEORESIZE:
                S.dirty = True
            elif e.type == pygame.KEYDOWN:
                S.last_motion = time.perf_counter()
                if e.key == pygame.K_ESCAPE:
                    running = False
                elif e.key == pygame.K_SPACE:
                    toggle_pause()
                elif e.key == pygame.K_t:
                    S.pal_i = (S.pal_i + 1) % len(pal_list)
                    S.recolor = True
                    S.dirty = True
                elif e.key == pygame.K_LEFT:
                    seek_to(now() - 5)
                elif e.key == pygame.K_RIGHT:
                    seek_to(now() + 5)
                elif e.key == pygame.K_f:
                    try:
                        pygame.display.toggle_fullscreen()
                    except Exception:
                        pass
            elif e.type == pygame.MOUSEMOTION:
                S.last_motion = time.perf_counter()
                if S.scrubbing:
                    S.scrub_t = x_to_time(e.pos[0], ww)
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                S.last_motion = time.perf_counter()
                mx, my = e.pos
                if my >= wh - BAR_H + 4:
                    if mx < 40:
                        toggle_pause()
                    else:
                        S.scrubbing = True
                        S.scrub_t = x_to_time(mx, ww)
                        mixer_call("pause")
                else:
                    toggle_pause()
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1 and S.scrubbing:
                S.scrubbing = False
                seek_to(S.scrub_t)

        t = now()
        idx = int(t * fps)
        if not S.scrubbing and idx >= count:               # loop
            S.t0 = time.perf_counter()
            S.paused = False
            t, idx, S.last_idx = 0.0, 0, -1
            start_audio(0.0)
        idx = min(max(idx, 0), count - 1)

        rebuild = False
        if idx != S.last_idx:
            cur = frames[idx].astype(np.float32) / (levels - 1)
            if cfg["ghost"]:
                cur = cur * (1 - GHOSTING) + prev * GHOSTING
            prev = cur
            last_big = scr.render(low_res_effects(cur, t, cfg, rng))
            S.last_idx = idx
            rebuild = True
        if S.recolor:
            rebuild = True
            S.recolor = False

        if rebuild and last_big is not None:
            dark, light = colours()
            rgb = Screen.colorize(last_big, dark, light)
            S.fsurf = pygame.image.frombuffer(rgb.tobytes(), (n, n), "RGB").copy()
            if cfg["rec"]:
                draw_overlay(pygame, S.fsurf, font, light, n, t, fps, cfg["crt"])
            S.scaled = None
            S.dirty = True

        if S.fsurf is not None and S.scaled is None:
            sd = max(64, min(ww, wh))
            S.scaled = S.fsurf if sd == n else pygame.transform.smoothscale(S.fsurf, (sd, sd))
            S.offs = ((ww - sd) // 2, (wh - sd) // 2)
            S.dirty = True

        bar_on = S.paused or S.scrubbing or (time.perf_counter() - S.last_motion) < 2.5
        if bar_on or bar_on != S.bar_prev:
            S.dirty = True
        S.bar_prev = bar_on

        if S.dirty and S.scaled is not None:
            screen.fill((0, 0, 0))
            screen.blit(S.scaled, S.offs)
            if bar_on:
                draw_bar(screen, ww, wh, t, colours()[1])
            pygame.display.flip()
            S.dirty = False
        clock.tick(60)

    pygame.quit()


# ----------------------------------------------------------------------------
# Export to MP4 (runs as its own process so the launcher stays responsive)
# ----------------------------------------------------------------------------
def _has_encoder(ffmpeg, name):
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                             **_nowin()).stdout.decode("utf-8", "ignore")
        return name in out
    except Exception:
        return False


def _export(path, out, cfg, size_choice, report):
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame
    pygame.font.init()

    res, fps = cfg["res"], cfg["fps"]
    levels = 2 ** cfg["vbits"]
    scale = SCALES[res]
    if size_choice.startswith("Large"):
        scale = max(2, round(1080 / res))
    scr = Screen(cfg, scale)
    n = scr.n
    dark, light = hex_to_rgb(cfg["dark"]), hex_to_rgb(cfg["light"])

    report(0.0, "Reading video...")
    frames = load_video(path, cfg, lambda f: report(f * 0.30, "Reading video..."))
    audio = None
    if not cfg["a_mute"]:
        report(0.31, "Processing audio...")
        audio = load_audio(path, cfg)

    ffmpeg = get_ffmpeg()
    if _has_encoder(ffmpeg, "libx264"):
        enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    else:
        enc = ["-c:v", "mpeg4", "-q:v", "2"]

    tmpdir = tempfile.mkdtemp(prefix="retro_oled_")
    try:
        wav = None
        if audio is not None:
            wav = os.path.join(tmpdir, "audio.wav")
            with wave.open(wav, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(cfg["arate"])
                w.writeframes(audio.tobytes())
        cmd = [ffmpeg, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{n}x{n}", "-r", str(fps), "-i", "-"]
        if wav:
            cmd += ["-i", wav]
        cmd += enc + ["-pix_fmt", "yuv420p"]
        if wav:
            cmd += ["-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-shortest"]
        cmd += ["-movflags", "+faststart", out]

        log_path = os.path.join(tmpdir, "ffmpeg.log")
        count = len(frames)
        rng = np.random.default_rng()
        prev = frames[0].astype(np.float32) / (levels - 1)
        font = pygame.font.Font(None, max(24, n // 22))
        with open(log_path, "wb") as log:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stderr=log, **_nowin())
            try:
                for idx in range(count):
                    t = idx / fps
                    cur = frames[idx].astype(np.float32) / (levels - 1)
                    if cfg["ghost"]:
                        cur = cur * (1 - GHOSTING) + prev * GHOSTING
                    prev = cur
                    rgb = Screen.colorize(scr.render(low_res_effects(cur, t, cfg, rng)),
                                          dark, light)
                    if cfg["rec"]:
                        surf = pygame.image.frombuffer(rgb.tobytes(), (n, n), "RGB").copy()
                        draw_overlay(pygame, surf, font, light, n, t, fps, cfg["crt"])
                        tob = getattr(pygame.image, "tobytes", None) or pygame.image.tostring
                        data = tob(surf, "RGB")
                    else:
                        data = rgb.tobytes()
                    proc.stdin.write(data)
                    if idx % 4 == 0:
                        report(0.33 + 0.62 * idx / count, "Rendering video...")
                report(0.96, "Finishing...")
            except (BrokenPipeError, OSError):
                pass                                   # the encoder died; its log says why
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            rc = proc.wait()
        if rc != 0 or not os.path.isfile(out):
            try:
                with open(log_path, "rb") as f:
                    msg = f.read().decode("utf-8", "ignore").strip().splitlines()
                msg = msg[-1] if msg else ""
            except Exception:
                msg = ""
            raise RuntimeError("The video encoder failed. " + msg[:150])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_export(path, out, cfg, size_choice, progress_file):
    def report(frac, msg=""):
        try:
            with open(progress_file + ".tmp", "w", encoding="utf-8") as f:
                f.write(f"{frac:.4f}|{msg}")
            os.replace(progress_file + ".tmp", progress_file)
        except Exception:
            pass

    try:
        _export(path, out, cfg, size_choice, report)
        report(1.0, "done")
    except Exception as exc:
        try:
            if os.path.exists(out):
                os.remove(out)
        except Exception:
            pass
        report(-1.0, "error: " + str(exc)[:300])


def read_progress(pf):
    try:
        with open(pf, "r", encoding="utf-8") as f:
            a, _, b = f.read().partition("|")
        return float(a), b
    except Exception:
        return None, ""


# ----------------------------------------------------------------------------
# Launcher (tkinter window)
# ----------------------------------------------------------------------------
def run_launcher(hook=None):
    import tkinter as tk
    from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

    store = load_store()
    root = tk.Tk()
    root.title("Retro OLED Player")
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
    st = ttk.Style(root)
    st.theme_use("clam")

    combos, seps, scales = [], [], []
    proc = {"p": None}
    exp = SimpleNamespace(proc=None, pf=None, out=None)

    outer = ttk.Frame(root, padding=12)
    outer.grid()

    # ---- variables -----------------------------------------------------------------
    d = DEFAULTS
    path_v = tk.StringVar()
    recent_v = tk.StringVar(value="Recent videos")
    preset_v = tk.StringVar(value="2005 Phone OLED")
    pal_v = tk.StringVar()
    dark_v = tk.StringVar(value=d["dark"])
    light_v = tk.StringVar(value=d["light"])
    single_v = tk.BooleanVar(value=True)
    res_v = tk.StringVar()
    vbits_v = tk.StringVar()
    dither_v = tk.StringVar()
    fps_v = tk.StringVar()
    bright_v = tk.IntVar()
    contrast_v = tk.IntVar()
    invert_v = tk.BooleanVar()
    abits_v = tk.StringVar()
    arate_v = tk.StringVar()
    a_flags = {k: tk.BooleanVar() for k in ("a_band", "a_dist", "a_hiss", "a_mute")}
    fx = {k: tk.BooleanVar() for k, _ in EFFECTS_UI}
    theme_v = tk.StringVar(value=store["theme"])
    size_v = tk.StringVar(value=EXPORT_SIZES[0])
    status_v = tk.StringVar()
    HINT = "Player:  Space = pause   <- / -> = skip 5s   T = colours   F = fullscreen   Esc = close"
    status_v.set(HINT)
    prog_v = tk.DoubleVar(value=0)

    def mk_combo(parent, var, values, width=18):
        cb = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=width)
        combos.append(cb)
        return cb

    # ---- header ----------------------------------------------------------------------
    header = ttk.Frame(outer)
    header.grid(row=0, column=0, columnspan=3, sticky="ew")
    header.columnconfigure(2, weight=1)
    logo_label = tk.Label(header, bd=0)
    logo_label.grid(row=0, column=0, rowspan=2, padx=(0, 10))
    ttk.Label(header, text="RETRO OLED PLAYER", style="Title.TLabel").grid(
        row=0, column=1, sticky="w")
    ttk.Label(header, text="Turn any video into a tiny old screen with a tiny old speaker.").grid(
        row=1, column=1, sticky="w")
    themebox = ttk.Frame(header)
    themebox.grid(row=0, column=2, rowspan=2, sticky="e")
    ttk.Label(themebox, text="Theme").grid(row=0, column=0, padx=(0, 6))
    theme_cb = mk_combo(themebox, theme_v, list(THEMES), 14)
    theme_cb.grid(row=0, column=1)
    sep = tk.Frame(outer, height=2)
    sep.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 10))
    seps.append(sep)

    # ---- file row + recent -----------------------------------------------------------
    filerow = ttk.Frame(outer)
    filerow.grid(row=2, column=0, columnspan=3, sticky="ew")
    ttk.Label(filerow, text="Video:").grid(row=0, column=0, padx=(0, 8))
    ttk.Entry(filerow, textvariable=path_v, width=36).grid(row=0, column=1, sticky="ew")

    def browse():
        p = filedialog.askopenfilename(title="Choose a video", filetypes=VIDEO_TYPES)
        if p:
            path_v.set(p)

    ttk.Button(filerow, text="Browse...", command=browse).grid(row=0, column=2, padx=(8, 6))
    recent_cb = mk_combo(filerow, recent_v, [], 16)
    recent_cb.grid(row=0, column=3)

    def refresh_recent():
        recent_cb["values"] = [
            f"{os.path.basename(p)}  ({os.path.basename(os.path.dirname(p))})"
            for p in store["history"]]

    def add_history(p):
        p = os.path.abspath(p)
        h = [x for x in store["history"] if os.path.normcase(x) != os.path.normcase(p)]
        store["history"] = ([p] + h)[:HISTORY_MAX]
        save_store(store)
        refresh_recent()

    def on_recent(_=None):
        i = recent_cb.current()
        recent_v.set("Recent videos")
        if i < 0 or i >= len(store["history"]):
            return
        p = store["history"][i]
        if os.path.isfile(p):
            path_v.set(p)
        else:
            messagebox.showwarning("Retro OLED Player",
                                   "That video can't be found any more, so it was removed "
                                   "from the list.")
            store["history"].pop(i)
            save_store(store)
            refresh_recent()

    recent_cb.bind("<<ComboboxSelected>>", on_recent)

    # ---- preset row -------------------------------------------------------------------
    prow = ttk.Frame(outer)
    prow.grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 8))
    ttk.Label(prow, text="Preset:").grid(row=0, column=0, padx=(0, 8))
    preset_cb = mk_combo(prow, preset_v, [], 20)
    preset_cb.grid(row=0, column=1)

    def preset_names():
        return list(BUILTIN_PRESETS) + ["★ " + k for k in store["presets"]]

    def preset_cfg(disp):
        if disp.startswith("★ "):
            return store["presets"].get(disp[2:])
        return BUILTIN_PRESETS.get(disp)

    def refresh_presets():
        preset_cb["values"] = preset_names()

    # ---- panels: VIDEO / AUDIO / EFFECTS / COLOUR ----------------------------------------
    def combo(parent, r, label, var, values, width=18):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=(6, 0))
        mk_combo(parent, var, values, width).grid(row=r + 1, column=0, sticky="w")

    def slider(parent, r, label, var, lo, hi):
        text = tk.StringVar()
        text.set(f"{label}: {var.get()}")
        var.trace_add("write", lambda *a: text.set(f"{label}: {var.get()}"))
        ttk.Label(parent, textvariable=text).grid(row=r, column=0, sticky="w", pady=(6, 0))
        sc = tk.Scale(parent, from_=lo, to=hi, orient="horizontal", variable=var, length=190,
                      showvalue=False, highlightthickness=0, bd=0, sliderrelief="flat",
                      sliderlength=16, width=14)
        sc.grid(row=r + 1, column=0, sticky="w", pady=(2, 0))
        scales.append(sc)

    vid = ttk.LabelFrame(outer, text=" VIDEO ", padding=8)
    vid.grid(row=4, column=0, rowspan=2, sticky="nsew", padx=(0, 8))
    combo(vid, 0, "Resolution", res_v, [f"{r} x {r}" for r in SCALES])
    combo(vid, 2, "Bit depth", vbits_v, list(VBITS_LABELS.values()))
    combo(vid, 4, "Dithering", dither_v, DITHERS)
    combo(vid, 6, "Frame rate", fps_v, [f"{f} fps" for f in FPS_CHOICES])
    slider(vid, 8, "Brightness", bright_v, -50, 50)
    slider(vid, 10, "Contrast %", contrast_v, 50, 200)
    ttk.Checkbutton(vid, text="Invert (negative)", variable=invert_v).grid(
        row=12, column=0, sticky="w", pady=(6, 0))

    aud = ttk.LabelFrame(outer, text=" AUDIO ", padding=8)
    aud.grid(row=4, column=1, sticky="nsew", padx=(0, 8))
    combo(aud, 0, "Bit depth", abits_v, [f"{b}-bit" for b in ABITS])
    combo(aud, 2, "Sample rate", arate_v, [f"{r} Hz" for r in ARATES])
    for r, (k, lab) in enumerate([("a_band", "Tiny speaker filter"),
                                  ("a_dist", "Speaker distortion"),
                                  ("a_hiss", "Tape / line hiss"),
                                  ("a_mute", "Mute")]):
        ttk.Checkbutton(aud, text=lab, variable=a_flags[k]).grid(
            row=4 + r, column=0, sticky="w", pady=(8 if r == 0 else 2, 0))

    efx = ttk.LabelFrame(outer, text=" EFFECTS ", padding=8)
    efx.grid(row=4, column=2, sticky="nsew")
    for r, (k, lab) in enumerate(EFFECTS_UI):
        ttk.Checkbutton(efx, text=lab, variable=fx[k]).grid(row=r, column=0, sticky="w", pady=1)

    # ---- colours ----------------------------------------------------------------------
    colp = ttk.LabelFrame(outer, text=" COLOURS ", padding=8)
    colp.grid(row=5, column=1, columnspan=2, sticky="nsew", pady=(8, 0))
    pal_cb = mk_combo(colp, pal_v, [], 20)
    pal_cb.grid(row=0, column=0, sticky="w")
    pal_save = ttk.Button(colp, text="Save...", style="Small.TButton")
    pal_save.grid(row=0, column=1, padx=(8, 4))
    pal_del = ttk.Button(colp, text="Delete", style="Small.TButton")
    pal_del.grid(row=0, column=2)
    strip = tk.Canvas(colp, width=396, height=18, highlightthickness=1, bd=0)
    strip.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 5))
    shadow_btn = ttk.Button(colp, text="Shadow colour", style="Small.TButton")
    shadow_btn.grid(row=2, column=0, sticky="w")
    light_btn = ttk.Button(colp, text="Highlight", style="Small.TButton")
    light_btn.grid(row=2, column=1, padx=(8, 4), sticky="w")
    single_cb = ttk.Checkbutton(colp, text="One colour", variable=single_v)
    single_cb.grid(row=2, column=2, columnspan=2, sticky="w", padx=(4, 0))

    def pal_map():
        m = dict(PALETTES)
        for k, v in store["colors"].items():
            if isinstance(v, list) and len(v) == 2:
                m["★ " + k] = (v[0], v[1])
        return m

    def refresh_palettes():
        pal_cb["values"] = list(pal_map())

    def sync_pal_name():
        dk, lt = dark_v.get().upper(), light_v.get().upper()
        for disp, (pd, pl) in pal_map().items():
            if pd.upper() == dk and pl.upper() == lt:
                pal_v.set(disp)
                return
        pal_v.set("Custom")

    def refresh_strip():
        strip.delete("all")
        d0, d1 = hex_to_rgb(dark_v.get()), hex_to_rgb(light_v.get())
        steps, w = 48, 396
        for i in range(steps):
            f = i / (steps - 1)
            col = rgb_to_hex([d0[j] + (d1[j] - d0[j]) * f for j in range(3)])
            strip.create_rectangle(i * w / steps, 0, (i + 1) * w / steps + 1, 20,
                                   fill=col, outline="")
        if single_v.get():
            shadow_btn.state(["disabled"])
        else:
            shadow_btn.state(["!disabled"])

    def on_pal(_=None):
        pair = pal_map().get(pal_v.get())
        if pair:
            dark_v.set(pair[0])
            light_v.set(pair[1])
            single_v.set(pair[0].upper() == "#000000")
            refresh_strip()

    def pick(var, title):
        res = colorchooser.askcolor(color=var.get(), title=title, parent=root)
        if res and res[1]:
            var.set(rgb_to_hex(res[0]))
            return True
        return False

    def pick_shadow():
        if pick(dark_v, "Shadow colour (the darkest parts)"):
            single_v.set(dark_v.get().upper() == "#000000")
            sync_pal_name()
            refresh_strip()

    def pick_light():
        if pick(light_v, "Highlight colour (the brightest parts)"):
            sync_pal_name()
            refresh_strip()

    def on_single():
        if single_v.get():
            dark_v.set("#000000")
        sync_pal_name()
        refresh_strip()

    def save_colours():
        name = simpledialog.askstring("Save colours", "Name for these colours:", parent=root)
        name = (name or "").strip()
        if not name:
            return
        if name in PALETTES:
            messagebox.showerror("Retro OLED Player", "That name is used by a built-in colour set.")
            return
        store["colors"][name] = [dark_v.get(), light_v.get()]
        save_store(store)
        refresh_palettes()
        pal_v.set("★ " + name)

    def delete_colours():
        disp = pal_v.get()
        if not disp.startswith("★ "):
            messagebox.showinfo("Retro OLED Player", "Only colours you saved can be deleted.")
            return
        if messagebox.askyesno("Retro OLED Player", f"Delete the colours '{disp[2:]}'?"):
            store["colors"].pop(disp[2:], None)
            save_store(store)
            refresh_palettes()
            sync_pal_name()

    pal_cb.bind("<<ComboboxSelected>>", on_pal)
    pal_save.configure(command=save_colours)
    pal_del.configure(command=delete_colours)
    shadow_btn.configure(command=pick_shadow)
    light_btn.configure(command=pick_light)
    single_cb.configure(command=on_single)

    # ---- settings <-> controls -----------------------------------------------------------
    def apply_cfg(cfg):
        c = {**DEFAULTS, **cfg}
        res = c["res"] if c["res"] in SCALES else 128
        res_v.set(f"{res} x {res}")
        vbits_v.set(VBITS_LABELS[c["vbits"] if c["vbits"] in VBITS_LABELS else 4])
        dither_v.set(c["dither"] if c["dither"] in DITHERS else "Bayer")
        fps_v.set(f"{c['fps'] if c['fps'] in FPS_CHOICES else 15} fps")
        bright_v.set(int(c["bright"]))
        contrast_v.set(int(c["contrast"]))
        invert_v.set(bool(c["invert"]))
        abits_v.set(f"{c['abits'] if c['abits'] in ABITS else 6}-bit")
        arate_v.set(f"{c['arate'] if c['arate'] in ARATES else 8000} Hz")
        for k, v in a_flags.items():
            v.set(bool(c[k]))
        for k, v in fx.items():
            v.set(bool(c[k]))
        dark_v.set(c["dark"])
        light_v.set(c["light"])
        single_v.set(c["dark"].upper() == "#000000")
        sync_pal_name()
        refresh_strip()

    def collect(preset_only=False):
        cfg = dict(
            res=int(res_v.get().split()[0]),
            vbits=int(vbits_v.get().split("-")[0]),
            dither=dither_v.get(),
            fps=int(fps_v.get().split()[0]),
            bright=int(bright_v.get()),
            contrast=int(contrast_v.get()),
            invert=bool(invert_v.get()),
            abits=int(abits_v.get().split("-")[0]),
            arate=int(arate_v.get().split()[0]),
            dark=dark_v.get(),
            light=light_v.get(),
        )
        cfg.update({k: bool(v.get()) for k, v in a_flags.items()})
        cfg.update({k: bool(v.get()) for k, v in fx.items()})
        if not preset_only:
            pm = pal_map()
            lst, idx = [], None
            for i, (k, (pd, pl)) in enumerate(pm.items()):
                lst.append([k.replace("★ ", ""), pd, pl])
                if idx is None and pd.upper() == cfg["dark"].upper() \
                        and pl.upper() == cfg["light"].upper():
                    idx = i
            if idx is None:
                lst.insert(0, ["Custom", cfg["dark"], cfg["light"]])
                idx = 0
            cfg["pal_list"], cfg["pal_i"] = lst, idx
        return cfg

    def on_preset(_=None):
        c = preset_cfg(preset_v.get())
        if c is not None:
            apply_cfg(c)

    def save_preset():
        name = simpledialog.askstring("Save preset", "Name for this preset:", parent=root)
        name = (name or "").strip()
        if not name:
            return
        if name in BUILTIN_PRESETS:
            messagebox.showerror("Retro OLED Player", "That name is used by a built-in preset.")
            return
        if name in store["presets"] and not messagebox.askyesno(
                "Retro OLED Player", f"A preset called '{name}' exists. Replace it?"):
            return
        store["presets"][name] = collect(preset_only=True)
        save_store(store)
        refresh_presets()
        preset_v.set("★ " + name)

    def delete_preset():
        disp = preset_v.get()
        if not disp.startswith("★ "):
            messagebox.showinfo("Retro OLED Player", "Built-in presets can't be deleted. "
                                "Only presets you saved can.")
            return
        if messagebox.askyesno("Retro OLED Player", f"Delete the preset '{disp[2:]}'?"):
            store["presets"].pop(disp[2:], None)
            save_store(store)
            refresh_presets()
            preset_v.set("2005 Phone OLED")

    def reset_all():
        preset_v.set("2005 Phone OLED")
        apply_cfg(DEFAULTS)

    preset_cb.bind("<<ComboboxSelected>>", on_preset)
    ttk.Button(prow, text="Save preset...", style="Small.TButton", command=save_preset).grid(
        row=0, column=2, padx=(10, 4))
    ttk.Button(prow, text="Delete", style="Small.TButton", command=delete_preset).grid(
        row=0, column=3, padx=4)
    ttk.Button(prow, text="Reset to default", style="Small.TButton", command=reset_all).grid(
        row=0, column=4, padx=4)

    # ---- play / export ----------------------------------------------------------------------
    def base_cmd():
        return [sys.executable] if getattr(sys, "frozen", False) \
            else [sys.executable, os.path.abspath(__file__)]

    def valid_path():
        p = path_v.get().strip().strip('"')
        if not os.path.isfile(p):
            messagebox.showerror("Retro OLED Player", "Please choose a valid video file.")
            return None
        return p

    def play():
        p = valid_path()
        if not p:
            return
        add_history(p)
        if proc["p"] and proc["p"].poll() is None:
            proc["p"].terminate()
        proc["p"] = subprocess.Popen(base_cmd() + ["--play", p, json.dumps(collect())])

    btns = ttk.Frame(outer)
    btns.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 2))
    btns.columnconfigure(0, weight=3)
    btns.columnconfigure(1, weight=3)
    play_btn = ttk.Button(btns, text="PLAY", style="Play.TButton", command=play)
    play_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    save_btn = ttk.Button(btns, text="SAVE AS MP4", style="Play.TButton")
    save_btn.grid(row=0, column=1, sticky="ew", padx=(0, 6))
    mk_combo(btns, size_v, EXPORT_SIZES, 16).grid(row=0, column=2)

    statusrow = ttk.Frame(outer)
    statusrow.grid(row=7, column=0, columnspan=3, sticky="ew")
    ttk.Label(statusrow, textvariable=status_v).grid(row=0, column=0, sticky="w")
    progrow = ttk.Frame(outer)
    progrow.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(6, 0))
    progrow.columnconfigure(0, weight=1)
    ttk.Progressbar(progrow, variable=prog_v, maximum=100, mode="determinate").grid(
        row=0, column=0, sticky="ew")
    cancel_btn = ttk.Button(progrow, text="Cancel", style="Small.TButton")
    cancel_btn.grid(row=0, column=1, padx=(8, 0))
    progrow.grid_remove()

    def end_export():
        exp.proc = None
        progrow.grid_remove()
        save_btn.state(["!disabled"])
        status_v.set(HINT)
        try:
            if exp.pf and os.path.exists(exp.pf):
                os.remove(exp.pf)
        except Exception:
            pass

    def poll_export():
        if exp.proc is None:
            return
        frac, msg = read_progress(exp.pf)
        if frac is not None and frac >= 0:
            prog_v.set(frac * 100)
            status_v.set(f"Saving video... {int(frac * 100)}%   {msg}")
        if exp.proc.poll() is None:
            root.after(250, poll_export)
            return
        frac, msg = read_progress(exp.pf)
        out = exp.out
        end_export()
        if msg == "done":
            messagebox.showinfo("Retro OLED Player", f"Video saved:\n{out}")
        else:
            text = msg.replace("error: ", "") if msg.startswith("error") else \
                "Something went wrong while saving the video."
            messagebox.showerror("Retro OLED Player", text)

    def cancel_export():
        p, out = exp.proc, exp.out
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                pass
        end_export()
        try:
            if out and os.path.exists(out):
                os.remove(out)
        except Exception:
            pass

    def start_export():
        p = valid_path()
        if not p or exp.proc is not None:
            return
        initial = os.path.splitext(os.path.basename(p))[0] + "_retro.mp4"
        out = filedialog.asksaveasfilename(
            title="Save video as", defaultextension=".mp4", initialfile=initial,
            initialdir=os.path.dirname(p), filetypes=[("MP4 video", "*.mp4")])
        if not out:
            return
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(p)):
            messagebox.showerror("Retro OLED Player",
                                 "Please save to a different file than the original video.")
            return
        add_history(p)
        exp.pf = os.path.join(tempfile.gettempdir(), f"retro_oled_progress_{os.getpid()}.txt")
        try:
            if os.path.exists(exp.pf):
                os.remove(exp.pf)
        except Exception:
            pass
        exp.out = out
        size = "Large" if "Large" in size_v.get() else "Normal"
        exp.proc = subprocess.Popen(base_cmd() + ["--export", p, out, json.dumps(collect()),
                                                  size, exp.pf])
        prog_v.set(0)
        status_v.set("Saving video... 0%")
        progrow.grid()
        save_btn.state(["disabled"])
        root.after(250, poll_export)

    save_btn.configure(command=start_export)
    cancel_btn.configure(command=cancel_export)

    # ---- theme ----------------------------------------------------------------------------
    logo_rgba = load_logo_rgba(52)

    def set_logo(bg):
        if logo_rgba is None:
            logo_label.grid_remove()
            return
        try:
            b = np.array(hex_to_rgb(bg), dtype=np.float32)
            a = logo_rgba[:, :, 3:4].astype(np.float32) / 255.0
            rgb = (logo_rgba[:, :, :3] * a + b * (1 - a)).astype(np.uint8)
            img = tk.PhotoImage(data=b"P6\n52 52\n255\n" + rgb.tobytes())
            logo_label.configure(image=img, bg=bg)
            logo_label.image = img
        except Exception:
            logo_label.grid_remove()

    def style_popdown(cb, field, text, line):
        try:
            pd = root.tk.eval(f"ttk::combobox::PopdownWindow {cb}")
            root.tk.call(f"{pd}.f.l", "configure", "-background", field, "-foreground", text,
                         "-selectbackground", line, "-selectforeground", text,
                         "-borderwidth", 0, "-highlightthickness", 0)
        except Exception:
            pass

    def titlebar(bg, text):
        if os.name != "nt":
            return
        try:
            root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            one = ctypes.c_int(1)
            for attr in (20, 19):                        # dark title bar
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(one), ctypes.sizeof(one)) == 0:
                    break
            for attr, col in ((35, bg), (36, text)):     # title bar colours (Windows 11)
                r, g, b = hex_to_rgb(col)
                val = ctypes.c_int(r | (g << 8) | (b << 16))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
        except Exception:
            pass

    def apply_theme(name):
        th = THEMES.get(name, THEMES["Navy"])
        bg, field, line = th["bg"], th["field"], th["line"]
        text, hi, btn = th["text"], th["hi"], th["btn"]
        dim = rgb_to_hex([(a + b_) / 2 for a, b_ in zip(hex_to_rgb(text), hex_to_rgb(field))])
        root.configure(bg=bg)
        st.configure(".", background=bg, foreground=text, font=F, bordercolor=line,
                     lightcolor=bg, darkcolor=bg, troughcolor=field, focuscolor=bg)
        st.configure("TLabel", background=bg, foreground=text)
        st.configure("Title.TLabel", font=(UI_FONT, 20, "bold"))
        st.configure("TFrame", background=bg)
        st.configure("TLabelframe", background=bg, bordercolor=line)
        st.configure("TLabelframe.Label", background=bg, foreground=text, font=FB)
        st.configure("TEntry", fieldbackground=field, foreground=text, insertcolor=text,
                     bordercolor=line)
        st.configure("TCombobox", fieldbackground=field, background=field, foreground=text,
                     arrowcolor=text, bordercolor=line, selectbackground=field,
                     selectforeground=text)
        st.map("TCombobox", fieldbackground=[("readonly", field)],
               foreground=[("readonly", text)], background=[("active", line)])
        st.configure("TButton", background=field, foreground=text, bordercolor=line,
                     padding=6, font=FB)
        st.map("TButton", background=[("disabled", bg), ("active", line), ("pressed", line)],
               foreground=[("disabled", dim)])
        st.configure("Small.TButton", padding=3, font=F)
        st.configure("Play.TButton", background=text, foreground=btn,
                     font=(UI_FONT, 13, "bold"), padding=6)
        st.map("Play.TButton", background=[("disabled", dim), ("active", hi), ("pressed", hi)],
               foreground=[("disabled", bg), ("active", btn)])
        st.configure("TCheckbutton", background=bg, foreground=text, indicatorbackground=field,
                     indicatorforeground=btn, indicatorcolor=field)
        st.map("TCheckbutton", background=[("active", bg)],
               indicatorbackground=[("selected", text), ("!selected", field)],
               indicatorcolor=[("selected", text), ("!selected", field)],
               foreground=[("active", text)])
        st.configure("Horizontal.TProgressbar", troughcolor=field, background=text,
                     bordercolor=line, lightcolor=text, darkcolor=text)
        for fr in seps:
            fr.configure(bg=line)
        for sc in scales:
            sc.configure(bg=text, troughcolor=field, activebackground=hi)
        strip.configure(bg=bg, highlightbackground=line)
        for cb in combos:
            style_popdown(cb, field, text, line)
        set_logo(bg)
        titlebar(bg, text)

    def on_theme(_=None):
        apply_theme(theme_v.get())
        store["theme"] = theme_v.get()
        save_store(store)

    theme_cb.bind("<<ComboboxSelected>>", on_theme)

    # ---- start ----------------------------------------------------------------------------
    refresh_recent()
    refresh_presets()
    refresh_palettes()
    apply_cfg(DEFAULTS)
    apply_theme(store["theme"])

    def on_close():
        if proc["p"] and proc["p"].poll() is None:
            proc["p"].terminate()
        if exp.proc is not None:
            cancel_export()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.update()
    titlebar(THEMES[store["theme"]]["bg"], THEMES[store["theme"]]["text"])
    if hook:
        hook(root, dict(locals()))
    root.mainloop()


if __name__ == "__main__":
    a = sys.argv
    if len(a) >= 4 and a[1] == "--play":
        run_player(a[2], {**DEFAULTS, **json.loads(a[3])})
    elif len(a) >= 7 and a[1] == "--export":
        run_export(a[2], a[3], {**DEFAULTS, **json.loads(a[4])}, a[5], a[6])
    else:
        run_launcher()
