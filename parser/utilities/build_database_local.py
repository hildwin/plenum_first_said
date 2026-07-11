import os
import sys

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/build_database_local.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

import database
import xml_processing
from text_parse import process_woerter

# Enthaelt manuell von der Open-Data-Seite heruntergeladene Plenarprotokolle
ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', 'archive')

# Merkt sich die zuletzt abgeschlossene Datei in Redis, damit ein Neustart nach
# einem Absturz/Kill nicht wieder komplett bei vorn beginnen muss - Dateinamen
# sind einheitlich 5-stellig gepolstert, alphabetische Sortierung entspricht
# also der numerischen Reihenfolge. Mit --full laesst sich das ignorieren
# (z.B. nach einem Bugfix wie find_beginn(), wo ein kompletter Neudurchlauf
# gewollt ist, um zuvor verpasste Woerter nachtraeglich einzufangen).
PROGRESS_KEY = 'meta:local_build_progress'

files = sorted(f for f in os.listdir(ARCHIVE_DIR) if f.endswith('.xml'))

if '--full' not in sys.argv:
    letzte_datei = database.r.get(PROGRESS_KEY)
    if letzte_datei:
        letzte_datei = letzte_datei.decode('utf-8')
        files = [f for f in files if f > letzte_datei]
        print('Setze fort nach', letzte_datei, '-', len(files), 'Dateien verbleiben.', flush=True)

wordnum = 0

for filename in files:
    filepath = os.path.join(ARCHIVE_DIR, filename)
    xml_file = xml_processing.parse(filepath)
    id = filename[:-4]  # ".xml" abschneiden

    metadata = xml_processing.get_protokoll_metadata(xml_file)
    if metadata:
        database.r.hset('protokoll:' + id, mapping=metadata)

    new_words = process_woerter(xml_file, id) or []
    wordnum += len(new_words)

    print(filename, '->', len(new_words), 'neue Woerter', flush=True)
    database.r.set(PROGRESS_KEY, filename)

print('Fertig.', wordnum, 'neue Woerter insgesamt.', flush=True)
