# SmolLM2-360M — bezpieczny plan eksperymentalnego douczania

Status: **przygotowanie danych; trening niewykonany**  
Issue: #133  
Model bazowy: `HuggingFaceTB/SmolLM2-360M-Instruct`  
Kandydat GGUF w Arenie: `QuantFactory/SmolLM2-360M-Instruct-GGUF`

## Co przygotowano

- Deterministyczny generator ośmiu syntetycznych pozycji K+H vs K z jednym matem w jednym.
- Każdy target jest wyliczany ponownie przez `python-chess`; generator odrzuca nielegalne FEN-y, duplikaty i pozycje bez dokładnie jednego ruchu matującego.
- Dane są samodzielnie wygenerowane na potrzeby projektu, bez pobierania partii, komentarzy ani zewnętrznych korpusów; deklarowana licencja danych: CC0-1.0.
- Podział train/validation/test jest zapisany w rekordach. Ten mały zbiór to wyłącznie smoke-test formatu i walidatora, nie materiał wystarczający do uczenia modelu.

## Granice eksperymentu

- Nie pobierać wag bazowych Transformers ani dodatkowych modeli w ramach tego etapu.
- Nie trenować na GGUF; adapter LoRA wymaga kompatybilnych wag bazowych i stosu treningowego.
- Nie zakładać, że plik GGUF 270 MB oznacza wymagane RAM dla treningu.
- VPS ma ograniczoną pamięć; trening na nim jest wstrzymany do osobnego pomiaru zasobów i oceny wykonalności.
- Nie używać płatnych API, płatnych GPU ani usług z automatycznym naliczaniem opłat.
- Oryginalne wagi, katalog chroniony `/home/debian/ai-agent/models`, DB, allowlista i turnieje pozostają nietknięte.

## Plan właściwego treningu

1. Rozszerzyć zbiór o większą liczbę niezależnych pozycji i klas zadań: mat w 1/2, obrona, promocja, szach, bicie, roszada, końcówki i taktyka.
2. Dla zadań wymagających oceny „najlepszego ruchu” używać wyłącznie etykiet zweryfikowanych przez silnik referencyjny i zachować wersję silnika, parametry oraz głębokość.
3. Przed użyciem danych zewnętrznych zapisać dokładny snapshot, licencję i warunki redystrybucji; odrzucać dane o niejasnym pochodzeniu.
4. Przygotować odizolowane środowisko treningowe i lokalny-only loader, który nie pobiera modelu automatycznie.
5. Zastosować LoRA/QLoRA dopiero po potwierdzeniu pamięci RAM/VRAM; zapisywać adapter osobno od modelu bazowego.
6. Porównać bazę i adapter na identycznym, nieużywanym podczas treningu zbiorze testowym.
7. Raportować osobno legalność ruchów, trafność targetu, maty, promocje, czas inferencji i wyniki sparingu.

## Bramka przed treningiem

Wymagane osobne potwierdzenie pobrania wag bazowych Transformers oraz uruchomienia treningu. Zgoda na wcześniejszy test GGUF nie jest automatycznie zgodą na pobranie innego artefaktu ani trening. Do czasu tej zgody pipeline pozostaje przygotowaniem danych i walidacji.
