# Mitgelieferte Komponenten

## Öffentlicher Quellstand

Dieses Repository enthält den Projektquellcode und lizenzierte Projektressourcen, keine Windows-Laufzeitdistribution. Die folgende Liste beschreibt die für einen Build verwendeten Komponenten. Vor einer öffentlichen Binärverteilung müssen sämtliche tatsächlich mitgelieferten Lizenztexte, Drittanbieterhinweise und die passenden Quellennachweise vollständig zusammengestellt werden. Insbesondere ersetzt ein allgemeiner Projektlink nicht den Nachweis der genauen Build- und Abhängigkeitsrevisionen. Alte Programmarchive gelten nicht automatisch als aus diesem bereinigten Quellstand neu gebaut oder zur öffentlichen Weitergabe geprüft.

## Komponentenübersicht

- Python 3.13.9: PSF-Lizenz, https://www.python.org/downloads/release/python-3139/
- PySide6 / Qt: LGPLv3/GPLv3 bzw. komponentenspezifische Lizenzen, dynamisch gelinkt. https://doc.qt.io/qtforpython-6/licenses.html
- FFmpeg / FFprobe: separate ausführbare Werkzeuge, Build `N-128790-g9bee1e603c-20261111`, GPLv3-Konfiguration. Projekt/Quellcode: https://github.com/FFmpeg/FFmpeg/tree/9bee1e603c — Windows-Buildumgebung: https://github.com/BtbN/FFmpeg-Builds ; Buildskripte und Abhängigkeiten: https://github.com/BtbN/FFmpeg-Builds/tree/master . Binaries sind getrennt austauschbar und werden nicht in den Python-Code gelinkt.
- Mutagen: GPLv2 oder neuer, https://github.com/quodlibet/mutagen
- Pillow: MIT-CMU und enthaltene Bibliothekslizenzen der jeweiligen Version, https://github.com/python-pillow/Pillow
- Requests: Apache-2.0, https://github.com/psf/requests
- Noto Sans: SIL Open Font License, enthalten als assets/FONT-LICENSE.txt, https://github.com/notofonts/noto-fonts

Die Laufzeitdistribution enthält weitere transitive Abhängigkeiten und deren jeweilige Metadaten/Lizenzen. Eigener Code ist GPLv3 oder neuer. Keine TonieToolbox-Bibliothek ist Bestandteil der Anwendung. Keine Originalcover oder Hörspiele werden verteilt.
