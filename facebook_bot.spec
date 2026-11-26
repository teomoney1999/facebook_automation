# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller Specification File for Facebook Automation Bot.
Produces a production-ready, standalone application bundle for macOS (.app) and Windows (.exe).
"""

import sys
import os
from pathlib import Path

block_cipher = None

# Base repository root directory
SPECPATH = os.path.abspath(os.path.dirname(__file__))
REPO_DIR = os.path.join(SPECPATH, "repo")

# Static data files to include
added_datas = [
    (os.path.join(REPO_DIR, "json"), "json"),
    (os.path.join(REPO_DIR, ".env.example"), "."),
]

# Hidden imports required by dynamic reflection or subpackages
hidden_imports = [
    "selenium",
    "selenium.webdriver",
    "selenium.webdriver.chrome.service",
    "selenium.webdriver.chrome.options",
    "selenium.webdriver.common.by",
    "selenium.webdriver.support.ui",
    "selenium.webdriver.support.expected_conditions",
    "webdriver_manager",
    "webdriver_manager.chrome",
    "dotenv",
    "queue",
    "threading",
    "urllib.request",
    "urllib.parse",
    "urllib.error",
    "json",
    "logging",
    "logging.handlers",
    "tkinter",
    "tkinter.ttk",
    "tkinter.scrolledtext",
    "tkinter.messagebox",
    "config",
    "facebookbot",
    "browser",
    "browser.driver_manager",
    "browser.dom_actions",
    "browser.dom_inspector",
    "services",
    "services.state_manager",
    "services.circuit_breaker",
    "services.post_verifier",
    "services.telegram_controller",
    "services.selector_sync",
    "services.updater",
    "services.group_verifier",
    "services.group_joiner",
    "services.posting_plan",
    "helpers",
    "helpers.logger",
    "helpers.common_helpers",
]

a = Analysis(
    [os.path.join(REPO_DIR, "app_ui.py")],
    pathex=[REPO_DIR],
    binaries=[],
    datas=added_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Executable configuration
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FacebookBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Windowed GUI without background terminal popup
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FacebookBot",
)

# macOS Application Bundle (.app)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="FacebookBot.app",
        icon=None,
        bundle_identifier="com.automation.facebookbot",
        info_plist={
            "NSHighResolutionCapable": "True",
            "LSBackgroundOnly": "False",
            "CFBundleDisplayName": "Facebook Automation Bot",
            "CFBundleShortVersionString": "2.0.0",
        },
    )
