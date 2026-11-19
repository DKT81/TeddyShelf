# Untersuchung: Inhalte von TeddyCloud lokal sichern

Stand: 17.11.2026. Ergebnis: technisch möglich. Eine eigene Download-Verwaltung in TeddyShelf ist in diesem Änderungsschritt noch nicht implementiert.

## Verfügbare Inhalte

Custom-Tonie-Modelle können über `/api/toniesCustomJson` gelesen werden. Die Audiodatei wird nicht durch den Modellnamen allein bestimmt, sondern durch Audio-ID und Hash einer vorhandenen TAF-Datei. Deshalb ist ein Abgleich mit den tatsächlichen Bibliotheks- und Inhaltsdateien erforderlich. So lassen sich auch auf dem PC nicht mehr vorhandene Quellen als auf TeddyCloud vorhandene Inhalte wiederfinden.

Andere Tonies sind ebenfalls lokal sicherbar, wenn ihre Audiodatei bereits in TeddyClouds Bibliothek oder Inhaltsablage vorliegt. Die globale Tonie-Metadatenliste beweist keine verfügbare Audiodatei. Ein Titel mit bloßen Metadaten muss als „Audio nicht vorhanden“ dargestellt werden.

## Lesende Schnittstellen

- `/api/fileIndexV2?special=library&path=…`: Bibliotheksordner und Dateien mit TAF-Headerinformationen.
- `/api/fileIndexV2?path=…`: Inhaltsablage; konfigurierte Overlays müssen bei einer späteren vollständigen Inventarisierung berücksichtigt werden.
- `/content/<relativer-pfad>?special=library`: vorhandene Bibliotheksdatei als TAF inklusive Header lesen. Ohne `special=library` adressiert derselbe Handler die Inhaltsablage.
- `ogg=true` am lokalen Content-Handler überspringt den 4096-Byte-TAF-Header und liefert den Ogg/Opus-Inhalt. Eine erneute verlustbehaftete Konvertierung ist dafür nicht erforderlich.
- Neuere Versionen besitzen `tracks` für serverseitigen Kapitelexport. Verfügbarkeit und genaue Parameter müssen pro Serverversion geprüft werden; alternativ kann TeddyShelf nach Download lokal dekodieren und exportieren.

**Nicht als allgemeinen Sicherungsabruf verwenden:** `/content/download/…` kann anhand einer Tag-Konfiguration einen Abruf aus der offiziellen Toniecloud auslösen. Diese Untersuchung löste einen solchen Abruf nicht aus. Die geplante Sicherung betrifft bereits auf TeddyCloud vorhandene Dateien, ohne Konto-/Authentifizierungsdaten zu ändern oder Zugriffsbeschränkungen zu umgehen.

## Zuordnung und lokale Ausgabe

1. Tatsächliche TAF-Dateien rekursiv inventarisieren, Verzeichnis-Elterneinträge überspringen und Schleifen/Duplikate vermeiden.
2. Custom- und offizielle Metadaten über Audio-ID plus Hash zuordnen; mehrdeutige oder fehlende Zuordnungen sichtbar lassen. Ein und dieselbe Audiodatei nicht mehrfach herunterladen, wenn mehrere Modelle darauf verweisen.
3. Original-TAF unter sicheren ASCII-Dateinamen und Serienordnern sichern. Covers und Metadaten in einem Manifest ablegen; keine RFID-Zuordnung verändern.
4. Download zuerst in eine `.partial`-Datei schreiben. Nach vollständigem Empfang Größe, TAF-Header und enthaltene Hash-Angaben prüfen, anschließend atomar übernehmen. Eine zusätzlich erhobene vollständige SHA256 dokumentieren.
5. Optional Ogg/Opus, MP3, FLAC oder WAV ausgeben; Kapitel und Metadaten übernehmen. Umwandlung nach MP3 ist erneut verlustbehaftet, Original-TAF und Ogg/Opus sollten deshalb als erhaltende Optionen sichtbar bleiben.
6. Vorhandene Warteschlange für Fortschritt, parallele lokale Verarbeitung, Pause, Abbruch und Wiederaufnahme verwenden. Servereinträge und Audiodateien unverändert lassen.

## Grenzen der Untersuchung

Die offiziellen Handler wurden gelesen und vorhandene Leseendpunkte am Testserver untersucht. Es wurden keine neuen Audios aus der offiziellen Toniecloud angefordert. Die Test-Inhaltsablage enthielt keine nicht benutzerdefinierte Audiodatei; deren vollständige Sicherung wurde dort deshalb nicht praktisch erprobt. Die gemeinsame Dateiausgabe-Implementierung unterstützt vorhandene TAF-Dateien unabhängig von ihrer Metadatenkategorie.

An einer vorhandenen Testdatei wurden jeweils nur die ersten 4096 Bytes abgerufen. Bibliotheksabruf und lokaler Content-Handler lieferten denselben TAF-Dateianfang. Mit `ogg=true` begann die Antwort stattdessen mit `OggS`. Alle drei Leseabrufe waren erfolgreich. Es wurde keine vollständige Audiodatei lokal gespeichert und kein Serverbestand verändert.

## Quellen

- [Offizielle HTTP-Routen](https://github.com/toniebox-reverse-engineering/teddycloud/blob/master/src/server.c)
- [Dateiindex und lokale Content-Ausgabe](https://github.com/toniebox-reverse-engineering/teddycloud/blob/master/src/handler_api.c), Funktionen `handleApiFileIndexV2`, `handleApiContent` und `handleApiContentDownload`
- [Modell- und Audiozuordnung](https://github.com/toniebox-reverse-engineering/teddycloud/blob/master/src/toniesJson.c)
