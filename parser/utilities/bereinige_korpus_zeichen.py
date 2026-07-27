import os
import sys

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/bereinige_korpus_zeichen.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

import database
from text_parse import clean_word_parts

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
#   - clean_word_parts() liefert genau EIN Ergebnis-Wort, das noch nicht
#     existiert -> umbenennen.
#   - clean_word_parts() liefert genau EIN Ergebnis-Wort, das schon
#     existiert -> die chronologisch AELTERE der beiden IDs gewinnt
#     (Vergleich wie check_age() in database.py).
#   - clean_word_parts() liefert MEHRERE Ergebnis-Woerter (z.B. historische
#     Bahnstrecken-/Gegensatzpaare wie "Koeln—Frankfurt" von vor dem
#     urspruenglichen Geviertstrich-Fix) -> jedes Ergebnis-Wort wird
#     einzeln wie oben behandelt (umbenennen/mergen), der urspruengliche
#     Mehrfach-Key anschliessend geloescht.
#   - clean_word_parts() liefert eine leere Liste (Ergebnis leer, oder
#     mehrdeutiger Mehrfach-Fund ausserhalb der als sicher geltenden
#     Trennzeichen, z.B. Apostroph-Kontraktionen oder echte OCR-Buchstaben-
#     luecken) -> nicht automatisch verarbeitet, nur zur manuellen Pruefung
#     aufgelistet.
#
# Per Default ein reiner Dry-Run (nur Ausgabe, keine Schreibzugriffe) -
# erst mit --apply werden tatsaechlich HSET/DELETE ausgefuehrt.


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


# Sorgt dafuer, dass zielwort im Korpus existiert und die chronologisch
# aelteste bekannte ID traegt. Rueckgabe 'umbenannt' (Zielwort war neu) oder
# 'gemergt' (Zielwort existierte schon).
def _uebernehme_in(quellwort, dirty_id, zielwort, apply):
    ziel_key = 'word:' + zielwort
    ziel_id_bytes = database.r.hget(ziel_key, 'id')

    if ziel_id_bytes is None:
        print('UMBENENNEN: "{}" -> "{}" (id={})'.format(quellwort, zielwort, dirty_id))
        if apply:
            database.r.hset(ziel_key, 'word', zielwort)
            database.r.hset(ziel_key, 'id', dirty_id)
        return 'umbenannt'

    ziel_id = ziel_id_bytes.decode('utf-8')
    gewinner_id = _aeltere_id(ziel_id, dirty_id) if dirty_id else ziel_id

    print('MERGE: "{}" (id={}) + "{}" (id={}) -> "{}" behaelt id={}'.format(
        quellwort, dirty_id, zielwort, ziel_id, zielwort, gewinner_id))

    if apply and gewinner_id != ziel_id:
        database.r.hset(ziel_key, 'id', gewinner_id)

    return 'gemergt'


def bereinige(apply=False):
    cursor = 0
    unveraendert = 0
    umbenannt = 0
    gemergt = 0
    manuelle_pruefung = []
    gescannt = 0
    batch_nr = 0

    while True:
        cursor, keys = database.r.scan(cursor=cursor, match='word:*', count=1000)
        batch_nr += 1
        gescannt += len(keys)

        # Status-Print statt \r-Fortschrittsbalken, damit ein per nohup
        # umgeleitetes Log per "tail -f" sauber lesbar bleibt (siehe README/
        # STATUS.md zur Begruendung gegen \r in Log-Dateien).
        if batch_nr % 50 == 0:
            print('... {} Keys gescannt (umbenannt={}, gemergt={})'.format(
                gescannt, umbenannt, gemergt), flush=True)

        for key in keys:
            key_str = key.decode('utf-8')
            word = key_str[len('word:'):]
            teile = clean_word_parts(word)

            if len(teile) == 1 and teile[0] == word:
                unveraendert += 1
                continue

            if not teile:
                manuelle_pruefung.append(word)
                continue

            dirty_id_bytes = database.r.hget(key_str, 'id')
            dirty_id = dirty_id_bytes.decode('utf-8') if dirty_id_bytes else None

            for zielwort in teile:
                ergebnis = _uebernehme_in(word, dirty_id, zielwort, apply)
                if ergebnis == 'umbenannt':
                    umbenannt += 1
                else:
                    gemergt += 1

            if apply:
                database.r.delete(key_str)

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
