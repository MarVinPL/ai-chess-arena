# Wyłączenie wpisów 9–10 z AI Chess Arena

Stan: obowiązuje od włączenia walidacji manifestu i zabezpieczeń runtime w ramach Issue #71.

## Wpisy objęte wyłączeniem

| Manifest ID | Repozytorium | Status audytu | Dopuszczenie |
|---:|---|---|---|
| 9 | `JonathanColetti/Qwen3.8-27B-Uncensored-GGUF` | `review_required` | Wyłączony |
| 10 | `mradermacher/JonathanColetti-Qwen3.8-27B-Uncensored-GGUF` | `review_required` | Wyłączony |

Wyłączenie jest niezależne od deklaracji licencji repozytorium pochodnego. Tag licencji ani ręczne ustawienie `license_verified` nie omija blokady.

## Wymuszone ograniczenia

- Manifest wymaga `arena_admission.status=excluded` oraz wartości `false` dla pobierania, runtime, allowlisty i udziału w turniejach.
- Walidator odrzuca usunięcie lub zmianę tych ograniczeń oraz próbę przeniesienia wyłączenia na inne wpisy.
- Rejestracja rozpoznanych repozytoriów jest odrzucana.
- Importer audytu #52 nie aktualizuje rekordów 9–10.
- Nowe turnieje pomijają rozpoznane wpisy; istniejący turniej zawierający taki wpis pozostaje zablokowany na etapie preflight.
- Pobieranie targetu i pliku draft oraz ręczne zatwierdzanie licencji są blokowane.
- Nie są pobierane wagi, nie jest uruchamiana inferencja i nie są zmieniane dane produkcyjne.

To jest techniczna polityka dopuszczenia do AI Chess Arena, a nie opinia prawna o licencji ani ogólna ocena modelu poza projektem.


## 2026-11-19 – wycofanie z aktywnego katalogu

Właściciel zdecydował o usunięciu modeli 9–10 z aktywnego manifestu i allowlisty z powodu rozmiaru artefaktów (ok. 16,81 GB i 15,83 GB) w relacji do zasobów obecnego VPS (3,7 GiB RAM, 2 vCPU). Nie pobierano wag ani nie uruchamiano modeli. Historyczne raporty pozostają jako ślad audytowy. Zmiana manifestu nie usuwa ani nie modyfikuje istniejących rekordów w bazie danych; nie wykonano migracji ani deployu.
