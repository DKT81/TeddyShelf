# TeddyShelf 0.3.0

## Ganze Warteschlange steuern

- **Starten / Fortsetzen:** wartende und pausierte Aufträge verarbeiten. Die Ansicht wechselt bewusst auf alle Aufträge einschließlich fertiger Einträge.
- **Pausieren:** keine neuen Aufträge starten und laufende Arbeitsprozesse samt FFmpeg beenden. Die Aufträge bleiben als „Pausiert“ mit ihren Arbeitsordnern erhalten. Fortsetzen prüft und verwendet fertige Zwischenergebnisse; ein unterbrochener einzelner Konvertierungsschritt kann neu beginnen. Eine laufende HTTP-Verbindung wird nicht eingefroren oder bytegenau fortgesetzt.
- **Abbrechen:** offene Aufträge aus der Verarbeitung nehmen und laufende Prozesse beenden. Einträge bleiben als „Abgebrochen“ sichtbar und können individuell wiederholt werden.
- **Leeren:** nach Bestätigung alle Aufträge einschließlich aktiver und fertiger Einträge aus der Liste und dem Journal entfernen. Aktive Prozesse werden zuvor beendet.

Vorhandene Quell-, Export- und TeddyCloud-Dateien werden weder durch Pausieren noch durch Abbrechen oder Leeren rückgängig gemacht. Ein bereits serverseitig eingegangener Schreibzugriff kann nach dem Beenden des Clients noch abgeschlossen werden. Eine Wiederaufnahme prüft den tatsächlichen Bestand.

## Einzelaufträge und Auswahl

„Ausgewählte abbrechen“ und „Ausgewählte löschen“ sind ohne pauschale Statussperre verfügbar. Aktive Aufträge werden vor dem Entfernen beendet; wartende starten danach nicht mehr. Bereits fertige Aufträge haben keine laufende Arbeit mehr und bleiben beim Abbrechen fertig, ihre Ergebnisse werden nicht zurückgenommen. Löschen entfernt auch solche Einträge.

Mehrfachauswahl bleibt bei Fortschrittsaktualisierungen erhalten. Fertige Aufträge bleiben standardmäßig als Zeilen sichtbar; sie heißen „Fertig · Cloud geprüft“ oder „Fertig · lokal“. Die Zusammenfassung unterscheidet Gesamtzahl, laufende, wartende, fertige und fehlgeschlagene Aufträge. Ausblenden erfolgt nur über eine ausdrücklich gewählte Filteroption.

## Parallele Verarbeitung

Unter **Einstellungen → Allgemein → Gleichzeitige Aufträge / Konvertierungen** sind 1 bis 16 Arbeitsprozesse einstellbar, Standard 1. Die Warteschlange verwendet die aktuelle Grenze auch für bereits wartende Aufträge. Eine niedrigere Grenze beendet keine laufenden Aufträge, sondern begrenzt neue Starts.

Jeder Auftrag läuft in einem eigenen Prozess und Arbeitsordner. Damit können auch CPU-lastige Python-Teile der TAF-Erstellung mehrere CPU-Kerne verwenden. Die Oberfläche zeigt die erkannten logischen CPU-Kerne und einen vorsichtigen Startwert. Mehr Parallelität erhöht Bedarf an CPU, RAM und Datenträgerleistung; sie ist keine Geschwindigkeitsgarantie. PCM-Arbeitsdateien benötigen ungefähr 691 MB pro Audiostunde und Auftrag.

Übertragungen und Modelländerungen werden je TeddyCloud-Zieladresse durch eine gemeinsame Prozesssperre serialisiert. Unterschiedliche Ziele können unabhängig arbeiten. Bekannte kollidierende lokale Ausgabedateien werden innerhalb der Warteschlange nicht gleichzeitig erzeugt. Änderungen durch andere Programme am selben Servermodell bleiben außerhalb dieser Sperre.

Windows Job Objects sorgen dafür, dass beim Beenden eines Auftrags auch native Konverter beendet werden. Temporäre Konverterdateien liegen im jeweiligen Arbeitsordner und werden nach Ende oder Abbruch bereinigt; prüfbare Arbeitsstände bleiben zur Wiederaufnahme erhalten.

## Sammlungsfilter

Neuer eigener Cloud-Statusfilter: **alle, Cloud, Lokal, Prüfen**. Ungeprüfte, nicht erreichbare sowie abweichende oder mehrdeutige Einträge gehören zu „Prüfen“. „Lokal“ bedeutet nur, dass beim erfolgreichen Abgleich keine passende Modellkennung gefunden wurde. Der schnelle Modellabgleich ersetzt keine vollständige Audiodateiverifikation.
