# STAC Navigation Map: Image ID PDF Check

Base code: PROJECT-DALYN `src/stac.py` (StacSession / StacRunner).
Reuse its sign-in, Chrome startup, search box recovery, and exception
pattern (DocumentProblem / SystemProblem). Do NOT reuse anything from
upload, matrix dialog, or save. This tool is read-only in STAC.

Legend:
- KNOWN = already proven in DALYN / PCSO911 production
- TODO = Nick fills this in

---

## Session rules (from DALYN, KNOWN)

- One browser session for the whole run. Do not sign in per case.
- Credentials and STAC url in config.yaml (DALYN style), not key.py.
- Chrome startup: config path, then Selenium Manager, then webdriver-manager
  (DALYN `_start_chrome`).
- Kendo drops clicks that land too fast. 1 second settle after Kendo
  interactions (DALYN `KENDO_PAUSE_SECONDS`).
- Click buttons through JavaScript (`execute_script("arguments[0].click();")`).
  The real buttons sit under overlays often enough that native clicks fail.

---

## Step 1: Sign in (KNOWN)

| What | Selector |
|---|---|
| "Authenticate Using" dropdown | `By.ID, "LoginProvider"`, select value `"Local"` |
| Username field | `By.ID, "Username"` |
| Password field | `By.ID, "Password"` |
| Submit button | `By.ID, "submitLogin"` (JS click) |
| Proof sign-in worked | `By.CSS_SELECTOR, "[data-menuid='incident']"` (Cases sidebar) |

Note: STAC re-renders the same page on a bad password. The Cases sidebar
appearing is the only reliable proof of success.

---

## Step 2: Open case search (KNOWN, one change)

| What | Selector |
|---|---|
| Cases sidebar button | `By.CSS_SELECTOR, "[data-menuid='incident']"` |
| Search criteria dropdown | `By.CSS_SELECTOR, "button[role='button'][aria-label='select']"` (JS click) |
| Criteria listbox | `By.ID, "incidentsSearchMainCriteria_listbox"` |
| **Case Number option** | `//ul[@id='incidentsSearchMainCriteria_listbox' and not(contains(@style,'display: none'))]//span[@class='k-list-item-text' and text()='Case Number']` (JS click) |

CHANGE FROM DALYN: pick the Case Number option instead of UCN. (CONFIRMED)

Option HTML (captured 2026-10-06):
```html
<li tabindex="-1" role="option" unselectable="on" class="k-list-item k-selected k-focus"
    aria-selected="true" data-offset-index="4" id="5d4be581-73a4-4f63-9dde-50141ea66005">
  <span class="k-list-item-text">Case Number</span>
</li>
```

Selector notes:
- Match on the exact text "Case Number". Exact `text()=` match, not
  `contains()`, so it can never hit "Case Id" or "CSECN".
- Do NOT use the `id` attribute. It is a GUID and changes per page load.
- Do NOT use `data-offset-index="4"`. If STAC adds or reorders options the
  index shifts and the script silently searches by the wrong criteria.
- Case Number showed as already selected (`k-selected`, `aria-selected="true"`)
  when this was captured, so it may be the default. Select it explicitly
  anyway. STAC remembers the last criteria used, and if the same account
  ran DALYN or PCSO911 it may be sitting on UCN.
- After selecting, verify it took: read the dropdown's displayed value (or
  `aria-selected="true"` on the Case Number `li`) before searching. A case
  number typed into the wrong criteria just returns "no records", which
  would look like CASE NOT FOUND for every row.

Other options in the list, for reference: Keyword, My last viewed cases,
Tags, My open cases, Case Number, Name (Last, First), Agency Report Number,
CSECN, Check Number, Check Account Number, Citation Number, Dept. of
Juvenile Justice Number, My Cases with Court 12 Days, My Cases Active,
My Cases Assigned Last 14 Days, Name Without Punctuation (Last, First),
UCN, Directory ID, Case Id.

Known gotchas (from DALYN `_open_case_search`):
- STAC remembers the criteria once set, so the dropdown only needs setting
  the first time. Track it with a flag, reset the flag after any reload.
- After a case is opened, STAC leaves the search box on the page but
  DISABLED. Check `is_enabled()` and `is_displayed()`. If not usable,
  reload the STAC url and set the criteria again.

---

## Step 3: Run the search (KNOWN)

| What | Selector |
|---|---|
| Search field | `By.ID, "incidentsSearchMainSearchValue"` (clear, click, type) |
| Search button | `By.ID, "incidentsSearchMainButton"` (JS click) |
| No results | `By.CSS_SELECTOR, ".k-grid-norecords-template"` |
| Result row (defendant name cell) | `By.CSS_SELECTOR, "td[data-original-column-name='Def_Name'] span.k-button-text"` |

Wait for EITHER no-results OR a result row (DALYN `_search_ucn` lambda wait).

- No results -> record "CASE NOT FOUND" in Status, skip all image IDs for
  that case. Do NOT mark Exclude.
- TODO: Can a Case Number search return more than one row? (e.g. co-defendants,
  CF1400494AXXS with the trailing letters)
  `[ ]`
- TODO: If yes, how do you pick the right one?
  `[ ]`
- TODO: Does clicking the row open the case, or does it go straight to the
  case page? (DALYN goes straight to the Images tab after the search, so
  probably straight to the case.)
  `[ ]`

No defendant name check in this project. There is no second source to
compare against.

---

## Step 4: Open the Images tab (KNOWN)

| What | Selector |
|---|---|
| Images tab | `By.ID, "incidentsTab-tab-3"` (JS click) |
| Images tab content panel | `By.ID, "incidentsTab-3"` |
| Image tiles (all) | `By.CSS_SELECTOR, "#image-manager-listview-name .cipimage"` |
| Selected tile | tile with class `k-selected` |

---

## Step 5: Find the right Image ID (PARTLY KNOWN)

### The Images tab search box (captured 2026-10-06)

```html
<div class="k-toolbar-item" aria-keyshortcuts="Enter" toolbaritemtype="ToolBarSearchBox"
     autoclearfiltervalue="true"
     linkedcomponents="image-manager-grid-name,image-manager-listview-name"
     data-filter-columns="{&quot;image-manager-listview-name&quot;:[&quot;Image_Type&quot;,&quot;Original_File_Name&quot;,&quot;Name&quot;,&quot;Comment&quot;,&quot;Image_Sub_Type_Description&quot;]}"
     data-user-config-record-name="image_search_box" linkedgrid="image-manager-grid-name"
     data-uid="50e0a990-e323-4e6a-ac22-43217c97a53d"
     id="pagesImagesIndexToolbar_searchBox_877732302" ...>
  <span class="k-searchbox k-input ...">
    <span class="k-input-icon k-icon k-font-icon k-i-search"></span>
    <input id="pagesImagesIndexToolbar_searchBox_877732302_search_box"
           placeholder="Search..." class="k-input-inner search-box grid-toolbar-search-box" type="search">
    <input id="pagesImagesIndexToolbar_searchBox_877732302_composit_search_box"
           placeholder="Content..." class="k-input-inner composit-search-box composit-divider" type="search">
  </span>
  <i id="info-icon" class="k-icon k-font-icon k-i-information" title="Show content search info"></i>
</div>
```

Selector for the box Nick types the Image ID into:
`By.CSS_SELECTOR, "input.search-box.grid-toolbar-search-box[placeholder='Search...']"`

Selector notes:
- Do NOT use the id. `_877732302` is a random number and changes per load.
- Do NOT use `id$='_search_box'`. The "Content..." box's id also ends in
  `_search_box` (`..._composit_search_box`), so that would hit the wrong
  input. The `search-box` class token only exists on the right one.
- Never type into the "Content..." box. That is a full-text content search,
  not the image list filter.
- `aria-keyshortcuts="Enter"`: press Enter after typing. Clear with real
  keys (Ctrl+A, Delete), not `.clear()`. DALYN learned this the hard way in
  the Type/Subtype dialog: Kendo filters on key events, and `.clear()` or
  typing without Enter left the old results showing.
- `autoclearfiltervalue="true"`: the filter likely resets when you leave the
  tab or case. Do not assume it persists.

### RED FLAG: Image ID may not be a searchable column

`data-filter-columns` lists what the search box filters the tile list
(`image-manager-listview-name`) on: Image_Type, Original_File_Name, Name,
Comment, Image_Sub_Type_Description. **Image ID is not in that list.**

It also filters a linked grid view (`image-manager-grid-name`) whose
columns are not listed here, so the grid might include Image ID. Unknown.

Even if typing the ID does narrow the list, it is a text "contains" filter.
15072070 could match a filename or comment containing those digits. So
filtering alone is never proof. The script must confirm the remaining
tile/row really IS that Image ID before checking its preview.

- CONFIRMED (Nick, 2026-10-06): typing a known Image ID in "Search..." and
  pressing Enter narrows the list to exactly that one image. So the search
  box is a working path, likely via the grid's columns or because the ID is
  part of a listed field (e.g. Name). Still verify the result, see below.

Search box flow:
1. Clear "Search..." (Ctrl+A, Delete), type the Image ID, press Enter.
   (Nick: Enter is not required, the list filters as you type, but press it
   anyway to be sure.)
2. Wait for the list to settle (tile count changes, Kendo settle pause).
3. Exactly 1 tile -> verify it is the right Image ID, then open preview.
4. 0 tiles -> Status "IMAGE ID NOT FOUND".
5. 2+ tiles -> Status "ERROR: search matched N images", never pick one.
6. Clear the box before the next Image ID on the same case.

How to verify step 3 is still open: need either the tile HTML (TODO below)
or the dataSource field list (console snippet below) to know where the
Image ID lives on the record.
- TODO: Is there a grid/list view toggle on the Images tab? If so, does the
  grid view show an Image ID column? `[ ]`

### Preferred approach: read Kendo's data, not the screen

The tiles are a Kendo ListView (`#image-manager-listview-name`) and there
is a Kendo Grid (`#image-manager-grid-name`). Both hold every image's
record in their dataSource, including fields that are never displayed.
DALYN already drives Kendo widgets this way through jQuery
(`PREPARE_MATRIX_GRID_JS`, `SELECT_MATRIX_ROW_JS`).

First thing to do during exploration: on the Images tab of a real case, run
in the browser console (or via execute_script) and report back:

```javascript
var lv = $('#image-manager-listview-name').data('kendoListView');
var items = lv ? lv.dataSource.data() : [];
JSON.stringify({count: items.length, fields: items.length ? Object.keys(items[0].toJSON()) : []});
```

(Same for `$('#image-manager-grid-name').data('kendoGrid')`.)

If one of those fields is the Image ID (something like `Image_Id`, `ImageId`,
`Id`), then the reliable path is:
1. Find the record whose Image ID field equals the wanted ID (exact match).
2. Find its tile via the record's `uid` (`.cipimage[data-uid='<uid>']`) and
   click it, or use `lv.select(...)`.
3. Not found in dataSource = IMAGE ID NOT FOUND. No guessing.

Caveat: if the list is paged or virtual-scrolled, dataSource may only hold
the current page. Check `dataSource.total()` against `data().length`, and
unpage the way DALYN's `PREPARE_MATRIX_GRID_JS` does if needed.

The search box becomes the fallback, not the main path.

### Tiles

- TODO: Is the Image ID visible on the tile? Where? (text, tooltip, hover)
  `[ ]`
- TODO: outerHTML of ONE full tile (the whole `.cipimage` element), ideally
  one whose Image ID you know:
  ```html
  [paste]
  ```
  Look for the ID hiding in an attribute like `data-id`, `data-uid`,
  `data-imageid`, `id="..."`. Even if it is not shown on screen it may be
  in the HTML.
- TODO: Does the tile list scroll or page if a case has many images?
  `[ ]`
- TODO: Is there a list/grid view toggle that shows Image ID as a column?
  `[ ]`

Image ID not found on the case -> Status "IMAGE ID NOT FOUND". Do NOT
mark Exclude.

---

## Step 6: Open the preview (PARTLY KNOWN)

Clicking a tile loads it into the preview pane on the right.

| What | Selector |
|---|---|
| Click the tile | JS click on the matching `.cipimage` |
| Preview iframe | `By.CSS_SELECTOR, "#imageTabPageSplitterRightPane iframe.iframe-document"` |
| Preview loaded signal | iframe `src` contains `"web/viewer.html?file="` |

IMPORTANT: `web/viewer.html?file=` means STAC uses **PDF.js** (Mozilla's
PDF viewer). "Invalid or corrupted PDF file." is PDF.js's own standard
error text. Two consequences:

1. The error and the rendered pages live INSIDE the iframe. Selenium must
   `switch_to.frame(...)` before looking, and `switch_to.default_content()`
   after.
2. The `file=` parameter in the iframe src is the URL of the PDF itself.
   Possible v2: fetch it with the session cookies and validate the bytes
   with PyMuPDF instead of reading the viewer. Investigate only, do not
   build yet.

- TODO: When you click a DIFFERENT tile, does the iframe src change to a new
  `file=` value? (this is how we know the preview actually switched and we
  are not reading the previous document)
  `[ ]`
- TODO: One full iframe src value (redact if it has anything sensitive):
  `[ ]`

---

## Detection rules (CONFIRMED by Nick and STAC admin, 2026-10-06)

Production uses Signal 2 (Details tab) ONLY. Signal 1 is kept below for
reference in case the preview is ever needed again. Ignore the
"Combining them" table and `check_mode` idea, superseded.

**Signal 1: Preview**
- Corrupted: the phrase "Invalid or corrupted PDF file." (or "The file
  cannot be displayed at this time.") appears ANYWHERE. Search the main
  page AND inside the preview iframe. `driver.page_source` does NOT include
  iframe contents, so switch into the iframe and check there too.
- Good: the previewer actually rendered the PDF. "The iframe src got set"
  is NOT enough, because the src is set for corrupted files too. Needs a
  positive signal from inside the viewer (e.g. PDF.js `#viewer .page`
  with a drawn `canvas`). TODO: confirm with good_preview HTML.

**Signal 2: Details tab (PRIMARY, captured 2026-10-06)**

Details tab:
```html
<li class="k-tabstrip-item k-active" data-tab-item-name="tabDetailContentName"
    role="tab" aria-selected="true" aria-controls="detailTabStripName-2"
    id="detailTabStripName-tab-2">
  <span class="k-link"><span class="k-link-text">Details</span></span>
</li>
```
- Click: `By.CSS_SELECTOR, "li[data-tab-item-name='tabDetailContentName']"` (JS click)
- Prefer `data-tab-item-name` over `id="detailTabStripName-tab-2"`: the `-2`
  is a position index and shifts if STAC adds a tab.
- Opened = that `li` has `aria-selected="true"`.

Details panel: `div[role='tabpanel']` with `aria-labelledby` = the tab's id
(`#detailTabStripName-2` today). Inside it, every field is:
```html
<div class="c-form-viewer-item col-12 pb-2">
  <label class="form-label fw-bold pe-2">ID: </label>
  <span class="c-form-viewer-value  pb-2" data-copy-value-base64="MTUwMzE0NTQ="
        data-field-label="ID">15031454<button ... title="Copy to clipboard">...</button></span>
</div>
```

Read any field by label:
`span.c-form-viewer-value[data-field-label='<Label>']`
Read its value by base64-decoding `data-copy-value-base64` (clean value, no
button text), falling back to `.text.strip()`.

Fields seen on the CORRUPTED example (Image ID 15031454, case
CF11003819XX): ID, Date, Type, Sub Type, Storage Location, File Name,
Redacted, Linked, Shared, Reviewed By, Analysis Sent, Analysis Complete,
Discovery, Discovery Status, User/Date Created, User/Date Modified.
**No "File Size".** Storage Location was "Local".

**This also solves Image ID verification.** The `ID` field is the Image ID.
Before judging anything, read `data-field-label='ID'` and require it to
equal the wanted Image ID exactly. No need for tile HTML.

Rules:
1. Wait for the Details panel to show `ID` == wanted Image ID. This is the
   freshness check. "File Size missing" is an ABSENCE signal, and absence
   is also what a half-loaded or stale panel (still showing the previous
   image) looks like. Never judge File Size until the ID matches.
2. ID matches, `data-field-label='File Size'` present -> `*`, Status OK.
3. ID matches, `File Size` absent -> blank, Status CORRUPTED.
4. ID never matches within timeout -> blank, Status "ERROR: details did not
   load for this image".

GOOD example (Image ID 10680257, captured 2026-10-06): same fields plus
```html
<div class="c-form-viewer-item col-12 pb-2">
  <label class="form-label fw-bold pe-2">File Size: </label>
  <span class="c-form-viewer-value  pb-2" data-copy-value-base64="MTYuNzI="
        data-field-label="File Size">16.72 MB<button ...>...</button></span>
</div>
```
- CONFIRMED label: `data-field-label="File Size"`.
- The base64 value is the bare number (`16.72`), the visible text adds the
  unit (`16.72 MB`). Read both; log the visible text.
- Storage Location on this one: "CIP Portal Archive". The corrupted
  example was "Local". See the confound note below.

### RESOLVED: File Size vs Storage Location

STAC admin confirmed (2026-10-06): missing File Size is a 100% reliable
indicator that the image is corrupted. Storage Location is not a factor.

**Details-only is the production method.** Preview checking is out of
scope. No iframe, no PDF.js, no preview memory leak.

Still log File Size and Storage Location per row in the output. Costs
nothing, and makes any future oddity easy to spot.

Extra rule: File Size present but 0 (or 0.00) -> Status "SUSPECT: zero
size", leave Exclude blank, human checks.
- TODO: After filtering to the one tile, do you have to click the tile
  before Details shows its info, or is it auto-selected? `[ ]`
- TODO: Does the Details tab stay open when you search the next Image ID,
  or does it flip back to the preview? `[ ]`

UNVERIFIED note retired: STAC admin confirmed the File Size rule (see
RESOLVED section). Preview check not needed.

**Combining them**

| Preview | Details | Exclude | Status |
|---|---|---|---|
| rendered | has File Size | `*` | OK |
| error phrase | no File Size | blank | CORRUPTED |
| disagree | disagree | blank | MISMATCH (human checks) |
| timeout / neither | any | blank | ERROR: reason |

If the test run shows the two signals always agree, Details-only becomes
the production mode: no PDF.js rendering, no iframe switching, no preview
memory leak, much faster. Make the mode a config option
(`check_mode: both | preview | details`).

## RESULT A: Good PDF (TODO)

- TODO: What it looks like:
  `[ ]`
- TODO: outerHTML of something INSIDE the iframe that only exists when the
  PDF rendered (PDF.js usually has `div.page` elements inside
  `#viewer`, with `canvas` elements once drawn):
  ```html
  [paste]
  ```
- Screenshot: `good_preview.png`
- Known good Image ID: `[ ]` (case `[ ]`)

Action: put `*` in Exclude, Status "OK".

## RESULT B: Corrupted PDF (TODO)

- Text: "The file cannot be displayed at this time." / "Invalid or corrupted PDF file."
- TODO: Are those two lines in the same element or different ones? Is the
  first line STAC's and the second PDF.js's?
  `[ ]`
- TODO: outerHTML of the element(s) holding the text:
  ```html
  [paste]
  ```
- TODO: Inside the iframe? `[ ]`
- Screenshot: `corrupted_preview.png`
- Known corrupted Image ID: `[ ]` (case `[ ]`)

Action: leave Exclude blank, Status "CORRUPTED".

## RESULT C: Neither (handled by code)

Timeout, wrong page, iframe never switched, neither A nor B signal found.
Action: leave Exclude blank, Status "ERROR: <reason>". Never guess.

---

## Step 7: Next image / next case (PARTLY KNOWN)

- Next image on the SAME case: click the next tile, wait for the iframe src
  to change, check again. No need to re-search.
- Next CASE: back to Step 2. DALYN's `_open_case_search` handles the
  disabled search box by reloading.

KNOWN BUG (from PCSO911 `remove_preview_iframe`): the PDF.js preview
iframe's resize/scroll handler leaks memory across every document viewed in
a case. PCSO911 views one preview per case. This tool views MANY. Plan for
it: reload the page between cases at minimum, and consider restarting the
browser every N cases (configurable).

- TODO: anything else that happens when switching tiles quickly?
  `[ ]`

---

## Weird stuff I've seen (TODO)

- `[ ]`
