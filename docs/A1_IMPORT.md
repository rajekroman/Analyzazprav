# Analýza zpráv — A1 Import dat

A1 je lokální deterministická vstupní vrstva projektu. Čte zdrojové exporty a převádí je do jednoduchého staging kontraktu pro A2. **A1 nevlastní kanonickou databázi ani finální deduplikaci.**

První funkční slice podporuje Apple Messages `chat.db`.

## Vlastnosti

- `chat.db` se otevírá přes SQLite `mode=ro` + `PRAGMA query_only=ON`;
- zprávy se exportují do `messages.jsonl` + `manifest.json`;
- zachovává se source message ID, GUID, chat vazba, sender handle, Apple timestamp, `service`, reply GUID a metadata příloh;
- zachovává se `raw_text` i JSON-safe `raw_payload`; BLOB hodnoty jsou reprezentované Base64;
- `text` má prioritu, `attributedBody` má best-effort fallback bez externích knihoven;
- každý record obsahuje SHA-256 zdroje a stabilní `source_record_key` pro idempotentní zpracování v A2;
- vše běží lokálně, bez cloudu, externího API a AI.

## A1 → A2 kontrakt

A1 pouze extrahuje a bezpečně serializuje zdrojová data. A2 je autoritativní normalizační a SQLite vrstva a rozhoduje o canonical participants, conversations, messages, relations, attachments a deduplikaci.

Výstup:

```text
staging/
├── manifest.json
└── messages.jsonl
```

## Spuštění

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
az-import imessage --chat-db ~/Library/Messages/chat.db --output-dir ./staging/imessage
```

## Další A1 slice

1. attachment resolver + SHA-256 obsahu;
2. iMazing CSV adapter;
3. generic CSV/JSON/TXT adapter;
4. participant/group-chat membership;
5. reactions/Tapbacks a edit history;
6. robustnější `attributedBody` decoder;
7. validační report A1 → A7.
