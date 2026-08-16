# Analýza zpráv — A1 Import dat

A1 je lokální deterministická vstupní vrstva projektu. Čte zdrojové exporty a převádí je do jednoduchého staging kontraktu pro A2. **A1 nevlastní kanonickou databázi ani finální deduplikaci.**

Aktuální funkční slice podporuje:

- Apple Messages `chat.db`;
- iMazing Messages CSV s hlavičkou;
- resolver skutečných souborů příloh + SHA-256.

## Vlastnosti

- `chat.db` se otevírá přes SQLite `mode=ro` + `PRAGMA query_only=ON`;
- zprávy se exportují do `messages.jsonl` + `manifest.json`;
- zachovává se source message ID, GUID, chat vazba, sender handle, timestamp, `service`, reply GUID a metadata příloh;
- zachovává se `raw_text` i JSON-safe `raw_payload`; BLOB hodnoty jsou reprezentované Base64;
- `text` má prioritu, `attributedBody` má best-effort fallback bez externích knihoven;
- každý record obsahuje SHA-256 zdroje a stabilní `source_record_key` pro idempotentní zpracování v A2;
- přílohy mohou být dohledány přes `--attachments-root`; nalezený soubor dostane `resolved_path`, `actual_bytes`, `sha256` a stav `resolved`;
- nedohledaná příloha zůstává ve staging recordu se stavem `missing` — message record se neztrácí;
- iMazing CSV používá autodetekci delimiteru `,`, `;` nebo tab a tolerantní aliasy názvů sloupců;
- celý původní CSV řádek zůstává v `raw_payload` pro audit A7;
- datum s explicitním timezone offsetem se převádí do UTC; lokální datum bez offsetu zůstává raw a není falešně označeno jako UTC;
- vše běží lokálně, bez cloudu, externího API a AI.

## A1 → A2 kontrakt

A1 pouze extrahuje a bezpečně serializuje zdrojová data. A2 je autoritativní normalizační a SQLite vrstva a rozhoduje o canonical participants, conversations, messages, relations, attachments a deduplikaci.

Výstup:

```text
staging/
├── manifest.json
└── messages.jsonl
```

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

## Attachment status

- `resolved` — soubor existuje, byl spočítán SHA-256 a skutečná velikost;
- `missing` — zdroj uvádí cestu/název, ale soubor nebyl nalezen;
- `no_path` — attachment metadata neobsahují použitelnou cestu.

## Další A1 slice

1. generic CSV/JSON/TXT adapter;
2. participant/group-chat membership;
3. reactions/Tapbacks a edit history;
4. robustnější `attributedBody` decoder;
5. explicitní mapping profil pro nestandardní/headerless CSV;
6. validační report A1 → A7.
