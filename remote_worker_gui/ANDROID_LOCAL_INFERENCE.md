# Android Worker — lokalne obliczenia na telefonie (Issue #196)

Arena nadal samodzielnie prowadzi partię, sprawdza ruchy i zapisuje checkpointy. Telefon wykonuje tylko inferencję GGUF i przesyła proponowany ruch UCI. Szachownica w aplikacji jest wyświetlana z Areny.

## Bezpłatny etap testowy — Termux + llama.cpp

Obecny prototyp korzysta z lokalnego serwera llama.cpp na tym samym telefonie pod adresem http://127.0.0.1:8088. Nie można skierować inferencji na serwer zewnętrzny ani zmienić adresu z zadania Areny.

Jeżeli llama-server i GGUF są już dostępne w Termux:

    MODEL=/sciezka/do/modelu.gguf
    SHA=$(sha256sum "$MODEL" | cut -d' ' -f1)
    llama-server --host 127.0.0.1 --port 8088 --model "$MODEL" --alias "$SHA"

Nie otwieraj portu 8088 na zewnętrznym interfejsie.

W APK zapisz token i sprawdź HTTPS. Przycisk Start lokalnie włącza odczyt przydziałów. Telefon weryfikuje aktualną dzierżawę, odczytuje model serwera lokalnego z GET /v1/models, zleca POST /completion i odsyła do Areny wyłącznie pojedynczy ruch UCI. Po obliczeniach ponownie sprawdza, czy Arena nadal przydziela to samo zadanie. Arena dokonuje ostatecznej walidacji szachowej.

Pauza po ruchu oraz Stop po ruchu wyłączają kolejne zadania, lecz nie przerywają trwającego wywołania modelu. Pętla jest teraz foreground-only — nie zapewnia działania po uśpieniu aplikacji.

## Ważne ograniczenia

- Alias SHA-256 w llama-server pomaga uniknąć pomyłki modelu, ale NIE potwierdza kryptograficznie bajtów GGUF. Wydanie produkcyjne wymaga dodatkowej kontroli rzeczywistego pliku modelu.
- Nie ma jeszcze pobierania i usuwania modeli na żądanie ani samodzielnego silnika GGUF w APK.
- Domyślnie turniej dalej używa obliczeń na VPS. Eksperymentalny adapter telefonu działa tylko po jawnym ustawieniu zmiennej ARENA_PHONE_INFERENCE_ENABLED=1 **w środowisku uruchomienia Areny**. Gdy włączony, Arena wybiera dla każdego ruchu gotowy telefon z pasującym modelem SHA-256; gdy go brak, nie uruchamia po cichu innego modelu na VPS. Zapis szachownicy i checkpointów pozostaje w Arenie.
- Po aktualizacji APK przycisk **Wznów** może ponownie uruchomić odbieranie **nowych** zadań wyłącznie po potwierdzeniu tokenu, `idle_confirmed` i braku oczekującego checkpointu wznowienia. Wznowienie **przerwanego zadania** z checkpointu i zatrzymanie trwającej inferencji wymagają osobnej integracji STOP/ACK.
- Wymagany jest rzeczywisty test na Androidzie 16 i sprawdzenie wyników CI.
- Wszystko bazuje na darmowym, otwartym oprogramowaniu.

Komenda testowa nie wymaga instalowania żadnej płatnej usługi ani przesyłania GGUF do VPS.

## Testowe włączenie dispatchera po stronie Areny

Najpierw uruchom odpowiednio przygotowany lokalny model telefonu i użyj Start lokalnie w APK. Dopiero po sprawdzeniu połączenia, testów i przypisania właściwego modelu administrator może ustawić zmienną ARENA_PHONE_INFERENCE_ENABLED=1 dla procesu Areny. Nie jest ona włączona automatycznie w systemd ani w produkcyjnej usłudze. Zmiana tej opcji dotyczy wyboru wykonawcy ruchu; **nie przejmuje checkpointów i nie tworzy drugiego silnika partii na telefonie**.

W trybie eksperymentalnym Arena musi mieć gotowy lokalny model na telefonie dla każdego gracza, którego ruch ma być obliczany zdalnie. Brak gotowości powoduje jawny błąd, bez niekontrolowanego powrotu do obliczeń na VPS. Obecnie zarządzanie pobieraniem GGUF i sprawdzaniem rzeczywistych bajtów modelu na telefonie pozostaje nieukończone.

## Wymagania przed startem turnieju w trybie telefonu

Przy ustawieniu ARENA_PHONE_INFERENCE_ENABLED=1 preflight Areny wymaga świeżego zgłoszenia gotowości telefonu z pasującym SHA-256 **dla każdego zawodnika**. Nie wymaga wówczas dostępnego lokalnego Llamafile na VPS do obliczeń ruchów. Jeśli którykolwiek telefon/model jest niedostępny, start turnieju zostaje jawnie zablokowany; nie ma automatycznego fallbacku na VPS. Również na początku każdej partii Arena nie pobiera dodatkowych GGUF na dysk VPS, ponieważ obliczenia wykonuje urządzenie mobilne.

**Ograniczenie obecnego prototypu:** jeden telefon z jednym aktywnie załadowanym modelem nie jest jeszcze zdolny do automatycznego przełączania wielu modeli w kolejnych ruchach. Dla partii dwóch różnych modeli oba muszą być obsługiwane przez gotowe urządzenia; docelowy downloader/switcher dla telefonu nadal wymaga implementacji. Weryfikacja deklarowanego aliasu modelu nie zastępuje kontroli pliku GGUF. Zasady zatwierdzania modeli w rejestrze Areny pozostają bez zmian.

## Bezpieczny wybór legalnego ruchu (protokół v2)

Arena wylicza pełną, posortowaną listę legalnych ruchów UCI dla własnego FEN i dołącza ją do zadania w protokole v2. Telefon **nie wylicza legalnych ruchów ani nie utrzymuje szachownicy**: lokalny llama.cpp dostaje listę w promptcie i ograniczenie GBNF dopuszczające dokładnie jeden ruch z tej listy. Po generacji aplikacja sprawdza, czy UCI rzeczywiście jest na liście, a Arena niezależnie sprawdza legalność względem własnego FEN przed przyjęciem wyniku. Starsze zadania v1 nadal pozostają akceptowane na poziomie protokołu; nowe przydziały Areny korzystają z v2. Wyniki pozostają w istniejącym formacie v1.

Lista musi być kompletną, kanoniczną listą legalnych ruchów z Areny. Usunięte ruchy, nieprawidłowa kolejność, duplikaty lub niespójny FEN powodują odrzucenie zadania, a nie wygenerowanie alternatywnej pozycji w APK.


## Atomowe zatwierdzanie ruchu i checkpointu przez Arenę

Po odebraniu UCI od telefonu Arena **nie zwalnia od razu** przypisanego
Workera. Ruch jest sprawdzany względem FEN i przechowywany w trwałej skrzynce
wyników. Dopiero po przygotowaniu kolejnej pozycji przez serwerowy
`SequentialChessRunner` Arena, w **jednej transakcji SQLite IMMEDIATE**:

1. porównuje aktualny checkpoint z FEN, parą oraz historią poprzedniego ruchu;
2. zapisuje nowy checkpoint za pomocą `tournament_checkpoint.save_in_transaction`;
3. usuwa zatwierdzony wynik i przydział oraz ustawia Workera jako `idle_confirmed`.

Jeżeli nastąpi awaria lub zapis checkpointu się nie powiedzie, wszystkie
te zmiany są wycofywane razem. Arena zachowuje poprzedni checkpoint oraz
wynik UCI, a Worker pozostaje w stanie `running`, wymagając uzgodnienia
przed kolejnym zadaniem. Telefon nadal nie przechowuje checkpointu ani nie
prowadzi własnej partii.

Testy obejmują przerwanie transakcji po przygotowaniu zapisu,
niezgodny FEN, błędną parę, rozbieżny ruch i integrację z prawdziwym
serwerowym runnerem partii. Test symuluje wynik telefonu w Pythonie:
**nie jest** dowodem działania modelu GGUF na fizycznym urządzeniu.


## Jednorazowe wznowienie gotowości po bezczynnej aktualizacji APK

Po bezpiecznej aktualizacji **bezczynnego** telefonu Arena zwalnia oryginalną
rezerwację aktualizacji za pomocą właściwego tokenu i zapisuje jednorazowe
potwierdzenie `worker_idle_update_recovery` (maksymalnie 24 godziny).
Worker przechodzi w `unknown`, a nie bezpośrednio w `idle_confirmed`.
Dopiero po ponownym uruchomieniu APK i potwierdzeniu tokenu maszyny,
braku przerwanego checkpointu, braku aktywnego przydziału i lokalnego
modelu `llama.cpp`, komenda `reconcile_idle_phone_after_update`
przedstawia gotowość Arenie. Arena **atomowo** zużywa potwierdzenie
i zmienia stan na `idle_confirmed`. Dopiero później przycisk `Wznów`
włącza odbieranie nowych zadań na telefonie.

Brak właściwego zwolnienia rezerwacji, brak modelu, starszy niż 24 h
dowód, wcześniejsza awaria, nieznany przydział, oczekująca pauza albo
checkpoint przerwanej partii **nie** pozwalają uznać Workera za bezczynnego.
Dowód nie może zostać wykorzystany ponownie. Stan `unknown` bez
zaufanej historii aktualizacji pozostaje zablokowany.

Ta funkcja **nie wznawia przerwanego ruchu** i nie stanowi jeszcze pełnego
STOP/ACK przy aktualizacji wykonywanej podczas inferencji. W takich
przypadkach checkpoint Areny pozostaje nietknięty i konieczne jest
osobne, potwierdzone uzgodnienie przydziału.


## Wczesna obsługa STOP (bez fałszywego ACK)

Natywna komenda Androida odpytuje uwierzytelniony
`/api/remote-workers/update-pause-poll` **przed rozpoczęciem i po zakończeniu**
lokalnego wywołania `llama.cpp`. Odpowiedź jest związana z nonce,
tokenem i identyfikatorem Workera. Żądanie `stop_checkpoint`,
`await_reconciliation`, `blocked` lub błąd sieci zatrzymuje
przekazanie ruchu do Areny i nie oznacza zadania jako wykonanego.
Błędny identyfikator joba także blokuje przesłanie UCI.
Przed instalacją APK sprawdzana jest dodatkowo lokalna blokada
`generation_active()`, a Arena atomowo sprawdza brak aktywnego lease.

**To nie jest jeszcze STOP/ACK.** Jeżeli STOP nadejdzie w trakcie
nieprzerywalnego żądania inferencji, telefon wykryje go po jego
zakończeniu. Nie wysyła automatycznego ACK ani nie zezwala na
instalację. Arena przechowuje checkpoint i zachowuje blokadę przydziału,
dopóki nie zostanie wprowadzony zaufany mechanizm potwierdzenia
bezpiecznego zakończenia lokalnych obliczeń.

## Android 0.1.2: trwały, szyfrowany token Workera

W 0.1.1 używany był keyring 3.6, który na Androidzie nie ma natywnego
trwałego magazynu poświadczeń. W 0.1.2 natywna komenda używa własnego
WorkerCredentialVault.kt i AndroidKeyStore do bezpiecznego przechowania
klucza AES-256. Token przechowywany jest tylko jako szyfrogram AES-GCM
w prywatnych SharedPreferences aplikacji. Nie ma fallbacku do mocka,
localStorage ani zapisu sekretu w jawnej postaci. Uszkodzenie klucza
lub danych powoduje odmowę uwierzytelnienia, nie silent reset.

Podpisany APK o tym samym identyfikatorze pakietu zachowuje dane przy
aktualizacji, ale token zapisany w poprzednim keyring mock mógł zniknąć.
Po instalacji 0.1.2 może być konieczne ponowne zapisanie istniejącego
tokenu lub jego jawne odnowienie przez administratora Areny (tylko po
poleceniu użytkownika). Sam kod nie odwołuje tokenu na serwerze.

Test na fizycznym telefonie powinien objąć zapis tokenu, uwierzytelnienie
HTTPS, wymuszone zamknięcie aplikacji, ponowne otwarcie oraz aktualizację
do nowszego APK bez odinstalowywania. Zielone CI nie zastępuje tego testu.

## Trwałość identyfikatora komputera

W 0.1.1 identyfikator wpisany ręcznie (np. android_marvin_01) nie był
zapisywany. Przy kolejnym uruchomieniu aplikacja przywracała identyfikator
automatycznie wygenerowany w WebView, przez co para worker_id + token
mogła nie odpowiadać rejestracji w Arenie.

W 0.1.2 zachowywany jest aktualnie wpisany i poprawny identyfikator
po zmianie pola oraz po zaakceptowaniu tokenu przez Arenę. Przy ponownym
uruchomieniu aplikacja wczytuje zapisany identyfikator zamiast tworzyć
nowy; bez reinstalacji i bez czyszczenia danych Android WebView
przetrwa również aktualizację podpisanego APK. Identyfikator nie jest
sekretem i może być zapisany w WebView localStorage. Token jest
przechowywany wyłącznie przez natywny magazyn Android Keystore.
