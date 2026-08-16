# A1 iMessage preflight

Před vytvořením A1 staging bundle musí iMessage importer ověřit přesně ten immutable SQLite snapshot, který následně hashujeme, parsujeme, inventarizujeme a reconciliujeme.

## Cíl

Preflight má zabránit dvěma třídám chyb:

1. poškozený SQLite snapshot;
2. strukturální schema varianta, kterou aktuální A1 parser neumí bezpečně číst.

Preflight není interpretace Apple interních polí. Neurčuje význam `associated_message_type`, edit historie ani jiných volitelných sloupců.

## Kontroly

Preflight verze 1 provádí:

- `PRAGMA quick_check` a vyžaduje jediný výsledek `ok`;
- vyžaduje tabulku `message`;
- v `message` vyžaduje pouze parser-minimum `date` a `is_from_me`;
- pokud existuje `chat_message_join`, vyžaduje `message_id` a `chat_id`;
- pokud existuje `chat_handle_join`, vyžaduje `chat_id` a `handle_id`;
- pokud existuje `message_attachment_join`, vyžaduje `message_id` a `attachment_id`;
- pokud parser skutečně používá tabulku `handle`, vyžaduje sloupec `id`;
- `chat_handle_join` bez tabulky `handle` je explicitně nepodporované schema.

Volitelné relační tabulky mohou zcela chybět. Například snapshot pouze s validní `message` tabulkou je podporovaný a zprávy se zachovají jako explicitní orphan conversation source. Tím se absence relace nezamění za ztracenou zprávu.

## Fail-closed chování

Preflight se spouští uvnitř konzistentního SQLite online-backup snapshotu, ale ještě před `_write_records()`.

Pokud selže:

- source `chat.db` se nemění;
- nevznikne staging adresář;
- nevznikne poloviční `manifest.json`, `messages.jsonl` ani jiný bundle artifact;
- caller dostane explicitní `ValueError` popisující strukturální problém.

## Parser version

Validní snapshot produkuje stejné A1 message records a stejné schema artifacts jako před přidáním preflightu. Proto se iMessage parser/output fingerprint nezvyšuje jen kvůli přísnějšímu odmítnutí nevalidního vstupu. Aktuální validní output zůstává `0.7.0`.

## QA

Testy musí pokrýt:

- validní minimální schema;
- malformed `chat_message_join`;
- malformed `message_attachment_join`;
- `chat_handle_join` bez `handle`;
- `handle` bez `id`, pokud jej parser potřebuje;
- absenci všech volitelných relation tables a zachování zprávy jako orphan;
- neexistenci staging adresáře po preflight failure;
- A1/A2/A3/A7 vertical regresi.
