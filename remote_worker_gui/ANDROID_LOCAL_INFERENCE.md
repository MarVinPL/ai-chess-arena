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
- Domyślny runner turnieju na VPS nie jest jeszcze podłączony do dispatchera zadań telefonu; nadal wybiera wykonanie lokalne na VPS.
- Wznawianie zadania po aktualizacji i zatrzymanie modelu podczas obliczeń wymagają osobnej integracji.
- Wymagany jest rzeczywisty test na Androidzie 16 i sprawdzenie wyników CI.
- Wszystko bazuje na darmowym, otwartym oprogramowaniu.

Komenda testowa nie wymaga instalowania żadnej płatnej usługi ani przesyłania GGUF do VPS.
