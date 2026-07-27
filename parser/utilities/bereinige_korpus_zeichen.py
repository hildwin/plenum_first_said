import os
import sys

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/bereinige_korpus_zeichen.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

import database
from text_parse import clean_word

# Bereinigt rueckwirkend die Zeichen-Artefakte (Geviertstrich, Halbgeviert-
# strich, Gradzeichen, Ellipse, oeffnendes einfaches Anfuehrungszeichen,
# weicher Trennstrich, geschuetzter Bindestrich - siehe STATUS.md), die im
# laufenden Betrieb nach und nach in text_parse.py gefixt wurden, aber nur
# fuer KUENFTIG verarbeitete Dateien gelten. Dieses Skript holt den bereits
# bestehenden Korpus-Anteil auf denselben Stand.
#
# WICHTIG: Nur ausfuehren, wenn kein Korpus-Aufbau/Live-Betrieb gleichzeitig
# gegen dieselbe Redis-DB schreibt (Race-Condition-Risiko bei paralleler
# Bereinigung).
#
# Scannt alle word:*-Keys (nur Key-Namen, kein Redis-Roundtrip fuer den
# Bereinigungs-Check selbst) und behandelt jeden "dirty" Fund (bereinigte
# Form weicht vom gespeicherten Wort ab) wie folgt:
#   - Bereinigte Form existiert noch nicht als eigener Key -> umbenennen.
#   - Bereinigte Form existiert schon -> die chronologisch AELTERE der beiden
#     IDs gewinnt (Vergleich wie check_age() in database.py), der jeweils
#     unterlegene Key wird geloescht.
#   - clean_word() liefert None (Ergebnis leer oder in mehrere Teile
#     zerfallen) -> nicht automatisch verarbeitet, nur zur manuellen Pruefung
#     aufgelistet.
#
# Per Default ein reiner Dry-Run (nur Ausgabe, keine Schreibzugriffe) -
# erst mit --apply werden tatsaechlich RENAME/HSET/DELETE ausgefuehrt.


def _protokoll_sortkey(id):
    p = database.r.hgetall('protokoll:' + str(id))
    try:
        wahlperiode = int(p[b'wahlperiode'].decode('utf-8'))
        protokollnummer = int(p[b'protokollnummer'].decode('utf-8'))
        return (wahlperiode, protokollnummer)
    except (KeyError, ValueError):
        return None


# Vergleicht zwei Protokoll-IDs und liefert die chronologisch AELTERE.
# Kann kein Datum ermitteln (fehlendes protokoll:*-Hash) -> a wird bevorzugt
# beibehalten (konservativ, aendert nichts an einem bestehenden Eintrag).
def _aeltere_id(a, b):
    sortkey_a = _protokoll_sortkey(a)
    sortkey_b = _protokoll_sortkey(b)

    if sortkey_a is None or sortkey_b is None:
        return a

    return a if sortkey_a <= sortkey_b else b


def bereinige(apply=False):
    cursor = 0
    unveraendert = 0
    umbenannt = 0
    gemergt = 0
    manuelle_pruefung = []

    while True:
        cursor, keys = database.r.scan(cursor=cursor, match='word:*', count=1000)

        for key in keys:
            key_str = key.decode('utf-8')
            word = key_str[len('word:'):]
            cleaned = clean_word(word)

            if cleaned is None:
                manuelle_pruefung.append(word)
                continue

            if cleaned == word:
                unveraendert += 1
                continue

            dirty_id_bytes = database.r.hget(key_str, 'id')
            dirty_id = dirty_id_bytes.decode('utf-8') if dirty_id_bytes else None
            clean_key = 'word:' + cleaned
            clean_id_bytes = database.r.hget(clean_key, 'id')

            if clean_id_bytes is None:
                print('UMBENENNEN: "{}" -> "{}" (id={})'.format(word, cleaned, dirty_id))
                if apply:
                    database.r.rename(key_str, clean_key)
                    database.r.hset(clean_key, 'word', cleaned)
                umbenannt += 1
                continue

            clean_id = clean_id_bytes.decode('utf-8')
            gewinner_id = _aeltere_id(clean_id, dirty_id) if dirty_id else clean_id

            print('MERGE: "{}" (id={}) + "{}" (id={}) -> "{}" behaelt id={}'.format(
                word, dirty_id, cleaned, clean_id, cleaned, gewinner_id))

            if apply:
                if gewinner_id != clean_id:
                    database.r.hset(clean_key, 'id', gewinner_id)
                database.r.delete(key_str)
            gemergt += 1

        if cursor == 0:
            break

    print()
    print('--- Zusammenfassung ({}) ---'.format('ANGEWENDET' if apply else 'DRY-RUN, nichts geschrieben'))
    print('Unveraendert:', unveraendert)
    print('Umbenannt:', umbenannt)
    print('Gemergt:', gemergt)
    print('Manuelle Pruefung noetig ({}):'.format(len(manuelle_pruefung)))
    for w in manuelle_pruefung:
        print(' -', repr(w))


def main():
    apply = '--apply' in sys.argv
    if not apply:
        print('Dry-Run (keine Schreibzugriffe). Mit --apply tatsaechlich anwenden.')
        print()
    bereinige(apply=apply)


if __name__ == '__main__':
    main()
