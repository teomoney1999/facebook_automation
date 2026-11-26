"""
Safe DOM Actions & Native JS Text Injection Module.

Provides robust DOM element location with multi-tier fallback lists,
anti-interception safe clicks, and native DOM text injection without
operating system clipboard dependencies.
"""

import json
import logging
import os
from pathlib import Path
import random
import sys
import time
from typing import Dict, List, Optional, Union

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger("FacebookBot.DOMActions")


class DOMActionError(Exception):
    """Base exception for DOM action failures."""
    pass


class DOMSelectorError(DOMActionError):
    """Raised when all primary and fallback selectors fail to match."""
    pass


INJECT_TEXT_SCRIPT = """
const el = arguments[0];
const text = arguments[1];

if (!el) {
    throw new Error("Target element is null or undefined.");
}

el.focus();

// 1. Value assignment for standard INPUT and TEXTAREA (compatible with React & standard DOM)
if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
    try {
        const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        if (desc && desc.set) {
            desc.set.call(el, text);
        } else {
            el.value = text;
        }
    } catch (e) {
        el.value = text;
    }
    try {
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    } catch (e2) {}
    return true;
}

// 2. Synthetic paste event with DataTransfer (preserves all exact newlines, formatting & emojis)
let pasteSuccess = false;
try {
    const dt = new DataTransfer();
    dt.setData('text/plain', text);
    const pasteEvt = new ClipboardEvent('paste', {
        clipboardData: dt,
        bubbles: true,
        cancelable: true
    });
    el.dispatchEvent(pasteEvt);
    // If Facebook Lexical handled the paste event, it calls preventDefault()
    if (pasteEvt.defaultPrevented || (el.innerText && el.innerText.trim().length > 0)) {
        pasteSuccess = true;
    }
} catch (e) {
    pasteSuccess = false;
}

// ONLY insert once: if synthetic paste was handled by Lexical/editor, stop immediately!
if (pasteSuccess) {
    try {
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    } catch (e) {}
    return true;
}

// 3. Fallback ONLY if paste event was NOT intercepted/handled (non-Lexical fallback)
try {
    const lines = text.split(/\\r\\n|\\r|\\n/);
    for (let i = 0; i < lines.length; i++) {
        if (lines[i].length > 0) {
            document.execCommand('insertText', false, lines[i]);
        }
        if (i < lines.length - 1) {
            const pOk = document.execCommand('insertParagraph', false, null);
            if (!pOk) {
                document.execCommand('insertLineBreak', false, null);
            }
        }
    }
} catch (e) {
    try {
        el.style.whiteSpace = 'pre-wrap';
        el.innerText = text;
    } catch (e2) {}
}

try {
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
} catch (e) {}

return true;
"""


def load_selectors(file_path: Union[str, Path]) -> Dict[str, List[str]]:
    """
    Loads and validates the selector registry from a JSON file.

    Args:
        file_path: Path to selectors.json.

    Returns:
        Dict mapping component name to list of XPath strings.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If JSON format is invalid or has empty selector lists.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Selector registry file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid selectors.json format: expected dict, got {type(data).__name__}")

    for comp, sel_list in data.items():
        if comp.startswith("_"):
            continue
        if not isinstance(sel_list, list) or len(sel_list) == 0:
            raise ValueError(f"Component '{comp}' in selectors.json must have a non-empty list of selectors.")

    return data


def find_element_with_fallback(
    driver: WebDriver,
    selectors: Union[List[str], str],
    timeout: float = 10.0,
    fallback_timeout: float = 2.0,
    component_name: str = "unknown",
    condition=None,
) -> WebElement:
    """
    Locates an element using a prioritized list of XPath selectors.

    Tries the Primary selector (index 0) first with `timeout`. If it fails,
    iterates through Fallback selectors with `fallback_timeout`. Emits a WARNING
    log if a fallback is matched to notify of DOM drift.

    Args:
        driver: Active Selenium WebDriver instance.
        selectors: Single XPath string or list of XPath selectors in priority order.
        timeout: Wait time in seconds for the primary selector.
        fallback_timeout: Wait time in seconds for each fallback selector.
        component_name: Semantic name of UI component for logging/diagnostics.
        condition: Optional Expected Condition factory (defaults to EC.presence_of_element_located).

    Returns:
        The matched WebElement.

    Raises:
        DOMSelectorError: If all selectors fail to locate an element.
    """
    if driver is None:
        raise DOMSelectorError("WebDriver instance cannot be None.")

    if isinstance(selectors, str):
        selector_list = [selectors]
    else:
        selector_list = list(selectors)

    if not selector_list:
        raise DOMSelectorError(f"No selectors provided for component '{component_name}'.")

    cond_factory = condition or EC.presence_of_element_located

    for idx, xpath in enumerate(selector_list):
        current_timeout = timeout if idx == 0 else fallback_timeout
        try:
            element = WebDriverWait(driver, current_timeout).until(
                cond_factory((By.XPATH, xpath))
            )
            if idx > 0:
                logger.warning(
                    "DOM DRIFT WARNING: Component '%s' matched fallback selector at index %d: '%s'. "
                    "Primary selector ('%s') failed and may be obsolete.",
                    component_name,
                    idx,
                    xpath,
                    selector_list[0],
                )
            else:
                logger.debug("Component '%s' matched primary selector: '%s'", component_name, xpath)
            return element
        except (TimeoutException, NoSuchElementException, StaleElementReferenceException):
            if idx == 0 and len(selector_list) > 1:
                logger.warning(
                    "Primary selector for component '%s' failed: '%s'. Attempting fallback selectors...",
                    component_name,
                    xpath,
                )
            continue
        except Exception as err:
            logger.debug("Selector '%s' raised unexpected exception: %s", xpath, err)
            continue

    raise DOMSelectorError(
        f"All {len(selector_list)} selector(s) for component '{component_name}' failed to find an element."
    )


def safe_click(driver: WebDriver, element: WebElement, timeout: float = 5.0) -> bool:
    """
    Safely clicks a WebElement by centering it in the viewport, attempting a
    native ActionChains click, and gracefully falling back to a JavaScript click
    if the element is intercepted or obscured.

    Args:
        driver: Active Selenium WebDriver instance.
        element: Target WebElement to click.
        timeout: Timeout parameter for compatibility.

    Returns:
        True if click was successfully dispatched.

    Raises:
        DOMActionError: If click could not be performed natively or via JS.
    """
    if driver is None:
        raise DOMActionError("WebDriver instance cannot be None.")
    if element is None:
        raise DOMActionError("Cannot click: element is None.")

    # Step 1: Scroll into center view
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});",
            element,
        )
    except Exception as err:
        logger.debug("Failed to scroll element into view: %s", err)

    # Step 2: Try native interaction
    try:
        ActionChains(driver).move_to_element(element).click().perform()
        return True
    except (ElementClickInterceptedException, WebDriverException) as err:
        logger.warning(
            "Native click failed or intercepted (%s). Falling back to JavaScript click.",
            type(err).__name__,
        )

    # Step 3: Fallback to direct JavaScript click
    try:
        driver.execute_script("arguments[0].click();", element)
        return True
    except Exception as js_err:
        raise DOMActionError(f"Both native and JavaScript clicks failed on element: {js_err}") from js_err


def inject_text_native_dom(
    driver: WebDriver,
    target_element: WebElement,
    text: str,
    strip_newlines: bool = False,
) -> bool:
    """
    Injects text directly into a DOM element (such as a Facebook contenteditable div,
    input, or textarea) preserving all newlines (\n), special characters, and emojis.

    Only strips newlines if explicitly requested via strip_newlines=True.

    Args:
        driver: Active Selenium WebDriver instance.
        target_element: Target WebElement receiving the text.
        text: String content to insert.
        strip_newlines: Whether to strip newlines (defaults to False; preserves formatting).

    Returns:
        True if text was injected successfully.

    Raises:
        DOMActionError: If text injection fails.
    """
    if driver is None:
        raise DOMActionError("WebDriver instance cannot be None.")
    if target_element is None:
        raise DOMActionError("Cannot inject text: target_element is None.")

    # Preserve all newlines and formatting unless user explicitly requested stripping
    effective_text = " ".join(text.splitlines()) if strip_newlines else text

    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});",
            target_element,
        )
        driver.execute_script(INJECT_TEXT_SCRIPT, target_element, effective_text)
        logger.debug("Successfully injected %d characters into element via native DOM JS.", len(effective_text))
        return True
    except Exception as err:
        raise DOMActionError(f"Failed to inject text into element via DOM: {err}") from err


def attach_media_files(
    driver: WebDriver,
    file_input_selectors: Union[List[str], str],
    photo_btn_selectors: Optional[Union[List[str], str]] = None,
    image_paths: Optional[List[str]] = None,
    timeout: float = 15.0,
) -> bool:
    """
    Attaches one or more local image files to the open Facebook post creation dialog.

    Uses direct <input type="file"> send_keys interaction (standard Selenium best practice),
    avoiding native OS file pickers completely. Unhides hidden file inputs if needed and
    waits for media upload previews to render.

    Args:
        driver: Active Selenium WebDriver.
        file_input_selectors: XPath or list of XPaths for the file input element.
        photo_btn_selectors: Optional XPath or list of XPaths for the Photo/video button if input is unmounted.
        image_paths: List of file system paths to the image files.
        timeout: Maximum seconds to wait for file input or preview rendering.

    Returns:
        True if files were attached successfully.

    Raises:
        DOMActionError: If images cannot be attached or upload fails.
    """
    if not image_paths:
        return True

    # Validate all image paths exist
    resolved_paths: List[str] = []
    for p in image_paths:
        abs_p = os.path.abspath(p)
        if not os.path.isfile(abs_p):
            raise DOMActionError(f"Tệp hình ảnh không tồn tại: {abs_p}")
        resolved_paths.append(abs_p)

    logger.info("Bắt đầu đính kèm %d hình ảnh vào bài viết...", len(resolved_paths))

    if isinstance(file_input_selectors, str):
        file_input_selectors = [file_input_selectors]

    def _locate_file_input() -> Optional[WebElement]:
        for xp in file_input_selectors:
            elems = driver.find_elements(By.XPATH, xp)
            if elems:
                return elems[0]
        return None

    # 1. Try finding file_input directly first
    file_input = _locate_file_input()

    # 2. If file_input is not found, click photo/video button to reveal drop zone
    if file_input is None and photo_btn_selectors:
        if isinstance(photo_btn_selectors, str):
            photo_btn_selectors = [photo_btn_selectors]
        for xp in photo_btn_selectors:
            btns = driver.find_elements(By.XPATH, xp)
            if btns and btns[0].is_displayed():
                try:
                    logger.info("Nhấp nút 'Ảnh/video' để mở khung tải tệp...")
                    safe_click(driver, btns[0])
                    time.sleep(1.0)
                    break
                except Exception as click_err:
                    logger.debug("Không thể nhấp photo_btn: %s", click_err)

        # Re-check file_input after clicking
        file_input = _locate_file_input()

    if file_input is None:
        raise DOMActionError("Không tìm thấy phần tử <input type='file'> để tải ảnh lên.")

    # 3. Send image paths to file input (resilient against React re-rendering / stale element)
    paths_payload = "\n".join(resolved_paths)
    uploaded = False
    last_err: Optional[Exception] = None

    for attempt in range(3):
        target_input = file_input if attempt == 0 else _locate_file_input()
        if target_input is None:
            time.sleep(1.0)
            target_input = _locate_file_input()
        if target_input is None:
            continue

        try:
            driver.execute_script(
                "arguments[0].style.display = 'block'; arguments[0].style.visibility = 'visible'; arguments[0].style.opacity = '1';",
                target_input,
            )
        except Exception:
            pass

        try:
            target_input.send_keys(paths_payload)
            logger.info("Đã gửi đường dẫn %d tệp ảnh qua send_keys.", len(resolved_paths))
            uploaded = True
            break
        except (StaleElementReferenceException, WebDriverException) as err:
            last_err = err
            try:
                fresh_input = _locate_file_input() or target_input
                for p in resolved_paths:
                    fresh_input.send_keys(p)
                    time.sleep(0.3)
                logger.info("Đã gửi tuần tự %d tệp ảnh qua send_keys.", len(resolved_paths))
                uploaded = True
                break
            except Exception as seq_err:
                last_err = seq_err
                logger.warning("Lần gửi tệp ảnh %d gặp lỗi (%s), đang thử lại...", attempt + 1, seq_err)
                time.sleep(1.0)

    if not uploaded:
        raise DOMActionError(f"Lỗi khi gửi tệp ảnh vào input[type=file]: {last_err}") from last_err

    # 5. Brief wait for Facebook to process media thumbnails
    time.sleep(random.uniform(2.0, 3.5))
    logger.info("✅ Đã đính kèm ảnh thành công vào khung soạn thảo bài viết.")
    return True
