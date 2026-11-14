# Częściowy audyt licencji modeli — 2026-11-14

## Status i zakres

To jest **częściowy przegląd dowodów**, a nie kompletne zamknięcie Issue #52 ani opinia prawna. Oceniono bezpieczny do analizy zakres rejestru (pozycje 1–8). Niektórych pozostałych wpisów celowo nie badano; nie zmieniano ich danych ani statusów. W konsekwencji wymaganie audytu wszystkich wpisów pozostaje otwarte.

Nie pobierano wag, nie uruchamiano inferencji, nie zmieniano produkcyjnej bazy danych, rosterów turniejów ani usług. Nie odczytywano współdzielonego katalogu modeli.

## Zasady klasyfikacji

- `verified_permissive`: tekst licencji i warunki dla dokładnego artefaktu sprawdzone; nie oznacza zgody na użycie.
- `verified_restricted`: tekst licencji i warunki sprawdzone, ale obowiązują dodatkowe ograniczenia lub obowiązki.
- `review_required`: brak pełnego, przypiętego dowodu dla artefaktu pochodnego albo niejasne warunki upstream.
- `incompatible`: warunki są sprzeczne z przyjętą polityką projektu.
- `evidence_missing`: nie udało się uzyskać wiarygodnego dowodu.

`license_review_status` opisuje stan audytu, a nie zgodę właściciela na pobranie, uruchomienie, komercyjne wykorzystanie ani udział w turnieju. Funkcja importu manifestu nadal wymusza `license_verified=0`.

## Wyniki

| ID rejestru | Model / artefakt | Deklarowana licencja | Status | Uzasadnienie |
|---:|---|---|---|---|
| 1 | Qwen2.5-0.5B-Instruct Q4_K_M GGUF | Apache-2.0 (upstream) | `review_required` | Oficjalny upstream udostępnia pełny tekst Apache-2.0, ale nie ma w repo przypiętej rewizji dokładnego repozytorium kwantyzacji i kompletnego dowodu dla artefaktu GGUF. |
| 2 | SmolLM2-360M-Instruct Q4_K_M GGUF | Apache-2.0 (upstream) | `review_required` | Oficjalna karta Hugging Face wskazuje Apache-2.0, ale dokładna rewizja pochodnego repozytorium GGUF i jego pliki licencyjne nie są przypięte w rejestrze. |
| 3 | Qwen2-0.5B-Instruct Q4_K_M GGUF | Apache-2.0 | `review_required` | README pochodnego repozytorium deklaruje kwantyzację oficjalnego Qwen2 i Apache-2.0; dostępny identyfikator rewizji jest skrócony, a rejestr nie przechowuje pełnego SHA rewizji źródła. |
| 4 | Meta Llama 3.1 8B Instruct Q3_K_S GGUF | Llama 3.1 Community License | `verified_restricted` | Pinned README wskazuje dokładny model bazowy, autora kwantyzacji i zawiera tekst Community License. Obowiązują AUP oraz warunki redystrybucji i atrybucji Meta. |
| 5 | Llama 3.2 3B Instruct UD-IQ3_XXS GGUF | Llama 3.2 Community License | `verified_restricted` | Pinned README wskazuje dokładny model bazowy i licencję Meta; sprawdzono Community License i AUP. Jest to wariant tekstowy; unijne zastrzeżenie z AUP dotyczące modeli multimodalnych nie opisuje tego wariantu. |
| 6 | Bielik 11B v3.0 Instruct Q4_K_M GGUF | Apache-2.0 deklarowane przez upstream | `review_required` | Karta modelu bazowego deklaruje Apache 2.0, ale wskazuje też Terms of Use. Nie uzyskano kompletnego, przypiętego tekstu warunków dla GGUF i modelu bazowego; sama etykieta Apache nie wystarcza. |
| 7 | Bielik 7B Instruct v0.1 Q3_K_S GGUF | CC BY-NC 4.0 deklarowane dla wariantu | `review_required` | Dostępne materiały opisują ograniczenie NonCommercial i niejasność sytuacji prawnej; nie uzyskano pełnego tekstu licencji z dokładnej przypiętej rewizji GGUF ani pełnej analizy relacji do upstream. Nie zatwierdzać do użycia komercyjnego. |
| 8 | Qwen3.8-27B UD-IQ1_S GGUF | Apache-2.0 upstream | `review_required` | Oficjalny model bazowy udostępnia pełny tekst Apache-2.0, ale nie uzyskano treści README dokładnej przypiętej rewizji pochodnego GGUF. Nie można automatycznie przenieść deklaracji z bieżącej karty na konkretny artefakt. |

## Szczegółowe warunki potwierdzone dla modeli Meta

### Llama 3.1

- Pinned repo GGUF: `bartowski/Meta-Llama-3.1-8B-Instruct-GGUF`, revision `bf5b95e96dac0462e2a09145ec66cae9a3f12067`.
- README wskazuje `meta-llama/Meta-Llama-3.1-8B-Instruct` jako base model, `bartowski` jako quantizer i zawiera tekst Llama 3.1 Community License.
- Licencja przyznaje ograniczone, niewyłączne, ogólnoświatowe i nieodpłatne prawa; nie jest Apache-2.0 ani licencją bezwarunkowo permissive.
- Przy dystrybucji materiałów lub pochodnych wymagane są m.in. kopia umowy, widoczne oznaczenie „Built with Llama” w powiązanych materiałach produktu oraz zachowanie wskazanej noty copyright/attribution w pliku NOTICE.
- AUP jest włączona przez odesłanie. Dodatkowe warunki komercyjne przewidują konieczność uzyskania licencji Meta, jeśli przekroczony jest próg 700 mln miesięcznych aktywnych użytkowników określony w umowie.
- Wniosek: `verified_restricted` jako status audytu warunków, nie jako zgoda na dystrybucję lub uruchomienie.

### Llama 3.2

- Pinned repo GGUF: `unsloth/Llama-3.2-3B-Instruct-GGUF`, revision `e7d0997e49c9cb00d88b4c1a6a16aa894b0bbc31`.
- README wskazuje `meta-llama/Llama-3.2-3B-Instruct` jako base model i odsyła do Llama 3.2 Community License.
- Umowa wymaga m.in. kopii umowy przy dystrybucji, oznaczenia „Built with Llama”, zachowania noty attribution w NOTICE oraz zgodności z AUP.
- Umowa zawiera dodatkowy próg 700 mln miesięcznych aktywnych użytkowników.
- AUP zawiera dodatkowe ograniczenia użycia. Wyjątek dotyczący osób/podmiotów w UE odnosi się do modeli multimodalnych; badany wariant 3B jest modelem tekstowym.
- Wniosek: `verified_restricted` jako status audytu warunków, nie jako zgoda na użycie.

## Pozostałe ustalenia i ryzyka

1. Apache-2.0 upstream nie wystarcza do zatwierdzenia kwantyzacji. Potrzebne są: pełny SHA rewizji repo GGUF, README/licencja z tej rewizji, wskazanie base model, plików tokenizer/config oraz potwierdzenie, że kwantyzacja nie wprowadza dodatkowych warunków.
2. Bielik 11B wymaga pozyskania i przeczytania Terms of Use wskazanych przez autora; do tego czasu status pozostaje `review_required`.
3. Bielik 7B v0.1 nie może być traktowany jako permissive. Dostępne materiały wskazują CC BY-NC 4.0 dla wariantu i ograniczenie NonCommercial; nie wyciągać wniosku o zgodzie na użycie komercyjne.
4. Dla wpisów 1–3 brakuje pełnych, przypiętych dowodów dla konkretnych artefaktów GGUF; statusy pozostają `review_required`.
5. Nie zmieniono `license_verified` w bazie ani żadnego rosteru turniejowego. Nie wykonano pobierania ani testów runtime.
6. Niektóre wpisy rejestru pozostają poza zakresem tego przeglądu. Issue #52 nie może zostać zamknięte na podstawie tego dokumentu.

## Źródła

### Repozytoria i dokładne rewizje GGUF

- Llama 3.1 GGUF README (pinned): https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/blob/bf5b95e96dac0462e2a09145ec66cae9a3f12067/README.md
- Llama 3.2 GGUF README (pinned): https://huggingface.co/unsloth/Llama-3.2-3B-Instruct-GGUF/blob/e7d0997e49c9cb00d88b4c1a6a16aa894b0bbc31/README.md
- Qwen2 GGUF README: https://huggingface.co/QuantFactory/Qwen2-0.5B-Instruct-GGUF/blob/main/README.md

### Oficjalne warunki upstream

- Meta Llama 3.1 Community License: https://dev.meta.ai/llama/llama3_1/license
- Meta Llama 3.1 Acceptable Use Policy: https://dev.meta.ai/llama/llama3_1/use-policy
- Meta Llama 3.2 Community License: https://dev.meta.ai/llama/llama3_2/license
- Meta Llama 3.2 Acceptable Use Policy: https://dev.meta.ai/llama/llama3_2/use-policy
- Qwen2.5 LICENSE: https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/blob/main/LICENSE
- Qwen2 LICENSE: https://huggingface.co/Qwen/Qwen2-0.5B-Instruct/blob/main/LICENSE
- SmolLM2 model card: https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct
- Bielik 11B v3 model card: https://huggingface.co/speakleash/Bielik-11B-v3.0-Instruct
- Bielik 7B v0.1 model card: https://huggingface.co/speakleash/Bielik-7B-Instruct-v0.1
- Qwen3.8-27B upstream LICENSE: https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/LICENSE

## Zastrzeżenie

To techniczny przegląd publicznych warunków i dowodów, nie porada prawna. W razie komercyjnej dystrybucji, udostępniania wag lub niejasności co do warunków bazowych potrzebna jest odrębna decyzja właściciela projektu i — w razie potrzeby — konsultacja prawna.
