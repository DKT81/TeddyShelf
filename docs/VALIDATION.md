# Prüfung des veröffentlichten Quellstands

Der Quellstand umfasst Sammlung, Metadaten, Cover, Audioschnitt, vollständige TAF-Playlists, geprüfte TeddyCloud-Übertragung, lokale Downloads und manuelle Tonie-Zuordnungen. Die Warteschlange unterstützt Pause, Abbruch und Wiederaufnahme; temporäres PCM wird bereinigt.

Die automatisierten Tests verwenden erzeugte Testtöne, erfundene Metadaten, lokale Testserver und isolierte Datenordner. Sie prüfen unter anderem TAF-Header, Ogg-Prüfsummen, vollständige Dekodierung, Kapitelgrenzen, Serverrücklesen, wiederholte Ausführung und die PCM-Bereinigung. Der aktuelle öffentliche Testlauf ist unter dem Reiter **Actions** dieses Repositorys einsehbar.

Die Dokumentationsbilder enthalten ausschließlich eigens erzeugte Beispieldaten. Persönliche Serveradressen, Quellordner und alte Git-Commit-Identitäten gehören nicht zu diesem Quellstand.

Für Version 0.4.2 wurden 114 automatisierte Tests unter Windows erfolgreich ausgeführt. Zusätzlich wurde der veröffentlichte Dateibaum mit der lokal geprüften Fassung verglichen. Die öffentliche Quellhistorie wurde unabhängig vom bisherigen Entwicklungsrepository angelegt und verwendet eine GitHub-`noreply`-Adresse.

## Grenzen

Automatisierte Datei- und Protokollprüfungen ersetzen keinen Hörtest auf einer physischen Toniebox. Alte Windows-Pakete sind nicht automatisch identisch mit dem bereinigten Quellstand. Eine neue öffentliche Binärdistribution benötigt einen erneuten Build, Datenschutzprüfung sowie vollständige Lizenz- und Quellennachweise der mitgelieferten Komponenten; siehe [THIRD_PARTY.md](../THIRD_PARTY.md).
