"""
Browser Automation Engine Package.

Provides Chrome Driver lifecycle management, safe DOM actions,
and DOM health inspection tools.
"""

from .driver_manager import (
    clean_chrome_locks,
    clear_chrome_session,
    clear_driver_session,
    free_port,
    build_chrome_options,
    apply_stealth_cdp,
    get_driver,
)
from .dom_actions import (
    DOMActionError,
    DOMSelectorError,
    load_selectors,
    find_element_with_fallback,
    safe_click,
    inject_text_native_dom,
    attach_media_files,
)
from .dom_inspector import (
    DOMStatus,
    ElementHealth,
    ComponentHealth,
    DOMHealthReport,
    DOMInspector,
)

__all__ = [
    "clean_chrome_locks",
    "clear_chrome_session",
    "clear_driver_session",
    "free_port",
    "build_chrome_options",
    "apply_stealth_cdp",
    "get_driver",
    "DOMActionError",
    "DOMSelectorError",
    "load_selectors",
    "find_element_with_fallback",
    "safe_click",
    "inject_text_native_dom",
    "attach_media_files",
    "DOMStatus",
    "ElementHealth",
    "ComponentHealth",
    "DOMHealthReport",
    "DOMInspector",
]
