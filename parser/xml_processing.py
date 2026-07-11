import logging
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
import os
from dip_api import get_url_content




load_dotenv()

# API Key aus dem Environment - wird nur fuer den Live-Fetch (_get_metadata) gebraucht,
# daher kein Fehlschlag schon beim Import (dieses Modul wird auch von rein lokalen
# Offline-Skripten importiert, die nie live gegen die DIP-API abfragen).
api_key = os.environ.get('BUNDESTAG_API_KEY')


# Speichert XML ab nach Download
def save(id, current_xml):

    filename = os.path.dirname(os.path.realpath(__file__)) + "/archive/" + str(id) + ".xml"

    with open (filename, 'wb') as file:
        file.write(current_xml.content)
    
    logging.info('XML gespeichert: ' + filename)
    
    return filename

# Metadaten (JSON) zu einer ID holen, um ggf. den echten xml_url zu finden.
# Die API liefert bei format=xml nur einen generischen Flat-Text-Wrapper -
# fundstelle.xml_url zeigt (falls vorhanden) auf die tatsaechliche, reich
# strukturierte Protokoll-XML auf dserver.bundestag.de.
def _get_metadata(id):

    if not api_key:
        raise RuntimeError('BUNDESTAG_API_KEY ist nicht gesetzt (.env pruefen)')

    url = 'https://search.dip.bundestag.de/api/v1/plenarprotokoll-text/' + str(id) + '?apikey=' + api_key

    response = get_url_content(url)

    if response and response.status_code == 200:
        try:
            return response.json()
        except ValueError:
            return None

    return None


# XML Dokument bekommen hinter der ID
def get(id):

    metadata = _get_metadata(id)
    xml_url = metadata.get('fundstelle', {}).get('xml_url') if metadata else None

    if xml_url:
        # Reich strukturierte XML direkt vom Bundestags-Server holen (kein API-Key noetig)
        response = get_url_content(xml_url)
    else:
        # Fallback: kein xml_url bekannt (z.B. aeltere Protokolle) - Flat-Text ueber die API
        url = 'https://search.dip.bundestag.de/api/v1/plenarprotokoll-text/' + str(id) + '?apikey=' + api_key + '&format=xml'
        response = get_url_content(url)

    if response and response.status_code == 200:
        filename = save(id, response)
        return parse(filename)
    else:
        return False


#Parse XML
def parse(filename):

    tree = ET.parse(filename)
    return tree.getroot()


# Liest Wahlperiode/Sitzungsnummer/Datum/Titel direkt aus der XML-Datei -
# funktioniert ohne die JSON-Antwort der DIP-API, z.B. für Protokolle, die
# manuell von der Open-Data-Seite heruntergeladen wurden.
def get_protokoll_metadata(xml_file):

    wahlperiode = xml_file.attrib.get('wahlperiode')

    if wahlperiode:
        # Neues Format: Angaben als Root-Attribute
        sitzung_nr = xml_file.attrib.get('sitzung-nr')
        return {
            'wahlperiode': wahlperiode,
            'protokollnummer': sitzung_nr,
            'datum': xml_file.attrib.get('sitzung-datum'),
            'titel': 'Plenarprotokoll ' + wahlperiode + '/' + str(sitzung_nr),
        }

    # Altes Format: Angaben als Kindelemente in Grossschreibung
    wahlperiode = xml_file.findtext('WAHLPERIODE')
    if not wahlperiode:
        return None

    nr = xml_file.findtext('NR')  # Format "WP/Sitzungsnr", z.B. "19/182"
    protokollnummer = nr.split('/')[1] if nr and '/' in nr else nr

    return {
        'wahlperiode': wahlperiode,
        'protokollnummer': protokollnummer,
        'datum': xml_file.findtext('DATUM'),
        'titel': xml_file.findtext('TITEL'),
    }

#  Auf verschiedene Arten der Formatierung eingehen und als String ausgeben.
def getText(xml_file):

    text_array = []
    klassen = ['J', '1','O', 'J_1', 'T']

    #Checken ob neues Format und Text rausziehen
    for p in xml_file.iter("p"):
        if any(value in p.attrib.values() for value in klassen):
            text_array.append(p.text)

    # Altes Format bekommen
    if not text_array:
        if xml_file.findall('text'):
            text_array.append(xml_file.find('text').text)
        if xml_file.findall('TEXT'):
            text_array.append(xml_file.find('TEXT').text)

    if not text_array:
        return False
    else:
        return ''.join(text_array)


# Absatzklassen, die tatsächlich gesprochenen/vorgelesenen Text enthalten
# (im Gegensatz zu T_*/K, die Tagesordnungs- bzw. Abschnittstitel sind)
REDE_KLASSEN = {'J', 'J_1', 'O', '1', 'Z'}


# Baut Name sowie Fraktion/Rolle aus einem <redner>-Element.
def _redner_info(redner_elem):
    name_elem = redner_elem.find('name')
    if name_elem is None:
        return redner_elem.attrib.get('id'), None, None

    teile = [name_elem.findtext(feld) for feld in ('titel', 'vorname', 'nachname')]
    name = ' '.join(teil for teil in teile if teil)

    fraktion = name_elem.findtext('fraktion')
    if not fraktion:
        rolle_elem = name_elem.find('rolle')
        if rolle_elem is not None:
            fraktion = rolle_elem.findtext('rolle_kurz') or rolle_elem.findtext('rolle_lang')

    if fraktion:
        fraktion = fraktion.replace(u'\xa0', u' ')

    return redner_elem.attrib.get('id'), name or None, fraktion


# Rekursiver Walker über die direkten Kinder eines Containers (sitzungsverlauf,
# tagesordnungspunkt, sitzungsbeginn, sitzungsende, rede). Trackt den aktuell
# sprechenden (Redner oder Präsidium) als Zustand und gibt ihn am Ende zurück,
# damit der Aufrufer mit dem richtigen Stand weitermachen kann.
def _walk_sitzungsverlauf(element, sprecher, haupt_redner_id):

    for child in element:
        tag = child.tag
        klasse = child.attrib.get('klasse')

        if tag == 'name':
            text = (child.text or '').strip().rstrip(':').strip()
            if text:
                sprecher = {'typ': 'Praesidium', 'sprecher': text, 'fraktion': None, 'redner_id': None}

        elif tag == 'p' and klasse == 'redner':
            redner_elem = child.find('redner')
            if redner_elem is not None:
                redner_id, name, fraktion = _redner_info(redner_elem)
                sprecher = {'typ': 'Redner', 'sprecher': name, 'fraktion': fraktion, 'redner_id': redner_id}
                if haupt_redner_id is None:
                    haupt_redner_id = redner_id

        elif tag == 'p' and klasse in REDE_KLASSEN:
            text = ''.join(child.itertext()).strip()
            if text:
                ist_zwischenfrage = bool(
                    sprecher.get('typ') == 'Redner'
                    and haupt_redner_id is not None
                    and sprecher.get('redner_id') != haupt_redner_id
                )
                yield {
                    'typ': sprecher.get('typ'),
                    'sprecher': sprecher.get('sprecher'),
                    'fraktion': sprecher.get('fraktion'),
                    'text': text,
                    'ist_zwischenfrage': ist_zwischenfrage,
                }

        elif tag == 'kommentar':
            text = ''.join(child.itertext()).strip()
            if text:
                yield {
                    'typ': 'Kommentar',
                    'sprecher': None,
                    'fraktion': None,
                    'text': text,
                    'ist_zwischenfrage': False,
                }

        elif tag == 'rede':
            # Neue Rede -> eigener Haupt-Redner, wird beim ersten "redner"-Absatz gesetzt
            sprecher = yield from _walk_sitzungsverlauf(child, sprecher, None)

        elif tag in ('tagesordnungspunkt', 'sitzungsbeginn', 'sitzungsende'):
            sprecher = yield from _walk_sitzungsverlauf(child, sprecher, haupt_redner_id)

    return sprecher


# Liefert pro Absatz/Kommentar ein strukturiertes Record mit Sprecherzuordnung
# (Redner/Praesidium/Kommentar), Fraktion/Rolle, Text und Zwischenfrage-Kennzeichnung.
def get_redebeitraege(xml_file):

    sitzungsverlauf = xml_file.find('sitzungsverlauf')
    if sitzungsverlauf is None:
        return []

    sprecher = {'typ': None, 'sprecher': None, 'fraktion': None, 'redner_id': None}
    return list(_walk_sitzungsverlauf(sitzungsverlauf, sprecher, None))

