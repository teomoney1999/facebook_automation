"""
DOM Health Inspector & Canary Check Module.

Provides automated scanning, classification (PASS, WARN, BROKEN),
and drift reporting for Facebook DOM components without side-effects.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .dom_actions import load_selectors

logger = logging.getLogger("FacebookBot.DOMInspector")


class DOMStatus(str, Enum):
    """Health classification for a UI component selector chain."""
    PASS = "PASS"      # Primary selector matched successfully
    WARN = "WARN"      # Primary selector failed, but a fallback selector matched
    BROKEN = "BROKEN"  # All primary and fallback selectors failed


# Alias for compatibility with ElementHealth terminology
ElementHealth = DOMStatus


@dataclass
class ComponentHealth:
    """Diagnostic health result for an individual component."""
    name: str
    status: DOMStatus
    matched_index: Optional[int] = None
    matched_selector: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def is_healthy(self) -> bool:
        return self.status != DOMStatus.BROKEN


@dataclass
class DOMHealthReport:
    """Aggregated health report across examined components."""
    components: Dict[str, ComponentHealth] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    @property
    def passed(self) -> List[ComponentHealth]:
        return [c for c in self.components.values() if c.status == DOMStatus.PASS]

    @property
    def warned(self) -> List[ComponentHealth]:
        return [c for c in self.components.values() if c.status == DOMStatus.WARN]

    @property
    def broken(self) -> List[ComponentHealth]:
        return [c for c in self.components.values() if c.status == DOMStatus.BROKEN]

    @property
    def is_healthy(self) -> bool:
        """True if no components are completely broken."""
        return len(self.broken) == 0

    @property
    def all_passed(self) -> bool:
        """True if every component matched its primary selector without warnings."""
        return len(self.warned) == 0 and len(self.broken) == 0

    def format_table(self) -> str:
        """Generates an ASCII diagnostic table representation of the report."""
        lines = [
            f"=== DOM HEALTH REPORT ({self.timestamp}) ===",
            f"{'Component':<30} | {'Status':<6} | {'Match':<12} | {'Selector'}",
            "-" * 80,
        ]
        for name, comp in self.components.items():
            idx_str = f"index {comp.matched_index}" if comp.matched_index is not None else "None"
            sel_str = comp.matched_selector or (comp.error_message or "N/A")
            lines.append(f"{name:<30} | {comp.status.value:<6} | {idx_str:<12} | {sel_str}")

        lines.append("-" * 80)
        lines.append(
            f"Summary: {len(self.passed)} PASS, {len(self.warned)} WARN, {len(self.broken)} BROKEN. "
            f"System Health: {'HEALTHY' if self.is_healthy else 'CRITICAL'}"
        )
        return "\n".join(lines)


SITUATIONAL_COMPONENTS = {
    "create_post_modal",
    "post_button",
    "action_blocked_dialog",
    "pending_approval_notice",
    "marketplace_discussion_tab",
    "email_input",
    "password_input",
    "login_button",
}


class DOMInspector:
    """
    Automated inspector for evaluating selector resilience and detecting DOM drift.
    """

    def __init__(
        self,
        selectors: Optional[Dict[str, List[str]]] = None,
        selectors_file: Optional[Union[str, Path]] = None,
        artifacts_dir: Optional[Union[str, Path]] = None,
    ):
        if selectors is not None:
            self.selectors = selectors
        elif selectors_file is not None:
            self.selectors = load_selectors(selectors_file)
        else:
            default_path = Path(__file__).resolve().parent.parent / "json" / "selectors.json"
            self.selectors = load_selectors(default_path)

        if artifacts_dir is not None:
            self.artifacts_dir = Path(artifacts_dir).resolve()
        else:
            self.artifacts_dir = Path(__file__).resolve().parent.parent / "logs" / "dom_drift"

    def inspect_component(
        self,
        driver: WebDriver,
        component_name: str,
        selectors: List[str],
        timeout: float = 2.0,
    ) -> ComponentHealth:
        """
        Inspects an individual component's selector chain against the current page DOM.

        Args:
            driver: Active Selenium WebDriver.
            component_name: Name of the component.
            selectors: Ordered list of XPath selectors (Primary at index 0).
            timeout: Maximum seconds to wait per selector attempt.

        Returns:
            ComponentHealth diagnostic result.
        """
        if not selectors:
            return ComponentHealth(
                name=component_name,
                status=DOMStatus.BROKEN,
                error_message="Empty selector list provided",
            )

        # 1. Test Primary selector (index 0)
        primary_xpath = selectors[0]
        try:
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.XPATH, primary_xpath))
            )
            return ComponentHealth(
                name=component_name,
                status=DOMStatus.PASS,
                matched_index=0,
                matched_selector=primary_xpath,
            )
        except (TimeoutException, NoSuchElementException):
            logger.debug("Primary selector for '%s' failed. Checking %d fallbacks...", component_name, len(selectors) - 1)
        except Exception as err:
            logger.debug("Unexpected error checking primary selector for '%s': %s", component_name, err)

        # 2. Test Fallback selectors (index 1..n-1)
        for idx in range(1, len(selectors)):
            fallback_xpath = selectors[idx]
            try:
                WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.XPATH, fallback_xpath))
                )
                logger.warning(
                    "DOM DRIFT DETECTED: Component '%s' passed on fallback selector [%d]: '%s'",
                    component_name,
                    idx,
                    fallback_xpath,
                )
                return ComponentHealth(
                    name=component_name,
                    status=DOMStatus.WARN,
                    matched_index=idx,
                    matched_selector=fallback_xpath,
                )
            except (TimeoutException, NoSuchElementException):
                continue
            except Exception as err:
                logger.debug("Fallback selector [%d] for '%s' error: %s", idx, component_name, err)
                continue

        # 3. All failed -> BROKEN
        logger.error("COMPONENT BROKEN: All %d selectors failed for '%s'.", len(selectors), component_name)
        return ComponentHealth(
            name=component_name,
            status=DOMStatus.BROKEN,
            matched_index=None,
            matched_selector=None,
            error_message=f"All {len(selectors)} selectors failed to locate element",
        )

    def inspect_page(
        self,
        driver: WebDriver,
        component_names: Optional[List[str]] = None,
        timeout: float = 2.0,
        capture_artifacts_on_broken: bool = True,
        check_situational: bool = True,
    ) -> DOMHealthReport:
        """
        Scans current page DOM against configured components and produces a health report.

        Args:
            driver: Active Selenium WebDriver.
            component_names: Optional subset of component names to inspect.
            timeout: Timeout per selector check.
            capture_artifacts_on_broken: If True, saves screenshot & page source on any BROKEN component.
            check_situational: If False and component_names is None, filters out situational modal components.

        Returns:
            DOMHealthReport instance.
        """
        if component_names is not None:
            target_keys = component_names
        elif check_situational:
            target_keys = list(self.selectors.keys())
        else:
            # Filter out situational components that are not present on a standard static page
            target_keys = [k for k in self.selectors.keys() if k not in SITUATIONAL_COMPONENTS]
            if not target_keys:
                target_keys = list(self.selectors.keys())

        report = DOMHealthReport()

        for name in target_keys:
            if name not in self.selectors:
                report.components[name] = ComponentHealth(
                    name=name,
                    status=DOMStatus.BROKEN,
                    error_message=f"Component '{name}' not found in registry",
                )
                continue

            health = self.inspect_component(
                driver=driver,
                component_name=name,
                selectors=self.selectors[name],
                timeout=timeout,
            )
            report.components[name] = health

        if not report.is_healthy and capture_artifacts_on_broken:
            self.save_drift_artifact(driver, report.broken)

        return report

    def save_drift_artifact(self, driver: WebDriver, broken_components: List[ComponentHealth]) -> Optional[Path]:
        """
        Saves diagnostic screenshot and HTML snippet for broken components.
        """
        try:
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = self.artifacts_dir / f"drift_{timestamp}.png"
            html_path = self.artifacts_dir / f"drift_{timestamp}.html"

            driver.save_screenshot(str(screenshot_path))
            html_path.write_text(driver.page_source, encoding="utf-8")

            broken_names = [c.name for c in broken_components]
            logger.info(
                "Captured drift diagnostic artifacts for broken components %s at: %s",
                broken_names,
                self.artifacts_dir,
            )
            return self.artifacts_dir
        except Exception as err:
            logger.warning("Failed to save drift diagnostic artifact: %s", err)
            return None
