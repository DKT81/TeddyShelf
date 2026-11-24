# Reproduzierbarer Windows-Build

Der automatische Windows-x64-Ein-Datei-Build wird durch `scripts/build-windows.ps1` und `.github/workflows/windows-build.yml` ausgeführt. Er integriert die Laufzeitwerkzeuge, führt sämtliche Tests aus und prüft danach die tatsächliche EXE ohne System-Python oder FFmpeg. Details, manuelle Ausführung und Versions-Tags: [Windows Build & Release](WINDOWS-RELEASES.md). Der nachfolgende Ordner-Build bleibt für die Entwicklung verfügbar.

Python 3.13 x64 installieren und eine virtuelle Umgebung erstellen:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -e . pytest pyinstaller
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts/build_windows.py
```

FFmpeg und FFprobe müssen als `tools/ffmpeg.exe` und `tools/ffprobe.exe` vorliegen. Die Anwendung löst Werkzeuge zuerst über die konfigurierten Pfade, danach über den mitgelieferten tools-Ordner und zuletzt über PATH auf. Binaries werden nicht ins Quellrepository eingecheckt. Build-Provenienz und Lizenzen siehe THIRD_PARTY.md. Der Build erstellt `dist/TeddyShelf/TeddyShelf.exe`, das portable ZIP und dessen SHA256-Datei.

Im Entwicklungsrechner wurde die offizielle Python-3.13.9-Embeddable-Laufzeit verwendet, mit separatem pip und PyInstaller. Auf anderen Rechnern ist die reguläre Python-Installation für Builds einfacher. Der Nutzer benötigt beim fertigen Paket keine Python-Installation. Die Anwendung ist nicht codesigniert; für automatische Updates oder einen signierten Installer ist keine Infrastruktur eingerichtet.

Qt wird dynamisch als DLL geliefert. Das portable Verzeichnis darf nicht auf die EXE allein reduziert werden. Zu Testzwecken kann `QT_QPA_PLATFORM=offscreen` gesetzt werden. Für einen automatisierten isolierten Lauf `TEDDYSHELF_DATA` auf ein Testverzeichnis setzen.
