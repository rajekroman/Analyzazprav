# A7 — current-main release gate

Tato brána je syntetická integrační kontrola aktuálního checkoutu. Nenahrazuje `az-qa staging` ani `az-qa vertical` nad skutečným archivem.

## Autoritativní princip

Všechny komponenty (`core`, `A5`, `A6`) musí být spuštěny nad stejným `GITHUB_SHA`. Workflow nepřepíná na historické A5/A6 branche ani nepoužívá připnuté staré SHA.

## Komponenty

- `core`: kompletní repository `pytest` + `compileall`;
- `A5`: aktuální A5 validator vytvoří materializovaný evidence snapshot a nezávislý A7 `validate_a5_evidence_chain` jej znovu ověří včetně membership, source a A4 metric provenance; záměrně poškozená provenance musí být odmítnuta;
- `A6`: production `CanonicalDatabase` vytvoří A2 v6 fixture s jednou kanonickou zprávou ve dvou conversations, explicitním unknown timestampem a přílohou. Aktuální A6 musí zachovat memberships, unknown-time stav, message/attachment provenance, vytvořit production A5 packet s source provenance a aktuální A5 packet adapter ji musí zachovat. Záměrně odstraněná source provenance musí selhat v A7 i A5;
- `release-verdict`: pouze tři `VALID` reporty se stejným SHA a úspěšné joby dávají `release_ready=true`.

## Fail-closed pravidla

Chybějící report po úspěšném jobu znamená `NEEDS_REVIEW`. Neúspěšný job, `INVALID` komponenta, neznámý verdict nebo rozdílný `contract_sha` znamená `INVALID`. Žádný takový stav se nesmí prezentovat jako release-ready.

## Scope boundary

`release_ready=true` z tohoto workflow znamená pouze to, že aktuální commit prošel definovanou syntetickou integrační bránou A1–A7. Neprokazuje kompatibilitu s libovolnou reálnou Apple Messages databází ani úplnost konkrétního uživatelského archivu.

Před označením MVP jako release candidate musí následovat skutečný archiv:

`A1 source reconciliation → A2 canonical provenance → A3 processing → A4 deterministic analytics → A5 bounded evidence → A6 drill-down → A7 vertical + downstream verdict`.
