# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: Carousell Collector
# 打包: pyinstaller build.spec --noconfirm
#   Windows → dist/CarousellCollector/CarousellCollector.exe
#   macOS   → dist/CarousellCollector.app + dist/CarousellCollector/（CLI）

import os
import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)
IS_MAC = sys.platform == "darwin"
# 交叉构建：macOS 上可通过 CC_TARGET_ARCH=x86_64 在 arm64 runner 产出 Intel 版
TARGET_ARCH = os.environ.get("CC_TARGET_ARCH") or None

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "curl_cffi",
        "websocket",
        "openpyxl",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter.test", "test", "unittest", "pydoc_data", "playwright"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CarousellCollector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # 保留控制台：GUI 崩溃可见错误，CLI 可用
    icon=None,
    target_arch=TARGET_ARCH,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="CarousellCollector",
)

# macOS：额外产出 .app 便于双击启动
if IS_MAC:
    app = BUNDLE(
        coll,
        name="CarousellCollector.app",
        icon=None,
        bundle_identifier="com.carousell.collector",
        info_plist={
            "CFBundleName": "CarousellCollector",
            "CFBundleDisplayName": "Carousell 采集工具",
            "CFBundleShortVersionString": "1.4.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.utilities",
        },
    )
