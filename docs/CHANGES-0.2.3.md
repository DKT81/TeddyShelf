# TeddyShelf 0.2.3

## Reihenfolge nur bei Änderungen oder Unklarheiten bestätigen

Eine eindeutig erkannte, unveränderte Reihenfolge ist direkt importierbar. Der Bestätigungsbutton ist dann deaktiviert. Manuelles Verschieben von Spuren kennzeichnet nur das betroffene Hörspiel als geändert. Die Importvorschau und der Worker berücksichtigen diese Kennzeichnung.

Bereits bestätigte Reihenfolgen bleiben beim erneuten Einlesen mit denselben Quelldateien erhalten, auch wenn die Metadaten danach bearbeitet wurden. Eine geänderte Quellenmenge erfordert eine neue Prüfung. Echte Audiofehler werden nicht durch eine Reihenfolgebestätigung gelöscht.

Alte falsche Playlist-Warnungen werden beim Import und individuellen Wiederholen nochmals anhand der tatsächlichen Dateien und der verbesserten Playlist-Erkennung geprüft. Vollständig eindeutige Zuordnungen werden automatisch aufgelöst. Fehlende oder doppelte Einträge werden weiterhin nicht geraten.

## Upload-Wiederaufnahme

- HTTP-Verbindungen werden nach jeder Antwort geschlossen, um Probleme mit vom Server beendeten Keep-Alive-Verbindungen zu vermeiden.
- Abgebrochene Datei-Downloads während der SHA256-Prüfung werden vollständig neu begonnen.
- Bei unklarem Upload-Ergebnis wird zuerst die komplette Serverdatei geprüft. Bei passender SHA256 gilt die Datei als übertragen, auch wenn die POST-Antwort verloren ging.
- Nur wenn der Bestand erfolgreich gelesen wurde und die Datei nachweislich fehlt oder abweicht, wird der Upload erneut versucht; höchstens drei Versuche je Datei. Bei unbekanntem Serverzustand erfolgt kein weiterer Upload.
- Vorhandene korrekte Dateien werden mit einem eigenen Protokolleintrag übersprungen. TAF-Header und Custom-Tonie-Modell werden weiterhin unabhängig geprüft.
- Ein reiner Statuswechsel im Auftragsjournal und neue optionale Datenfelder erzwingen keinen neuen Auftrag. Die individuelle Wiederholung unveränderter Aufträge verwendet denselben Arbeitsordner und geprüfte Konvertierungsergebnisse.

Die genauen Ursachen serverseitiger Verbindungsabbrüche sind deploymentspezifisch. Diese Änderungen härten die Kommunikation und Wiederaufnahme; sie ersetzen keine Fehleranalyse eines dauerhaft nicht erreichbaren Servers.
