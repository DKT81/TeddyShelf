# TeddyShelf 0.2.2

## Textfelder und Details

Einzeilige Eingabefelder erhalten eine zur Schrift passende Mindesthöhe und kleinere vertikale Innenabstände. Der Metadateninspektor scrollt, wenn sein Bereich klein gezogen wird; die Felder werden nicht mehr unter die benötigte Texthöhe zusammengedrückt.

## Legacy-Playlists und Importvoraussetzungen

M3U-Dateien werden einschließlich UTF-8, Windows-1252 und CP850 gelesen. Zusätzliche Leerzeichen um Einträge, Windows-Schreibweisen, Unicode-Normalisierung, Anführungszeichen und lokale Datei-URLs werden berücksichtigt. Eine Reihenfolge wird nur übernommen, wenn jeder vorhandenen Audiodatei genau ein Playlist-Eintrag zugeordnet werden kann. Fehlende, zusätzliche oder doppelte Einträge bleiben ein Prüfgrund. Echte Audiofehler bleiben erhalten.

Ungeklärte Audio-/Reihenfolgefehler werden vor dem Anlegen eines Imports angezeigt. Im Worker werden identische Fehlermeldungen nur einmal ausgegeben, ergänzt um den Weg zur Korrektur.

Nach dem Update den betroffenen Quellordner erneut einlesen. Bereits angelegte Aufträge enthalten unveränderliche alte Momentaufnahmen. Beim individuellen Wiederholen eines fehlgeschlagenen Imports bietet die App nach einer Korrektur ausdrücklich einen neuen Auftrag mit aktuellen Sammlungsdaten an; Ziel und Profil bleiben aus dem ursprünglichen Auftrag erhalten. Der alte Verlauf bleibt bestehen. Noch ungeklärte aktuelle Quellen werden nicht übernommen. Bei unveränderter Quelle gilt die bisherige Wiederaufnahme.

## Aufträge und Verlauf

- Mehrere Zeilen mit Strg-/Umschalttaste auswählen und über „Ausgewählte löschen“ entfernen.
- „Fertige ausblenden“ blendet bestätigte und lokal gespeicherte Aufträge ohne Datenverlust aus.
- „Verlauf bereinigen“ entfernt abgeschlossene und fehlgeschlagene Aufträge samt Protokollen; wartende und unterbrochene bleiben erhalten.
- Löschen ist nur bei angehaltener Warteschlange möglich. Eine Prozesssperre schützt gegen einen parallel laufenden Worker. Aktive Aufträge werden auch in der Datenbank nicht gelöscht. Aufträge und zugehörige Protokolle werden in einer Transaktion entfernt.
- Audiodateien, Exportdateien und TeddyCloud-Inhalte werden durch das Löschen von Aufträgen nicht verändert.

## Vorgemerkt

Creative-Tonie aus frei hinzugefügten Tracks steht auf [der To-do-Liste](TODO.md). Auf ausdrücklichen Wunsch wurde diese Funktion noch nicht umgesetzt.
