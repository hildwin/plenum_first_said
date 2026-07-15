from urllib.parse import quote

from api_functions import get_url_content

BASE_URL = 'https://ddc.dwds.de/dstar/bundestagprt/dstar.perl'
# Kleiner Puffer statt limit=1: gibt bei mehreren Treffern im selben
# (fruehesten) Dokument zusaetzlichen Kontext, ohne die Anfrage aufzublaehen.
LIMIT = 5


# Fragt das DWDS-Korpus "Bundestagsprotokolle" (dstar/bundestagprt) nach dem
# fruehesten Beleg eines Wortes ab - unabhaengig vom eigenen Korpus, als
# externer Vergleichswert. Rueckgabe None bei keinem Treffer; wirft bei
# Netzwerk-/Parsing-Fehlern (Aufrufer faengt ab und zeigt eine Fehlermeldung,
# statt abzustuerzen).
#
# Die dstar.perl-API liefert Treffer standardmaessig bereits chronologisch
# aufsteigend sortiert (gegen mehrere Stichproben verifiziert, u.a. mit einem
# Lemma mit >1900 Treffern) - ein expliziter sort=-Parameter ist daher nicht
# noetig, der erste Treffer ist der fruehste Beleg.
#
# Versucht zuerst eine Lemma-Suche ($l=@wort): findet bei einer echten
# Grundform auch fruehere Flexionsvarianten (z.B. "Frachtausgleich" als Lemma
# findet auch die frueher belegte Genitivform "Frachtausgleichs"). $l= matched
# aber nur, wenn "wort" selbst als Lemma im DWDS-Index steht - bei einer
# Flexionsform (z.B. "Frachtausgleichs", passend zu unserem eigenen Korpus,
# der Oberflaechenformen statt Lemmata trackt) liefert die Lemma-Suche
# faelschlich 0 Treffer, obwohl DWDS die Form kennt. Fallback bei 0 Treffern:
# reine Wortform-Suche (@wort, ohne $l=) - findet dann nur die exakte
# Zeichenkette, nicht die ganze Lemma-Familie, ist aber besser als "kein
# Beleg" zu behaupten, wo DWDS tatsaechlich etwas hat (verifiziert: $l=@
# Frachtausgleichs -> 0 Treffer, @Frachtausgleichs -> 12 Treffer).
def fruehester_beleg(wort):
    ergebnis = _abfrage(wort, als_lemma=True)
    if ergebnis is None:
        ergebnis = _abfrage(wort, als_lemma=False)
    return ergebnis


def _abfrage(wort, als_lemma):
    praefix = '$l=@' if als_lemma else '@'
    query = quote(praefix + wort)
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
        'lemma_suche': als_lemma,
    }
