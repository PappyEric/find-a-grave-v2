# ------------------------------------------------\
#  Create a local stash of "Find a Grave" memorial pages.
#  Last update: 2024/06/10 @ 03:15pm.
#
#  Name:               stash_graves.py
#  URI:                https://github.com/doug-foster/find-a-grave-tools
#  Description:	       Create a local stash of "Find a Grave" memorial pages
#  Version:            1.2.2
#  Requires at least:  3.1 Python
#  Prefers:            3.12 Python
#  Author:             Doug Foster
#  Author URI:         http://dougfoster.me
#  License:            GPL v3 or later
#  License URI:        https://www.gnu.org/licenses/agpl-3.0.html
#  Update URI:         https://github.com/doug-foster/find-a-grave-tools
#  Text Domain:        find-a-grave-tools
# ------------------------------------------------\

# --- Import libraries. ---
# Standard Libraries
import time  #https://docs.python.org/3/library/time.html
import os  # https://docs.python.org/3/library/os.html
import shutil  # https://docs.python.org/3/library/shutil.html
# Packages
import requests  # https://pypi.org/project/requests/
from bs4 import BeautifulSoup  # https://www.crummy.com/software/BeautifulSoup/bs4/doc/
# My modules
import toolbox  # https://github.com/doug-foster/find-a-grave-tools
import grave_digger  # https://github.com/doug-foster/find-a-grave-tools

# --- Globals. ---
cookie_domain = grave_digger.cookie_domain
path_to_stash = grave_digger.path_to_stash
burial_urls = []
master_list_of_urls = []
this_script = __file__.split('/')
this_script = this_script[len(this_script)-1]

# --- Functions. ---

# --- Get digging instructions. ---
instructions = grave_digger.dig_instructions()

# --- Start. ---
toolbox.print_l('Started script @ ' + time.strftime('%Y%m%d-%H%M%S') + '.')

# --- Establish a new "browser" session. ---
# https://stackoverflow.com/questions/73688432/python-request-with-cookies-content-blocked-by-cookie-banner
session = requests.Session()
descriptor = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML,'
descriptor += ' like Gecko) Chrome/104.0.5112.79 Safari/537.36'
headers = {
	'User-Agent': descriptor
}
session.headers.update(headers)
session.cookies.set("name", "notice_preferences", domain=cookie_domain)
session.cookies.set( "value", "2:", domain=cookie_domain)

# --- Digging instructions. ---
for cemetery_id, groups in instructions.items() : # Loop cemeteries.

	# -- Set cemetery id & abbreviation.
	cemetery_abrev =  cemetery_id.split('-')[1]  # Cemetery abbreviation.
	cemetery_id =  cemetery_id.split('-')[0]  # Cemetery id.

	# --- Check for valid cemetery. ---
	cemetery_url = 'https://findagrave.com/cemetery/' + cemetery_id
	request = toolbox.get_url(session, cemetery_url)  # Get page.
	toolbox.print_l()  # User status - readability.
	toolbox.print_l('Cemetery validated: ' + cemetery_url)

	# --- Cemetery vars. ---
	soup = BeautifulSoup(request.text, 'html.parser')
	cemetery_name = grave_digger.soup_find(soup, 'cemetery_name')
	cemetery_name = cemetery_name.rstrip().lstrip().lower().replace(' ', '-')
	cemetery_slug = cemetery_id + '_' + cemetery_name
	path_to_cemetery_folder = path_to_stash + cemetery_slug

	# --- Create cemetery folder? ---
	if not os.path.exists(path_to_cemetery_folder) :  # Does folder exist?
		os.mkdir(path_to_cemetery_folder)  # Create a new folder.
		if 'burial' not in groups :
			groups.insert(0, 'burial')  # Will need to find burials.
		time.sleep(1)

	# --- File & folder path dictionaries. ---
	path_to_list = {
		'burial' : path_to_cemetery_folder + '/' + cemetery_id + \
			'_burials_list.txt',
		'parent' : path_to_cemetery_folder + '/' + cemetery_id + \
			'_parents_list.txt',
		'spouse' : path_to_cemetery_folder + '/' + cemetery_id + \
			'_spouses_list.txt',
		'child' : path_to_cemetery_folder + '/' + cemetery_id + \
			'_children_list.txt',
		'sibling' : path_to_cemetery_folder + '/' + cemetery_id + \
			'_siblings_list.txt',
		'half-sibling' : path_to_cemetery_folder + '/' + cemetery_id + \
			'_half-siblings_list.txt'
	}
	path_to_folder = {
		'cemetery' : path_to_cemetery_folder,
		'burial' : path_to_cemetery_folder + '/' + cemetery_id + \
			'_burials',
		'parent' : path_to_cemetery_folder + '/' + cemetery_id + \
			'_parents',
		'spouse' : path_to_cemetery_folder + '/' + cemetery_id + \
			'_spouses',
		'child' : path_to_cemetery_folder + '/' + cemetery_id + \
			'_children',
		'sibling' : path_to_cemetery_folder + '/' + cemetery_id + \
			'_siblings',
		'half-sibling' : path_to_cemetery_folder + '/' + cemetery_id + \
			'_half-siblings'
	}

	# --- Digging instructions - loop groups. ---
	for group in groups :

		# --- Start group. ---
		start_time = time.strftime('%Y%m%d-%H%M%S')
		toolbox.print_l('Started group ' + group + ' @ ' + start_time + '.')
				
		# --- New group folder & folder list.  ---
		if os.path.isdir(path_to_folder[group]) :  # Folder exists?
			shutil.rmtree(path_to_folder[group])  # Remove.
		os.mkdir(path_to_folder[group])  # New folder.
		if os.path.isfile(path_to_list[group]) :  # List exists?
			os.remove(path_to_list[group])  # Remove list.

		# --- Build a new master list. ---
		# Burial group list created in grave_digger.find_burial_urls().
		# Other group lists created in stash_graves().
		master_list_of_urls = grave_digger.build_master_list()
		time.sleep(1)

		# --- Get "burial" group list. ---
		if 'burial' == group :
			args = [session, cemetery_id, path_to_list, group]
			# Fill a new burial list.
			burial_urls = grave_digger.find_burial_urls(args)
			num_burials = len(burial_urls)
		else :
			if 0 == len(burial_urls) :  # Get the list only once.
				f = open(path_to_list['burial'], 'r', encoding='utf-8')
				burial_urls = f.read().splitlines()
				f.close()
				num_burials = len(burial_urls)
		
		# --- # Loop burial URLs & stash pages for this group. ---
		toolbox.print_l()  # Blank line for better readabiity.
		if 'burial' != group :
				toolbox.print_l('Searching ' + str(len(burial_urls)) + 
		  			' burials in cemetery "' + cemetery_id + '", retrieving "' 
					+ group + '" pages ...')
				f = open(path_to_list[group], 'w', encoding='utf-8')  # Open family list file.
		this_burial = 1
		for burial_url in burial_urls :  
			if 'burial' == group :
				# Stash "burial" page.
				toolbox.print_l('Saving ' + str(this_burial) + ' of ' + 
					str(num_burials) + ' "burial" pages - ' + burial_url)
				args = [session, group, burial_url, '', path_to_folder]
				grave_digger.stash_group_page(args)
			else :
				# Stash "family" pages.
				toolbox.print_l(str(this_burial) + ' of ' + str(num_burials) 
		 			+ ' "burial" pages - ' + burial_url)  # User status.
				burial_slug = burial_url.split('/memorial/')[1]
				burial_slug = burial_slug.replace('/', '_')
				burial_page = path_to_folder['burial'] + '/' + \
					burial_slug + '.html'
				# Make burial soup.
				toolbox.print_l('- ' + burial_url)
				f_b = open(burial_page, 'r', encoding = 'utf8')
				soup = BeautifulSoup(f_b, 'html.parser')
				# Find "group" URLs for this burial.
				family_urls = grave_digger.find_family_urls(group, soup)
				# Loop "group" URLs.
				for family_url in family_urls :
					# If not a duplicate URL, stash the page.
					if family_url not in master_list_of_urls :
						args = [session, group, burial_url, family_url, 
							path_to_folder]
						# Stash page.
						grave_digger.stash_group_page(args)
						# Update family list.
						f.write(family_url + '\n')
			this_burial +=1

		# --- Close & tweak group list. ---
		if 'burial' != group :
			f.close()
			# Remove dangling "\n".
			toolbox.remove_last_byte(f, path_to_list[group])

		# --- Save the new master list. ---
		grave_digger.save_master_list()

		# --- Print # of pages in group. ---
		lines = len(os.listdir(path_to_folder[group]))
		toolbox.print_l (str(lines) + ' ' + group + ' pages.')

		# --- Take a breather if more groups to do. ---
		# Try to avoid looking like a DOS attack on the Find a Grave site.
		toolbox.print_l('Finished group ' + group + ' @ ' + 
			time.strftime('%Y%m%d-%H%M%S') + '.')
		if list(groups).index(group) < len(groups)-1 :
			toolbox.pause(10, 15, True)

# --- Create master index. ---
grave_digger.build_master_index()

# --- Wrap up. ---
toolbox.print_l()  # User status - readability.
stop_time = time.strftime('%Y%m%d-%H%M%S')
toolbox.print_l('Finished script @ ' + stop_time + '.')
toolbox.print_l('Done.')

# ------------------------------------------------\
