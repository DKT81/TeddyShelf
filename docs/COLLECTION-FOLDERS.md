# Sammlung und Downloadordner

## Ordner einlesen und aktualisieren

Ein normaler Hörspielordner entspricht einem Sammlungsobjekt mit seinen Audiospuren. Mehrere MP3-Dateien können deshalb zu einem einzigen Hörspiel gehören. JSON-Nachweise, Bilder, Videodateien und temporäre Downloads sind keine zusätzlichen Audiospuren. Die Sammlung zeigt nun neben der Zahl der Hörspiele auch die Zahl der Audiodateien an.

**Ordner aktualisieren** gleicht den gewählten Ordner einschließlich Unterordnern erneut ab. Ohne gewählten Ordner werden die eingelesenen Quellordner aktualisiert. Neue Titel kommen hinzu, nicht mehr vorhandene Sammlungsobjekte im erfolgreich eingelesenen Bereich fallen heraus. Andere Ordner bleiben unberührt. Bewusst bearbeitete Metadaten und bestätigte Reihenfolgen bleiben bei denselben Quellen erhalten. Ein nicht erreichbarer oder unlesbarer Bereich führt zu einem Fehler; er wird nicht als leerer Ordner gewertet und entfernt keine Sammlungseinträge.

## Nur aus der Sammlung entfernen

In **Nach Ordnern** den Ordner auswählen und **Ordner aus Sammlung entfernen …** wählen. Die Bestätigung nennt den Ordner und die Anzahl der betroffenen Hörspiele. In der Serienansicht kann der betreffende Ordner über die Ordnerauswahl bestimmt werden. Es werden nur Datenbankreferenzen entfernt, keine Quelldateien, Unterordner oder Downloadnachweise. Bestehende Dateien und Aufträge bleiben erhalten.

Ein entfernter Unterordner bleibt auch beim Aktualisieren seines übergeordneten Ordners ausgeschlossen. Mit **Ordner einlesen** kann er ausdrücklich wieder aufgenommen werden. Auch die Wiederaufnahme eines einzelnen Unterordners ist möglich, ohne entfernte Nachbarordner erneut einzulesen.

## Web-Downloads als eigene Hörspielordner

Neue Web-Downloads werden einzeln strukturiert: `Ziel/0001-Titel-Kennung/0001-Titel-Kennung.mp3`. Audio erhält Titel-, Album- und Track-Tags; vorhandene, geprüfte Downloadnachweise liefern zusätzlich den ursprünglichen lesbaren Titel. Serien und Folgen werden nicht aus Verzeichnisnamen wie `Music` erfunden.

Für bereits mit Version 0.7.0 flach gespeicherte Downloads gibt es zwei Wege:

- **Ordner aktualisieren** erkennt die alten `*.teddyshelf-web.json`-Nachweise und zeigt jedes Audio als eigenen Titel mit dem Originaltitel. Die Dateien werden dabei nicht verändert.
- In **Web-Download → Bisherige Web-Aufträge** die fertigen Audios auswählen und **Fertiges Audio in Sammlung übernehmen** wählen. TeddyShelf kopiert flach liegende Dateien geprüft in einzelne Hörspielordner, ergänzt sinnvolle Audiotags und übernimmt diese Ordner in die Sammlung. Die ursprünglichen Audiodateien bleiben unverändert erhalten. Vorhandene andere Dateien werden nicht überschrieben. Verifizierte ursprüngliche Dubletten werden beim Einlesen nicht doppelt angezeigt; fehlt die neue Kopie später, bleibt das ursprüngliche Audio wieder sichtbar.

**Fertiges Audio an Audioansicht übergeben** bleibt der andere Weg: Die ausgewählten Dateien werden in Playlist-Reihenfolge als Eingabespuren für einen gemeinsamen TAF-/Kapitelauftrag geladen. Dieser Schritt erstellt nicht automatisch mehrere Sammlungstitel und überträgt nichts ungefragt in TeddyCloud.

## Warteschlange

**Fertige ausblenden** bleibt beim Starten und Wiederholen von Aufträgen gesetzt und wird für den nächsten Programmstart gespeichert.

## Kapitel aus TeddyCloud

Beim Export von MP3/FLAC/WAV mit **nach Kapiteln aufteilen** werden passende Katalog-Tracknamen in Dateinamen und Audiotags übernommen, zum Beispiel `001-Titellied.mp3`. Die Nummer erhält die Reihenfolge und verhindert Kollisionen bei gleichnamigen Kapiteln. Nicht erlaubte Dateizeichen werden bereinigt und Umlaute für die Dateinamen transliteriert; die Audiotags behalten die Originalschreibweise. Passt die Titelliste nicht zur tatsächlichen TAF-Kapitelanzahl, bleiben neutrale Spurnamen erhalten, statt falsche Titel zuzuordnen.
