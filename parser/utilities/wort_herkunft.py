import os
import sys
from collections import Counter

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/wort_herkunft.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

import database
import dwds_api


# Liefert Wahlperiode/Sitzungsnummer/Datum fuer die Quelle eines einzelnen Wortes
def wort_herkunft(word):
    id_bytes = database.r.hget('word:' + word, 'id')
    if not id_bytes:
        return None

    id = id_bytes.decode('utf-8')
    protokoll = database.r.hgetall('protokoll:' + id)

    return {
        'id': id,
        'wahlperiode': protokoll.get(b'wahlperiode', b'').decode('utf-8'),
        'protokollnummer': protokoll.get(b'protokollnummer', b'').decode('utf-8'),
        'datum': protokoll.get(b'datum', b'').decode('utf-8'),
    }


# Holt per Scan-Seite die 'id' aller uebergebenen Keys in einem einzigen
# Roundtrip statt einem HGET pro Key - macht bei entfernten Redis-Instanzen
# (z.B. per SSH-Tunnel) den Unterschied zwischen Sekunden und Minuten.
def _ids_gepipelined(keys):
    if not keys:
        return []
    pipe = database.r.pipeline()
    for key in keys:
        pipe.hget(key, 'id')
    return pipe.execute()


# Zaehlt, wie viele Woerter pro Wahlperiode zuerst dort auftauchten.
# Scannt alle word:*-Keys - bei einem grossen Korpus spuerbar CPU-intensiv,
# daher am besten ausserhalb eines laufenden Korpus-Aufbaus ausfuehren.
def uebersicht_nach_wahlperiode():
    anzahl_pro_wp = Counter()
    protokoll_cache = {}
    cursor = 0

    while True:
        cursor, keys = database.r.scan(cursor=cursor, match='word:*', count=1000)
        ids = _ids_gepipelined(keys)

        neue_ids = list({
            id_bytes.decode('utf-8') for id_bytes in ids
            if id_bytes and id_bytes.decode('utf-8') not in protokoll_cache
        })
        if neue_ids:
            wp_pipe = database.r.pipeline()
            for id in neue_ids:
                wp_pipe.hget('protokoll:' + id, 'wahlperiode')
            for id, wp_bytes in zip(neue_ids, wp_pipe.execute()):
                protokoll_cache[id] = wp_bytes.decode('utf-8') if wp_bytes else None

        for id_bytes in ids:
            if not id_bytes:
                continue
            wahlperiode = protokoll_cache.get(id_bytes.decode('utf-8'))
            if wahlperiode:
                anzahl_pro_wp[wahlperiode] += 1

        if cursor == 0:
            break

    return anzahl_pro_wp


# Listet alle Woerter auf, deren "zuerst gesehen"-ID auf ein bestimmtes
# Protokoll zeigt. Scannt wie uebersicht_nach_wahlperiode() den kompletten
# Korpus - fuer eine einzelne Datei waehrend eines laufenden Erstaufbaus
# unproblematisch (Lesezugriff, kein Einfluss auf den Build-Prozess).
def woerter_fuer_protokoll(id):
    treffer = []
    cursor = 0

    while True:
        cursor, keys = database.r.scan(cursor=cursor, match='word:*', count=1000)
        ids = _ids_gepipelined(keys)

        for key, id_bytes in zip(keys, ids):
            if id_bytes and id_bytes.decode('utf-8') == id:
                treffer.append(key.decode('utf-8')[len('word:'):])

        if cursor == 0:
            break

    return sorted(treffer)


def _zeige_dwds_vergleich(word):
    try:
        beleg = dwds_api.fruehester_beleg(word)
    except Exception as e:
        print('DWDS-Abfrage fehlgeschlagen ({}: {})'.format(type(e).__name__, e))
        return

    if beleg:
        print('DWDS (Korpus "Bundestagsprotokolle"): fruehester Beleg am {} - {} ({} Treffer insgesamt)'.format(
            beleg['datum'], beleg['quelle'], beleg['anzahl_treffer_gesamt']))
        if beleg['url']:
            print('  ', beleg['url'])
    else:
        print('DWDS (Korpus "Bundestagsprotokolle"): kein Beleg gefunden.')


def main():
    zeige_dwds = '--dwds' in sys.argv
    if zeige_dwds:
        sys.argv.remove('--dwds')

    if len(sys.argv) > 2 and sys.argv[1] == '--protokoll':
        id = sys.argv[2]
        print('Scanne Korpus nach Woertern mit Quelle Protokoll {} (kann dauern)...'.format(id))
        woerter = woerter_fuer_protokoll(id)
        if woerter:
            print('{} Wort/Woerter zuerst gefunden in Protokoll {}:'.format(len(woerter), id))
            for wort in woerter:
                print(' -', wort)
        else:
            print('Keine Woerter mit erster Fundstelle in Protokoll {} gefunden.'.format(id))
    elif len(sys.argv) > 1:
        word = sys.argv[1]
        herkunft = wort_herkunft(word)
        if herkunft:
            print('"{}" zuerst gefunden in Protokoll {} (WP{}, Sitzung {}, {})'.format(
                word, herkunft['id'], herkunft['wahlperiode'],
                herkunft['protokollnummer'], herkunft['datum']))
        else:
            print('"{}" nicht im Korpus gefunden.'.format(word))

        if zeige_dwds:
            _zeige_dwds_vergleich(word)
    else:
        print('Scanne kompletten Korpus (kann bei grossem Datenbestand dauern)...')
        anzahl_pro_wp = uebersicht_nach_wahlperiode()
        for wp in sorted(anzahl_pro_wp, key=lambda w: int(w) if w.isdigit() else 0):
            print('WP{}: {} Woerter'.format(wp, anzahl_pro_wp[wp]))


if __name__ == '__main__':
    main()
