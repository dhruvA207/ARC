"""The orb in the menu bar, and the controls behind it.

This is what makes ARC feel installed rather than launched: there is no Dock icon and no
window to find, just the mark sitting in the menu bar, and everything administrative
hangs off clicking it.

The icon is drawn rather than shipped as a file — the same broken ring the web UI uses,
as a template image so macOS tints it correctly in light mode, dark mode, and when the
menu bar item is highlighted.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from arc.log import get_logger

_log = get_logger(__name__)

ICON_SIZE = 18.0


def _icon() -> Any:
    """Draw the ARC mark: a ring with a gap, rotated, as a template image."""
    import AppKit
    from Foundation import NSMakeRect, NSMakeSize

    image = AppKit.NSImage.alloc().initWithSize_(NSMakeSize(ICON_SIZE, ICON_SIZE))
    image.lockFocus()

    inset = 2.5
    rect = NSMakeRect(inset, inset, ICON_SIZE - inset * 2, ICON_SIZE - inset * 2)
    path = AppKit.NSBezierPath.bezierPath()
    # 300° of arc, leaving the gap that makes it read as ARC's mark rather than as a
    # plain circle. Angles are anticlockwise from east in AppKit.
    path.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
        AppKit.NSMakePoint(ICON_SIZE / 2, ICON_SIZE / 2),
        rect.size.width / 2,
        135.0,
        75.0,
    )
    path.setLineWidth_(1.9)
    path.setLineCapStyle_(AppKit.NSLineCapStyleRound)
    AppKit.NSColor.blackColor().set()
    path.stroke()

    image.unlockFocus()
    # Template means macOS recolours it for the menu bar rather than showing black on
    # black in dark mode.
    image.setTemplate_(True)
    return image


class MenuBar:
    """The status item and its menu."""

    def __init__(
        self,
        *,
        on_summon: Callable[[], None],
        on_toggle_mute: Callable[[], bool],
        on_open_web: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._on_summon = on_summon
        self._on_toggle_mute = on_toggle_mute
        self._on_open_web = on_open_web
        self._on_quit = on_quit
        self._item: Any = None
        self._mute_entry: Any = None
        self._muted = False
        self._delegate: Any = None

    def install(self) -> bool:
        """Put the orb in the menu bar. Returns False if AppKit is unavailable."""
        try:
            import AppKit
            import objc
            from Foundation import NSObject
        except Exception:
            _log.warning("menu bar unavailable: AppKit could not be imported")
            return False

        outer = self

        class _Target(NSObject):
            """Selector target. AppKit menu actions need an Objective-C object."""

            def summon_(self, _sender: object) -> None:
                outer._on_summon()

            def toggleMute_(self, _sender: object) -> None:  # noqa: N802 - ObjC selector
                outer._muted = outer._on_toggle_mute()
                outer._refresh_mute()

            def openWeb_(self, _sender: object) -> None:  # noqa: N802 - ObjC selector
                outer._on_open_web()

            def quit_(self, _sender: object) -> None:
                outer._on_quit()

        self._delegate = _Target.alloc().init()

        bar = AppKit.NSStatusBar.systemStatusBar()
        item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        item.button().setImage_(_icon())
        item.button().setToolTip_("ARC — double-tap ⌘ to summon")

        menu = AppKit.NSMenu.alloc().init()

        summon = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Summon ARC", objc.selector(self._delegate.summon_, signature=b"v@:@"), ""
        )
        summon.setTarget_(self._delegate)
        menu.addItem_(summon)

        self._mute_entry = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Mute", objc.selector(self._delegate.toggleMute_, signature=b"v@:@"), ""
        )
        self._mute_entry.setTarget_(self._delegate)
        menu.addItem_(self._mute_entry)

        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        web = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Open web version", objc.selector(self._delegate.openWeb_, signature=b"v@:@"), ""
        )
        web.setTarget_(self._delegate)
        menu.addItem_(web)

        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        quit_entry = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit ARC", objc.selector(self._delegate.quit_, signature=b"v@:@"), "q"
        )
        quit_entry.setTarget_(self._delegate)
        menu.addItem_(quit_entry)

        item.setMenu_(menu)
        self._item = item
        return True

    def _refresh_mute(self) -> None:
        if self._mute_entry is not None:
            self._mute_entry.setTitle_("Unmute" if self._muted else "Mute")

    def remove(self) -> None:
        if self._item is not None:
            try:
                import AppKit

                AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self._item)
            except Exception:
                pass
            self._item = None
