"""
Selector Synchronization Service (Hot-Patch OTA for Facebook DOM Selectors).

Downloads and updates selectors.json in the background without requiring
re-building or re-installing the application binary.
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional, Union
import urllib.error
import urllib.request

logger = logging.getLogger("FacebookBot.SelectorSync")

MAX_SELECTORS_PAYLOAD_BYTES = 512 * 1024  # 512 KB sanity ceiling


def validate_selectors_data(data: object) -> bool:
    """Validates that loaded data conforms to the selectors.json schema."""
    if not isinstance(data, dict) or not data:
        return False
    valid_components = 0
    for comp, sel_list in data.items():
        if comp.startswith("_"):
            continue
        if not isinstance(sel_list, list) or len(sel_list) == 0:
            return False
        if not all(isinstance(s, str) and s.strip() for s in sel_list):
            return False
        valid_components += 1
    return valid_components > 0


def sync_selectors(
    remote_url: str,
    local_path: Union[str, Path],
    timeout: float = 3.0,
    custom_logger: Optional[logging.Logger] = None,
) -> bool:
    """
    Downloads remote selectors.json and atomically updates local_path if changed.

    Args:
        remote_url: Direct URL to the raw selectors.json file on cloud/GitHub.
        local_path: Target path to local selectors.json.
        timeout: Network request timeout in seconds.
        custom_logger: Optional logger for diagnostic messages.

    Returns:
        True if selectors.json was updated with new content, False otherwise.
    """
    log = custom_logger or logger
    if not remote_url or not str(remote_url).strip():
        return False

    target_path = Path(local_path).resolve()

    try:
        req = urllib.request.Request(
            str(remote_url).strip(),
            headers={"User-Agent": "FacebookAutomationBot-SelectorSync/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = response.read(MAX_SELECTORS_PAYLOAD_BYTES + 1)
            if len(payload) > MAX_SELECTORS_PAYLOAD_BYTES:
                log.warning("Bỏ qua selectors từ cloud: Kích thước vượt quá 512KB.")
                return False

        # Parse and validate schema
        data = json.loads(payload.decode("utf-8"))
        if not validate_selectors_data(data):
            log.warning("Bỏ qua selectors từ cloud: Cấu trúc JSON không hợp lệ.")
            return False

        # Check against existing local file
        remote_hash = hashlib.sha256(payload).hexdigest()
        if target_path.is_file():
            try:
                local_bytes = target_path.read_bytes()
                local_hash = hashlib.sha256(local_bytes).hexdigest()
                if remote_hash == local_hash:
                    log.debug("Bộ chọn selectors.json đã ở phiên bản mới nhất.")
                    return False
            except Exception:
                pass

        # Atomic write to local_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_path.with_suffix(".tmp")
        tmp_path.write_bytes(payload)
        tmp_path.replace(target_path)

        log.info("✅ Đã hot-patch cập nhật bộ nhận diện Facebook DOM mới nhất (%s)", target_path.name)
        return True

    except (urllib.error.URLError, TimeoutError, OSError) as net_err:
        log.warning("Không thể đồng bộ selectors từ cloud: %s. Tiếp tục dùng bản local.", net_err)
        return False
    except Exception as err:
        log.warning("Lỗi không xác định khi hot-patch selectors: %s", err)
        return False
