# Web-Downloads

Ab TeddyShelf 0.7.0 ergänzt **Web-Download** den vorhandenen Download aus TeddyCloud. Die beiden Funktionen haben getrennte Auftragstypen und verändern einander nicht.

## Einrichtung

In der eigenständigen Release-EXE ab 0.8.0 sind die Ausgangsversionen bereits enthalten. Für Quellinstallationen und ältere Pakete gilt:

1. In **Web-Download** den Kanal **Stable** beibehalten und **yt-dlp-Update prüfen …** wählen. TeddyShelf zeigt das offizielle Release und bittet vor der Installation um Bestätigung.
2. **Deno einrichten / aktualisieren …** wählen. Deno ist die JavaScript-Laufzeit für YouTube; die benötigten EJS-Skripte sind bereits Bestandteil der offiziellen `yt-dlp.exe`.
3. Die aktiven Versionen werden in derselben Ansicht angezeigt. Python, YTDLnis und TonieToolbox sind nicht erforderlich. Das Windows-Paket enthält FFmpeg/FFprobe; die beiden neuen Werkzeuge werden bei der Einrichtung separat heruntergeladen.

## Vorschau und Auswahl

- Eine Einzelvideo- oder Playlist-URL eingeben und **URL-Vorschau laden** wählen. Die Vorschau liest Metadaten, lädt aber noch keine Mediendateien herunter. Die Vorschau lässt sich abbrechen.
- **Audio (MP3)** oder **Video (MP4)** sowie den Zielordner wählen. Audio wird vollständig übernommen; Video wird bei Bedarf zusammengeführt beziehungsweise zu MP4 konvertiert.
- Alle verfügbaren Playlist-Einträge sind zunächst angehakt. Einzelne Einträge lassen sich abwählen. Nicht verfügbare Einträge bleiben sichtbar, werden aber nicht eingeplant. Größenlimit: 5000 Vorschau-Einträge; bei größeren Sammlungen kleinere Playlists verwenden.
- **Auswahl herunterladen** legt je ausgewähltem Eintrag einen eigenen **Web-Download** in der vorhandenen Warteschlange an. Der gespeicherte Einstellungsstand gehört zum Auftrag. Beim Ausführungsstart werden die aktiven Werkzeugversionen einmalig festgehalten.

## Aufträge und Fehler

Die normale Auftragsansicht bietet Fortschritt, Protokolle, Abbruch, Pause, Fortsetzung und Löschen. In der Web-Ansicht zeigt jede Zeile den Status ihres Eintrags. Über **Bisherige Web-Aufträge** lassen sich auch nach einem Neustart frühere Gruppen auswählen. Angehakte fehlgeschlagene, unterbrochene, pausierte oder abgebrochene Einträge lassen sich mit **Fehler der Auswahl wiederholen** gezielt erneut starten; fertige Einträge bleiben unangetastet.

Die eingestellte Parallelität gilt auch für Web-Downloads. Die ursprünglichen Playlist-Positionen stehen in den Dateinamen, beispielsweise `0001-Titel-URLKennung.mp3` und `0003-Titel-URLKennung.mp3`, wenn Nummer 2 abgewählt wurde. Die URL-Kennung vermeidet Namenskollisionen. Parallel fertig werdende Aufträge verändern die Reihenfolge nicht.

Fertige Dateien werden mit FFprobe und einem vollständigen FFmpeg-Dekodierlauf geprüft. Eine Begleitdatei `*.teddyshelf-web.json` enthält Herkunft, Format, Position, Werkzeugversion und SHA256. Wiederholungen verwenden nur Ergebnisse mit passender Herkunft und Prüfsumme. Unbekannte oder veränderte Dateien werden nicht überschrieben. Ein zwischen Veröffentlichung und Abschluss unterbrochener Auftrag kann sein fertiges Ergebnis erneut prüfen.

Unfertige Downloads liegen unter `.teddyshelf-web` im gewählten Zielordner und bleiben für eine Fortsetzung erhalten. Nach Erfolg werden die temporären Dateien des Eintrags entfernt. Das Löschen eines Auftrags löscht weiterhin nur den Verlauf; bereits gespeicherte Medien und fortsetzbare Teil-Downloads werden nicht automatisch entfernt. Vollständig verworfene Teil-Downloads können nach Abbruch aller zugehörigen Aufträge im Zielordner manuell entfernt werden. Es werden keine dauerhaften PCM-Zwischendateien erzeugt.

## Audio an den bestehenden Import übergeben

Fertige Audio-Einträge anhaken und **Fertiges Audio an Audioansicht übergeben** wählen. TeddyShelf prüft die Dateien erneut und ersetzt die Eingabeplaylist durch diese Auswahl in ursprünglicher Reihenfolge. Bereits eingegebene andere Tracks werden dabei ersetzt. Standardmäßig wird **TAF als Playlist (Originaldateien vollständig)** gewählt: jede Datei wird ein Kapitel, ohne Zeitschnitt oder Kürzung. Titel, Reihenfolge, Schnittmodus und Ziel können anschließend in der Audioansicht angepasst werden. TAF-Erstellung und TeddyCloud-Übertragung laufen danach unverändert über die bisherigen Funktionen. Ein Web-Download allein lädt nichts in TeddyCloud hoch. Video-Einträge werden nicht als Audio übergeben.

## Unabhängige Updates und Rückkehr

**Stable** verwendet ausschließlich `yt-dlp/yt-dlp`, **Nightly** ausschließlich `yt-dlp/yt-dlp-nightly-builds`. Deno stammt aus `denoland/deno` und verwendet Stable. Nightly ist eine bewusste Alternative für neuere Engine-Fixes, keine Übernahme der Android-Oberfläche von YTDLnis.

TeddyShelf lädt Kandidaten in einen separaten Bereich, prüft die über HTTPS vom offiziellen Release gelieferten SHA256-Prüfsummen und, soweit vorhanden, den GitHub-Asset-Digest. Danach werden Version und benötigte Kommandooptionen getestet. Deno muss mindestens Version 2.3.0 sein. Erst nach erfolgreicher Prüfung wird die aktive Version atomar umgeschaltet. Dies ist eine Prüfung gegen offizielle Release-Prüfsummen, keine zusätzliche GPG-Signaturprüfung.

Die Werkzeugdateien liegen unveränderlich nach Inhaltshash unter `<Datenordner>/web-tools/versions`; `active.json` enthält aktuelle und vorherige Versionen. Die Schaltflächen **Vorheriges yt-dlp aktivieren** und **Vorheriges Deno aktivieren** erlauben die Rückkehr. Aktuelle, vorherige und von Aufträgen referenzierte Dateien werden nicht automatisch gelöscht. Laufende Downloads behalten die beim Ausführungsstart festgehaltenen Versionen. Noch wartende oder gezielt wiederholte Aufträge übernehmen bei ihrem nächsten Start die aktuell aktivierten Versionen. Bereits vollständig geprüfte Ergebnisse werden weiterhin ohne erneuten Download verwendet.

## Grenzen und Diagnose

Webseiten ändern sich. yt-dlp-Engine-Fixes kommen über die unabhängigen Werkzeugupdates. Zugangsbeschränkte, entfernte, regionale oder anderweitig nicht verfügbare Quellen können je Eintrag fehlschlagen; diese Meldungen stehen im Auftragsprotokoll. TeddyShelf übernimmt keine Browser-Cookies, Konten, globale yt-dlp-Konfigurationen oder Plugins. URLs mit eingebetteten Zugangsdaten werden abgelehnt. Andere technische Meldungen von yt-dlp und FFmpeg erscheinen in deren Originalsprache. Vorschau und Download benötigen Netzwerkzugriff; lokale Test-URLs über HTTP werden ebenfalls unterstützt.

Offizielle Referenzen: [yt-dlp](https://github.com/yt-dlp/yt-dlp), [EJS / JavaScript-Laufzeiten](https://github.com/yt-dlp/yt-dlp/wiki/EJS), [Nightly-Releases](https://github.com/yt-dlp/yt-dlp-nightly-builds/releases), [Deno-Releases](https://github.com/denoland/deno/releases).

## Eigene Hörspielordner und Sammlung

Ab 0.8.0 erhält jeder neue Download einen eigenen Hörspielordner. Alte flach gespeicherte Dateien können über **Fertiges Audio in Sammlung übernehmen** geprüft kopiert, mit Metadaten versehen und als einzelne Titel übernommen werden; ursprüngliche Audiodateien bleiben erhalten. Details, Ordneraktualisierung und Entfernen ausschließlich aus der Sammlung: [Sammlungsordner](COLLECTION-FOLDERS.md).
