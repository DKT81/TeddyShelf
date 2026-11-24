# Windows Build & Release

## Fertige Anwendung herunterladen

Unter [GitHub Releases](https://github.com/DKT81/TeddyShelf/releases) die Datei `TeddyShelf-VERSION-Windows-x64.exe` herunterladen. Die EXE enthält Python, Qt, FFmpeg/FFprobe, Übersetzungen, Schrift, Lizenzhinweise sowie Stable-Ausgangsversionen von yt-dlp einschließlich EJS und Deno. Es ist keine separate Python- oder TonieToolbox-Installation erforderlich. Die Prüfsumme steht in der zugehörigen `*.exe.sha256`-Datei; unter PowerShell lässt sie sich mit `Get-FileHash DATEI.exe -Algorithm SHA256` vergleichen.

Die Anwendung entpackt ihre Laufzeit beim Start temporär. Die nativen Werkzeuge laufen als externe Prozesse. Einstellungen, Bibliothek und Auftragsverlauf bleiben unabhängig davon im Benutzerdatenordner `%LOCALAPPDATA%/TeddyShelf`, bei einer weiterverwendeten alten Installation gegebenenfalls `%LOCALAPPDATA%/TeddyStudio`. `TEDDYSHELF_DATA` hat Vorrang; `TEDDYSTUDIO_DATA` bleibt kompatibel. Die mitgelieferten Web-Werkzeuge werden beim ersten Start in den beschreibbaren Benutzerdatenordner kopiert. Spätere unabhängige Updates und die vorherige Version liegen ebenfalls dort und werden durch ein neues Programm-EXE nicht überschrieben.

## Manuell über GitHub bauen

1. Im Repository **Actions → Windows Build & Release → Run workflow** öffnen.
2. Den gewünschten Branch auswählen und den Workflow starten.
3. Nach erfolgreichem Abschluss das Artifact **TeddyShelf-Windows-x64-VERSION** herunterladen und entpacken. Darin liegen EXE, SHA-256-Datei, Lizenzarchiv und Build-Provenienz. Ein manueller Lauf veröffentlicht keinen GitHub Release.

## Version veröffentlichen

1. Die gewünschte Version in `pyproject.toml` und `src/teddystudio/__init__.py` identisch eintragen und die Änderungen übertragen.
2. Einen Versions-Tag erstellen und pushen, zum Beispiel `v0.8.0`. Für Beta-Versionen beispielsweise `0.9.0b1` als Programmversion und `v0.9.0-beta.1` als Tag verwenden; beide werden als dieselbe PEP-440-Version geprüft.
3. Der Tag startet automatisch den Windows-Build. Nur wenn Quelltests, Versionsprüfung und Selbsttest der fertigen EXE erfolgreich sind, läuft der Veröffentlichungsjob.
4. Dieser lädt die geprüften Artefakte, verifiziert die SHA-256-Datei erneut und legt einen Release mit automatisch erzeugten Release Notes an. Uploads erfolgen zunächst in einen Entwurf; erst anschließend wird der Release sichtbar. Beta-, Alpha-, RC- und Dev-Versionen sind Pre-Releases und werden nicht als neueste stabile Version markiert.

Bereits veröffentlichte Tags/Assets werden nicht still überschrieben. Bei einem fehlgeschlagenen Upload kann ein unveröffentlichter Entwurf zurückbleiben. Diesen vor einem erneuten Veröffentlichungsversuch prüfen und gegebenenfalls entfernen. Änderungen von Fork-Pull-Requests erhalten keine Schreibrechte und veröffentlichen nichts.

## Was der Workflow prüft

Der Build läuft auf einem GitHub-gehosteten Windows-2022-x64-Runner mit Python 3.13.9 und den in `requirements-build-windows.txt` festgehaltenen Build-Abhängigkeiten. Die Runtime-Werkzeuge stammen aus den offiziellen Releases, werden gegen die veröffentlichten SHA-256-Werte geprüft und mit Versions-/Quellnachweisen erfasst. Die passenden Lizenztexte werden gesammelt und in der EXE sowie als separates Archiv mitgeliefert. `build-provenance.json` dokumentiert die tatsächlich eingesetzten Revisionen und Prüfsummen.

Vor dem Packaging werden alle Tests und die Übersetzungskataloge geprüft. Danach läuft die tatsächliche Ein-Datei-EXE mit einem PATH ohne Python oder FFmpeg und mit isolierten Benutzerdaten. Der Selbsttest prüft TAF-Erstellung/-Dekodierung, parallele Arbeitsprozesse, PCM-Bereinigung, deutsche/englische Ansichten, echte Audio-/Video-Downloads von lokal erzeugten Testmedien sowie die geordnete Audio- und Sammlungsübergabe. Er benötigt keine privaten Konten, Hörspiele oder Server. Die Diagnoseartefakte enthalten ausschließlich Testdaten.

Der Build-Job hat nur `contents: read`. `contents: write` ist ausschließlich dem von einem erfolgreichen Tag-Build abhängigen Veröffentlichungsjob zugeteilt; GitHub erlaubt Berechtigungen auf Workflow-/Job-Ebene, nicht auf Ebene einzelner Shell-Schritte. Der Token wird dort nur dem Veröffentlichungsschritt als Umgebungsvariable übergeben. Der Workflow verändert keine Docker- oder LXC-Dateien.

## Lokal denselben Build ausführen

Auf einem Windows-x64-Rechner mit installiertem Python 3.13:

```powershell
./scripts/build-windows.ps1
```

Das Skript erstellt eine separate `.venv-build-windows`, lädt geprüfte Werkzeuge, führt Tests aus und legt die fertigen Dateien unter `dist/windows-release` ab. Für eine bereits eingerichtete Entwicklungsumgebung ist `-PythonExe PFAD -UseExistingEnvironment` möglich. Das Überspringen der Tests ist nicht vorgesehen. `scripts/build_windows.py` ohne `--onefile` erstellt weiterhin den bisherigen portablen Ordner mit ZIP-Paket.

Offizielle Grundlagen: [GitHub-Workflow-Berechtigungen](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idpermissions), [GitHub Release CLI](https://cli.github.com/manual/gh_release_create), [PyInstaller-Prozesse und temporäre Laufzeit](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html).
