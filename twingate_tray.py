#!/usr/bin/env python3
"""Prosta ikona Twingate w zasobniku w stylu klienta na Windows - odpowiednik tailscale-tray.

Menu (lewy lub prawy klik): status (klik = połącz/rozłącz), konto (przełączanie, dodawanie, wylogowanie),
zasoby (kopiowanie adresu, uwierzytelnianie), exit networks, ustawienia, About, Exit.

Stan czyta z IPC demona twingated (/run/twingate/auth.sock, SOCK_SEQPACKET, JSON) - tak samo robi CLI.
Połącz/rozłącz = start/stop twingate.service, bo tak robi `twingate connect/disconnect`; reguła polkit
z install.sh pozwala na to bez hasła użytkownikom z grupy wheel (odpowiednik operatora Tailscale).
Konta, exit nody i uwierzytelnianie zasobów idą przez CLI `twingate`.
AppIndicator zamiast QSystemTrayIcon, bo w Plasmie tylko wtedy lewy klik otwiera menu.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator3  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

APP_NAME = "Twingate Tray"
APP_VERSION = "1.0"
SOCKET = "/run/twingate/auth.sock"
PROFILES = Path("/var/lib/twingate/profiles")
SERVICE = "twingate"
NOTIFIER = "twingate-desktop-notifier"  # otwiera przeglądarkę, gdy demon prosi o logowanie
POLL_SECONDS = 3
RESOURCES_SECONDS = 30  # każde zapytanie o zasoby to 2 linie w journalu twingated
ICON_DIR = Path(__file__).resolve().with_name("icons")


def icon_path(name: str) -> str:
    """Pełna ścieżka do kopii ikony z hashem zawartości w nazwie.

    Pełna ścieżka, nie nazwa: Plasma ignoruje IconThemePath z płaskim katalogiem i obcina nieznaną
    nazwę do "twingate-tray" (ikona aplikacji). Hash w nazwie: Plasma trzyma w cache ikonę spod
    tej samej ścieżki, więc po zmianie pliku pokazywałaby starą wersję."""
    src = ICON_DIR / f"{name}.svg"
    try:
        data = src.read_bytes()
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/twingate-tray-{os.getuid()}") / "twingate-tray"
        runtime.mkdir(parents=True, exist_ok=True)
        dst = runtime / f"{name}-{hashlib.sha1(data).hexdigest()[:10]}.svg"
        if not dst.exists():
            dst.write_bytes(data)
        return str(dst)
    except OSError:
        return str(src)


ICON_ON, ICON_OFF, ICON_EXIT, ICON_AUTH = (
    icon_path(name)
    for name in ("twingate-tray-on", "twingate-tray-disconnected", "twingate-tray-exit", "twingate-tray-auth")
)


class IPCError(Exception):
    pass


def _recv_json(sock: socket.socket) -> dict:
    data = sock.recv(1 << 20)  # SEQPACKET: jeden recv = jeden pakiet, za mały bufor obciąłby go
    if not data:
        raise IPCError("twingated zamknął połączenie")
    return json.loads(data)


def ipc(cmd: dict | None = None, timeout: float = 5) -> tuple[dict, dict | None]:
    """Status i opcjonalnie odpowiedź na komendę.

    Po połączeniu demon sam wysyła {"cmd": "status", "msg": "Online", ...} i {"cmd": "status-end"};
    samo połączenie nie zostawia śladu w journalu. Odpowiedź na "resources" to nagłówek
    {"cmd": "resources", "content-length": N}, a po nim JSON w kolejnych pakietach."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as s:
            s.settimeout(timeout)
            s.connect(SOCKET)
            status: dict = {}
            while (msg := _recv_json(s)).get("cmd") != "status-end":
                if msg.get("cmd") == "status":
                    status = msg
            if cmd is None:
                return status, None
            s.send(json.dumps(cmd).encode())
            while (header := _recv_json(s)).get("cmd") != cmd["cmd"]:
                pass  # powiadomienia wysyłane w międzyczasie
            length = header.get("content-length")
            if length is None:
                return status, header
            body = b""
            while len(body) < length:
                chunk = s.recv(1 << 20)
                if not chunk:
                    break
                body += chunk
            return status, json.loads(body)
    except (OSError, ValueError) as e:
        raise IPCError(f"twingated nie odpowiada: {e}") from e


def idle_once(func, *args) -> None:
    """Wywołaj func raz w wątku GTK. Samo GLib.idle_add powtarza wywołanie, dopóki funkcja zwraca
    True - z webbrowser.open (zwraca True) otwierało to przeglądarkę w pętli."""
    def _call() -> bool:
        func(*args)
        return False
    GLib.idle_add(_call)


def run(*cmd: str, timeout: int = 30, input: str | None = None) -> subprocess.CompletedProcess:
    """Polecenie z limitem czasu, które przy przekroczeniu ubija całą grupę procesów: CLI twingate
    odpala pod spodem twingate-notifier, który potrafi wisieć (np. exit-node start z nieznaną nazwą)."""
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        out, err = proc.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        err = (err or "") + f"\n(timed out after {timeout}s)"
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def output(res: subprocess.CompletedProcess) -> str:
    return ANSI.sub("", (res.stderr or "") + (res.stdout or "")).strip()


def notify(title: str, body: str = "") -> None:
    subprocess.Popen(["notify-send", "-a", APP_NAME, "-i", "twingate-tray", title, body])


def error(msg: str) -> None:
    subprocess.Popen(["kdialog", "--title", APP_NAME, "--error", msg])


def copy_to_clipboard(text: str) -> None:
    # Na Waylandzie proces bez okna nie ustawi schowka przez GTK - wl-copy tak.
    if shutil.which("wl-copy") and os.environ.get("WAYLAND_DISPLAY"):
        subprocess.run(["wl-copy", text], timeout=5)
    else:
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        cb.set_text(text, -1)
        cb.store()
    notify("Copied to clipboard", text)


def in_terminal(*cmd: str) -> None:
    """Interaktywne polecenie CLI (pyta o sieć itp.) w oknie terminala."""
    script = '"$@"; echo; printf "Press Enter to close… "; read _'
    for term in ("konsole", "alacritty", "kitty", "xterm"):
        if shutil.which(term):
            subprocess.Popen([term, "-e", "sh", "-c", script, "sh", *cmd])
            return
    error("No terminal emulator found to run:\n" + " ".join(cmd))


def service_enabled() -> bool:
    # "Start with the system" = twingate.service włączony w systemd (tak robi `twingate config autostart`).
    return run("systemctl", "is-enabled", SERVICE).stdout.strip() == "enabled"


def load_accounts() -> tuple[list[dict], str]:
    """Konta z /var/lib/twingate/profiles (czytelne dla wszystkich, tak samo czyta je `twingate account list`)."""
    accounts = []
    try:
        for d in PROFILES.iterdir():
            try:
                info = json.loads((d / "info.json").read_text())
            except (OSError, ValueError):
                continue
            info.setdefault("uuid", d.name)
            accounts.append(info)
        current = (PROFILES / "default").read_text().strip()
    except OSError:
        return accounts, ""
    return accounts, current


def account_label(acc: dict) -> str:
    name, email = acc.get("name", ""), acc.get("email", "")
    label = f"{name} ({email})" if name and email and name != email else (name or email or acc.get("uuid", "?"))
    network = acc.get("network_display_name") or acc.get("network_slug", "")
    return f"{label}  [{network}]" if network else label


def account_id(acc: dict) -> str:
    # `twingate account switch/logout` przyjmuje email, email:network_slug albo 4-znakowe ID.
    email, slug = acc.get("email", ""), acc.get("network_slug", "")
    return f"{email}:{slug}" if email and slug else email


def user_label(user: dict) -> str:
    name = " ".join(filter(None, (user.get("first_name"), user.get("last_name"))))
    email = user.get("email", "")
    return f"{name} ({email})" if name and email else (name or email)


def resource_address(r: dict) -> str:
    addr = r.get("address") or ""
    return addr[:-3] if addr.endswith("/32") else addr


def resource_alias(r: dict) -> str:
    aliases = r.get("aliases") or []
    alias = aliases[0] if aliases else r.get("alias")
    if isinstance(alias, dict):
        alias = alias.get("address") or alias.get("name")
    return alias if isinstance(alias, str) and alias not in ("", "-") else ""


def expires_text(ts: int) -> str:
    left = ts - time.time()
    if left <= 0:
        return "expired"
    if left < 3600:
        return f"in {max(1, int(left // 60))} min"
    if left < 86400:
        return f"in {int(left // 3600)} h"
    return f"in {int(left // 86400)} days"


def needs_auth(r: dict) -> bool:
    exp = r.get("auth_expires_at") or 0
    return r.get("auth_state") not in (None, "", "none", "authenticated") or 0 < exp < time.time()


class TwingateTray:
    def __init__(self) -> None:
        self.status: dict = {}
        self.data: dict | None = None  # odpowiedź na "resources": zasoby, użytkownik, exit networks
        self.data_time = 0.0
        self.error = ""
        self.accounts: list[dict] = []
        self.current_account = ""
        self.autostart = service_enabled()
        self.signature = None
        self.updating = False  # blokuje sygnały 'toggled' przy programowym ustawianiu pozycji
        self.busy = ""  # opis trwającej akcji w tle (połączenie, przełączanie konta…)

        self.indicator = AppIndicator3.Indicator.new(
            "twingate-tray", ICON_OFF, AppIndicator3.IndicatorCategory.SYSTEM_SERVICES
        )
        self.indicator.set_title("Twingate")
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.refresh()
        GLib.timeout_add_seconds(POLL_SECONDS, self.refresh)

    # ---------- stan ----------

    def refresh(self, full: bool = False) -> bool:
        old_msg = self.status.get("msg")
        try:
            self.status, _ = ipc()
            self.error = ""
        except IPCError as e:
            self.status, self.data, self.error = {}, None, str(e)
        # Zasoby osobno i rzadziej: status jest tani i nie loguje, a brak odpowiedzi na "resources"
        # (np. w trakcie logowania) nie może udawać, że usługa nie działa.
        stale = time.time() - self.data_time > RESOURCES_SECONDS
        if not self.error and (full or stale or self.status.get("msg") != old_msg):
            self.data_time = time.time()
            try:
                _, self.data = ipc({"cmd": "resources"}, timeout=3)
            except IPCError:
                self.data = None
        self.accounts, self.current_account = load_accounts()
        sig = self.make_signature()
        if sig != self.signature:
            self.signature = sig
            self.update_icon()
            self.indicator.set_menu(self.build_menu())
        return True

    def force_refresh(self) -> None:
        self.signature = None
        self.refresh(full=True)

    def make_signature(self):
        d = self.data or {}
        resources = tuple(
            (r.get("id"), r.get("name"), resource_address(r), resource_alias(r), r.get("auth_state"), needs_auth(r),
             expires_text(r.get("auth_expires_at") or 0), r.get("is_favorite"), r.get("client_visibility"))
            for r in d.get("resources") or []
        )
        nets = tuple((n.get("id"), n.get("name")) for n in d.get("remote_networks") or [])
        return (
            self.error,
            self.busy,
            self.status.get("msg"),
            self.status.get("details"),
            user_label(d.get("user") or {}),
            d.get("network_name"),
            resources,
            nets,
            json.dumps(d.get("full_tunnel"), sort_keys=True),
            tuple((a.get("uuid"), account_label(a)) for a in self.accounts),
            self.current_account,
            self.autostart,
        )

    @property
    def state(self) -> str:
        return self.status.get("msg", "") if not self.error else "Not running"

    @property
    def connected(self) -> bool:
        return self.state == "Online"

    def exit_network(self) -> dict | None:
        d = self.data or {}
        ft = d.get("full_tunnel") or {}
        net_id = ft.get("remote_network_id") if isinstance(ft, dict) else None
        if not net_id:
            return None
        for n in d.get("remote_networks") or []:
            if n.get("id") == net_id:
                return {**n, "expires_at": ft.get("expires_at")}
        return {"id": net_id, "name": str(net_id), "expires_at": ft.get("expires_at")}

    def update_icon(self) -> None:
        if self.connected and self.exit_network():
            icon, desc = ICON_EXIT, "Twingate: connected (exit network)"
        elif self.connected:
            icon, desc = ICON_ON, "Twingate: connected"
        elif self.state == "Authenticating":
            icon, desc = ICON_AUTH, "Twingate: sign-in required"
        else:
            icon, desc = ICON_OFF, "Twingate: disconnected"
        self.indicator.set_icon_full(icon, desc)

    # ---------- menu ----------

    def item(self, label: str, callback=None, sensitive: bool = True) -> Gtk.MenuItem:
        it = Gtk.MenuItem(label=label)
        it.set_use_underline(False)
        if callback:
            it.connect("activate", lambda _w: callback())
        it.set_sensitive(sensitive and (callback is not None))
        return it

    def check(self, label: str, active: bool, callback, sensitive: bool = True) -> Gtk.CheckMenuItem:
        it = Gtk.CheckMenuItem(label=label)
        it.set_use_underline(False)
        it.set_active(active)
        it.set_sensitive(sensitive)
        it.connect("toggled", lambda w: None if self.updating else callback(w.get_active()))
        return it

    def build_menu(self) -> Gtk.Menu:
        self.updating = True
        m = Gtk.Menu()
        add = m.append
        state = self.state

        # --- status ---
        if self.busy:
            add(self.item(self.busy))
        elif state == "Online":
            add(self.check("Connected", True, lambda _a: self.disconnect()))
        elif state == "Not running":
            add(self.check("Disconnected - click to connect", False, lambda _a: self.connect()))
        elif state == "Authenticating":
            add(self.item("Sign-in required - click to sign in…", self.reconnect))
        elif state == "Connecting":
            add(self.item("Connecting…"))
        else:
            add(self.item(f"{state or 'Offline'} - click to reconnect", self.reconnect))
        add(Gtk.SeparatorMenuItem())

        # --- użytkownik / konta ---
        add(self.accounts_menu())
        add(Gtk.SeparatorMenuItem())

        # --- sieć + zasoby ---
        d = self.data or {}
        if d.get("network_name"):
            add(self.item(f"Network: {d['network_name']}"))
        add(self.resources_menu())
        add(Gtk.SeparatorMenuItem())

        # --- exit networks ---
        add(self.exit_networks_menu())
        add(Gtk.SeparatorMenuItem())

        # --- ustawienia, about ---
        add(self.settings_menu())
        add(self.item("About", self.about))
        add(Gtk.SeparatorMenuItem())
        add(self.item("Exit", Gtk.main_quit))

        m.show_all()
        self.updating = False
        return m

    def accounts_menu(self) -> Gtk.MenuItem:
        d = self.data or {}
        user = d.get("user") or {}
        current = next((a for a in self.accounts if a.get("uuid") == self.current_account), None)
        label = user_label(user) or (account_label(current) if current else "Not signed in")
        root = Gtk.MenuItem(label=label)
        root.set_use_underline(False)
        sub = Gtk.Menu()
        group: list[Gtk.RadioMenuItem] = []
        for acc in sorted(self.accounts, key=lambda a: account_label(a).lower()):
            uuid = acc.get("uuid", "")
            it = Gtk.RadioMenuItem.new_with_label(group[0].get_group() if group else None, account_label(acc))
            it.set_use_underline(False)
            group.append(it)
            it.set_active(uuid == self.current_account)
            it.connect("toggled", lambda w, acc=acc: None if self.updating or not w.get_active() else self.switch_account(acc))
            sub.append(it)
        if self.accounts:
            sub.append(Gtk.SeparatorMenuItem())
        sub.append(self.item("Add another account…", lambda: in_terminal("twingate", "account", "add")))
        admin_url = d.get("admin_url")
        sub.append(self.item("Admin console", lambda: webbrowser.open(admin_url), bool(admin_url and user.get("is_admin"))))
        sub.append(Gtk.SeparatorMenuItem())
        sub.append(self.item("Log out", lambda: self.logout(current), current is not None))
        root.set_submenu(sub)
        return root

    def resource_item(self, r: dict) -> Gtk.MenuItem:
        addr, alias = resource_address(r), resource_alias(r)
        star = "★ " if r.get("is_favorite") else ""
        lock = "  (sign-in needed)" if needs_auth(r) else ""
        root = Gtk.MenuItem(label=f"{star}{r.get('name', '?')}  {alias or addr}{lock}")
        root.set_use_underline(False)
        sub = Gtk.Menu()
        if alias:
            sub.append(self.item(f"Copy alias  {alias}", lambda: copy_to_clipboard(alias)))
        sub.append(self.item(f"Copy address  {addr}", lambda: copy_to_clipboard(addr)))
        if r.get("open_url"):
            sub.append(self.item("Open in browser", lambda: webbrowser.open(r["open_url"])))
        sub.append(Gtk.SeparatorMenuItem())
        name = r.get("name", "")
        sub.append(self.item("Authenticate…" if needs_auth(r) else "Re-authenticate…", lambda: self.authenticate(name)))
        exp = r.get("auth_expires_at") or 0
        if exp:
            sub.append(self.item("Auth expired" if exp < time.time() else f"Auth expires {expires_text(exp)}"))
        if r.get("admin_url") and ((self.data or {}).get("user") or {}).get("is_admin"):
            sub.append(self.item("Open in admin console", lambda: webbrowser.open(r["admin_url"])))
        root.set_submenu(sub)
        return root

    def resources_menu(self) -> Gtk.MenuItem:
        all_res = list((self.data or {}).get("resources") or [])
        main = [r for r in all_res if r.get("client_visibility", 1)]
        hidden = [r for r in all_res if not r.get("client_visibility", 1)]
        root = Gtk.MenuItem(label=f"Resources ({len(main)})")
        sub = Gtk.Menu()
        if not main:
            sub.append(self.item("No resources" if self.connected else "Connect to see resources"))
        for r in sorted(main, key=lambda r: (not r.get("is_favorite"), r.get("name", "").lower())):
            sub.append(self.resource_item(r))
        if hidden:
            sub.append(Gtk.SeparatorMenuItem())
            more = Gtk.MenuItem(label=f"Background resources ({len(hidden)})")
            more_sub = Gtk.Menu()
            for r in sorted(hidden, key=lambda r: r.get("name", "").lower()):
                more_sub.append(self.resource_item(r))
            more.set_submenu(more_sub)
            sub.append(more)
        root.set_submenu(sub)
        root.set_sensitive(self.data is not None)
        return root

    def exit_networks_menu(self) -> Gtk.MenuItem:
        nets = list((self.data or {}).get("remote_networks") or [])
        current = self.exit_network()
        if current:
            until = f" (until {time.strftime('%H:%M', time.localtime(current['expires_at']))})" if current.get("expires_at") else ""
            label = f"Exit network: {current.get('name')}{until}"
        else:
            label = "Exit networks"
        root = Gtk.MenuItem(label=label)
        root.set_use_underline(False)
        sub = Gtk.Menu()
        group: list[Gtk.RadioMenuItem] = []

        def radio(text: str, active: bool, name: str) -> Gtk.RadioMenuItem:
            it = Gtk.RadioMenuItem.new_with_label(group[0].get_group() if group else None, text)
            it.set_use_underline(False)
            group.append(it)
            it.set_active(active)
            it.connect("toggled", lambda w: None if self.updating or not w.get_active() else self.set_exit_network(name))
            return it

        sub.append(radio("None (only Resources go through Twingate)", current is None, ""))
        for n in sorted(nets, key=lambda n: n.get("name", "").lower()):
            sub.append(radio(n.get("name", "?"), bool(current) and n.get("id") == current.get("id"), n.get("name", "")))
        if not nets:
            sub.append(self.item("No exit networks in this network"))
        root.set_submenu(sub)
        root.set_sensitive(self.connected and not self.busy)
        return root

    def settings_menu(self) -> Gtk.MenuItem:
        root = Gtk.MenuItem(label="Settings")
        sub = Gtk.Menu()
        sub.append(self.check("Start with the system", self.autostart, self.set_autostart))
        sub.append(Gtk.SeparatorMenuItem())
        sub.append(self.item("Restart Twingate service", self.reconnect, not self.busy))
        sub.append(self.item("Service log", lambda: in_terminal("journalctl", "-u", SERVICE, "-n", "200", "-f")))
        root.set_submenu(sub)
        return root

    # ---------- akcje ----------

    def in_background(self, busy: str, work, done=None) -> None:
        """Długie polecenie (systemctl, CLI twingate) w wątku, żeby menu nie zamarzało;
        w tym czasie status w menu pokazuje, co się dzieje."""
        if self.busy:
            return
        self.busy = busy
        self.force_refresh()

        def _thread() -> None:
            result = work()
            idle_once(_finish, result)

        def _finish(result) -> None:
            self.busy = ""
            if done:
                done(result)
            self.force_refresh()

        threading.Thread(target=_thread, daemon=True).start()

    def connect(self) -> None:
        def work():
            res = run("systemctl", "start", SERVICE, timeout=120)
            # Notifier otwiera przeglądarkę, gdy Twingate prosi o zalogowanie (jak `twingate start`).
            run("systemctl", "--user", "start", NOTIFIER)
            return res
        self.in_background("Connecting…", work, lambda res: self.report(res, "Could not start Twingate"))

    def disconnect(self) -> None:
        work = lambda: run("systemctl", "stop", SERVICE, timeout=120)  # noqa: E731
        self.in_background("Disconnecting…", work, lambda res: self.report(res, "Could not stop Twingate"))

    def reconnect(self) -> None:
        def work():
            run("systemctl", "--user", "start", NOTIFIER)
            return run("systemctl", "restart", SERVICE, timeout=120)
        self.in_background("Reconnecting…", work, lambda res: self.report(res, "Could not restart Twingate"))

    def report(self, res: subprocess.CompletedProcess, what: str) -> None:
        if res.returncode not in (0, 126):  # 126 = anulowane okno pkexec
            error(f"{what}:\n{output(res)}")

    def set_exit_network(self, name: str) -> None:
        current = self.exit_network()
        if not name:
            cmd = ("twingate", "exit-node", "stop")
        else:
            cmd = ("twingate", "exit-node", "switch" if current else "start", name)

        def done(res: subprocess.CompletedProcess) -> None:
            self.force_refresh()
            now = self.exit_network()
            if (now.get("name") if now else "") != name and res.returncode != 0:
                error(f"Could not change the exit network:\n{output(res)}")

        self.in_background("Changing exit network…", lambda: run(*cmd, timeout=60), done)

    def authenticate(self, resource: str) -> None:
        # CLI wysyła "auth" do demona, a przeglądarkę z logowaniem otwiera twingate-notifier.
        run("systemctl", "--user", "start", NOTIFIER)

        def work():
            return run("twingate", "auth", resource, timeout=300)

        def done(res: subprocess.CompletedProcess) -> None:
            if res.returncode != 0:
                error(f"Could not authenticate {resource}:\n{output(res)}")

        threading.Thread(target=lambda: idle_once(done, work()), daemon=True).start()

    def set_autostart(self, enabled: bool) -> None:
        # To samo co `twingate config autostart`: usługa systemowa + globalnie włączony notifier.
        verb = "enable" if enabled else "disable"
        script = f"systemctl {verb} {SERVICE} && systemctl --global {verb} {NOTIFIER}"
        res = run("pkexec", "sh", "-c", script, timeout=120)
        if res.returncode not in (0, 126):  # 126 = anulowane okno pkexec
            error(f"systemctl {verb} {SERVICE} failed:\n{output(res)}")
        self.autostart = service_enabled()
        self.force_refresh()

    def switch_account(self, acc: dict) -> None:
        def done(res: subprocess.CompletedProcess) -> None:
            _, current = load_accounts()
            if current != acc.get("uuid"):
                error(f"Could not switch account:\n{output(res) or 'twingate refused the switch'}")

        work = lambda: run("twingate", "account", "switch", account_id(acc), timeout=120)  # noqa: E731
        self.in_background("Switching account…", work, done)

    def logout(self, acc: dict | None) -> None:
        if not acc:
            return
        answer = run("kdialog", "--title", APP_NAME, "--yesno",
                     f"Log out {account_label(acc)}?\n\nTwingate Resources will be unavailable until you sign in again.",
                     timeout=600)
        if answer.returncode != 0:
            return
        # CLI pyta "Are you sure? [y/N]" na stdin.
        work = lambda: run("twingate", "account", "logout", account_id(acc), input="y\n", timeout=120)  # noqa: E731
        self.in_background("Logging out…", work, lambda res: self.report(res, "Could not log out"))

    def about(self) -> None:
        ver = output(run("twingate", "--version")).splitlines()
        d = self.data or {}
        text = (
            f"{APP_NAME} {APP_VERSION}\n"
            "A small Twingate tray client in the style of the Windows app.\n\n"
            f"{ver[0] if ver else 'Twingate ?'}\n"
            f"Network: {d.get('network_name', '-')}\n"
            f"State: {self.state or '-'}"
            + (f" ({self.status['details']})" if self.status.get("details") else "")
        )
        subprocess.Popen(["kdialog", "--title", f"About {APP_NAME}", "--icon", "twingate-tray", "--msgbox", text])


def single_instance_lock():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/twingate-tray-{os.getuid()}"
    os.makedirs(runtime, exist_ok=True)
    lock = open(os.path.join(runtime, "twingate-tray.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def main() -> None:
    lock = single_instance_lock()
    if lock is None:
        notify("Twingate Tray is already running", "The icon is in the system tray.")
        return
    # CLI twingate sam woła sudo przy niektórych poleceniach; bez terminala potrzebuje askpass.
    if "SUDO_ASKPASS" not in os.environ:
        for helper in (Path.home() / ".local/bin/sudo-askpass", Path("/usr/bin/ksshaskpass")):
            if os.access(helper, os.X_OK):
                os.environ["SUDO_ASKPASS"] = str(helper)
                break
    GLib.set_prgname("twingate-tray")
    TwingateTray()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Gtk.main()


if __name__ == "__main__":
    main()
