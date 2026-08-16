# A1 Apple event metadata

Tento slice rozšiřuje A1 iMessage import bez změny kanonického A2 schématu. Cílem je zpřístupnit vybraná Apple source metadata pro reactions/associated messages a edit state, ale nepřidávat neověřenou semantiku nad interními Apple kódy.

## Zásada

`raw_payload` zůstává autoritativní bezeztrátová reprezentace source `message` row. Nová metadata jsou pouze deterministická projekce již zachovaných source hodnot.

A1 nesmí:

- měnit `associated_message_guid`;
- odstraňovat Apple part-prefix nebo jinak normalizovat target GUID bez explicitního kontraktu;
- mapovat `associated_message_type` na `like`, `love`, `dislike`, `laugh`, `emphasize` apod. pouze podle neoficiálního číselníku;
- tvrdit, že associated-message row je reaction, pokud to nelze doložit z datového kontraktu;
- dekódovat nebo zahodit originální `edit_history` BLOB.

## `apple_associated_message`

Pokud source row obsahuje některé z následujících polí, A1 je zkopíruje do `metadata.apple_associated_message`:

- `associated_message_guid`
- `associated_message_type`
- `associated_message_emoji`
- `associated_message_range_location`
- `associated_message_range_length`

Hodnoty jsou zachovány přesně tak, jak byly v source row po JSON-safe serializaci.

Příklad:

```json
{
  "metadata": {
    "apple_associated_message": {
      "associated_message_guid": "p:0/GUID-10",
      "associated_message_type": 2001,
      "associated_message_emoji": "👍",
      "associated_message_range_location": 0,
      "associated_message_range_length": 4
    }
  }
}
```

Číslo `2001` zde zůstává raw source hodnota. A1 z něj neodvozuje semantic reaction label.

## `apple_edit_state`

Pokud jsou ve source schema přítomné edit/retraction údaje, A1 může do `metadata.apple_edit_state` promítnout:

- `date_edited_raw`
- `date_edited_utc`
- `date_retracted_raw`
- `date_retracted_utc`
- `is_edited_raw`
- `is_deleted_raw`
- `is_retracted_raw`
- `edit_history_present`
- `edit_history_bytes`

UTC derivace se vytvoří pouze pro nenulový timestamp, který lze převést stávajícím Apple timestamp kontraktem. Originální raw timestamp zůstává zachovaný.

`edit_history` BLOB zůstává celý v `raw_payload` jako Base64. Strukturovaná metadata ukládají pouze přítomnost a velikost BLOBu; obsah se zde neinterpretuje.

## A1 → A2

A2 už uchovává A1 `metadata` v `message_source.metadata_json`. Tento slice proto nevyžaduje změnu A2 schématu a zachovává provenance:

```text
chat.db message row
→ A1 raw_payload
→ A1 metadata.apple_associated_message / apple_edit_state
→ A2 message_source.metadata_json
```

Canonical `message.is_edited`, reaction relations a semantické reaction labels nejsou tímto A1 slicem automaticky odvozovány. To je samostatné rozhraní A2/A3 a musí být implementováno až s explicitním, testovaným kontraktem.

## QA

Release gate tohoto slice ověřuje:

1. parser/import zůstává 1 physical source row → 1 A1 message record;
2. associated fields se zachovají přesně, včetně raw target GUID a numeric type;
3. edit timestamp se zachová raw a současně dostane auditovatelnou UTC projekci;
4. `edit_history` BLOB se neztratí a zůstane Base64 v `raw_payload`;
5. `edit_history_bytes` odpovídá skutečné délce BLOBu;
6. A2 ingest zachová obě metadata struktury v `message_source.metadata_json`;
7. A2 integrity a foreign-key checks zůstanou čisté;
8. žádný reaction type není semanticky klasifikován bez ověřeného kontraktu.
