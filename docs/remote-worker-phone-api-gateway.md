# Bezpieczne API Android Workera (Issue #272)

Ten etap przenosi z niescalonego PR #197 jedynie endpointy:
job-poll, ready, job-result, update-pause-poll i board-view.

- Nowa flaga ARENA_PHONE_WORKER_API_ENABLED=1 dopuszcza API do pracy.
  Domyślnie pozostaje wyłączona i zwraca HTTP 503, nie stronę logowania.
- Niezależna flaga ARENA_PHONE_INFERENCE_ENABLED pozostaje wyłączona.
  Nie zostaną przydzielone żadne zadania ani uruchomione turnieje telefoniczne.
- Worker uwierzytelnia się istniejącym tokenem SQLite, przekazuje nonce
  i dokładnie określony zestaw pól, z limitem wejścia 2048 bajtów.
- Ready rejestruje wyłącznie alias SHA-256 działającego modelu; nie jest to
  weryfikacja kryptograficzna zawartości GGUF.
- Job-poll jedynie czyta bieżący przydział; stworzenie przydziału wymaga
  zaufanego kodu koordynatora i nie ma publicznego endpointu.
- Wynik UCI nie kończy lease ani nie aktualizuje checkpointu. STOP jest
  instrukcją read-only; bez potwierdzonego ACK i bez zezwolenia na instalację.
- Odczyt szachownicy jest dozwolony tylko dla bieżącej własnej dzierżawy
  i zgodnego świeżego zdarzenia Arena-live.
- Testy używają prywatnego, krótkotrwałego tokenu, tymczasowej bazy i
  losowego portu loopback. Nie wykorzystują produkcyjnych sekretów.

Osobny PR będzie potrzebny do bezpiecznego dispatchowania ruchów
turniejowych na telefon i atomowego rozliczenia checkpointów.

## Wdrożenie

Plik systemd/ai-chess-arena-worker-api.conf jest wersjonowaną propozycją
odrębnego drop-in do ai-chess-arena.service. Nie należy uruchamiać
samej gałęzi PR na produkcji przed zakończeniem CI, review i scaleniem.
Wdrożenie polega na uruchomieniu serwera ze scalonego main (z osobnego
worktree, po weryfikacji DB symlink i zasobów produkcyjnych), sprawdzeniu
bezczynności turnieju, konfiguracji drop-in, a następnie restarcie
usługi z mechanizmem rollback. Nie uruchamiać telefonu w produkcyjnym
turnieju. Kryteria gotowości: uwierzytelnione job-poll zwraca state none,
ready może potwierdzić istniejący model telefonu, a błędny token daje 401
bez login_url. W przypadku błędów po restarcie przywrócić poprzedni
worktree i drop-in.

Szczegóły tokenu nie mogą trafić do logów, testów ani PR.
