import os
import sys
import xml.etree.ElementTree as ET

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/load_namen.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

import database

# MDB_STAMMDATEN.XML liegt standardmaessig im Projekt-Root (dort, wo sie von
# der Open-Data-Seite abgelegt wurde). Pfad bei Bedarf per Argument ueberschreiben.
STANDARD_PFAD = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', '..', 'MDB_STAMMDATEN.XML')


# Sammelt alle Vor- und Nachnamen (inkl. Namenshistorie bei Namensaenderungen)
# aus den MdB-Stammdaten. Vornamen werden zusaetzlich in ihre Einzelteile
# zerlegt (z.B. "Swantje Henrike" -> auch "Swantje" und "Henrike" einzeln),
# da wordsplitter() an Leerzeichen trennt; Bindestrich-Namen (z.B.
# "Schmitt-Vockenhausen", "Hans-Peter") bleiben als ein Token erhalten.
def lade_namen(pfad=STANDARD_PFAD):
    tree = ET.parse(pfad)
    root = tree.getroot()

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


def main():
    pfad = sys.argv[1] if len(sys.argv) > 1 else STANDARD_PFAD

    print('Lese Stammdaten aus', pfad, '...')
    namen = lade_namen(pfad)
    print(len(namen), 'Namensbestandteile gefunden.')

    if namen:
        database.r.sadd(database.NAMEN_SET_KEY, *namen)

    print('Fertig. Redis-Set "{}" enthaelt jetzt {} Eintraege.'.format(
        database.NAMEN_SET_KEY, database.r.scard(database.NAMEN_SET_KEY)))


if __name__ == '__main__':
    main()
