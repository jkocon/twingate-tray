#!/usr/bin/env bash
# Instaluje/aktualizuje twingate-tray w systemie. Uruchom jako root (sudo -A ./install.sh).
# Na X13 robi to automatycznie target/apply.sh po każdej zmianie w twingate-tray/ albo tray-common/.
# Działający tray podmienia się dopiero po ponownym zalogowaniu (albo: pkill -x twingate-tray; /usr/local/lib/twingate-tray/twingate-tray &).
# Kod wyjścia 10 = brak Twingate, nic nie zainstalowano.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
LIB=/usr/local/lib/twingate-tray
[[ $EUID -eq 0 ]] || { echo "Uruchom przez sudo"; exit 1; }

if ! command -v twingate >/dev/null; then
    echo "twingate-tray: brak twingate - pomijam"
    exit 10
fi

pacman -S --needed --asdeps --noconfirm kdialog wl-clipboard libnotify xdg-utils

# Binarka w Rust (od 2.0; wcześniej twingate_tray.py z GTK/AppIndicator), budowana jako zwykły użytkownik.
BIN=$("$SRC/../tray-common/build.sh" "$SRC")

install -Dm755 "$BIN" "$LIB/twingate-tray"
rm -rf "$LIB/twingate_tray.py" "$LIB/__pycache__"  # wersja w Pythonie
rm -rf "$LIB/icons"  # bez ikon o starych nazwach
install -Dm644 -t "$LIB/icons" "$SRC"/icons/*.svg
install -Dm644 -t /etc/xdg/autostart "$SRC/twingate-tray.desktop"
install -Dm644 "$SRC/twingate-tray-launcher.desktop" /usr/local/share/applications/twingate-tray.desktop
install -Dm644 "$SRC/twingate-tray.svg" /usr/local/share/icons/hicolor/scalable/apps/twingate-tray.svg
gtk-update-icon-cache -qtf /usr/local/share/icons/hicolor 2>/dev/null || true

# Połącz/rozłącz z traya bez hasła (start/stop twingate.service) - jak operator w Tailscale.
install -Dm644 "$SRC/49-twingate-tray.rules" /etc/polkit-1/rules.d/49-twingate-tray.rules
