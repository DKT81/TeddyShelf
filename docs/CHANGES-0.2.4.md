# TeddyStudio 0.2.4

## Modell wird von neuerer TeddyCloud-Validierung abgewiesen

Die offizielle Modell-API verlangt das Feld `no` als Zeichenkette. TeddyStudio sendete bisher eine JSON-Zahl, beispielsweise `8`; neuere Server lehnen diesen Wert mit HTTP 400 ab. Der Client sendet nun `"8"`, beziehungsweise `"0"` für Titel ohne Folgennummer. Die Audio-ID und die Folgennummer bleiben getrennte Werte.

Quelle: [offizielle API-Validierung](https://github.com/toniebox-reverse-engineering/teddycloud/blob/master/src/handler_api.c), Funktion `validateToniesCustomJson`.

Der lesende Vergleich bleibt mit älteren Servern kompatibel, die die Nummer als Zahl zurückgeben. Gleichwertige Zahlen-/Textdarstellungen erzwingen keine erneute Modelländerung.

## Verständliche HTTP-Fehler

HTTP-Fehler enthalten nun zusätzlich die vom Server zurückgegebene Fehlermeldung, auf 1000 Zeichen begrenzt. So wird beispielsweise neben HTTP 400 auch `'no' must be string` angezeigt. Der HTTP-Status und das zugehörige Antwortobjekt bleiben für die Fehlerbehandlung erhalten; fehlgeschlagene POSTs werden nicht blind wiederholt.

## Prüfung

Der lokale HTTP-Testserver prüft die Folgennummer nun wie die strengere TeddyCloud-API. Der Test reproduziert die Ablehnung einer Zahl, prüft die konkrete Diagnose und verlangt bei erfolgreichen Importen den Textwert. Ein weiterer Fall deckt numerische Rückgaben älterer Server ab. Bestehende Upload-, Wiederaufnahme- und Verifikationsprüfungen bleiben aktiv.

Nach dem Update genügt das individuelle Wiederholen des fehlgeschlagenen Auftrags. Bereits korrekt vorhandene Audio- und Coverdateien werden weiterhin anhand ihrer vollständigen Prüfsummen erkannt.
