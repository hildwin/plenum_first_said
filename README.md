# Plenum First Said

Plenum First Said findet neue Wörter, die zum ersten Mal während einer Bundestagsdebatte gesagt wurden, samt Satzkontext und Sprecherzuordnung, zur manuellen Nachbearbeitung. Es wird in keiner Weise Korrektheit garantiert.

Hinweis: Automatisches Posten auf Mastodon ist aktuell nicht aktiv (siehe Abschnitt "Mastodon" unten) — der Code dafür ist noch im Repo vorhanden, wird aber nicht mehr aufgerufen.

Das Projekt wurde durch den Twitter-Account [@NYT_first_said](https://x.com/NYT_first_said) von Max Bittker inspiriert und dessen [Code](https://github.com/MaxBittker/nyt-first-said) als Startpunkt genutzt, jedoch zum großen Teil verändert. Aufbauend auf dem bisherigen Code wird nun ermittelt, wann ein Wort erstmals im Bundestagsplenum gesagt wurde - und in welchem Zusammenhang.

## Funktionsweise

Über eine vom Bundestag bereitgestellte [OpenData-API](https://dip.bundestag.de/%C3%BCber-dip/hilfe/api#content) wird täglich nach einem neuen Plenarprotokoll des Bundestags gesucht. Wird es gefunden, wird jedes einzelne Wort mit einer selbsterstellten Datenbank abgeglichen, die aus allen veröffentlichten Plenarprotokollen aufgebaut wurde. Sollte das Wort nicht in der Datenbank gefunden werden, wird es zusammen mit dem Satz, in dem es fiel, sowie der sprechenden Person (Redner:in, Präsidium oder Zwischenruf/Kommentar) sowohl in `parser/output/neue_woerter.csv` (Arbeitskopie zur Durchsicht) als auch in `parser/output/neue_woerter.db` (dauerhafte, durchsuchbare SQLite-Ablage) abgelegt und besagter Datenbank selbst hinzugefügt.

Unregelmäßigkeiten entstehen z.B. durch Silbentrennungen, die nicht gut von Wortverbindungen getrennt werden können (z.B. Know- (neue Zeile) how) und Rechtschreibfehlern.

## Architektur

`plenar.py` ist die Hauptfunktion, die den Rest orchestriert. Da in der Regel höchstens ein neues Protokoll pro Tag erscheint, reicht ein Cron-Aufruf alle 12 Stunden (z.B. 10 und 22 Uhr) statt stündlich. `database.py` erlaubt eine Verbindung zur lokalen Redis Datenbank (Persistenz-Konfiguration siehe `DEPLOYMENT.md`).

`post_queue.py`, `twitter_creds.py` und `mastodon_creds.py` enthalten die (aktuell nicht aufgerufene) Logik zum Posten neuer Wörter auf Mastodon/Twitter. Twitter wurde mittlerweile auskommentiert, weil der Bot nichts zu diesem Höllenort beitragen muss.

`dip_api.py` verbindet den Bot mit den Servern des Bundestags und sucht nach neuen Protokollen über weiterlaufende IDs. `api_functions.py` hilft bei der Abfrage.

`xml_processing.py` verarbeitet das Protokoll und liefert über `get_redebeitraege()` pro Absatz ein strukturiertes Record mit Sprecherzuordnung (Redner:in, Präsidium oder Kommentar/Zwischenruf), Fraktion/Rolle und einer Zwischenfrage-Kennzeichnung.

`text_parse.py` ist für die Worttrennung, Satzsplitting und Normalisierung da, sowie die Verbindung zum Abgleich mit der Datenbank über `database.py`.

`export.py` schreibt jedes neu gefundene Wort samt Satzkontext und Sprecherzuordnung in `parser/output/neue_woerter.csv` und `parser/output/neue_woerter.db` (SQLite).

`llm_classify.py` klassifiziert die nach den bestehenden Filtern verbliebenen Kandidatenwörter eines Protokolls in einem einzigen gebündelten Claude-API-Aufruf (Lemma + Ist-es-ein-Nomen), damit `prune()` echte Wortarterkennung statt einer reinen Endungs-Heuristik nutzen kann. Schlägt der Aufruf fehl (Netzwerk, Rate-Limit, o.ä.), exportiert `prune()` die Kandidaten unverändert ohne Nomen-/Lemma-Filter weiter — der Tages-Export geht dadurch nie verloren.

Im Ordner `utilities` finden sich Hilfsskripte: `build_database_local.py` verarbeitet lokal in `parser/archive/` abgelegte Protokoll-XMLs in den Korpus (für den Erstaufbau, siehe unten). `download_new_format_xml.py` lädt Protokolle im neuen, reich strukturierten XML-Format automatisiert über die DIP-API herunter. `load_namen.py` befüllt den Namensfilter (siehe "Was bedeutet neues Wort?") aus den MdB-Stammdaten, wahlweise aus einer lokalen Datei oder per `--url` direkt von bundestag.de. `wort_herkunft.py` schlägt nach, in welcher Sitzung/Wahlperiode ein Wort im Korpus zuerst auftauchte.

Über das Paket [python-dotenv](https://github.com/theskumar/python-dotenv) werden API-Schlüssel durch Umgebungsvariablen bereitgestellt. Dazu muss eine `.env` Datei in der Basis des Projektes existieren. In dem Repo liegt die Datei `example.env`, die alle Variabeln aufzählt und den momentan öffentlichen API Key des Bundestags beinhaltet. Für `llm_classify.py` muss zusätzlich ein eigener `ANTHROPIC_API_KEY` gesetzt werden (kein öffentlicher Key hinterlegt).

## Datenbank-Erstaufbau

Der Korpus wurde einmalig aus allen historischen Plenarprotokollen aufgebaut. Bundestagsprotokolle liegen in zwei grundverschiedenen XML-Formaten vor: einem reich strukturierten Format (`<sitzungsverlauf>`/`<rede>`/`<redner>`, ab ca. Mitte der 19. Wahlperiode) und einem älteren, unstrukturierten Flat-Text-Format (davor). `process_woerter()` erkennt beide automatisch.

Für neuere Protokolle (reiches Format) lassen sich alle Dateien automatisiert per `download_new_format_xml.py` über die DIP-API holen. Für ältere Protokolle bietet die Open-Data-Seite des Bundestags kein Sammel-Archiv an — diese müssen manuell heruntergeladen und nach `parser/archive/` gelegt werden. Anschließend füllt `build_database_local.py` daraus den Korpus. Nach einem (Neu-)Aufbau muss `meta:id` in Redis manuell auf die zuletzt verarbeitete ID gesetzt werden, bevor `plenar.py` den Live-Betrieb aufnimmt.

## DIP API

Das Dokumentations- und Informationssystem für Parlamentsmaterialien stellt jährlich einen neuen öffentlichen Key aus. Der aktuelle bis Mai 2025 gültige Key ist unter `example.env` hinterlegt. Bei dauerhafter Nutzung empfiehlt es sich jedoch, [einen eigenen Key zu beantragen](https://dip.bundestag.de/%C3%BCber-dip/hilfe/api#content).

## Mastodon (aktuell nicht aktiv)

Der Bot postete früher automatisiert auf Mastodon; das ist mit dem Umstieg auf CSV-/DB-Export für die manuelle Nachbearbeitung nicht mehr aktiv. `post_queue.py` und `mastodon_cred.py` sind unverändert im Repo vorhanden, werden aber von `plenar.py` nicht mehr aufgerufen. Für den Zugang zu Mastodon wurde [Mastodon.py](https://github.com/halcy/Mastodon.py) genutzt.

## Was bedeutet "neues Wort"?

Aus Gründen der Unterhaltung werden einige Worte aussortiert, die zwar tatsächlich zum ersten Mal so gesagt werden, aber nur bedingt an sich einen Informationswert haben. Folgendes wird z.B. versucht, herauszufiltern:

- Plural
- Genitiv
- Wörter unter 5 Buchstaben — außer sie sind komplett großgeschrieben (Abkürzungen wie "DDR", "NATO" werden also erkannt, kurze Wortfragmente nicht)
- Gängige Funktionswörter (Artikel, Präpositionen, Konjunktionen, Pronomen, Hilfs-/Modalverben, Füllpartikeln — siehe `FUELLWOERTER` in `text_parse.py`)
- Vor- und Nachnamen von Abgeordneten (laut MdB-Stammdaten seit der 1. Wahlperiode, siehe `utilities/load_namen.py`) — diese werden weiterhin im Korpus getrackt, aber aus der Export-CSV/DB herausgefiltert
- Wörter, die laut LLM-Klassifikation (`llm_classify.py`) im Satzkontext kein Nomen sind
- Wörter, deren Lemma laut LLM-Klassifikation bereits bekannt ist (verhindert künftige Dopplungen wie z.B. Genitiv-/Plural-Varianten desselben Wortstamms) — dieser Abgleich läuft nur vorausschauend ab Einführung des Features, nicht rückwirkend gegen den bereits bestehenden historischen Korpus

Gegenderte Formen (z.B. "Bundeskanzlerin", "Alterspräsidentin") werden dagegen bewusst **nicht** herausgefiltert, sondern als eigenständiges neues Wort erkannt — das erstmalige Auftreten einer weiblichen Form eines zuvor nur männlich besetzten Amts ist gerade ein bemerkenswerter Fund.
