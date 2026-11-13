# Audyt 10 wersji modeli w rejestrze AI Chess Arena

Data audytu: 2026-11-13. Audyt read-only; nie pobierano modeli, nie uruchamiano inferencji i nie odczytywano katalogu współdzielonych modeli.

## Wniosek wykonawczy

W bazie znajduje się 10 aktywnych wpisów wersji, ale **nie oznacza to 10 gotowych zawodników**:
- Wersje 1–3 mają komplet podstawowych danych artefaktu (nazwa pliku, SHA-256, rozmiar) i były już wpisane do turniejów. Są warunkowo gotowe, ale bieżąca dostępność plików i runtime nie została potwierdzona.
- Wersje 4–10 nie mają w DB nazwy pliku w polu `filename`, SHA-256 ani rozmiaru. Są katalogowymi wpisami bez kompletnego manifestu artefaktu.
- Wszystkie 10 rekordów mają `license_verified=1`, ale zapisane `license_evidence` jest puste albo sprzeczne z tym flagiem. Nie należy traktować samego flagu jako wystarczającego dowodu.
- VPS ma 4,01 GB (3,74 GiB) RAM, 2 vCPU i 2 GiB swapu; w chwili audytu dostępne było około 2,06 GiB RAM. Większość większych kandydatów nie mieści się w tej konfiguracji.
- Wersja #9 jest wpisana jako sam plik MTP draft, a nie pełny model docelowy. Nie jest poprawną samodzielną konfiguracją target.
- Nie potwierdzono zgodności modeli #4–10 z zainstalowanym buildem llamafile. Endpoint runtime `127.0.0.1:8088/health` nie odpowiadał.

## Dane rejestru i ocena

Wszystkie rekordy mają `active=1`, `status=catalogued`, `runtime=llamafile` i `managed_copy=0`. Katalog prywatny Areny `/home/debian/ai-chess-arena/managed-models` nie istnieje. Nie sprawdzano zawartości `/home/debian/ai-agent/models` ani żadnych plików w tym współdzielonym katalogu.

| ID | Wersja / konfiguracja z rejestru | Manifest DB | Licencja wg publicznej karty | Ocena |
|---:|---|---|---|---|
| 1 | Qwen2.5-0.5B-Instruct-Q4_K_M | 491 400 032 B, SHA-256 obecny, plik wskazany | Apache-2.0 | Warunkowo gotowy |
| 2 | SmolLM2-360M-Instruct-Q4_K_M | 270 590 592 B, SHA-256 obecny, plik wskazany | Apache-2.0 | Warunkowo gotowy |
| 3 | Qwen2-0.5B-Instruct-Q4_K_M | 397 805 248 B, SHA-256 obecny, plik wskazany | Apache-2.0 | Warunkowo gotowy |
| 4 | Meta-Llama-3.1-8B-Instruct, Q3_K_S | Brak filename/SHA/size w kolumnach manifestu | `llama3.1` – warunki Meta Llama 3.1 | Niegotowy |
| 5 | Llama-3.2-3B-Instruct, UD-IQ3_XXS | Brak filename/SHA/size w kolumnach manifestu | `llama3.2` – Community License Meta | Wymaga weryfikacji |
| 6 | Bielik-11B-v3.0-Instruct, Q4_K_M | Brak filename/SHA/size w kolumnach manifestu | Apache-2.0 na aktualnej karcie; dowody DB mówią `license_not_declared` | Niegotowy |
| 7 | Bielik-7B-Instruct-v0.1, Q3_K_S | Brak filename/SHA/size w kolumnach manifestu | CC BY-NC 4.0 – użycie niekomercyjne | Niegotowy |
| 8 | Qwen3.8-27B, UD-IQ1_S | Brak filename/SHA/size w kolumnach manifestu | Apache-2.0 | Niegotowy |
| 9 | Qwen3.8-27B-Uncensored draft Q8_0 | Brak filename/SHA/size; `draft_filename` również pusty | Apache-2.0 | Niegotowy – błędny typ artefaktu |
| 10 | Qwen3.8-27B-Uncensored, Q4_K_S | Brak filename/SHA/size w kolumnach manifestu | Apache-2.0 na karcie repozytorium | Niegotowy |

### Szczegółowe ustalenia

**Wersje 1–3**
- Wpisy mają dokładny rozmiar i SHA-256; źródła #1 i #2 są przypięte do rewizji repozytorium, a #3 ma przypiętą rewizję w `download_url`.
- Wszystkie trzy mają odrębne SHA-256, więc nie są duplikatami na podstawie zapisanych hashy.
- Były używane jako zawodnicy w turniejach #1–5. Nie oznacza to jednak, że pliki są obecnie dostępne: `managed_copy=0`, a współdzielonego katalogu modeli nie sprawdzano.
- Publiczne karty Qwen2.5, SmolLM2 i Qwen2 oznaczają licencję Apache-2.0.

**Wersje 4–7**
- Wersje #4–7 mają nazwę pliku tylko w `version_key`; nie mają kompletnego manifestu w kolumnach artefaktu ani zapisanego SHA-256/rozmiaru.
- #4: karta bartowski wskazuje wariant Q3_K_S o rozmiarze ok. 3,66 GB. To niemal cała pamięć RAM hosta (4,01 GB (3,74 GiB)), jeszcze przed runtime i cache. Licencja jest oznaczona jako `llama3.1`, a nie Apache-2.0.
- #5: karta Unsloth podaje ok. 1,37 GB dla UD-IQ3_XXS. To jedyny z większych kandydatów, który może być sprzętowo możliwy do sprawdzenia na tym hoście, ale pozostaje mały margines pamięci, brak manifestu i konieczność sprawdzenia warunków Llama 3.2 Community License oraz Acceptable Use Policy.
- #6: karta SpeakLeash opisuje model 11B i wariant Q4_K_M. Taka konfiguracja przekracza możliwości hosta z 4,01 GB (3,74 GiB) RAM. Publiczna karta wskazuje Apache 2.0, ale zapisane dowody licencji w DB mówią `license_not_declared`.
- #7: karta SpeakLeash podaje 3,16 GB dla Q3_K_S. Po doliczeniu runtime i cache nie jest to bezpieczny rozmiar dla hosta z 4,01 GB (3,74 GiB) RAM. CC BY-NC 4.0 ogranicza użycie do niekomercyjnego; trzeba uwzględnić ten warunek przed ewentualnym zatwierdzeniem.

**Wersje 8–10**
- #8: karta Unsloth wskazuje 27B, architekturę `qwen35` i 6,19 GB dla UD-IQ1_S. Sam plik jest większy niż cała fizyczna pamięć VPS; zgodność z obecnym buildem llamafile nie została potwierdzona.
- #9: plik `Qwen3.8-27B-Uncensored-draft-Q8_0.gguf` ma ok. 3,2 GB, ale jest głowicą MTP draft, a nie pełnym modelem docelowym. Dokumentacja źródłowa rozróżnia plik target od pliku draft. W rekordzie DB nie ma ani `filename`, ani `draft_filename`, więc ta pozycja nie jest poprawną konfiguracją target/draft.
- #10: karta mradermacher wskazuje ok. 15,9 GB dla Q4_K_S. To wielokrotnie przekracza 4,01 GB (3,74 GiB) RAM VPS. Wpis DB nie zawiera manifestu pliku.

## Spójność licencji

Każdy rekord ma `license_verified=1`, ale:
- #1–3 nie mają zapisanych obiektów `license_evidence`;
- #4, #6 i #7 mają `evidence_status=license_not_declared`, mimo ustawionego flagu;
- #5, #8, #9 i #10 mają `documents_found_metadata_missing`, ale bez zapisanych plików licencji i bez jednoznacznej nazwy licencji w obiekcie dowodowym.

Aktualne publiczne karty modeli pozwalają ustalić deklarowane oznaczenia licencji, ale nie zastępują ręcznego przeglądu pełnych warunków. W szczególności `llama3.1` i `llama3.2` nie są licencjami Apache; Bielik 7B v0.1 ma ograniczenie niekomercyjne. Nie zmieniano flagów licencyjnych w DB.

## Duplikaty i identyfikacja konfiguracji

- #1–3 mają różne, niepuste SHA-256.
- #4–10 nie mają SHA-256, więc nie da się wiarygodnie rozstrzygnąć duplikatów binarnych ani deduplikować ich po rzeczywistym artefakcie.
- #9 i #10 pochodzą z tej samej rodziny modelu Qwen3.8-27B-Uncensored, ale opisują różne role artefaktów: draft MTP i model target. Nie wolno traktować samego draftu jako drugiego pełnego modelu.
- Nie zmieniono żadnych wpisów ani rosteru.

## Zgodność runtime i sprzęt

Odczyt hosta:
- x86_64, Intel Haswell, 2 vCPU;
- 4 012 273 664 B RAM (ok. 3,74 GiB), około 2,06 GiB dostępne w chwili odczytu;
- 2 GiB swapu;
- binarium llamafile istnieje i ma SHA-256 `756bf97febae557084e8ced9bde7a1cb0c7f6ce778530d582afa219406237a50`;
- endpoint `127.0.0.1:8088/health` nie odpowiadał.

Nie uruchamiano binarium, nie pobierano modeli i nie wykonywano prób ładowania. Wersja/build llamafile nie została ustalona, dlatego zgodność z architekturą Qwen3.8/Qwen3.5 i MTP pozostaje niepotwierdzona.

## Rekomendacje

1. Pozostawić turniej #5 bez zmian; jego roster nadal obejmuje wyłącznie wersje 1–3.
2. Traktować wersje 1–3 jako jedyne warunkowo gotowe wpisy, do czasu potwierdzenia dostępności plików i ponownego uruchomienia health-check runtime.
3. Nie dopuszczać #4, #6–#10 do nowego turnieju na obecnym VPS.
4. Dla #5 (Llama 3.2 3B) przed jakąkolwiek decyzją uzupełnić manifest SHA/size/filename, ręcznie sprawdzić licencję i wykonać test na kopii/stagingu po odzyskaniu runtime.
5. Nie oznaczać #9 jako samodzielnego zawodnika. Najpierw wymaga poprawnej definicji pary target/draft i kompletnego manifestu obu plików; obecny host i tak nie ma zasobów na target 27B.
6. Dla każdego wpisu bez SHA-256 nie zatwierdzać użycia ani deduplikacji.

## Źródła zewnętrzne

- Qwen2.5-0.5B GGUF: https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF
- SmolLM2-360M GGUF: https://huggingface.co/QuantFactory/SmolLM2-360M-Instruct-GGUF
- Qwen2-0.5B GGUF: https://huggingface.co/Qwen/Qwen2-0.5B-Instruct-GGUF
- Meta Llama 3.1 8B GGUF: https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF
- Llama 3.2 3B GGUF: https://huggingface.co/unsloth/Llama-3.2-3B-Instruct-GGUF
- Bielik 11B v3 GGUF: https://huggingface.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF
- Bielik 7B v0.1 GGUF: https://huggingface.co/speakleash/Bielik-7B-Instruct-v0.1-GGUF
- Qwen3.8-27B GGUF: https://huggingface.co/unsloth/Qwen3.8-27B-GGUF
- Qwen3.8-27B-Uncensored GGUF: https://huggingface.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF
- mradermacher Qwen3.8-27B-Uncensored GGUF: https://huggingface.co/mradermacher/JonathanColetti-Qwen3.8-27B-Uncensored-GGUF

## Zakres bezpieczeństwa

Audyt był read-only. Nie pobierano ani nie instalowano modeli, nie zmieniano DB, statusów licencji, plików, runtime, wspólnego katalogu modeli ani turniejów. Turniej #5 pozostaje `planned` z niezmienionym rosterem.
