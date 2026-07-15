from urllib.parse import quote

from api_functions import get_url_content

BASE_URL = 'https://ddc.dwds.de/dstar/bundestagprt/dstar.perl'
# Kleiner Puffer statt limit=1: gibt bei mehreren Treffern im selben
# (fruehesten) Dokument zusaetzlichen Kontext, ohne die Anfrage aufzublaehen.
LIMIT = 5


# Fragt das DWDS-Korpus "Bundestagsprotokolle" (dstar/bundestagprt) nach dem
# fruehesten Beleg eines Lemmas ab - unabhaengig vom eigenen Korpus, als
# externer Vergleichswert. Rueckgabe None bei keinem Treffer; wirft bei
# Netzwerk-/Parsing-Fehlern (Aufrufer faengt ab und zeigt eine Fehlermeldung,
# statt abzustuerzen).
#
# Die dstar.perl-API liefert Treffer standardmaessig bereits chronologisch
# aufsteigend sortiert (gegen mehrere Stichproben verifiziert, u.a. mit einem
# Lemma mit >1900 Treffern) - ein expliziter sort=-Parameter ist daher nicht
# noetig, der erste Treffer ist der fruehste Beleg.
def fruehester_beleg(lemma):
    query = quote('$l=@' + lemma)
    url = '{}?q={}&fmt=json&start=1&limit={}&ctx=0'.format(BASE_URL, query, LIMIT)

    response = get_url_content(url)
    if not response or response.status_code != 200:
        raise RuntimeError('DWDS-Abfrage fehlgeschlagen (Status {})'.format(
            response.status_code if response else 'kein Response'))

    data = response.json()
    hits = data.get('hits_') or []
    if not hits:
        return None

    meta = hits[0].get('meta_', {})

    return {
        'datum': meta.get('date_', ''),
        'quelle': meta.get('biblSig') or meta.get('bibl', ''),
        'url': meta.get('url', ''),
        'anzahl_treffer_gesamt': data.get('nhits_', len(hits)),
    }
