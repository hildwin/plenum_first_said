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
    lemma_korrekt: bool


class KlassifikationsAntwort(BaseModel):
    words: List[WortKlassifikation]


SYSTEM_PROMPT = (
    "Du bist ein Linguistik-Assistent fuer deutsche Bundestagsprotokolle. Du bekommst "
    "eine Liste deutscher Woerter, jedes mit dem Satz, in dem es vorkam. Fuer jedes Wort "
    "bestimmst du:\n"
    "1. wortart: 'Nomen', 'Verb', 'Adjektiv', 'Adverb' oder 'Sonstiges' - die tatsaechliche "
    "Wortart im Satzkontext. Gross-/Kleinschreibung im Originaltext ist NICHT zuverlaessig "
    "(Behoerdentexte, Flüchtigkeitsfehler, Satzanfang) - entscheide anhand der Funktion im Satz, "
    "nicht anhand der Schreibweise.\n"
    "2. lemma: Die morphologische Grundform, gleicher Wortstamm (Nomen -> Nominativ Singular; Verben -> Infinitiv;  "
    "Adjektive -> Positiv/Unflektiert). Erfinde niemals ein unabhaengiges, nur thematisch "
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
    "WICHTIG fuer lemma: IMMER genau EIN einzelnes Wort, NIEMALS mehrere durch Leerzeichen "
    "getrennte Woerter oder eine ganze Wortgruppe/Phrase aus dem Satz. "
    "Beispiel fuer einen Fehler: 'Thesaurierte' in 'Thesaurierte Gewinne' ist ein flektiertes "
    "Adjektiv/Partizip zu 'Gewinne' (wie 'gewaehlte Vertreter') - richtig waere wortart='Adjektiv', "
    "lemma='thesauriert'; wortart='Nomen', lemma='Thesaurierter Gewinn' waere falsch. \n\n"
    "WICHTIG fuer lemma: Ist das Eingabewort nur ein Fragment eines fremdsprachigen "
    "Eigennamens (Orts-/Personenname), z.B. weil der Name im Satz an einem Leerzeichen "
    "oder Apostroph zerlegt wurde, dann NICHT den vollstaendigen mehrteiligen Namen als "
    "Lemma rekonstruieren, sondern wortart='Sonstiges' und lemma = das Eingabewort selbst, "
    "unveraendert. Beispiel: Wort 'Scheva' (aus 'Be'er Scheva') -> wortart='Sonstiges', "
    "lemma='Scheva'; NICHT lemma=\"Be'er Scheva\".\n\n"
    "WICHTIG fuer lemma: Pruefe vor der Antwort die Rechtschreibung deines Lemmas noch "
    "einmal genau (korrektes Deutsch, kein fehlender/vertauschter Buchstabe). Beispiel fuer "
    "einen Fehler: lemma='Klappstul' statt korrekt 'Klappstuhl'.\n\n"
    "WICHTIG fuer lemma: Deutsche Komposita NIEMALS durch ein Leerzeichen mittendrin "
    "zerreissen - das Lemma muss eine einzige zusammenhaengende Zeichenkette sein, auch "
    "wenn es aus mehreren Wortbestandteilen besteht. Beispiel fuer einen Fehler: "
    "lemma='Übergangsberei ch' statt korrekt 'Übergangsbereich' (aus Wort "
    "'Übergangsbereiche'). Dies ist kein Fall von mehreren getrennten Woertern (siehe "
    "oben), sondern ein versehentlich eingefuegtes Leerzeichen innerhalb eines einzigen "
    "Wortes.\n\n"
    "3. lemma_korrekt: Ein Boolean (true/false), der angibt, ob das extrahierte Lemma "
    "orthografisch korrekt im Deutschen existiert.\n\n"
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


# entries: Liste von Dicts mit 'word', 'satz'.
# Rueckgabe: Dict {index: {'wortart': str, 'lemma': str, 'lemma_korrekt': bool}} fuer jeden erfolgreich
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
    if parsed is None:
        raise RuntimeError(f'Anthropic-Antwort nicht parsebar (stop_reason={response.stop_reason!r})')

    result = {}
    for item in parsed.words:
        if not (0 <= item.index < len(entries)) or item.index in result:
            logging.debug('LLM-Klassifikation: ungueltiger/doppelter Index %d ignoriert.', item.index)
            continue

        word = entries[item.index]['word']
        lemma = item.lemma.strip() or word

        # Verteidigung gegen Prompt-Nichteinhaltung: ein Kompositum, das vom
        # LLM durch genau EIN Leerzeichen zerrissen wurde (beobachtet:
        # "Übergangsberei ch"/"Gerichtskosten vorschuss" statt korrekt
        # "Übergangsbereich"/"Gerichtskostenvorschuss"), laesst sich sicher
        # reparieren, wenn die zusammengefuegte Laenge nah an der Laenge des
        # Original-Worts liegt. Bei einer echten Mehrwort-Phrase (z.B. "Be'er
        # Scheva" fuer Wort "Scheva", "Thesaurierter Gewinn" fuer Wort
        # "Thesaurierte") liegt die zusammengefuegte Laenge dagegen weit von
        # der Wortlaenge entfernt - dort greift weiterhin die Ablehnung unten.
        if lemma.count(' ') == 1:
            repariert = lemma.replace(' ', '')
            if abs(len(repariert) - len(word)) <= 3:
                logging.info(
                    'LLM-Klassifikation: Kompositum-Lemma "%s" fuer Wort "%s" repariert zu "%s" (Index %d).',
                    lemma, word, repariert, item.index)
                lemma = repariert

        # Verteidigung gegen Prompt-Nichteinhaltung (beobachtet: "Thesaurierter
        # Gewinn"/"Guest House" statt Einzelwort-Lemma "thesauriert"/"Guest") -
        # ein mehrwortiges Lemma darf nicht in lemma:* landen, unabhaengig
        # davon, ob der Prompt das verhindern soll. Wird wie ein fehlendes
        # Antwort-Item behandelt (konservativ exportieren ohne Lemma-Abgleich).
        if ' ' in lemma:
            logging.warning(
                'LLM-Klassifikation: mehrwortiges Lemma "%s" fuer Wort "%s" verworfen (Index %d).',
                lemma, word, item.index)
            continue

        result[item.index] = {
            'wortart': item.wortart,
            'lemma': lemma,
            'lemma_korrekt': item.lemma_korrekt,
        }

    missing = len(entries) - len(result)
    if missing:
        logging.debug('LLM-Klassifikation: %d von %d Woertern fehlten in der Antwort.', missing, len(entries))

    return result
