# SSH Screen Manager

SSH Screen Manager lets you see and control the screens of another computer
that is already signed in to GNOME. The picture and your keyboard and mouse
travel through SSH, so the connection is authenticated and encrypted. The app
does not open an extra network port, and it does not start a background service.

The window, the in-app help, and the command-line messages are in English.
Open **Help** in the window, or press **F1**, for the same instructions while
you use the app.

You need two computers, or more:

- the one you are sitting at
- each computer you want to view

Install the app on all of them, as the person who is signed in to GNOME.

## What has to be true first

On every computer you want to view:

- GNOME is running on Wayland, and that person is signed in.
- The screen is unlocked. A locked screen is reported as unavailable.
- The OpenSSH server is running.
- This app is installed for that same person.

On the computer you are sitting at:

- You can SSH to the other computer with a key.
- The command below finishes without asking for a password and without asking
  you to trust a host key.

```sh
ssh user@other-computer echo ok
```

Put a custom user, port, or address in `~/.ssh/config`. The app keeps strict
host-key checking turned on. It does not scan the network, and it does not
accept a new host key for you. If SSH has never seen that computer, connect
once in a terminal and confirm the key there.

A usual first-time SSH setup is:

```sh
ssh-keygen -t ed25519
ssh-copy-id user@other-computer
ssh user@other-computer echo ok
```

## Install

Install the packages below, then run the installer as the desktop user.
`install.py` does not need sudo. It installs into your home directory.

### Arch Linux and Manjaro

These are the packages this project is tested with:

```sh
sudo pacman -S python python-gobject gtk4 libadwaita gstreamer \
  gst-plugins-base gst-plugins-good gst-plugin-pipewire openssh
python install.py
```

Install `openssh` and enable `sshd` on each computer you want to view.
The computer you sit at needs the SSH client.

### Debian, Ubuntu, and Fedora

Install the same components under the names your distribution uses:

- Python 3 and PyGObject
- GTK 4 and libadwaita
- GStreamer plugins that provide `jpegenc`, `glupload`, `glcolorconvert`,
  and `gldownload`
- the GStreamer PipeWire plugin
- an OpenSSH client where you sit, and an OpenSSH server where you view

On Debian or Ubuntu that is usually:

```sh
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-gl \
  gstreamer1.0-pipewire openssh-client openssh-server
python3 install.py
```

On Fedora that is usually:

```sh
sudo dnf install python3 python3-gobject gtk4 libadwaita \
  gstreamer1-plugins-base gstreamer1-plugins-good gstreamer1-plugin-pipewire \
  openssh-clients openssh-server
python3 install.py
```

The remote computer needs an EGL-capable graphics driver. Full-screen
applications are captured through that path.

### Where the files go

| Item | Location |
| --- | --- |
| Program | `~/.local/share/ssh-screen-manager/ssh_screen_manager/` |
| Command | `~/.local/bin/ssh-screen-manager` |
| Application menu | `~/.local/share/applications/io.github.ssh_screen_manager.Remote.desktop` |
| Your preferences | `~/.config/ssh-screen-manager/settings.json` |
| Icon | `~/.local/share/icons/hicolor/scalable/apps/io.github.ssh_screen_manager.Remote.svg` |

`~/.config` follows `XDG_CONFIG_HOME` when that variable is set.
`~/.local/bin` needs to be on your `PATH`. If the application menu does not
show the new entry, log out and back in.

The icon shipped in `data/ssh-screen-manager.svg` is the icon the installer copies.

Repeat `python install.py` on each computer after you update the source.
Close the window first. The next connection uses the updated copy on the
computer you are viewing.

If an older installation used the command name `telas`, the installer leaves
that command in place and copies its preferences once, when you do not already
have new ones.

## Use the app

1. Open **SSH Screen Manager** from the application menu or the dock.
2. Wait until the computers you can reach appear in the list.
3. Select one that is available and click **Connect**.
4. Click a screen. The keyboard and mouse then go to that computer.

The list is filled from computers you have saved, names in `~/.ssh/config`,
and known SSH hosts. Several names for the same computer become one entry.
The app remembers the computer and the image quality you used last time.

**Refresh devices** checks the list again. The list also refreshes when the
window opens and about every 30 seconds while you are disconnected.
A saved computer that does not answer stays in the list and is marked unavailable.

**Add computer** asks for a name or address, checks it, and saves it.
Use the same name you would pass to `ssh`.

You do not need this window open on the computer you are viewing.
SSH starts the capture program when you connect, and stops it when you disconnect.

### While you are connected

- **Keyboard & mouse** switches between controlling the other computer and
  view only.
- **Connection options → Send system shortcuts** forwards shortcuts such as
  Alt+Tab while a remote screen is focused. GNOME may ask you to allow this.
- The expand button on a screen fills your display with that screen, without
  the title bar or the borders. The full-screen button in the header does the
  same when the other computer has only one screen.
- **Overview**, when there are several screens, places the primary monitor
  above the others. The tabs follow the screens from left to right. This does
  not rearrange the other computer's monitors and does not move its windows.
- In a window, the picture keeps its proportions. In full screen it stretches
  to fill your display. The other computer's pointer is part of the picture.
- **Connection options** shows the frame rate, bandwidth, and latency of the
  current connection.

### Keyboard shortcuts

| Shortcut | What it does |
| --- | --- |
| F1 | Open help |
| Ctrl+Alt+Enter | Leave full screen and keep control |
| Ctrl+Alt+Esc | Release the keyboard and mouse, and leave full screen |

Ctrl+Alt+Esc always stays on your computer, including while system shortcuts
are being sent.

### Image quality

Choose this in **Connection options** before you connect.

| In the window | With `--profile` | Widest picture | Frames each second, per screen | Use it when |
| --- | --- | --- | --- | --- |
| Save data | `low-bandwidth` | 1024 px | 12 | The network is slow |
| Balanced | `balanced` | 1600 px | 20 | You want the ordinary setting |
| Sharper text | `sharp` | 3840 px | 15 | You are reading text or code |

A screen that is not on screen stays connected, and its frames are dropped
before they are encoded. A still screen sends fewer frames than the maximum.

### From a terminal

```sh
ssh-screen-manager
ssh-screen-manager --host other-computer --connect
ssh-screen-manager --check
```

`--host` is the same name you would give to `ssh`. `--check` prints whether
this computer can be viewed. It does not start screen capture.
`--profile` selects the image quality for the capture program started with
`--serve`. The window stores its own quality choice separately.

## When a computer does not appear, or will not connect

- It is turned on, and the person is signed in to GNOME on Wayland.
- The screen is unlocked.
- `ssh user@that-computer echo ok` works with no questions.
- This app is installed for that person, so `~/.local/bin/ssh-screen-manager`
  exists there.
- You clicked **Refresh devices** after changing any of the above.

The status line repeats the reason when SSH refuses the connection, the host
key is unknown, the address does not resolve, or the app is missing on the
other side.

## What it does not do

- It does not play sound, share the clipboard, transfer files, or send touch
  gestures. Copy files with the SSH or SFTP tools you already use.
- It does not blank the physical screens. People near that computer see the
  same activity. There is no privacy mode.
- It does not sign anyone in, reach another person's session, or create a
  virtual monitor. It uses the session that is already open.
- It does not change the lock screen or the sleep settings.
- Adding, removing, or rearranging monitors needs a new connection.
- Super and Alt+Tab may stay on your computer when **Send system shortcuts**
  is off, or when the compositor does not hand those keys over.
- Video, games, and H.264 are outside what the JPEG pictures are for.

The capture path uses GNOME's `org.gnome.Mutter.ScreenCast` and
`org.gnome.Mutter.RemoteDesktop` interfaces. It is checked against GNOME and
Mutter 50. Another compositor, or a future change to those interfaces, can
fail even when SSH itself works.

## Remove the app

Close the window, then delete the paths in the table above. There is no
service and no firewall rule to remove. If you still have a `telas` command
from an older installation and you no longer want it, delete
`~/.local/bin/telas` as well.

## For people changing the program

The window runs:

```text
ssh -T HOST ~/.local/bin/ssh-screen-manager --serve
```

The program on the other computer asks Mutter for one PipeWire stream per
monitor, encodes each stream as JPEG with GStreamer, and writes framed JSON
plus the pictures to standard output. Keyboard and mouse events come back as
JSON on standard input. Diagnostics go to standard error. `--check` only
reports readiness.

Each screen keeps its newest frame, so a slow link does not pile up old
pictures. The capture program exits after 20 seconds without a command or a
heartbeat. Keys and buttons are released when the window loses focus, when
you change screens, when you turn control off, or when the connection ends.

```sh
python -m unittest discover -s tests -v
```

The other files in `tests/` connect to a real computer and move its pointer.
Pass them the SSH name and the screen connectors of a computer you are allowed
to control. They open temporary windows and take focus. Do not use that
computer for anything else while they run. Their reports go under `evidence/`,
which is not part of the Git history, because the pictures can contain whatever
was on the desktop. The app does not record a session and does not send telemetry.

- [Mutter ScreenCast interface](https://github.com/GNOME/mutter/blob/main/data/dbus-interfaces/org.gnome.Mutter.ScreenCast.xml)
- [Mutter RemoteDesktop interface](https://github.com/GNOME/mutter/blob/main/data/dbus-interfaces/org.gnome.Mutter.RemoteDesktop.xml)
- [GStreamer JPEG encoder](https://gstreamer.freedesktop.org/documentation/jpeg/jpegenc.html)
