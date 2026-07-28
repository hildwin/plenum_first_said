import csv
import os
import sqlite3
import sys

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/korrigiere_klassifikation.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

import export
from database import r, merke_lemma, LEMMA_KEY_PREFIX

# Ermoeglicht die manuelle Nachbearbeitung einzelner Eintraege in
# neue_woerter.db/.csv, wenn die LLM-Klassifikation (llm_classify.py) bei
# wortart/lemma/lemma_korrekt danebenliegt, sowie das Entfernen von
# Eintraegen, deren extrahiertes Wort selbst Muell ist (z.B. Tokenizer-
# Artefakte wie "Scheva" aus "Be'er Scheva").
#
# korrektur.csv (von Hand gepflegt, NUR die zu aendernden Zeilen - kein
# Vollexport aller Eintraege) mit Spalten:
#   protokoll_id, wort  - Lookup-Schluessel, IMMER der AKTUELLE (unveraenderte)
#                         Stand in neue_woerter.db, niemals der Zielwert
#   wortart, lemma, lemma_korrekt - neue Zielwerte, bei einer Korrektur alle
#                         drei Pflicht (lemma_korrekt als true/false/1/0)
#   wort_neu            - optional, nur befuellen wenn der rohe wort-Text
#                         selbst korrigiert werden soll (sonst leer lassen)
#   aktion               - optional, 'loeschen' entfernt die Zeile komplett
#                         statt sie zu korrigieren (wortart/lemma/
#                         lemma_korrekt/wort_neu werden dann ignoriert)
#
# Bei jeder Korrektur, die wortart oder lemma aendert, sowie bei jeder
# Loeschung: der alte lemma:*-Key wird geloescht, bei einer Korrektur der
# neue per merke_lemma() gesetzt - sonst wuerde die kuenftige Dedup-Pruefung
# (ist_lemma_bekannt) weiter auf dem falschen Lemma basieren.
#
# word:*-Keys werden NIE angefasst (auch nicht bei Loeschung) - das ist die
# langfristige "schon gesehen"-Sperre (siehe database.py/check_newness) und
# verhindert, dass exakt derselbe (ggf. kaputte) Wortstring erneut als "neu"
# auftaucht und wieder LLM-Kosten verursacht.
#
# Per Default ein reiner Dry-Run (nur Ausgabe) - erst mit --apply werden
# tatsaechlich DB/CSV/Redis geschrieben.

ERLAUBTE_WORTARTEN = {'Nomen', 'Verb', 'Adjektiv', 'Adverb', 'Sonstiges'}


def _lemma_key(wortart, lemma):
    return LEMMA_KEY_PREFIX + wortart + ':' + lemma


def _parse_lemma_korrekt(wert):
    wert = wert.strip().lower()
    if wert in ('1', 'true', 'wahr', 'ja'):
        return 1
    if wert in ('0', 'false', 'falsch', 'nein'):
        return 0
    return None


# Liest die aktuellen DB-Zeilen zu jeder korrektur.csv-Zeile und ermittelt
# die konkret durchzufuehrende Aktion. Reine Planungsphase, keine
# Schreibzugriffe - dieselbe Liste wird fuer Dry-Run-Ausgabe UND --apply
# verwendet, damit beide garantiert denselben Stand zeigen/anwenden.
def _plane(conn, korrekturen):
    plan = []

    for zeile in korrekturen:
        protokoll_id = zeile['protokoll_id'].strip()
        wort = zeile['wort'].strip()
        aktion = (zeile.get('aktion') or '').strip().lower()

        treffer = conn.execute(
            'SELECT id, wortart, lemma FROM neue_woerter WHERE protokoll_id = ? AND wort = ?',
            (protokoll_id, wort)).fetchall()

        if len(treffer) != 1:
            print('UEBERSPRUNGEN (protokoll_id={}, wort="{}"): {} Treffer in DB, erwartet genau 1.'.format(
                protokoll_id, wort, len(treffer)))
            continue

        db_id, alte_wortart, alte_lemma = treffer[0]

        if aktion == 'loeschen':
            plan.append({
                'aktion': 'loeschen',
                'protokoll_id': protokoll_id, 'wort': wort, 'db_id': db_id,
                'alte_wortart': alte_wortart, 'alte_lemma': alte_lemma,
            })
            continue

        neue_wortart = zeile['wortart'].strip()
        neue_lemma = zeile['lemma'].strip()
        neue_lemma_korrekt = _parse_lemma_korrekt((zeile.get('lemma_korrekt') or ''))
        neues_wort = (zeile.get('wort_neu') or '').strip() or wort

        if not neue_wortart or not neue_lemma or neue_lemma_korrekt is None:
            print('UEBERSPRUNGEN (protokoll_id={}, wort="{}"): wortart/lemma/lemma_korrekt fehlen oder '
                  'lemma_korrekt nicht als true/false erkennbar (kein aktion=loeschen).'.format(
                      protokoll_id, wort))
            continue

        if neue_wortart not in ERLAUBTE_WORTARTEN:
            print('UEBERSPRUNGEN (protokoll_id={}, wort="{}"): unbekannte wortart "{}" (erlaubt: {}).'.format(
                protokoll_id, wort, neue_wortart, ', '.join(sorted(ERLAUBTE_WORTARTEN))))
            continue

        plan.append({
            'aktion': 'korrigieren',
            'protokoll_id': protokoll_id, 'wort': wort, 'db_id': db_id,
            'alte_wortart': alte_wortart, 'alte_lemma': alte_lemma,
            'neues_wort': neues_wort, 'neue_wortart': neue_wortart,
            'neue_lemma': neue_lemma, 'neue_lemma_korrekt': neue_lemma_korrekt,
        })

    return plan


def _drucke_plan(plan):
    for eintrag in plan:
        if eintrag['aktion'] == 'loeschen':
            print('LOESCHEN: protokoll_id={protokoll_id}, wort="{wort}" (wortart={alte_wortart}, '
                  'lemma={alte_lemma})'.format(**eintrag))
        else:
            print('KORRIGIEREN: protokoll_id={protokoll_id}, wort="{wort}" -> wort="{neues_wort}", '
                  'wortart={alte_wortart}->{neue_wortart}, lemma={alte_lemma}->{neue_lemma}, '
                  'lemma_korrekt={neue_lemma_korrekt}'.format(**eintrag))


def _wende_auf_db_und_redis_an(conn, plan):
    for eintrag in plan:
        if eintrag['aktion'] == 'loeschen':
            conn.execute('DELETE FROM neue_woerter WHERE id = ?', (eintrag['db_id'],))
            if eintrag['alte_wortart'] and eintrag['alte_lemma']:
                r.delete(_lemma_key(eintrag['alte_wortart'], eintrag['alte_lemma']))
            continue

        if (eintrag['neue_wortart'], eintrag['neue_lemma']) != (eintrag['alte_wortart'], eintrag['alte_lemma']):
            if eintrag['alte_wortart'] and eintrag['alte_lemma']:
                r.delete(_lemma_key(eintrag['alte_wortart'], eintrag['alte_lemma']))
            merke_lemma(eintrag['neue_wortart'], eintrag['neue_lemma'], eintrag['protokoll_id'])

        conn.execute(
            'UPDATE neue_woerter SET wort = ?, wortart = ?, lemma = ?, lemma_korrekt = ? WHERE id = ?',
            (eintrag['neues_wort'], eintrag['neue_wortart'], eintrag['neue_lemma'],
             eintrag['neue_lemma_korrekt'], eintrag['db_id']))

    conn.commit()


def _wende_auf_csv_an(plan):
    aktionen = {(e['protokoll_id'], e['wort']): e for e in plan}

    with open(export.CSV_PATH, newline='', encoding='utf-8') as f:
        zeilen = list(csv.DictReader(f))

    neue_zeilen = []
    gefunden = set()
    for zeile in zeilen:
        schluessel = (zeile['protokoll_id'], zeile['wort'])
        eintrag = aktionen.get(schluessel)
        if eintrag is None:
            neue_zeilen.append(zeile)
            continue

        gefunden.add(schluessel)
        if eintrag['aktion'] == 'loeschen':
            continue

        zeile['wort'] = eintrag['neues_wort']
        zeile['wortart'] = eintrag['neue_wortart']
        zeile['lemma'] = eintrag['neue_lemma']
        zeile['lemma_korrekt'] = eintrag['neue_lemma_korrekt']
        neue_zeilen.append(zeile)

    for protokoll_id, wort in set(aktionen) - gefunden:
        print('WARNUNG: protokoll_id={}, wort="{}" in DB gefunden, aber nicht in neue_woerter.csv - '
              'CSV nicht aktualisiert.'.format(protokoll_id, wort))

    with open(export.CSV_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=export.CSV_FELDER)
        writer.writeheader()
        writer.writerows(neue_zeilen)


def main():
    apply = '--apply' in sys.argv
    pfad = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'korrektur.csv')
    for arg in sys.argv[1:]:
        if not arg.startswith('--'):
            pfad = arg
            break

    if not os.path.exists(pfad):
        print('Korrektur-CSV nicht gefunden:', pfad)
        return

    with open(pfad, newline='', encoding='utf-8') as f:
        korrekturen = list(csv.DictReader(f))

    if not apply:
        print('Dry-Run (keine Schreibzugriffe). Mit --apply tatsaechlich anwenden.')
        print()

    with sqlite3.connect(export.DB_PATH) as conn:
        plan = _plane(conn, korrekturen)
        _drucke_plan(plan)

        if not plan:
            return

        if apply:
            _wende_auf_db_und_redis_an(conn, plan)
            _wende_auf_csv_an(plan)
            print()
            print('{} Aenderung(en) angewendet.'.format(len(plan)))


if __name__ == '__main__':
    main()
