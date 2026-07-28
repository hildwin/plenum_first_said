import csv
import logging
import os
from difflib import SequenceMatcher

import requests

import export

REVIEW_CSV = os.path.join(export.OUTPUT_DIR, 'rechtschreib_review.csv')
REVIEW_CSV_FELDER = ['protokoll_id', 'wort', 'lemma_llm', 'lemma_vorschlag', 'aehnlichkeit', 'grund', 'satz']

LANGUAGETOOL_URL = 'https://api.languagetool.org/v2/check'
REQUEST_TIMEOUT = 10


# Zweite, unabhaengige Meinung fuer LLM-Ergebnisse mit lemma_korrekt=false
# (siehe llm_classify.py) - fragt den oeffentlichen LanguageTool-Dienst ab
# und vergleicht einen eventuellen Korrekturvorschlag per Zeichen-Aehnlichkeit
# mit dem Original. Ab similarity_threshold gilt der Vorschlag als sichere
# automatische Korrektur (valid=True), sonst als manuell zu pruefen
# (needs_review=True). Netzwerk-/API-Fehler werden NICHT hochgeworfen, sondern
# ebenfalls als needs_review=True behandelt - konservativ, analog zur
# LLM-Klassifikations-Fehlerbehandlung in text_parse.py: ein nicht
# erreichbarer externer Dienst darf nicht den gesamten Tages-Export scheitern
# lassen.
def validate_and_fix_lemma(lemma_from_claude, similarity_threshold=0.85):
    try:
        response = requests.get(
            LANGUAGETOOL_URL,
            params={'text': lemma_from_claude, 'language': 'de-DE'},
            timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as e:
        logging.warning('Rechtschreibpruefung fuer "%s" fehlgeschlagen: %s', lemma_from_claude, e)
        return {
            'original': lemma_from_claude,
            'corrected': None,
            'valid': False,
            'similarity': 0.0,
            'needs_review': True,
            'reason': 'LanguageTool nicht erreichbar/Fehler: {}'.format(e),
        }

    matches = data.get('matches', [])

    if not matches:
        return {
            'original': lemma_from_claude,
            'corrected': lemma_from_claude,
            'valid': True,
            'similarity': 1.0,
            'needs_review': False,
            'reason': 'Kein Fehler gefunden',
        }

    suggestions = matches[0].get('replacements', [])

    if not suggestions:
        return {
            'original': lemma_from_claude,
            'corrected': None,
            'valid': False,
            'similarity': 0.0,
            'needs_review': True,
            'reason': 'Fehler gefunden, aber kein Korrekturvorschlag',
        }

    suggestion = suggestions[0]['value']
    similarity = SequenceMatcher(None, lemma_from_claude, suggestion).ratio()

    if similarity >= similarity_threshold:
        return {
            'original': lemma_from_claude,
            'corrected': suggestion,
            'valid': True,
            'similarity': similarity,
            'needs_review': False,
            'reason': 'Automatisch korrigiert (Aehnlichkeit {:.2f})'.format(similarity),
        }

    return {
        'original': lemma_from_claude,
        'corrected': suggestion,
        'valid': False,
        'similarity': similarity,
        'needs_review': True,
        'reason': 'Geringe Aehnlichkeit ({:.2f}), manuelle Pruefung noetig'.format(similarity),
    }


# Haengt einen Eintrag an rechtschreib_review.csv an - die Warteschlange fuer
# Faelle, die weder das LLM (lemma_korrekt=false) noch LanguageTool sicher
# aufloesen konnten. Dient als Rechercheliste fuer manuelle Korrekturen ueber
# korrigiere_klassifikation.py/korrektur.csv (eigenes, kleineres Format hier:
# Kontext fuer die Pruefung statt Zielwerte fuer die Anwendung).
def log_review(protokoll_id, wort, lemma_llm, pruefung, satz):
    os.makedirs(export.OUTPUT_DIR, exist_ok=True)
    ist_neu = not os.path.exists(REVIEW_CSV)

    with open(REVIEW_CSV, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_CSV_FELDER)
        if ist_neu:
            writer.writeheader()
        writer.writerow({
            'protokoll_id': protokoll_id,
            'wort': wort,
            'lemma_llm': lemma_llm,
            'lemma_vorschlag': pruefung.get('corrected') or '',
            'aehnlichkeit': '{:.2f}'.format(pruefung.get('similarity') or 0.0),
            'grund': pruefung.get('reason', ''),
            'satz': satz or '',
        })
