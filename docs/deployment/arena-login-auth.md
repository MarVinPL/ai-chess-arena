# Logowanie do panelu AI Chess Arena

## Zakres

Panel używa formularza logowania aplikacyjnego, a nie okna HTTP Basic Auth. Backend weryfikuje hasło i chroni wszystkie endpointy API. Sesja jest podpisanym tokenem w ciasteczku `HttpOnly; Secure; SameSite=Strict`, ważnym 8 godzin. Hasło jest przechowywane wyłącznie jako PBKDF2-HMAC-SHA256 (600 000 iteracji); sekret podpisujący sesje jest osobny. Brak konfiguracji oznacza odmowę logowania (fail-closed).

Nie zapisuj haseł, hashy ani sekretu sesji w repozytorium, w GitHub Issues/PR ani w logach.

## Konfiguracja na VPS — dopiero podczas zatwierdzonego wdrożenia

1. Utwórz katalog sekretów i wygeneruj plik interaktywnie. Generator pyta o login i hasło w terminalu; hasło nie jest wyświetlane:
   ```bash
   sudo install -d -o root -g root -m 0700 /etc/ai-chess-arena
   sudo /home/debian/ai-chess-arena/.venv/bin/python /home/debian/ai-chess-arena/arena_auth.py --generate-env /etc/ai-chess-arena/auth.env
   sudo chown root:root /etc/ai-chess-arena/auth.env
   sudo chmod 0600 /etc/ai-chess-arena/auth.env
   ```
   Generator odmawia nadpisania istniejącego pliku. Przy rotacji hasła najpierw przygotuj nowy plik tymczasowy, sprawdź go i dopiero po osobnej decyzji zastąp konfigurację.
2. Jednostka systemd odczytuje `/etc/ai-chess-arena/auth.env`. Plik musi istnieć przed restartem usługi.
3. Najpierw wdroż backend i sprawdź lokalnie formularz/logowanie, pozostawiając dotychczasową ochronę Caddy jako dodatkową warstwę.
4. Dopiero po pozytywnym teście usuń blok `basicauth` z osobnego pliku `/etc/caddy/sites/ai-chess-arena.caddy`. Docelowa konfiguracja tej domeny:
   ```caddy
   arena.148-113-182-137.sslip.io {
       reverse_proxy 127.0.0.1:8090
   }
   ```
5. Zweryfikuj całą konfigurację Caddy przed przeładowaniem. Nie zmieniaj głównego Caddyfile ani konfiguracji innych usług.

## Zachowanie i zabezpieczenia

- Publiczne są tylko `/login`, `/api/auth/login` i `/api/auth/status`; pozostałe API i panel wymagają ważnej sesji.
- Wszystkie żądania POST poza logowaniem wymagają sesji i dokładnego nagłówka Origin zgodnego z `ARENA_AUTH_ORIGIN`.
- Maksymalnie 5 nieudanych prób logowania na adres klienta w oknie 10 minut (limit w pamięci procesu).
- Wylogowanie unieważnia ciasteczko po stronie przeglądarki.
- Caddy Basic Auth pozostaje aktywny do czasu osobnego, zatwierdzonego wdrożenia. Ten PR nie zmienia produkcji ani konfiguracji VPS.
