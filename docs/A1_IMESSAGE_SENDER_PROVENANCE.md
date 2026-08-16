# A1 iMessage sender-handle provenance

`message.handle_id` a výsledný `sender_handle` nejsou totéž. A1 musí zachovat, zda source zpráva měla handle reference, zda bylo možné ji rozřešit proti tabulce `handle`, a zda rozřešený row skutečně obsahoval hodnotu `id`.

## Výstup

Každý iMessage record od parser verze `0.9.0` obsahuje:

```json
{
  "metadata": {
    "_a1_sender_relation": {
      "raw_handle_id": 1,
      "resolved_handle_rowid": 1,
      "handle": "alice@example.com",
      "resolution_status": "resolved"
    }
  }
}
```

`sender_handle` zůstává veřejnou, zpětně kompatibilní projekcí skutečně rozřešené `handle.id`. `_a1_sender_relation` je source provenance.

## Resolution statuses

- `resolved` — `message.handle_id` existuje, cílový `handle` row existuje a `handle.id` není NULL;
- `missing_handle_id` — source schema má `message.handle_id`, ale konkrétní zpráva má hodnotu NULL;
- `handle_table_missing` — zpráva má raw `handle_id`, ale source schema nemá tabulku `handle`;
- `missing_handle_row` — `handle` tabulka existuje, ale cílový ROWID chybí;
- `handle_value_null` — cílový handle row existuje, ale jeho `id` je NULL;
- `handle_id_column_missing` — konkrétní Apple schema varianta vůbec nemá `message.handle_id`.

A1 z těchto stavů nevyvozuje identitu ani motivaci odesílatele. NULL nebo dangling reference je source fakt, nikoli automaticky chyba zprávy.

## Reconciliation

Pro iMessage parser `>=0.9.0` rozšířená A1 reconciliation nezávisle znovu vytvoří sender provenance ze stejného immutable SQLite snapshotu a ověří současně:

1. `metadata._a1_sender_relation` přesně odpovídá source `message.handle_id` a případnému `handle` row;
2. veřejné `sender_handle` odpovídá `handle.id` pouze ve stavu `resolved`, jinak je `null`.

`reconciliation.json` přidává:

- `checks.source_sender_provenance_matches_snapshot`;
- `sender_provenance.failure_count` a případné mismatch detaily;
- počty present/resolved/unresolved/null sender handle IDs a schema variant bez `handle_id` sloupce.

Úmyslná změna raw handle ID v metadata nebo vymyšlená hodnota `sender_handle` musí reconciliation shodit.

## A2 handoff

A2 již ukládá A1 `metadata` do source provenance vrstvy, takže sender relation nevyžaduje nový kanonický DB model. A1 tím pouze zpřesňuje dohledatelnost source identity; canonical participant identity zůstává odpovědností A2+.

## Parser version

Protože se metadata validních iMessage message records mění, output fingerprint se zvyšuje `0.8.0 → 0.9.0`.
