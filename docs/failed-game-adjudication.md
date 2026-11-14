# Konserwatywne rozstrzyganie przerwanych partii

Arena może rozstrzygnąć partię zakończoną awarią tylko wtedy, gdy:

- awaria wystąpiła podczas ruchu jednego z zawodników;
- zapis historii zawiera co najmniej 40 legalnych półruchów;
- odtworzona historia legalnych ruchów daje dokładnie ten sam FEN co zapisany stan awarii;
- nie odnotowano próby nielegalnego ruchu UCI ani ruchu zablokowanego przez regułę antyoscylacyjną;
- lokalny silnik UCI został jawnie wskazany przez operatora w zmiennej środowiskowej `ARENA_ANALYSIS_ENGINE`;
- silnik dwukrotnie przeanalizował tę samą pozycję: na głębokości 12 i 16;
- oba wyniki wskazują przewagę co najmniej +8,00 pionka dla przeciwnika zawodnika, którego ruch się nie powiódł, a najlepszy ruch jest taki sam w obu analizach.

Wszystkie warunki muszą być spełnione. Każdy brak danych, błąd silnika, rozbieżność FEN, zbyt krótka partia, niestabilna analiza albo mniejsza przewaga oznacza status `failed` / `unresolved` — bez przyznania punktów.

## Konfiguracja

Ustaw `ARENA_ANALYSIS_ENGINE` na bezwzględną ścieżkę do już zainstalowanego, wykonywalnego silnika UCI (np. lokalnego Stockfisha), w środowisku usługi areny. Arena nie pobiera ani nie instaluje silników. Brak zmiennej lub niedostępny plik oznacza fail-closed.

## Audyt i rankingi

Każda awaria zapisuje FEN, legalną historię ruchów, czas, przyczynę, stronę na ruchu, model oraz wynik analizy. Rozstrzygnięta analiza otrzymuje `adjudication_status=adjudicated`, zapisany wynik 1-0/0-1 i zwycięzcę pary. Nierozstrzygnięta partia ma `result=failed` i `adjudication_status=unresolved`.

Elo i tabela turniejowa uwzględniają tylko poprawne wyniki 1-0, 0-1 i 1/2-1/2. Nierozstrzygnięte awarie nie są traktowane jako remisy. Adjudykowane wyniki liczą się dokładnie raz i są oznaczone w historii oraz terminarzu.

To nie jest mechanizm automatycznego karania zawodnika za awarię. Wynik jest przyznawany wyłącznie na podstawie niezależnej, lokalnej analizy pozycji i z zachowaniem powyższych progów.
