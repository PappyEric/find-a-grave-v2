# 📖 Step-by-Step Visual Walkthrough & User Guide

Welcome to the **Find a Grave Genealogy Workstation**! This guide provides a detailed, step-by-step visual tutorial to help you add cemeteries, crawl memorial pages, map family grave plots, and navigate all features of the workstation.

---

## 🧭 Navigation Overview

At the top of your browser window, you will find the main header bar for switching between views:

- **Dashboard** (`/`): Add cemeteries, configure scraper jobs, monitor live logs, and explore database records.
- **Family Tree** (`/tree`): View interactive pedigree charts and export GEDCOM files.
- **GIS Cemetery Map** (`/map`): View cemeteries and grave plots on interactive satellite imagery or OSM map.
- **Analytics** (`/analytics`): View lifespan distributions, timelines, and top surname statistics.
- **Data Quality** (`/quality`): Detect genealogical discrepancies and perform full-text searches.

---

## 🚀 Step 1: Requirements, Setup & Launching the Application

### 1. Install Python (Prerequisite)

Before running the application, make sure Python (version 3.12 to 3.14) is installed on your computer.

- **Download Python**: Download the installer from the official [Python Downloads Page](https://www.python.org/downloads/).
- ⚠️ **IMPORTANT (Windows)**: During installation, make sure to check the box **"Add python.exe to PATH"** at the bottom of the installer window before clicking Install.

---

### 2. Download the Code from GitHub

- **Option A (ZIP Download — Easiest)**: Click the green **Code** button at the top of the GitHub repository page, select **Download ZIP**, and extract the ZIP file to a folder on your computer.
- **Option B (Git Clone)**: Open your Command Prompt / Terminal and run:
  ```bash
  git clone https://github.com/PappyEric/find-a-grave-v2.git
  ```

---

### 3. One-Time Setup: Create Virtual Environment & Install Requirements

Running `run_gui.py` requires a local virtual environment (`.venv`) with the project's dependencies installed (`requests`, `beautifulsoup4`, `flask`, `drissionpage`, etc.).

Open a terminal / Command Prompt inside the project folder and run the following two commands (one-time setup):

1. **Create Virtual Environment**:
   ```powershell
   python -m venv .venv
   ```
2. **Install Required Packages**:
   - **Windows**:
     ```powershell
     .venv\Scripts\pip install -r requirements.txt
     ```
   - **macOS / Linux**:
     ```bash
     .venv/bin/pip install -r requirements.txt
     ```

---

### 4. Launch the Application

1. Double-click **`run_gui.py`** (or run `python run_gui.py` in your terminal inside the project folder).
2. `run_gui.py` will locate the virtual environment, start the background database & web server, and automatically open your web browser to `http://127.0.0.1:5050`.

---

## 📊 Step 2: Adding a Cemetery & Crawling Records (Step-by-Step)

### 1. Dashboard Overview

When you open the application, you arrive at the empty **Cemetery & Memorial Dashboard**.

![Dashboard Overview](images/01_dashboard_overview.png)

---

### 2. Enter the Cemetery ID

Find your target cemetery on Find a Grave and copy its numeric ID from the website address (URL). Type the ID into the **Cemetery ID (numeric)** box under **Cemeteries Configuration**.

![Entering Cemetery ID](images/02_entering_cemetery_id.png)
_(Example: Entering cemetery ID `77797` highlighted in red)._

---

### 3. Add Cemetery & Fetch Metadata / GPS

Click **+ Add Cemetery**. The cemetery will appear in your configuration list with its location, GPS coordinates, and options to fetch location data or scrape records.

![Cemetery Added and GPS Info](images/03_cemetery_added_and_gps.png)
_(Example: "Blue Sulphur Burial Park (77797)" auto-populated with coordinates `38.41720, -82.24220`)._

---

### 4. Select Target Cemetery in Job Manager

Under **Active Scraping Job Manager**, click the **Target Cemetery** dropdown menu and select the cemetery you just added.

![Selecting Target Cemetery](images/04_selecting_target_cemetery.png)

---

### 5. Configure Relatives & Start Scraping

Choose which family relationship groups you want the crawler to collect (such as _Burial_, _Parents_, _Spouses_, _Children_, _Siblings_, or _Half-Siblings_). When ready, click the green **⚡ Start Scrape** button.

![Configuring Scrape Groups](images/05_configuring_scrape_groups.png)

---

### 6. Monitor Live Progress Logs

Watch the live console log panel at the bottom of the Job Manager. It shows real-time progress as burials are discovered, enqueued, and parsed. In this example it found 502 memorials for this cemetery.

![Scraper Live Console Logs](images/06_scraper_live_console_logs.png)

Once all the initial memorials are scraped, it will then cycle back to start processing the other selected relationship groups (Parents, Spouses, Children, Siblings, Half-Siblings) for each of the initial memorials. Which then also pulls in any new cemeteries for the relatives that are not in the initial cemetery list. This process will continue until all selected relationship groups are processed for all memorials.

You will see some "Validating" status cemeteries listed on the left side bar. The initial scan will pull in the cemetery IDs but not the cemetery details. (something for me to work on later) These details will need to be added using the **Fetch Missing Cemetery Details** button. This button will pull in the cemetery name, address, and GPS coordinates for all cemeteries that are missing this information. It will cycle through all the "Validating" cemeteries and pull in the cemetery details.

![Validating Cemeteries Status and Fetching Locations](images/07_validating_cemeteries_and_fetch_gps.png)
_(Notice that in this example the cemetery had 502 memorials; after scraping through those and adding in relatives, we have doubled our memorials and added over 200 cemeteries where relatives have been found - there would have been more but I stopped the scraper to save me time making this walkthrough!)._

Once completed you will see a list of "Active" cemeteries ready to use. You can now use the **Scrape** function to scrape the memorials for the selected cemetery or cemeteries. The **Scrape** in the cemetery listing will only scrape through relatives stashed from the initial scrape. If you want to also **fully** scrape through the new cemeteries to get all the memorials and all the relatives in that cemetery you will need to use the **⚡ Start Scrape** button in the Job Manager section and select the new cemeteries and the relationship groups you want to scrape through. This process will continue until all selected relationship groups are processed for all memorials.

---

## 🌳 Step 3: Exploring Family Trees (`/tree`)

![Interactive Family Tree Canvas](images/08_family_tree_view.png)

- Select any individual to center the pedigree network around them.
- Use the **Ancestor Generations** and **Descendant Generations** sliders to expand or collapse how many levels of relatives are displayed.
- Click any box (node) on the screen to instantly refocus the tree on that person.
- Click **Export Pedigree to GEDCOM** to save a standard family tree file.
- I took this screenshot before the scrape was finished: ID numbers will be replaced with readable names when the scrape is done.

---

## 🗺️ Step 4: Using the GIS Cemetery Map (`/map`)

![GIS Cemetery Map Overview](images/09_gis_cemetery_map_overview.png)

- Toggle between **OpenStreetMap** and **Esri World Imagery** satellite aerial view.
- Click purple cemetery pins to view totals and zoom into plot coordinates.

### Grave Plot Markers View

![GIS Grave Plot Markers](images/10_gis_grave_plots_markers.png)

- Type a surname into **Surname Cluster Search** to highlight family plot clusters in gold.
- Use **Geographic Kinship Mapping** to draw color-coded flowlines between a focus person and their buried relatives.

### 👨‍👩‍👧 Geographic Kinship Flowlines across States (Example: James H. Curry)

Using the **Kinship Map** feature, selecting a focus individual projects their entire family network onto the map and draws color-coded geodesic flowlines connecting their burial sites across state lines.

![Geographic Kinship Flowlines Across States](images/11_gis_kinship_state_flowlines.png)
_(In this example, mapping relatives for **James H. Curry** reveals 4 family grave sites connected by 3 flowlines spanning across West Virginia and Virginia—demonstrating how a single cemetery scrape expands to uncover kin buried across multiple states)._

#### Inspecting Focus Individual & Relative Markers

Zooming into the grave markers allows you to inspect exact plot details and geodesic distances between family members:

![Focus Person Marker: James H. Curry](images/12_gis_kinship_focus_curry.png)
_(Focus person **James H. Curry** [1915–1996] highlighted in gold at Duty Cemetery)._

![Sibling Marker: Allen Cleveland Curry](images/13_gis_kinship_sibling_curry.png)
_(Sibling **Allen Cleveland Curry** [1923–1962] highlighted in purple, buried nearby in Duty Cemetery just 0.01 miles / 43.32 ft away from James)._

---

## 📈 Step 5: Demographics & Analytics (`/analytics`)

- Analyze age distributions at death, births and deaths by decade, mortality seasonality by month, and top surnames.
- Scope analytics to a specific cemetery or view combined statistics.

---

## 🔍 Step 6: Data Quality & Discrepancy Detection (`/quality`)

- Run the **Discrepancy Detector** to spot chronological errors (e.g., child born before parent, death before birth) or potential duplicates.
- Perform full-text searches across bios and headstone inscriptions with search term highlighting.

---

## 💡 System Overview & Future Roadmap

In general terms, the workstation operates by fetching and parsing Find a Grave pages to populate a local SQLite database. Once you end/close your session, don't worry! The data is saved in your database for later use. When you restart it, it will use the data in the database to rebuild the web application so you can continue where you left off.

While the core functionality is straightforward, the application is actively evolving. Many features require expansion, some deprecated options need removal, and various interfaces are slated for performance and aesthetic enhancements. More visual assets, screenshots, and step-by-step guides will be added soon.

### 📋 Feedback, Feature Wishlists & Bug Reports

Please add issues, comments, or notes to this GitHub repository regarding:

- **Feature Wishlists**: What additional tools or integrations would make your genealogy research easier?
- **Bug Reports**: Encountered a crawl failure, relationship loop, or database lock? Let us know.
- **Plain Jankiness**: Spot any awkward UI behaviors, layout/styling quirks, or confusing controls? We want to smooth them out!
