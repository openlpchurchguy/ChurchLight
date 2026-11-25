# Fonts for the operator page

The operator page uses two open-licensed fonts (SIL Open Font License 1.1). Their files and licenses are in this folder:

| File | Font |
|---|---|
| `AtkinsonHyperlegible-Regular.ttf` | Atkinson Hyperlegible, Regular (text) |
| `AtkinsonHyperlegible-Bold.ttf` | Atkinson Hyperlegible, Bold |
| `BricolageGrotesque.ttf` | Bricolage Grotesque, the variable font from Google Fonts (headings and the ChurchLight name, Bold) |
| `OFL-AtkinsonHyperlegible.txt`, `OFL-BricolageGrotesque.txt` | Their licenses. The license requires these to stay with the font files |

Both families came from fonts.google.com (the "Download all" zips). To use different files, keep the file names above, or change the `@font-face` rules at the top of `operator.css`.
If a font file is missing the page still works and shows the system font instead.

## Fonts for slide themes

The theme editor's font list is built on 17 more open fonts so a theme looks the same on every PC, including the XSplit or OBS computer, with nothing to install. They are loaded by `static/themefonts.css` (written by `tools/bundle_fonts.py`), which the output pages link. Every file is the unmodified Regular and Bold (Anton and Atkinson: as listed) from Google Fonts, and every family's license is beside it as `OFL-<Name>.txt`. All are SIL Open Font License 1.1 (Lato, Lora, Merriweather and Playfair Display declare Reserved Font Names, so the files must not be edited or renamed and redistributed under the same name).

| Family | Used for |
|---|---|
| Open Sans, Lato, Source Sans 3, Roboto | Clean sans-serif for lyrics and Scripture |
| Nunito | Rounded and friendly (kids, welcome) |
| Atkinson Hyperlegible | Easy to read from far away (the same files as the operator page) |
| Montserrat, Poppins | Modern sans-serif |
| Oswald, Anton | Condensed and heavy, for titles |
| Merriweather, Lora, Libre Baskerville, PT Serif | Serif for Scripture and traditional services |
| Playfair Display, Cinzel | Dramatic serif for titles (Cinzel is Roman capitals) |
| Dancing Script | Script for short titles only |

To change the set: edit `FAMILIES` in `tools/bundle_fonts.py`, run it on a folder of unzipped Google Fonts downloads, then update `THEME_FONTS` in `static/themes.js`. `tests/test_theme_fonts.py` checks that the files, licenses, stylesheet and editor list agree and that every font can draw the KJV text.
