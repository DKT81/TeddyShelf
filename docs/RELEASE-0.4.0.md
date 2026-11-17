# TeddyStudio 0.4.0

- PCM-Arbeitsaudio wird nach jedem Verarbeitungsversuch entfernt, auch nach Fehler, Pause und Abbruch. Altbestände in inaktiven Arbeitsordnern werden beim Start bereinigt. Eine manuelle Bereinigung steht unter Einstellungen → Protokolle bereit. Quellen und fertige Exporte bleiben erhalten.
- Die Sammlung nennt die Aktion jetzt „In TeddyCloud übertragen“.
- Eigene Bestandsansicht für tatsächlich vorhandene TeddyCloud-Dateien und Custom-/Katalogmodelle; lokale Sicherung und Audioexport über die vorhandene Warteschlange.
- Separate Ansicht für konkrete Tonies und ihre Modell-/Audiozuordnung, mit expliziter Vorschau, Konflikterkennung und Rückleseprüfung. Kein Beschreiben von NFC-Chips.
- Creative-Tonie-Projekte und YouTube-Quellen bleiben vorgemerkt.

Geprüfte TAF-Zwischenergebnisse bleiben für Wiederaufnahme erhalten. PCM wird dafür bei Bedarf neu dekodiert. Ein noch laufender Auftrag benötigt weiterhin temporären Speicher; parallele Aufträge erhöhen den Spitzenbedarf.
