"""ARC's desktop shell — the menu bar orb and the floating panel.

Separate from ``arc/interface/`` because it is a different kind of thing: that package is
an application window you open, this is something that lives on the machine and is
summoned. They share the server, the conversations, and nothing else.
"""

from __future__ import annotations

from arc.desktop.app import available, run

__all__ = ["available", "run"]
