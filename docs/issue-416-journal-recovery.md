# Uzgadnianie pary dzienników po przerwaniu (#416)

Natywna procedura otrzymuje wcześniej zachowaną, zaufaną transakcję instalatora.
Przed wysłaniem żądania zapisuje prywatny plik intencji zawierający losowy nonce
oraz skróty powiązania z serwerem, Worker ID, tokenem i zapisem sesji instalatora.
Token pozostaje w magazynie poświadczeń. Zapis intencji blokuje zwykłe operacje
na dziennikach, w tym rezerwację aktualizacji i zmianę tokenu.

| Punkt przerwania | Zachowanie przy ponowieniu |
| --- | --- |
| Intencja zapisana, odpowiedź o blokadzie utracona | Klient używa pierwotnego nonce. Koordynator ponawia tylko potwierdzenie tej samej blokady dla oryginalnego tokenu, Workera i adresu. |
| Zapisano znacznik roboczy i ukończono jeden lub oba sloty | Po ponownej weryfikacji dowodów klient może dokończyć tę samą parę, jeśli oba sloty mają bezpieczny stan i brak sprzecznego rekordu. |
| Para zatwierdzona, odpowiedź o zakończeniu utracona | Klient ponawia wyłącznie zatwierdzenie istniejącej pary. Intencja pozostaje do czasu prawidłowej odpowiedzi. |
| Częściowy lub uszkodzony plik, inna sesja, inny token albo niespójny slot | Zatrzymaj operację i zachowaj zapisy. Wymagane jest odrębne ręczne uzgodnienie. |
| Brak sieci lub niewystarczający dowód instalatora | Ponów po odzyskaniu połączenia i pierwotnych danych. Blokada pozostaje. |

Powtórzenie nie zwalnia rezerwacji aktualizacji. Brak widocznej sesji lub
sekretu nie jest dowodem. Żaden synchroniczny mutex nie obejmuje żądania HTTP.
Podłączenie procedury do interfejsu i dostarczenie zaufanego kontekstu
instalatora pozostaje w #417.
