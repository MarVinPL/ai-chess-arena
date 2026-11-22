# Decyzja właściciela: wpisy 1–8 w turnieju (2026-11-22)

## Zakres
Właściciel Areny potwierdził zgodność licencji modeli i oświadczył, że wcześniej zaliczyły testy. Nakazał dopuścić dokładnie osiem wersji wskazanych w `manifests/huggingface-models.json`, **bez ponownego testowania jako warunku dopuszczenia**.

Decyzja obejmuje wyłącznie udział w formacie `PURE_AI` i nie jest wynikiem nowego testu uruchomienia. Nie jest również instrukcją pobierania wag ani uruchomienia turnieju. Istniejące dowody poszczególnych testów pozostają w historycznych raportach; brak pełnego protokołu dla modelu nie jest oznaczany jako `PASS`.

## Kwalifikacja licencji (dla wybranego artefaktu GGUF)
W oparciu o źródła i przypięte artefakty opisane w `license-review-2026-11-14.md`, politykę proporcjonalnej kwalifikacji `docs/model-license-qualification-policy.md` i decyzję właściciela, kwalifikacja udziału dotyczy **jedynie wybranego pliku GGUF**, nie całego repozytorium, wszystkich helperów lub pochodzenia każdego elementu danych treningowych.

| ID | Model | Decyzja | Warunki |
|---:|---|---|---|
| 1 | Qwen2.5-0.5B-Instruct | eligible | Apache-2.0 |
| 2 | SmolLM2-360M-Instruct | eligible | Apache-2.0 dla wybranego artefaktu; dokumentacja datasetów pozostaje niepełna |
| 3 | Qwen2-0.5B-Instruct | eligible | Apache-2.0 |
| 4 | Meta-Llama-3.1-8B-Instruct | restricted | Meta Llama 3.1 Community License i AUP; obowiązki NOTICE, atrybucji i pozostałe warunki |
| 5 | Llama-3.2-3B-Instruct | restricted | Meta Llama 3.2 Community License i AUP; obowiązki NOTICE, atrybucji i pozostałe warunki |
| 6 | Bielik-11B-v3.0-Instruct | restricted | Oficjalne warunki Bielik v3, **minimum 18 lat dla użytkownika**, ograniczenia dot. danych osobowych |
| 7 | Bielik-7B-Instruct-v0.1 | restricted | CC BY-NC 4.0, cel niekomercyjny, atrybucja, oznaczanie zmian |
| 8 | Qwen3.8-27B | eligible | Apache-2.0 dla wybranego GGUF; `imatrix` i `mmproj` nie są zatwierdzone do użycia |

Dla 4/5: SHA-256 i rewizja **pochodnego GGUF** są przypięte, ale jego karta nie identyfikuje dokładnego SHA upstream base modelu. Nie deklarujemy rozwiązania tej kwestii; wybrany pochodny artefakt nadal podlega wszystkim warunkom Meta.

## Zasady operacyjne
- Decyzja dopuszczenia i rzeczywista gotowość techniczna to różne pojęcia. Jeżeli nie ma pełnego dowodu konkretnego testu, `technical_readiness` zostaje `not_evaluated`, bez fałszywego `PASS`.
- Brak wcześniejszego testu nie zatrzymuje samej kwalifikacji. Pierwsze prawdziwe uruchomienie nadal musi pomyślnie załadować dokładny, zweryfikowany artefakt.
- Nigdy nie pomijaj weryfikacji SHA-256, dostępnych zasobów, poprawności rewizji, zgodności wiążących warunków licencji i legalności każdego wykonanego ruchu.
- Nie omijaj 18+ dla Bielika v3. Jeśli wymóg wieku nie może zostać spełniony, model nie może zostać uruchomiony.
- Słabe ruchy, słaba polszczyzna czy wolne generowanie nie stanowią przyczyn odrzucenia.
- Zmiany w rejestrze nie uruchamiają pobierania modeli, nie zmieniają bazy produkcyjnej ani nie startują turnieju.

Źródła szczegółowe: `docs/model-audit/license-review-2026-11-14.md`, `docs/model-audit/smollm2-360m-provenance-2026-11-16.md` i oficjalne strony przypięte w `license_audit.evidence` manifestu.
