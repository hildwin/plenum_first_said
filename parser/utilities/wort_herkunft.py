import os
import sys
from collections import Counter

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/wort_herkunft.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

import database


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


# Zaehlt, wie viele Woerter pro Wahlperiode zuerst dort auftauchten.
# Scannt alle word:*-Keys - bei einem grossen Korpus spuerbar CPU-intensiv,
# daher am besten ausserhalb eines laufenden Korpus-Aufbaus ausfuehren.
def uebersicht_nach_wahlperiode():
    anzahl_pro_wp = Counter()
    protokoll_cache = {}
    cursor = 0

    while True:
        cursor, keys = database.r.scan(cursor=cursor, match='word:*', count=1000)

        for key in keys:
            id_bytes = database.r.hget(key, 'id')
            if not id_bytes:
                continue
            id = id_bytes.decode('utf-8')

            if id not in protokoll_cache:
                wp_bytes = database.r.hget('protokoll:' + id, 'wahlperiode')
                protokoll_cache[id] = wp_bytes.decode('utf-8') if wp_bytes else None

            wahlperiode = protokoll_cache[id]
            if wahlperiode:
                anzahl_pro_wp[wahlperiode] += 1

        if cursor == 0:
            break

    return anzahl_pro_wp


def main():
    if len(sys.argv) > 1:
        word = sys.argv[1]
        herkunft = wort_herkunft(word)
        if herkunft:
            print('"{}" zuerst gefunden in Protokoll {} (WP{}, Sitzung {}, {})'.format(
                word, herkunft['id'], herkunft['wahlperiode'],
                herkunft['protokollnummer'], herkunft['datum']))
        else:
            print('"{}" nicht im Korpus gefunden.'.format(word))
    else:
        print('Scanne kompletten Korpus (kann bei grossem Datenbestand dauern)...')
        anzahl_pro_wp = uebersicht_nach_wahlperiode()
        for wp in sorted(anzahl_pro_wp, key=lambda w: int(w) if w.isdigit() else 0):
            print('WP{}: {} Woerter'.format(wp, anzahl_pro_wp[wp]))


if __name__ == '__main__':
    main()
