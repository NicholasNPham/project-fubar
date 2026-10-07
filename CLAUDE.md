# STAC Image ID Corruption Check

SAO10 automation. Reads an Excel list of Case Number + Image Id rows, looks
each image up in STAC (the case management web app), and marks the Exclude
column with `*` when the image is good. Corrupted images are left blank.

Owner: Nick (IT Specialist / Automation Developer, SAO10).

## Read these first

- `stac_navigation.md`: the full click path, every selector, and the
  detection rules. It is the source of truth for anything STAC-related.
  If it conflicts with your assumptions, it wins.
- PROJECT-DALYN `src/stac.py` (https://github.com/NicholasNPham/PROJECT-DALYN):
  the base for all STAC code. Reuse its `StacSession` patterns: one signed-in
  session per run, `_start_chrome` fallbacks, `_open_case_search` recovery
  for the disabled search box, JS clicks, Kendo settle pause, and the
  `DocumentProblem` / `SystemProblem` exception split.
- PCSO911 (https://github.com/NicholasNPham/PCSO911): older, same STAC
  screens. Reference only. Do not copy its per-case browser restarts or its
  email-inside-every-except pattern.

## Hard rules (never break these)

1. **STAC is read-only.** Never click Save, Delete, Edit, Upload, Add, or
   anything that changes a record. This tool only searches and reads.
2. **Never modify the input Excel file.** Write results to a new copy:
   `<original name>_checked_<YYYYMMDD_HHMMSS>.xlsx`.
3. **Never guess.** A row is only marked `*` when the Details panel's ID
   field exactly equals the row's Image Id AND a File Size field is present.
   Anything uncertain gets a blank Exclude and an explicit Status.
4. **No secrets in git.** Credentials and the STAC url live in
   `config/config.yaml`, which is gitignored. Commit `config/config.example.yaml`
   with placeholders only.
5. **No defendant names or personal data in logs or committed files.**
   Case numbers and Image Ids are fine to log.
6. Do not read `config/config.yaml` or the real input spreadsheets. Use the
   example config and the sample rows in this file.

## Detection rule (confirmed by STAC admin)

On an image's Details tab:
- `span.c-form-viewer-value[data-field-label='File Size']` present -> good -> `*`
- absent -> corrupted -> blank

Only judge after `data-field-label='ID'` equals the wanted Image Id (the
freshness check). Full details in `stac_navigation.md`.

## Output columns

Keep every original column. Fill `Exclude`. Add:

| Column | Values |
|---|---|
| Status | OK, CORRUPTED, CASE NOT FOUND, IMAGE ID NOT FOUND, SUSPECT: zero size, ERROR: <reason> |
| File Size | visible text, e.g. `16.72 MB`, or blank |
| Storage Location | e.g. `Local`, `CIP Portal Archive` |
| Checked At | timestamp |

## Input format

Sheet has headers in row 1. Only these columns matter (match header text,
case-insensitive, trimmed; never by column letter):

```
Case Number     Image Id   Exclude
CF12010742XX    15072070
CF11003819XX    15031454
CF1400494AXXS   15076194
CF1400494AXXS   15080911
```

A case number can repeat with different Image Ids. Group by case number,
search each case once, check all its Image Ids. Image Id is the unique key.

Known test values:
- 15031454 (CF11003819XX): corrupted, no File Size
- 10680257: good, File Size 16.72 MB (case number TBD from Nick)

## Stack

Python 3, Selenium 4, openpyxl, PyYAML, pytest. Windows. Chrome.

## Structure (DALYN style)

```
config/
  config.example.yaml   # committed, placeholders
  config.yaml           # gitignored, real values
src/
  excel_io.py           # read rows, group by case, write output copy
  stac.py               # StacSession: sign in, search case, filter image, read Details
  checker.py            # decides Status/Exclude from Details fields
  logger.py
  models.py             # dataclasses: ImageRow, CheckResult
  exceptions.py         # DocumentProblem, SystemProblem
tests/
  test_checker.py       # pure logic, no browser
  test_excel_io.py
logs/                   # gitignored
main.py
```

## Coding standards (NASA Power of 10, adapted, same as PCSO911/DALYN)

- Single-responsibility functions
- Named constants for every selector and timeout, no magic strings
- Type hints and docstrings (Args/Returns/Raises) on every function
- Raise explicit exceptions, never return None to signal failure
- No globals
- Element-based waits only. The one allowed `time.sleep` is the Kendo
  settle pause, as in DALYN
- Comments explain WHY, the way DALYN's do

## Run behavior

- `--limit N` to process only the first N rows (testing)
- `--headed` to watch the browser, plus DALYN's `action_pause` config
- Save output progress every N rows and support resuming from a partial
  output file
- End-of-run summary: totals per Status

## Working with Nick

- Nick cannot share STAC with you directly. When a selector or behavior is
  unknown, say exactly what HTML or test he needs to grab, don't invent it.
- Plan before implementing. Small reviewable steps.
- Be direct. Push back on bad ideas.
