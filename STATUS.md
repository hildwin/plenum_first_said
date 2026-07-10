# Status — Optimierung Plenum First Said (2026-07-09)

Arbeitsstand der heutigen Session. Noch nichts davon ist im Code umgesetzt außer `DEPLOYMENT.md` — alles andere ist Analyse/Vorschlag.

## Kontext

- Repo frisch geklont (Arbeitsverzeichnis war leer), Redis/Produktion existiert noch nicht — Setup steht noch bevor.
- DIP-API-Key aus `hib-match/src/.env` (`DIP_API_KEY`) genutzt, um Live-Abfragen gegen die Bundestags-API zu machen (der Key in `example.env` ist abgelaufen).

## Gefundene Bugs (noch nicht behoben)

1. **`[A-z]`-Regex-Bug** in `ok_word()`, [parser/text_parse.py:131,133](parser/text_parse.py#L131-L133) — sollte `[A-Za-z]` sein. Aktuell folgenlos, weil `pre_split_clean()` die betroffenen Zeichen vorher schon entfernt, aber fragil bei künftigen Pipeline-Änderungen.
2. **Typ-Mismatch in `check_age()`**, [parser/database.py:129-131](parser/database.py#L129-L131) — `id` (int) wird mit `aktuelle_id` (str aus Redis) verglichen, `int == str` ist immer `False`. Vermutlich aktuell harmlos, aber semantisch falsch.
3. **`find_matches()`** mutiert `new_words` während die Schleife darüber läuft, [parser/text_parse.py:167-178](parser/text_parse.py#L167-L178) — riskantes Pattern, kann Wörter je nach Reihenfolge überspringen.

## Wortfilterung: Nomen-only-Problem (geklärt)

- **Beobachtung bestätigt:** Es werden praktisch nur Substantive gepostet. Ursache: `if word[0].islower(): return False` in `add_to_queue()`, [parser/database.py:161-163](parser/database.py#L161-L163).
- **Git-Historie zur Herkunft:**
  - April 2022 (`e3582fd`): Filter kam als Begleiter zu einer Großbuchstaben-Split-Logik in `wordsplitter` (`re.split('(?=[A-Z])', word)`), markiert mit `# TODO Bindestrichfehler richtig lösen`.
  - Juni 2022 (`0580fcc`, "Refactoring"): Genau diese Split-Logik wurde entfernt und durch eine echte `dehyphenate()`- und `de_enumaration()`-Funktion ersetzt (heutiger Stand). Der `islower`-Filter blieb aber bestehen — sein ursprünglicher Zweck ist seit über 3 Jahren gegenstandslos.
  - **Schlussfolgerung:** Der Filter kann gefahrlos durch eine POS-basierte Prüfung ersetzt werden, um Verben zuzulassen — das Risiko, das er einmal mitigiert hat, existiert in der heutigen Pipeline nicht mehr.

## DIP-API-ID-Recherche (informativ, kein Bug)

- Die `plenarprotokoll-text`-IDs sind **nicht chronologisch** nach Wahlperiode geordnet (per Live-Abfrage verifiziert). Grobe Zuordnung:
  - IDs 1–1441: WP13(Anfang)–WP18 (1994–2017)
  - IDs 1442–5501 (Bereich von `build_database_online.py`): WP1–WP13(Rest) plus WP19–WP20-Anfang
  - IDs 5501+: laufender WP20-Betrieb (2022 →)
- Relevant nur für den **Erstaufbau** der Wortdatenbank (da noch keine Produktion existiert): Der Build-Bereich `range(1442, 5502)` in `build_database_online.py` lässt IDs 1–1441 aus — das müsste beim Erstaufbau mit abgedeckt werden, sonst fehlt WP13(Anfang)–WP18 im Korpus.

## Deployment / Redis (umgesetzt)

- `DEPLOYMENT.md` wurde geschrieben: Redis-Persistenz-Konfiguration (AOF + RDB), Bind/Auth, Systemsettings (`vm.overcommit_memory`), Offsite-Backup-Cron.
- Restliche Infrastruktur-Checkliste (Server, Cron-Zeitplan für `plenar.py`/`post_queue.py`/`db_cleaner.py`, Mastodon-Zugangsdaten, Log-Rotation, Disk-Planung für `archive/`) bisher nur im Chat besprochen, nicht dokumentiert.

## Vorschlag: Claude-API zur Worterkennung (Design, nicht umgesetzt)

Ziel: bessere Lemma-Normalisierung, POS-Tagging (um Verben zuzulassen) und Kompositazerlegung für Neologismen — als Ergänzung, nicht Ersatz für den Redis-Abgleich.

- **Voraussetzung:** Satzkontext geht aktuell in `pre_split_clean()` verloren (Satzzeichen werden vor dem Wortsplitting entfernt). Für sinnvolle Klassifikation braucht man den Ursprungssatz pro Kandidatenwort — müsste vorher separat erfasst werden.
- **Neues Modul** `parser/llm_classify.py`: ein Messages-API-Call pro neuem Protokoll, strukturierter Output (`output_config.format` mit JSON-Schema: `lemma`, `pos`, `is_compound`, `compound_parts`) für alle Kandidatenwörter (die, die keinen exakten Redis-Treffer haben).
- **Integration:**
  - `check_newness()` in `database.py`: Lemma-Abgleich gegen neuen Redis-Namensraum `lemma:<lemma>` statt/zusätzlich zu `similiar_word()`.
  - `add_to_queue()`: POS-Prüfung (`NOUN`/`VERB` zulassen) statt Blanket-`islower`-Filter.
  - OPTV-Doppelprüfung beim Posten bleibt unverändert.
- **Fehlerfall:** Bei Ausfall des Claude-Calls auf die alte Heuristik zurückfallen, nicht den ganzen Lauf abbrechen.
- Modell/Setup: normale (nicht Batch-)Messages API reicht bei diesem Umfang (wenige Kandidatenwörter pro Lauf, nicht zeitkritisch); `anthropic`-SDK und `ANTHROPIC_API_KEY` müssen noch zu `requirements.txt`/`example.env` hinzugefügt werden.

## Nächste Schritte (offen, nächste Session)

1. Entscheiden: Satzkontext-Änderung in `text_parse.py` zuerst isoliert umsetzen, oder direkt zusammen mit `llm_classify.py`.
2. Die drei gefundenen Bugs beheben (klein, risikoarm).
3. Erstaufbau-Strategie für die Wortdatenbank festlegen (IDs 1–1441 mit einbeziehen).
4. Rest der Infrastruktur-Checkliste dokumentieren (Cron, Mastodon-Setup, Log-Rotation).
