# Analýza zpráv — A1 Import dat

A1 je lokální deterministická vstupní vrstva projektu. Čte zdrojové exporty a převádí je do jednoduchého staging kontraktu pro A2. **A1 nevlastní kanonickou databázi ani finální deduplikaci.**

Aktuální funkční slice podporuje:

- Apple Messages `chat.db` včetně committed WAL stavu;
- iMazing Messages CSV s hlavičkou;
- obecné message CSV s hlavičkou nebo explicitním mapping profilem pro nestandardní/headerless export;
- JSON a JSONL message exporty;
- TXT s explicitní hranicí recordu (`line`, `block`, `whole`);
- deterministickou detekci podporovaného source formátu;
- resolver skutečných souborů příloh + SHA-256;
- source participant membership a metadata Apple konverzací;
- konzervativní Apple associated-message/edit provenance;
- Unicode-safe best-effort `attributedBody` fallback;
- auditní evidenci neemitovaných recordů přes `errors.jsonl`;
- source-level reconciliation přes `reconciliation.json`.

## Vlastnosti

- původní `chat.db` se otevírá pouze pro čtení;
- před parsingem A1 vytvoří dočasný konzistentní SQLite snapshot přes online backup API;
- committed obsah aktivního `chat.db-wal` je součástí stejného logického snapshotu, který se následně hashují, parsuje i reconciliuje;
- A1 necheckpointuje ani nepřepisuje původní `chat.db` nebo WAL;
- `manifest.source.sha256` u iMessage označuje hash neměnného logického SQLite snapshotu skutečně analyzovaného A1, ne samotného hlavního souboru bez WAL;
- zprávy se exportují do `messages.jsonl` + `manifest.json`; serializační problémy jdou do `errors.jsonl`;
- `reconciliation.json` ověřuje staging proti skutečnému source snapshotu;
- jedna fyzická Apple `message.ROWID` vytváří právě jeden staging message record;
- všechny unikátní `chat_message_join` vazby jsou zachovány zvlášť v `conversation_sources[]`, takže více chat vazeb nemůže duplikovat zprávu;
- duplicitní raw `message_id/chat_id` join rows jsou jednotlivě evidované s outcome `duplicate`;
- dangling/nepodporované source relations a neodkazované attachment rows jsou jednotlivě evidované s outcome `unsupported`;
- Apple `chat.guid` je preferovaný source conversation key; fallback je explicitní `rowid:<id>` a raw ROWID se dál zachovává jako provenance;
- zachovává se source message ID, GUID, sender handle, timestamp, `service`, reply GUID a metadata příloh;
- pro Apple chaty se z `chat_handle_join` zachovají source participant handles a raw metadata řádku `chat`; hodnoty se cachují po `chat_id`;
- zachovává se `raw_text` i JSON-safe `raw_payload`; BLOB hodnoty jsou reprezentované Base64;
- `message.text` má vždy prioritu; `attributedBody` se používá pouze jako konzervativní fallback a původní BLOB zůstává v `raw_payload`;
- Apple associated-message/edit hodnoty se projektují do metadata bez neověřeného mapování interních numeric reaction kódů;
- každý record obsahuje source snapshot SHA-256 a stabilní `source_record_key` pro idempotentní zpracování v A2;
- iMessage `source_record_key` v2 je nezávislý na chat membership a identifikuje fyzickou message occurrence v konkrétním snapshotu;
- přílohy mohou být dohledány přes `--attachments-root`; nalezený soubor dostane `resolved_path`, `actual_bytes`, `sha256` a stav `resolved`;
- nedohledaná příloha zůstává ve staging recordu se stavem `missing` — message record se neztrácí;
- standardní CSV používá autodetekci delimiteru `,`, `;` nebo tab a pouze omezené jednoznačné aliasy názvů sloupců;
- nestandardní/headerless CSV používá explicitní JSON mapping profile, nikoli heuristiku;
- celý původní CSV/JSON record zůstává v `raw_payload` pro audit A7;
- headerless CSV zachovává každý source sloupec jako `column:<index>`, včetně nezmapovaných hodnot;
- CSV parser fail-closed odmítá duplicitní headers nebo strukturálně širší řádky, které by jinak `DictReader` mohl tiše oříznout;
- datum s explicitním timezone offsetem se převádí do UTC; lokální datum bez offsetu zůstává raw a není falešně označeno jako UTC;
- neznámý numerický timestamp se automaticky nepřevádí, protože bez znalosti epochy/jednotky by šlo o neauditovatelný odhad;
- generic TXT nikdy nehádá sendera ani timestamp a hranice recordu je povinně deklarována uživatelem;
- manifest rozlišuje `messages_seen`, `messages_emitted`, `message_errors`, `reconciliation_errors`, `duplicates` a `unsupported`;
- reconciliation mismatch vytvoří explicitní chybu a zvýší `counts.errors`, takže A2 takový bundle fail-closed odmítne;
- vše běží lokálně, bez cloudu, externího API a AI.

## A1 → A2 kontrakt

A1 pouze extrahuje a bezpečně serializuje zdrojová data. A2 je autoritativní normalizační a SQLite vrstva a rozhoduje o canonical participants, conversations, messages, memberships, relations, attachments a deduplikaci.

Výstup:

```text
staging/
├── manifest.json
├── messages.jsonl
├── errors.jsonl
└── reconciliation.json
```

`errors.jsonl` obsahuje importní nebo reconciliation chyby. `reconciliation.json` obsahuje raw source counts, jednotlivé `duplicate`/`unsupported` outcomes a explicitní boolean checks. Reconciliation stav `failed` vede k nenulovému `manifest.counts.errors`, takže bundle není způsobilý pro A2 ingest.

Podrobný lossless kontrakt včetně `conversation_sources[]`, M:N memberships a snapshot identity je v `docs/A1_A2_CONTRACT.md`.

## Detekce zdroje

```bash
az-import detect --source ~/Library/Messages/chat.db
az-import detect --source ./export/messages.csv
```

Detekce je konzervativní:

- SQLite se označí jako `imessage_chat_db` pouze pokud má Apple Messages `message` schema s požadovanými poli;
- iMazing CSV vyžaduje iMazing chat-session header společně s message fields;
- ostatní podporované headered CSV se označí jako generic CSV;
- headerless/zcela nestandardní CSV se neodhaduje — import se provádí explicitně přes `az-import csv --mapping-profile ...`;
- JSON/JSONL se strukturálně validuje během importu;
- TXT vždy vyžaduje explicitní `--mode`;
- neznámý nebo nejednoznačný source vrací `unknown`, nikoli odhadovaný parser.

## Reconciliation

Reconciliation se spouští automaticky při každém importu. Lze ji také zopakovat samostatně:

```bash
az-import reconcile \
  --source ~/Library/Messages/chat.db \
  --output-dir ./staging/imessage
```

Pro iMessage se kontroluje zejména:

- logical snapshot SHA-256 proti manifestu;
- právě jeden známý outcome pro každý `message.ROWID`;
- přesná množina unikátních source message↔chat relations;
- explicitní orphan messages;
- přesná multiplicita validních message↔attachment relations;
- `messages.jsonl`/`errors.jsonl` counts proti manifestu;
- source identity každého emitovaného recordu;
- přítomnost a unikátnost `source_record_key`;
- konzistence primary conversation s prvním `conversation_sources[]` recordem.

Raw duplicate join rows se zapisují jako `duplicate`; source rows/relations, které A1 nemůže bezpečně převést, se zapisují jako `unsupported`. Tyto outcomes nejsou potichu ztraceny a jsou dostupné A7.

## Apple Messages

```bash
az-import imessage \
  --chat-db ~/Library/Messages/chat.db \
  --attachments-root ~/Library/Messages/Attachments \
  --output-dir ./staging/imessage
```

`--attachments-root` je volitelný. Bez něj A1 stále zachová metadata a původní cestu přílohy.

### WAL a konzistentní snapshot

macOS může mít nejnovější committed Messages data v `chat.db-wal`. A1 proto neprovádí prostou kopii nebo hash pouze hlavního souboru. SQLite online backup vytvoří dočasný neměnný logický snapshot viditelných committed dat. **Stejný snapshot se hashují, parsuje i reconciliuje.**

Manifest pak obsahuje mimo jiné:

```json
{
  "source": {
    "type": "imessage_chat_db",
    "name": "chat.db",
    "sha256": "...",
    "snapshot_method": "sqlite_online_backup_v1",
    "snapshot_includes_committed_wal": true
  }
}
```

Dočasný snapshot se po importu odstraní. Originální databáze zůstává read-only vstupem.

### Apple associated/edit metadata a attributedBody

A1 zachovává vybraná associated-message/edit source metadata jako auditovatelnou projekci, ale neodvozuje semantic reaction label z interního numeric kódu. Podrobný kontrakt je v `docs/A1_APPLE_EVENT_METADATA.md`.

Pokud `message.text` chybí, A1 může zkusit Unicode-safe best-effort `attributedBody` fallback. `message.text` má vždy prioritu a source BLOB se nikdy nezahazuje. Podrobný kontrakt je v `docs/A1_ATTRIBUTED_BODY.md`.

## iMazing CSV

```bash
az-import imazing-csv \
  --csv ./export/messages.csv \
  --attachments-root ./export/Attachments \
  --output-dir ./staging/imazing
```

Specializovaný iMazing parser očekává CSV s hlavičkou. Headerless iMazing export se tímto parserem záměrně nehádá. Pokud jde ve skutečnosti o obecný známý CSV layout, lze jej importovat přes generic `csv` parser s explicitním mapping profilem.

## Obecné CSV

Standardní headered CSV:

```bash
az-import csv \
  --csv ./export/messages.csv \
  --attachments-root ./export/attachments \
  --output-dir ./staging/csv
```

Automaticky se mapují pouze běžné jednoznačné aliasy jako `text/message/body`, `sender/from`, `timestamp/date`, `conversation/chat/thread`, `direction`, `service` a `attachment(s)`. Všechny původní sloupce zůstávají v `raw_payload`.

Nestandardní nebo headerless CSV:

```bash
az-import csv \
  --csv ./export/messages.csv \
  --mapping-profile ./profiles/vendor.json \
  --output-dir ./staging/csv
```

Profil explicitně deklaruje delimiter, přítomnost hlavičky a přesné mapování kanonických polí. Manifest ukládá file SHA-256 i semantic SHA-256 profilu a semantic hash je součástí efektivní parser version, aby A2 nezaměnil dvě skutečně odlišná mapování téhož source souboru. Kompletní specifikace je v `docs/A1_CSV_MAPPING_PROFILE.md`.

## JSON / JSONL

```bash
az-import json \
  --json ./export/messages.json \
  --attachments-root ./export/attachments \
  --output-dir ./staging/json
```

Podporované vstupní tvary:

- JSON list objektů;
- JSON objekt s polem `messages`;
- jeden JSON message objekt;
- JSONL, jeden objekt na řádek.

Nested attachment objekt může obsahovat `path`/`filename`/`file`/`name`, MIME typ a velikost; celý objekt se zároveň zachová jako raw metadata.

## TXT

```bash
az-import txt \
  --txt ./export/messages.txt \
  --mode block \
  --output-dir ./staging/txt
```

`--mode` je povinný:

- `line` — každý neprázdný řádek je jeden staging record;
- `block` — bloky oddělené prázdným řádkem jsou staging records;
- `whole` — celý soubor je jediný staging record.

TXT fallback záměrně neinterpretuje sendera ani datum. Pokud je potřeba strukturovaný TXT export konkrétní aplikace, musí mít vlastní explicitní parser/profile.

## Attachment status

- `resolved` — soubor existuje, byl spočítán SHA-256 a skutečná velikost;
- `missing` — zdroj uvádí cestu/název, ale soubor nebyl nalezen;
- `no_path` — attachment metadata neobsahují použitelnou cestu.

## Aktuální QA gate

Integrační suite ověřuje mimo jiné:

- 1 fyzická message + 2 source chat vazby → 1 staging message + 2 A2 memberships;
- duplicate source chat relation je evidovaná jako `duplicate`, nikoli jako druhá message;
- neodkazovaný source attachment je evidovaný jako `unsupported`;
- tampering s `messages.jsonl` způsobí reconciliation failure;
- committed message existující v aktivním WAL je zahrnuta ve staging;
- import nezmění bytes `chat.db` ani aktivního WAL;
- dva importy stejného logického snapshotu vytvoří stejné `source.sha256` a `source_record_key`;
- Apple associated/edit metadata projdou do A2 source provenance bez neověřené reaction semantiky;
- Unicode `attributedBody` fallback nezmění existující `message.text` a zachová původní BLOB;
- explicitní CSV mapping zachovává i nezmapované headerless hodnoty a fingerprintuje skutečnou mapping semantiku;
- A1 staging projde A2/A3 integračními testy s čistým `PRAGMA integrity_check` a `foreign_key_check`.

## Další A1 slice

1. ověření na reálném uživatelském `chat.db` + skutečný reconciliation report;
2. A7 golden dataset generovaný přímo současným A1 importerem;
3. semantic reaction/Tapback mapping pouze po ověření skutečných source hodnot a explicitním A1→A2/A3 kontraktu.
