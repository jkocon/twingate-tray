# twingate-tray

Prosta ikona Twingate w zasobniku w stylu klienta na Windows – odpowiednik `tailscale-tray`
(oficjalny klient Twingate na Linuksie ma tylko CLI i powiadomienia). Menu otwiera się lewym lub prawym kliknięciem:

- status: klik łączy albo rozłącza; przy „Sign-in required” klik ponawia logowanie w przeglądarce,
- konto: lista kont Twingate do przełączania (`twingate account switch`), „Add another account…”
  (`twingate account add` w terminalu, bo pyta o sieć), „Admin console” (dla adminów) i „Log out”,
- sieć i zasoby (Resources): kopiowanie adresu/aliasu, „Authenticate…” dla zasobów wymagających
  logowania, termin wygaśnięcia uwierzytelnienia, link do zasobu w konsoli admina,
- exit networks do wyboru (`twingate exit-node start/switch/stop`),
- Settings: Start with the system (`twingate.service` + notifier włączone w systemd, przez pkexec),
  Restart Twingate service, Service log,
- About, Exit.

Ikona w zasobniku (sygnet Twingate z twingate.com): jasna = połączony, szara = rozłączony, zielona strzałka = cały ruch idzie
przez exit network, pomarańczowy „!” = Twingate czeka na zalogowanie w przeglądarce.

Stan czyta z IPC demona (`/run/twingate/auth.sock`, JSON po SOCK_SEQPACKET – tak samo jak CLI), konta
z `/var/lib/twingate/profiles`. Połącz/rozłącz to start/stop `twingate.service`, bo tak działa
`twingate connect/disconnect`; `install.sh` dodaje regułę polkit `49-twingate-tray.rules`, żeby
użytkownicy z grupy `wheel` mogli to robić bez hasła (odpowiednik operatora w Tailscale).
Przeglądarkę z logowaniem otwiera `twingate-desktop-notifier` z pakietu Twingate – tray go uruchamia
przy łączeniu.

Uwaga: `twingate -p` („print commands”) wcale nie jest suchym przebiegiem – wykonuje polecenia
(łącznie z sudo), więc nie używać go do podglądania.

Kod: Rust (`src/`, od wersji 2.0 zamiast `twingate_tray.py` z GTK/AppIndicator). Ikona i menu przez `ksni`
(StatusNotifierItem + DBusMenu, bez GTK; lewy klik otwiera menu). `src/common/` to część wspólna z
[tailscale-tray](https://github.com/jkocon/tailscale-tray), [twingate-tray](https://github.com/jkocon/twingate-tray)
i [netbird-tray](https://github.com/jkocon/netbird-tray) - ta sama kopia w każdym repo.
`install.sh` buduje binarkę jako zwykły użytkownik (`build.sh`, cargo z pakietu `rust`).
Podgląd bez ikony: `cargo run -- --dump` wypisuje menu dla bieżącego stanu demona; testy: `cargo test`.

Instalacja: `sudo -A ./install.sh` (do `/usr/local/lib/twingate-tray`, autostart w `/etc/xdg/autostart`).
Na komputerach synchronizowanych przez cachyos_sync robi to automatycznie `target/apply.sh` po każdym nowym commicie.

## Licencja

MIT – patrz [LICENSE](LICENSE).
