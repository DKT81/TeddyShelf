# Architektur und Datenhaltung

`domain.py`: serialisierbare Hörspiele/Spuren, getrennte Folge-/Trackwerte, sichere Namensprofile und Quell-Prüfsummen.

`storage.py`: atomare Einstellungen und SQLite-Transaktionen; WAL, Auftragszustände, Ereignisse und getrennte Quell-/Serverassoziationen. Keine Zugangsdaten im Repository. Schema der Assoziationen berücksichtigt Servereinträge ohne lokale Quelle für eine spätere Erweiterung; der Import schreibt nur seine eigenen Zuordnungen.

`library.py`: rekursives Einlesen, Tags, M3U-Reihenfolge und explizites Tag-Schreiben mit Sicherung. Der Ordner ist die Werkeinheit; bei Quellen, die mehrere Werke in einem Ordner enthalten, Audioauswahl verwenden oder die Sammlung zuvor passend ordnen. Automatische Erkennung bleibt editierbar.

`media.py`: FFmpeg/FFprobe als separate Prozesse ohne Shell, begrenzte Laufzeiten, garantiertes Einsammeln abgebrochener Prozesse, PCM-Normalisierung, genaue Schnittgrenzen, vollständige Dekodierung und Wellenform.

`taf.py`: eigener Encodercontainer, Protobuf-Wireformat, Ogg-CRC, blockgenaue Repagination mit unveränderten komprimierten Opus-Frames und Prüfung. Keine importierte TonieToolbox.

`client.py`: TeddyCloud-Adapter, UTF-8-JSON, begrenzte GET-Wiederholungen, keine blinden POST-Wiederholungen, vollständige SHA256-Rückprüfung und Modellvergleich. Keine RFID-Endpunkte. Der Import arbeitet seriell; fremde Modellfelder bleiben erhalten und nicht ausgewählte Modelle unverändert. Gleichzeitige Änderungen durch andere Programme am selben Modell sollten vermieden werden; die API bietet keine transaktionale Versionssperre.

`queue.py`: begrenzter Prozess-Scheduler, globale und einzelne Pause-/Abbruch-/Löschaktionen, unveränderliche Auftragsdaten und getrennte Arbeitsverzeichnisse. JSONL-Ereignisse und atomare Ergebnisdateien liefern Zustände an den Elternprozess; nur dieser schreibt das Auftragsjournal. `processes.py` bindet Windows-Konverter mittels Job Object an ihren Arbeitsprozess. Pause beendet aktive Arbeit und setzt später anhand validierter Arbeitsstände fort.

`jobs.py`: Vorbereitung, Quellfingerprint, Konvertierung und unabhängige Verifikation; Prozesssperren serialisieren Transaktionen je TeddyCloud-Ziel. Speicherbedarf: dekodiertes Stereo-PCM benötigt rund 691 MB pro Stunde und Auftrag. Prüffähige Arbeitsdateien bleiben erhalten, native temporäre Dateien werden nach Prozessende bereinigt. Bekannte lokale Dateikollisionen werden bereits bei der Disposition verhindert.

`ui_*.py`: Qt-Ansichten ohne eigene Konvertierungslogik. Lange Aufgaben laufen außerhalb des GUI-Threads, Ergebnisübergabe über Qt-Signale. `app.py` setzt die Komponenten zusammen. Keine sichtbaren inaktiven Platzhalter für spätere Serververwaltung.

## Einstellungen

Vorgaben wirken auf neue Aufträge. Ein Auftrag speichert eine JSON-Kopie des ausgewählten Profils und der Metadaten. Quellpfade referenzieren Originaldateien; nach Beginn schützt ein vollständiger Quellhash gegen Änderungen während der Wiederaufnahme. Änderungen an Quellen zwischen Erstellen und erstmaligem Start sind durch die erneute Medienprüfung sichtbar, der Erststart ist die Fingerprint-Grenze.

## Tests

Die Tests erzeugen eigene kurze Audiodateien, prüfen mehrere Ausgabeformate und Bitraten, beschädigte Header/CRC, Kapitel, Dauer, Wiederaufnahme, getrennte Tracknummern, unbekannte Folgen, fehlende Cover, GUI-Ansichten und einen echten lokalen HTTP-Testserver. Produktive TeddyCloud-Inhalte werden dabei nicht verändert. Physische Wiedergabe und das Verhalten anderer Serverversionen bleiben separate Akzeptanzprüfungen.
