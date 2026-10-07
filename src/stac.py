"""Drives STAC with Selenium: sign in, find a case, read each image's preview.

Ported from PROJECT-DALYN src/stac.py, which took it from PCSO911. Kept:
one signed-in session per run, the Chrome startup fallbacks, the disabled
search box recovery, JS clicks and the Kendo settle pause. Dropped: upload,
the Type/Subtype matrix, Save, and the defendant name checks.

STAC is read-only here. Nothing in this file clicks Save, Edit, Delete,
Upload or Add. It searches, opens tabs, selects an image row and reads.

Selectors and the detection rule are documented in stac_navigation.md.
"""

import time
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from checker import decide, url_has_image_id
from exceptions import DocumentProblem, FubarError, SystemProblem
from logger import get_logger
from models import PreviewReading, Status

logger = get_logger(__name__)

# Seconds. Defaults only; config.yaml overrides.
DEFAULT_WAIT_TIMEOUT = 10
# How long a preview may take to load or show its error once it is this
# image's. A good PDF loaded in 0.5s on 2026-10-06; this is the ceiling, and
# corrupted images whose message is not found pay all of it.
DEFAULT_PREVIEW_TIMEOUT = 10

# Kendo rebuilds widgets after each interaction and drops a click that lands
# too soon after the previous one. Found the hard way in PCSO911.
KENDO_PAUSE_SECONDS = 1

# Headless Chrome has no screen to maximize to, and STAC's layout collapses
# the Images tab splitter at small widths.
HEADLESS_WINDOW_SIZE = "1920,1080"

# Sign-in page
LOGIN_PROVIDER_SELECT_ID = "LoginProvider"
LOGIN_PROVIDER_LOCAL = "Local"
USERNAME_FIELD_ID = "Username"
PASSWORD_FIELD_ID = "Password"
SUBMIT_LOGIN_BUTTON_ID = "submitLogin"

# Present only once signed in, so it doubles as the proof that sign-in worked.
CASES_SIDEBAR_CSS = "[data-menuid='incident']"

# Case search. Exact text match on "Case Number", never contains(), so it can
# never hit "Case Id"; never the li id or data-offset-index, which change.
SEARCH_CRITERIA_DROPDOWN_CSS = "button[role='button'][aria-label='select']"
SEARCH_CRITERIA_CASE_NUMBER = "Case Number"
SEARCH_CRITERIA_OPTION_XPATH = (
    "//ul[@id='incidentsSearchMainCriteria_listbox' and not(contains(@style,'display: none'))]"
    "//span[@class='k-list-item-text' and text()='Case Number']"
)
SEARCH_CRITERIA_SELECTED_XPATH = (
    "//ul[@id='incidentsSearchMainCriteria_listbox']/li[@aria-selected='true']"
)
SEARCH_FIELD_ID = "incidentsSearchMainSearchValue"
SEARCH_BUTTON_ID = "incidentsSearchMainButton"
NO_RECORDS_CSS = ".k-grid-norecords-template"
# Counted, never read: the cell holds the defendant name.
CASE_RESULT_ROW_CSS = "td[data-original-column-name='Def_Name'] span.k-button-text"

# Images tab
IMAGES_TAB_ID = "incidentsTab-tab-3"
IMAGES_PANEL_ID = "incidentsTab-3"
# The "Search..." filter box. The class token search-box is only on this one;
# the "Content..." full-text box next to it must never be typed into.
IMAGE_FILTER_CSS = (
    f"#{IMAGES_PANEL_ID} input.search-box.grid-toolbar-search-box[placeholder='Search...']"
)

# The Images tab opens in thumbnail view, which comes up empty here. View >
# Details switches to the grid, which the filter works on. Matched on title and
# data-id: the ids and data-uids are GUIDs that change per load. The menu
# popup is not scoped to the panel because Kendo appends popups to <body>.
IMAGE_VIEW_BUTTON_CSS = f"#{IMAGES_PANEL_ID} button[data-role='dropdownbutton'][title='View']"
IMAGE_VIEW_GRID_ITEM_CSS = "li[role='menuitem'][data-id='grid']"
IMAGE_GRID_ID = "image-manager-grid-name"
IMAGE_LISTVIEW_ID = "image-manager-listview-name"
# Standard Kendo grid markup: data rows carry data-uid, the no-records row
# does not. Not yet confirmed against STAC's HTML; the probe reports counts.
IMAGE_ROW_CSS = f"#{IMAGE_GRID_ID} tbody tr[data-uid]"
IMAGE_GRID_NO_RECORDS_CSS = f"#{IMAGE_GRID_ID} .k-grid-norecords"
KENDO_SELECTED_CLASS = "k-selected"

# Selects a grid row through Kendo's API rather than a click, and fires change
# so STAC's handler loads that image into the right-hand pane.
SELECT_GRID_ROW_JS = r"""
var row = arguments[0];
var gridEl = row.closest ? row.closest('.k-grid') : null;
var grid = (gridEl && window.jQuery) ? window.jQuery(gridEl).data('kendoGrid') : null;
if (!grid) { return false; }
grid.clearSelection();
grid.select(row);
grid.trigger('change');
return true;
"""

# Named cells of one grid row, found by header text, so a reordered grid
# cannot shift which column is read. Only non-personal columns are ever asked
# for: file name and user columns can carry names.
GRID_ROW_CELLS_JS = r"""
var row = arguments[0], wanted = arguments[1];
var grid = row.closest ? row.closest('.k-grid') : null;
if (!grid) { return null; }
var headers = Array.prototype.map.call(grid.querySelectorAll('thead th'), function (th) {
  return (th.textContent || '').trim();
});
var out = {};
wanted.forEach(function (name) {
  var at = headers.indexOf(name);
  out[name] = (at >= 0 && row.cells[at]) ? (row.cells[at].textContent || '').trim() : '';
});
return out;
"""
GRID_IMAGE_ID_COLUMN = "image id"
GRID_FILE_SIZE_COLUMN = "file size MB"
GRID_STORAGE_COLUMN = "storage location"

# Visible grid rows whose image id cell is exactly the wanted Id, in one round
# trip. Returns null, not [], when the column is missing, so that case can be
# told apart from "no such image".
EXACT_ROWS_JS = r"""
var gridEl = document.getElementById(arguments[0]), id = arguments[1], column = arguments[2];
if (!gridEl) { return []; }
var headers = Array.prototype.map.call(gridEl.querySelectorAll('thead th'), function (th) {
  return (th.textContent || '').trim();
});
var at = headers.indexOf(column);
if (at < 0) { return null; }
var out = [];
gridEl.querySelectorAll('tbody tr[data-uid]').forEach(function (tr) {
  if (tr.offsetParent === null) { return; }
  var cell = tr.cells[at];
  if (cell && (cell.textContent || '').trim() === id) { out.push(tr); }
});
return out;
"""

# Seconds. How long a filter result may take to settle beyond the Kendo
# pause: a row still rendering, or the old list not yet replaced.
FILTER_SETTLE_TIMEOUT = 3

# Right-hand pane. Matched on data-tab-item-name, not the li id, whose index
# shifts if STAC adds a tab. Strip seen 2026-10-06: Preview, Details, Log.
PREVIEW_TAB_CSS = "li[data-tab-item-name='tabDetailContentPreviewName']"
PREVIEW_PANE_CSS = "#imageTabPageSplitterRightPane"
PREVIEW_IFRAME_CSS = f"{PREVIEW_PANE_CSS} iframe.iframe-document"

# Phrases that mean the preview could not show the file. The first is PDF.js's
# own error text, the second STAC's. Searched for, never logged.
PREVIEW_ERROR_PHRASES = ["Invalid or corrupted PDF file.", "The file cannot be displayed at this time."]

ERROR_PREVIEW_NOT_SWITCHED = "preview did not switch to this image"

# Runs inside the preview iframe. Three things, all needed before judging:
#   href         the iframe's own document URL, which stac.py checks for this
#                exact Image Id. The src attribute changes before the old
#                document is replaced, so the old image's "loaded" could
#                otherwise be read as this one's. Compared, never logged.
#   pdf_loaded   PDF.js's pdfDocument is set only when a PDF actually parsed,
#                a positive signal rather than the absence of an error.
#   phrases      textContent, not innerText, so the message is found even
#                while the pane is hidden. Searched for, never returned.
#   load_failed  PDF.js's own verdict on this document's load. The hook lives
#                on the iframe's window, which is new for every image, so it
#                cannot carry over from the previous one the way STAC's pane
#                message does. Only memory in this browser tab; nothing in
#                STAC is touched.
PREVIEW_STATE_JS = r"""
var phrases = arguments[0];
var text = document.body ? (document.body.textContent || '') : '';
var app = window.PDFViewerApplication;
if (app && app.pdfLoadingTask && app.pdfLoadingTask.promise && !window.__fubarHooked) {
  window.__fubarHooked = true;
  app.pdfLoadingTask.promise.then(
    function () { window.__fubarLoad = 'ok'; },
    function () { window.__fubarLoad = 'failed'; }
  );
}
return {
  href: location.href,
  load_failed: window.__fubarLoad === 'failed',
  pdf_loaded: !!(app && app.pdfDocument),
  pages: (app && app.pagesCount) || 0,
  error_phrases: phrases.filter(function (p) { return text.indexOf(p) !== -1; })
};
"""

# STAC's own message sits in the pane around the iframe (confirmed 2026-10-06:
# both phrases came from the pane, none from inside the iframe), and STAC
# leaves it there when the next image on the same case is selected.
#
# So messages already on screen are marked before an image is selected, and
# only unmarked ones count. The mark is an attribute on this browser's copy of
# the page; nothing is sent to STAC. If STAC reuses the same element for the
# next message, it stays marked and is missed: the row then rests on PDF.js's
# own verdict, or comes out ERROR, never a wrong verdict.
MARK_PANE_MESSAGES_JS = r"""
var pane = document.querySelector(arguments[0]), phrases = arguments[1];
if (!pane) { return 0; }
var walker = document.createTreeWalker(pane, NodeFilter.SHOW_TEXT), node, marked = 0;
while ((node = walker.nextNode())) {
  var t = node.nodeValue || '';
  if (phrases.some(function (p) { return t.indexOf(p) !== -1; }) && node.parentElement) {
    node.parentElement.setAttribute('data-fubar-seen', '1');
    marked += 1;
  }
}
return marked;
"""

# Phrases in the pane that are not inside an element marked as already seen.
PANE_PHRASES_JS = r"""
var pane = document.querySelector(arguments[0]), phrases = arguments[1];
if (!pane) { return []; }
var walker = document.createTreeWalker(pane, NodeFilter.SHOW_TEXT), node, found = {};
while ((node = walker.nextNode())) {
  if (node.parentElement && node.parentElement.closest('[data-fubar-seen]')) { continue; }
  var t = node.nodeValue || '';
  phrases.forEach(function (p) { if (t.indexOf(p) !== -1) { found[p] = true; } });
}
return Object.keys(found);
"""

# Probe only: the image grid's column headers. Headers only, never cells.
GRID_HEADERS_JS = r"""
var grid = document.getElementById('image-manager-grid-name');
if (!grid) { return null; }
return Array.prototype.map.call(grid.querySelectorAll('thead th'), function (th) {
  return (th.textContent || '').trim();
});
"""

# Probe only: the first few values of the image id column, to find other
# images on a case to test with. Ids only; no other column is read.
GRID_SAMPLE_IDS_JS = r"""
var gridEl = document.getElementById('image-manager-grid-name');
if (!gridEl) { return null; }
var headers = Array.prototype.map.call(gridEl.querySelectorAll('thead th'), function (th) {
  return (th.textContent || '').trim();
});
var at = headers.indexOf(arguments[0]);
if (at < 0) { return null; }
return Array.prototype.slice.call(gridEl.querySelectorAll('tbody tr[data-uid]'), 0, 6).map(function (tr) {
  return tr.cells[at] ? (tr.cells[at].textContent || '').trim() : '';
});
"""

# Probe only: whether the grid holds every image or one page of them.
GRID_COUNTS_JS = r"""
if (!window.jQuery) { return null; }
var grid = window.jQuery('#image-manager-grid-name').data('kendoGrid');
if (!grid || !grid.dataSource) { return null; }
var ds = grid.dataSource;
return {total: ds.total(), loaded: ds.data().length, shown: ds.view().length,
        pageSize: ds.pageSize() || null};
"""


def _usable_driver_path(configured: object) -> str | None:
    """Return the configured chromedriver only if it is actually a file.

    A blank or wrong path otherwise reaches Selenium as a directory and fails
    with a message that sends everyone looking in the wrong place.

    Args:
        configured: paths.chromedriver from config, possibly blank.

    Returns:
        The path as a string, or None to let Selenium find a driver itself.
    """
    if not configured:
        return None
    path = Path(str(configured))
    if path.is_file():
        return str(path)
    logger.warning("paths.chromedriver is set to %s, which is not a file. Ignoring it.", path)
    return None


class StacSession:
    """One signed-in browser session, reused for every case in a run.

    Use as `with StacSession(config, headed) as stac:` so Chrome closes even
    when something blows up halfway.
    """

    def __init__(self, config: dict, headed: bool = False) -> None:
        """Read what is needed from config. Opens nothing yet.

        Args:
            config: Parsed config.yaml.
            headed: Show the browser window. Off by default for full runs.
        """
        stac = config["stac"]
        self.url = str(stac["url"]).rstrip("/")
        self._username = stac["username"]
        self._password = stac["password"]
        self.wait_timeout = int(stac.get("wait_timeout", DEFAULT_WAIT_TIMEOUT))
        self.preview_timeout = int(stac.get("preview_timeout", DEFAULT_PREVIEW_TIMEOUT))
        self.action_pause = float(stac.get("action_pause", 0) or 0)
        self.headed = headed
        self._chromedriver = _usable_driver_path(config.get("paths", {}).get("chromedriver"))

        self.driver: webdriver.Chrome | None = None
        self.wait: WebDriverWait | None = None
        # STAC remembers the search criteria once set, so it only needs
        # choosing after a fresh page load. Reset on every reload.
        self._criteria_set = False
        # Where STAC lands after sign-in. Reloads go here, not to the
        # configured url, which is the login page.
        self._home_url = ""
        # True once any search has run on this page. STAC disables the search
        # box after a case opens, sometimes while still reporting it enabled,
        # and a CASE NOT FOUND leaves old results on screen, so every search
        # after the first starts from a reload.
        self._case_open = False

    def __enter__(self) -> "StacSession":
        """Open and sign in.

        Returns:
            This session.
        """
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        """Close Chrome whatever happened."""
        self.close()

    # ------------------------------------------------------------ lifecycle

    def open(self) -> None:
        """Launch Chrome, load STAC, and sign in.

        Raises:
            SystemProblem: Chrome will not start, STAC will not load, or the
                credentials are refused. The run cannot continue.
        """
        logger.info("Opening STAC at %s (headed: %s)", self.url, self.headed)
        self.driver = self._start_chrome()
        self.wait = self._waiter(self.wait_timeout)
        self._criteria_set = False
        self._case_open = False

        try:
            self.driver.get(self.url)
            if self.headed:
                self.driver.maximize_window()
        except WebDriverException as error:
            self.close()
            raise SystemProblem(f"Could not load STAC at {self.url}: {error}") from error

        try:
            self._sign_in()
        except Exception:
            self.close()
            raise

    def _chrome_options(self) -> Options:
        """Chrome options: headless unless headed was asked for.

        Returns:
            The options to start Chrome with.
        """
        options = Options()
        if not self.headed:
            options.add_argument("--headless=new")
            options.add_argument(f"--window-size={HEADLESS_WINDOW_SIZE}")
        return options

    def _start_chrome(self) -> webdriver.Chrome:
        """Start Chrome, trying each way of finding chromedriver in turn.

        Config path first, then Selenium Manager, and the downloader last,
        since it needs the internet and a locked-down network often blocks it.

        Returns:
            A running Chrome.

        Raises:
            SystemProblem: If none of them work. The message lists what was tried.
        """
        attempts = []
        if self._chromedriver:
            attempts.append(("paths.chromedriver", lambda: Service(self._chromedriver)))
        attempts.append(("Selenium's own driver lookup", lambda: Service()))
        attempts.append(("downloading chromedriver", lambda: Service(ChromeDriverManager().install())))

        failures = []
        for description, build_service in attempts:
            try:
                driver = webdriver.Chrome(service=build_service(), options=self._chrome_options())
                logger.debug("Chrome started via %s", description)
                return driver
            except Exception as error:  # noqa: BLE001 - each route fails differently
                failures.append(f"{description}: {type(error).__name__}: {str(error).splitlines()[0]}")

        raise SystemProblem(
            "Could not start Chrome. Set paths.chromedriver in config.yaml to a "
            "chromedriver.exe matching your Chrome version. Tried: " + " | ".join(failures)
        )

    def _sign_in(self) -> None:
        """Fill the sign-in form and wait for the Cases sidebar.

        Raises:
            SystemProblem: If any step of sign-in does not complete.
        """
        try:
            provider = self.wait.until(EC.presence_of_element_located((By.ID, LOGIN_PROVIDER_SELECT_ID)))
            Select(provider).select_by_value(LOGIN_PROVIDER_LOCAL)
            self.wait.until(EC.element_to_be_clickable((By.ID, USERNAME_FIELD_ID))).send_keys(self._username)
            self.wait.until(EC.element_to_be_clickable((By.ID, PASSWORD_FIELD_ID))).send_keys(self._password)
            submit = self.wait.until(EC.element_to_be_clickable((By.ID, SUBMIT_LOGIN_BUTTON_ID)))
            self._js_click(submit)
        except TimeoutException as error:
            raise SystemProblem(
                f"STAC's sign-in page at {self.url} did not have the expected fields."
            ) from error

        try:
            self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, CASES_SIDEBAR_CSS)))
        except TimeoutException as error:
            # STAC re-renders the same page on a bad password, so the sidebar
            # appearing is the only reliable proof of success.
            raise SystemProblem(
                "Signed in but the Cases sidebar never appeared. Usually a wrong "
                "username or password in config.yaml."
            ) from error

        self._home_url = self.driver.current_url
        # Account name deliberately not logged.
        logger.info("Signed in to STAC")
        self._pause("signed in")

    def close(self) -> None:
        """Close Chrome. Safe to call twice, and never raises."""
        if self.driver is None:
            return
        try:
            self.driver.quit()
        except WebDriverException as error:
            logger.warning("Chrome did not close cleanly: %s", error)
        finally:
            self.driver = None
            self.wait = None

    # -------------------------------------------------------------- helpers

    def _waiter(self, timeout: float) -> WebDriverWait:
        """A wait that keeps polling when Kendo replaces an element mid-wait.

        Kendo re-renders tabs and grids freely, so an element found on one
        poll can be gone by the next. By default that StaleElementReference
        escapes the wait and crashes the run; here it just means "not yet".

        Args:
            timeout: Seconds.

        Returns:
            The wait.
        """
        return WebDriverWait(
            self.driver,
            timeout,
            ignored_exceptions=(NoSuchElementException, StaleElementReferenceException),
        )

    def _js_click(self, element: WebElement) -> None:
        """Click through JavaScript.

        STAC's buttons sit under overlays often enough that a native click is
        intercepted.

        Args:
            element: What to click.
        """
        self.driver.execute_script("arguments[0].click();", element)

    @staticmethod
    def _settle() -> None:
        """Give Kendo a beat to finish rebuilding before the next action."""
        time.sleep(KENDO_PAUSE_SECONDS)

    def _pause(self, what: str) -> None:
        """Hold after a step so a person can watch, when action_pause is set.

        Args:
            what: Step name, logged so the pause is explained.
        """
        if self.action_pause <= 0:
            return
        logger.info("  ... %s", what)
        time.sleep(self.action_pause)

    def _close_dropdown(self) -> None:
        """Press Escape so a dropdown left open cannot block the next click."""
        try:
            self.driver.switch_to.active_element.send_keys(Keys.ESCAPE)
        except WebDriverException:
            pass

    # ---------------------------------------------------------- case search

    def open_case(self, case_number: str) -> None:
        """Search for a case by Case Number and open its Images tab.

        Args:
            case_number: As in the input sheet.

        Raises:
            DocumentProblem: No such case, or the search returned several.
            SystemProblem: STAC did not respond as expected.
        """
        self._open_case_search()
        # Set before searching, so whatever happens next (a case opened, CASE
        # NOT FOUND left on screen, a failure halfway) the next case starts
        # from a fresh page instead of trusting what this one left behind.
        self._case_open = True
        self._search_case(case_number)
        # The case page keeps loading after the result appears; going straight
        # to the Images tab was judged too fast in the first real run.
        self._settle()
        self._pause(f"found {case_number}")
        self._open_images_tab(case_number)
        self._pause("images tab open")

    def _open_case_search(self) -> None:
        """Get to a usable Cases search box, whatever page we are on.

        After a case is opened STAC leaves the search box on the page but
        disabled, and the first test run showed it can still pass the
        enabled/visible check and then refuse typing. So after any opened
        case the page is reloaded first rather than trusting that check.

        Raises:
            SystemProblem: If the search box cannot be reached even after a reload.
        """
        if not self._case_open and self._try_open_case_search():
            return

        self._reload_home()
        if not self._try_open_case_search():
            raise SystemProblem("STAC's case search box is still not usable after reloading.")

    def _reload_home(self) -> None:
        """Load STAC's post-sign-in page fresh, clearing any opened case.

        Raises:
            SystemProblem: If the Cases sidebar does not come back.
        """
        logger.debug("Reloading STAC before the next search")
        try:
            self.driver.get(self._home_url or self.url)
            self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, CASES_SIDEBAR_CSS)))
        except (TimeoutException, WebDriverException) as error:
            raise SystemProblem(f"Could not get back to STAC's Cases page: {error}") from error
        self._criteria_set = False
        self._case_open = False

    def _try_open_case_search(self) -> bool:
        """One attempt at reaching a usable search box.

        Returns:
            True if the box is enabled, visible and set to Case Number.
        """
        try:
            # JS click, like every other button: a native click can be
            # intercepted by an overlay still fading out after a reload.
            self._js_click(self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, CASES_SIDEBAR_CSS))))
        except (TimeoutException, WebDriverException):
            return False

        if not self._criteria_set and not self._choose_case_number_criteria():
            return False

        # Present is not usable: STAC disables the box after a case is opened.
        try:
            field = self.wait.until(EC.element_to_be_clickable((By.ID, SEARCH_FIELD_ID)))
            return field.is_enabled() and field.is_displayed()
        except (TimeoutException, WebDriverException):
            return False

    def _choose_case_number_criteria(self) -> bool:
        """Set the search dropdown to Case Number and confirm it took.

        Confirming matters: a case number searched under the wrong criteria
        (say UCN, left over from DALYN on the same account) returns "no
        records", which would read as CASE NOT FOUND for every row.

        Returns:
            True once the dropdown's selected option is Case Number.
        """
        # STAC remembers the criteria across reloads. Re-picking an option
        # that is already selected failed in the 5-row headed run while the
        # dropdown read Case Number all along, so check before touching it.
        if self.selected_criteria() == SEARCH_CRITERIA_CASE_NUMBER:
            self._criteria_set = True
            return True

        try:
            self._js_click(self.wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, SEARCH_CRITERIA_DROPDOWN_CSS))
            ))
            self._js_click(self.wait.until(
                EC.element_to_be_clickable((By.XPATH, SEARCH_CRITERIA_OPTION_XPATH))
            ))
            self.wait.until(lambda _: self.selected_criteria() == SEARCH_CRITERIA_CASE_NUMBER)
        except (TimeoutException, WebDriverException):
            self._close_dropdown()
            # What matters is the result, not whether the clicks went through.
            if self.selected_criteria() != SEARCH_CRITERIA_CASE_NUMBER:
                logger.debug("Case Number criteria did not take; selected is %r", self.selected_criteria())
                return False

        self._settle()
        self._criteria_set = True
        return True

    def selected_criteria(self) -> str:
        """The search dropdown's selected option text, or "" if unreadable.

        Read with textContent because the listbox is hidden once the dropdown
        closes, and Selenium's .text is empty for hidden elements.

        Returns:
            E.g. "Case Number".
        """
        try:
            option = self.driver.find_element(By.XPATH, SEARCH_CRITERIA_SELECTED_XPATH)
            return (option.get_attribute("textContent") or "").strip()
        except WebDriverException:
            return ""

    def _search_case(self, case_number: str) -> None:
        """Run the search and require exactly one result.

        Args:
            case_number: The case to find.

        Raises:
            DocumentProblem: No records, or more than one row.
            SystemProblem: The search could not run or never finished.
        """
        # Results from the previous search may still be on the page (a CASE
        # NOT FOUND does not open a case, so no reload clears them). Waiting
        # for the old result to go stale stops the next case being judged on
        # the last one's grid.
        previous = (
            self.driver.find_elements(By.CSS_SELECTOR, NO_RECORDS_CSS)
            + self.driver.find_elements(By.CSS_SELECTOR, CASE_RESULT_ROW_CSS)
        )

        try:
            field = self.wait.until(EC.element_to_be_clickable((By.ID, SEARCH_FIELD_ID)))
            field.clear()
            field.click()
            field.send_keys(case_number)
            self._js_click(self.driver.find_element(By.ID, SEARCH_BUTTON_ID))
        except (TimeoutException, WebDriverException) as error:
            raise SystemProblem(f"Could not run the case search for {case_number}: {error}") from error

        try:
            if previous:
                self.wait.until(EC.staleness_of(previous[0]))
            # find_elements returns a list: truthy on either outcome, and
            # keeps waiting while the grid is still loading.
            self.wait.until(
                lambda d: d.find_elements(By.CSS_SELECTOR, NO_RECORDS_CSS)
                or d.find_elements(By.CSS_SELECTOR, CASE_RESULT_ROW_CSS)
            )
        except TimeoutException as error:
            raise SystemProblem(f"Search results never loaded for {case_number}.") from error

        if self.driver.find_elements(By.CSS_SELECTOR, NO_RECORDS_CSS):
            raise DocumentProblem(Status.CASE_NOT_FOUND)

        rows = len(self.driver.find_elements(By.CSS_SELECTOR, CASE_RESULT_ROW_CSS))
        if rows > 1:
            raise DocumentProblem(Status.error(f"search returned {rows} cases"))

    def _open_images_tab(self, case_number: str) -> None:
        """Open the case's Images tab, switch it to grid view, and wait for rows.

        Args:
            case_number: For the error message only.

        Raises:
            SystemProblem: If the tab, its filter box, or the grid never appears.
        """
        try:
            self._js_click(self.wait.until(EC.element_to_be_clickable((By.ID, IMAGES_TAB_ID))))
            self.wait.until(EC.visibility_of_element_located((By.ID, IMAGES_PANEL_ID)))
            self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, IMAGE_FILTER_CSS)))
        except (TimeoutException, WebDriverException) as error:
            raise SystemProblem(f"Could not open the Images tab for {case_number}.") from error
        self._settle()
        self._switch_to_grid_view(case_number)

    def grid_is_showing(self) -> bool:
        """Whether the Images tab is in grid (View > Details) mode.

        Returns:
            True if the image grid is visible.
        """
        try:
            return self.driver.find_element(By.ID, IMAGE_GRID_ID).is_displayed()
        except WebDriverException:
            return False

    def _switch_to_grid_view(self, case_number: str) -> None:
        """Choose View > Details unless the grid is already showing, then wait for it to load.

        Loaded means data rows or the no-records row. A case whose grid shows
        neither within wait_timeout is an error, not "no images": an empty
        grid and an unloaded one look the same, and only one is true.

        Args:
            case_number: For the error message only.

        Raises:
            SystemProblem: If the view will not switch or the grid never loads.
        """
        # The tab's content renders after its filter box does. Deciding before
        # either view has appeared is what made the first test run switch
        # views needlessly and fail, so wait for one or the other first.
        try:
            self.wait.until(lambda d: self.grid_is_showing() or any(
                e.is_displayed() for e in d.find_elements(By.ID, IMAGE_LISTVIEW_ID)
            ))
        except TimeoutException as error:
            raise SystemProblem(f"The Images tab never showed its image list for {case_number}.") from error

        if not self.grid_is_showing():
            logger.debug("Images tab is in thumbnail view; switching to grid")
            try:
                self._js_click(self.wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, IMAGE_VIEW_BUTTON_CSS))
                ))
                self._settle()
                item = self.wait.until(lambda d: next(
                    (i for i in d.find_elements(By.CSS_SELECTOR, IMAGE_VIEW_GRID_ITEM_CSS) if i.is_displayed()),
                    False,
                ))
                self._js_click(item)
                self.wait.until(lambda _: self.grid_is_showing())
            except (TimeoutException, WebDriverException) as error:
                self._close_dropdown()
                raise SystemProblem(f"Could not switch the Images tab to grid view for {case_number}.") from error

        try:
            self.wait.until(
                lambda d: d.find_elements(By.CSS_SELECTOR, IMAGE_ROW_CSS)
                or d.find_elements(By.CSS_SELECTOR, IMAGE_GRID_NO_RECORDS_CSS)
            )
        except TimeoutException as error:
            raise SystemProblem(f"The image grid never loaded for {case_number}.") from error
        self._settle()

    # ------------------------------------------------------- image preview

    def read_image_preview(self, image_id: str) -> PreviewReading:
        """Filter the open case's Images grid to one Image Id and read its preview.

        Nothing is judged until the preview's own document URL carries this
        Image Id, so the previous image's preview can never be read as this
        one's.

        Args:
            image_id: Normalized Image Id. The case must already be open.

        Returns:
            What the preview showed, plus the grid's file size and storage
            location for the output.

        Raises:
            DocumentProblem: No row matched, several did, or the preview never
                switched to this image.
            SystemProblem: The grid or the Preview tab did not respond.
        """
        # Before filtering: the filter can auto-select the row, which starts
        # this image's preview loading, and its message must not get marked.
        self._mark_pane_messages()
        row = self._filter_images(image_id)
        cells = self._row_cells(row, [GRID_FILE_SIZE_COLUMN, GRID_STORAGE_COLUMN])
        self._select_row(row)
        self._open_preview_tab()
        state = self._wait_for_preview(image_id)
        self._pause(f"preview for {image_id}")
        return PreviewReading(
            pdf_loaded=bool(state.get("pdf_loaded")),
            corrupted_message=bool(state.get("error_phrases") or state.get("load_failed")),
            file_size=cells.get(GRID_FILE_SIZE_COLUMN, ""),
            storage_location=cells.get(GRID_STORAGE_COLUMN, ""),
        )

    def _filter_images(self, image_id: str) -> WebElement:
        """Filter the Images grid and return the row whose image id is exactly this one.

        The filter is a text "contains" match across several columns, so an
        Id that also appears in another image's comment would leave two rows.
        Picking by the exact "image id" cell makes the filter only a way to
        narrow the list, never the proof. The preview URL check is the proof.

        Args:
            image_id: What to filter on.

        Returns:
            The single visible row with that exact image id.

        Raises:
            DocumentProblem: No row has this id (IMAGE ID NOT FOUND), or more
                than one does.
            SystemProblem: The filter box is missing, or the grid has no
                "image id" column to check against.
        """
        # The row left from the previous Id would pass for this one's result
        # if the filter has not re-rendered yet. Waiting for it to go stale
        # avoids selecting it. Best effort: the exact-id check still guards.
        previous = self._visible_rows()

        try:
            box = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, IMAGE_FILTER_CSS)))
            # Real key events, not clear(). Kendo filters on key events, and
            # clear() left the old results showing in DALYN's matrix dialog.
            box.send_keys(Keys.CONTROL, "a")
            box.send_keys(Keys.DELETE)
            box.send_keys(image_id)
            box.send_keys(Keys.ENTER)
        except (TimeoutException, WebDriverException) as error:
            raise SystemProblem(f"Could not use the Images filter box: {error}") from error

        if previous:
            try:
                self._waiter(FILTER_SETTLE_TIMEOUT).until(EC.staleness_of(previous[0]))
            except TimeoutException:
                pass
        self._settle()

        # Waits the full timeout only when the image truly is not on the case,
        # which should be rare; a found image returns on the first poll.
        try:
            self.wait.until(lambda _: self._exact_rows(image_id))
        except TimeoutException:
            pass

        rows = self._exact_rows(image_id)
        if not rows:
            raise DocumentProblem(Status.IMAGE_ID_NOT_FOUND)
        if len(rows) > 1:
            raise DocumentProblem(Status.error(f"{len(rows)} grid rows have image id {image_id}"))
        return rows[0]

    def _exact_rows(self, image_id: str) -> list[WebElement]:
        """Visible grid rows whose "image id" cell equals image_id exactly.

        Args:
            image_id: The wanted Image Id.

        Returns:
            The matching rows, possibly none.

        Raises:
            SystemProblem: If the grid has no "image id" column. Without it
                every row would read as IMAGE ID NOT FOUND, so it must stop
                loudly rather than fill the sheet with a wrong status.
        """
        try:
            rows = self.driver.execute_script(EXACT_ROWS_JS, IMAGE_GRID_ID, image_id, GRID_IMAGE_ID_COLUMN)
        except WebDriverException:
            # Grid mid-render. The caller's wait polls again.
            return []
        if rows is None:
            raise SystemProblem(
                f"The image grid has no '{GRID_IMAGE_ID_COLUMN}' column. STAC's grid may have changed."
            )
        return list(rows)

    def _visible_rows(self) -> list[WebElement]:
        """Image grid data rows currently shown. Hidden ones are left out.

        Returns:
            The displayed rows, possibly none.
        """
        try:
            return [r for r in self.driver.find_elements(By.CSS_SELECTOR, IMAGE_ROW_CSS) if r.is_displayed()]
        except WebDriverException:
            # A row re-rendered between find and is_displayed. Next poll retries.
            return []

    def _row_cells(self, row: WebElement, columns: list[str]) -> dict[str, str]:
        """Read named, non-personal cells from a grid row.

        Args:
            row: A grid row.
            columns: Header texts, e.g. ["file size MB"].

        Returns:
            Header to cell text; "" for a column that is not there.
        """
        try:
            return self.driver.execute_script(GRID_ROW_CELLS_JS, row, columns) or {}
        except WebDriverException:
            return {}

    def _row_is_selected(self, row: WebElement) -> bool:
        """Whether Kendo has marked the row selected.

        Args:
            row: A grid row.

        Returns:
            True if it has the k-selected class.
        """
        try:
            return KENDO_SELECTED_CLASS in (row.get_attribute("class") or "")
        except WebDriverException:
            return False

    def _select_row(self, row: WebElement) -> None:
        """Select the grid row so the preview loads that image.

        Always done, whether or not the filter auto-selected it. Selected
        through Kendo's grid API rather than by clicking: a click into a cell
        could land on a link or command button, which a read-only tool must
        never risk.

        Args:
            row: The row to select.

        Raises:
            SystemProblem: If it never becomes selected.
        """
        try:
            if not self.driver.execute_script(SELECT_GRID_ROW_JS, row):
                raise SystemProblem("Could not reach the image grid to select the row.")
            self.wait.until(lambda _: self._row_is_selected(row))
        except (TimeoutException, WebDriverException) as error:
            raise SystemProblem(f"The image row never became selected: {error}") from error
        self._settle()

    def _open_preview_tab(self) -> None:
        """Make sure the right-hand pane is on its Preview tab.

        Checked for every image rather than once, in case selecting a row
        flips the pane to another tab.

        Raises:
            SystemProblem: If the tab is missing or will not open.
        """
        try:
            tab = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, PREVIEW_TAB_CSS)))
            if tab.get_attribute("aria-selected") != "true":
                self._js_click(tab)
                self.wait.until(lambda _: tab.get_attribute("aria-selected") == "true")
        except (TimeoutException, WebDriverException) as error:
            raise SystemProblem(f"Could not open the Preview tab: {error}") from error

    def _preview_tab_selected(self) -> str:
        """The Preview tab's aria-selected, for diagnostics.

        Returns:
            "true", "false", or "missing".
        """
        try:
            return self.driver.find_element(By.CSS_SELECTOR, PREVIEW_TAB_CSS).get_attribute("aria-selected") or ""
        except WebDriverException:
            return "missing"

    def _mark_pane_messages(self) -> None:
        """Mark the error messages already in the pane, so only new ones count.

        Raises:
            SystemProblem: If the marking script cannot run. Without the mark a
                leftover message could be read as this image's, so the image
                is not judged.
        """
        try:
            self.driver.execute_script(MARK_PANE_MESSAGES_JS, PREVIEW_PANE_CSS, PREVIEW_ERROR_PHRASES)
        except WebDriverException as error:
            raise SystemProblem(f"Could not mark the preview pane's old messages: {error}") from error

    def _pane_phrases(self) -> list[str]:
        """Error phrases in the pane that appeared since the last mark.

        Returns:
            The matching phrases, possibly none.
        """
        try:
            return list(self.driver.execute_script(PANE_PHRASES_JS, PREVIEW_PANE_CSS, PREVIEW_ERROR_PHRASES) or [])
        except WebDriverException:
            return []

    def _preview_state(self, image_id: str) -> dict:
        """Look inside the preview iframe once, plus the pane around it.

        Always switches back to the main page, even on failure, or every later
        lookup would search inside the iframe and fail.

        Args:
            image_id: The wanted Image Id, for the URL check.

        Returns:
            url_has_id, pdf_loaded, load_failed, pages and error_phrases (new
            pane messages plus any inside the iframe). Never the URL. Empty if
            there is no iframe or it is mid-navigation.
        """
        try:
            frame = self.driver.find_element(By.CSS_SELECTOR, PREVIEW_IFRAME_CSS)
        except WebDriverException:
            return {}
        try:
            self.driver.switch_to.frame(frame)
            state = self.driver.execute_script(PREVIEW_STATE_JS, PREVIEW_ERROR_PHRASES) or {}
        except WebDriverException:
            # Mid-navigation: the iframe is swapping documents. Next poll retries.
            return {}
        finally:
            self.driver.switch_to.default_content()

        state["url_has_id"] = url_has_image_id(state.pop("href", ""), image_id)
        new_in_pane = self._pane_phrases()
        # Kept apart for the debug log, so it shows which source said corrupted.
        state["phrase_in_iframe"] = bool(state.get("error_phrases"))
        state["phrase_in_pane"] = bool(new_in_pane)
        state["error_phrases"] = list(state.get("error_phrases") or []) + new_in_pane
        return state

    def _wait_for_preview(self, image_id: str) -> dict:
        """Wait for the preview to be this image, then for it to load or error.

        Two waits on purpose. The first is the freshness check and failing it
        is an error. The second can run out without failing: that state goes
        to checker.decide, which leaves the row blank unless something proves
        the image good.

        Args:
            image_id: The wanted Image Id.

        Returns:
            The last preview state seen.

        Raises:
            DocumentProblem: ERROR status if the preview never switched.
        """
        seen: dict = {}

        def is_this_image(_: object) -> bool:
            """Record the state; true once the iframe's document is this image's."""
            seen.clear()
            seen.update(self._preview_state(image_id))
            return bool(seen.get("url_has_id"))

        def has_settled(_: object) -> bool:
            """Record the state; true once it is this image and loaded or failed."""
            is_this_image(_)
            verdict = seen.get("pdf_loaded") or seen.get("load_failed") or seen.get("error_phrases")
            return bool(seen.get("url_has_id") and verdict)

        # The first preview after a fresh browser or a view switch is slower
        # (5-row headed run, case 1), so the switch gets both timeouts.
        try:
            self._waiter(self.wait_timeout + self.preview_timeout).until(is_this_image)
        except TimeoutException as error:
            # Flags only: whether there was an iframe, whether its URL matched,
            # whether PDF.js was there. Never the URL itself.
            logger.debug(
                "Preview for %s never switched. Last state: %s. Preview tab selected: %s",
                image_id, seen or "no iframe", self._preview_tab_selected(),
            )
            raise DocumentProblem(Status.error(ERROR_PREVIEW_NOT_SWITCHED)) from error

        try:
            self._waiter(self.preview_timeout).until(has_settled)
        except TimeoutException:
            logger.debug("Preview for %s neither loaded nor errored: %s", image_id, seen)

        # A last read can only have drifted to another image if something else
        # changed the selection; refuse to judge if it did.
        if not seen.get("url_has_id"):
            raise DocumentProblem(Status.error(ERROR_PREVIEW_NOT_SWITCHED))
        # Flags and counts only; the URL was dropped in _preview_state.
        logger.debug("Preview for %s: %s", image_id, seen)
        return dict(seen)

    # ---------------------------------------------------------------- probe

    def probe(self, case_number: str, image_ids: list[str]) -> list[str]:
        """Walk one case through every step and report what STAC did.

        Uses the production steps, so a clean probe means a full run will see
        the same thing. The first Id is checked a second time at the end.
        Read-only, and prints no names or document text.

        Args:
            case_number: Case to open.
            image_ids: Normalized Image Ids on that case.

        Returns:
            Report lines.
        """
        report = [f"Probe {datetime.now():%Y-%m-%d %H:%M:%S}, case {case_number}"]

        try:
            self.open_case(case_number)
        except FubarError as error:
            report.append(f"open_case FAILED: {error}")
            report.append(f"  criteria showing: {self.selected_criteria()!r}")
            rows = len(self.driver.find_elements(By.CSS_SELECTOR, CASE_RESULT_ROW_CSS))
            report.append(f"  result rows on page: {rows}")
            return report

        report.append(f"criteria after selecting: {self.selected_criteria()!r}")
        report.append(f"grid view showing: {self.grid_is_showing()}")
        report.append(f"grid columns: {self.driver.execute_script(GRID_HEADERS_JS)}")
        report.append(f"grid dataSource: {self.driver.execute_script(GRID_COUNTS_JS)}")
        report.append(f"first image ids in grid: {self.driver.execute_script(GRID_SAMPLE_IDS_JS, GRID_IMAGE_ID_COLUMN)}")

        passes = [(image_id, "") for image_id in image_ids] + [(image_ids[0], " (second pass)")]
        for image_id, label in passes:
            report.append("")
            report.append(f"--- Image Id {image_id}{label}")
            started = time.monotonic()
            try:
                reading = self.read_image_preview(image_id)
            except FubarError as error:
                report.append(f"  FAILED after {time.monotonic() - started:.1f}s: {error}")
                continue
            report.append(f"  read in {time.monotonic() - started:.1f}s")
            report.append(f"  {reading}")
            verdict = decide(
                reading.pdf_loaded, reading.corrupted_message, reading.file_size,
                reading.storage_location, datetime.now(),
            )
            report.append(f"  verdict: {verdict.status}, Exclude {verdict.exclude!r}")
        return report
