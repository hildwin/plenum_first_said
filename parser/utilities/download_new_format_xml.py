import logging
import os
import sys
import time

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/download_new_format_xml.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

from dotenv import load_dotenv

from api_functions import get_url_content

load_dotenv()

# Absoluter Pfad, damit das Log unabhaengig vom Arbeitsverzeichnis beim Aufruf
# (z.B. per Cron/nohup) immer neben dem Skript landet.
LOG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'download_new_format_xml.log')

logging.basicConfig(
    filename=LOG_FILE,
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')

API_KEY = os.environ.get('BUNDESTAG_API_KEY')
if not API_KEY:
    raise RuntimeError('BUNDESTAG_API_KEY ist nicht gesetzt (.env pruefen)')
SEARCH_URL = 'https://search.dip.bundestag.de/api/v1/plenarprotokoll-text'
ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', 'archive')

# Wahlperioden, fuer die die Open-Data-Seite keine ZIP-Sammlung anbietet -
# betrifft nur die Sitzungen mit dem neuen, reich strukturierten XML-Format
# (erkennbar an fundstelle.xml_url); aeltere Sitzungen ohne xml_url werden
# uebersprungen und bleiben Sache des manuellen Downloads.
WAHLPERIODEN = [19, 20, 21]


# Iteriert per Cursor-Pagination ueber alle Dokumente einer Wahlperiode
def iter_dokumente(wahlperiode):

    cursor = None

    while True:
        url = SEARCH_URL + '?f.wahlperiode=' + str(wahlperiode) + '&apikey=' + API_KEY + '&format=json'
        if cursor:
            url += '&cursor=' + cursor

        response = get_url_content(url)

        if not response or response.status_code != 200:
            logging.warning('Suche fuer WP %s fehlgeschlagen (cursor=%s)', wahlperiode, cursor)
            return

        data = response.json()
        dokumente = data.get('documents', [])

        if not dokumente:
            return

        for dokument in dokumente:
            yield dokument

        neuer_cursor = data.get('cursor')
        if not neuer_cursor or neuer_cursor == cursor:
            return

        cursor = neuer_cursor
        time.sleep(1)


# Leitet aus wahlperiode + dokumentnummer den Dateinamen ab, passend zur
# Konvention der Open-Data-Seite (z.B. "21087.xml" fuer WP21, Sitzung 87)
def _dateiname(dokument):

    wahlperiode = dokument.get('wahlperiode')
    dokumentnummer = dokument.get('dokumentnummer', '')
    sitzungsnr = dokumentnummer.split('/')[-1] if '/' in dokumentnummer else dokumentnummer

    try:
        return '{:02d}{:03d}.xml'.format(int(wahlperiode), int(sitzungsnr))
    except (TypeError, ValueError):
        return str(dokument.get('id')) + '.xml'


# Laedt die reich strukturierte XML eines Dokuments herunter, falls verfuegbar.
# Gibt den Dateinamen zurueck, wenn heruntergeladen oder bereits vorhanden war;
# None, wenn kein Bundestagsprotokoll oder kein neues Format verfuegbar ist.
def download_xml(dokument):

    if dokument.get('herausgeber') != 'BT':
        return None

    xml_url = dokument.get('fundstelle', {}).get('xml_url')
    if not xml_url:
        return None

    filename = _dateiname(dokument)
    filepath = os.path.join(ARCHIVE_DIR, filename)

    if os.path.exists(filepath):
        return filename

    response = get_url_content(xml_url)
    if not response or response.status_code != 200:
        logging.warning('Download fehlgeschlagen: %s', xml_url)
        return None

    with open(filepath, 'wb') as f:
        f.write(response.content)

    logging.info('Heruntergeladen: %s', filename)
    return filename


def main():
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    for wahlperiode in WAHLPERIODEN:
        print('--- Wahlperiode', wahlperiode, '---')
        anzahl_neu = 0
        anzahl_uebersprungen = 0

        for i, dokument in enumerate(iter_dokumente(wahlperiode)):
            filename = download_xml(dokument)

            if filename:
                anzahl_neu += 1
                print(' ', filename)
            else:
                anzahl_uebersprungen += 1

            if i % 10 == 0:
                time.sleep(1)

        print('WP', wahlperiode, ':', anzahl_neu, 'heruntergeladen/vorhanden,', anzahl_uebersprungen, 'uebersprungen (kein neues Format oder kein BT-Protokoll)')

    print('Fertig.')


if __name__ == '__main__':
    main()
