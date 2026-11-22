# Pełna decyzja właściciela o dopuszczeniu ośmiu modeli do PURE_AI — 2026-11-22

## Rozstrzygnięcie

**Właściciel AI Chess Arena dopuszcza wszystkie 8 modeli z obecnego przypiętego manifestu do turniejów w formacie `PURE_AI`, bez dodatkowych blokad administracyjnych oraz bez ponawiania wcześniejszych testów kwalifikacyjnych.** Właściciel potwierdził, że modele wcześniej zaliczyły testy i ich licencje są zgodne z zamierzonym wykorzystaniem.

To **pełna decyzja o dopuszczeniu do rejestru uczestników**, a nie prośba o rozpoczęcie nowego audytu czy wstępne dopuszczenie warunkowe. Powtarzany test wydajności lub uruchomienia nie może być warunkiem samej decyzji administracyjnej. Ta decyzja nie stanowi nowego protokołu testowego, nie zastępuje faktycznie zebranych logów i nie oznacza wykonania nowej inferencji.

## Modele objęte decyzją

| ID | Model (przypięty GGUF) | Decyzja właściciela | Wiążące warunki przy użyciu |
|---:|---|---|---|
| 1 | Qwen2.5-0.5B-Instruct | **Dopuszczony** | Apache-2.0 |
| 2 | SmolLM2-360M-Instruct | **Dopuszczony** | Apache-2.0 dla wybranego GGUF; historyczne braki dokumentacji datasetów pozostają odnotowane |
| 3 | Qwen2-0.5B-Instruct | **Dopuszczony** | Apache-2.0 |
| 4 | Meta-Llama-3.1-8B-Instruct | **Dopuszczony** | Meta Llama 3.1 Community License, AUP, NOTICE i atrybucja |
| 5 | Llama-3.2-3B-Instruct | **Dopuszczony** | Meta Llama 3.2 Community License, AUP, NOTICE i atrybucja |
| 6 | Bielik-11B-v3.0-Instruct | **Wskazany do dopuszczenia** | Warunki producenta Bielik v3 wymagają **ukończonych 18 lat**, a także spełnienia ograniczeń dot. danych osobowych i innych warunków użycia; bez ich spełnienia model nie może być uruchomiony |
| 7 | Bielik-7B-Instruct-v0.1 | **Dopuszczony do niekomercyjnej Areny** | CC BY-NC 4.0: użycie niekomercyjne, atrybucja, oznaczenie zmian |
| 8 | Qwen3.8-27B | **Dopuszczony** | Apache-2.0 dla wybranego GGUF; odrębne helpery `mmproj` / `imatrix` nie są objęte decyzją |

Decyzja obejmuje wyłącznie przypięte `repo_id`, `revision`, `filename` i `sha256` z `manifests/huggingface-models.json`, a nie dowolne wydania lub dodatkowe pliki. Historyczne `license_review_status` raportów nie zmieniają się automatycznie; są śladem wcześniejszego zakresu audytu, nie nowym pomiarem.

## Brak powtarzania testów i brak blokad administracyjnych

- Nie wymagaj kolejnego pozytywnego testu wydajności, testu Elo ani powtórnego testu startowego **do samej kwalifikacji modelu**.
- Nie traktuj brakującego protokołu testowego jako powodu do odrzucenia decyzji właściciela. Nie twórz jednak fikcyjnych danych ani statusu `PASS`, jeżeli jego pochodzenie nie jest udokumentowane.
- Powolna inferencja, słaba gra lub nieidealny język odpowiedzi nie dyskwalifikują.
- Przy realnym użyciu nadal wymagaj zgodnego z manifestem pliku, SHA-256, legalnego ruchu, dostępnych zasobów oraz przestrzegania wiążących warunków korzystania.
- **Blokada wynikająca z rzeczywistego zakazu producenta nie jest blokadą administracyjną Areny.** Dla Bielika v3 należy respektować 18+; nie wolno przedstawiać go jako bezwarunkowo dostępnego każdemu uczestnikowi.
- Nic w tym dokumencie nie uruchamia automatycznie pobierania, inferencji lub turnieju i nie zmienia bazy produkcyjnej.

## Stan implementacji

Dokument utrwala **decyzję właściciela**. Zgodność kodu aplikacji, lokalnej bazy, `model-allowlist.json` i uruchomionego środowiska z tą decyzją wymaga osobnej weryfikacji oraz implementacji w zakresie Issue #259. Nie należy twierdzić, że produkcyjna Arena już automatycznie honoruje wszystkie osiem pozycji bez weryfikacji aktualnych ustawień.

## Źródła

- `docs/model-audit/license-review-2026-11-14.md`
- `docs/model-audit/smollm2-360m-provenance-2026-11-16.md`
- `docs/model-license-qualification-policy.md`
- `manifests/huggingface-models.json` wraz z dowodami `license_audit.evidence`

## Aktualizacja #266: autorytatywna baza

Lista dopuszczonych wersji modeli pochodzi teraz z SQLite, z model_admission_decisions oraz przypiętych model_versions. Poprzedni plik allowlisty został usunięty z kodu wykonywalnego, a z Gitem nie wykonuje się żadnej blokującej kontroli synchronizacji. Decyzje, ograniczenia i historia pozostają zapisane w bazie. Wzmianki o dawnym pliku powyżej są wyłącznie historyczne.
