# Plenum First Said


Plenum First Said findet neue Wörter, die zum ersten Mal während einer Bundestagsdebatte gesagt wurden, samt Satzkontext und Sprecherzuordnung, zur manuellen Nachbearbeitung. Es wird in keiner Weise Korrektheit garantiert.

Hinweis: Automatisches Posten auf Mastodon ist aktuell nicht aktiv (siehe Abschnitt "Mastodon" unten) — der Code dafür ist noch im Repo vorhanden, wird aber nicht mehr aufgerufen.

Das Projekt wurde durch den Twitter-Account [@NYT_first_said](https://x.com/NYT_first_said) von Max Bittker inspiriert und dessen [Code](https://github.com/MaxBittker/nyt-first-said) als Startpunkt genutzt, jedoch zum großen Teil verändert. 

## Funktionsweise

Über eine vom Bundestag bereitgestellte [OpenData-API](https://dip.bundestag.de/%C3%BCber-dip/hilfe/api#content) wird täglich nach einem neuen Plenarprotokoll des Bundestags gesucht. Wird es gefunden, wird jedes einzelne Wort mit einer selbsterstellten Datenbank abgeglichen, die aus allen veröffentlichten Plenarprotokollen aufgebaut wurde. Sollte das Wort nicht in der Datenbank gefunden werden, wird es zusammen mit dem Satz, in dem es fiel, sowie der sprechenden Person (Redner:in, Präsidium oder Zwischenruf/Kommentar) sowohl in `parser/output/neue_woerter.csv` (Arbeitskopie zur Durchsicht) als auch in `parser/output/neue_woerter.db` (dauerhafte, durchsuchbare SQLite-Ablage) abgelegt und besagter Datenbank selbst hinzugefügt.

Unregelmäßigkeiten entstehen z.B. durch Silbentrennungen, die nicht gut von Wortverbindungen getrennt werden können (z.B. Know- (neue Zeile) how) und Rechtschreibfehlern. 

## Architektur

`plenar.py` ist die Hauptfunktion, die den Rest orchestriert. Da in der Regel höchstens ein neues Protokoll pro Tag erscheint, reicht ein Cron-Aufruf alle 12 Stunden (z.B. 10 und 22 Uhr) statt stündlich. `database.py` erlaubt eine Verbindung zur lokalen Redis Datenbank.

`post_queue.py`, `twitter_creds.py` und `mastodon_creds.py` enthalten die (aktuell nicht aufgerufene) Logik zum Posten neuer Wörter auf Mastodon/Twitter. Twitter wurde mittlerweile auskommentiert, weil der Bot nichts zu diesem Höllenort beitragen muss.

`dpi_api.py` verbindet den Bot mit den Servern des Bundestags und sucht nach neuen Protokollen über weiterlaufende IDs. `api_functions.py` hilft bei der Abfrage.

`xml_processing.py` verarbeitet das Protokoll und liefert über `get_redebeitraege()` pro Absatz ein strukturiertes Record mit Sprecherzuordnung (Redner:in, Präsidium oder Kommentar/Zwischenruf), Fraktion/Rolle und einer Zwischenfrage-Kennzeichnung.

`text_parse.py` ist für die Worttrennung, Satzsplitting und Normalisierung da, sowie die Verbindung zum Abgleich mit der Datenbank über `database.py`.

`export.py` schreibt jedes neu gefundene Wort samt Satzkontext und Sprecherzuordnung in `parser/output/neue_woerter.csv` und `parser/output/neue_woerter.db` (SQLite).

Im Ordner utilities finden sich Skripte, die bei dem Aufbau der Datenbank geholfen haben. 

Über das Paket [python-dotenv](https://github.com/theskumar/python-dotenv) werden API-Schlüssel durch Umgebungsvariablen bereitgestellt. Dazu muss eine `.env` Datei in der Basis des Projektes existieren. In dem Repo liegt die Datei `example.env`, die alle Variabeln aufzählt und den momentan öffentlichen API Key des Bundestags beinhaltet.

## DPI API 

Das Dokumentations- und Informationssystem für Parlamentsmaterialien stellt jährlich einen neuen öffentlichen Key aus. Der aktuelle bis Mai 2025 gültige Key ist unter `example.env` hinterlegt. Bei dauerhafter Nutzung empfiehlt es sich jedoch, [einen eigenen Key zu beantragen](https://dip.bundestag.de/%C3%BCber-dip/hilfe/api#content).

## Mastodon (aktuell nicht aktiv)

Der Bot postete früher automatisiert auf Mastodon; das ist mit dem Umstieg auf CSV-/DB-Export für die manuelle Nachbearbeitung nicht mehr aktiv. `post_queue.py` und `mastodon_cred.py` sind unverändert im Repo vorhanden, werden aber von `plenar.py` nicht mehr aufgerufen. Für den Zugang zu Mastodon wurde [Mastodon.py](https://github.com/halcy/Mastodon.py) genutzt.

Die früher genutzten Mastodon-Accounts: <a rel="me" href="https://mastodon.social/@BT_First_Said">@BT_First_Said@mastodon.social</a> und <a rel="me" href="https://mastodon.social/@FSBT_Kontext">@FSBT_Kontext@mastodon.social</a>.


## Was bedeutet "neues Wort"?

Aus Gründen der Unterhaltung werden einige Worte aussortiert, die zwar tatsächlich zum ersten Mal so gesagt werden, aber nur bedingt an sich einen Informationswert haben. Folgendes wird z.B. versucht, herauszufiltern:
- Plural
- Genitiv
- gegenderte Formen
- Wörter unter 4 Buchstaben
- Gesetzesabkürzungen




