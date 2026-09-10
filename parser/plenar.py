
# -*- coding: utf-8 -*-
import logging
from dip_api import find_new_doc
from database import r
from text_parse import process_woerter, prune
from dotenv import load_dotenv
import xml_processing

load_dotenv()

def get_current_id():
    # redis-stubs modellieren get() als ggf. Awaitable (Async-Client) - hier kommt
    # aber immer der Sync-Client zum Einsatz, r.get() liefert also bytes|None.
    return int(r.get('meta:id'))  # type: ignore[arg-type]

def increase_current_id(new_id):
    r.set('meta:id', int(new_id) + 1)
    return True

def main():

    old_id = get_current_id()

    logging.info('Starte suche nach Dokument mit ID ' + str(old_id))

    new_id = find_new_doc(old_id)

    if new_id:

        # xml_processing.get() liefert die WP+Sitzungsnummer-ID (z.B. "21090")
        # zurueck, unter der Archiv-Datei und Korpus-Eintraege abgelegt werden -
        # new_id (die DIP-API-interne ID) wird ab hier nur noch fuer
        # increase_current_id() gebraucht (das ist der Suchcursor-Raum von
        # find_new_doc(), ein eigener, unabhaengiger ID-Raum). Siehe
        # xml_processing.get()/setze_meta_id.py fuer den Hintergrund.
        xml_file, protokoll_id = xml_processing.get(new_id)

        if xml_file is not None:
            logging.info('Sitzung mit der ID ' + str(protokoll_id) +  ' gefunden')

            metadata = xml_processing.get_protokoll_metadata(xml_file)
            if metadata:
                r.hset('protokoll:' + protokoll_id, mapping=metadata)

            new_words = process_woerter(xml_file, protokoll_id)
            if len(new_words) == 0:
                logging.debug('Es wurde kein neues Wort hinzugefügt.')
                exit
            else:
                prune(new_words, protokoll_id)
                increase_current_id(new_id)

            logging.info("Es wurden " + str(len(new_words)) + " neue Wörter hinzugefügt.")

        else:
            logging.warning('XML fuer Dokument-ID ' + str(new_id) + ' konnte nicht geholt/geparst werden.')

    else:
        logging.info('Keine neue Sitzung gefunden.')
    
    exit


if __name__ == "__main__":
    import os
    log_file = os.path.join(os.path.dirname(__file__), 'plenarlog.log')
    logging.basicConfig(
        filename=log_file,
        format='%(asctime)s %(levelname)-8s %(message)s',
        level=logging.INFO,
        datefmt='%Y-%m-%d %H:%M:%S')
    logging.info('Starte Plenar-Parser')
    main()
    logging.info('Beende Plenar-Parser')

 