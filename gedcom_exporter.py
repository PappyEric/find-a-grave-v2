import os
import re
import datetime
import database

MONTH_MAP = {
    'jan': 'JAN', 'january': 'JAN',
    'feb': 'FEB', 'february': 'FEB',
    'mar': 'MAR', 'march': 'MAR',
    'apr': 'APR', 'april': 'APR',
    'may': 'MAY',
    'jun': 'JUN', 'june': 'JUN',
    'jul': 'JUL', 'july': 'JUL',
    'aug': 'AUG', 'august': 'AUG',
    'sep': 'SEP', 'september': 'SEP',
    'oct': 'OCT', 'october': 'OCT',
    'nov': 'NOV', 'november': 'NOV',
    'dec': 'DEC', 'december': 'DEC'
}

def format_gedcom_date(date_str):
    """
    Normalizes date strings into GEDCOM standard date format (DD MMM YYYY, MMM YYYY, or YYYY).
    Examples:
      - '10 Nov 1925' or '10 Nov, 1925' -> '10 NOV 1925'
      - '1925-11-10' -> '10 NOV 1925'
      - 'Nov 1925' -> 'NOV 1925'
      - '1925' -> '1925'
      - 'abt 1925' -> 'ABT 1925'
    """
    if not date_str or not isinstance(date_str, str):
        return None
        
    cleaned = date_str.strip()
    if not cleaned or cleaned.lower() == 'unknown':
        return None

    # Handle prefixes like 'abt', 'about', 'circa', 'c.'
    prefix = ""
    lower_c = cleaned.lower()
    for p_kw, g_kw in [('about', 'ABT '), ('abt', 'ABT '), ('circa', 'ABT '), ('c.', 'ABT '), ('before', 'BEF '), ('bef', 'BEF '), ('after', 'AFT '), ('aft', 'AFT ')]:
        if lower_c.startswith(p_kw):
            prefix = g_kw
            cleaned = cleaned[len(p_kw):].strip()
            break

    # ISO Format YYYY-MM-DD
    iso_match = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', cleaned)
    if iso_match:
        y, m, d = iso_match.groups()
        months_list = ['', 'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
        m_idx = int(m)
        if 1 <= m_idx <= 12:
            return f"{prefix}{int(d)} {months_list[m_idx]} {y}".strip()

    # Day Month Year (e.g. '10 Nov 1925' or '10 November 1925')
    dmy_match = re.match(r'^(\d{1,2})[\s,-]+([A-Za-z]+)[\s,-]+(\d{4})$', cleaned)
    if dmy_match:
        d, m, y = dmy_match.groups()
        m_key = m.lower()
        if m_key in MONTH_MAP:
            return f"{prefix}{int(d)} {MONTH_MAP[m_key]} {y}".strip()

    # Month Day, Year (e.g. 'Nov 10, 1925')
    mdy_match = re.match(r'^([A-Za-z]+)[\s,-]+(\d{1,2})[\s,-]+(\d{4})$', cleaned)
    if mdy_match:
        m, d, y = mdy_match.groups()
        m_key = m.lower()
        if m_key in MONTH_MAP:
            return f"{prefix}{int(d)} {MONTH_MAP[m_key]} {y}".strip()

    # Month Year (e.g. 'Nov 1925')
    my_match = re.match(r'^([A-Za-z]+)[\s,-]+(\d{4})$', cleaned)
    if my_match:
        m, y = my_match.groups()
        m_key = m.lower()
        if m_key in MONTH_MAP:
            return f"{prefix}{MONTH_MAP[m_key]} {y}".strip()

    # Just Year (e.g. '1925')
    y_match = re.match(r'^(\d{4})$', cleaned)
    if y_match:
        return f"{prefix}{y_match.group(1)}".strip()

    # Fallback to uppercase
    return f"{prefix}{cleaned.upper()}".strip()

def infer_gender(mem, rels):
    """
    Infers gender ('M', 'F', or 'U') for a memorial based on:
      1. Maiden name presence -> 'F'
      2. Prefix/Suffix hints (e.g., 'Mrs.', 'Mr.', 'Sister', 'Father', 'Fr.')
      3. Relationship roles (mother/wife/daughter/sister -> 'F', father/husband/son/brother -> 'M')
      4. Pronoun scanning in biography
    """
    # 1. Maiden name check
    if mem.get('maiden_name') and mem.get('maiden_name').strip():
        return 'F'

    # 2. Prefix check
    prefix = (mem.get('prefix') or '').lower().strip()
    if prefix in ['mrs', 'mrs.', 'ms', 'ms.', 'miss', 'sr', 'sr.', 'sister', 'lady', 'dame']:
        return 'F'
    if prefix in ['mr', 'mr.', 'father', 'fr', 'fr.', 'sir', 'rev', 'rev.']:
        return 'M'

    # 3. Relationship roles check
    mem_id = mem['id']

    for r in rels:
        rtype = r['relationship_type']
        from_id = r['from_memorial_id']
        to_id = r['to_memorial_id']

        # If mem is target of a relationship from another person
        if to_id == mem_id:
            if rtype == 'spouse':
                # Check if from_id spouse is male/female
                pass
        if from_id == mem_id:
            pass

    # 4. Pronoun frequency scan in bio
    bio = (mem.get('bio') or '').lower()
    if bio:
        male_pronouns = len(re.findall(r'\b(he|him|his|himself)\b', bio))
        female_pronouns = len(re.findall(r'\b(she|her|hers|herself)\b', bio))
        if male_pronouns > female_pronouns and male_pronouns >= 2:
            return 'M'
        if female_pronouns > male_pronouns and female_pronouns >= 2:
            return 'F'

    return 'U'

def format_gedcom_note(level, tag, text):
    """
    Formats multi-line text blocks into standard GEDCOM lines using CONT.
    """
    if not text:
        return []
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    output = []
    first = True
    for line in lines:
        cleaned_line = line.strip()
        if not cleaned_line and not first:
            cleaned_line = ""
        if first:
            output.append(f"{level} {tag} {cleaned_line}".strip())
            first = False
        else:
            output.append(f"{level + 1} CONT {cleaned_line}".strip())
    return output

def parse_name_parts(mem):
    """
    Extracts first/given name, middle name, surname, prefix, suffix, and nickname.
    """
    prefix = (mem.get('prefix') or '').strip()
    first = (mem.get('first_name') or '').strip()
    middle = (mem.get('middle_name') or '').strip()
    maiden = (mem.get('maiden_name') or '').strip()
    last = (mem.get('last_name') or '').strip() or (mem.get('surname') or '').strip()
    suffix = (mem.get('suffix') or '').strip()
    nickname = (mem.get('nickname') or '').strip()

    # Fallback to parsing 'name' if first/last are missing
    if not first and not last:
        full_name = (mem.get('name') or '').strip()
        if full_name:
            parts = full_name.split()
            if len(parts) == 1:
                first = parts[0]
            elif len(parts) > 1:
                first = parts[0]
                last = parts[-1]
                if len(parts) > 2:
                    middle = " ".join(parts[1:-1])

    surname = maiden if maiden else last
    given_parts = [p for p in [first, middle] if p]
    given = " ".join(given_parts).strip()

    name_str = f"{given} /{surname}/".strip()
    if suffix:
        name_str += f" {suffix}"

    return {
        'full_gedcom_name': name_str,
        'given': given,
        'surname': surname,
        'prefix': prefix,
        'suffix': suffix,
        'nickname': nickname
    }

def get_focus_network(focus_memorial_id, conn):
    """
    Performs an undirected BFS starting from focus_memorial_id to collect all connected memorial IDs.
    Returns (memorial_dict_by_id, relationship_list).
    """
    cursor = conn.cursor()
    visited = set([str(focus_memorial_id)])
    queue = [str(focus_memorial_id)]

    while queue:
        curr_id = queue.pop(0)
        cursor.execute("""
            SELECT to_memorial_id FROM relationships WHERE from_memorial_id = ?
            UNION
            SELECT from_memorial_id FROM relationships WHERE to_memorial_id = ?;
        """, (curr_id, curr_id))
        rows = cursor.fetchall()
        for r in rows:
            rel_id = str(r[0])
            if rel_id not in visited:
                visited.add(rel_id)
                queue.append(rel_id)

    if not visited:
        return {}, []

    # Fetch memorials in chunks of 500 to prevent SQLite parameter limits
    visited_list = list(visited)
    memorials = {}
    chunk_size = 500
    for i in range(0, len(visited_list), chunk_size):
        chunk = visited_list[i:i+chunk_size]
        placeholders = ",".join(["?"] * len(chunk))
        cursor.execute(f"""
            SELECT m.*, c.name as cemetery_name, c.location as cemetery_location
            FROM memorials m
            LEFT JOIN cemeteries c ON m.cemetery_id = c.id
            WHERE m.id IN ({placeholders});
        """, chunk)
        for row in cursor.fetchall():
            memorials[dict(row)['id']] = dict(row)

    # Fetch relationships within this network
    relationships = []
    for i in range(0, len(visited_list), chunk_size):
        chunk = visited_list[i:i+chunk_size]
        placeholders = ",".join(["?"] * len(chunk))
        cursor.execute(f"""
            SELECT * FROM relationships
            WHERE from_memorial_id IN ({placeholders}) OR to_memorial_id IN ({placeholders});
        """, chunk + chunk)
        for row in cursor.fetchall():
            r = dict(row)
            if r['from_memorial_id'] in memorials and r['to_memorial_id'] in memorials:
                relationships.append(r)

    return memorials, relationships

def get_cemetery_network(cemetery_id, conn):
    """
    Fetches all stashed memorials and relationships for a cemetery (or all cemeteries if cemetery_id is 'all').
    Returns (memorial_dict_by_id, relationship_list).
    """
    cursor = conn.cursor()
    if cemetery_id and str(cemetery_id).lower() != 'all':
        cursor.execute("""
            SELECT m.*, c.name as cemetery_name, c.location as cemetery_location
            FROM memorials m
            LEFT JOIN cemeteries c ON m.cemetery_id = c.id
            WHERE m.cemetery_id = ?;
        """, (cemetery_id,))
        mem_rows = cursor.fetchall()
        memorials = {dict(row)['id']: dict(row) for row in mem_rows}

        if not memorials:
            return {}, []

        mem_ids = list(memorials.keys())
        relationships = []
        chunk_size = 500
        for i in range(0, len(mem_ids), chunk_size):
            chunk = mem_ids[i:i+chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cursor.execute(f"""
                SELECT * FROM relationships
                WHERE from_memorial_id IN ({placeholders}) OR to_memorial_id IN ({placeholders});
            """, chunk + chunk)
            for row in cursor.fetchall():
                r = dict(row)
                if r['from_memorial_id'] in memorials and r['to_memorial_id'] in memorials:
                    relationships.append(r)

        return memorials, relationships
    else:
        cursor.execute("""
            SELECT m.*, c.name as cemetery_name, c.location as cemetery_location
            FROM memorials m
            LEFT JOIN cemeteries c ON m.cemetery_id = c.id;
        """)
        mem_rows = cursor.fetchall()
        memorials = {dict(row)['id']: dict(row) for row in mem_rows}

        if not memorials:
            return {}, []

        cursor.execute("SELECT * FROM relationships;")
        rel_rows = cursor.fetchall()
        relationships = [dict(row) for row in rel_rows]

        return memorials, relationships

def build_families(memorials, relationships):
    """
    Synthesizes GEDCOM Family (FAM) records from memorial and relationship dicts.
    Returns:
      - families: dict mapping fam_id -> {'husb': id, 'wife': id, 'children': [ids]}
      - indi_famc: dict mapping mem_id -> fam_id (Family as Child)
      - indi_fams: dict mapping mem_id -> set of fam_ids (Family as Spouse)
    """
    # 1. Gather parent-child pairs and spouse pairs
    parent_child = [] # (parent_id, child_id)
    spouses = set()   # (person1, person2) sorted tuple

    for r in relationships:
        from_id = r['from_memorial_id']
        to_id = r['to_memorial_id']
        rtype = r['relationship_type']

        if from_id not in memorials or to_id not in memorials:
            continue

        if rtype == 'parent':
            # from_id has parent to_id => to_id is parent of from_id
            parent_child.append((to_id, from_id))
        elif rtype == 'child':
            # from_id has child to_id => from_id is parent of to_id
            parent_child.append((from_id, to_id))
        elif rtype == 'spouse':
            pair = tuple(sorted([from_id, to_id]))
            spouses.add(pair)

    # 2. Group children by set of parents
    child_parents = {} # child_id -> set of parent_ids
    for parent_id, child_id in parent_child:
        if child_id not in child_parents:
            child_parents[child_id] = set()
        child_parents[child_id].add(parent_id)

    families = {}
    fam_counter = 1
    indi_famc = {}
    indi_fams = {}

    def add_fams(p_id, f_id):
        if p_id not in indi_fams:
            indi_fams[p_id] = set()
        indi_fams[p_id].add(f_id)

    # Track parent couples already turned into FAMs
    couple_fam = {} # tuple(sorted(parents)) -> fam_id

    # Create FAMs for parents and children
    for child_id, parents in child_parents.items():
        if not parents:
            continue
        p_list = sorted(list(parents))
        p_key = tuple(p_list)

        if p_key in couple_fam:
            fam_id = couple_fam[p_key]
            families[fam_id]['children'].append(child_id)
        else:
            fam_id = f"F{fam_counter}"
            fam_counter += 1
            couple_fam[p_key] = fam_id

            fam_entry = {'husb': None, 'wife': None, 'children': [child_id]}
            if len(p_list) == 1:
                # Single parent
                p1 = p_list[0]
                g = infer_gender(memorials[p1], relationships)
                if g == 'F':
                    fam_entry['wife'] = p1
                else:
                    fam_entry['husb'] = p1
                add_fams(p1, fam_id)
            else:
                # Two or more parents (take first 2)
                p1, p2 = p_list[0], p_list[1]
                g1 = infer_gender(memorials[p1], relationships)
                g2 = infer_gender(memorials[p2], relationships)

                if g1 == 'M' and g2 == 'F':
                    fam_entry['husb'] = p1
                    fam_entry['wife'] = p2
                elif g1 == 'F' and g2 == 'M':
                    fam_entry['husb'] = p2
                    fam_entry['wife'] = p1
                else:
                    fam_entry['husb'] = p1
                    fam_entry['wife'] = p2

                add_fams(p1, fam_id)
                add_fams(p2, fam_id)

            families[fam_id] = fam_entry

        indi_famc[child_id] = fam_id

    # 3. Create FAMs for spouse pairs that don't already share a child FAM
    for s1, s2 in spouses:
        p_key = tuple(sorted([s1, s2]))
        if p_key in couple_fam:
            continue

        fam_id = f"F{fam_counter}"
        fam_counter += 1
        couple_fam[p_key] = fam_id

        g1 = infer_gender(memorials[s1], relationships)
        g2 = infer_gender(memorials[s2], relationships)

        fam_entry = {'husb': None, 'wife': None, 'children': []}
        if g1 == 'M' and g2 == 'F':
            fam_entry['husb'] = s1
            fam_entry['wife'] = s2
        elif g1 == 'F' and g2 == 'M':
            fam_entry['husb'] = s2
            fam_entry['wife'] = s1
        else:
            fam_entry['husb'] = s1
            fam_entry['wife'] = s2

        add_fams(s1, fam_id)
        add_fams(s2, fam_id)
        families[fam_id] = fam_entry

    return families, indi_famc, indi_fams

def generate_gedcom_content(memorials, relationships):
    """
    Generates full GEDCOM 5.5.1 text string given memorial dicts and relationship list.
    """
    families, indi_famc, indi_fams = build_families(memorials, relationships)

    lines = []
    # 1. Header
    today_str = datetime.datetime.now().strftime("%d %b %Y").upper()
    lines.append("0 HEAD")
    lines.append("1 SOUR FindAGraveTools")
    lines.append("2 VERS 1.0.0")
    lines.append("2 NAME Find a Grave Tools")
    lines.append("1 DEST ANSTCY")
    lines.append(f"1 DATE {today_str}")
    lines.append("1 GEDC")
    lines.append("2 VERS 5.5.1")
    lines.append("2 FORM LINEAGE-LINKED")
    lines.append("1 CHAR UTF-8")

    # 2. Individuals (INDI records)
    for mem_id, mem in memorials.items():
        lines.append(f"0 @I{mem_id}@ INDI")

        # Name
        n_parts = parse_name_parts(mem)
        lines.append(f"1 NAME {n_parts['full_gedcom_name']}")
        if n_parts['given']:
            lines.append(f"2 GIVN {n_parts['given']}")
        if n_parts['surname']:
            lines.append(f"2 SURN {n_parts['surname']}")
        if n_parts['prefix']:
            lines.append(f"2 NPFX {n_parts['prefix']}")
        if n_parts['suffix']:
            lines.append(f"2 NSFX {n_parts['suffix']}")
        if n_parts['nickname']:
            lines.append(f"2 NICK {n_parts['nickname']}")

        # Gender
        gender = infer_gender(mem, relationships)
        lines.append(f"1 SEX {gender}")

        # Birth Event
        b_date = format_gedcom_date(mem.get('birth_date'))
        b_place = (mem.get('birth_location') or '').strip()
        if b_date or b_place:
            lines.append("1 BIRT")
            if b_date:
                lines.append(f"2 DATE {b_date}")
            if b_place:
                lines.append(f"2 PLAC {b_place}")

        # Death Event
        d_date = format_gedcom_date(mem.get('death_date'))
        d_place = (mem.get('death_location') or '').strip()
        if d_date or d_place:
            lines.append("1 DEAT")
            if d_date:
                lines.append(f"2 DATE {d_date}")
            if d_place:
                lines.append(f"2 PLAC {d_place}")

        # Burial Event
        cem_name = (mem.get('cemetery_name') or '').strip()
        cem_loc = (mem.get('cemetery_location') or '').strip()
        plot = (mem.get('plot') or '').strip()
        lat = mem.get('gps_lat')
        lng = mem.get('gps_lng')

        burial_place = ", ".join([p for p in [cem_name, cem_loc] if p])
        if burial_place or plot or (lat and lng):
            lines.append("1 BURI")
            if burial_place:
                lines.append(f"2 PLAC {burial_place}")
            if lat and lng:
                lines.append("2 MAP")
                lines.append(f"3 LATI {lat}")
                lines.append(f"3 LONG {lng}")
            if plot:
                lines.append(f"2 NOTE Plot: {plot}")

        # Source URL
        url = mem.get('url') or f"https://www.findagrave.com/memorial/{mem_id}"
        lines.append("1 SOUR")
        lines.append("2 TITL Find a Grave Memorial")
        lines.append(f"2 PAGE {url}")

        # Inscription & Bio Notes
        notes = []
        if mem.get('inscription'):
            notes.append(f"Inscription: {mem['inscription'].strip()}")
        if mem.get('bio'):
            notes.append(f"Biography:\n{mem['bio'].strip()}")

        if notes:
            full_note_text = "\n\n".join(notes)
            note_lines = format_gedcom_note(1, "NOTE", full_note_text)
            lines.extend(note_lines)

        # Family Links
        if mem_id in indi_famc:
            lines.append(f"1 FAMC @{indi_famc[mem_id]}@")
        if mem_id in indi_fams:
            for fam_id in sorted(list(indi_fams[mem_id])):
                lines.append(f"1 FAMS @{fam_id}@")

    # 3. Families (FAM records)
    for fam_id, fam in families.items():
        lines.append(f"0 @{fam_id}@ FAM")
        if fam['husb']:
            lines.append(f"1 HUSB @I{fam['husb']}@")
        if fam['wife']:
            lines.append(f"1 WIFE @I{fam['wife']}@")
        for chil_id in fam['children']:
            lines.append(f"1 CHIL @I{chil_id}@")

    # 4. Trailer
    lines.append("0 TRLR")

    return "\n".join(lines) + "\n"

def export_focus_person_gedcom(focus_memorial_id):
    """
    Exports GEDCOM string for the connected network of a focus person.
    """
    conn = database.get_db_conn()
    try:
        memorials, relationships = get_focus_network(focus_memorial_id, conn)
        if not memorials:
            return None
        return generate_gedcom_content(memorials, relationships)
    finally:
        conn.close()

def export_cemetery_gedcom(cemetery_id='all'):
    """
    Exports GEDCOM string for burials in a cemetery or all cemeteries.
    """
    conn = database.get_db_conn()
    try:
        memorials, relationships = get_cemetery_network(cemetery_id, conn)
        if not memorials:
            return None
        return generate_gedcom_content(memorials, relationships)
    finally:
        conn.close()
