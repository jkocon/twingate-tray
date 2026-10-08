# twingate-tray

A **Twingate** icon for the Linux system tray, modelled on the official Windows client. On Linux
Twingate itself only ships a CLI and desktop notifications; this adds the tray menu: connect,
switch accounts, browse and authenticate Resources, and choose an exit network.

> **Polish documentation:** [README.pl.md](README.pl.md)

Sibling projects with the same look and behaviour:
[tailscale-tray](https://github.com/jkocon/tailscale-tray) and
[netbird-tray](https://github.com/jkocon/netbird-tray).

---

## Features

The menu opens on a left **or** right click:

| Menu entry | What it does |
|---|---|
| **Status** | *Connected* / *Disconnected – click to connect*. *Sign-in required – click to sign in…* restarts the sign-in in the browser. While an action runs, the status line says what is happening. |
| **Account** (submenu) | All Twingate accounts on this machine – click one to switch (`twingate account switch`). *Add another account…* (opens `twingate account add` in a terminal, because it asks for the network), *Admin console* (admins only), *Log out*. |
| **Network** | Name of the Twingate network. |
| **Resources** (submenu) | Every Resource, favourites first; each has *Copy alias*, *Copy address*, *Open in browser*, *Authenticate…* / *Re-authenticate…*, the authentication expiry and (for admins) *Open in admin console*. Resources hidden from the client are under *Background resources*. |
| **Exit networks** | Route all traffic through an exit network, switch between them, or *None* (only Resources go through Twingate); shows until when it is active. |
| **Settings** | *Start with the system* (Twingate service + notifier), *Restart Twingate service*, *Service log* (journal in a terminal). |
| **About**, **Exit** | Version information; quit the tray. |

### Tray icon

| Icon | State |
|---|---|
| bright logo | connected |
| grey logo | disconnected |
| green arrow | connected, all traffic goes through an exit network |
| orange "!" | Twingate waits for you to sign in in the browser |

---

## Requirements

- Linux desktop with a **StatusNotifierItem** tray: KDE Plasma works out of the box; GNOME needs the
  *AppIndicator and KStatusNotifierItem Support* extension.
- **Twingate** for Linux (`twingate`, `twingate.service`, `twingate-desktop-notifier`).
- polkit, and membership in the `wheel` group to connect/disconnect without a password.
- Runtime helpers: `kdialog`, `notify-send` (libnotify), `xdg-open` (xdg-utils), `wl-copy`
  (wl-clipboard) – or `xclip`/`xsel` on X11 – and a terminal (`konsole`, `alacritty`, `kitty` or `xterm`).
- To build: Rust 1.85+ (`cargo`).

`install.sh` targets Arch-based systems (CachyOS, Arch, EndeavourOS): it installs the runtime
helpers with `pacman`. On other distributions build with `cargo build --release` and copy the
files listed below by hand.

---

## Installation

```bash
git clone https://github.com/jkocon/twingate-tray.git
cd twingate-tray
sudo ./install.sh
```

`install.sh` (run as root; exit code 10 = Twingate is not installed, nothing done):

1. installs the runtime helpers with `pacman`,
2. builds the binary **as your normal user** with `build.sh` (cargo never runs as root),
3. installs `/usr/local/lib/twingate-tray/twingate-tray` and its state icons,
4. adds autostart (`/etc/xdg/autostart/twingate-tray.desktop`), a menu entry and the app icon,
5. installs the polkit rule `/etc/polkit-1/rules.d/49-twingate-tray.rules`: users in `wheel`, in an
   active local session, may start/stop/restart `twingate.service` without a password (the
   Twingate equivalent of a Tailscale operator). Enabling autostart still asks for a password.

The tray starts at the next login, or run `/usr/local/lib/twingate-tray/twingate-tray` now.
Only one instance runs per session.

### Command line

```
twingate-tray            start the tray icon
twingate-tray --dump     query twingated once and print the menu as text (no icon, for testing)
twingate-tray --version
```

---

## How it works

- State comes from the daemon's IPC socket `/run/twingate/auth.sock` (JSON over `SOCK_SEQPACKET`,
  the same channel the CLI uses). Reading the status leaves nothing in the journal, so it is
  polled every 3 s; the Resource list (which does log) is fetched every 30 s or after a change.
- Accounts are read from `/var/lib/twingate/profiles` (world-readable, like `twingate account list`).
- Connect/disconnect = start/stop `twingate.service`, exactly what `twingate connect/disconnect`
  does. The sign-in page is opened by `twingate-desktop-notifier`, which the tray starts when
  connecting or authenticating.
- Accounts, exit networks and Resource authentication use the `twingate` CLI. Some CLI commands call
  `sudo` themselves; the tray sets `SUDO_ASKPASS` (`~/.local/bin/sudo-askpass` or `ksshaskpass`) so
  they can ask for a password without a terminal.
- Long actions run in the background, so the menu never freezes.

> **Warning:** `twingate -p` ("print commands") is **not** a dry run – it executes the commands,
> including `sudo` ones. Don't use it to preview what the CLI would do.

---

## Building and testing

```bash
cargo build --release
cargo test
cargo run -- --dump        # menu for the current Twingate state, without a tray icon
```

### Layout

```
src/main.rs        tray state, menu, actions, polling
src/ipc.rs         twingated IPC client (SOCK_SEQPACKET via libc)
src/common/        shared with tailscale-tray and netbird-tray: commands with timeouts, kdialog,
                   notifications, clipboard, icons, single-instance lock, poller, ksni menu helpers
icons/             state icons (on, disconnected, exit, sign-in)
build.sh           builds as a regular user, used by install.sh
install.sh         system installation (Arch-based)
49-twingate-tray.rules   polkit rule
```

`src/common/` is the same copy in all three tray repositories – apply a fix there to all of them.

---

## License

MIT – see [LICENSE](LICENSE).
