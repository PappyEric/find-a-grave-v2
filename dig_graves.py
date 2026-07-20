# ------------------------------------------------\
#  Extract/report data for "Find a Grave" memorial pages.
#  Last update: 2024/06/10 @ 09:00pm.
#
#  Name:               dig_graves.py
#  URI:                https://github.com/doug-foster/find-a-grave-tools
#  Description:	       Extract/report data for "Find a Grave" memorial pages.
#  Version:            1.2.3
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
import glob  # https://docs.python.org/3/library/glob.html
import re  # https://docs.python.org/3/library/re.html
# Packages
from bs4 import BeautifulSoup  # https://www.crummy.com/software/BeautifulSoup/bs4/doc/
import xlsxwriter  # https://xlsxwriter.readthedocs.io/index.html
# My modules
import toolbox  # https://github.com/doug-foster/find-a-grave-tools
import grave_digger  # https://github.com/doug-foster/find-a-grave-tools

# --- Globals. ---
path_to_stash = grave_digger.path_to_stash
fag_prefix = grave_digger.fag_prefix
path_to_output = grave_digger.path_to_output
master_index = grave_digger.master_index
all_cells = 'A1:XFD1048576'

# --- Functions. ---

# --- Start. ---
toolbox.print_l('\nStarted script @ ' + time.strftime('%Y%m%d-%H%M%S') + '.')

# --- Read master file index. ---
grave_digger.read_master_index()

# --- Get digging instructions. ---
instructions = grave_digger.dig_instructions()

# --- Create workbook. ---
workbook_name = path_to_output + 'burials.xlsx'
workbook = xlsxwriter.Workbook(workbook_name)
workbook.set_size(1200, 800)

# --- Create formats. ---
format_bold = workbook.add_format({'bold': 1})
format_text = workbook.add_format({'num_format': '@'})
format_wrap = workbook.add_format({'text_wrap': True})
format_red = workbook.add_format({'font_color': 'red'})
formats = [format_bold, format_text, format_wrap, format_red]

# --- Loop cemeteries. ---
for cemetery_id, groups in instructions.items() :

	# -- Set cemetery id & abbreviation.
	cemetery_abrev =  cemetery_id.split('-')[1]  # Cemetery abbreviation.
	cemetery_id =  cemetery_id.split('-')[0]  # Cemetery id.

	# --- Get cemetery folder. ---
	cemetery_folder = glob.glob(path_to_stash + '/' + cemetery_id + '*_*/')[0]
	if not os.path.exists(cemetery_folder) :  # Does folder exit?
		toolbox.print_l('Error: Folder for cemetery id="' + cemetery_id +
			'" does not exist.')
		quit()

	# --- Get burial folder. ---
	burial_folder = cemetery_folder + cemetery_id + '_burials'
	if not os.path.exists(burial_folder) :  # Does folder exit?
		toolbox.print_l('Error: Burial folder for cemetery id="' + 
			cemetery_id + '" does not exist.')
		quit()

	# --- Get burial list. ---
	burial_list = burial_folder + '_list.txt'
	if not os.path.exists(burial_list) :  # Does list exit?
		toolbox.print_l('Error: Burial list for cemetery id="' + 
			cemetery_id + '" does not exist.')
		quit()

	# --- Build a list of burials. ---
	burials = []
	lines = []
	f = open(burial_list, 'r', encoding='utf-8')
	lines += f.read().splitlines()
	f.close()
	# Set file path for each burial file.
	for line in lines :
		line = '_'.join(line.rsplit('/',1))
		line = burial_folder + '/' + line.replace(fag_prefix, '') + '.html'
		burials.append(line)

	# --- Add a cemetery worksheet. ---
	worksheet_id = workbook.add_worksheet(cemetery_abrev)
	worksheet_id.ignore_errors({'number_stored_as_text': all_cells})
	worksheet_id.freeze_panes(1, 0)

	# --- Write cemetery worksheet header row. ---
	num_row = 0
	num_col = 0
	args = ['', num_row, path_to_stash, formats]
	cols_to_write = grave_digger.dig(args)  # Get header row.
	for cell in cols_to_write :  # Loop columns.
		worksheet_id.write(num_row, num_col, cell, format_bold)  # Write header.
		num_col += 1
	num_row = 1
	toolbox.print_l()

	# --- Worksheet data rows. ---
	for burial_file_name in burials :  # Each row is a burial.
		num_col = 0

		# Get data row.
		args = [burial_file_name, num_row, path_to_stash, formats]
		toolbox.print_l(str(num_row) + ' of ' + str(len(burials)) + ', ', '')
		toolbox.print_l('Cemetery: ' + cemetery_id + ', Memorial: ', '')
		cols_to_write = grave_digger.dig(args)
		rich_list = cols_to_write[2][1]  # Log name.
		full_name = rich_list[len(rich_list)-1]
		cols_to_write[2][1].remove(full_name)
		toolbox.print_l(full_name + ' ', '')
		# Write row data - one column at a time.
		for cell in cols_to_write :  # Loop columns.
			toolbox.print_l('.', '')
			# Write string.
			if str == type(cell) :
				worksheet_id.write(num_row, num_col, cell, format_wrap)
			elif list == type(cell) and 'url' == cell[0] :
				# Write link (list format is ['url'] [url] [text]).
				url = cell[1]
				text = cell[2]
				worksheet_id.write_url(num_row, num_col, url, string=text)
			elif list == type(cell) and 'rich_name' == cell[0] :
				# Write rich string
				worksheet_id.write_rich_string(num_row, num_col, *cell[1], 
					format_wrap)
			num_col += 1

		# Increment row.
		toolbox.print_l(' !',)
		num_row += 1
		# if 8 == num_row :  # Limit # of rows.
		# 	break

	# --- Wrap up cemetery worksheet. ---
	grave_digger.adjust_worksheet(worksheet_id)

# --- Finish. ---
workbook.close()
toolbox.print_l('Finished script @ ' + time.strftime('%Y%m%d-%H%M%S') + '.')

# ------------------------------------------------/
