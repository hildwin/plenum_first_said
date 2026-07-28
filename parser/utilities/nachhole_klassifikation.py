import csv
import os
import sqlite3
import sys

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/nachhole_klassifikation.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

import export
import llm_classify
import text_parse
from database import merke_lemma

# Holt die LLM-Klassifikation (wortart/lemma/lemma_korrekt) fuer Eintraege
# nach, bei denen sie beim Live-Lauf komplett fehlgeschlagen ist (z.B.
# Protokoll 21089: 254 Kandidaten in einem einzigen API-Aufruf haben
# MAX_TOKENS gesprengt, Antwort brach mitten im JSON ab -> siehe der
# BATCH_SIZE-Fix in llm_classify.py). Findet alle Zeilen mit leerem wortart
# fuer die angegebene protokoll_id, klassifiziert sie erneut und wendet das
# Ergebnis ueber dieselbe Logik wie beim Live-Lauf an
# (text_parse.wende_klassifikation_an: LanguageTool-Zweitmeinung,
# ist_lemma_bekannt/ist_wort_bekannt-Dedup, lemma:*-Key setzen) - danach
# werden DB- und CSV-Zeile aktualisiert.
#
# Per Default ein reiner Dry-Run (nur Ausgabe) - erst mit --apply werden
# tatsaechlich DB/CSV/Redis geschrieben.


def _fehlende_laden(conn, protokoll_id):
    return conn.execute(
        "SELECT id, wort, satz FROM neue_woerter WHERE protokoll_id = ? "
        "AND (wortart IS NULL OR wortart = '')",
        (protokoll_id,)).fetchall()


def _aktualisiere_db(conn, db_id, entry):
    conn.execute(
        'UPDATE neue_woerter SET wortart = ?, lemma = ?, lemma_korrekt = ? WHERE id = ?',
        (entry['wortart'], entry['lemma'], int(entry['lemma_korrekt']), db_id))


def _aktualisiere_csv(protokoll_id, aktualisierungen):
    aktionen = {(protokoll_id, wort): entry for wort, entry in aktualisierungen}

    with open(export.CSV_PATH, newline='', encoding='utf-8') as f:
        zeilen = list(csv.DictReader(f))

    treffer = set()
    for zeile in zeilen:
        schluessel = (zeile['protokoll_id'], zeile['wort'])
        entry = aktionen.get(schluessel)
        if entry is None:
            continue
        treffer.add(schluessel)
        zeile['wortart'] = entry['wortart']
        zeile['lemma'] = entry['lemma']
        zeile['lemma_korrekt'] = int(entry['lemma_korrekt'])

    for protokoll_id, wort in set(aktionen) - treffer:
        print('WARNUNG: protokoll_id={}, wort="{}" in DB aktualisiert, aber nicht in '
              'neue_woerter.csv gefunden.'.format(protokoll_id, wort))

    with open(export.CSV_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=export.CSV_FELDER)
        writer.writeheader()
        writer.writerows(zeilen)


def nachholen(protokoll_id, apply):
    with sqlite3.connect(export.DB_PATH) as conn:
        zeilen = _fehlende_laden(conn, protokoll_id)

        if not zeilen:
            print('Keine Zeilen mit fehlender Klassifikation fuer protokoll_id={} gefunden.'.format(protokoll_id))
            return

        print('{} Zeile(n) mit fehlender Klassifikation gefunden, klassifiziere neu ...'.format(len(zeilen)))

        entries = [{'word': wort, 'satz': satz} for (_, wort, satz) in zeilen]
        klassifikation = llm_classify.classify_words(entries)

        aktualisierungen = []
        for i, (db_id, wort, satz) in enumerate(zeilen):
            ergebnis = klassifikation.get(i)
            if ergebnis is None:
                print('UNVERAENDERT (weiterhin nicht klassifiziert): wort="{}"'.format(wort))
                continue

            entry = {'word': wort, 'satz': satz}
            merke_lemma_fn = merke_lemma if apply else (lambda wortart, lemma, id: None)
            text_parse.wende_klassifikation_an(entry, protokoll_id, ergebnis, merke_lemma_fn)

            if 'wortart' not in entry:
                print('UEBERSPRUNGEN (Lemma/Wort bereits anderweitig bekannt): wort="{}"'.format(wort))
                continue

            print('KLASSIFIZIERT: wort="{}" -> wortart={}, lemma={}, lemma_korrekt={}'.format(
                wort, entry['wortart'], entry['lemma'], entry['lemma_korrekt']))

            if apply:
                _aktualisiere_db(conn, db_id, entry)
                aktualisierungen.append((wort, entry))

        if apply:
            conn.commit()
            if aktualisierungen:
                _aktualisiere_csv(protokoll_id, aktualisierungen)
            print()
            print('{} Zeile(n) aktualisiert.'.format(len(aktualisierungen)))


def main():
    apply = '--apply' in sys.argv
    protokoll_ids = [arg for arg in sys.argv[1:] if not arg.startswith('--')]

    if not protokoll_ids:
        print('Aufruf: python nachhole_klassifikation.py <protokoll_id> [<protokoll_id> ...] [--apply]')
        return

    if not apply:
        print('Dry-Run (keine Schreibzugriffe). Mit --apply tatsaechlich anwenden.')
        print()

    for protokoll_id in protokoll_ids:
        nachholen(protokoll_id, apply=apply)


if __name__ == '__main__':
    main()
