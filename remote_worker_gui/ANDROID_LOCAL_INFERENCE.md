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
- Wznawianie zadania po aktualizacji i zatrzymanie modelu podczas obliczeń wymagają osobnej integracji.
- Wymagany jest rzeczywisty test na Androidzie 16 i sprawdzenie wyników CI.
- Wszystko bazuje na darmowym, otwartym oprogramowaniu.

Komenda testowa nie wymaga instalowania żadnej płatnej usługi ani przesyłania GGUF do VPS.

## Testowe włączenie dispatchera po stronie Areny

Najpierw uruchom odpowiednio przygotowany lokalny model telefonu i użyj Start lokalnie w APK. Dopiero po sprawdzeniu połączenia, testów i przypisania właściwego modelu administrator może ustawić zmienną ARENA_PHONE_INFERENCE_ENABLED=1 dla procesu Areny. Nie jest ona włączona automatycznie w systemd ani w produkcyjnej usłudze. Zmiana tej opcji dotyczy wyboru wykonawcy ruchu; **nie przejmuje checkpointów i nie tworzy drugiego silnika partii na telefonie**.

W trybie eksperymentalnym Arena musi mieć gotowy lokalny model na telefonie dla każdego gracza, którego ruch ma być obliczany zdalnie. Brak gotowości powoduje jawny błąd, bez niekontrolowanego powrotu do obliczeń na VPS. Obecnie zarządzanie pobieraniem GGUF i sprawdzaniem rzeczywistych bajtów modelu na telefonie pozostaje nieukończone.

## Wymagania przed startem turnieju w trybie telefonu

Przy ustawieniu ARENA_PHONE_INFERENCE_ENABLED=1 preflight Areny wymaga świeżego zgłoszenia gotowości telefonu z pasującym SHA-256 **dla każdego zawodnika**. Nie wymaga wówczas dostępnego lokalnego Llamafile na VPS do obliczeń ruchów. Jeśli którykolwiek telefon/model jest niedostępny, start turnieju zostaje jawnie zablokowany; nie ma automatycznego fallbacku na VPS. Również na początku każdej partii Arena nie pobiera dodatkowych GGUF na dysk VPS, ponieważ obliczenia wykonuje urządzenie mobilne.

**Ograniczenie obecnego prototypu:** jeden telefon z jednym aktywnie załadowanym modelem nie jest jeszcze zdolny do automatycznego przełączania wielu modeli w kolejnych ruchach. Dla partii dwóch różnych modeli oba muszą być obsługiwane przez gotowe urządzenia; docelowy downloader/switcher dla telefonu nadal wymaga implementacji. Weryfikacja deklarowanego aliasu modelu nie zastępuje kontroli pliku GGUF. Zasady zatwierdzania modeli w rejestrze Areny pozostają bez zmian.
