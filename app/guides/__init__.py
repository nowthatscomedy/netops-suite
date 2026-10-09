from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.guides.catalog import GuideCatalog, GuideEntry

if TYPE_CHECKING:
    from app.guides.dialog import GuideDialog
    from app.guides.quick_help import QuickHelpPanel


def __getattr__(name: str) -> Any:
    if name == "GuideDialog":
        from app.guides.dialog import GuideDialog

        return GuideDialog
    if name == "QuickHelpPanel":
        from app.guides.quick_help import QuickHelpPanel

        return QuickHelpPanel
    raise AttributeError(name)

__all__ = ["GuideCatalog", "GuideDialog", "GuideEntry", "QuickHelpPanel"]
