# Diagnoza procesu API i jednostki systemd

Data obserwacji: 2026-11-13 (read-only).

## Ustalenia

- `127.0.0.1:8090` ma listener `python`, PID `2943842`.
- Proces wykonuje `/home/debian/ai-chess-arena/.venv/bin/python server.py`, jego katalog roboczy to `/home/debian/ai-chess-arena`.
- Proces ma PPID 1, użytkownika/grupę `debian` i cgroup `/system.slice/sentinelx-cloud-core.service`.
- Jednostka `ai-chess-arena.service` nie istnieje w systemd: `LoadState=not-found`, `FragmentPath` pusty, `MainPID=0`; `systemctl cat ai-chess-arena` zgłasza brak pliku.
- `sentinelx-cloud-core.service` jest aktywna i zawiera PID procesu API w swoim cgroup. To wyjaśnia, dlaczego proces API może działać mimo że nie istnieje osobna jednostka `ai-chess-arena`.
- API odpowiada na porcie 8090, ale ostatni odczyt `/api/live` zwrócił stan aplikacyjny `error` związany z nielegalnym UCI `g6d8`; odpowiedź HTTP należy interpretować oddzielnie od zdrowia silnika i stanu turnieju.
- Repozytorium na VPS ma lokalny HEAD `52bd27d`, podczas gdy GitHub `main` wskazuje `9041bd8`. Lokalna gałąź `main` jest za aktualnym GitHub `main` o sześć commitów (stan lokalnych remote-tracking refs również był nieświeży).
- W drzewie roboczym pozostają lokalne zmiany `index.html` oraz nieśledzony backup `index.html.before-board-arrows-20261113-185919`. Nie były modyfikowane.

## Wniosek

Status `inactive` nie oznacza, że API jest zatrzymane. Odczytana jednostka `ai-chess-arena.service` w ogóle nie jest zainstalowana. Proces API działa jako proces potomny/członek cgroup aktywnej usługi `sentinelx-cloud-core.service`. Nie należy restartować ani zatrzymywać tej usługi w celu „naprawienia” statusu, ponieważ zarządza ona także innymi procesami, w tym narzędziami zdalnego dostępu.

## Zalecane dalsze kroki

1. W osobnym Issue ustalić docelowy, dedykowany mechanizm zarządzania procesem API, niezależny od usługi SentinelX.
2. Przed jakąkolwiek zmianą przygotować plan migracji procesu, rollback i testy health-check.
3. Osobno uzgodnić deploy/restart. Nie wykonywać go w ramach tej diagnostyki.
4. Przed synchronizacją repozytorium zabezpieczyć i przejrzeć lokalne zmiany `index.html` oraz backup; nie nadpisywać ich.

## Zakres bezpieczeństwa

Wykonano wyłącznie odczyty: `ss`, `ps`, `/proc`, `systemctl show/cat/status`, `journalctl`, `git status/log/branch/remote` i test odczytu API. Nie zatrzymywano ani nie uruchamiano procesów, nie zmieniano plików, konfiguracji, DB, runtime, modeli ani turniejów.
