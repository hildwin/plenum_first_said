import csv
import os
import sys

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/nacherfassung_wp21.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

import database
import xml_processing
import text_parse

# Holt rueckwirkend nach, was build_database_local.py beim Erstaufbau fuer
# WP21 verworfen hat: Die pro Datei tatsaechlich neu gefundenen Woerter
# (inkl. Satzkontext/Sprecherzuordnung) wurden dort nur gezaehlt, nie
# exportiert oder durch die LLM-Wortart/Lemma-Klassifikation geschickt.
#
# WICHTIG: Der Korpus (word:*) ist fuer WP21 laengst vollstaendig - ein
# erneuter check_newness()-Aufruf wuerde daher fuer JEDES Wort "schon
# bekannt" melden (da es ja bereits beim Erstaufbau eingetragen wurde),
# nicht "neu genau in dieser Sitzung". Stattdessen wird rein lesend
# geprueft, ob die im Korpus hinterlegte "zuerst gesehen"-ID exakt der
# aktuell verarbeiteten Datei entspricht - nur dann war DIESE Sitzung
# tatsaechlich die erste Fundstelle.
#
# Testphase (siehe STATUS.md): Export in eine separate CSV statt der
# produktiven neue_woerter.csv/.db, und merke_lemma() wird NICHT aufgerufen
# (kein Schreibzugriff auf die von plenar.py mitgenutzten lemma:*-Keys),
# bis die Strategie an einer Stichprobe geprueft und bestaetigt ist. Erst
# danach auf export.append_row/merke_lemma (echte Ziele) umstellen und auf
# alle WP21-Dateien ausweiten.

ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', 'archive')
TEST_CSV = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', 'output', 'neue_woerter_wp21_test.csv')
TEST_CSV_FELDER = ['protokoll_id', 'wort', 'wortart', 'lemma', 'satz', 'sprecher_typ', 'sprecher', 'fraktion', 'ist_zwischenfrage']


def war_zuerst_hier(word, id):
    if not text_parse.ok_word(word):
        return False

    gespeicherte_id = database.r.hget('word:' + word, 'id')
    if not gespeicherte_id:
        return False

    return gespeicherte_id.decode('utf-8') == str(id)


def _kein_lemma_merken(wortart, lemma, id):
    pass  # Testphase: lemma:*-Keys bewusst nicht anfassen


def _test_export(entry, id):
    os.makedirs(os.path.dirname(TEST_CSV), exist_ok=True)
    ist_neu = not os.path.exists(TEST_CSV)

    with open(TEST_CSV, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=TEST_CSV_FELDER)
        if ist_neu:
            writer.writeheader()
        writer.writerow({
            'protokoll_id': str(id),
            'wort': entry['word'],
            # Nur gefuellt, wenn die LLM-Klassifikation fuer dieses Wort
            # erfolgreich war (siehe prune() in text_parse.py) - sonst leer,
            # NICHT das Fehlen einer Klassifikation vortaeuschen.
            'wortart': entry.get('wortart', ''),
            'lemma': entry.get('lemma', ''),
            'satz': entry.get('satz'),
            'sprecher_typ': entry.get('sprecher_typ'),
            'sprecher': entry.get('sprecher'),
            'fraktion': entry.get('fraktion'),
            'ist_zwischenfrage': int(bool(entry.get('ist_zwischenfrage'))),
        })

    return True


def sammle_neue_woerter(filename):
    filepath = os.path.join(ARCHIVE_DIR, filename)
    id = filename[:-4]  # ".xml" abschneiden

    xml_file = xml_processing.parse(filepath)
    redebeitraege = xml_processing.get_redebeitraege(xml_file)

    # Im Live-Betrieb schreibt check_newness() das Wort sofort beim ersten
    # Auftreten in den Korpus, wodurch jedes weitere Auftreten desselben
    # Wortes in derselben Sitzung automatisch als "schon bekannt" erkannt
    # wird - war_zuerst_hier() ist rein lesend und hat diesen Selbst-
    # unterdrueckungs-Effekt nicht. bereits_gesehen gleicht das lokal (nur
    # fuer diese eine Datei) aus, damit ein Wort hoechstens einmal pro
    # Sitzung als "neu" gezaehlt wird - genau wie im Original.
    bereits_gesehen = set()

    new_words = []
    for beitrag in redebeitraege:
        for satz in text_parse.split_saetze(beitrag['text']):
            text = text_parse.pre_split_clean(satz)
            text = text_parse.dehyphenate(text)
            words = text_parse.wordsplitter(text)
            words = text_parse.de_enumaration(words)

            for word in text_parse.wordsfilter(words, id, pruefe_neuheit=war_zuerst_hier):
                if word in bereits_gesehen:
                    continue
                bereits_gesehen.add(word)

                new_words.append({
                    'word': word,
                    'satz': satz,
                    'sprecher_typ': beitrag['typ'],
                    'sprecher': beitrag['sprecher'],
                    'fraktion': beitrag['fraktion'],
                    'ist_zwischenfrage': beitrag['ist_zwischenfrage'],
                })

    return id, new_words


# Erlaubt --start <sitzungsnummer>, um den Testlauf an einer beliebigen
# WP21-Sitzung zu beginnen, statt immer bei 21001 anzufangen. Sicher, weil
# war_zuerst_hier() rein lesend gegen den bereits vollstaendigen Korpus
# prueft - die Verarbeitungsreihenfolge der Testdateien spielt dafuer keine
# Rolle (anders als beim urspruenglichen Erstaufbau, der lediglich fuer den
# Lemma-Dedupe innerhalb desselben Testlaufs relevant waere, die ist in der
# Testphase aber ohnehin per _kein_lemma_merken() deaktiviert).
# Akzeptiert sowohl die reine Sitzungsnummer ("50") als auch die volle
# Dateiname-Schreibweise mit WP-Praefix ("21050"), da beides eine
# naheliegende Eingabe ist.
def _sitzungsnummer(wert):
    wert = str(wert)
    if wert.startswith('21') and len(wert) > 3:
        wert = wert[2:]
    return int(wert)


def _parse_args(argv):
    argv = list(argv)
    start_sitzung = None

    if '--start' in argv:
        idx = argv.index('--start')
        start_sitzung = _sitzungsnummer(argv[idx + 1])
        del argv[idx:idx + 2]

    anzahl_dateien = int(argv[0]) if argv else 3

    return start_sitzung, anzahl_dateien


def main():
    start_sitzung, anzahl_dateien = _parse_args(sys.argv[1:])

    dateien = sorted(f for f in os.listdir(ARCHIVE_DIR) if f.startswith('21') and f.endswith('.xml'))

    if start_sitzung is not None:
        start_dateiname = '21{:03d}.xml'.format(start_sitzung)
        dateien = [f for f in dateien if f >= start_dateiname]

    dateien = dateien[:anzahl_dateien]
    print('Testlauf ueber', len(dateien), 'Datei(en):', dateien)
    print('Export-Ziel (Testphase):', TEST_CSV)
    print()

    for filename in dateien:
        id, new_words = sammle_neue_woerter(filename)
        print(filename, '->', len(new_words), 'tatsaechlich neue Woerter (vor Lemma-Dedupe)')

        text_parse.prune(new_words, id, merke_lemma_fn=_kein_lemma_merken, export_fn=_test_export)

    print()
    print('Fertig. Ergebnis in', TEST_CSV, 'zur Durchsicht.')


if __name__ == '__main__':
    main()
