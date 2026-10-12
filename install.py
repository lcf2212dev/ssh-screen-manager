#!/usr/bin/env python3
"""Install SSH Screen Manager for the current user and migrate its old launchers."""
import os
from pathlib import Path
import shutil
import subprocess

APP_ID = "io.github.ssh_screen_manager.Remote"
OLD_APP_ID = "io.github.telas.Remote"


def migrate_favorites():
    """Replace the old favorite in place without touching other dock entries."""
    try:
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        if not source or not source.lookup("org.gnome.shell", True):
            return False
        settings = Gio.Settings.new("org.gnome.shell")
        before = list(settings.get_strv("favorite-apps"))
        after = list(dict.fromkeys(APP_ID + ".desktop" if item == OLD_APP_ID + ".desktop" else item
                                   for item in before))
        if after != before:
            if not settings.set_strv("favorite-apps", after):
                return False
            Gio.Settings.sync()
        return True
    except (ImportError, RuntimeError):
        return False


def main():
    source = Path(__file__).resolve().parent
    home = Path.home()
    icon_source = source / "data/ssh-screen-manager.svg"
    if not icon_source.is_file():
        raise SystemExit(f"Missing application icon: {icon_source}")
    destination = home / ".local/share/ssh-screen-manager"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / "ssh_screen_manager", destination / "ssh_screen_manager", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    binary = home / ".local/bin/ssh-screen-manager"
    binary.parent.mkdir(parents=True, exist_ok=True)
    launcher = ('#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n'
                'sys.path.insert(0, str(Path.home() / ".local/share/ssh-screen-manager"))\n'
                'from ssh_screen_manager.__main__ import main\nraise SystemExit(main())\n')
    binary.write_text(launcher)
    binary.chmod(0o755)
    # Existing SSH commands and old clients can still invoke the former name.
    legacy_binary = home / ".local/bin/telas"
    if legacy_binary.exists():
        legacy_binary.write_text(launcher)
        legacy_binary.chmod(0o755)
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    old_config = config_root / "telas/settings.json"
    new_config = config_root / "ssh-screen-manager/settings.json"
    if old_config.is_file() and not new_config.exists():
        new_config.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_config, new_config)
    icons = home / ".local/share/icons/hicolor/scalable/apps"
    icons.mkdir(parents=True, exist_ok=True)
    icon = icons / f"{APP_ID}.svg"
    shutil.copy2(icon_source, icon)
    applications = home / ".local/share/applications"
    applications.mkdir(parents=True, exist_ok=True)
    desktop = source.joinpath(f"data/{APP_ID}.desktop").read_text()
    desktop = desktop.replace("Exec=ssh-screen-manager", f'Exec="{binary}"')
    desktop = desktop.replace(f"Icon={APP_ID}", f"Icon={icon}")
    applications.joinpath(f"{APP_ID}.desktop").write_text(desktop)
    old_desktop = applications / f"{OLD_APP_ID}.desktop"
    if migrate_favorites():
        old_desktop.unlink(missing_ok=True)
    elif old_desktop.exists():
        # Keep existing dock shortcuts usable if GNOME settings are unavailable.
        old_desktop.write_text(desktop + "NoDisplay=true\n")
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(applications)], check=False)
    if shutil.which("gtk-update-icon-cache"):
        subprocess.run(["gtk-update-icon-cache", "-f", "-t", str(icons.parents[1])],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    print(f"SSH Screen Manager installed: {binary}")
    print("Open SSH Screen Manager from the GNOME application menu or dock.")


if __name__ == "__main__":
    main()
