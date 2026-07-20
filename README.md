# Find a Grave Tools & Genealogy Workstation

Create a local, offline media-rich archive of Find a Grave (https://www.findagrave.com) memorial pages and analyze relationships to build family trees.

## Last Updated
2026-07-19 (Updated with Local Web GUI, SQLite Caching, GPS Scraping, Detailed Name Parsing, Vis.js Pedigree Trees, On-Demand Media Vault, & GEDCOM Export)

This project builds upon the original command-line scraping scripts developed by **Doug Foster** (https://dougfoster.me). It has been upgraded into a local workstation featuring a Flask Web GUI dashboard, SQLite database, background browser recycling, automated Cloudflare challenge bypass, and deep extraction capabilities.

---

## Key Features & Capabilities

### 1. The Local Web GUI
A modern, dark-mode web application served locally on your computer at `http://127.0.0.1:5050`. It provides a visual dashboard to:
* **Manage Cemeteries**: Add cemeteries to your workstation simply by entering their numeric Find a Grave ID (names, locations, and GPS details are fetched automatically).
* **Interactive Scraper Controls**: Configure relationship depth parameters and trigger scrapes via explicit controls.
* **Real-Time Monitor**: Watch scraping progress with visual progress bars, status indicators, and live scrolling log consoles.
* **Turnstile Bypass**: Automatically solve and bypass Cloudflare Turnstile Challenges in the background using a recycling headless Chrome session via `DrissionPage`.
* **Stashed Memorial Explorer**: Search, filter, and browse stashed burials in an interactive, paginated data grid. Click any row to view biographies, detailed dates, location details, and structured family relationship mappings.
* **Click-to-Export**: Compile and generate the output Excel spreadsheet (`output/burials.xlsx`) with a single click.

### 2. Deep Name Parsing & Anatomy
Instead of saving raw full names, the workstation parses every memorial's name into 7 distinct parts:
* **Prefix** (e.g. `Dr.`, `Rev.`, `Col.`)
* **First Name**
* **Middle Name**
* **Maiden Name** (extracted from italicized `<i>` HTML structures)
* **Last Name**
* **Suffix** (e.g. `Jr.`, `III`, `Sr.`)
* **Nickname** (extracted from quote-wrapped regex matches, e.g. `“Skeet”`)

### 3. Geographic Mapping & GPS
* Extracts the cemetery's official address/location (e.g., `Cabell County, West Virginia, USA`).
* Scrapes the latitude and longitude coordinates from the cemetery directions link.
* Embeds clickable Google Maps link in the GUI dashboard cemetery list cards.

### 4. Incremental Scrapes & Multi-Phase Tree Building
The workstation database is designed for **incremental, multi-phase scrapes**. You do not need to scrape everything at once:
* **Phase 1 (Burials Only)**: Select a cemetery, check only the `Burial (Required)` group, and click **Start Scrape**. The tool will crawl the cemetery's index, queue all burials, and download their profiles. During this phase, it automatically extracts all family relationships (Parents, Spouses, Children, Siblings, Half-siblings) and stores their connections in the `relationships` table.
* **Phase 2 (Expanding the Tree)**: At any point in the future, you can select the same cemetery, check additional relationship boxes (e.g., `Parents` or `Siblings`), and click **Start Scrape**. The workstation will skip crawling the index pages, query the existing relationship table for any missing family profiles, enqueue their URLs, and download them. 
* This allows you to expand your genealogy database layer-by-layer without ever downloading the same memorial page twice.

### 5. Interactive Relationship Graphing (Family Trees)
Directly in the Web GUI's Biography & Details modal, switch to the **🕸️ Interactive Family Tree** tab to visualize relationships dynamically:
* **Pedigree Layout**: Automatically renders family lines in a vertical top-to-bottom layout (Ancestors on top, Spouses & Siblings in the middle, Descendants at the bottom).
* **Generation Tickers**: Adjust display depth on-the-fly using independent **Ancestors** and **Descendants** generation selectors (ranges from `0` to `10` generations).
* **Orthogonal Bezier Flowlines**: Connects relatives using vertical Bezier connectors for clean, readable paths that mimic professional family tree diagrams.
* **Click-to-Refocus**: Click any relative in the graph to instantly reload the dashboard details modal centered on that person.

### 6. On-Demand Photo Archiving & Offline Media Vault
* Maintain a local media vault of high-resolution gravestone photos and face portraits under `photos/<memorial_id>/`.
* **Bandwidth & Storage Efficient**: Zero photos are downloaded during automatic scrapes. Instead, you click the **📥 Download Pictures from Find A Grave** button on any profile card to pull photos on-demand.
* **Strict Filtering**: Automatically matches URLs against the memorial ID prefix to ensure only relevant deceased/tombstone photos are saved, filtering out side-panel thumbnails for other relatives.
* **Inline Gallery Viewer**: Displays a horizontal image reel at the top of the details modal. Clicking any photo opens the original high-resolution file in a new tab.

### 7. Veteran Badge Parsing
* Automatically deconflicts the Find a Grave veteran military honor badge (`<b class="icon-vet">`) from name text, preventing letters like `VVeteran` from corrupting the parsed **Last Name** field.

### 8. GEDCOM File Export
Export stashed relationship networks into standard genealogy exchange format (`.ged`) files to import into Ancestry.com, FamilySearch, Gramps, or MyHeritage:
* **Focus Person Line Export**: Recursively crawls the connected family line (ancestors, spouses, siblings, and descendants) of a chosen individual to generate a clean, isolated pedigree tree.
* **Cemetery Registry Export**: Bulk-exports all stashed memorials in a selected cemetery as individual separate family lines.
* **Smart Gender & Date Parsing**: Automatically infers sex (M/F) from relationship context and biography pronouns, and normalizes date strings to standard uppercase GEDCOM formats (e.g. `11 NOV 1925`).

---

## Operations Guide

### Option A: Running the Web GUI (Recommended)
1. Ensure your Python virtual environment is active and dependencies are installed (see Python Notes below).
2. Run the launcher script:
   ```powershell
   python run_gui.py
   ```
3. This will automatically:
   * Scan your system and terminate any zombie automated browser processes.
   * Initialize the SQLite database at `stash/find_a_grave_v2.db`.
   * Scan your `stash/` folder and import any existing stashed HTML files.
   * Spin up the Flask server and launch your default browser to `http://127.0.0.1:5050`.
4. **Scraping a Cemetery**:
   * Enter the numeric **Cemetery ID** under *Cemeteries Configuration* and click **Add Cemetery**.
   * Pick your target cemetery from the dropdown selector.
   * Tick the checkbox variables for the relationship lines you wish to download (e.g. Spouses, Children).
   * Click **⚡ Start Scrape** to initiate.
5. **Syncing Metadata**:
   * Click the **`🔄 Fetch Missing Locations & GPS`** button to loop through all cemeteries in your database and fetch their names, addresses, and coordinates in the background.

### Option B: Running the Command Line Scripts
If you prefer running command line scripts, the scraper is fully backwards-compatible:
1. Edit `instructions/stash_graves.txt` to include the cemeteries and groups to pull from 'Find a Grave' (E.g. `2748319 : burial, parent, spouse`).
2. Run the stasher:
   ```powershell
   python stash_graves.py
   ```
3. Edit `instructions/dig_graves.txt` to specify which stashed cemeteries to compile.
4. Run the compiler:
   ```powershell
   python dig_graves.py
   ```
5. The output spreadsheet will be generated at `output/burials.xlsx` with worksheet tabs named by Cemetery ID.

---

## Python Notes & Setup

These scripts are fully compatible with Python versions 3.12 to 3.14. Follow these setup instructions:

1. Create a project directory and download/clone this repository.
2. Initialize a Python virtual environment:
   ```powershell
   python -m venv .venv
   ```
3. Activate the virtual environment:
   * **Windows Powershell**: `.venv\Scripts\Activate.ps1`
   * **macOS/Linux Terminal**: `source .venv/bin/activate`
4. Install the required libraries:
   ```powershell
   pip install requests beautifulsoup4 xlsxwriter flask drissionpage setuptools psutil
   ```
5. Run the web interface:
   ```powershell
   python run_gui.py
   ```

### Installed Package Manifest
* `flask`: Web framework serving the API and HTML dashboard.
* `drissionpage`: Automation library used to control a headless Chrome instance natively via CDP to bypass Cloudflare protection without version mismatch errors.
* `xlsxwriter`: Excel spreadsheet generation.
* `beautifulsoup4`: HTML DOM parsing.
* `psutil`: Automated background process cleanup.

---

## Credits & Support
* **Original Creator**: Doug Foster (https://dougfoster.me).
* **Updates**: Upgraded with local SQLite database caching, parallel scraping queue, browser recycling, automated self-healing, detailed name-parsing (prefixes, suffixes, maiden names, nicknames), GPS extraction, and an interactive dark-mode Web GUI dashboard.

## License
This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

A copy of the GNU General Public License is [included](LICENSE.txt) in this repository. Please also refer to https://www.gnu.org/licenses/.
