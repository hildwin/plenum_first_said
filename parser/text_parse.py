from ctypes import sizeof
import logging
import re
from string import punctuation
import xml_processing
import difflib
import export
import llm_classify
from database import check_newness, ist_bekannter_name, ist_lemma_bekannt, merke_lemma, ist_wort_bekannt

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

    regex_url = r'(http|ftp|https|http)://([\w_-]+(?:(?:\.[\w_-]+)+))([\w.,@?^=%&:/~+#-]*[\w@?^=%&/~+#-])?'
    text = re.sub(regex_url, '', text) # URL-Filter

    # Satzzeichen werden durch Leerzeichen ersetzt. "-" und "--" (Bindestrich)
    # bleiben bewusst aussen vor (Komposita wie "Deutsch-Franzoesisch" sollen
    # erhalten bleiben) - Geviertstrich, Aufzaehlungspunkt, invertiertes
    # Ausrufezeichen und Guillemets (»«) sind dagegen mit aufgenommen, da sie
    # im Deutschen nie Teil eines Wortes sind, sondern bei alten digitalisierten
    # Protokollen gelegentlich ohne Leerzeichen am Wort kleben (z.B. "zitieren"
    # + Gedankenstrich, Aufzaehlungspunkt + "entfallen", "¡" + "Wiedervereinigung",
    # "Debatte" + "»").
    punctuation = r"""#"!$%&'()*+,‚.":;<=>?@[\]^_`{|}~“”„‘’ʼ—•¡»«°…"""
    for character in punctuation:
        text = text.replace(character, ' ')
    text = text.replace(u'\xa0', u' ') # Sonderzeichen entfernen
    # Weicher Trennstrich (U+00AD, unsichtbar ausser bei Zeilenumbruch) hat
    # DREI verschiedene Bedeutungen je nach Position:
    # 1. Zwischen einem Buchstaben und einem Grossbuchstaben, OHNE bereits
    #    vorhandenen echten Bindestrich davor (z.B. "Karl\xadHeinz"): Der
    #    Zeilenumbruch lag genau an der Stelle eines ECHTEN Bindestrichs bei
    #    hyphenierten Doppelnamen/Komposita ("Karl-Heinz", "NATO-Staaten") -
    #    dort auf "-" abbilden, sonst verschmelzen die Teile faelschlich zu
    #    "KarlHeinz". Steht schon ein echter Bindestrich davor (z.B.
    #    "Hans-\xadDietrich"), greift stattdessen Fall 2 (einfach entfernen),
    #    sonst entstuende ein doppelter Bindestrich.
    # 2. Sonst MITTEN im Wort (gefolgt von einem Nicht-Leerzeichen): beide
    #    Haelften stehen bereits da, nur zusammenfuegen ("Alters\xadentlastung"
    #    -> "Altersentlastung").
    # 3. AM WORTENDE (gefolgt von Leerzeichen/Textende) fehlt dagegen die
    #    Fortsetzung - das ist eine Silbentrennung mit verlorener zweiter
    #    Haelfte (z.B. "ausver\xad" aus "ausverkauft" o.ae., zweite Haelfte in
    #    einem anderen Satz/Absatz). Dort NICHT einfach entfernen (sonst
    #    entsteht ein falsches, abgeschnittenes Wort wie "ausver"), sondern
    #    auf den normalen Bindestrich "-" abbilden - die bestehende Rand-
    #    Bindestrich-Erkennung (wordsfilter()/clean_word_parts()) verwirft/
    #    markiert das Fragment dann wie gewohnt.
    text = re.sub(r'(?<=[A-Za-zÄÖÜäöüß])\xad(?=[A-ZÄÖÜ])', '-', text)
    text = re.sub(r'\xad(?=\S)', '', text)
    text = text.replace('\xad', '-')
    # Geschuetzter Bindestrich (U+2011) verhaelt sich in jeder Hinsicht wie ein
    # normaler Bindestrich (nur ohne Zeilenumbruch) - auf "-" normalisieren,
    # statt ihn separat wie einen eigenen Zeichentyp zu behandeln. Verhindert
    # auch, dass z.B. "x-fachen" (normaler Bindestrich) und "x‑fachen"
    # (U+2011) als zwei verschiedene Woerter im Korpus landen.
    text = text.replace('‑', '-')
    text = text.replace('  ', ' ') # Doppelze Leerzeichen zu einfachen.

    return text

# Zeichen, bei denen ein historischer Korpus-Eintrag mit mehreren Ergebnis-
# Teilen (siehe clean_word_parts()) sicher in einzelne Woerter aufgesplittet
# werden darf - gegen ~260 Stichproben aus dem historischen Bestand ohne
# Gegenbeispiel verifiziert (Bahnstrecken wie "Koeln—Frankfurt", Gegensaetze
# wie "Bund—Laender", Gedankenstrich-Sprechpausen wie "ist—nicht"). Bewusst
# NICHT die Ellipse "…" (nur 3 Belege gefunden, davon "Im…tenz" ein klares
# Gegenbeispiel - dort ersetzt "…" einen fehlenden Buchstaben, kein Trenner
# zwischen zwei echten Woertern) und NICHT das Apostroph-artige "‘" (z.B.
# "wer‘s", "d‘Arc" - Kontraktion/Fremdwort, kein zweites eigenstaendiges
# Wort). Fuer alles ausserhalb dieser Liste bleibt ein Mehrfach-Ergebnis
# mehrdeutig und wird nicht automatisch aufgesplittet.
HARTE_TRENNER = ('—',)


# Bereinigt ein EINZELNES, bereits im Korpus gespeichertes Wort um dieselben
# Zeichen-Artefakte, die die laufende Pipeline mittlerweile abfaengt, und
# liefert das Ergebnis als Liste (0, 1 oder mehrere Woerter) - fuer den
# rueckwirkenden Bereinigungs-Lauf ueber den historischen Korpus-Bestand
# (siehe utilities/bereinige_korpus_zeichen.py). Anders als wordsfilter()
# (das Woerter mit Rand-Bindestrich komplett verwirft) wird ein Bindestrich
# am WORTANFANG abgeschnitten und der Rest behalten - ein rueckwirkend
# geloeschter Eintrag waere sonst der einzige Beleg fuer dieses Wort zu
# diesem Zeitpunkt und wuerde das "zuerst gesagt"-Datum verfaelschen. Ein
# Bindestrich am WORTENDE wird dagegen NICHT abgeschnitten+behalten (anders
# als zunaechst umgesetzt) - er kann eine echte Silbentrennung mit
# verlorener zweiter Haelfte anzeigen (z.B. "ausver-" aus "ausverkauft",
# siehe pre_split_clean()) und wuerde sonst ein falsches, abgeschnittenes
# Wort im Korpus erzeugen. Ein verbleibender Bindestrich am Wortende macht
# daher das GESAMTE Ergebnis mehrdeutig (leere Liste), auch wenn andere
# Teile eines Mehrfach-Splits fuer sich genommen unproblematisch waeren.
#
# Enthaelt "word" keinen der HARTE_TRENNER-Zeichen, bleibt die bisherige
# strenge 1-Teil-oder-nichts-Regel bestehen (leere Liste bei Mehrdeutigkeit)
# - sicherer Default fuer alle noch nicht explizit geprueften Trennzeichen.
def clean_word_parts(word):
    cleaned = pre_split_clean(word)
    rohteile = cleaned.split()

    if not any(z in word for z in HARTE_TRENNER) and len(rohteile) != 1:
        return []

    teile = []
    for teil in rohteile:
        teil = re.sub(r'^[-–]+', '', teil)

        if re.search(r'[-–]$', teil):
            return []

        if teil:
            teile.append(teil)

    return teile


# Wie clean_word_parts(), aber fuer den Fall, dass genau EIN Ergebnis-Wort
# erwartet wird. Rueckgabe None bei Mehrdeutigkeit (leer oder mehrteilig).
def clean_word(word):
    teile = clean_word_parts(word)
    return teile[0] if len(teile) == 1 else None

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


# pruefe_neuheit ist austauschbar (Default: check_word, mutierend - schreibt
# den Korpus fort) - z.B. utilities/nacherfassung_wp21.py nutzt stattdessen
# eine rein lesende Pruefung, da der Korpus fuer bereits verarbeitete
# Wahlperioden laengst vollstaendig ist und ein erneuter check_newness()-
# Aufruf dort immer "schon bekannt" melden wuerde.
def wordsfilter(words, id, pruefe_neuheit=None):
    new_words = []

    # check_word ist erst weiter unten in dieser Datei definiert - als
    # Default-Argument direkt eingetragen wuerde es beim Modul-Import
    # (Zeitpunkt der def-Auswertung) noch nicht existieren, daher Lookup
    # zur Laufzeit statt als Default-Parameterwert.
    if pruefe_neuheit is None:
        pruefe_neuheit = check_word

    # Wort hat nur Buchstaben
    regchar = re.compile(r'([A-Z])|([a-z])\w+')

    for word in words:
        if regchar.search(word):

            # Enfernen von sonst nicht filterbaren Aufzählungen
            if word.endswith('-,') or word.endswith('-') or word.endswith('–') or word.startswith('-') or word.startswith('–'):
                continue

            if pruefe_neuheit(word, id):
                new_words.append(word)
        
    return new_words

# Absätze in Sätze splitten (einfache Heuristik mit punktueller
# Abkürzungserkennung - siehe ABKUERZUNGEN_OHNE_SATZENDE unten - reicht aus,
# um ein neues Wort im vollständigen Satzkontext zu zeigen, ohne eine volle
# NLP-Satzgrenzenerkennung zu benoetigen).
# (?<!\d\.) verhindert das Trennen an Ordnungszahl-Abkuerzungen wie "21."
# (z.B. "des 21. Deutschen Bundestages") - ohne diese Ausnahme schnitt die
# Heuristik den Satz genau an dieser Stelle ab, obwohl er erkennbar
# weiterging. Kompromiss: ein Satz, der zufaellig mit einer blossen Zahl
# endet (z.B. "...im Jahr 2024."), wird dadurch faelschlich mit dem
# naechsten Satz zusammengefuehrt statt korrekt getrennt - unkritisch (mehr
# statt weniger Kontext), anders als das bisherige Abschneiden mitten im Satz.
SATZ_ENDE = re.compile(r'(?<!\d\.)(?<=[.!?])\s+')

# Bekannte Abkuerzungen, nach deren Punkt KEIN Satzende folgt - vor allem
# Anrede-/Titel-Abkuerzungen vor Namen, sehr haeufig in Bundestagsprotokollen
# (z.B. "(Dr. Johannes Fechner [SPD]: ...)" bei Zwischenrufen). Da ein
# Lookbehind in SATZ_ENDE (anders als bei der Ordnungszahl-Ausnahme oben)
# nicht mehrere unterschiedlich lange Abkuerzungen gleichzeitig abdecken
# kann, werden faelschlich getrennte Fragmente stattdessen in split_saetze()
# nachtraeglich wieder zusammengefuegt.
ABKUERZUNGEN_OHNE_SATZENDE = frozenset({
    'Dr', 'Prof', 'Nr', 'Abs', 'Art', 'bzw', 'ca', 'usw', 'etc', 'vgl',
    'sog', 'Str', 'Mio', 'Mrd', 'Hr', 'Fr',
})

_ABKUERZUNG_AM_ENDE = re.compile(r'([A-Za-zÄÖÜäöüß]+)\.$')


def split_saetze(text):
    rohteile = [satz for satz in SATZ_ENDE.split(text.strip()) if satz]

    saetze = []
    for teil in rohteile:
        if saetze:
            match = _ABKUERZUNG_AM_ENDE.search(saetze[-1])
            if match and match.group(1) in ABKUERZUNGEN_OHNE_SATZENDE:
                saetze[-1] = saetze[-1] + ' ' + teil
                continue

        saetze.append(teil)

    return saetze


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


# Aussortieren von Wörtern und Export der Überlebenden (CSV + DB).
# merke_lemma_fn/export_fn sind fuer den Live-Betrieb austauschbar, damit
# z.B. utilities/nacherfassung_wp21.py (rueckwirkender Testlauf) dieselbe
# Filter-/Klassifikationslogik nutzen kann, ohne die produktiven lemma:*-Keys
# oder neue_woerter.csv/.db anzufassen - per Default exakt das bisherige
# Verhalten.
def prune(new_words, id, merke_lemma_fn=merke_lemma, export_fn=export.append_row):

    pruned_entries = find_matches(new_words)
    kandidaten = []

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

        kandidaten.append(entry)

    if not kandidaten:
        return

    # LLM-basierte Wortart/Lemma-Klassifikation: laeuft NACH allen obigen Filtern,
    # VOR dem Export. Filtert NICHT nach Wortart (Verben/Adjektive/etc. gehoeren
    # genauso auf die Liste wie Nomen, da auch der Korpus alle Wortarten trackt)
    # - dient nur der Lemma-Deduplizierung pro Wortart, um kuenftige Frachtausgleich/
    # -s-artige Dopplungen zu verhindern. Bei jedem Fehler (Netzwerk, Rate-Limit,
    # Refusal, kaputtes JSON) wird NICHT der ganze Tages-Export verworfen - nur
    # der Lemma-Abgleich entfaellt, siehe _klassifiziere_kandidaten().
    klassifikation = _klassifiziere_kandidaten(kandidaten)

    for i, entry in enumerate(kandidaten):
        if klassifikation is not None:
            ergebnis = klassifikation.get(i)
            if ergebnis is not None:
                wortart = ergebnis['wortart']
                lemma = ergebnis['lemma']
                # ist_wort_bekannt() faengt den Fall ab, dass das Lemma selbst
                # (unabhaengig von der lemma:*-Prospektiv-Tracking) schon
                # lange als eigener word:*-Eintrag existiert (z.B.
                # "einknicken" seit 1990, waehrend "einknickend" gerade erst
                # zum ersten Mal auftaucht) - lemma:* allein wuerde das nicht
                # erkennen, da es erst seit Einfuehrung von Option A befuellt
                # wird. Bei Treffer NICHT merke_lemma_fn() aufrufen (waere
                # eine falsche "zuerst gesehen"-ID fuer ein Wort, das laengst
                # bekannt ist).
                if ist_lemma_bekannt(wortart, lemma) or ist_wort_bekannt(lemma):
                    continue

                merke_lemma_fn(wortart, lemma, id)
                # Nur informativ fuer export_fn (z.B. zur Qualitaetspruefung
                # in nacherfassung_wp21.py) - export.append_row() im
                # Live-Betrieb liest diese Felder nicht, ignoriert sie also.
                entry['wortart'] = wortart
                entry['lemma'] = lemma
            # ergebnis is None (Wort fehlte in der LLM-Antwort) -> konservativ
            # exportieren statt stillschweigend zu verwerfen.

        export_fn(entry, id)


def _klassifiziere_kandidaten(kandidaten):
    try:
        return llm_classify.classify_words(kandidaten)
    except Exception as e:
        logging.warning(
            'LLM-Klassifikation fehlgeschlagen (%s: %s) - Fallback: Export ohne '
            'Lemma-Abgleich fuer %d Kandidat(en).',
            type(e).__name__, e, len(kandidaten))
        return None



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
