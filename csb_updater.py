#!/usr/bin/env python3
"""
CSB Updater - Separater Update-Prozess für Claude Session Browser.

Ablauf:
1. App ruft: updater.exe --install <setup.exe>
2. App beendet sich
3. Updater wartet bis App-Prozess weg ist
4. Updater führt Installer aus
5. Updater startet neue App
6. Updater beendet sich

So wie Chrome, VS Code, Discord es machen.
"""
import os
import sys
import time
import subprocess
import argparse
import ctypes
from pathlib import Path

VERSION = "1.1.0"
APP_NAME = "ClaudeSessionBrowser"
APP_EXE = "ClaudeSessionBrowser.exe"

def log(msg):
    """Log to file in TEMP."""
    log_file = Path(os.environ.get("TEMP", ".")) / "csb_updater.log"
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}\n"
    print(line, end="")
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)
    except:
        pass

def _hidden_kwargs():
    """Keep console helpers (tasklist/taskkill) from flashing a window up."""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return {"startupinfo": si,
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}

def is_process_running(name):
    """Check if process with given name is running."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            capture_output=True, text=True, timeout=10, **_hidden_kwargs()
        )
        return name.lower() in result.stdout.lower()
    except:
        return False

def kill_process(name, timeout=10):
    """Kill process and wait until it's gone."""
    log(f"Killing {name}...")
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", name, "/T"],
            capture_output=True, timeout=10, **_hidden_kwargs()
        )
    except:
        pass

    start = time.time()
    while time.time() - start < timeout:
        if not is_process_running(name):
            log(f"{name} terminated")
            return True
        time.sleep(0.5)

    log(f"Warning: {name} still running after {timeout}s")
    return False

def wait_for_process_exit(name, timeout=30):
    """Wait until process exits."""
    log(f"Waiting for {name} to exit...")
    start = time.time()
    while time.time() - start < timeout:
        if not is_process_running(name):
            log(f"{name} exited")
            return True
        time.sleep(0.5)
    log(f"Timeout waiting for {name}")
    return False

def get_install_path():
    """Get app install path from registry."""
    try:
        import winreg
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{A2E1C4F8-9B3D-4E5A-8F2B-7C6D5A4E3F21}_is1"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            path, _ = winreg.QueryValueEx(key, "InstallLocation")
            return Path(path)
    except:
        pass

    # Fallback
    local = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return Path(local) / "Programs" / APP_NAME

def run_installer(setup_path):
    """Run Inno Setup installer silently."""
    log(f"Running installer: {setup_path}")
    try:
        result = subprocess.run(
            [str(setup_path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL"],
            timeout=300
        )
        log(f"Installer exit code: {result.returncode}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log("Installer timeout!")
        return False
    except Exception as e:
        log(f"Installer error: {e}")
        return False

def start_app(app_path):
    """Start the app in a new process."""
    log(f"Starting app: {app_path}")
    try:
        # Use subprocess with CREATE_NEW_PROCESS_GROUP so it survives updater exit
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        subprocess.Popen(
            [str(app_path)],
            creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
            close_fds=True
        )
        log("App started successfully")
        return True
    except Exception as e:
        log(f"Failed to start app: {e}")
        return False

def relaunch_from_temp(setup_path):
    """Copy self to TEMP and continue from there.

    The updater is installed INTO the app folder, so the installer needs to
    replace the very file we are running from - Windows locks it and the
    install hangs. Chrome/VS Code solve this the same way: run the updater
    from a scratch copy outside the install directory.

    Returns True when a copy was launched (caller should exit).
    """
    if not getattr(sys, "frozen", False):
        return False  # running as .py, nothing is locked

    src = Path(sys.executable)
    dst = Path(os.environ.get("TEMP", ".")) / "csb_updater_run.exe"
    try:
        if src.resolve() == dst.resolve():
            return False  # already the temp copy
    except OSError:
        pass

    try:
        import shutil
        shutil.copy2(src, dst)
    except OSError as e:
        log(f"Could not copy updater to temp: {e} - continuing in place")
        return False

    log(f"Relaunching from {dst}")
    try:
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        subprocess.Popen(
            [str(dst), "--install", str(setup_path), "--relaunched"],
            creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
            close_fds=True,
        )
        return True
    except OSError as e:
        log(f"Relaunch failed: {e} - continuing in place")
        return False


def do_install(setup_path, relaunched=False):
    """Main install routine."""
    log(f"=== CSB Updater v{VERSION} ===")
    log(f"Setup: {setup_path}")

    if not os.path.exists(setup_path):
        log(f"ERROR: Setup not found: {setup_path}")
        return False

    if not relaunched and relaunch_from_temp(setup_path):
        return True  # the temp copy takes over from here

    # Wait for main app to exit (it should have closed itself)
    wait_for_process_exit(APP_EXE, timeout=10)

    # Force kill if still running
    if is_process_running(APP_EXE):
        kill_process(APP_EXE, timeout=5)

    # Delete lock file
    lock_file = Path(os.environ.get("LOCALAPPDATA", ".")) / f"{APP_NAME}.instance.lock"
    if lock_file.exists():
        try:
            lock_file.unlink()
            log("Lock file deleted")
        except:
            pass

    # Small delay to ensure file handles are released
    time.sleep(1)

    # Run installer
    if not run_installer(setup_path):
        log("ERROR: Installer failed!")
        return False

    # Wait a moment for installer cleanup
    time.sleep(2)

    # Find and start app
    install_path = get_install_path()
    app_path = install_path / APP_EXE

    if not app_path.exists():
        log(f"ERROR: App not found at {app_path}")
        return False

    if not start_app(app_path):
        log("ERROR: Failed to start app!")
        return False

    # Cleanup setup file
    try:
        os.remove(setup_path)
        log("Setup file deleted")
    except:
        pass

    log("=== Update complete ===")
    return True

def main():
    parser = argparse.ArgumentParser(description="CSB Updater")
    parser.add_argument("--install", metavar="SETUP", help="Install from setup.exe")
    parser.add_argument("--relaunched", action="store_true",
                        help=argparse.SUPPRESS)  # internal: already the temp copy
    parser.add_argument("--version", action="store_true", help="Show version")
    args = parser.parse_args()

    if args.version:
        print(f"CSB Updater v{VERSION}")
        return 0

    if args.install:
        success = do_install(args.install, relaunched=args.relaunched)
        return 0 if success else 1

    parser.print_help()
    return 1

if __name__ == "__main__":
    sys.exit(main())
