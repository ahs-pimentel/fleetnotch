# Translations

The interface ships in **Português (Brasil)**, **English**, **Español** and **Français**. By default the app follows the
system language (including the per-app language in System Settings › General › Language & Region › Applications, since the
app bundle carries an `.lproj` folder per language). Settings › General › Language switches it live, no relaunch.

## How it works

- **The key is the Portuguese text** (Portuguese is the source language). `en.json`, `es.json` and `fr.json` map each key
  to its translation. There is no `pt.json`: in Portuguese the key itself is shown.
- **Swift:** `t("Esvaziar a Lixeira")`. **Python:** `from comum import t` and `t('Esvaziar a Lixeira')`. Same tables, same rules.
- **Arguments:** `{0}`, `{1}`… in the key, filled in order: `t("{0} GB livres", livre)`. A translation may move them
  (`"{0} GB free"`, `"{0} Go libres"`) but must keep the same set of markers.
- **Plurals:** one key per form, chosen in code: `n == 1 ? t("{0} conta", n) : t("{0} contas", n)`.
- **Short ambiguous words:** `t("dia|seg")` → `"Mon"`. In Portuguese only the part after `|` is shown.
- **Values that arrive in a variable** (a status word from the probe, say) are translated where they are shown,
  `t(valor)`, and every possible value is marked once with `N_("atenção")` so the checker sees it.
- The Python side (collector, actions, quick ask, agents, email, settings helpers, demo data) gets the language from the
  app in the `FROTA_LANG` environment variable; run from a terminal it speaks Portuguese.
- `i18n/extras.txt`: keys that do not appear in a `t()` call (today, the Location prompt in `Info.plist`).

## Rules for a new string

- Wrap every word a person reads: labels, buttons, toggles, tooltips, window titles, menu items, errors, empty states,
  accessibility labels, demo data.
- Do **not** wrap identifiers, dictionary/JSON keys, notification names, SF Symbol names, paths, bundle ids, and text that
  is compared with program or system output (`'Permission denied'`, `'not logged in'`).
- Brands and technical terms stay as they are (CPU, GPU, RAM, Wi-Fi, Tailscale, SSH, macOS, Claude, Codex…).
- No `t()` inside a `static let` or a global `let` in Swift: it would freeze the language at first use. Use a computed property.
- Never glue sentence fragments (`t("Apagar") + " " + nome`); give translators the whole sentence with markers.

## Check

```sh
python3 teste_i18n.py            # every key in en/es/fr, same {markers}, no leftovers, no raw UI text in Swift
python3 teste_i18n.py --chaves   # list keys and where they are used (handy when translating)
```

## Adding a language

1. Copy `en.json` to `<code>.json` (two-letter ISO 639-1 code) and translate the values.
2. Add the code to `Idioma.suportados` and `Idioma.nomes` (the name in its own language) in the Swift file, to the
   `IDIOMAS` list in `teste_i18n.py`, and to the `.lproj` loop and `CFBundleLocalizations` in `install.sh`.
3. `python3 teste_i18n.py` must print `ok`.

Style: follow Apple's own wording for macOS places and features in each language (System Settings / Réglages Système /
Ajustes del Sistema, Remote Login, Activity Monitor…). Keep it as short as the Portuguese: much of the UI lives in a small
drawer under the notch.
