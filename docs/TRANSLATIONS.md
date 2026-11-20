# Übersetzungen / Translations

## Sprache auswählen

Unter **Einstellungen → Allgemein → Sprache** stehen **Deutsch** und **English** zur Verfügung. Die Oberfläche wechselt sofort; die Auswahl bleibt beim nächsten Start erhalten. Aufträge werden dadurch nicht angehalten oder neu erstellt.

Nur Programmtexte werden übersetzt. Hörspieltitel, Metadaten, Dateinamen, Vorlagen und eigene Profilnamen bleiben unverändert. Intern gespeicherte Optionswerte behalten ihre bisherigen Kennungen; bestehende Sammlungen und Auftragsstände bleiben kompatibel. Technische Fehlerdetails von Windows, FFmpeg oder Servern können in deren ursprünglicher Sprache erscheinen.

## Eine Sprache ergänzen

1. `src/teddystudio/locales/en.json` kopieren, beispielsweise als `fr.json`.
2. `code` auf `fr` und `name` auf den muttersprachlichen Namen `Français` setzen.
3. Nur die rechten Werte in `messages` übersetzen. Die deutschen Schlüssel sind stabile Textkennungen und bleiben unverändert. Platzhalter wie `{p0}`, `{p1:03}` und `{p0!s}` müssen vollständig erhalten bleiben; ihre Reihenfolge darf angepasst werden.
4. Die Datei als UTF-8 speichern. Zum Testen im Ordner `translations` innerhalb des verwendeten TeddyShelf-Datenordners ablegen und die Anwendung neu starten. Im portablen Paket ist alternativ `translations` neben `TeddyShelf.exe` möglich. Die neue Sprache erscheint automatisch in der Auswahl.

Der tatsächliche Datenordner ist standardmäßig `%LOCALAPPDATA%/TeddyShelf`, bei weiterverwendeten alten Installationen `%LOCALAPPDATA%/TeddyStudio`, oder der über `TEDDYSHELF_DATA` festgelegte Ordner. Eine gleichnamige benutzerdefinierte Sprache überschreibt die mitgelieferte Version. Reihenfolge: mitgelieferte Dateien, portabler Ordner, Datenordner. Es werden keine Texte an Übersetzungsdienste übertragen.

Fehlende Einträge fallen auf den deutschen Quelltext zurück. Ungültige optionale Dateien, falsche Platzhalter und Dateien über 2 MB werden mit Warnung im Programmprotokoll ignoriert. `de` und `en` werden mit der Anwendung ausgeliefert und müssen im Quellpaket erhalten bleiben.

Ein minimales zusätzliches Wörterbuch ist beispielsweise:

```json
{
  "code": "fr",
  "name": "Français",
  "messages": {
    "Einstellungen": "Paramètres",
    "Profil: {p0}": "Profil : {p0}"
  }
}
```

## Für Mitwirkende

Neue Oberflächentexte mit `tr_text("Quelltext")`, dynamische Texte mit `_f("Text {p0}", p0=wert)` markieren. Für Qt-Steuerelemente die Adapter aus `i18n_widgets.py` verwenden. Eigene Nutzerdaten nicht markieren. Vorhandene zusammengesetzte Übersetzungen mit `tr_text("\n").join(...)` verbinden; gewöhnliche f-Strings verwerfen die Übersetzungsinformation ihrer Bestandteile.

```powershell
python scripts/extract_translations.py
# Neue Werte in locales/en.json und weiteren Katalogen übersetzen.
python scripts/extract_translations.py --check
python -m pytest tests/test_i18n.py -q
```

Der Extraktor übernimmt neue Kennungen und bewahrt vorhandene Übersetzungen. Er erfasst auch bekannte Diagnosemeldungen aus den Hintergrundaufträgen. Die GUI übersetzt deren Darstellung, während das ursprüngliche Auftragsprotokoll unverändert bleibt. Qt-Standarddialoge verwenden mitgelieferte Qt-Übersetzungen; für zusätzliche Sprachen kann ein optionales `qt`-Wörterbuch Qt-Quelltexte ersetzen. Native Dateiauswahldialoge richten sich nach der Sprache des Betriebssystems.

## English

Choose **Settings → General → Language → English**. Changes apply immediately and are saved. Audiobook metadata, filenames, naming templates and custom profile names are preserved.

To add a language, copy `en.json`, change `code` and the native `name`, and translate the values under `messages`. Keep all German keys and named placeholders unchanged. Save the UTF-8 JSON file in the data directory's `translations` folder, or beside the portable executable in `translations`, then restart. Missing entries fall back to German; invalid optional files are logged and ignored. No online translation service is used. Run the extraction/check commands above when contributing translations to the repository.
