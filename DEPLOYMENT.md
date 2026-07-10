# Deployment: Redis-Persistenz

Der Wortkorpus in Redis (DB0) ist praktisch unersetzlich: Er wird aus Jahren an
Plenarprotokollen aufgebaut und ein Neuaufbau ist durch die Rate-Limits der
DIP-API teuer und langsam. Die Schreibfrequenz ist dagegen sehr gering (ein
paar Dutzend `HSET`-Aufrufe, wenn stündlich ein neues Protokoll gefunden
wird, sonst Idle). Für dieses Profil geht Durability klar vor Performance —
und weil so selten geschrieben wird, kostet die sicherste Einstellung
praktisch nichts an Durchsatz.

## `redis.conf`

```conf
# --- Bind & Zugriff ---
bind 127.0.0.1 ::1          # nur lokal erreichbar; falls Remote-Zugriff nötig: SSH-Tunnel statt offener Port
requirepass <starkes-passwort>   # via REDIS_PASSWORD in .env schon vorgesehen
protected-mode yes

# --- AOF: primäre Durability-Garantie ---
appendonly yes
appendfilename "appendonly.aof"
appendfsync everysec        # verliert maximal 1s an Schreibvorgängen - bei dieser Schreibrate irrelevant
aof-use-rdb-preamble yes
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# --- RDB: Snapshots als einfach kopierbares Backup-Artefakt ---
save 900 1
save 300 10
save 60 10000
rdbcompression yes
rdbchecksum yes
dbfilename dump.rdb
dir /var/lib/redis
```

Beides zusammen: AOF ist die eigentliche Absicherung gegen Datenverlust bei
Crash/Absturz. RDB-Snapshots sind zusätzlich praktisch, weil `dump.rdb` eine
einzelne Datei ist, die sich leicht offsite kopieren lässt (AOF-Dateien sind
fürs Backup unhandlicher).

Alle drei genutzten logischen DBs (0 = Wortkorpus, 1 = Post-Queue,
2 = Archiv geposteter Wörter) liegen in derselben Instanz und werden
gemeinsam persistiert — kein Sonderfall nötig.

## Systemseitig

- `sysctl vm.overcommit_memory=1` — Standard-Redis-Empfehlung, damit der Fork
  für BGSAVE nicht am Speicher scheitert.
- `systemctl enable redis-server` — startet nach Reboot automatisch neu,
  lädt AOF/RDB beim Start.

## Offsite-Backup

Ohne Kopie außerhalb des Servers ist auch die beste lokale Persistenz nur ein
Schutz gegen Prozessabstürze, nicht gegen Festplatten-/Server-Ausfall. Ein
einfacher Cron-Job, der `dump.rdb` täglich auf einen zweiten Host/Storage
kopiert:

```bash
0 4 * * * rsync -az /var/lib/redis/dump.rdb backup-host:/backups/firstsaid/dump-$(date +\%F).rdb
```

Backups regelmäßig verifizieren, indem eine Kopie tatsächlich mit
`redis-cli --rdb` geladen wird — ein Backup, das nie getestet wurde, ist
kein Backup.
