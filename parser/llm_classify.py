import logging
import os
import json

import anthropic
from pydantic import BaseModel
from typing import List, Literal

MODEL = 'claude-haiku-4-5'  # guenstig, fuer diese gebundene Klassifikationsaufgabe ausreichend;
                            # bei Qualitaetsproblemen (z.B. Faelle wie Altwohnungsmieten/-mieter)
                            # einzeiliger Wechsel auf 'claude-sonnet-5'
MAX_TOKENS = 8192

_client = None

Wortart = Literal['Nomen', 'Verb', 'Adjektiv', 'Adverb', 'Sonstiges']


class WortKlassifikation(BaseModel):
    index: int
    wortart: Wortart
    lemma: str


class KlassifikationsAntwort(BaseModel):
    words: List[WortKlassifikation]


SYSTEM_PROMPT = (
    "Du bist ein Linguistik-Assistent fuer deutsche Bundestagsprotokolle. Du bekommst "
    "eine Liste deutscher Woerter, jedes mit dem Satz, in dem es vorkam, sowie Sprecher/"
    "Fraktion. Fuer jedes Wort bestimmst du:\n"
    "1. wortart: 'Nomen', 'Verb', 'Adjektiv', 'Adverb' oder 'Sonstiges' - die tatsaechliche "
    "Wortart im Satzkontext. Gross-/Kleinschreibung im Originaltext ist NICHT zuverlaessig "
    "(Behoerdentexte, OCR-Fehler, Satzanfang) - entscheide anhand der Funktion im Satz, "
    "nicht anhand der Schreibweise.\n"
    "2. lemma: Die Grundform (Nominativ Singular bei Nomen, Infinitiv bei Verben, Positiv "
    "bei Adjektiven/Adverbien). Das Lemma MUSS morphologisch aus dem Wort selbst ableitbar "
    "sein (gleicher Wortstamm) - erfinde niemals ein unabhaengiges, nur thematisch "
    "verwandtes Wort als Lemma, auch wenn es im Satz naheliegend erscheint (Beispiel fuer "
    "einen Fehler: 'Neue' in 'Neue Autobahnen wollt ihr bauen!' ist ein flektiertes "
    "Adjektiv zu 'Autobahnen' - richtig waere wortart='Adjektiv', lemma='neu'; 'Auto' waere "
    "falsch, da es nicht der Wortstamm von 'Neue' ist).\n\n"
    "WICHTIG fuer lemma: echtes morphologisches/semantisches Verstaendnis, keine reine "
    "Endungs-Heuristik. Beispiel fuer FALSCHE Gleichsetzung: 'Altwohnungsmieten' (Plural "
    "von 'Altwohnungsmiete', die Zahlung) und 'Altwohnungsmieter' (die Person) sind ZWEI "
    "verschiedene Lemmata, kein Genitiv/Plural voneinander, obwohl die Oberflaechenform "
    "sehr aehnlich ist. Beispiel fuer RICHTIGE Gleichsetzung: 'Frachtausgleichs' (Genitiv) "
    "und 'Frachtausgleich' (Grundform) haben beide das Lemma 'Frachtausgleich'.\n\n"
    "Antworte fuer JEDES Wort aus der Eingabeliste per index, auch bei Unsicherheit "
    "(dann nach bestem Wissen). Erfinde keine zusaetzlichen Woerter/Indizes."
)


def _get_client():
    global _client
    if _client is None:
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            raise RuntimeError('ANTHROPIC_API_KEY ist nicht gesetzt (.env pruefen)')
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


# entries: Liste von Dicts mit 'word', 'satz', 'sprecher', 'fraktion'.
# Rueckgabe: Dict {index: {'wortart': str, 'lemma': str}} fuer jeden erfolgreich
# klassifizierten Index. Fehlende Indizes = "nicht klassifiziert", vom Aufrufer
# konservativ (exportieren, ohne Lemma-Abgleich) zu behandeln. Wirft bei jedem
# nicht verwertbaren Ergebnis (API-Fehler, Refusal, kaputtes JSON) eine Exception
# - der Aufrufer faengt das ab und faellt zurueck auf Export ohne Lemma-Abgleich.
def classify_words(entries):
    client = _get_client()

    payload_words = [
        {
            'index': i,
            'word': entry['word'],
            'satz': entry.get('satz') or '',
            'sprecher': entry.get('sprecher') or '',
            'fraktion': entry.get('fraktion') or '',
        }
        for i, entry in enumerate(entries)
    ]

    response = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': json.dumps({'words': payload_words}, ensure_ascii=False)}],
        output_format=KlassifikationsAntwort,
    )

    if response.stop_reason == 'refusal':
        raise RuntimeError('Anthropic-Antwort refused')

    parsed = response.parsed_output

    result = {}
    for item in parsed.words:
        if not (0 <= item.index < len(entries)) or item.index in result:
            logging.debug('LLM-Klassifikation: ungueltiger/doppelter Index %d ignoriert.', item.index)
            continue
        result[item.index] = {
            'wortart': item.wortart,
            'lemma': item.lemma.strip() or entries[item.index]['word'],
        }

    missing = len(entries) - len(result)
    if missing:
        logging.debug('LLM-Klassifikation: %d von %d Woertern fehlten in der Antwort.', missing, len(entries))

    return result
