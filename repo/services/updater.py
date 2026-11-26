"""
Application Updater Service (Binary Auto-Update for Windows .exe).

Provides version comparison, background check via GitHub Releases or custom API,
chunked downloading with SHA-256 integrity verification, and silent installer execution.
"""

from dataclasses import dataclass, field
import hashlib
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable, List, Optional, Tuple, Union
import urllib.error
import urllib.request

logger = logging.getLogger("FacebookBot.Updater")


@dataclass(frozen=True)
class UpdateInfo:
    """Information about an available software update."""
    version: str
    release_date: str = ""
    changelog: List[str] = field(default_factory=list)
    download_url: str = ""
    sha256: str = ""


def parse_version(v_str: str) -> Tuple[int, ...]:
    """
    Parses a version string into a tuple of integers for reliable SemVer comparison.
    Examples: '2.1.0' -> (2, 1, 0), 'v2.0.5' -> (2, 0, 5), '2.1' -> (2, 1, 0).
    """
    if not v_str or not isinstance(v_str, str):
        return (0, 0, 0)
    parts = re.findall(r"\d+", v_str.strip())
    if not parts:
        return (0, 0, 0)
    nums = [int(p) for p in parts]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def is_newer_version(current_version: str, remote_version: str) -> bool:
    """Returns True if remote_version is strictly newer than current_version."""
    return parse_version(remote_version) > parse_version(current_version)


def check_for_updates(
    current_version: str,
    update_url: str,
    timeout: float = 5.0,
    custom_logger: Optional[logging.Logger] = None,
) -> Optional[UpdateInfo]:
    """
    Checks remote endpoint for newer application releases.

    Args:
        current_version: Currently running application version (e.g. '2.0.0').
        update_url: URL to version.json (GitHub raw or API).
        timeout: Network timeout in seconds.
        custom_logger: Optional logger instance.

    Returns:
        UpdateInfo if a newer version is available, None otherwise.
    """
    log = custom_logger or logger
    if not update_url or not str(update_url).strip():
        return None

    try:
        req = urllib.request.Request(
            str(update_url).strip(),
            headers={"User-Agent": "FacebookAutomationBot-Updater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read(128 * 1024).decode("utf-8"))

        if not isinstance(data, dict) or "version" not in data:
            log.warning("Bản ghi version.json từ máy chủ không hợp lệ.")
            return None

        remote_ver = str(data["version"]).strip()
        if not is_newer_version(current_version, remote_ver):
            log.debug("Ứng dụng đang ở phiên bản mới nhất (%s).", current_version)
            return None

        # Resolve platform-specific download url
        download_url = ""
        sha256 = ""
        platforms = data.get("platforms", {})
        if isinstance(platforms, dict):
            plat_key = "windows" if sys.platform == "win32" else ("macos" if sys.platform == "darwin" else "linux")
            target_plat = platforms.get(plat_key) or platforms.get("windows")
            if isinstance(target_plat, dict):
                download_url = target_plat.get("url", "")
                sha256 = target_plat.get("sha256", "")

        if not download_url:
            download_url = str(data.get("url", "")).strip()
            sha256 = str(data.get("sha256", "")).strip()

        raw_changelog = data.get("changelog", [])
        changelog = [str(item) for item in raw_changelog] if isinstance(raw_changelog, list) else []

        update_info = UpdateInfo(
            version=remote_ver,
            release_date=str(data.get("release_date", "")),
            changelog=changelog,
            download_url=download_url,
            sha256=sha256,
        )
        log.info("🚀 Phát hiện phiên bản mới: v%s (Hiện tại: v%s)", remote_ver, current_version)
        return update_info

    except (urllib.error.URLError, TimeoutError, OSError) as net_err:
        log.debug("Không thể kết nối máy chủ cập nhật: %s", net_err)
        return None
    except Exception as err:
        log.warning("Lỗi kiểm tra cập nhật: %s", err)
        return None


def download_update(
    download_url: str,
    dest_path: Union[str, Path],
    expected_sha256: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    timeout: float = 30.0,
    custom_logger: Optional[logging.Logger] = None,
) -> bool:
    """
    Downloads updater installer in 64KB chunks with optional progress callback and SHA-256 verification.

    Args:
        download_url: Direct URL to the installer binary.
        dest_path: Target path to save the installer.
        expected_sha256: Optional SHA-256 hex digest to verify file integrity.
        progress_callback: Optional callback receiving (bytes_downloaded, total_bytes).
        timeout: Network timeout in seconds.
        custom_logger: Optional logger instance.

    Returns:
        True if download succeeded and passed integrity check, False otherwise.
    """
    log = custom_logger or logger
    if not download_url or not str(download_url).strip():
        return False

    target_file = Path(dest_path).resolve()
    target_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = target_file.with_suffix(".part")

    hasher = hashlib.sha256() if expected_sha256 else None
    downloaded_bytes = 0

    try:
        req = urllib.request.Request(
            str(download_url).strip(),
            headers={"User-Agent": "FacebookAutomationBot-Updater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total_bytes = int(resp.headers.get("Content-Length", 0))

            with open(tmp_file, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded_bytes += len(chunk)
                    if hasher:
                        hasher.update(chunk)
                    if progress_callback:
                        progress_callback(downloaded_bytes, total_bytes)

        if expected_sha256 and hasher:
            actual_sha256 = hasher.hexdigest().lower()
            if actual_sha256 != expected_sha256.strip().lower():
                log.error("Sai mã băm SHA-256 sau khi tải: mong đợi %s, thực tế %s", expected_sha256, actual_sha256)
                if tmp_file.exists():
                    tmp_file.unlink()
                return False

        tmp_file.replace(target_file)
        log.info("✅ Tải bản cập nhật thành công: %s (%d bytes)", target_file.name, downloaded_bytes)
        return True

    except Exception as err:
        log.error("Lỗi trong quá trình tải bản cập nhật: %s", err)
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except Exception:
                pass
        return False


def launch_silent_installer(
    installer_path: Union[str, Path],
    exit_current: bool = False,
    custom_logger: Optional[logging.Logger] = None,
) -> Optional[subprocess.Popen]:
    """
    Executes the Inno Setup installer in silent background mode.
    Arguments: /VERYSILENT /SUPPRESSMSGBOXES /NORESTART

    Args:
        installer_path: Path to FacebookBotSetup.exe.
        exit_current: If True, terminates the running process after launching installer.
        custom_logger: Optional logger instance.

    Returns:
        subprocess.Popen instance if launched, None on error.
    """
    log = custom_logger or logger
    target = Path(installer_path).resolve()
    if not target.is_file():
        log.error("Không tìm thấy bộ cài đặt tại: %s", target)
        return None

    cmd = [str(target), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]

    try:
        proc = subprocess.Popen(cmd, close_fds=True)
        log.info("🚀 Đã kích hoạt bộ cài đặt ngầm: %s (PID: %d)", target.name, proc.pid)
        if exit_current:
            sys.exit(0)
        return proc
    except Exception as err:
        log.error("Không thể khởi chạy bộ cài đặt: %s", err)
        return None
