# A1 iMessage SQLite schema inventory

A1 při importu Apple Messages `chat.db` vytváří vedle message stagingu také deterministický popis SQLite schématu přesně toho immutable snapshotu, který se hashoval, parsoval a reconcilioval.

Cílem není interpretovat neznámé Apple sloupce. Cílem je auditovat, **jakou variantu source schématu A1 skutečně zpracoval**, a umožnit pozdější reprodukovatelné porovnání reálných macOS/iOS schema variant.

## Výstup

Pro iMessage staging vzniká:

```text
staging/
├── manifest.json
├── messages.jsonl
├── errors.jsonl
├── reconciliation.json
└── schema.json
```

`manifest.json` deklaruje:

```json
{
  "source": {
    "schema_inventory_version": "1",
    "schema_signature_sha256": "..."
  },
  "outputs": {
    "schema": "schema.json"
  }
}
```

## Co `schema.json` obsahuje

Inventory verze 1 obsahuje pouze strukturální SQLite metadata:

- `PRAGMA user_version`;
- `PRAGMA application_id`;
- seznam user tables mimo `sqlite_%`;
- pro každou tabulku sloupce z `table_xinfo`/`table_info`:
  - ordinal/cid,
  - název,
  - declared type,
  - NOT NULL,
  - default expression/value deklarovanou ve schématu,
  - pozici v primary key,
  - hidden/generated flag, pokud jej SQLite poskytuje;
- deklarované foreign keys;
- indexy, unique/origin/partial flags a indexované sloupce.

Inventory **nečte hodnoty řádků**. Neobsahuje text zpráv, handles, telefonní čísla, participant data ani attachment paths z tabulkových řádků.

## Schema signature

`signature_sha256` se počítá nad kanonickým JSON payloadem:

```text
inventory_version + sqlite metadata + tables + columns + foreign keys + indexes
```

Kanonizace používá stabilní pořadí klíčů a kompaktní JSON encoding. SQLite `schema_version` není součástí podpisu, protože jde o mutable interní counter a ne o logické schema metadata.

Důsledek:

- změna pouze řádkových dat podpis nemění;
- DDL změna, například nový sloupec nebo index, podpis změní;
- podpis je použitelný pro seskupení/porovnání skutečných Apple schema variant bez čtení obsahu komunikace.

## Snapshot invariant

U iMessage se používá jeden konzistentní SQLite online-backup snapshot. Ze stejného snapshotu vzniká:

```text
source logical SHA-256
→ message parsing
→ schema.json
→ reconciliation source inventory
```

Schema report proto nemůže legitimně popisovat jiný WAL stav než zprávy v témže staging bundle.

## Reconciliation

Pokud manifest deklaruje schema contract, A1 reconciliation znovu vytvoří inventory ze source snapshotu a kontroluje:

- schema declaration je kompletní;
- `schema.json` je validní JSON object;
- manifest schema signature odpovídá aktuálnímu snapshotu;
- inventory version odpovídá snapshotu;
- celý `schema.json` přesně odpovídá nově vypočtenému inventory;
- signature uvnitř `schema.json` odpovídá manifestu.

Reconciliation report obsahuje pouze stručný schema summary:

- expected/actual/report signature;
- inventory version;
- table count;
- `user_version` a `application_id`.

Plný strukturální inventory zůstává v `schema.json` a není zbytečně duplikován do `reconciliation.json`.

## Zpětná kompatibilita

Starší A1 bundle, který `schema_inventory_version`, `schema_signature_sha256` ani `outputs.schema` vůbec nedeklaruje, zůstává reconcilovatelný podle staršího kontraktu. Schema checks se aktivují pouze tehdy, když bundle schema contract deklaruje.

Nový iMessage adapter output používá parser version `0.7.0`, protože staging bundle nově obsahuje auditovatelný schema artifact a manifest source metadata.

## Hranice interpretace

Schema inventory nesmí být používán jako důkaz významu neznámého Apple sloupce. Přítomnost názvu jako `associated_message_type`, `edit_history` nebo jiného interního pole je pouze **fakt o schématu**. Semantika musí být ověřena zvláštním datovým kontraktem a testy nad skutečnými source hodnotami.

## QA gate

Před integrací musí testy prokázat:

1. row data nejsou součástí inventory;
2. změna row data nemění signature;
3. DDL změna signature mění;
4. iMessage import emituje `schema.json` a manifest signature;
5. A2 přijme bundle s extra schema artifactem;
6. schema tampering způsobí reconciliation failure;
7. legacy bundle bez schema declaration zůstává reconcilovatelný;
8. A1/A2/A3/A7 vertical regression zůstane zelený.
