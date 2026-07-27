import os
import sys

# Erlaubt den Import von Modulen aus dem parser/-Verzeichnis, wenn dieses
# Skript direkt ausgefuehrt wird (python utilities/setze_meta_id.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

import database
from download_new_format_xml import iter_dokumente, _dateiname

# Bestimmt die korrekte Start-ID fuer plenar.py (meta:id in Redis) nach dem
# archivbasierten Erstaufbau bzw. nach manuellem Nachholen neuer Protokolle.
#
# plenar.py sucht ueber die DIP-API-interne, fortlaufende Dokument-ID (z.B.
# "5807"), waehrend der Korpus (word:*, protokoll:*) intern die WP+
# Sitzungsnummer-ID nutzt (z.B. "21090") - beide sind unabhaengige, nicht
# ineinander umrechenbare ID-Raeume. Dieses Skript findet die DIP-ID des
# zuletzt lokal verarbeiteten Protokolls (per meta:local_build_progress,
# demselben Fortschritts-Marker wie build_database_local.py) und setzt
# meta:id auf diese ID + 1, damit plenar.py ab dem naechsten, wirklich neuen
# Protokoll weitersucht statt bereits Bekanntes erneut zu verarbeiten.
#
# Per Default ein reiner Dry-Run (nur Ausgabe) - erst mit --apply wird
# meta:id tatsaechlich geschrieben.


def _letzte_verarbeitete_datei():
    letzte_datei = database.r.get('meta:local_build_progress')
    if not letzte_datei:
        raise RuntimeError(
            'meta:local_build_progress ist nicht gesetzt - kann die zuletzt '
            'verarbeitete Datei nicht bestimmen.')
    return letzte_datei.decode('utf-8')


# Dateiname-Konvention: {WP:02d}{Sitzungsnummer:03d}.xml (siehe _dateiname()
# in download_new_format_xml.py)
def _wahlperiode_aus_dateiname(dateiname):
    return int(dateiname[:2])


def finde_dip_id(dateiname):
    wahlperiode = _wahlperiode_aus_dateiname(dateiname)

    for dokument in iter_dokumente(wahlperiode):
        if _dateiname(dokument) == dateiname:
            return dokument.get('id')

    return None


def main():
    apply = '--apply' in sys.argv

    dateiname = _letzte_verarbeitete_datei()
    print('Zuletzt lokal verarbeitete Datei (meta:local_build_progress):', dateiname)

    dip_id = finde_dip_id(dateiname)
    if dip_id is None:
        print('Keine passende DIP-ID fuer', dateiname, 'gefunden - Dokument nicht in der '
              'DIP-Suche fuer diese Wahlperiode gefunden (Netzwerkfehler? falsche/abgelaufene '
              'API-Key? Dokument ausserhalb des durchsuchten Zeitraums?). meta:id NICHT geaendert.')
        return

    neue_meta_id = int(dip_id) + 1
    aktuelle_meta_id = database.r.get('meta:id')
    aktuelle_meta_id = aktuelle_meta_id.decode('utf-8') if aktuelle_meta_id else '(nicht gesetzt)'

    print('Gefundene DIP-ID fuer', dateiname, ':', dip_id)
    print('Aktueller meta:id-Wert in Redis:', aktuelle_meta_id)
    print('Neuer meta:id-Wert:', neue_meta_id)

    if apply:
        database.r.set('meta:id', neue_meta_id)
        print('meta:id gesetzt.')
    else:
        print()
        print('Dry-Run - mit --apply tatsaechlich schreiben.')


if __name__ == '__main__':
    main()
