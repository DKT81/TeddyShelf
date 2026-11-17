# TeddyStudio 0.2.1

- Gemeinsame Aktionsleisten halten Buttons links zusammen. Bei kleiner Breite brechen reine Buttonleisten um; Buttons wachsen nicht mehr in der Höhe. Das betrifft Sammlung, Cover, Audio und weitere Ansichten.
- Listenzeilen sind kompakter. Coverkarten behalten ihre eigene Gestaltung. Die Cover-Titelliste und das Vorschaubild sind durch eine verschiebbare Bereichsgrenze getrennt.
- Audio enthält drei höhenverstellbare Bereiche: Eingabe-Playlist, Wellenform mit Wiedergabe und Schnittoptionen mit Ausgabevorschau.
- Auf jedem Cover liegt ein farbiges Cloud-Etikett: CLOUD (passendes Modell mit Audioreferenzen), LOKAL (keine passende Modellkennung), PRÜFEN (abweichende Metadaten oder unvollständige/mehrdeutige Modelldaten) oder CLOUD ? (nicht geprüft, z. B. Verbindungsfehler). Es gelten weiterhin die Grenzen des schnellen Modellabgleichs: keine Aussage über vollständig geprüften Audioinhalt.
- Der Schnittplan zeigt Eingabeanzahl, Gesamtdauer, Abschnittslänge und Ausgabeanzahl. Eine zusätzliche Spalte ordnet jedem Abschnitt die Nummern der Eingabedateien zu; der Tooltip nennt Dateinamen und Zeitbereiche innerhalb der Quelldatei.
- Eingabedateien werden zu einer durchgehenden Wiedergabereihenfolge verbunden und anschließend nach Länge geteilt. Daher müssen Eingabe- und Ausgabeanzahl nicht übereinstimmen. Beispiel: 26 Dateien à 5:10 ergeben 2:14:20 Audio und bei zehn Minuten Abschnittslänge 14 Ausgabedateien. Keine Quelle entfällt dadurch.
- Im Modus TAF mit Kapiteln nennt die Vorschau korrekt eine TAF-Datei mit mehreren Kapiteln und zeigt für alle Kapitel denselben tatsächlichen Dateinamen.

Prüfung: 54 automatisierte Tests, darunter breite Fenster, kompakte Zeilen, umgebrochene Aktionsleisten und das konkrete Beispiel mit 26 Eingaben / 14 Ausgaben. Die Ansichten wurden zusätzlich mit synthetischen Daten bei normaler und großer Fensterbreite als Bilder geprüft. Echte Bestandsdaten werden nicht veröffentlicht.
