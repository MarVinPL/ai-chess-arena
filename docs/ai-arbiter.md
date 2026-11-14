# AI Arbiter — powtarzalne manewry

AI Arbiter jest warstwą obserwacyjną i doradczą. Nie wybiera ruchu, nie modyfikuje planszy, nie zmienia listy legalnych ruchów i nie rozstrzyga partii.

## Wykrywanie

- Wykrywa, gdy ta sama strona wykonała dwukrotnie z rzędu ten sam trzyruchowy cykl tej samej figury przez trzy pola.
- Analiza korzysta z historii legalnych ruchów i działa niezależnie dla białych i czarnych.
- Formalne remisy, w tym trzykrotne powtórzenie pozycji i reguła 50 ruchów, pozostają obsługiwane przez `python-chess` (`is_game_over(claim_draw=True)`). Heurystyka stagnacji nie kończy partii.

## Interwencja

- Sędzia dodaje neutralny komunikat do promptu aktualnie grającego modelu: ponownie oceń pozycję i samodzielnie wybierz ruch z legalnej listy.
- Komunikat nie ujawnia konkretnego ruchu, planu ani przewagi.
- Limit: maksymalnie 3 interwencje na partię. Po osiągnięciu limitu gra przebiega bez kolejnych komunikatów sędziego.
- Każdy ruch nadal przechodzi dotychczasową walidację legalności i ograniczenia anty-oscylacyjne.

## Audyt

W metadanych ruchu zapisywane są typ i powód sygnału, kolor, model, odpowiedź modelu, czas odpowiedzi i wybrany legalny ruch. API szczegółów partii udostępnia `arbiter_events`; lista partii pokazuje liczbę interwencji. Brak interwencji oznacza wartość `0`.
