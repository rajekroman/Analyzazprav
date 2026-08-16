# A1 `attributedBody` fallback

Apple Messages může mít u některých řádků `message.text = NULL`, zatímco text zůstává uložen v BLOB poli `attributedBody`. A1 používá `attributedBody` pouze jako best-effort fallback; nejde o autoritativní náhradu `message.text` ani o plný Apple typedstream decoder.

## Priorita dat

1. Pokud existuje `message.text`, A1 použije tuto hodnotu beze změny a `text_source = "text"`.
2. Pouze pokud `message.text` chybí, může A1 zkusit `decode_attributed_body(...)`.
3. Pokud decoder nenajde obhajitelný text, `text` zůstane `NULL`.
4. Originální `attributedBody` BLOB zůstává vždy zachován v `raw_payload` jako Base64, takže pozdější lepší decoder může data znovu zpracovat bez návratu ke zdroji.

## Co decoder dělá

Decoder verze A1 iMessage adapteru `0.6.0`:

- zachovává původní konzervativní Latin text extraction;
- přidává Unicode text runs pro nelatinkové skripty;
- podporuje emoji-only text a zachovává Unicode mark/format znaky potřebné pro variation/ZWJ sekvence;
- podporuje UTF-8 a pouze plausibilní UTF-16 LE/BE obsah;
- odmítá známé archive/class metadata jako `NSString`, `NSAttributedString`, `NSDictionary` apod.;
- nevrací punctuation-only fragment jako zprávu;
- omezuje výstup na 100 000 znaků.

## Co decoder nedělá

A1 netvrdí, že tímto implementuje Apple typedstream/NSAttributedString formát. Decoder:

- neinterpretuje atributy stylu;
- nevytváří text, pokud jej nelze přímo získat z BLOBu;
- neskládá heuristicky fragmenty do vět;
- nepřepisuje `raw_payload`;
- nepřepisuje existující `message.text`;
- nepovažuje náhodně dekódovatelná binární data za důkaz zprávy bez minimálních strukturálních kontrol.

## Provenance

Při úspěšném fallbacku staging record obsahuje:

```json
{
  "text": "日本語 👍",
  "raw_text": null,
  "text_source": "attributedBody",
  "raw_payload": {
    "attributedBody": {
      "encoding": "base64",
      "data": "..."
    }
  }
}
```

`raw_text` zůstává `NULL`, protože source `message.text` byl `NULL`. Tím je deterministicky rozlišeno, co bylo přímo ve zdrojovém textovém sloupci a co bylo získáno fallback decoderem.

## QA gate

Testy musí ověřovat minimálně:

- emoji-only UTF-8 text;
- nelatinkový UTF-8 text;
- plausibilní UTF-16 text;
- archive metadata bez skutečné zprávy → `None`;
- punctuation-only data → `None`;
- skutečný text oddělený od archive metadata;
- `message.text` má vždy přednost před jiným obsahem `attributedBody`;
- při fallbacku zůstává celý source BLOB v `raw_payload`;
- reconciliation a A2/A3 integrační testy zůstávají zelené.

Reálný uživatelský `chat.db` zůstává release gate pro ověření skutečných typedstream variant používaných konkrétní verzí macOS/iOS.
