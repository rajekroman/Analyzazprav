# A0 — Real archive release gate

Tento nástroj skládá existující A1–A7 kontrakty nad jedním skutečným Apple Messages `chat.db`. Nevytváří vlastní importer, normalizaci ani analytiku a nevolá LLM.

## Spuštění

Z kořene repozitáře:

```bash
python -m tools.real_archive_gate \
  --chat-db /cesta/k/chat.db \
  --workdir /cesta/k/novemu-prazdnemu-workdir \
  --target ILA
```

Pokud `ILA` není přesná hodnota uložená v canonical/source identitě, gate skončí `TARGET_NOT_RESOLVED` a do `real_archive_report.json` uloží inventář conversations. Potom spusťte nový workdir s autoritativním ID:

```bash
python -m tools.real_archive_gate \
  --chat-db /cesta/k/chat.db \
  --workdir /cesta/k/dalsimu-novemu-workdir \
  --conversation-id 123
```

Přílohy lze doplnit explicitně:

```bash
--attachments-root ~/Library/Messages/Attachments
```

## Co gate dělá

1. A1 vytvoří přes SQLite online backup konzistentní logical snapshot včetně committed WAL, tento snapshot hashne, parsuje a reconciliuje. Zdrojový `chat.db` je read-only.
2. A7 ověří A1 staging reconciliation.
3. A2 ingestuje staging do canonical SQLite a spustí structural/semantic integrity.
4. Resolver vybere přesně jednu conversation. `--target` používá pouze exact match nad title/canonical identity, participant identity nebo zdrojovými chat metadata (`display_name`, `chat_identifier`, `guid` atd.). Fuzzy ani substring match nikdy automaticky nevybere data.
5. A3 zpracuje canonical memberships a participant resolution.
6. A7 ověří participant sidecars a A1→A2→A3 vertical reconciliation.
7. A4 přepočítá pouze zvolenou conversation; nezávislý A7 arithmetic/evidence oracle znovu ověří current deterministic result.
8. A5 přečte uložené A4 candidates a A2/A3 message source read-only. Pro jeden candidate každého typu ověří bounded context, evidence dostupnost a membership/source provenance. Pokud A4 nevytvoří candidate, použije se pouze manuální provenance probe bez modelu.
9. A6 načte skutečný canonical read model, vytvoří minimální production packet, doplní A2 source provenance, projde A7 packet oracle a A5 packet adapterem.
10. Vznikne `real_archive_report.json` a log každého CLI kroku.

## Verdict

- `VALID` / `release_ready=true`: všechny integrity/provenance kontroly prošly a nejsou quality warnings.
- `NEEDS_REVIEW`: data nejsou tiše ztracena, ale existuje quality stav vyžadující kontrolu, například neznámé timestampy nebo chybějící attachment soubory.
- `INVALID`: selhal reconciliation, canonical integrity, participant/A4/A5/A6 provenance, target resolution nebo jiný povinný gate.

Běžná A5 redukce dlouhého kontextu na bounded selection je zaznamenána v A5 probe, ale sama o sobě není release chyba; candidate evidence se nesmí tiše ztratit.

## Ochrana dat

Nástroj neposílá zprávy žádné externí službě a report neukládá text zpráv. Inventář obsahuje lokální participant/source identity hodnoty potřebné k bezpečné identifikaci conversation, proto zůstává součástí lokálního workdiru a nemá se automaticky publikovat jako CI artifact.

## Release hranice

Syntetický GitHub A7 gate ověřuje kód. Tento real-archive gate ověřuje konkrétní data. MVP lze označit jako release candidate až po úspěšném běhu na skutečném požadovaném archivu a po vyřešení případných `NEEDS_REVIEW` položek.
