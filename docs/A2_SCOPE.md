# A2 — Normalizace a databáze

Tato větev implementuje autoritativní normalizační a databázovou vrstvu projektu Analýza zpráv.

## Cíl

Převést strukturovaný výstup importérů A1 do jednotného kanonického modelu nad SQLite bez ztráty provenance.

## Kontrakt

- participant + participant_identity
- conversation + conversation_participant + conversation_source
- message + message_source + message_relation
- attachment + message_attachment + attachment_source
- import_run
- mikrosekundové UTC timestampy s evidencí kvality a přesnosti
- idempotentní import
- bezpečná deduplikace
- auditovatelná provenance
- analytické SQL views
- SQLite foreign keys + WAL

## Deduplikace

Automaticky se slučují pouze stabilně identifikované nebo plně ověřené duplicity. Pravděpodobné duplicity se evidují, ale neslučují destruktivně.

## Definition of Done

- schema lze inicializovat na čisté SQLite databázi
- opakovaný import stejného zdroje nevytvoří nové canonical messages
- dvě legitimně opakované zprávy se stejným textem zůstanou dvěma zprávami
- chybějící příloha nezpůsobí ztrátu message recordu
- raw text a raw payload jsou zachovány
- foreign_key_check a integrity_check projdou
- analytické views fungují bez znalosti původního zdroje
