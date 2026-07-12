import logging
import os

import redis
from dotenv import load_dotenv

load_dotenv()


def _redis_port():
    port = os.getenv('REDIS_PORT', '6379')
    try:
        return int(port)
    except ValueError:
        logging.warning("Ungültiger REDIS_PORT '%s'. Fallback auf 6379.", port)
        return 6379


# Ohne Timeout blockiert ein einzelner haengender Socket-Read/Write
# (z.B. nach einem stillen Verbindungsabbruch/Conntrack-Idle-Timeout) auf
# unbestimmte Zeit - der Aufbau-Prozess friert dann ein, ohne dass CPU-Last
# oder Exit das erkennen lassen. retry_on_timeout baut die Verbindung nach
# einem Timeout einmal automatisch neu auf, statt komplett haengen zu bleiben.
SOCKET_CONNECT_TIMEOUT = 5
SOCKET_TIMEOUT = 30


def _redis_client(db):
    redis_url = os.getenv('REDIS_URL', '').strip()
    redis_socket = os.getenv('REDIS_SOCKET', '').strip()
    redis_host = os.getenv('REDIS_HOST', 'localhost').strip()
    redis_password = os.getenv('REDIS_PASSWORD', '').strip() or None

    if redis_url:
        return redis.Redis.from_url(
            redis_url,
            db=db,
            socket_connect_timeout=SOCKET_CONNECT_TIMEOUT,
            socket_timeout=SOCKET_TIMEOUT,
            retry_on_timeout=True,
        )

    if redis_socket:
        return redis.Redis(
            unix_socket_path=redis_socket,
            db=db,
            password=redis_password,
            socket_connect_timeout=SOCKET_CONNECT_TIMEOUT,
            socket_timeout=SOCKET_TIMEOUT,
            retry_on_timeout=True,
        )

    return redis.Redis(
        host=redis_host,
        port=_redis_port(),
        db=db,
        password=redis_password,
        socket_connect_timeout=SOCKET_CONNECT_TIMEOUT,
        socket_timeout=SOCKET_TIMEOUT,
        retry_on_timeout=True,
    )


# Tatsächliche Datenbank für Wörter
r = _redis_client(0)

# Datenbank für zu postende Wörter
postRedis = _redis_client(1)

# Datenbank mit geposteten tweets
pastRedis = _redis_client(2)

# Wort abgleichen und zur Datenbank hinzufügen
def similiar_word(word):

    # Sonst Abfrage von verschiedenen Versionen und das tatsächliche Wort in die Datenbank einfügen
    pipe = r.pipeline()

    # Checken ob Kleinschreibung/Großschreibung
    pipe.hget('word:' + word.lower(), 'word')
    pipe.hget('word:' + word.capitalize(), 'word')


    # Existiert es schon im Plural oder ein einem anderen Fall?
    pipe.hget('word:' + word + 'er', 'word')
    pipe.hget('word:' + word + 'n', 'word')
    pipe.hget('word:' + word + 'en', 'word')
    pipe.hget('word:' + word + 's', 'word')
    pipe.hget('word:' + word + 'es', 'word')
    pipe.hget('word:' + word + 'e', 'word')


    # Existiert schon ein anderer Fall oder ein Singular?
    if word.endswith(('s','n', 'e')):
        pipe.hget('word:' + word[:-1], 'word')
    
    # "in" bewusst nicht (mehr) geprueft: gegenderte Formen (z.B. "Bundeskanzlerin",
    # "Alterspraesidentin") sollen als eigenstaendiges neues Wort erkannt werden,
    # nicht als blosse Variante der maennlichen Form unterdrueckt werden.
    if word.endswith(('’s', '’n', 'er', 'en', 'es', 'se')):
        pipe.hget('word:' + word[:-2], 'word')

    if word.endswith(('ern')):
        pipe.hget('word:' + word[:-3], 'word')

    if word.endswith(('m')):
        pipe.hget('word:' + word[:-1] + 'n', 'word')

    if word.endswith(('n')):
        pipe.hget('word:' + word[:-1] + 'm', 'word')

    if word.endswith(('en')):
        pipe.hget('word:' + word[:-2] + 'er', 'word')
        pipe.hget('word:' + word[:-2] + 'e', 'word')
        pipe.hget('word:' + word[:-2] + 't', 'word')

    # "innen"-Pluralform (z.B. "Kanzlerinnen") wird aus demselben Grund nicht
    # mehr geprueft - gegenderte Formen sollen als neues Wort durchgehen.

    return pipe.execute()

# Überprüft, ob das Wort schon in der Datenbank ist und ob die älteste Version notiert ist. 
def check_newness(word, id):
    # Wenn das Wort direkt existiert, skippen
    if r.hexists('word:' + word, 'word'):
        check_age(word, id)
        return False
    
    # Wenn nicht, dann zur Datenbank hinzufügen und trotzdem checken, ob andere Formen schon existieren.
    else:
        if all(v is None for v in similiar_word(word)):
            add_to_database(word, id)
            return True
        else:
            add_to_database (word, id)
            return False

# Helferfunktion, um Wort zum Korpus hinzuzufügen
def add_to_database (word, id):
    try:
        r.hset('word:' + word, 'word', word)
        r.hset('word:' + word, 'id', id)
        return True
    except Exception as e:
        logging.exception(e)
        raise

#Sorgt dafür, dass tatsächlich das älteste Wort in der Datenbank steht
def check_age(word,id):

    # Quelle des Wortes welches aktuell in der Datenbank ist
    aktuelle_id = r.hget('word:' + word, 'id').decode("utf-8")

    # id kommt je nach Aufrufer als int oder str (z.B. int aus plenar.py,
    # str aus build_database_local.py) - als str vergleichen, damit der
    # Vergleich unabhaengig vom Typ des Aufrufers korrekt funktioniert.
    if str(id) == aktuelle_id:
        return False

    else:

        try: 
            aktuell_p = r.hgetall('protokoll:' + str(aktuelle_id))
            aktuelle_periode = int(aktuell_p[b'wahlperiode'].decode("utf-8"))
            aktuelle_protokollnummer = int(aktuell_p[b'protokollnummer'].decode("utf-8"))


            # Quelle des Wortes, welches sich doppelt 
            neu_p = r.hgetall('protokoll:' + str(id))
            neue_periode = int(neu_p[b'wahlperiode'].decode("utf-8"))
            neue_protokollnummer = int(neu_p[b'protokollnummer'].decode("utf-8"))


            if (aktuelle_periode == neue_periode and aktuelle_protokollnummer > neue_protokollnummer) or (aktuelle_periode > neue_periode):
                r.hset('word:' + word, 'id', id)
                return True
            else:
                return False
        except Exception as e:
            logging.exception(e)
            raise


# Fügt ein Wort zur Posting-Datenbank hinzu
def add_to_queue(word, id):

    # Fix für Strichfehler
    if word[0].islower():
        return False

    postRedis.hset(word, 'word', word)
    postRedis.hset(word, 'id', id)
    
    return True

def delete_from_queue(word):
    if postRedis.delete(word):
        return True
    else:
        return False


# Redis-Set mit Vor-/Nachnamen aller Abgeordneten (aus den MdB-Stammdaten
# befuellt, siehe utilities/load_namen.py). Dient nur dazu, Namen aus der
# Export-Ausgabe herauszufiltern - der Korpus selbst (word:*) bleibt davon
# unberuehrt, Namen werden dort weiterhin ganz normal als "bekannt" getrackt.
NAMEN_SET_KEY = 'bekannte_namen'


def ist_bekannter_name(word):
    return bool(r.sismember(NAMEN_SET_KEY, word))


# Lemma-Tracking (additiv, nur fuer Export-Filterung in prune() - siehe
# STATUS.md "Weiterhin offen" #1). Wird ausschliesslich vorausschauend ab
# Einfuehrung dieses Features befuellt, kein rueckwirkender Abgleich gegen
# den historischen word:*-Korpus. word:*/check_newness bleiben unveraendert.
LEMMA_KEY_PREFIX = 'lemma:'


def ist_lemma_bekannt(lemma):
    return bool(r.hexists(LEMMA_KEY_PREFIX + lemma, 'id'))


def merke_lemma(lemma, id):
    try:
        r.hset(LEMMA_KEY_PREFIX + lemma, 'word', lemma)
        r.hset(LEMMA_KEY_PREFIX + lemma, 'id', id)
        return True
    except Exception as e:
        logging.exception(e)
        raise


