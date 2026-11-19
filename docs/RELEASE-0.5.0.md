# TeddyShelf 0.5.0

Die Anwendung heißt jetzt TeddyShelf. Fenster, Seitenleiste, Programmmetadaten, Dokumentation, Demo-Screenshots, Windows-Programm und Paketnamen verwenden den neuen Namen. Die Python-Distribution heißt `teddyshelf`; der gleichnamige Startbefehl ist verfügbar. Der bisherige Startbefehl und interne Modulpfad bleiben kompatibel.

Neue Installationen speichern Daten unter `%LOCALAPPDATA%/TeddyShelf`. Bestehende Installationen verwenden weiterhin ihren bisherigen TeddyStudio-Datenordner, sofern noch kein eigener neuer Datenbestand vorhanden ist. `TEDDYSHELF_DATA` wird bevorzugt; `TEDDYSTUDIO_DATA` bleibt als bisheriger Override nutzbar. Einstellungen, Aufträge, Wiederaufnahmestände und Export-Manifeste werden nicht durch die Umbenennung gelöscht oder verschoben.
