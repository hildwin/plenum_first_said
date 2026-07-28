import csv
import datetime
import logging
import os
import sqlite3

from database import r

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'output')
CSV_PATH = os.path.join(OUTPUT_DIR, 'neue_woerter.csv')
DB_PATH = os.path.join(OUTPUT_DIR, 'neue_woerter.db')

CSV_FELDER = ['protokoll_id', 'datum', 'wort', 'wortart', 'lemma', 'lemma_korrekt', 'satz', 'sprecher_typ', 'sprecher', 'fraktion', 'ist_zwischenfrage']


# Liest Datum aus dem bereits vorhandenen protokoll:<id>-Hash (befüllt durch
# dip_api.add_protokoll()/xml_processing.get_protokoll_metadata(), dort
# jeweils im deutschen Format TT.MM.JJJJ). Fuer den Export auf ISO (JJJJ-MM-TT)
# umgestellt - TT.MM.JJJJ wird von Excel je nach Laendereinstellung nicht
# zuverlaessig erkannt (teils als Text importiert, teils Tag/Monat vertauscht),
# ISO 8601 ist dagegen unabhaengig von der Locale eindeutig. Die Rohablage in
# Redis bleibt bewusst unveraendert (andere Konsumenten wie wort_herkunft.py
# erwarten weiterhin TT.MM.JJJJ).
def _protokoll_datum(id):
    keys = r.hgetall('protokoll:' + str(id))  # type: ignore[assignment]
    datum = keys.get(b'datum')
    if not datum:
        return None

    datum = datum.decode('utf-8')
    try:
        return datetime.datetime.strptime(datum, '%d.%m.%Y').strftime('%Y-%m-%d')
    except ValueError:
        return datum


def _init_db(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS neue_woerter (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            protokoll_id TEXT NOT NULL,
            datum TEXT,
            wort TEXT NOT NULL,
            wortart TEXT,
            lemma TEXT,
            lemma_korrekt INTEGER,
            satz TEXT,
            sprecher_typ TEXT,
            sprecher TEXT,
            fraktion TEXT,
            ist_zwischenfrage INTEGER,
            erstellt_am TEXT DEFAULT (datetime('now'))
        )
    ''')

    # Bestandsschutz fuer bereits existierende neue_woerter.db (z.B. auf dem
    # Produktionsserver), die vor Einfuehrung von lemma_korrekt angelegt wurde -
    # CREATE TABLE IF NOT EXISTS aendert eine bestehende Tabelle nicht, daher
    # hier per ALTER TABLE nachziehen, falls die Spalte fehlt.
    spalten = {row[1] for row in conn.execute('PRAGMA table_info(neue_woerter)')}
    if 'lemma_korrekt' not in spalten:
        conn.execute('ALTER TABLE neue_woerter ADD COLUMN lemma_korrekt INTEGER')


# Bestandsschutz fuer eine bereits existierende neue_woerter.csv (z.B. auf dem
# Produktionsserver), deren Header noch nicht CSV_FELDER entspricht (z.B. vor
# Einfuehrung von lemma_korrekt angelegt) - ohne diese Migration wuerden neu
# angehaengte Zeilen mehr/andere Spalten haben als der bestehende Header, was
# beim Import (Excel/pandas) zu Spaltenverschiebungen fuehrt.
#
# Behandelt auch den Fall, dass bereits VOR dieser Migration einzelne Zeilen
# mit dem neuen (laengeren) Spaltenlayout unter dem alten Header angehaengt
# wurden (Code-Update vor Header-Update deployed): Zeilen werden anhand ihrer
# tatsaechlichen Feldanzahl zugeordnet (alte Laenge -> alter Header, neue
# Laenge -> CSV_FELDER), nicht pauschal ueber einen einzigen Header geparst -
# sonst wuerden genau diese Zeilen beim Migrieren falsch verschoben statt
# korrigiert.
def _migriere_csv_falls_noetig():
    if not os.path.exists(CSV_PATH):
        return

    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        rohzeilen = list(csv.reader(f))

    if not rohzeilen:
        return

    alter_header = rohzeilen[0]
    if alter_header == CSV_FELDER:
        return

    zeilen = []
    anomalien = 0
    for roh in rohzeilen[1:]:
        if len(roh) == len(alter_header):
            zeilen.append(dict(zip(alter_header, roh)))
        elif len(roh) == len(CSV_FELDER):
            zeilen.append(dict(zip(CSV_FELDER, roh)))
        else:
            anomalien += 1
            zeilen.append(dict(zip(alter_header, roh)))

    with open(CSV_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FELDER)
        writer.writeheader()
        writer.writerows(zeilen)

    logging.info(
        'neue_woerter.csv-Header migriert (%s -> %s), %d Zeile(n) neu geschrieben.',
        alter_header, CSV_FELDER, len(zeilen))
    if anomalien:
        logging.warning(
            'neue_woerter.csv-Migration: %d Zeile(n) mit unerwarteter Feldanzahl '
            '(weder altes noch neues Layout) - best effort mit altem Header geparst.',
            anomalien)


# Wandelt ein ISO-Datum (JJJJ-MM-TT) in eine Excel-Formel um, die beim
# Oeffnen der CSV zuverlaessig als echtes Datum ausgewertet wird - reiner
# ISO-Text wird von Excel je nach Locale/Version NICHT zuverlaessig als Datum
# erkannt (beobachtet: "2025-05-22" wurde beim Import zu "22052025"
# verstuemmelt). Nur fuer die CSV (Arbeitskopie fuer die manuelle Durchsicht
# in Excel) - die SQLite-DB behaelt bewusst das reine ISO-Datum, damit
# Datums-Abfragen/-Sortierung dort weiterhin normal funktionieren. Semikolon
# als Argumenttrenner (deutsches Excel-Gebietsschema).
def _excel_datum(iso_datum):
    if not iso_datum:
        return iso_datum

    try:
        jahr, monat, tag = iso_datum.split('-')
        return '=DATE({};{};{})'.format(int(jahr), int(monat), int(tag))
    except ValueError:
        return iso_datum


def _append_csv(zeile):
    _migriere_csv_falls_noetig()
    ist_neu = not os.path.exists(CSV_PATH)

    csv_zeile = dict(zeile)
    csv_zeile['datum'] = _excel_datum(zeile.get('datum'))

    with open(CSV_PATH, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FELDER)
        if ist_neu:
            writer.writeheader()
        writer.writerow(csv_zeile)


def _append_db(zeile):
    with sqlite3.connect(DB_PATH) as conn:
        _init_db(conn)
        conn.execute(
            '''INSERT INTO neue_woerter
               (protokoll_id, datum, wort, wortart, lemma, lemma_korrekt, satz, sprecher_typ, sprecher, fraktion, ist_zwischenfrage)
               VALUES (:protokoll_id, :datum, :wort, :wortart, :lemma, :lemma_korrekt, :satz, :sprecher_typ, :sprecher, :fraktion, :ist_zwischenfrage)''',
            zeile,
        )


# Schreibt ein neues Wort samt Satzkontext/Sprecherzuordnung in CSV (Arbeitskopie
# für die manuelle Durchsicht) und SQLite (dauerhafte, durchsuchbare Ablage).
def append_row(entry, id):

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    zeile = {
        'protokoll_id': str(id),
        'datum': _protokoll_datum(id),
        'wort': entry['word'],
        # Nur gefuellt, wenn die LLM-Klassifikation fuer dieses Wort
        # erfolgreich war (siehe prune() in text_parse.py) - sonst leer,
        # NICHT das Fehlen einer Klassifikation vortaeuschen.
        'wortart': entry.get('wortart', ''),
        'lemma': entry.get('lemma', ''),
        'lemma_korrekt': int(entry['lemma_korrekt']) if 'lemma_korrekt' in entry else '',
        'satz': entry.get('satz'),
        'sprecher_typ': entry.get('sprecher_typ'),
        'sprecher': entry.get('sprecher'),
        'fraktion': entry.get('fraktion'),
        'ist_zwischenfrage': int(bool(entry.get('ist_zwischenfrage'))),
    }

    try:
        _append_csv(zeile)
        _append_db(zeile)
        logging.info('Wort "%s" exportiert (CSV + DB).', entry['word'])
        return True
    except Exception as e:
        logging.exception(e)
        return False
