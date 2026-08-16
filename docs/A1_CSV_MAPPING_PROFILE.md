# A1 CSV mapping profile

A1 standardně podporuje headered CSV s omezenou sadou jednoznačných aliasů. Pro nestandardní názvy sloupců nebo headerless exporty se význam dat nesmí hádat. K tomu slouží explicitní JSON mapping profile.

## CLI

```bash
az-import csv \
  --csv ./export/messages.csv \
  --mapping-profile ./profiles/vendor-x.json \
  --output-dir ./staging/vendor-x
```

Bez `--mapping-profile` zůstává zachované standardní aliasové chování A1.

## Profil verze 1

Povinné klíče:

- `version` — aktuálně přesně `"1"`;
- `delimiter` — právě jeden znak, například `","`, `";"` nebo `"\t"`;
- `has_header` — `true` nebo `false`;
- `fields` — neprázdná mapa kanonických A1 polí na source sloupec/index.

Podporovaná kanonická pole:

- `id`
- `guid`
- `conversation`
- `sender`
- `timestamp`
- `text`
- `service`
- `direction`
- `attachment`

Profil neprovádí transformace hodnot. Pouze explicitně určuje, odkud se jednotlivá kanonická pole čtou. Stávající deterministické A1 převody timestampu a direction se aplikují až následně.

## Headered CSV

Při `has_header: true` musí být selectors přesné názvy source hlaviček:

```json
{
  "version": "1",
  "delimiter": ";",
  "has_header": true,
  "fields": {
    "id": "Klíč",
    "sender": "Osoba",
    "timestamp": "Kdy",
    "text": "Obsah",
    "conversation": "Vlákno",
    "direction": "Směr"
  }
}
```

A1 odmítne profil, který odkazuje na neexistující header. Hlavičky se v profilovém režimu nemapují přes aliasy ani fuzzy matching.

## Headerless CSV

Při `has_header: false` jsou selectors zero-based indexy:

```json
{
  "version": "1",
  "delimiter": ",",
  "has_header": false,
  "fields": {
    "id": 0,
    "sender": 1,
    "timestamp": 2,
    "text": 3,
    "conversation": 4,
    "direction": 5
  }
}
```

Pro source řádek:

```text
m1,+420111222333,2026-08-15T12:00:00Z,Ahoj,c1,outgoing,EXTRA
```

zůstane `raw_payload` bezeztrátově:

```json
{
  "column:0": "m1",
  "column:1": "+420111222333",
  "column:2": "2026-08-15T12:00:00Z",
  "column:3": "Ahoj",
  "column:4": "c1",
  "column:5": "outgoing",
  "column:6": "EXTRA"
}
```

Nezmapovaný `column:6` se tedy neztratí.

## Strukturální fail-closed chování

A1 záměrně odmítá:

- nepodporovanou verzi profilu;
- delimiter delší než jeden znak nebo newline;
- neznámé kanonické pole;
- string selector v headerless režimu;
- záporný/non-integer index v headerless režimu;
- chybějící source header v headered režimu;
- duplicitní CSV header names;
- headered řádek s jiným počtem polí než header;
- headerless řádek kratší než nejvyšší požadovaný index.

U standardního CSV bez profilu A1 nově rovněž odmítne duplicitní headers a řádky s více poli než header. Důvodem je zabránit tiché kolizi/truncation v `csv.DictReader`.

## Identita profilu a A2 fingerprint

Manifest ukládá dvě identity profilu:

- `file_sha256` — hash přesných bytes JSON souboru pro provenance;
- `semantic_sha256` — hash kanonické reprezentace `version + delimiter + has_header + fields`.

Efektivní parser version má tvar:

```text
0.2.0+profile.<semantic_sha256>
```

A2 už zahrnuje `parser.version` do source fingerprintu. Dvě skutečně odlišná mapování téhož CSV proto nejsou zaměněna za jeden import. Pouhá změna whitespace/formátování JSON profilu semantic fingerprint nemění.

## Hranice odpovědnosti

Mapping profile nesmí:

- hádat epochu numerického timestampu;
- domýšlet timezone;
- normalizovat identitu kontaktu;
- spojovat konverzace;
- deduplikovat zprávy;
- interpretovat neznámé hodnoty direction mimo stávající explicitní A1 slovník.

Tyto odpovědnosti zůstávají v příslušných vrstvách A1/A2/A3 podle společného projektu.
