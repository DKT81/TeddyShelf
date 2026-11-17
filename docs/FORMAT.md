# Unabhängige TAF-Erzeugung

Referenzen:
- [TeddyCloud Header-Schema](https://github.com/toniebox-reverse-engineering/teddycloud/blob/master/proto/toniebox.pb.taf-header.proto)
- [TeddyCloud Encoder](https://github.com/toniebox-reverse-engineering/teddycloud/blob/master/src/toniefile.c)
- [Opus framing und Padding, RFC 6716 §3](https://www.rfc-editor.org/rfc/rfc6716#section-3)
- [Ogg Opus, RFC 7845](https://www.rfc-editor.org/rfc/rfc7845)

Die ersten vier Bytes enthalten Big-Endian 4092. Darauf folgen Protobuf-Wirefelder: SHA1 des Payloads (1), Payloadlänge (2), Audio-ID (3), gepackte Kapitel-Seitenindizes (4), Füllbytes (5). Die Audio-ID wird stabil aus dem normalisierten PCM abgeleitet; Modellnamen sind davon unabhängig.

FFmpeg erzeugt Stereo-Opus mit 48 kHz, 20-ms-Einzelframes und CBR. Eigener Code liest Pakete und bildet neue Ogg-Seiten. OpusHead und OpusTags belegen zusammen 512 Byte; die erste Audioseite 3584 Byte und weitere Seiten 4096 Byte. Nur das Framing des letzten Pakets einer Seite wird zu Opus Code 3 mit Padding verändert; der komprimierte Frame bleibt identisch. Sequenz, Seriennummer, Granule und CRC werden korrekt geschrieben. Der letzte Granule-Wert erhält die tatsächliche Länge einschließlich Encoder-Preskip, sodass keine Padding-Stille entsteht.

Kapitel sind reale Ogg-Seitenindizes. Der Writer beginnt an der ersten 20-ms-Opus-Paketgrenze ab der gewünschten Zeit eine neue Seite; die Abweichung beträgt höchstens eine Paketlänge. Audio wird dabei weder entfernt noch dupliziert. Kapitel, die keine eigene Seite erhalten können, werden mit einer verständlichen Fehlermeldung abgewiesen. Dateischnitte dagegen erfolgen am PCM-Sample; Exportformate können einen kleinen Encoder-Delay besitzen, der gegen die vollständige Dekodierung geprüft wird.

Prüfung: Protobuf-Grenzen, SHA1, Datenlänge, sämtliche Ogg-Prüfsummen, fortlaufende Seiten, identische Seriennummer, Anfang und Ende des Opus-Streams, Kapitelgrenzen, vollständige Dekodierung und Gesamtdauer. Serverprüfung zusätzlich: Server-Headerinterpretation, komplette SHA256-Dateigleichheit und Modellrücklesen. Das ersetzt keinen physischen Toniebox-Hörtest.
