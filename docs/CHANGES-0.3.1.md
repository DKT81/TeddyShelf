# TeddyShelf 0.3.1

Enthält alle Änderungen aus [0.3.0](CHANGES-0.3.0.md): globale Warteschlangensteuerung, einstellbare parallele Prozesse, sichtbare fertige Aufträge und Cloud-Statusfilter.

## Cover direkt im Bild zuschneiden

Der Zuschnitt verwendet einen grafischen Rahmen statt Eingabefeldern für Pixelgrenzen:

- Im Bild einen neuen Rahmen aufziehen.
- Innerhalb des Rahmens ziehen, um ihn zu verschieben.
- Ecken und Kanten greifen, um die Größe zu ändern.
- Quadratischen Ausschnitt ein- oder ausschalten und bei Bedarf zurücksetzen.
- Live-Vorschau des Ausschnitts; Pfeiltasten für feine Positionsänderungen, mit Umschalt größere Schritte.
- Die Auswahl bleibt bei Größenänderungen des Fensters in den ursprünglichen Bildkoordinaten erhalten. Der tatsächliche Zuschnitt erfolgt auf der originalen, nach EXIF ausgerichteten Bildauflösung.

„Zuschnitt übernehmen“ erzeugt zunächst eine bearbeitete Coverdatei. Erst „Diesem Titel zuordnen und speichern“ speichert die Zuordnung. Abbrechen verändert das bisherige Cover und die Quelldatei nicht.

## Downloads untersucht

Die [technische Untersuchung](DOWNLOAD-ANALYSE.md) beschreibt das lokale Sichern vorhandener Custom- und anderer Tonie-Dateien. Original-TAF und Ogg/Opus können über lesende Schnittstellen abgerufen werden; Metadaten müssen anhand Audio-ID und Hash zu tatsächlichen Dateien zugeordnet werden. Eine Download-Verwaltung ist noch nicht implementiert.

## Für später vorgemerkt

YouTube als Audioquelle steht auf der [To-do-Liste](TODO.md), ebenso die zuvor vorgemerkte Creative-Tonie-Funktion. Beide wurden noch nicht implementiert.
