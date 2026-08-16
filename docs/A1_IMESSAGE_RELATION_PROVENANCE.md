# A1 iMessage relation provenance

A1 musí zachovat nejen fyzický `message` row, ale také source-level vazby, které určují jeho původní chat a participant relace. Apple `chat.db` může obsahovat dangling nebo částečně rozřešitelné reference. Tyto reference se nesmí tiše zahodit ani automaticky opravovat.

## Message → chat

Každá emitovaná `conversation_sources[]` relace s `raw_chat_rowid` obsahuje v `metadata` A1-owned blok:

```json
{
  "_a1_source_relation": {
    "chat": {
      "raw_chat_rowid": 7,
      "resolution_status": "resolved"
    },
    "participant_relations": []
  }
}
```

`chat.resolution_status` je deterministicky jeden z:

- `resolved` — odpovídající `chat.ROWID` existuje;
- `missing_chat_row` — `chat` tabulka existuje, ale cílový row chybí;
- `chat_table_missing` — source schema vůbec nemá `chat` tabulku.

I při unresolved chat reference zůstává `source_conversation_key = rowid:<chat_id>` a raw `chat_id` se zachová. A1 nevymýšlí GUID ani canonical identity.

## Chat → handle occurrences

`participant_relations` zachovává každý relevantní `chat_handle_join` occurrence samostatně. Relace se řadí deterministicky podle `handle_id` a dostává `source_relation_ordinal` v rámci chatu.

Každá occurrence obsahuje:

- `raw_chat_rowid`;
- `raw_handle_id` včetně `null`;
- `source_relation_ordinal`;
- `resolution_status`;
- při rozřešení také `resolved_handle_rowid` a `handle`.

Podporované statusy:

- `resolved`;
- `missing_handle_id`;
- `missing_handle_row`;
- `handle_value_null`;
- `handle_table_missing` jako defensivní parser stav; běžný import jej preflight odmítne, pokud je `chat_handle_join` přítomný.

Duplicitní source relation occurrences se zde neslučují. Pokud `chat_handle_join` obsahuje stejný `handle_id` dvakrát, provenance obsahuje dva záznamy. `participant_handles` nadále obsahuje pouze skutečně rozřešené handle hodnoty a zachovává jejich source multiplicitu.

## Reconciliation extension

Stávající A1 reconciliation zůstává základním autoritativním checkem pro source SHA, message rows, conversation pairs, attachments, schema signature a outcomes.

Pro iMessage output od parser verze `0.8.0` se nad stejným immutable SQLite snapshotem přidává relation-provenance check:

```text
base reconciliation
→ reconstruct expected chat/participant relation provenance
→ compare with messages.jsonl conversation_sources metadata
→ source_relation_provenance_matches_snapshot
```

Wrapper používá stejný online-backup snapshot, takže source SHA, parser, schema inventory, základní reconciliation i relation reconciliation nemohou legitimně reprezentovat odlišný WAL stav.

`reconciliation.json` přidává:

- `checks.source_relation_provenance_matches_snapshot`;
- `relation_provenance.failure_count` a detail mismatchů;
- počty emitovaných relation provenance vazeb;
- počet relevantních `chat_handle_join` rows;
- počty unresolved chat/participant reference stavů.

Úmyslné poškození relation metadata musí tento check shodit a tím označit A1 bundle jako nevalidní pro A2 ingest.

## Parser version

Protože se staging metadata validních iMessage záznamů mění, iMessage parser/output version se zvyšuje z `0.7.0` na `0.8.0`.

## Hranice

A1 pouze zaznamenává source relation a její rozřešitelnost. Nevyvozuje sociální význam participantů, neslučuje identity a nepřevádí dangling reference na domnělé kontakty nebo konverzace.
