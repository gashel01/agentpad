"""Radial menu window, drawn by hand -- real per-pixel transparency.

pywebview (WebView2) can't do a transparent window on Windows: the page is transparent, but
the window stays an opaque square, and WebView2 draws via DirectComposition, which ignores any
clipping (SetWindowRgn, color key, layered window: all tried, none work). Here every frame is
drawn with PIL then handed to a layered window (UpdateLayeredWindow, premultiplied alpha):
smooth edges, a halo that blends into the desktop, no square.

The window lives on its own thread (whoever creates it must pump its messages). The controller
(widget.py) calls set_list / set_hover / set_cursor / show / hide from any thread:
they only set the state; the loop redraws when needed.
"""
from __future__ import annotations

import ctypes
import math
import sys
import threading
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---- geometry (logical pixels, at 96 dpi) ----------------------------------
SIZE = 460                  # window: the 380 scene + the halo margin
C = SIZE / 2                # center
NODE_R = 134                # item radius
CURSOR_R = 150              # max cursor travel
NODE_W, NODE_H = 104, 62
SS = 2                      # background supersampling (smooth edges)

ACCENT = (91, 140, 255)
CARD, CARD_HOVER = (32, 35, 43, 238), (42, 49, 66, 242)
BORDER = (58, 65, 80, 255)
RING = (45, 49, 59, 170)
TEXT, FAINT = (243, 244, 247, 255), (107, 115, 133, 255)
DOT = {"running": ACCENT, "waiting": (255, 176, 32), "error": (255, 93, 93), "done": (58, 209, 122),
       "attention": (255, 214, 0), "finished": (58, 209, 122)}


_FALLBACK_REG = ["/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc",
                 "/Library/Fonts/Arial.ttf", "DejaVuSans.ttf"]
_FALLBACK_BOLD = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
                  "/System/Library/Fonts/Helvetica.ttc", "DejaVuSans-Bold.ttf"]


def _font(names, px):
    bold = names[0] in ("segoeuib.ttf", "seguisb.ttf")
    for n in list(names) + (_FALLBACK_BOLD if bold else _FALLBACK_REG):
        try:
            return ImageFont.truetype(n, max(1, round(px)))
        except OSError:
            pass
    return ImageFont.load_default()


def _wrap(draw, text, font, maxw, max_lines):
    """Splits `text` into lines of width maxw; the last one ends with "…" if it overflows."""
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= maxw:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _fit(draw, lines[-1] + " …", font, maxw)
    return [_fit(draw, l, font, maxw) for l in lines]


def _fit(draw, text, font, maxw):
    if draw.textlength(text, font=font) <= maxw:
        return text
    while text and draw.textlength(text + "…", font=font) > maxw:
        text = text[:-1]
    return text + "…"


def _radial(size, fn):
    """L image of size `size` whose every pixel is fn(distance to center in logical px)."""
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    k = size / SIZE
    r = np.hypot(x - size / 2 + 0.5, y - size / 2 + 0.5) / k
    return Image.fromarray(np.clip(fn(r), 0, 255).astype(np.uint8), "L")


def _layer(size, rgb, alpha_L):
    im = Image.new("RGBA", (size, size), rgb + (0,))
    im.putalpha(alpha_L)
    return im


def node_centers(n):
    """Item 0 at the top, clockwise -- same angle calculation as widget.py."""
    out = []
    for i in range(n):
        a = math.radians(90 - i * 360 / n)
        out.append((C + NODE_R * math.cos(a), C - NODE_R * math.sin(a)))
    return out


DEFAULT_HINT = "Confirm: select · Cancel: close"
_STATIC = {}


def _static(scale):
    """Background, halo and rings: only depend on scale -> computed once."""
    if scale in _STATIC:
        return _STATIC[scale]
    S = round(SIZE * scale)
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # dark disc with a sharp edge (readable on any desktop) + blue halo inside
    im.alpha_composite(_layer(S, (12, 14, 20), _radial(S, lambda r: 236 * np.clip((204 - r) / 3, 0, 1))))
    im.alpha_composite(_layer(S, ACCENT, _radial(S, lambda r: 255 * 0.16 * np.clip(1 - r / (0.68 * C), 0, 1))))
    k = scale * SS
    lines = Image.new("RGBA", (round(SIZE * k),) * 2, (0, 0, 0, 0))
    d = ImageDraw.Draw(lines)
    P = lambda v: v * k                                         # noqa: E731
    lw = max(1, round(k))
    d.ellipse([P(C - 204), P(C - 204), P(C + 204), P(C + 204)], outline=(70, 78, 98, 150), width=lw)
    r = 156
    d.ellipse([P(C - r), P(C - r), P(C + r), P(C + r)], outline=RING, width=lw)
    r, ring_in = 75, (*RING[:3], int(RING[3] * 0.6))
    for j in range(0, 48, 2):
        d.arc([P(C - r), P(C - r), P(C + r), P(C + r)], j * 7.5, j * 7.5 + 4.5, fill=ring_in, width=lw)
    im.alpha_composite(lines.resize((S, S), Image.LANCZOS))
    _STATIC[scale] = im
    return im


def _draw_card(it, z, hov, k):
    """An item card in its own small image (rendered at k, with margin for the shadow).
    Returns (image, half-size in logical px, "running" dot position relative to center)."""
    m = 26                                                     # logical margin for shadow / glow
    w, h = NODE_W * z / 2, NODE_H * z / 2
    W, H = round((2 * w + 2 * m) * k), round((2 * h + 2 * m) * k)
    cx, cy = W / 2, H / 2
    P = lambda v: v * k                                         # noqa: E731
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dy = 10 if hov else 8
    ImageDraw.Draw(sh).rounded_rectangle([cx - P(w), cy - P(h) + P(dy), cx + P(w), cy + P(h) + P(dy)], radius=P(14),
                                         fill=(*ACCENT, 110) if hov else (0, 0, 0, 110))
    im = sh.filter(ImageFilter.GaussianBlur(P(9)))
    top = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(top)
    lw = max(1, round(k))
    b = [cx - P(w), cy - P(h), cx + P(w), cy + P(h)]
    if hov:
        g = P(1.5)
        d.rounded_rectangle([b[0] - g, b[1] - g, b[2] + g, b[3] + g], radius=P(15.5), outline=(*ACCENT, 140), width=lw)
    d.rounded_rectangle(b, radius=P(14 * z), fill=CARD_HOVER if hov else CARD,
                        outline=(*ACCENT, 255) if hov else BORDER, width=max(1, round(2 * k)))
    sub = it.get("sub") or ""
    ty = -(17 if sub else 11) * z
    status = "done" if it.get("active") else it.get("status")
    dr = 4 * z
    d.ellipse([cx - P(dr), cy + P(ty - dr), cx + P(dr), cy + P(ty + dr)], fill=(*DOT.get(status, FAINT[:3]), 255))
    fl = _font(["seguisb.ttf", "segoeuib.ttf", "segoeui.ttf"], 11.5 * k * z)
    d.text((cx, cy + P(ty + 13 * z)), _fit(d, str(it.get("label", "")), fl, P((NODE_W - 18) * z)), font=fl, fill=TEXT, anchor="mm")
    if sub:
        fs = _font(["segoeui.ttf"], 9 * k * z)
        d.text((cx, cy + P(ty + 27 * z)), _fit(d, sub, fs, P((NODE_W - 18) * z)), font=fs, fill=FAINT, anchor="mm")
    if it.get("more"):
        fc = _font(["segoeuib.ttf", "segoeui.ttf"], 13 * k * z)
        d.text((cx + P(w - 9 * z), cy), "›", font=fc, fill=(*ACCENT, 255) if hov else FAINT, anchor="mm")
    im.alpha_composite(top)
    return im, (w + m, h + m), ((0, ty, dr) if status == "running" else None)


def _paste(dst, card, center, half, scale):
    """Downscales a card rendered at SS and composites it centered on `center` (logical px)."""
    sm = card.resize((round(2 * half[0] * scale), round(2 * half[1] * scale)), Image.LANCZOS)
    dst.alpha_composite(sm, (round((center[0] - half[0]) * scale), round((center[1] - half[1]) * scale)))


_LAYOUT = {}


def _layout(mode, items, scale, hint=DEFAULT_HINT):
    """The whole menu without hover: background, separators, resting cards, title. Cached as long as
    the list doesn't change -- a hover then only redraws one card."""
    key = (mode, repr(items), scale, hint)
    if key in _LAYOUT:
        return _LAYOUT[key]
    S = round(SIZE * scale)
    im = _static(scale).copy()
    k = scale * SS
    SK = round(SIZE * k)
    P = lambda v: v * k                                         # noqa: E731
    n = len(items)
    centers = node_centers(n)
    sharp = Image.new("RGBA", (SK, SK), (0, 0, 0, 0))
    d = ImageDraw.Draw(sharp)
    lw = max(1, round(k))
    # separators between items: invisible at the center and at the end, solid two-thirds out
    for i in range(n if n > 1 else 0):
        a = math.radians(90 - (i + 0.5) * 360 / n)
        steps = 40
        for st in range(steps):
            t0, t1 = st / steps, (st + 1) / steps
            t = (t0 + t1) / 2
            f = t / 0.65 if t < 0.65 else max(0.0, (0.9 - t) / 0.25)
            if f <= 0:
                continue
            d.line([(P(C + 156 * t0 * math.cos(a)), P(C - 156 * t0 * math.sin(a))),
                    (P(C + 156 * t1 * math.cos(a)), P(C - 156 * t1 * math.sin(a)))],
                   fill=(*RING[:3], int(RING[3] * f)), width=lw)
    # mode title (blue glow behind it) + help
    title = (mode or "").upper()

    def spaced(dr, fill, kk):
        ft = _font(["segoeuib.ttf", "segoeui.ttf"], 13 * kk)
        tw = sum(dr.textlength(ch, font=ft) for ch in title) + 1.3 * kk * max(0, len(title) - 1)
        xx = C * kk - tw / 2
        for ch in title:
            dr.text((xx, (C - 10) * kk), ch, font=ft, fill=fill, anchor="lm")
            xx += dr.textlength(ch, font=ft) + 1.3 * kk

    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    spaced(ImageDraw.Draw(glow), (*ACCENT, 210), scale)
    im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(6 * scale)))
    spaced(d, TEXT, k)
    f_hint = _font(["segoeui.ttf"], 10 * k)
    if any(it.get("detail") for it in items):
        # the question at the top of the center; the pointed item's full text is added below
        lines = [l for part in hint.split(" · ") for l in _wrap(d, part, f_hint, P(140), 2)][:2]
        for j, line in enumerate(lines):
            d.text((P(C), P(C + 8 + 13 * j)), line, font=f_hint, fill=FAINT, anchor="mm")
    else:
        d.text((P(C), P(C + 8)), "joystick: point", font=f_hint, fill=FAINT, anchor="mm")
        for j, line in enumerate(hint.split(" · ")):
            d.text((P(C), P(C + 23 + 15 * j)), line, font=f_hint, fill=FAINT, anchor="mm")
    if not n:
        d.text((P(C), P(C + 52)), "Nothing here", font=f_hint, fill=FAINT, anchor="mm")
    im.alpha_composite(sharp.resize((S, S), Image.LANCZOS))
    dots = []
    for i, it in enumerate(items):
        card, half, dot = _draw_card(it, 1.0, False, k)
        _paste(im, card, centers[i], half, scale)
        if dot:
            dots.append((centers[i][0], centers[i][1] + dot[1], dot[2]))
    _LAYOUT.clear()
    _LAYOUT[key] = (im, dots)
    return im, dots


def render_base(mode, items, hover, scale, hint=DEFAULT_HINT):
    """Everything except the cursor and the pulsing of "running" dots: the resting menu (cached)
    + the hovered card, enlarged, placed on top (it fully covers its resting version)."""
    base, dots = _layout(mode, items, scale, hint)
    if not (0 <= hover < len(items)):
        return base, dots
    im = base.copy()
    c = node_centers(len(items))[hover]
    card, half, dot = _draw_card(items[hover], 1.12, True, scale * SS)
    _paste(im, card, c, half, scale)
    dots = [p for p in dots if math.hypot(p[0] - c[0], p[1] - c[1]) > 20]
    if dot:
        dots.append((c[0], c[1] + dot[1], dot[2]))
    detail = items[hover].get("detail")
    if detail:
        # full text of the pointed item, under the question (rendered on a band, at SS then downscaled)
        k = scale * SS
        bw, bh = 124, 32
        band = Image.new("RGBA", (round(bw * k), round(bh * k)), (0, 0, 0, 0))
        bd = ImageDraw.Draw(band)
        f = _font(["seguisb.ttf", "segoeui.ttf"], 11 * k)
        for j, line in enumerate(_wrap(bd, detail, f, bw * k, 2)):
            bd.text((bw * k / 2, (8 + 15 * j) * k), line, font=f, fill=TEXT, anchor="mm")
        im.alpha_composite(band.resize((round(bw * scale), round(bh * scale)), Image.LANCZOS),
                           (round((C - bw / 2) * scale), round((C + 26) * scale)))
    return im, dots


def _sprite(diam, rgb, alpha, glow, scale):
    """Disc (+ glow) centered in a small image, at the final scale."""
    k = scale * 4
    pad = 16
    S = round((diam + 2 * pad) * k)
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    c = S / 2
    if glow:
        g = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        gr = (diam / 2 + 2) * k
        ImageDraw.Draw(g).ellipse([c - gr, c - gr, c + gr, c + gr], fill=(*rgb, 190))
        im.alpha_composite(g.filter(ImageFilter.GaussianBlur(6 * k)))
    r = diam / 2 * k
    ImageDraw.Draw(im).ellipse([c - r, c - r, c + r, c + r], fill=(*rgb, alpha))
    return im.resize((round((diam + 2 * pad) * scale),) * 2, Image.LANCZOS)


if sys.platform == "win32":
    from ctypes import wintypes

    # ---- Win32 -------------------------------------------------------------------
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32
    # 64-bit handles: without restype, ctypes truncates them to a 32-bit int (random access violation)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClassExW.restype = wintypes.ATOM
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p, wintypes.HDC,
                                           ctypes.c_void_p, wintypes.COLORREF, ctypes.c_void_p, wintypes.DWORD]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.PeekMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.MsgWaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, wintypes.DWORD]
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.HANDLE, wintypes.DWORD]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]


    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


    WS_POPUP = 0x80000000
    WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOPMOST = 0x80000, 0x20, 0x8
    WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x08000000
    SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
    ULW_ALPHA, AC_SRC_ALPHA = 2, 1
    PM_REMOVE, QS_ALLINPUT = 1, 0x04FF


FADE_S = 0.16


class RadialWindow:
    """Radial menu on screen (Win32 window; macOS: RadialWindowMac in radial_mac.py). All public methods are safe from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self.mode, self.items, self.hover, self.hint = "agents", [], 0, DEFAULT_HINT
        self._cursor = [0.0, 0.0]          # target (logical units from the center)
        self._cur_draw = [0.0, 0.0]        # displayed position (smoothed)
        self._centered = True
        self._want_visible = False
        self._shown_at = 0.0
        self._base_dirty = True
        self._frame_dirty = True
        self._ready = threading.Event()

    # ---- controller API ----
    def set_list(self, mode, items, hover, hint=DEFAULT_HINT):
        with self._lock:
            self.mode, self.items, self.hover, self.hint = mode, list(items), hover, hint
            self._base_dirty = True

    def set_hover(self, i):
        with self._lock:
            if i != self.hover:
                self.hover = i
                self._base_dirty = True

    def set_cursor(self, nx, ny):
        with self._lock:
            self._cursor = [nx * CURSOR_R, -ny * CURSOR_R]
            self._centered = math.hypot(nx, ny) < 0.22
            self._frame_dirty = True

    def show(self):
        with self._lock:
            if not self._want_visible:
                self._want_visible = True
                self._shown_at = 0.0       # the thread sets the fade start and the position
                self._cursor = [0.0, 0.0]
                self._cur_draw = [0.0, 0.0]
                self._centered = True
                self._base_dirty = True

    def hide(self):
        with self._lock:
            self._want_visible = False

    # ---- window thread ----
    def run(self):
        """Blocking: creates the window and pumps its messages (run it on the main thread)."""
        try:
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # per-monitor, v2
        except Exception:
            pass
        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._wndproc,
                         hInstance=hinst, lpszClassName="agentpad_radial")
        user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "agentpad_radial", "agentpad — radial", WS_POPUP, 0, 0, 1, 1, None, None, hinst, None)
        self._memdc = gdi32.CreateCompatibleDC(None)
        self._dib, self._bits, self._dib_px = None, None, 0
        self._scale, self._pos = 1.0, (0, 0)
        self._base, self._dots, self._sprites = None, [], {}
        self._visible = False
        self._ready.set()
        msg = wintypes.MSG()
        while True:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            try:
                self._tick()
            except Exception:
                pass
            user32.MsgWaitForMultipleObjects(0, None, False, 16, QS_ALLINPUT)

    def _place(self):
        """Screen under the mouse: centered on its work area, at its scale."""
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        mon = user32.MonitorFromPoint(pt, 2)
        mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(mon, ctypes.byref(mi))
        dpi_x, dpi_y = wintypes.UINT(96), wintypes.UINT(96)
        try:
            ctypes.windll.shcore.GetDpiForMonitor(wintypes.HMONITOR(mon), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        except Exception:
            pass
        scale = dpi_x.value / 96
        if scale != self._scale:
            self._scale, self._sprites = scale, {}
        px = round(SIZE * scale)
        wr = mi.rcWork
        self._pos = ((wr.left + wr.right - px) // 2, (wr.top + wr.bottom - px) // 2)

    def _ensure_dib(self, px):
        if self._dib_px == px:
            return
        if self._dib:
            gdi32.DeleteObject(self._dib)
        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=px, biHeight=-px,
                               biPlanes=1, biBitCount=32, biCompression=0)
        bits = ctypes.c_void_p()
        self._dib = gdi32.CreateDIBSection(self._memdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        gdi32.SelectObject(self._memdc, self._dib)
        self._bits, self._dib_px = bits, px

    def _sprite(self, key):
        if key not in self._sprites:
            if key == "cursor":
                self._sprites[key] = _sprite(14, ACCENT, 255, True, self._scale)
            elif key == "cursor_c":
                self._sprites[key] = _sprite(14, FAINT[:3], 115, False, self._scale)
            else:
                self._sprites[key] = _sprite(key[1] * 2, ACCENT, 255, True, self._scale)
        return self._sprites[key]

    def _tick(self):
        with self._lock:
            want = self._want_visible
            if want and not self._visible and self._shown_at == 0.0:
                self._place()
                self._shown_at = time.monotonic()
            base_dirty, self._base_dirty = self._base_dirty, False
            frame_dirty, self._frame_dirty = self._frame_dirty, False
            mode, items, hover, hint = self.mode, self.items, self.hover, self.hint
            target, centered = list(self._cursor), self._centered
        if not want:
            if self._visible:
                self._native_hide()
                self._visible = False
            return
        if base_dirty or self._base is None:
            self._base, self._dots = render_base(mode, items, hover, self._scale, hint)
        # smoothed cursor (~80 ms), opening fade, pulsing of working sessions
        cd = self._cur_draw
        moving = math.hypot(target[0] - cd[0], target[1] - cd[1]) > 0.3
        cd[0] += (target[0] - cd[0]) * 0.45
        cd[1] += (target[1] - cd[1]) * 0.45
        fade = min(1.0, (time.monotonic() - self._shown_at) / FADE_S)
        if not (base_dirty or frame_dirty or moving or fade < 1 or self._dots or not self._visible):
            return
        frame = self._base.copy()
        s = self._scale
        pulse = 0.35 + 0.65 * (0.5 + 0.5 * math.cos(time.monotonic() * 2 * math.pi / 1.2))
        for x, y, dr in self._dots:
            sp = self._sprite(("dot", round(dr, 2)))
            if pulse < 1:
                sp = sp.copy()
                sp.putalpha(sp.getchannel("A").point(lambda a: int(a * pulse)))
            frame.alpha_composite(sp, (round(x * s - sp.width / 2), round(y * s - sp.height / 2)))
        cs = self._sprite("cursor_c" if centered else "cursor")
        frame.alpha_composite(cs, (round((C + cd[0]) * s - cs.width / 2), round((C + cd[1]) * s - cs.height / 2)))
        self._present(frame, int(255 * (1 - (1 - fade) ** 2)))

    def _native_hide(self):
        user32.ShowWindow(self.hwnd, SW_HIDE)

    def _present(self, img, alpha):
        px = img.width
        self._ensure_dib(px)
        a = np.asarray(img, dtype=np.uint16)
        pm = np.empty((px, px, 4), dtype=np.uint8)
        al = a[..., 3]
        pm[..., 0] = a[..., 2] * al // 255     # premultiplied BGRA
        pm[..., 1] = a[..., 1] * al // 255
        pm[..., 2] = a[..., 0] * al // 255
        pm[..., 3] = al
        ctypes.memmove(self._bits, pm.ctypes.data, pm.nbytes)
        pt_dst = wintypes.POINT(*self._pos)
        size = wintypes.SIZE(px, px)
        pt_src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, alpha, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(pt_dst), ctypes.byref(size), self._memdc,
                                   ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA)
        if not self._visible:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            self._visible = True
