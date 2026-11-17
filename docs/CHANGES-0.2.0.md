# TeddyStudio 0.2.0

## Sammlung

- Die Grenze zwischen Ordnerbaum und Coverkarten sowie zwischen Sammlung und Details lässt sich ziehen. Bereiche werden nicht mehr durch feste Maximalgrößen begrenzt.
- Der Ordnerbaum enthält echte aufklappbare Eltern- und Unterordner. Die Auswahl eines Elternordners zeigt auch dessen Unterordner.
- Kompaktere Schaltflächen und Registerkarten schaffen mehr Platz. Metadatenaktionen erscheinen nur im zugehörigen Register.
- Spuren lassen sich als ganze Zeilen ziehen oder mit den Pfeiltasten-Schaltflächen verschieben. „Reihenfolge bestätigen“ speichert die Reihenfolge.
- Schaltflächen und wichtige Bedienelemente besitzen Hover-Hilfen.

## Einlesen und TeddyCloud

- Bis zu vier Audiodateien werden gleichzeitig untersucht. Erfolgreiche Ergebnisse werden anhand von Pfad, Größe, Änderungszeit und ffprobe-Konfiguration wiederverwendet. Fehler werden erneut geprüft.
- Das Speichern einer eingelesenen Sammlung erfolgt in einer gemeinsamen Datenbanktransaktion.
- Nach dem Start mit einer gespeicherten Sammlung und nach einem Scan wird die Custom-Tonie-Datenbank im Hintergrund gelesen. „TeddyCloud abgleichen“ aktualisiert sie manuell.
- Karten markieren nicht geprüfte Titel, fehlende passende Modellkennungen, vorhandene Modelle, abweichende Metadaten und unvollständige Audioreferenzen. Der Abgleich verwendet die gespeicherte Modellkennung oder das aktuell konfigurierte Benennungsschema; anders benannte Altimporte können deshalb als nicht passend erscheinen.
- „Modell online“ bestätigt die Modellkennung und vorhandene Audio-ID-/Hash-Angaben. Es bestätigt weder die Existenz der verknüpften Audiodatei noch deren vollständigen Inhalt. Der Import prüft Audio, TAF und Uploads weiterhin unabhängig vollständig.
- Verbindungs- oder Zertifikatsfehler erscheinen als „nicht geprüft“, niemals als vermeintlich fehlender Online-Titel. Es erfolgen keine Serveränderungen oder RFID-Zuordnungen.
- Selbstsignierte Zertifikate benötigen weiterhin die passende CA-Datei oder die bewusst gewählte TLS-Einstellung unter Einstellungen.

## Stapelbearbeitung und Audio

Die frühere Metadatenansicht heißt jetzt „Stapelbearbeitung“. Sie ändert gemeinsame Felder mehrerer in der Sammlung ausgewählter Hörspiele mit Vorschau. Die Detailansicht in der Sammlung bearbeitet ein einzelnes Hörspiel einschließlich Spuren. Das Schreiben von Tags in Originaldateien bleibt eine gesonderte Aktion mit Sicherung.

Die obere Audioliste ist die Eingabe-Playlist in Wiedergabereihenfolge. Dateien und Ordner einschließlich Unterordnern lassen sich aus dem Explorer dort ablegen. Unterstützte Audiodateien werden hinzugefügt, bereits enthaltene Pfade übersprungen. Innerhalb der Liste lassen sich Einträge durch Ziehen umsortieren. Die untere Tabelle ist die Ausgabevorschau der geplanten Abschnitte. Sie erzeugt selbst keine Dateien; dafür einen Auftrag erstellen und ausführen.

## Prüfung

51 automatisierte Tests unter Windows prüfen unter anderem Cache-Wiederverwendung und Invalidierung, Ordnerhierarchie, Bereichsaufteilung, Spurenreihenfolge, Playlist-Reihenfolge und differenzierte Online-Markierungen. Echte Bestandsdaten werden nicht im Repository gespeichert.
