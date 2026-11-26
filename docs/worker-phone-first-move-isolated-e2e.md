# Pierwszy ruch Android Worker -> Arena -> checkpoint (#274)

To jest izolowany test protokolu HTTP z klientem symulującym telefon.
Nie jest to test rzeczywistej inferencji na fizycznym Androidzie.

Repozytorium main od #273 zawiera obsługę żądań telefonu i trwałą
skrzynkę odbiorczą UCI, ale nie zawierało wymaganej funkcji
tournament_checkpoint.save_in_transaction. Ten PR uzupełnia zależność
oraz sprawdza niezmienność metadanych, pary, kolejności półruchów, FEN
i strony na ruchu.

Test test_remote_worker_phone_atomic_checkpoint_http.py uruchamia
prawdziwy server.Handler na losowym porcie localhost, tymczasową SQLite
i sztuczny krótkotrwały token. Po ready z poprawnym SHA koordynator
przydziela dwa zadania z pełną listą legalnych ruchów, klient wysyła
e2e4 i e7e5, a Arena przeprowadza walidację i zatwierdza oba
checkpointy. Lease jest zwolniony dopiero po transakcyjnym zapisie.
Powtórzone UCI, nielegalny ruch, niezgodny checkpoint i uszkodzona
ciągłość nie mogą wywołać dodatkowego zapisu. Symulowana awaria po
zapisie w niezatwierdzonej transakcji powoduje rollback całej operacji
i pozostawia poprzedni checkpoint oraz nierozliczony wynik Workera.

Wszystkie testy są offline, bez produkcyjnej bazy i bez tokenu
android_marvin_01. Brama nadal obsługuje tylko uwierzytelnione,
zdefiniowane wcześniej endpointy; ARENA_PHONE_INFERENCE_ENABLED=0
pozostaje bez zmian.

## Kolejny etap — fizyczny Android

Aby użytkownik rzeczywiście wykonał zlecony ruch przez lokalny model
GGUF, potrzebny jest kontrolowany przydział testowy na odizolowanej
arenie ze stanem partii i zaufanym mechanizmem finalizacji. Nie należy
włączać produkcyjnego turnieju ani tworzyć fikcyjnych zakończonych gier.
Wymagany będzie kolejny niezależnie recenzowany PR i test z fizycznym
Androidem online.
