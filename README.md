# Find a Grave Tools & Genealogy Workstation

Create a local, offline, media-rich archive of [Find a Grave](https://www.findagrave.com) memorial pages, analyze relationships, map gravesite plots in 2D GIS space, build pedigree family trees, and audit stashed records with advanced research tools.

## Last Updated

**July 2026 (v2.5 Release)** — Upgraded with Multi-Page Web Application Architecture, Dedicated Full-Screen Family Tree Canvas, Esri Satellite GIS Cemetery Map, Demographics & Analytics Dashboard, Research & Data Quality Suite, SQLite WAL Performance Tuning, and GEDCOM Exporting.

---

## 👨‍💻 Credits & Attribution

This project is built upon the foundational command-line stashing and compiling tools created by **Doug Foster** ([https://dougfoster.me](https://dougfoster.me)).

It has been expanded into a comprehensive genealogy workstation featuring a multi-page Flask web application, an optimized SQLite database with WAL mode, background browser recycling via `DrissionPage` with automated Cloudflare Turnstile challenge bypass, Leaflet GIS mapping, interactive family trees, demographic analytics, and genealogical anomaly detection.

---

## 🌟 Key Features & Web Views

The workstation is served locally at `http://127.0.0.1:5050` with top-level header navigation across all views:

### 1. 📊 Main Dashboard (`/`)

- **Scraper Management**: Add cemeteries by their numeric Find a Grave ID (cemetery name, address, and coordinates are fetched automatically).
- **Multi-Phase Incremental Scraper**: Configure relationship crawl depth (Burials, Parents, Spouses, Children, Siblings) to build family trees layer-by-layer without downloading any profile twice.
- **Turnstile Challenge Bypass**: Automatically solves Cloudflare Turnstile challenges using a background headless Chrome browser with `DrissionPage`.
- **Stashed Memorial Explorer**: Paginated data grid to search, filter, and view memorial profiles, biographies, inscriptions, and detailed family connections.
- **One-Click Exports**: Generate Excel reports (`output/burials.xlsx`) and GEDCOM (`.ged`) files with a single click.

### 2. 🌳 Interactive Family Tree Canvas (`/tree`)

- **Dedicated Full-Screen View**: A dedicated canvas powered by `vis.js` for exploring complex family networks (`/tree?focus=MEM_ID`).
- **Orthogonal Bezier Flowlines**: Displays ancestral lines in clear top-to-bottom layouts (Ancestors on top, Spouses & Siblings in the middle, Descendants at bottom).
- **Generation Depth Controls**: Adjustable Ancestor (`0`–`10`) and Descendant (`0`–`10`) generation depth sliders.
- **Click-to-Refocus & Focus Card**: Search any focus individual or click any node in the network to re-center the pedigree tree instantly.
- **GEDCOM Pedigree Exporter**: Export the focus person's pedigree tree directly to standard GEDCOM (`.ged`) format for Ancestry.com, Gramps, or FamilySearch.

### 3. 🗺️ Full-Screen GIS Cemetery Map (`/map`)

- **Dual Layer GIS Imagery**: Switch seamlessly between **📡 Esri World Imagery (Satellite Aerial View)** and **🗺️ OpenStreetMap**.
- **Tier 1 — Cemetery Pins**: Plot all stashed cemeteries with purple pins showing total burial counts and direct buttons to inspect grave plots (`/map?cemetery=CEM_ID`).
- **Tier 2 — Memorial Plot Markers**: Plot individual grave plots with latitude/longitude coordinates on satellite imagery.
- **Surname Cluster Search**: Highlight family grave clusters sharing the same surname in gold/amber.
- **$N$-Feet Geodesic Proximity Finder**: Specify a focus grave and distance radius (in feet) to draw a translucent circle on the map, identifying neighboring family plots or unmarked graves.
- **👨‍👩‍👧 Geographic Kinship Network Mapping**: Select any focus individual (`/map?kinship=MEM_ID`) to project their entire family network onto the satellite map. Relatives are color-coded by kinship role (⭐ Focus Person in Gold, 💙 Parents in Blue, 🩷 Spouses in Pink, 💚 Children in Green, 💜 Siblings in Purple) with dashed geodesic flowlines connecting relative burial sites across cemeteries.

### 4. 📈 Demographics & Analytics Insights (`/analytics`)

- **Summary Cards**: Total Burials, Cemeteries, Average Lifespan, Veteran %, and Family Connections.
- **Lifespan Age Distribution**: Bar chart of binned age at death ranges (`0-17`, `18-35` ... `96+`).
- **Decade Births & Deaths Timeline**: Dual-line timeline chart tracking births and deaths from the 1800s to present.
- **Top Family Surnames**: Horizontal bar chart of the top 10 family surnames in the database.
- **Mortality Seasonality**: Death distribution by month (`Jan`–`Dec`).
- **Cemetery Filter**: Toggle between **📍 All Cemeteries (Combined)** or scope analytics dynamically to any single cemetery.

### 5. 🔍 Research & Data Quality Suite (`/quality`)

- ⚠️ **Genealogical Anomaly & Discrepancy Detector**: Automatically scans stashed records for logical inconsistencies:
  - **Chronological Errors**: Child born before parent, child born >9 months post parent death, parent <12 yrs old at child's birth, death before birth, extreme lifespan (>110 yrs).
  - **Potential Duplicates**: Identifies matching first/last names and birth/death years within the same cemetery or state.
  - **Data Gaps & Kinship**: Flags unlinked isolated individuals (0 connections) and missing GPS coordinates.
  - **Action Items**: Direct links to jump to the **Family Tree** (`/tree?focus=ID`) or **GIS Map** (`/map?focus=ID`).
- 🔍 **Full-Text Inscription & Bio Search**: Rapid search engine across headstone inscriptions, bio notes, and gravesite notes with term highlighting (`<mark>query</mark>`).
  - **Quick Presets**: One-click searches for `🎖️ Military Infantry`, `🏛️ Masons / Masonic`, `📜 Inscriptions "Beloved"`, `🎖️ WWII Veterans`, and `🏛️ DAR`.
- 🔄 **Stash Sync & Change Tracker**: Health auditor monitoring missing bios, missing inscriptions, and missing GPS coordinates for incremental scrape refreshes.

---

## 🛠️ Data Structure & Performance Enhancements

### Deep Name Anatomy

Every memorial's full name is parsed into 7 distinct columns:

- **Prefix** (e.g. `Dr.`, `Rev.`, `Col.`)
- **First Name**
- **Middle Name**
- **Maiden Name** (extracted from italicized `<i>` HTML structures)
- **Last Name** (prioritized over URL strings)
- **Suffix** (e.g. `Jr.`, `III`, `Sr.`)
- **Nickname** (extracted from quote-wrapped regex matches, e.g. `“Skeet”`)

### SQLite Performance Tuning & Connection Safety

- **WAL Mode**: Database runs in Write-Ahead Logging (`journal_mode=WAL; PRAGMA synchronous=NORMAL;`) enabling non-blocking concurrent reads and writes between Flask web views and background scraper threads.
- **7 Performance Indexes**: Indexed on `cemetery_id`, `last_name`, `surname`, `gps_lat/lng`, `relationships`, and queue status.
- **Memory Safety**: Uses `soup.decompose()` after HTML parsing to free DOM parse trees immediately from RAM.

---

## 🚀 Setup & Quick Start

### Python Requirements

Compatible with Python **3.12 to 3.14**.

1. Clone or extract this repository to your computer.
2. Initialize a Python virtual environment:
   ```powershell
   python -m venv .venv
   ```
3. Activate the virtual environment:
   - **Windows PowerShell**: `.venv\Scripts\Activate.ps1`
   - **macOS/Linux Terminal**: `source .venv/bin/activate`
4. Install dependencies:
   ```powershell
   pip install requests beautifulsoup4 xlsxwriter flask drissionpage setuptools psutil
   ```
5. Launch the workstation:
   ```powershell
   python run_gui.py
   ```
6. Open your browser to `http://127.0.0.1:5050`.

---

## 📜 Credits & License

- **Original Scraper Creator**: Doug Foster ([https://dougfoster.me](https://dougfoster.me))
- **Workstation Upgrades**: Local SQLite database caching, multi-page Flask web application, vis.js family tree canvas, Leaflet Esri satellite GIS map, Chart.js demographic analytics, GEDCOM 5.5.1 exporter, background browser recycling with Turnstile bypass, and genealogical anomaly detector.

### License

This program is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public License (GPLv3)** as published by the Free Software Foundation. A copy of the license is included in [LICENSE.txt](LICENSE.txt).
