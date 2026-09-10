import csv
import os
import re
import sqlite3
import sys

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/korrigiere_protokoll_id.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

import export
from database import r

# Repariert zwei Folgeschaeden des in xml_processing.get()/plenar.py
# behobenen ID-Verwechslungs-Bugs (DIP-interne Dokument-ID statt
# WP+Sitzungsnummer-ID im Export/Korpus gelandet - siehe STATUS.md
# "Kritischer Fund #4"):
#
# 1. Ersetzt eine unter der DIP-internen ID abgelegte Sitzung (z.B. "5808")
#    durch die korrekte WP+Sitzungsnummer-ID (z.B. "21091") - in Redis
#    (protokoll:*/word:*/lemma:*), im Archiv (Dateiname) sowie in
#    neue_woerter.csv/.db. Nur aufgerufen, wenn die falsche ID als Argument
#    uebergeben wird.
# 2. Wandelt in neue_woerter.csv unabhaengig davon jede noch vorhandene
#    "=DATE(j;m;t)"-Excel-Formel zurueck in reinen ISO-Text ("j-m-t") - die
#    Formel wird beim Import in diesem Setup nicht ausgewertet, sondern nur
#    als Text angezeigt (siehe export.py, der Formel-Ansatz wurde deshalb
#    wieder entfernt). Laeuft immer mit, auch ohne falsche-ID-Argument.
#
# Aufruf: python utilities/korrigiere_protokoll_id.py [falsche_id] [--apply]
# Per Default reiner Dry-Run (nur Ausgabe) - erst --apply schreibt tatsaechlich.


def _korrekte_id_aus_hash(falsche_id):
    daten = r.hgetall('protokoll:' + falsche_id)
    if not daten:
        return None
    try:
        wahlperiode = int(daten[b'wahlperiode'].decode('utf-8'))
        protokollnummer = int(daten[b'protokollnummer'].decode('utf-8'))
    except (KeyError, ValueError):
        return None
    return '{:02d}{:03d}'.format(wahlperiode, protokollnummer)


def _keys_mit_id(prefix, falsche_id):
    treffer = []
    for key in r.scan_iter(match=prefix + '*'):
        wert = r.hget(key, 'id')
        if wert and wert.decode('utf-8') == falsche_id:
            treffer.append(key)
    return treffer


DATE_FORMEL = re.compile(r'^=DATE\((\d+);(\d+);(\d+)\)$')


def _formel_zu_iso(wert):
    m = DATE_FORMEL.match(wert or '')
    if not m:
        return None
    jahr, monat, tag = m.groups()
    return '{}-{:02d}-{:02d}'.format(int(jahr), int(monat), int(tag))


def _repariere_redis_und_archiv(falsche_id, korrekte_id, apply):
    word_keys = _keys_mit_id('word:', falsche_id)
    lemma_keys = _keys_mit_id('lemma:', falsche_id)
    print(len(word_keys), 'word:*-Eintraege mit id ==', falsche_id)
    print(len(lemma_keys), 'lemma:*-Eintraege mit id ==', falsche_id)

    archive_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', 'archive')
    alter_pfad = os.path.join(archive_dir, falsche_id + '.xml')
    neuer_pfad = os.path.join(archive_dir, korrekte_id + '.xml')
    if os.path.exists(alter_pfad):
        print('Archiv:', alter_pfad, '->', neuer_pfad)
    else:
        print('Archiv-Datei', alter_pfad, 'nicht gefunden - Umbenennung wird uebersprungen.')

    if not apply:
        return

    alte_daten = r.hgetall('protokoll:' + falsche_id)
    if alte_daten:
        r.hset('protokoll:' + korrekte_id, mapping={k.decode('utf-8'): v.decode('utf-8') for k, v in alte_daten.items()})
        r.delete('protokoll:' + falsche_id)

    pipe = r.pipeline()
    for key in word_keys:
        pipe.hset(key, 'id', korrekte_id)
    for key in lemma_keys:
        pipe.hset(key, 'id', korrekte_id)
    pipe.execute()

    if os.path.exists(alter_pfad):
        os.rename(alter_pfad, neuer_pfad)

    print('Redis + Archiv aktualisiert.')


def _repariere_csv_und_db(falsche_id, korrekte_id, apply):
    if not os.path.exists(export.CSV_PATH):
        print('neue_woerter.csv nicht gefunden - ueberspringe CSV/DB.')
        return

    with open(export.CSV_PATH, newline='', encoding='utf-8') as f:
        zeilen = list(csv.DictReader(f))

    id_korrekturen = 0
    datum_korrekturen = 0
    for zeile in zeilen:
        if korrekte_id and zeile.get('protokoll_id') == falsche_id:
            zeile['protokoll_id'] = korrekte_id
            id_korrekturen += 1
        iso = _formel_zu_iso(zeile.get('datum'))
        if iso:
            zeile['datum'] = iso
            datum_korrekturen += 1

    print(id_korrekturen, 'CSV-Zeile(n) mit korrigierter protokoll_id')
    print(datum_korrekturen, 'CSV-Zeile(n) mit zurueckgedrehtem Datum (Formel -> ISO-Text)')

    if apply and (id_korrekturen or datum_korrekturen):
        with open(export.CSV_PATH, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=export.CSV_FELDER)
            writer.writeheader()
            writer.writerows(zeilen)
        print('neue_woerter.csv geschrieben.')

    if not korrekte_id:
        return

    with sqlite3.connect(export.DB_PATH) as conn:
        anzahl = conn.execute(
            'SELECT COUNT(*) FROM neue_woerter WHERE protokoll_id = ?', (falsche_id,)
        ).fetchone()[0]
        print(anzahl, 'DB-Zeile(n) mit protokoll_id =', falsche_id)
        if apply and anzahl:
            conn.execute(
                'UPDATE neue_woerter SET protokoll_id = ? WHERE protokoll_id = ?',
                (korrekte_id, falsche_id))
            conn.commit()
            print('neue_woerter.db aktualisiert.')


def main():
    apply = '--apply' in sys.argv
    falsche_id = None
    for arg in sys.argv[1:]:
        if not arg.startswith('--'):
            falsche_id = arg
            break

    if not apply:
        print('Dry-Run (keine Schreibzugriffe). Mit --apply tatsaechlich anwenden.')
        print()

    korrekte_id = None
    if falsche_id:
        korrekte_id = _korrekte_id_aus_hash(falsche_id)
        if not korrekte_id:
            print('Konnte keine korrekte WP+Sitzungsnummer-ID aus protokoll:' + falsche_id,
                  'ableiten (Hash fehlt oder wahlperiode/protokollnummer nicht lesbar) - '
                  'ID-Korrektur wird uebersprungen, Datum-Formel-Fix laeuft trotzdem.')
        else:
            print('DIP-ID', falsche_id, '-> WP+Sitzungsnummer-ID', korrekte_id)
        print()

        if korrekte_id:
            _repariere_redis_und_archiv(falsche_id, korrekte_id, apply)
            print()

    _repariere_csv_und_db(falsche_id, korrekte_id, apply)

    if not apply:
        print()
        print('Dry-Run - mit --apply tatsaechlich schreiben.')


if __name__ == '__main__':
    main()
