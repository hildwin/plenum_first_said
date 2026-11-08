import argparse
import collections
import csv
import datetime
import os
import random
import sys

from dotenv import load_dotenv

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/vergleiche_llm.py)
PARSER_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))
sys.path.insert(0, PARSER_DIR)

import llm_classify

# Vergleicht die LLM-Klassifikation (wortart/lemma/lemma_korrekt) eines
# Anbieters (Default: OpenAI) mit den bereits in neue_woerter.csv stehenden
# Werten - also dem bisherigen Anthropic-Ergebnis inkl. aller manuellen
# Korrekturen per korrigiere_klassifikation.py.
#
# Rein lesend: schreibt NUR eine separate Vergleichs-CSV nach
# parser/output/llm_vergleich_<provider>_<zeitstempel>.csv. Kein Redis-Zugriff
# (bewusst kein Import von export.py/database.py), kein merke_lemma(), keine
# Aenderung an neue_woerter.csv/.db - kann also gefahrlos parallel zum
# Live-Betrieb laufen. Kostet nur API-Aufrufe beim gewaehlten Anbieter.
#
# Hinweis zur Fairness: Die Referenzwerte stammen teils aus aelteren
# Prompt-Versionen und sind teils von Hand korrigiert. Fuer einen reinen
# Modellvergleich mit dem AKTUELLEN Prompt denselben Lauf zusaetzlich mit
# --provider anthropic machen (gleicher --seed -> gleiche Stichprobe).
#
# Beispiele:
#   uv run python utilities/vergleiche_llm.py --anzahl 100
#   uv run python utilities/vergleiche_llm.py --protokoll 21004
#   uv run python utilities/vergleiche_llm.py --anzahl 100 --provider anthropic

CSV_PATH = os.path.join(PARSER_DIR, 'output', 'neue_woerter.csv')

VERGLEICH_FELDER = [
    'protokoll_id', 'wort', 'satz',
    'wortart_ref', 'wortart_neu',
    'lemma_ref', 'lemma_neu',
    'lemma_korrekt_ref', 'lemma_korrekt_neu',
    'abweichung',
]


def _bool(wert):
    return str(wert).strip().lower() in ('1', 'true', 'wahr')


def _lade_referenz(csv_path, protokoll_ids, anzahl, seed):
    with open(csv_path, newline='', encoding='utf-8') as f:
        zeilen = [z for z in csv.DictReader(f) if z.get('wortart')]

    if protokoll_ids:
        zeilen = [z for z in zeilen if z['protokoll_id'] in protokoll_ids]

    if anzahl and len(zeilen) > anzahl:
        zeilen = random.Random(seed).sample(zeilen, anzahl)

    return zeilen


def _abweichungen(ref, neu):
    if neu is None:
        return ['fehlt']

    abw = []
    if ref['wortart'] != neu['wortart']:
        abw.append('wortart')
    if ref['lemma'] != neu['lemma']:
        # Nur Gross-/Kleinschreibung verschieden separat markieren - bei
        # Adjektiv/Nomen-Grenzfaellen oft die eigentliche Ursache.
        abw.append('lemma_case' if ref['lemma'].lower() == neu['lemma'].lower() else 'lemma')
    if _bool(ref['lemma_korrekt']) != bool(neu['lemma_korrekt']):
        abw.append('lemma_korrekt')
    return abw


def main():
    parser = argparse.ArgumentParser(description='LLM-Klassifikation gegen neue_woerter.csv vergleichen (rein lesend).')
    parser.add_argument('--provider', default='openai', choices=['openai', 'anthropic'])
    parser.add_argument('--anzahl', type=int, default=100, help='Stichprobengroesse (0 = alle passenden Zeilen)')
    parser.add_argument('--protokoll', action='append', help='nur diese protokoll_id (mehrfach moeglich)')
    parser.add_argument('--seed', type=int, default=42, help='Zufallsseed fuer reproduzierbare Stichprobe')
    parser.add_argument('--csv', default=CSV_PATH, help='Referenz-CSV (Default: output/neue_woerter.csv)')
    args = parser.parse_args()

    load_dotenv()
    # Erst NACH load_dotenv setzen, damit --provider Vorrang vor LLM_PROVIDER
    # aus der .env hat (llm_classify liest das erst zur Laufzeit).
    os.environ['LLM_PROVIDER'] = args.provider

    referenz = _lade_referenz(args.csv, args.protokoll, args.anzahl, args.seed)
    if not referenz:
        print('Keine passenden Zeilen mit wortart in', args.csv)
        return

    modell = llm_classify._openai_model() if args.provider == 'openai' else llm_classify.MODEL
    print(f'Klassifiziere {len(referenz)} Woerter mit {args.provider} ({modell}) ...')

    ergebnis = llm_classify.classify_words([{'word': z['wort'], 'satz': z['satz']} for z in referenz])

    zeitstempel = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(PARSER_DIR, 'output', f'llm_vergleich_{args.provider}_{zeitstempel}.csv')

    zaehler = collections.Counter()
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=VERGLEICH_FELDER)
        writer.writeheader()
        for i, ref in enumerate(referenz):
            neu = ergebnis.get(i)
            abw = _abweichungen(ref, neu)
            zaehler.update(abw)
            if not abw:
                zaehler['gleich'] += 1
            writer.writerow({
                'protokoll_id': ref['protokoll_id'],
                'wort': ref['wort'],
                'satz': ref['satz'],
                'wortart_ref': ref['wortart'],
                'wortart_neu': neu['wortart'] if neu else '',
                'lemma_ref': ref['lemma'],
                'lemma_neu': neu['lemma'] if neu else '',
                'lemma_korrekt_ref': int(_bool(ref['lemma_korrekt'])),
                'lemma_korrekt_neu': int(neu['lemma_korrekt']) if neu else '',
                'abweichung': ','.join(abw),
            })

    gesamt = len(referenz)
    print(f'\nErgebnis ({gesamt} Woerter):')
    for schluessel in ('gleich', 'wortart', 'lemma', 'lemma_case', 'lemma_korrekt', 'fehlt'):
        print(f'  {schluessel:<14} {zaehler[schluessel]:>5}  ({zaehler[schluessel] / gesamt:.0%})')
    print(f'\nDetails: {out_path}')


if __name__ == '__main__':
    main()
