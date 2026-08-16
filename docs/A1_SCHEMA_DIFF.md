# A1 schema inventory diff

`az-import schema-diff` porovnává dva A1 `schema.json` inventáře bez přístupu k původním message rows. Nástroj je určen pro audit změn Apple SQLite schématu mezi dvěma importy nebo zařízeními/verzemi systému.

## Použití

```bash
az-import schema-diff \
  --before ./older/schema.json \
  --after ./newer/schema.json
```

Výstup je deterministický JSON report. Běžné porovnání vrací exit code `0` bez ohledu na to, zda drift existuje.

Pro CI gate:

```bash
az-import schema-diff \
  --before ./baseline/schema.json \
  --after ./candidate/schema.json \
  --fail-on-change
```

`--fail-on-change` vrací `2`, pokud je detekována strukturální změna, jinak `0`.

## Vstupní integrita

Před porovnáním A1:

1. načte JSON object;
2. ověří podporovanou `inventory_version`;
3. vyžaduje `signature_sha256`;
4. znovu vypočte signature z kanonického inventory payloadu;
5. odmítne artifact, jehož deklarovaný podpis neodpovídá obsahu;
6. odmítne duplicitní názvy tabulek a strukturálně nevalidní collections.

Schema diff tedy nepracuje s ručně pozměněným `schema.json` jako s důvěryhodným vstupem.

## Reportované změny

Report verze 1 rozlišuje:

- změny SQLite metadata, například `user_version` nebo `application_id`;
- přidané tabulky;
- odstraněné tabulky;
- u společných tabulek:
  - přidané sloupce,
  - odstraněné sloupce,
  - změněné definice sloupců,
  - přidané/odstraněné foreign-key entries,
  - přidané/odstraněné/změněné indexy.

Sloupec se identifikuje přesným názvem a jako změněný se zobrazí, pokud se změní jiná inventarizovaná strukturální vlastnost, například type, NOT NULL, default, PK position, cid nebo hidden flag.

Index se identifikuje přesným názvem. Změna unique/origin/partial flagu nebo indexovaných sloupců se zobrazí jako `changed`.

Foreign keys nemají stabilní uživatelské jméno, proto se porovnávají jako přesné kanonické strukturální entries; změna constraintu se objeví jako removal + addition.

## Co diff nedělá

Schema diff:

- nečte ani neporovnává message rows;
- neporovnává počty zpráv;
- neobsahuje telefonní čísla, handles, texty ani attachment paths z row dat;
- neinterpretuje význam interních Apple názvů;
- netvrdí kompatibilitu nebo nekompatibilitu parseru pouze na základě názvu nového sloupce;
- nemění source, staging ani canonical DB.

Výsledek je **strukturální fakt**, nikoli interpretace.

## Typický workflow pro reálný `chat.db`

```text
chat.db A → A1 import → schema.json A
chat.db B → A1 import → schema.json B
                         ↓
               az-import schema-diff
                         ↓
          deterministický schema drift report
```

Pokud se objeví neznámý Apple sloupec, správný další krok je analyzovat jeho raw source hodnoty a provenance v odděleném testovaném kontraktu. Samotná schema presence není důkaz semantiky.

## Verze

Schema diff má vlastní `diff_version = "1"`. Jde pouze o analytickou utilitu nad existujícími signed inventory artifacts, takže nezvyšuje iMessage parser/output version `0.7.0` a nemění A1 staging contract.
