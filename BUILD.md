# Building the Windows Installer

This guide explains how to build the Windows installer locally.

## Prerequisites

1. **Python 3.10+** - https://www.python.org/downloads/
2. **PyInstaller** - `pip install pyinstaller`
3. **Inno Setup 6** - https://jrsoftware.org/isdl.php

## Build Steps

### 1. Install dependencies

```bash
pip install pyinstaller
pip install -e .
```

### 2. Build the executable with PyInstaller

```bash
pyinstaller aye-chat.spec --noconfirm
```

This creates the application in `dist/aye-chat/`.

To test the executable:

```bash
dist\aye-chat\aye.exe --version
```

### 3. Build the installer with Inno Setup

```bash
iscc installer.iss /DMyAppVersion=0.29.1
```

Or with full path if `iscc` is not in PATH:

```bash
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss /DMyAppVersion=0.29.1
```

The installer will be created in `Output/aye-chat-0.29.1-setup.exe`.

## Build Outputs

| File | Description |
|------|-------------|
| `dist/aye-chat/` | PyInstaller output (standalone application) |
| `Output/aye-chat-X.X.X-setup.exe` | Windows installer |

## Installer Features

The installer provides the following options:

- **Install location**: `%LOCALAPPDATA%\Programs\Aye Chat` (per-user, no admin required)
- **Add to PATH**: Adds the install directory to user PATH
- **Desktop shortcut**: Optional desktop shortcut
- **Context menu**: "Open Aye Chat here" in folder right-click menu

## CI/CD

The GitHub Actions workflow (`.github/workflows/build-windows-installer.yml`) automatically builds and uploads the installer when a release is published.

To manually trigger a build, go to Actions → "Build Windows Installer" → Run workflow.
