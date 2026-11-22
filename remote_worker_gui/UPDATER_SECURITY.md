# Android Worker — aktualizator APK (Issue #196)

Stan: specyfikacja bezpieczeństwa; nie jest to gotowy instalator.

1. Wykrywaj tylko stabilne wydania worker-vX.Y.Z, nowsze od aktualnej wersji.
2. Pobieraj wyłącznie APK ARM64 przez HTTPS z zaufanego źródła, do prywatnego katalogu aplikacji, z limitem rozmiaru i postępem.
3. Weryfikuj SHA-256, podpis APK względem zainstalowanego certyfikatu, package name i versionCode. Sam SHA-256 obok APK nie uwierzytelnia wydawcy.
4. Blokuj instalację bez uwierzytelnionego i świeżego potwierdzenia idle od koordynatora. Sprawdzaj ponownie przed instalacją.
5. Uruchamiaj systemowy instalator Android przez FileProvider; wymagaj potwierdzenia użytkownika. Bez root i cichej instalacji.
6. Obsłuż błędny podpis, downgrade, brak miejsca, zerwane pobieranie, przekierowania i aktywne zadania.
7. Testy Android 16: scenariusze pozytywne i negatywne, szczególnie unknown idle.

Obecny backend nie ma jeszcze trwałego rejestru zadań; nie wolno uznawać unknown za idle.
