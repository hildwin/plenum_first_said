from ctypes import sizeof
import logging
import re
from string import punctuation
import xml_processing
import difflib
import export
from database import check_newness, ist_bekannter_name

# Beginn des Dokumentes finden mit Rechtschreibfehlern. 
def find_beginn(text):

    # Nur der eindeutige "Beginn:"-Marker (mit Doppelpunkt) zaehlt. Das bloße
    # Wort "Beginn" kommt in normalen Reden vor (z.B. "am Beginn unserer
    # Arbeit ...") und wuerde dort faelschlich als Struktur-Marker erkannt -
    # das schneidet dann echten Inhalt vor der eigentlichen Fundstelle ab.
    # Wird "Beginn:" nicht gefunden, wird der komplette Text behalten statt
    # zu raten (text[-1:] waere sonst nur das letzte Zeichen, nicht "nichts
    # gefunden" - siehe Git-Historie).
    index = text.find('Beginn:')

    if index == -1:
        return text

    return text[index:]

# Silbentrennung rückgängig machen. 
def dehyphenate(text):

    lines = text.split('\n')
    for num, line in enumerate(lines):
        if line.endswith('-'):
            # Keine naechste Zeile vorhanden, oder naechste Zeile ist leer ->
            # nichts zum Zusammenfuegen da, unveraendert lassen.
            if num + 1 >= len(lines) or not lines[num+1].split():
                continue
            try:
                # the end of the word is at the start of next line
                end = lines[num+1].split()[0]
                # we remove the - and append the end of the word
                lines[num] = line[:-1] + end
                # and remove the end of the word and possibly the
                # following space from the next line
                lines[num+1] = lines[num+1][len(end)+1:]
            except Exception as e:
                logging.exception(e)
                logging.info('Line 1: ' + lines[num])
                continue

    return '\n'.join(lines)

# Cleaning vor dem Wordsplitting
def pre_split_clean(text):

    regex_url = '(http|ftp|https|http)://([\w_-]+(?:(?:\.[\w_-]+)+))([\w.,@?^=%&:/~+#-]*[\w@?^=%&/~+#-])?'
    text = re.sub(regex_url, '', text) # URL-Filter

    # Satzzeichen werden durch Leerzeichen ersetzt. "-" und "--" (Bindestrich)
    # bleiben bewusst aussen vor (Komposita wie "Deutsch-Franzoesisch" sollen
    # erhalten bleiben) - der Halbgeviertstrich ist dagegen mit aufgenommen,
    # da er im Deutschen nie fuer Komposita genutzt wird, sondern als
    # Gedankenstrich, der bei alten digitalisierten Protokollen gelegentlich
    # ohne Leerzeichen am Wort klebt (z.B. "zitieren" + Gedankenstrich).
    punctuation = r"""#"!$%&'()*+,‚.":;<=>?@[\]^_`{|}~“”„’ʼ—"""
    for character in punctuation:
        text = text.replace(character, ' ')
    text = text.replace(u'\xa0', u' ') # Sonderzeichen entfernen
    text = text.replace('  ', ' ') # Doppelze Leerzeichen zu einfachen. 
   
    return text

# Wörter splitten am Leerzeichen
def wordsplitter(text):
    words = []

    try:
        words = text.split()

    except Exception as e:
        logging.exception(e)
        exit()
    
    return words

# Wenn Aufzählung, werden die nächsten zwei Worte entfernt.
def de_enumaration(words):

    clean_words = []
    skip = 0

    for word in words:
        if skip > 0:
            skip -= 1
            continue
        
        if word.endswith('-') or word.endswith('–'):
            skip = 2
        else:
            clean_words.append(word)
    
    return clean_words


def wordsfilter(words, id):  
    new_words = []
    
    # Wort hat nur Buchstaben
    regchar = re.compile('([A-Z])|([a-z])\w+')

    for word in words:
        if regchar.search(word):

            # Enfernen von sonst nicht filterbaren Aufzählungen
            if word.endswith('-,') or word.endswith('-') or word.endswith('–') or word.startswith('-'):
                continue     

            if check_word(word, id):
                new_words.append(word)
        
    return new_words

# Absätze in Sätze splitten (einfache Heuristik ohne Abkürzungserkennung -
# reicht aus, um ein neues Wort im vollständigen Satzkontext zu zeigen).
SATZ_ENDE = re.compile(r'(?<=[.!?])\s+')

def split_saetze(text):
    return [satz for satz in SATZ_ENDE.split(text.strip()) if satz]


# Verarbeitet die strukturierten Redebeiträge (neues Protokollformat):
# jeder gefundene neue Wort-Treffer trägt Satzkontext und Sprecherzuordnung.
def process_redebeitraege(redebeitraege, id):

    new_words = []

    for beitrag in redebeitraege:
        for satz in split_saetze(beitrag['text']):
            text = pre_split_clean(satz)
            text = dehyphenate(text)
            words = wordsplitter(text)
            words = de_enumaration(words)

            for word in wordsfilter(words, id):
                new_words.append({
                    'word': word,
                    'satz': satz,
                    'sprecher_typ': beitrag['typ'],
                    'sprecher': beitrag['sprecher'],
                    'fraktion': beitrag['fraktion'],
                    'ist_zwischenfrage': beitrag['ist_zwischenfrage'],
                })

    return new_words


# Hauptfunktion des Moduls für die Aufbereitung und Trennung der Wörter
def process_woerter (xml_file, id):

    redebeitraege = xml_processing.get_redebeitraege(xml_file)

    if redebeitraege:
        return process_redebeitraege(redebeitraege, id)

    # Fallback für das alte Protokollformat (relevant beim Korpus-Erstaufbau
    # historischer Protokolle ohne <sitzungsverlauf>-Struktur) - liefert
    # keinen Satzkontext/keine Sprecherzuordnung.
    raw_text = xml_processing.getText(xml_file)

    if not raw_text:
        return False

    text = find_beginn(raw_text)
    text = pre_split_clean(text)
    text = dehyphenate(text)

    words = wordsplitter(text)
    words = de_enumaration(words)

    return [
        {'word': word, 'satz': None, 'sprecher_typ': None, 'sprecher': None,
         'fraktion': None, 'ist_zwischenfrage': False}
        for word in wordsfilter(words, id)
    ]


# Check ob es ein valides Wort ist
def ok_word(word):

    # Wort hat gleiche Zeichen mehrmals hintereinander
    regmul = re.compile(r'([A-Za-z])\1{4,}')
    # Wort hat nicht nur am Anfang Großbuchstaben
    regsmall = re.compile('[A-Za-z]{1}[a-z]*[A-Z]+[a-z]*')

    if regmul.search(word) or regsmall.search(word):
        return False

    return (not any(i.isdigit() or i in '(.@/#_§ ' for i in word))

# Normalisiert das Wort, überprüft ob es schon im Speicher ist und fügt es der Queue hinzu
def check_word(word, id):

    if ok_word(word):
        if check_newness(word, id):
            return True
        else:
            return False
    else:
        return False

# Gaengige deutsche Funktionswoerter (Artikel, Praepositionen, Konjunktionen,
# Pronomen, Hilfs-/Modalverben, Fuellpartikeln) - fuer den Export uninteressant,
# auch wenn sie zufaellig zum ersten Mal in exakt dieser Form auftauchen.
FUELLWOERTER = frozenset({
    # Artikel
    'der', 'die', 'das', 'den', 'dem', 'des', 'ein', 'eine', 'einen', 'einem', 'einer', 'eines',
    # Praepositionen
    'in', 'an', 'auf', 'mit', 'fuer', 'für', 'von', 'zu', 'bei', 'nach', 'über', 'unter', 'vor',
    'hinter', 'neben', 'zwischen', 'durch', 'gegen', 'ohne', 'um', 'bis', 'aus', 'seit',
    'während', 'wegen', 'trotz', 'statt', 'außer', 'innerhalb', 'außerhalb', 'entlang',
    'gemäß', 'laut', 'dank',
    # Konjunktionen
    'und', 'oder', 'aber', 'doch', 'sondern', 'denn', 'weil', 'dass', 'wenn', 'als', 'obwohl',
    'bevor', 'nachdem', 'damit', 'sodass', 'sowie', 'wie',
    # Pronomen
    'ich', 'du', 'er', 'sie', 'es', 'wir', 'ihr', 'mich', 'dich', 'ihn', 'uns', 'euch', 'ihnen',
    'mein', 'dein', 'sein', 'unser', 'euer', 'dieser', 'jener', 'welcher', 'man', 'etwas',
    'nichts', 'jemand', 'niemand',
    # Hilfs-/Modalverben (konjugiert)
    'ist', 'sind', 'war', 'waren', 'bin', 'bist', 'seid', 'hat', 'haben', 'hatte', 'hatten',
    'wird', 'werden', 'wurde', 'wurden', 'kann', 'können', 'konnte', 'muss', 'müssen', 'musste',
    'soll', 'sollen', 'sollte', 'will', 'wollen', 'wollte', 'mag', 'mögen', 'darf', 'dürfen',
    # Fuellwoerter/Partikeln
    'eben', 'halt', 'mal', 'schon', 'noch', 'nur', 'sehr', 'sogar', 'eigentlich', 'wirklich',
    'natürlich', 'überhaupt', 'wohl', 'etwa', 'ohnehin',
    # Haeufige Adverbien
    'hier', 'dort', 'jetzt', 'heute', 'morgen', 'gestern', 'immer', 'manchmal', 'mehr', 'weniger',
})


# Aussortieren von Wörtern und Export der Überlebenden (CSV + DB)
def prune(new_words, id):

    pruned_entries = find_matches(new_words)

    # Entfernt Kompositionen, die eine Silbentrennung in der Mitte der Zeile sein könnten.
    for entry in pruned_entries:
        wort = entry['word']
        regcomp = re.compile('[a-z]+[-–][a-z]+')

        if regcomp.search(wort):
            continue

        if wort.lower() in FUELLWOERTER:
            continue

        # Schwelle bewusst niedrig (3): FUELLWOERTER faengt die eigentlich
        # uninteressanten kurzen Woerter (Artikel, Praepositionen usw.) schon
        # separat ab, daher muss die Laenge nicht mehr diese Aufgabe
        # mituebernehmen. Komplett grossgeschriebene Woerter (Abkuerzungen
        # wie "DDR", "NATO") sind ohnehin von der Pruefung ausgenommen.
        if len(wort) < 3 and not wort.isupper():
            continue

        # Namen von Abgeordneten sind zwar "neu", aber kein interessantes
        # neues Wort - nur die Ausgabe wird bereinigt, der Korpus (word:*)
        # bleibt unveraendert.
        if ist_bekannter_name(wort):
            continue

        export.append_row(entry, id)



# Entfernt aehnliche Wortformen aus der Liste (z.B. Tippfehler-Varianten).
# Iterativ statt rekursiv: nach jeder Entfernung wird von vorne neu gescannt,
# bis sich nichts mehr aendert - vermeidet, waehrend der Iteration ueber
# "entries" gleichzeitig Eintraege daraus zu entfernen.
#
# Kurze Woerter (< 6 Zeichen) werden von dieser Pruefung ausgenommen: bei
# kurzen Strings fuehrt schon 1 Buchstabe Unterschied zu einem hohen
# difflib-Aehnlichkeitswert, wodurch voellig unterschiedliche Woerter
# faelschlich als Tippfehler-Variante voneinander gelten wuerden
# (z.B. "Art"/"Ort"/"Amt", "Mai"/"Maß").
def find_matches(entries):
    kurz = [e for e in entries if len(e['word']) < 6]
    lang = [e for e in entries if len(e['word']) >= 6]

    aenderung = True

    while aenderung:
        aenderung = False
        woerter = [entry['word'] for entry in lang]

        for entry in lang:
            matches = difflib.get_close_matches(entry['word'], woerter, n=4)

            if matches and len(matches) > 1:
                zu_entfernen = {match for match in matches if match != entry['word']}
                lang[:] = [e for e in lang if e['word'] not in zu_entfernen]
                aenderung = True
                break

    return kurz + lang

if __name__ == "__main__":
    file = '#'
    root = xml_processing.parse(file)
    text = xml_processing.getText(root)
    text = find_beginn(text)
    text = dehyphenate(text)
    text = pre_split_clean(text)
    words = wordsplitter(text)
