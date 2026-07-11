import csv
import logging
import os
import sqlite3

from database import r

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'output')
CSV_PATH = os.path.join(OUTPUT_DIR, 'neue_woerter.csv')
DB_PATH = os.path.join(OUTPUT_DIR, 'neue_woerter.db')

CSV_FELDER = ['protokoll_id', 'datum', 'wort', 'satz', 'sprecher_typ', 'sprecher', 'fraktion', 'ist_zwischenfrage']


# Liest Datum aus dem bereits vorhandenen protokoll:<id>-Hash (befüllt durch dip_api.add_protokoll)
def _protokoll_datum(id):
    keys = r.hgetall('protokoll:' + str(id))
    datum = keys.get(b'datum')
    return datum.decode('utf-8') if datum else None


def _init_db(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS neue_woerter (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            protokoll_id TEXT NOT NULL,
            datum TEXT,
            wort TEXT NOT NULL,
            satz TEXT,
            sprecher_typ TEXT,
            sprecher TEXT,
            fraktion TEXT,
            ist_zwischenfrage INTEGER,
            erstellt_am TEXT DEFAULT (datetime('now'))
        )
    ''')


def _append_csv(zeile):
    ist_neu = not os.path.exists(CSV_PATH)

    with open(CSV_PATH, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FELDER)
        if ist_neu:
            writer.writeheader()
        writer.writerow(zeile)


def _append_db(zeile):
    with sqlite3.connect(DB_PATH) as conn:
        _init_db(conn)
        conn.execute(
            '''INSERT INTO neue_woerter
               (protokoll_id, datum, wort, satz, sprecher_typ, sprecher, fraktion, ist_zwischenfrage)
               VALUES (:protokoll_id, :datum, :wort, :satz, :sprecher_typ, :sprecher, :fraktion, :ist_zwischenfrage)''',
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
