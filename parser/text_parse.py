from ctypes import sizeof
import logging
import re
from string import punctuation
import xml_processing
import difflib
import export
from database import check_newness

# Beginn des Dokumentes finden mit Rechtschreibfehlern. 
def find_beginn(text):

    if text.find('Beginn:') == -1:
        text = text[text.find('Beginn'):]
    else:
        text = text[text.find('Beginn'):]
    
    return text

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

    # Satzzeichen werden durch Leerzeichen ersetzt
    punctuation = r"""#"!$%&'()*+,‚.":;<=>?@[\]^_`{|}~“”„’ʼ"""
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

# Aussortieren von Wörtern und Export der Überlebenden (CSV + DB)
def prune(new_words, id):

    pruned_entries = find_matches(new_words)

    # Entfernt Kompositionen, die eine Silbentrennung in der Mitte der Zeile sein könnten.
    for entry in pruned_entries:
        regcomp = re.compile('[a-z]+[-–][a-z]+')
        if regcomp.search(entry['word']) or len(entry['word']) < 5:
            continue
        else:
            export.append_row(entry, id)



# Entfernt aehnliche Wortformen aus der Liste (z.B. Tippfehler-Varianten).
# Iterativ statt rekursiv: nach jeder Entfernung wird von vorne neu gescannt,
# bis sich nichts mehr aendert - vermeidet, waehrend der Iteration ueber
# "entries" gleichzeitig Eintraege daraus zu entfernen.
def find_matches(entries):
    aenderung = True

    while aenderung:
        aenderung = False
        woerter = [entry['word'] for entry in entries]

        for entry in entries:
            matches = difflib.get_close_matches(entry['word'], woerter, n=4)

            if matches and len(matches) > 1:
                zu_entfernen = {match for match in matches if match != entry['word']}
                entries[:] = [e for e in entries if e['word'] not in zu_entfernen]
                aenderung = True
                break

    return entries

if __name__ == "__main__":
    file = '#'
    root = xml_processing.parse(file)
    text = xml_processing.getText(root)
    text = find_beginn(text)
    text = dehyphenate(text)
    text = pre_split_clean(text)
    words = wordsplitter(text)
