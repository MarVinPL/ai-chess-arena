# Regresja inicjalizacji dzienników Android Worker (#424)

| Przypadek | Oczekiwany wynik |
| --- | --- |
| Wyścig inicjalizacji z przydziałem koordynatora | Najwyżej jeden zapis wygrywa, a baza nie zawiera jednocześnie blokady i nowego zadania |
| Brak lub konflikt autoryzacji i dowodu instalatora | Oba dzienniki pozostają zablokowane |
| Przerwany zapis po pierwszym slocie lub przed publikacją znacznika | Stan po ponownym otwarciu nadal blokuje oba sloty |
| Błąd transakcji zwalniającej blokadę SQLite | Pozostaje pierwotna blokada, a potwierdzenie nie jest zapisywane |
| Utrata odpowiedzi po zatwierdzeniu | Ponowienie z tą samą tożsamością zwraca ten sam wynik. Stan Workera wymaga niezależnego uzgodnienia |
| Rotacja tokenu lub niezgodny identyfikator | Nie dochodzi do zwolnienia blokady |

Weryfikacja na hoście używa pełnych zestawów `python -m pytest -q tests`
oraz `cargo test --locked --lib` w `remote_worker_gui/src-tauri`. Workflow
`Worker selective builds` kompiluje APK Android ARM64 dla PR. Testy na urządzeniu
i włączenie ścieżki migracji należą do odrębnych zadań EPIC #406.
