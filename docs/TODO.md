# Geplante Funktionen

## Creative-Tonie aus frei zusammengestellten Tracks

Auf Wunsch vom 17.11.2026 für später vorgemerkt, ausdrücklich noch nicht umsetzen.

- Neues Hörspiel unabhängig von einem bestehenden Quellordner erstellen.
- Audiodateien per Drag-and-drop hinzufügen, entfernen und umsortieren.
- Eigene Titel-, Cover- und Custom-Tonie-Metadaten vergeben.
- Quellen unverändert lassen; optional einen eigenen Projekt-/Exportordner erzeugen.
- Geordnete Tracks als zusammengehörigen Inhalt in eine TAF mit Kapiteln umwandeln und über die bestehenden verifizierten Importfunktionen an TeddyCloud übertragen.
- Projekt speichern und später weiterbearbeiten können.

Fachlich entspricht das einem eigenen Hörspielordner mit einer geordneten Audiozusammenstellung. Technisch kann ein Projekt auf vorhandene Dateien verweisen; Kopien in einen neuen Ordner sind dafür nicht zwingend nötig. Keine RFID-Zuordnung vorsehen.

## Web-/YouTube-Download als Audio- und Videoquelle

In Version 0.7.0 umgesetzt: Vorschau für Einzelvideos und Playlists, geordnete Einzelauswahl, eigene Download-Aufträge, Audio-/Videoausgabe, Wiederholung einzelner Fehler und Übergabe an den vorhandenen Audio-/TAF-Import. Offizielle yt-dlp-/Deno-Werkzeuge werden unabhängig verwaltet, geprüft und mit Rückkehrmöglichkeit aktualisiert. [Einrichtung und Grenzen](WEB-DOWNLOADS.md).

## TeddyCloud-Inhalte lokal sichern / zurückholen

Technische Untersuchung vom 17.11.2026: [Download-Möglichkeiten](DOWNLOAD-ANALYSE.md). In Version 0.4.0 umgesetzt: Bestandsansicht, lokale Sicherung über die Warteschlange, Audioexport und optionale Übernahme in die Sammlung. Eine separate Ansicht bearbeitet konkrete Tonie-/Tag-Zuordnungen mit Vorschau und Rückleseprüfung. Kein Schreiben physischer Chips.

- Bereits auf TeddyCloud gespeicherte Custom- und andere Tonie-Dateien inventarisieren, einschließlich serverexklusiver Inhalte.
- Tatsächliche Audiodateien über Audio-ID plus Hash eindeutig zuordnen; reine Metadateneinträge als solche kennzeichnen.
- Original-TAF lokal sichern, optional als Audio mit Metadaten und einzelnen Kapiteln exportieren.
- Bestehende Warteschlange für Pause, Abbruch und Fortschritt wiederverwenden; keine RFID-Zuordnung verändern.
