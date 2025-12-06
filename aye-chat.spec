# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for aye-chat.
Build with: pyinstaller aye-chat.spec
"""

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect hidden imports for complex dependencies
hiddenimports = [
    # Jaraco namespace packages (required by pkg_resources/setuptools)
    'jaraco.text',
    'jaraco.functools',
    'jaraco.context',
    # Keyring backends
    'keyring.backends',
    'keyring.backends.Windows',
    # Prompt toolkit
    'prompt_toolkit',
    'prompt_toolkit.clipboard',
    'prompt_toolkit.clipboard.pyperclip',
    # Typer/Click
    'typer',
    'click',
    # Rich (used by typer)
    'rich',
    'rich.console',
    'rich.markdown',
    'rich.syntax',
    'rich.table',
    # HTTPX
    'httpx',
    'httpcore',
    'h11',
    'anyio',
    'sniffio',
    # ChromaDB and ONNX (heavy dependencies)
    'chromadb',
    'onnxruntime',
    # Tokenizers (required by sentence-transformers/chromadb)
    'tokenizers',
    # SSL/TLS
    'certifi',
    # Our plugins
    'aye.plugins',
    'aye.plugins.local_model',
    'aye.plugins.offline_llm',
    'aye.plugins.shell_executor',
    'aye.plugins.auto_detect_mask',
    'aye.plugins.completer',
    'aye.plugins.slash_completer',
]

# Collect all submodules for chromadb (it has many internal imports)
hiddenimports += collect_submodules('chromadb')
hiddenimports += collect_submodules('onnxruntime')
hiddenimports += collect_submodules('jaraco')
hiddenimports += collect_submodules('tokenizers')

# Collect data files needed at runtime
datas = []
datas += collect_data_files('chromadb')
datas += collect_data_files('onnxruntime')
datas += collect_data_files('certifi')
datas += collect_data_files('jaraco.text')
datas += collect_data_files('jaraco.functools')
datas += collect_data_files('jaraco.context')

# Include plugin .py files for runtime discovery
datas += [('src/aye/plugins/*.py', 'aye/plugins')]

a = Analysis(
    ['src/aye/__main_chat__.py'],
    pathex=['src'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude test/dev dependencies
        'pytest',
        'ruff',
        'coverage',
        'pip',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='aye',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # CLI application needs console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/aye-chat.ico' if os.path.exists('assets/aye-chat.ico') else None,
    version='version_info.txt' if os.path.exists('version_info.txt') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='aye-chat',
)
