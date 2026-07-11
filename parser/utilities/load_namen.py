import io
import logging
import os
import sys
import xml.etree.ElementTree as ET
import zipfile

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/load_namen.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

import database
from api_functions import get_url_content

logging.basicConfig(
    filename=os.path.join(os.path.dirname(os.path.realpath(__file__)), 'load_namen.log'),
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')

# MDB_STAMMDATEN.XML liegt standardmaessig im Projekt-Root (dort, wo sie von
# der Open-Data-Seite abgelegt wurde). Pfad bei Bedarf per Argument ueberschreiben.
STANDARD_PFAD = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', '..', 'MDB_STAMMDATEN.XML')

# Stand 2026 - die Blob-ID kann sich bei einer Neuveroeffentlichung durch den
# Bundestag aendern (kein garantiert stabiler Link). Bei Download-Fehlern
# zuerst pruefen, ob sich die URL auf bundestag.de/services/opendata geaendert hat.
STAMMDATEN_URL = 'https://www.bundestag.de/resource/blob/472878/MdB-Stammdaten.zip'


# Extrahiert Namensbestandteile aus einem geparsten Stammdaten-Baum (inkl.
# Namenshistorie bei Namensaenderungen). Vornamen werden zusaetzlich in ihre
# Einzelteile zerlegt (z.B. "Swantje Henrike" -> auch "Swantje"/"Henrike"
# einzeln), da wordsplitter() an Leerzeichen trennt; Bindestrich-Namen
# (z.B. "Schmitt-Vockenhausen", "Hans-Peter") bleiben als ein Token erhalten.
def _namen_aus_baum(root):
    namen = set()

    for name in root.iter('NAME'):
        nachname = name.findtext('NACHNAME')
        vorname = name.findtext('VORNAME')

        if nachname:
            namen.add(nachname.strip())

        if vorname:
            vorname = vorname.strip()
            namen.add(vorname)
            for teil in vorname.split():
                namen.add(teil)

    return namen


# Liest die Stammdaten aus einer lokalen XML-Datei (z.B. manuell von der
# Open-Data-Seite heruntergeladen).
def lade_namen(pfad=STANDARD_PFAD):
    tree = ET.parse(pfad)
    return _namen_aus_baum(tree.getroot())


# Laedt die Stammdaten-ZIP direkt von bundestag.de, extrahiert die darin
# enthaltene XML und liefert die Namensbestandteile. Wirft eine klare
# Exception bei Fehlern (wichtig fuer Cron: lieber laut scheitern, als
# unbemerkt eine veraltete Namensliste zu behalten).
def lade_namen_von_url(url=STAMMDATEN_URL):
    response = get_url_content(url)

    if not response or response.status_code != 200:
        status = response.status_code if response else 'keine Antwort'
        raise RuntimeError(
            'Download der MdB-Stammdaten von {} fehlgeschlagen (HTTP {}). '
            'Moeglicherweise hat sich die URL geaendert - siehe '
            'bundestag.de/services/opendata.'.format(url, status))

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        xml_dateien = [n for n in zf.namelist() if n.upper().endswith('.XML')]
        if not xml_dateien:
            raise ValueError('Keine XML-Datei in der ZIP von {} gefunden.'.format(url))

        with zf.open(xml_dateien[0]) as f:
            tree = ET.parse(f)

    return _namen_aus_baum(tree.getroot())


def _namen_speichern(namen):
    print(len(namen), 'Namensbestandteile gefunden.')

    if namen:
        database.r.sadd(database.NAMEN_SET_KEY, *namen)

    logging.info('%d Namensbestandteile geladen, Redis-Set enthaelt jetzt %d Eintraege.',
                  len(namen), database.r.scard(database.NAMEN_SET_KEY))
    print('Fertig. Redis-Set "{}" enthaelt jetzt {} Eintraege.'.format(
        database.NAMEN_SET_KEY, database.r.scard(database.NAMEN_SET_KEY)))


def main():
    # python load_namen.py            -> Standard-Lokaldatei
    # python load_namen.py <pfad>     -> andere Lokaldatei
    # python load_namen.py --url      -> direkt von bundestag.de herunterladen (fuer Cron)
    try:
        if len(sys.argv) > 1 and sys.argv[1] == '--url':
            print('Lade Stammdaten von', STAMMDATEN_URL, '...')
            namen = lade_namen_von_url()
        else:
            pfad = sys.argv[1] if len(sys.argv) > 1 else STANDARD_PFAD
            print('Lese Stammdaten aus', pfad, '...')
            namen = lade_namen(pfad)

        _namen_speichern(namen)

    except Exception as e:
        logging.exception(e)
        print('FEHLER:', e, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
