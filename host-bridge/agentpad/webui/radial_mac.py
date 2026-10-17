"""macOS: the radial menu window (PyObjC / Cocoa). The drawing is the same as Windows's
(radial_native.render_base, PIL); only the window changes: borderless NSWindow, transparent
background, above the others, which ignores the mouse and never takes focus. Images
are rendered at screen scale (2x on Retina) and displayed in points.

The Cocoa loop (NSApplication.run) runs on the main thread; a timer calls
_tick on it at 60 Hz. The controller's set_* calls arrive from other threads, under lock, same as on Windows.
"""
from __future__ import annotations

import io

import objc
from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered, NSColor,
                    NSEvent, NSImage, NSImageScaleAxesIndependently, NSImageView, NSScreen, NSStatusWindowLevel,
                    NSWindow)
from Foundation import NSData, NSMakeRect, NSMakeSize, NSObject, NSPointInRect, NSTimer

from agentpad.webui.radial_native import SIZE, RadialWindow

_BORDERLESS = 0
_ALL_SPACES = 1 << 0             # NSWindowCollectionBehaviorCanJoinAllSpaces
_FULLSCREEN_AUX = 1 << 8         # NSWindowCollectionBehaviorFullScreenAuxiliary


class _Ticker(NSObject):
    def initWithOwner_(self, owner):
        self = objc.super(_Ticker, self).init()
        if self is not None:
            self.owner = owner
        return self

    def tick_(self, _timer):
        try:
            self.owner._tick()
        except Exception:
            pass


class RadialWindowMac(RadialWindow):
    def run(self):
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)    # no icon in the Dock
        rect = NSMakeRect(0, 0, SIZE, SIZE)
        w = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(rect, _BORDERLESS, NSBackingStoreBuffered, False)
        w.setOpaque_(False)
        w.setBackgroundColor_(NSColor.clearColor())
        w.setHasShadow_(False)
        w.setLevel_(NSStatusWindowLevel)
        w.setIgnoresMouseEvents_(True)
        w.setCollectionBehavior_(_ALL_SPACES | _FULLSCREEN_AUX)
        view = NSImageView.alloc().initWithFrame_(rect)
        view.setImageScaling_(NSImageScaleAxesIndependently)
        w.setContentView_(view)
        self._win, self._view = w, view
        self._scale, self._pos = 1.0, (0, 0)
        self._base, self._dots, self._sprites = None, [], {}
        self._visible = False
        self._ticker = _Ticker.alloc().initWithOwner_(self)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1 / 60, self._ticker, "tick:", None, True)
        self._ready.set()
        app.run()

    def _place(self):
        """Screen under the mouse: centered on its visible area (without the menu bar or the Dock)."""
        mouse = NSEvent.mouseLocation()
        scr = next((s for s in NSScreen.screens() if NSPointInRect(mouse, s.frame())), NSScreen.mainScreen())
        scale = float(scr.backingScaleFactor())
        if scale != self._scale:
            self._scale, self._sprites = scale, {}
        v = scr.visibleFrame()
        x = v.origin.x + (v.size.width - SIZE) / 2
        y = v.origin.y + (v.size.height - SIZE) / 2
        self._pos = (x, y)
        self._win.setFrame_display_(NSMakeRect(x, y, SIZE, SIZE), False)

    def _present(self, img, alpha):
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=1)
        raw = buf.getvalue()
        ns = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(raw, len(raw)))
        ns.setSize_(NSMakeSize(SIZE, SIZE))          # in points: Retina displays the extra pixels
        self._view.setImage_(ns)
        self._win.setAlphaValue_(alpha / 255.0)
        if not self._visible:
            self._win.orderFrontRegardless()         # in front, without activating the application
            self._visible = True

    def _native_hide(self):
        self._win.orderOut_(None)
