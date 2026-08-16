# Analýza zpráv — A1 Import dat

A1 je lokální deterministická vstupní vrstva projektu. Čte zdrojové exporty a převádí je do jednoduchého staging kontraktu pro A2. **A1 nevlastní kanonickou databázi ani finální deduplikaci.**

Aktuální funkční slice podporuje:

- Apple Messages `chat.db`;
- iMazing Messages CSV s hlavičkou;
- obecné message CSV s hlavičkou;
- JSON a JSONL message exporty;
- TXT s explicitní hranicí recordu (`line`, `block`, `whole`);
- resolver skutečných souborů příloh + SHA-256;
- source participant membership a metadata Apple konverzací;
- auditní evidenci neemitovaných recordů přes `errors.jsonl`.

## Vlastnosti

- `chat.db` se otevírá přes SQLite `mode=ro` + `PRAGMA query_only=ON`;
- zprávy se exportují do `messages.jsonl` + `manifest.json`;
- zachovává se source message ID, GUID, chat vazba, sender handle, timestamp, `service`, reply GUID a metadata příloh;
- pro Apple chaty se z `chat_handle_join` zachovají source participant handles a raw metadata řádku `chat`; hodnoty se cachují po `chat_id`, aby dlouhé konverzace nevytvářely opakované SQL dotazy;
- zachovává se `raw_text` i JSON-safe `raw_payload`; BLOB hodnoty jsou reprezentované Base64;
- `text` má prioritu, `attributedBody` má best-effort fallback bez externích knihoven;
- každý record obsahuje SHA-256 zdroje a stabilní `source_record_key` pro idempotentní zpracování v A2;
- přílohy mohou být dohledány přes `--attachments-root`; nalezený soubor dostane `resolved_path`, `actual_bytes`, `sha256` a stav `resolved`;
- nedohledaná příloha zůstává ve staging recordu se stavem `missing` — message record se neztrácí;
- CSV používá autodetekci delimiteru `,`, `;` nebo tab a pouze omezené jednoznačné aliasy názvů sloupců;
- celý původní CSV/JSON record zůstává v `raw_payload` pro audit A7;
- datum s explicitním timezone offsetem se převádí do UTC; lokální datum bez offsetu zůstává raw a není falešně označeno jako UTC;
- neznámý numerický timestamp se automaticky nepřevádí, protože bez znalosti epochy/jednotky by šlo o neauditovatelný odhad;
- generic TXT nikdy nehádá sendera ani timestamp a hranice recordu je povinně deklarována uživatelem;
- manifest rozlišuje `messages_seen` a `messages_emitted`; serializační chyba se zapíše do `errors.jsonl` se source identifikací a typem chyby;
- vše běží lokálně, bez cloudu, externího API a AI.

## A1 → A2 kontrakt

A1 pouze extrahuje a bezpečně serializuje zdrojová data. A2 je autoritativní normalizační a SQLite vrstva a rozhoduje o canonical participants, conversations, messages, relations, attachments a deduplikaci.

Výstup:

```text
staging/
├── manifest.json
├── messages.jsonl
└── errors.jsonl
```

Prázdný `errors.jsonl` znamená, že během serializace nebyl vynechán žádný záznam. A7 může současně ověřit `messages_seen == messages_emitted` a `errors == 0`.

## Apple Messages

```bash
az-import imessage \
  --chat-db ~/Library/Messages/chat.db \
  --attachments-root ~/Library/Messages/Attachments \
  --output-dir ./staging/imessage
```

`--attachments-root` je volitelný. Bez něj A1 stále zachová metadata a původní cestu přílohy.

## iMazing CSV

```bash
az-import imazing-csv \
  --csv ./export/messages.csv \
  --attachments-root ./export/Attachments \
  --output-dir ./staging/imazing
```

Parser očekává CSV s hlavičkou. Headerless export je záměrně odmítnut, protože bez explicitní mapy sloupců by A1 musel hádat význam hodnot a porušil by auditovatelnost.

## Obecné CSV

```bash
az-import csv \
  --csv ./export/messages.csv \
  --attachments-root ./export/attachments \
  --output-dir ./staging/csv
```

Automaticky se mapují pouze běžné jednoznačné aliasy jako `text/message/body`, `sender/from`, `timestamp/date`, `conversation/chat/thread`, `direction`, `service` a `attachment(s)`. Všechny původní sloupce zůstávají v `raw_payload`.

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

## Další A1 slice

1. reactions/Tapbacks a edit history;
2. robustnější `attributedBody` decoder;
3. explicitní mapping profil pro nestandardní/headerless CSV;
4. validační report A1 → A7.
