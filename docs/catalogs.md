# Catalogs: texts, languages and spoken numbers

A catalog is a JSON file of texts keyed by name, with the placeholders each text takes. A
connector uses them in two places: its **announcement catalog** (declared to Pitwall with
`pitwall.catalog.declare`, [contract.md section 4.4](contract.md#44-connector-messages-and-announcements))
and, optionally, the catalog of its own control app's window texts. A connector writes its
catalogs in one language (usually English); the managing app translates them. This page is the
format, the keys, the placeholders, the fallback and the validation rules Pitwall applies.

## 1. Files and places

| File | Where | Written by |
|---|---|---|
| An app's built-in English catalog | inside the app's package | the app's authors |
| A user's language pack: one catalog per app | `<workspace>\locales\<tag>\<catalog>.json` | the hub's translate job, or the user, or the user's agent |
| The pack's number catalog (spoken numbers, plural rules) | `<workspace>\locales\<tag>\numbers.json` | Pitwall, or the user |
| The pack's glossary (read by the translate tasks) | `<workspace>\locales\<tag>\glossary.json` | the user, or the user's agent |
| A connector's translated event catalog | `<workspace>\locales\<tag>\connector.<id>.json` | the hub's translate job (the one translation path, [contract.md section 6](contract.md#6-a-connectors-announcement-catalog-and-its-translation)) |

An app serves its built-in English catalog over IPC (`control.catalog.get`,
[contract.md section 3.1](contract.md#31-methods)), which is how the hub's translate job
learns what to translate without reading the app's files. No file has two automatic writers.

- `<tag>` is a BCP 47 language tag (section 6), used as the folder name as is (`es`, `es-CL`,
  `pt-BR`). Folder names are compared without regard to case.
- Catalog names (the `catalog` field and the file name): `hub`, `pitwall` (Pitwall's control
  app), `voice` (Stintmate Voice: its control app and what it speaks) and, for any connector's
  own control app (its window texts), **`<id>-connector`** where `<id>` is the connector's id
  (`example-connector` for a connector with the id `example`). The name is derived from the id, so the list is
  open: no registration. `connector.<id>` is reserved for connectors' event catalogs (the
  announcements Pitwall holds). A connector only declares its English (or own-language)
  catalogs; **the hub translates every catalog**, the connector's own and Pitwall's alike.
  Pattern: `^[a-z][a-z0-9-]{0,31}(\.[a-z0-9-]{1,32})?$`. A connector's catalog is named
  `<id>-connector`, and a connector id may also hold `_` and start with a digit
  (`^[a-z0-9][a-z0-9_-]{0,31}$`), so such a name can fall outside this pattern; no code of
  this repository checks the pattern.
- Only English is built in. A pack may hold any subset of the catalogs; a missing file, key or
  number word falls back (section 6). A pack for `en` itself is allowed (it rewords English).
- The workspace layout and the language setting: [descriptor.md](descriptor.md#languages-locales-and-the-language-setting).

## 2. Catalog file

```json
{
  "schema": "stintmate-catalog/1",
  "catalog": "voice",
  "language": "es",
  "language_name": "Español",
  "source_language": "en",
  "messages": {
    "speech.lap.best": {"text": "Mejor vuelta.", "source_hash": "3f1c0a9b2e7d"},
    "speech.fuel.laps": {"text": "Combustible para {laps}.", "source_hash": "9a0b1c2d3e4f"},
    "ui.messages.count": {
      "text": {"one": "{n} mensaje", "other": "{n} mensajes"},
      "source_hash": "0123456789ab"
    }
  }
}
```

Top level (unknown keys are ignored):

| Field | Type | Meaning |
|---|---|---|
| `schema` | string, required | `"stintmate-catalog/1"`. Anything else: the file is refused. |
| `catalog` | string, required | The catalog's name; equals the file name without `.json`. |
| `language` | string, required | The language of the texts (BCP 47); in a pack, equal to the folder's tag. |
| `language_name` | string, optional | The language's name in that language (`Español`), shown by the hub's selector. |
| `source_language` | string, optional | In a translation: the language it was translated from (`en`). |
| `messages` | object, required | Key (section 3) → message. |

(The hashes are illustrative.) A message (unknown keys are ignored, so a protocol that carries a catalog may add its own,
e.g. `audience` and `priority` (`spotter`, `critical`, `high`, `normal`, `low`; default `normal`), `max_wait_s` (seconds, 0.5..600, optional) and `slot` (optional; a newer message of a slot replaces a queued one), `variants` (OPTIONAL, 1..8 further wordings of `text`, each a string with the same placeholders), and `group`, the category of the message, one of `laps`, `fuel`, `setup`, `session`, `flags`, `safety_car`, `penalties`, `race`, `rivals`, `car`, `spotter`, `test`, `other`; Pitwall stores a missing or unknown one as `other`):

| Field | Type | In | Meaning |
|---|---|---|---|
| `text` | string, or a plural object (section 5) | every catalog | The text with `{name}` placeholders (section 4). |
| `params` | object: name → kind | the source (English) | The placeholders and their kinds (section 4.2). Omitted: no placeholders. A translation's `params` is ignored: the source declares. |
| `plural` | string | the source | The `int` or `laps` param whose value picks the plural form; `text` is then a plural object. |
| `values` | object: param → (value → text) | source; a translation may translate them | The texts of an `enum` param's values (section 4.2). |
| `forms` | object: param → `m`, `f` or `n` | a translation | The grammatical gender of a spoken number param in this language (section 7.3), e.g. Spanish `"forms": {"n": "f"}` before a feminine noun. |
| `note` | string | the source | For the translator: where the text appears, what a param holds. Never shown. |
| `source_hash` | string | a translation | The source message's hash when it was translated (section 8). |

Rules for all texts: plain text, no markup (rich text is built by the code around the
message), no control characters (U+0000-U+001F, U+007F); a text written to be **spoken** (any
key under `speech.`) is at most 200 characters as written (placeholders count as written; the limit for spoken texts).

## 3. Keys

- Pattern: `^[a-z][a-z0-9_]{0,63}(\.[a-z0-9_]{1,64}){0,5}$`. An app's own catalog uses at least
  two segments, `<area>.<thing>[.<part>]` (`settings.language.title`, `tray.quit`,
  `wizard.agent.check.failed`). A connector's event codes are one segment (`kind`
  pattern `^[a-z][a-z0-9_]{0,31}$`), namespaced by their catalog.
- A connector message's variant n is the key `<code>.v<n>` (n >= 1), translated like
  any key; its source is the message with that text.
- Spoken texts live under `speech.`; written (shown) texts never do. The same idea written and
  spoken is two keys.
- A key never changes meaning: a new meaning is a new key. Reworded English keeps its key (the
  hash marks old translations stale, section 8).
- Keys are always literal in code (`t("tray.quit")`), so a test can check that every key used
  exists and none is unused; a key built at run time is allowed only from a fixed table of
  literal keys in the same module.

## 4. Placeholders

### 4.1 Syntax

- `{name}`, `name` matching `^[a-z][a-z0-9_]{0,31}$`. Nothing else inside the braces (no format
  specifiers, no types: types are declared in `params`).
- `{{` and `}}` are a literal `{` and `}`. Any other `{` or `}` is an error.
- No other escaping: the file is JSON, so `"` and `\` follow JSON.
- Filling: each `{name}` is replaced by the param's rendering (4.2); the values are not parsed
  again. A spoken text's runs of spaces collapse to one and it is trimmed after filling (so an
  empty part, e.g. the sign of a zero gap, leaves no gap).
- At run time formatting never raises: a param the call did not give stays as `{name}` in the
  text (a bug the tests catch), extra arguments are ignored.

### 4.2 Param kinds

A param is declared as a kind name (`"lap": "int"`) or an object with `kind` and its options
(`{"kind": "decimal", "digits": 2}`, `{"kind": "enum", "values": ["fl", "fr", "rl", "rr"]}`).
Two renderings per kind: **written** (shown in a UI; the app formats numbers for the language,
with `QLocale` in the Qt apps; no group separators, so every app writes a number the same way) and **spoken** (from the number catalog, section 7).

| Kind | Value | Options | Written (en) | Spoken (built from) |
|---|---|---|---|---|
| `text` | string (a name, an already formatted value) | – | as is | as is |
| `int` | integer | – | `1234` (plain digits, `-` for a negative; no grouping) | number words (7.2), gender from `forms` |
| `decimal` | number | `digits` (default 1) | `0.2` (the language's decimal separator, no grouping) | whole part in words, the `decimal` pattern, then the fraction digit by digit |
| `gap` | signed seconds | – | `+0.2 s` | the `gap` pattern: `plus`/`minus` word and the value as a 1-digit decimal |
| `lap_time` | seconds | – | `1:56.590` (every language keeps the sport's notation) | the `lap_time` patterns (7.4) |
| `ordinal` | integer ≥ 1 | – | `3rd` (or the app's own notation, e.g. `P3`) | the `ordinals` table, gender from `forms` |
| `distance_m` | metres | – | `150 m` | number + unit word (7.5) |
| `speed` | km/h | – | `212 km/h` | number + unit word |
| `temperature` | °C | – | `85 °C` | number + unit word |
| `litres` | litres | – | `12.5 L` | number + unit word |
| `laps` | integer | – | `3 laps` | number + unit word |
| `enum` | one of `values` | `values` (required, the value set) | the message's `values` text | the same text |

- An app implements the written or spoken rendering of every kind **its own catalog
  declares** (its tests check it); `text` and `int` always.
- Numbers are never written into a text: they are params (so they can be spoken and
  localised).
- `enum`: the message carries `"values": {"<param>": {"<value>": "<text>"}}` for every declared
  value; a translation may give its own `values` with exactly the same value set (otherwise the
  source's are used). An undeclared value at run time renders as itself.

## 5. Plurals

- `text` may be an object of CLDR cardinal categories (`zero`, `one`, `two`, `few`, `many`,
  `other`) when the source message names its count param in `plural` (an `int` or `laps`
  param). `other` is required. One level only: no nesting, no other selectors (`enum` covers
  value texts).
- A translation may use only the categories its language's plural rules define, plus `other`.
  A category it does not give falls back to `other`.
- The category of a value comes from the language's **plural rules**, CLDR's rule strings, in
  the pack's `numbers.json` `plural_rules` (category → rule; samples after `@` are ignored).
  Built in: English, `{"one": "i = 1 and v = 0"}`. A language without `numbers.json` (or
  without `plural_rules` in it) uses its chain's next rules (section 6), English last. The
  rules of the selected language (the first along its chain) pick the forms and validate every
  pack entry of the chain.
- Rules are evaluated in the order `zero`, `one`, `two`, `few`, `many`; the first that holds
  wins; none: `other`. Supported syntax (CLDR's, the subset plural counts need): operands `n`
  `i` `v` `w` `f` `t` `e` `c`; `%` (modulo); relations `=` and `!=` with a list of values
  and ranges (`2..4,7`); `and` binds tighter than `or`. Plural values are integers, so
  `n = i = |value|` and `v = w = f = t = e = c = 0`.
- Placeholders in plural forms: each form uses only declared params; every declared param
  except the count param appears in every form; the count param may be left out of a form
  ("one tenth", "una décima").

## 6. Languages and fallback

- Tags: BCP 47 ([RFC 5646](https://www.rfc-editor.org/rfc/rfc5646)), the subset
  `^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$` after normalising case (language lower
  case, script title case, region upper case): `en`, `es`, `es-CL`, `es-419`, `zh-Hant-TW`. An
  invalid tag is treated as `en`.
- **The chain** for a tag: the tag, then the tag without its last subtag, repeatedly, then
  `en`, then the built-in English: `es-CL` → `es` → `en` → built-in; `en-GB` → `en` →
  built-in; `en` → built-in.
- **Per key:** a key's text is the first **valid** entry along the chain. A rejected entry
  (section 9) falls back individually; a pack file that cannot be used (missing, not JSON, wrong
  `schema`/`catalog`/`language`) is skipped as a whole. An app says what it skipped once per
  start (its log; a notice in the hub where it has one).
- Number words, patterns and plural rules fall back per field along the same chain (section 7).
- **The language setting** is the workspace's `<workspace>\hub\settings.json` `language`
  (written by the hub's Settings › Language, [descriptor.md](descriptor.md#languages-locales-and-the-language-setting));
  every app reads it once at start. No workspace, no file, no field or an invalid tag: `en`.
- A catalog declared by a connector names its own language: its texts are the last
  fallback for those codes, after the user's chain.

## 7. Number catalog (`numbers.json`)

The words and patterns to say numbers aloud, and the language's plural rules. Generated per
language by the user's AI agent, validated by tests per kind; the
composition rules are code, the file gives only words and patterns.

```json
{
  "schema": "stintmate-numbers/1",
  "language": "es",
  "language_name": "Español",
  "plural_rules": {"one": "n = 1", "many": "e = 0 and i != 0 and i % 1000000 = 0 and v = 0 or e != 0..5"},
  "words": {"0": "cero", "1": "uno", "...": "...", "29": "veintinueve", "30": "treinta", "40": "cuarenta", "...": "...", "90": "noventa"},
  "tens_join": "{tens} y {unit}",
  "hundreds": {"100": "ciento", "200": "doscientos", "...": "...", "900": "novecientos"},
  "exact": {"100": "cien"},
  "hundreds_join": "{hundreds} {rest}",
  "thousands": "{n} mil", "thousands_one": "mil", "thousands_join": "{thousands} {rest}",
  "thousands_gender": "m",
  "gender_forms": {"m": {"1": "un", "21": "veintiún"}, "f": {"1": "una", "21": "veintiuna", "200": "doscientas"}},
  "plus": "más", "minus": "menos",
  "decimal": "{whole} coma {fraction}",
  "ordinals": {"default": {"1": "primero", "2": "segundo"}, "f": {"1": "primera", "2": "segunda"}},
  "units": {"laps": {"one": "vuelta", "other": "vueltas", "gender": "f"},
            "litres": {"one": "litro", "other": "litros", "gender": "m"}},
  "unit_pattern": "{value} {unit}",
  "patterns": {"lap_time": "{minutes} {seconds} {tenths}", "lap_time_under_minute": "{seconds} {tenths}",
               "seconds_padded": "cero {seconds}", "gap": "{sign} {value}"}
}
```

### 7.1 Fields

| Field | Meaning |
|---|---|
| `schema`, `language`, `language_name` | As in a catalog (`"stintmate-numbers/1"`). |
| `plural_rules` | Section 5. |
| `words` | Number → word, for every value said as one word (English: 0-19 and the tens; Spanish: 0-29 and the tens). |
| `tens_join` | A number below 100 not in `words`: `{tens}` = the word of its tens, `{unit}` = the word of its unit. |
| `hundreds`, `exact`, `hundreds_join` | 100-999: `exact` for a whole hundred said differently (`100` "cien"), otherwise the hundreds word and the rest (`{rest}` = the number below 100; none when it is 0). |
| `thousands`, `thousands_one`, `thousands_join`, `thousands_gender` | 1 000-999 999: `{n}` = the thousands said as a number (with the param's gender, or `thousands_gender` when it has none: Spanish `m`, "veintiún mil"); `thousands_one` for exactly 1 000 (and 1 000-1 999); `{rest}` = the remainder. Larger numbers are said as digits. |
| `gender_forms` | Gender (`m`, `f`, `n`) → number → word: replaces the word of that value wherever it is used (a word, the unit of a tens join, a hundreds word) when the param has that gender. |
| `plus`, `minus` | Sign words (negative numbers, gaps). |
| `decimal` | `{whole}` and `{fraction}` (the fraction's digits, each as its word, separated by spaces). |
| `ordinals` | `default` and per gender: number → ordinal word. A number not listed: said as a cardinal. |
| `units`, `unit_pattern` | Per unit kind (`laps`, `litres`, `distance_m`, `speed`, `temperature`, `seconds`): the plural forms (by the plural rules; a non-integer value takes `other`) and the noun's `gender`, which the number takes. |
| `patterns` | `lap_time`, `lap_time_under_minute`, `seconds_padded`, `gap` (7.4). |

### 7.2 Integers

`spoken_int(n, gender)`: negative → `minus` + the absolute value; `n` in `words` → that word
(or its `gender_forms` word); below 100 → `tens_join`; below 1 000 → `exact`, or
`hundreds_join` with the hundreds word and the rest; below 1 000 000 → `thousands_one` or
`thousands` with the thousands, then `thousands_join` with the rest; otherwise the digits.
The gender applies to the last word that has a gendered form (the unit, the hundreds).

### 7.3 Gender

A param's gender is chosen by the translation (`forms`) for `int` and `ordinal`, and by the
unit's `gender` for unit kinds. English has none. Example (Spanish): `laps` 21 → "veintiuna
vueltas", `litres` 21 → "veintiún litros", `int` 1 with no form → "uno".

### 7.4 Times and gaps

- `lap_time`: seconds **truncated** to tenths, as the timing screens show them (116.59 → 1:56.5;
  a tiny epsilon, 0.00001 tenths, absorbs float artefacts: 116.5999999 is 1:56.6, 116.59999 is
  1:56.5), never rounded up to the next
  second. With minutes: `lap_time` with `{minutes}`, `{seconds}` (`seconds_padded` when below
  ten) and `{tenths}`, each a cardinal; under a minute: `lap_time_under_minute`.
- `gap`: the absolute value rounded half up to one decimal, as a `decimal`; `{sign}` is `plus`
  for a positive, `minus` for a negative value, empty when the rounded value is zero.
- `decimal`: rounded half up to `digits`; a negative value starts with `minus` (unless it rounds
  to zero).

### 7.5 Unit kinds

`unit_pattern` with `{value}` (an integer value as `spoken_int` with the unit's gender, a
non-integer one as a 1-digit `decimal`) and `{unit}` (the unit's plural form for the value). A
unit missing from the chain: the value alone.

### 7.6 Fallback

Every field falls back along the language chain (section 6) and, last, to the app's built-in
English number catalog. A number catalog whose `schema` or `language` is wrong is skipped as a
whole. The words of one language are never mixed into another's composition beyond this
per-field rule (a pack should be complete: the translate task's tests check it).


## 8. Stale translations

- The **source hash** of a source message: the first 12 hexadecimal digits of the SHA-256 of
  its canonical JSON, the object `{"params": <params or {}>, "text": <text>}` plus `"values"`
  when it has values, serialised with sorted keys, no spaces (`separators=(",", ":")`), UTF-8,
  not ASCII-escaped.
- A translation stores the hash of the source it translated in `source_hash`. A translation
  whose `source_hash` is missing or differs from the current source's is **stale**: still used
  (a stale text beats English mid-race), counted and listed by the app (log once per start), and
  re-translated by the next run of the managing app's translate job (a connector's catalog too;
  not by Pitwall). This is how a changed English text, or a connector's changed event
  catalog, is recognised. The job never sends or touches a current entry, so a hand-edited
  text survives until its English changes.


## 9. Validation on load

Every app validates its built-in catalog in its tests (all rules) and every pack entry when it
loads it. An entry that fails is dropped (the chain's next entry is used) and counted; the app
logs once per file: the file, the number of dropped entries and the first keys with their
reasons. Checks, in order (the reason codes are those of the vectors):

| Reason | Check |
|---|---|
| `unknown_key` | The key exists in the built-in catalog (unknown keys are ignored). |
| `bad_text` | `text` is a non-empty string, or (only if the source has `plural`) a non-empty plural object of non-empty strings. A string for a plural source is its `other` form. |
| `control_character` | No control character in any text. |
| `bad_placeholder` | Every form parses: braces balanced, `{{`/`}}` only as escapes, names valid. |
| `placeholder_mismatch` | Without a plural: the set of placeholders equals the source's params. With a plural: each form uses only declared params and contains all but the count param. |
| `plural_missing_other` | A plural object has `other`. |
| `plural_bad_category` | Its categories are within the language's plural rules plus `other`. |
| `enum_values_mismatch` | A translation's `values` give exactly the source's value set for each enum param. |
| `bad_forms` | `forms` name declared params of kind `int` or `ordinal`, with `m`, `f` or `n`. |
| `too_long` | A `speech.` text is at most 200 characters before filling. |
| `bad_source_hash` | `source_hash`, when present, is 12 lower-case hexadecimal digits. |

A file is skipped as a whole when it cannot be read (`unreadable`), is not a JSON object
(`not_json`), or its `schema` (`bad_schema`), `catalog` (`catalog_mismatch`) or `language`
(`language_mismatch`, against its folder) is wrong; `messages` that is not an object is
`bad_schema`.

