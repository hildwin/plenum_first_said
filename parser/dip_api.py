import logging
from dotenv import load_dotenv
import os
import json
import xml.etree.ElementTree as ET
from database import r
import datetime
from api_functions import get_url_content


load_dotenv()


# API Key aus dem Environment - wird nur gebraucht, wenn find_new_doc() tatsaechlich
# live gegen die DIP-API abfragt, daher kein Fehlschlag schon beim Import (dieses
# Modul wird auch von rein lokalen Offline-Skripten importiert, die den Key nie nutzen).
api_key = os.environ.get('BUNDESTAG_API_KEY')


def add_protokoll(response):

    parameters = ['dokumentnummer', 'fundstelle', 'id', 'wahlperiode', 'datum', 'titel']

    # Erst checken ob es ein Protokoll des Bundestags ist und dann, ob es einen Text hat. 

    document_data = json.loads(response.text)
    if document_data['herausgeber'] == 'BT':

        if 'text' in document_data:
            if len(document_data['text']) > 2:
                
                # Datenbankeintrag für das Protokoll erstellen

                redis_id = 'protokoll:' + document_data['id']

                pipe = r.pipeline()

                for parameter in parameters:
                    if parameter in document_data:
                        if parameter == 'fundstelle':
                            if 'pdf_url' in document_data[parameter]:
                                pipe.hset(redis_id, 'pdf_url', document_data[parameter]['pdf_url'])
                        elif parameter == 'datum':
                            pipe.hset(redis_id, parameter, datetime.datetime.strptime(document_data[parameter], '%Y-%m-%d').strftime('%d.%m.%Y'))
                        elif parameter == 'dokumentnummer':
                            pipe.hset(redis_id, 'dokumentnummer', document_data['dokumentnummer'])
                            pipe.hset(redis_id, 'protokollnummer', document_data[parameter].split('/')[1])
                        else:
                            pipe.hset(redis_id, parameter, document_data[parameter])

                # Fallback, wenn die DIP-API-JSON-Antwort selbst kein 'datum'
                # liefert (beobachtet bei sehr aktuellen/laufenden WP21-
                # Sitzungen: Text bereits verfuegbar, Metadaten-Feld 'datum'
                # noch nicht nachgezogen). Das reichhaltige XML-Dokument
                # (fundstelle.xml_url) hat das Sitzungsdatum zuverlaessig
                # schon als Root-Attribut 'sitzung-datum', bereits im Format
                # TT.MM.JJJJ (keine Konvertierung noetig, anders als beim
                # ISO-Datum oben aus der JSON-Antwort). Dupliziert bewusst nur
                # den Attribut-Zugriff statt xml_processing.
                # get_protokoll_metadata() zu importieren - xml_processing.py
                # importiert bereits von dip_api.py, ein Ruecksimport wuerde
                # einen Zirkelbezug erzeugen.
                if 'datum' not in document_data:
                    datum = _datum_aus_xml(document_data.get('fundstelle', {}).get('xml_url'))
                    if datum:
                        pipe.hset(redis_id, 'datum', datum)
                    else:
                        logging.warning(
                            'Kein Datum in JSON-Antwort UND XML-Fallback fuer Protokoll %s gefunden.',
                            document_data['id'])

                try:
                    pipe.execute()
                    return True
                except Exception as e:
                    logging.exception(e)
                    raise
            else:
                logging.info('Dokument mit ID ' + document_data['id'] + ' hat keinen Text')
                return False
        else:
            logging.info('Kein Text gefunden')
            return False
    else:
        logging.info('Dokument mit ID ' + document_data['id'] + ' ist kein Bundestagsprotokoll')
        return False


# Laedt xml_url herunter und liefert das Sitzungsdatum aus dem Root-Attribut
# 'sitzung-datum' (neues XML-Format, bereits TT.MM.JJJJ). None bei fehlender
# URL, Netzwerkfehler, kaputtem XML oder fehlendem Attribut - der Aufrufer
# behandelt das als "kein Fallback moeglich", nicht als harten Fehler.
def _datum_aus_xml(xml_url):
    if not xml_url:
        return None

    response = get_url_content(xml_url)
    if not response or response.status_code != 200:
        return None

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        return None

    return root.attrib.get('sitzung-datum') or None


def find_new_doc(id):

    if not api_key:
        raise RuntimeError('BUNDESTAG_API_KEY ist nicht gesetzt (.env pruefen)')

    for x in range(id, id + 20):

        url = 'https://search.dip.bundestag.de/api/v1/plenarprotokoll-text/' + str(x) + '?apikey=' + api_key
        
        response = get_url_content(url)

        if response and response.status_code == 200:
            if add_protokoll(response):
                logging.info('Neue Sitzung mit Text gefunden unter der URL ' + url)
                return x
        else:
            logging.debug('Response für ID ' + str(x) + ' war nicht gültig.')

    
    return False 