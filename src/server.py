#!/usr/bin/env python3
#
# Use [Flask](http://flask.pocoo.org) to serve a validation page.

import csv
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from copy import deepcopy
from flask import Flask, request, render_template
from os import path

from common import IriMaps, split_gate, extract_iri_special_label_maps, extract_iri_label_maps, \
  extract_iri_exact_label_maps, extract_suffix_syns_symbs_maps, update_iri_maps_from_owl


pwd = path.dirname(path.realpath(__file__))
app = Flask(__name__)

# Used for managing shared maps:
irimaps = IriMaps()


def load_maps():
  """
  Read data from various files in the build directory and use it to populate the maps (dicts)
  that will be used by the server.
  """
  def update_main_maps(to_iris={}, from_iris={}):
    # This inner function updates the synonyms_iris map with the contents of to_iris, and the
    # iri_labels map with the contents of from_iris.
    irimaps.iri_labels.update(from_iris)
    # to_iris maps labels to lists of iris, so flatten the lists here:
    for key in to_iris:
      # irimaps.synonym_iris.update({'{}'.format(key): '{}'.format(','.join(to_iris[key]))})
      irimaps.synonym_iris.update({'{}'.format(key): '{}'.format(to_iris[key][0])})

  # Read suffix symbols and suffix synonyms:
  with open(pwd + '/../build/value-scale.tsv') as f:
    rows = csv.DictReader(f, delimiter='\t')
    tmp_1, tmp_2 = extract_suffix_syns_symbs_maps(rows)
    irimaps.suffixsymbs.update(tmp_1)
    irimaps.suffixsyns.update(tmp_2)

  # Read special gates and update the synonym_iris and iris_labels maps
  with open(pwd + '/../build/special-gates.tsv') as f:
    rows = csv.DictReader(f, delimiter='\t')
    to_iris, from_iris = extract_iri_special_label_maps(rows)
    update_main_maps(to_iris, from_iris)

  # Read PR labels and update the synonym_iris and iris_labels maps
  with open(pwd + '/../build/pr-labels.tsv') as f:
    rows = csv.reader(f, delimiter='\t')
    to_iris, from_iris = extract_iri_label_maps(rows)
    update_main_maps(to_iris, from_iris)

  # Read PR synonyms and update the synonym_iris and iris_labels maps
  with open(pwd + '/../build/pr-exact-synonyms.tsv') as f:
    rows = csv.reader(f, delimiter='\t')
    to_iris = extract_iri_exact_label_maps(rows)
    update_main_maps(to_iris)

  with open(pwd + '/../build/cl-plus.owl') as f:
    source = f.read().strip()
    root = ET.fromstring(source)
    update_iri_maps_from_owl(root, irimaps.iri_gates, irimaps.iri_parents, irimaps.iri_labels,
                             irimaps.synonym_iris)


def decorate_gate(kind, level):
  """
  Create and return a dictionary with information on the supplied gate type and level
  """
  gate = {
    'kind': kind,
    'kind_recognized': False,
    'level': level,
    'level_recognized': False,
  }

  if kind in irimaps.iri_labels:
    gate['kind_recognized'] = True
    gate['kind_label'] = irimaps.iri_labels[kind]
  if kind and not kind.startswith('http'):
    gate['kind'] = '?gate=' + kind

  if level in irimaps.iri_labels:
    gate['level_recognized'] = True
    gate['level_label'] = irimaps.iri_labels[level]

  return gate


def process_gate(gate_string):
  """
  In the given gate, replace any suffix synonym with the standard suffix, decorate the
  gate, and then add the gate string, kind, and level information.
  """
  # If the gate string has a suffix which is a synonym of one of the standard suffixes, then replace
  # it with the standard suffix:
  for suffix in irimaps.suffixsyns.keys():
    if gate_string.casefold().endswith(suffix.casefold()):
      gate_string = re.sub(r'\s*' + re.escape(suffix) + r'$',
                           irimaps.suffixsymbs[irimaps.suffixsyns[suffix]],
                           gate_string, flags=re.IGNORECASE)

  # The 'kind' is the root of the gate string without the suffix, and the 'level' is the suffix
  kind_name, level_name = split_gate(gate_string, irimaps.suffixsymbs.values())
  # Anything in square brackets should be thought of as a 'comment' and not part of the kind.
  kind_name = re.sub(r'\s*\[.*\]\s*', r'', kind_name)
  kind = None
  if kind_name.casefold() in irimaps.synonym_iris:
    kind = irimaps.synonym_iris[kind_name.casefold()]
  level = None
  if level_name == '':
    level_name = '+'
  if level_name in irimaps.level_iris:
    level = irimaps.level_iris[level_name]
  gate = {}

  has_errors = False
  gate = decorate_gate(kind, level)
  if not kind:
    has_errors = True
  gate['gate'] = gate_string
  gate['kind_name'] = kind_name
  gate['level_name'] = irimaps.level_names[level_name]

  return gate, has_errors


def get_cell_name_and_gates(cells_field):
  """
  Parse out the name and gate list from the given cells field
  """
  cell_gates = []
  if '&' in cells_field:
    cells_fields = cells_field.split('&', maxsplit=1)
    # Remove any enclosing quotation marks and collapse extra spaces inside the string:
    cell_name = re.sub(r"^(\"|\')|(\"|\')$", r'', cells_fields[0].strip())
    cell_name = re.sub(r"\s\s+", r" ", cell_name)
    cell_gating = cells_fields[1].strip()

    if cell_gating:
      # Gates are assumed to be separated by commas
      gate_strings = list(csv.reader([cell_gating], quotechar='"', delimiter=',',
                                     quoting=csv.QUOTE_ALL, skipinitialspace=True)).pop()
      for gate_string in gate_strings:
        gate, has_errors = process_gate(gate_string.strip("'"))
        cell_gates.append(gate)
  else:
    cell_name = cells_field.strip().strip('"\'')

  return cell_name, cell_gates


def get_cell_core_info(cell_gates, cell_iri):
  """
  Initialise a dictionary which will contain information about this cell
  """
  cell = {'recognized': False, 'conflicts': False, 'has_cell_gates': len(cell_gates) > 0,
          'cell_gates': cell_gates}
  if cell_iri in irimaps.iri_gates:
    # If the cell IRI is in the IRI->Gates map, then add its IRI and flag it as recognised.
    cell['recognized'] = True
    cell['iri'] = cell_iri
    if cell_iri in irimaps.iri_labels:
      # If the cell is in the IRI->Labels map, then add its label
      cell['label'] = irimaps.iri_labels[cell_iri]
    if cell_iri in irimaps.iri_parents:
      # It it is in the IRI->Parents map, then add its parent's IRI
      cell['parent'] = irimaps.iri_parents[cell_iri]
      if cell['parent'] in irimaps.iri_labels:
        # If its parent's IRI is in the IRI->Labels map, then add its parent's label
        cell['parent_label'] = irimaps.iri_labels[cell['parent']]

  return cell


def get_gate_info_for_cell(cell_iri):
  """
  For each gate associated with the cell IRI, create a dictionary with information about it
  and append it to a list which is eventually returned.
  """
  cell_results = []

  if cell_iri:
    for gate in irimaps.iri_gates[cell_iri]:
      gate = decorate_gate(gate['kind'], gate['level'])
      if gate['level'] in irimaps.iri_levels:
        gate['level_name'] = irimaps.level_names[irimaps.iri_levels[gate['level']]]
      cell_results.append(gate)

  return cell_results


def get_cell_iri(cell_name):
  """
  Find the IRI for the cell based on cell_name, which can be a: label/synonym, ID, or IRI.
  """
  if cell_name.casefold() in irimaps.synonym_iris:
    cell_iri = irimaps.synonym_iris[cell_name.casefold()]
  elif cell_name in irimaps.iri_labels:
    cell_iri = cell_name
  else:
    iri = re.sub('^CL:', 'http://purl.obolibrary.org/obo/CL_', cell_name)
    cell_iri = iri if iri in irimaps.iri_labels else None

  return cell_iri


def parse_cells_field(cells_field):
  """
  Create and return a dictionary with information about the cell extracted from the given
  cells_field: its name, its associated gates, its IRI, and other core information about the cell.
  """
  cell = {}
  # Extract the cell name and the gates specified in the cells field of the request string
  cell_name, cell_gates = get_cell_name_and_gates(cells_field)
  # Get the cell and gate information for the gates specified in the cells field
  cell_iri = get_cell_iri(cell_name)
  cell['core_info'] = get_cell_core_info(cell_gates, cell_iri)
  # Include the information from cell_gates (the gates specified in the request) to cell_results
  # (the list of gates extracted based on the cell's IRI)
  cell['results'] = get_gate_info_for_cell(cell_iri) + cell_gates
  return cell


def parse_gates_field(gates_field, cell):
  """
  Parses the gates field submitted through the web form for a given cell.
  The gates field should be a list of gates separated by commas.
  Also check for and indicate any discrepancies between the gates information and the extracted
  cell info.
  """
  gating = {'results': [], 'conflicts': [], 'has_errors': False}
  # Assume gates are separated by commas
  gate_strings = list(csv.reader([gates_field], quotechar='"', delimiter=',', quoting=csv.QUOTE_ALL,
                                 skipinitialspace=True)).pop()
  for gate_string in gate_strings:
    gate, has_errors = process_gate(gate_string)
    if has_errors and not gating['has_errors']:
      gating['has_errors'] = True

    # Check for any discrepancies between what has been given through the request and the gate info
    # that has been extracted (cell_results) based on a lookup of the cell IRIs. Indicate any such
    # in the info for the gate, and append the gate info to a list of gates with conflicts. Either
    # way, append the gate into to the gate_results list.
    for cell_result in cell['results']:
      if gate['kind'] == cell_result['kind'] and gate['level'] != cell_result['level']:
        gate['conflict'] = True
        cell_result['conflict'] = True
        cell['core_info']['conflicts'] = True
        conflict = deepcopy(gate)
        conflict['cell_level'] = cell_result['level']
        conflict['cell_level_name'] = cell_result['level_name']
        gating['conflicts'].append(conflict)
    gating['results'].append(gate)

  return gating


@app.route('/', methods=['GET'])
def my_app():
  if 'gate' in request.args:
    special_gate = request.args['gate'].strip()
    return render_template('/gate.html', special_gate=special_gate)

  # cells_field holds cell population names from the cell ontology database; if not specified, it's
  # initialised to the following default value. Otherwise we get it from the request
  cells_field = 'CD4-positive, alpha-beta T cell & CD19-'
  if 'cells' in request.args:
    cells_field = request.args['cells'].strip().replace("‘", "'").replace("’", "'")

  # Parse the cells_field
  cell = parse_cells_field(cells_field)

  # gates_field holds gate names from the protein ontology database; if not specified, it gets
  # initialised to the following default value, otherwise get it from the request.
  gates_field = 'CD4-, CD19+, CD20-, CD27++, CD38+-, CD56[glycosylated]+'
  if 'gates' in request.args:
    gates_field = request.args['gates'].strip().replace("‘", "'").replace("’", "'")

  # Parse the gates_field
  gating = parse_gates_field(gates_field, cell)

  # Serve the web page back with the generated info
  return render_template(
    '/index.html',
    cells=cells_field,
    gates=gates_field,
    cell=cell['core_info'],
    cell_results=cell['results'],
    gate_results=gating['results'],
    gate_errors=gating['has_errors'],
    conflicts=gating['conflicts'])


if __name__ == '__main__':
  """
  At startup, the main function reads information from files in the build directory and uses it to
  populate our global dictionaries. It then starts the Flask application.
  """
  load_maps()
  app.debug = True
  app.run()


def test_server():
  irimaps.synonym_iris = {
    'b-cell differentiation antigen ly-44': 'http://purl.obolibrary.org/obo/PR_000001289',
    'b-cell surface antigen cd20': 'http://purl.obolibrary.org/obo/PR_000001289',
    'b-lymphocyte antigen cd20': 'http://purl.obolibrary.org/obo/PR_000001289',
    'b-lymphocyte surface antigen b1': 'http://purl.obolibrary.org/obo/PR_000001289',
    'b-lymphocyte surface antigen b4': 'http://purl.obolibrary.org/obo/PR_000001002',
    'bp35': 'http://purl.obolibrary.org/obo/PR_000001289',
    'cd103': 'http://purl.obolibrary.org/obo/PR_000001010',
    'cd103-positive dendritic cell': 'http://purl.obolibrary.org/obo/CL_0002461',
    'cd11 antigen-like family member c': 'http://purl.obolibrary.org/obo/PR_000001013',
    'cd11c': 'http://purl.obolibrary.org/obo/PR_000001013',
    'cd19': 'http://purl.obolibrary.org/obo/PR_000001002',
    'cd19 molecule': 'http://purl.obolibrary.org/obo/PR_000001002',
    'cd20': 'http://purl.obolibrary.org/obo/PR_000001289',
    'cd3 epsilon': 'http://purl.obolibrary.org/obo/PR_000001020',
    'cd34': 'http://purl.obolibrary.org/obo/PR_000001003',
    'cd34 molecule': 'http://purl.obolibrary.org/obo/PR_000001003',
    'cd3e': 'http://purl.obolibrary.org/obo/PR_000001020',
    'cd4': 'http://purl.obolibrary.org/obo/PR_000001004',
    'cd4 molecule': 'http://purl.obolibrary.org/obo/PR_000001004',
    'cd45ra': 'http://purl.obolibrary.org/obo/PR_000001015',
    'cd56': 'http://purl.obolibrary.org/obo/PR_000001024',
    'cd8alphabeta': 'http://purl.obolibrary.org/obo/PR_000025402',
    'differentiation antigen cd19': 'http://purl.obolibrary.org/obo/PR_000001002',
    'effector cd4-positive, alpha-beta t cell': 'http://purl.obolibrary.org/obo/CL_0001044',
    'effector cd4-positive, alpha-beta t lymphocyte': 'http://purl.obolibrary.org/obo/CL_0001044',
    'effector cd4-positive, alpha-beta t-cell': 'http://purl.obolibrary.org/obo/CL_0001044',
    'effector cd4-positive, alpha-beta t-lymphocyte': 'http://purl.obolibrary.org/obo/CL_0001044',
    'hematopoietic progenitor cell antigen cd34': 'http://purl.obolibrary.org/obo/PR_000001003',
    'hml-1 antigen': 'http://purl.obolibrary.org/obo/PR_000001010',
    'integrin alpha m290': 'http://purl.obolibrary.org/obo/PR_000001010',
    'integrin alpha-e': 'http://purl.obolibrary.org/obo/PR_000001010',
    'integrin alpha-iel': 'http://purl.obolibrary.org/obo/PR_000001010',
    'integrin alpha-x': 'http://purl.obolibrary.org/obo/PR_000001013',
    'itgae': 'http://purl.obolibrary.org/obo/PR_000001010',
    'itgax': 'http://purl.obolibrary.org/obo/PR_000001013',
    'leu m5': 'http://purl.obolibrary.org/obo/PR_000001013',
    'leukocyte adhesion glycoprotein p150,95 alpha chain': 'http://purl.obolibrary.org/obo/PR_000001013',
    'leukocyte adhesion receptor p150,95': 'http://purl.obolibrary.org/obo/PR_000001013',
    'leukocyte surface antigen leu-16': 'http://purl.obolibrary.org/obo/PR_000001289',
    'lymphocyte antigen 44': 'http://purl.obolibrary.org/obo/PR_000001289',
    'membrane-spanning 4-domains subfamily a member 1': 'http://purl.obolibrary.org/obo/PR_000001289',
    'ms4a1': 'http://purl.obolibrary.org/obo/PR_000001289',
    'mucosal lymphocyte 1 antigen': 'http://purl.obolibrary.org/obo/PR_000001010',
    'n-cam-1': 'http://purl.obolibrary.org/obo/PR_000001024',
    'ncam-1': 'http://purl.obolibrary.org/obo/PR_000001024',
    'ncam1': 'http://purl.obolibrary.org/obo/PR_000001024',
    'neural cell adhesion molecule 1': 'http://purl.obolibrary.org/obo/PR_000001024',
    'ptprc/iso:cd45ra': 'http://purl.obolibrary.org/obo/PR_000001015',
    'receptor-type tyrosine-protein phosphatase c isoform cd45ra': 'http://purl.obolibrary.org/obo/PR_000001015',
    't cell receptor co-receptor cd8': 'http://purl.obolibrary.org/obo/PR_000025402',
    't-cell differentiation antigen l3t4': 'http://purl.obolibrary.org/obo/PR_000001004',
    't-cell surface antigen leu-12': 'http://purl.obolibrary.org/obo/PR_000001002',
    't-cell surface antigen t3/leu-4 epsilon chain': 'http://purl.obolibrary.org/obo/PR_000001020',
    't-cell surface antigen t4/leu-3': 'http://purl.obolibrary.org/obo/PR_000001004',
    't-cell surface glycoprotein cd4': 'http://purl.obolibrary.org/obo/PR_000001004',
    'tcr co-receptor cd8': 'http://purl.obolibrary.org/obo/PR_000025402',
    'blr2': 'http://purl.obolibrary.org/obo/PR_000001203',
    'c-c chemokine receptor type 7': 'http://purl.obolibrary.org/obo/PR_000001203',
    'c-c ckr-7': 'http://purl.obolibrary.org/obo/PR_000001203',
    'cc-ckr-7': 'http://purl.obolibrary.org/obo/PR_000001203',
    'ccr-7': 'http://purl.obolibrary.org/obo/PR_000001203',
    'ccr7': 'http://purl.obolibrary.org/obo/PR_000001203',
    'cd197': 'http://purl.obolibrary.org/obo/PR_000001203',
    'cdw197': 'http://purl.obolibrary.org/obo/PR_000001203',
    'chemokine receptor ccr7': 'http://purl.obolibrary.org/obo/PR_000001203',
    'ebi1': 'http://purl.obolibrary.org/obo/PR_000001203',
    'ebv-induced g-protein coupled receptor 1': 'http://purl.obolibrary.org/obo/PR_000001203',
    'epstein-barr virus-induced g-protein coupled receptor 1': 'http://purl.obolibrary.org/obo/PR_000001203',
    'mip-3 beta receptor': 'http://purl.obolibrary.org/obo/PR_000001203',
  }

  irimaps.iri_labels = {
    'http://purl.obolibrary.org/obo/CL_0001044': 'effector CD4-positive, alpha-beta T cell',
    'http://purl.obolibrary.org/obo/PR_000001004': 'CD4 molecule',
    'http://purl.obolibrary.org/obo/RO_0002104': 'has plasma membrane part',
    'http://purl.obolibrary.org/obo/PR_000001203': 'C-C chemokine receptor type 7',
    'http://purl.obolibrary.org/obo/PR_000001015': 'receptor-type tyrosine-protein phosphatase C isoform CD45RA',
    'http://purl.obolibrary.org/obo/cl#lacks_plasma_membrane_part': 'lacks plasma membrane part',
    'http://purl.obolibrary.org/obo/PR_000025402': 'T cell receptor co-receptor CD8',
    'http://purl.obolibrary.org/obo/CL_0002461': 'CD103-positive dendritic cell',
    'http://purl.obolibrary.org/obo/PR_000001010': 'integrin alpha-E',
    'http://purl.obolibrary.org/obo/PR_000001013': 'integrin alpha-X',
    'http://purl.obolibrary.org/obo/cl#has_high_plasma_membrane_amount': 'has high plasma membrane amount',
    'http://purl.obolibrary.org/obo/PR_000001002': 'CD19 molecule',
    'http://purl.obolibrary.org/obo/PR_000001003': 'CD34 molecule',
    'http://purl.obolibrary.org/obo/PR_000001020': 'CD3 epsilon',
    'http://purl.obolibrary.org/obo/PR_000001024': 'neural cell adhesion molecule 1',
    'http://purl.obolibrary.org/obo/PR_000001289': 'membrane-spanning 4-domains subfamily A member 1',
  }

  irimaps.iri_parents = {}

  irimaps.iri_gates = {
    'http://purl.obolibrary.org/obo/CL_0001044': [
      {'kind': 'http://purl.obolibrary.org/obo/PR_000001004',
       'level': 'http://purl.obolibrary.org/obo/RO_0002104'},
      {'kind': 'http://purl.obolibrary.org/obo/PR_000001015',
       'level': 'http://purl.obolibrary.org/obo/RO_0002104'},
      {'kind': 'http://purl.obolibrary.org/obo/PR_000001203',
       'level': 'http://purl.obolibrary.org/obo/cl#lacks_plasma_membrane_part'},
      {'kind': 'http://purl.obolibrary.org/obo/PR_000025402',
       'level': 'http://purl.obolibrary.org/obo/cl#lacks_plasma_membrane_part'}],
    'http://purl.obolibrary.org/obo/CL_0002461': [
      {'kind': 'http://purl.obolibrary.org/obo/PR_000001010',
       'level': 'http://purl.obolibrary.org/obo/RO_0002104'},
      {'kind': 'http://purl.obolibrary.org/obo/PR_000001013',
       'level': 'http://purl.obolibrary.org/obo/cl#has_high_plasma_membrane_amount'},
      {'kind': 'http://purl.obolibrary.org/obo/PR_000001002',
       'level': 'http://purl.obolibrary.org/obo/cl#lacks_plasma_membrane_part'},
      {'kind': 'http://purl.obolibrary.org/obo/PR_000001003',
       'level': 'http://purl.obolibrary.org/obo/cl#lacks_plasma_membrane_part'},
      {'kind': 'http://purl.obolibrary.org/obo/PR_000001020',
       'level': 'http://purl.obolibrary.org/obo/cl#lacks_plasma_membrane_part'},
      {'kind': 'http://purl.obolibrary.org/obo/PR_000001024',
       'level': 'http://purl.obolibrary.org/obo/cl#lacks_plasma_membrane_part'},
      {'kind': 'http://purl.obolibrary.org/obo/PR_000001289',
       'level': 'http://purl.obolibrary.org/obo/cl#lacks_plasma_membrane_part'}]
  }

  irimaps.suffixsymbs = {
    'high': '++',
    'intermediate': '+~',
    'low': '+-',
    'positive': '+',
    'negative': '-'
  }

  irimaps.suffixsyns = OrderedDict([
    ('high', 'high'),
    ('bright', 'high'),
    ('hi', 'high'),
    ('intermediate', 'intermediate'),
    ('int', 'intermediate'),
    ('medium', 'intermediate'),
    ('med', 'intermediate'),
    ('low', 'low'),
    ('dim', 'low'),
    ('lo', 'low'),
    ('positive', 'positive'),
    ('pos', 'positive'),
    ('negative', 'negative'),
    ('neg', 'negative')]
  )

  cells_field = 'effector CD4-positive, alpha-beta T cell & CD19-'
  gates_field = 'CD3e+, CD20++, CD103-, itgax[This is a comment]++, ncam1+~'
  cell = parse_cells_field(cells_field)
  gating = parse_gates_field(gates_field, cell)

  assert cell == {
    'core_info': {
      'cell_gates': [
        {'gate': 'CD19-',
         'kind': 'http://purl.obolibrary.org/obo/PR_000001002',
         'kind_label': 'CD19 molecule',
         'kind_name': 'CD19',
         'kind_recognized': True,
         'level': 'http://purl.obolibrary.org/obo/cl#lacks_plasma_membrane_part',
         'level_label': 'lacks plasma membrane part',
         'level_name': 'negative',
         'level_recognized': True}],
      'conflicts': False,
      'has_cell_gates': True,
      'iri': 'http://purl.obolibrary.org/obo/CL_0001044',
      'label': 'effector CD4-positive, alpha-beta T cell',
      'recognized': True},
    'results': [
      {'kind': 'http://purl.obolibrary.org/obo/PR_000001004',
       'kind_label': 'CD4 molecule',
       'kind_recognized': True,
       'level': 'http://purl.obolibrary.org/obo/RO_0002104',
       'level_label': 'has plasma membrane part',
       'level_name': 'positive',
       'level_recognized': True},
      {'kind': 'http://purl.obolibrary.org/obo/PR_000001015',
       'kind_label': 'receptor-type tyrosine-protein phosphatase C '
       'isoform CD45RA',
       'kind_recognized': True,
       'level': 'http://purl.obolibrary.org/obo/RO_0002104',
       'level_label': 'has plasma membrane part',
       'level_name': 'positive',
       'level_recognized': True},
      {'kind': 'http://purl.obolibrary.org/obo/PR_000001203',
       'kind_label': 'C-C chemokine receptor type 7',
       'kind_recognized': True,
       'level': 'http://purl.obolibrary.org/obo/cl#lacks_plasma_membrane_part',
       'level_label': 'lacks plasma membrane part',
       'level_name': 'negative',
       'level_recognized': True},
      {'kind': 'http://purl.obolibrary.org/obo/PR_000025402',
       'kind_label': 'T cell receptor co-receptor CD8',
       'kind_recognized': True,
       'level': 'http://purl.obolibrary.org/obo/cl#lacks_plasma_membrane_part',
       'level_label': 'lacks plasma membrane part',
       'level_name': 'negative',
       'level_recognized': True},
      {'gate': 'CD19-',
       'kind': 'http://purl.obolibrary.org/obo/PR_000001002',
       'kind_label': 'CD19 molecule',
       'kind_name': 'CD19',
       'kind_recognized': True,
       'level': 'http://purl.obolibrary.org/obo/cl#lacks_plasma_membrane_part',
       'level_label': 'lacks plasma membrane part',
       'level_name': 'negative',
       'level_recognized': True}]}

  assert gating == {
    'conflicts': [],
    'has_errors': False,
    'results': [
      {'gate': 'CD3e+',
       'kind': 'http://purl.obolibrary.org/obo/PR_000001020',
       'kind_label': 'CD3 epsilon',
       'kind_name': 'CD3e',
       'kind_recognized': True,
       'level': 'http://purl.obolibrary.org/obo/RO_0002104',
       'level_label': 'has plasma membrane part',
       'level_name': 'positive',
       'level_recognized': True},
      {'gate': 'CD20++',
       'kind': 'http://purl.obolibrary.org/obo/PR_000001289',
       'kind_label': 'membrane-spanning 4-domains subfamily A member 1',
       'kind_name': 'CD20',
       'kind_recognized': True,
       'level': 'http://purl.obolibrary.org/obo/cl#has_high_plasma_membrane_amount',
       'level_label': 'has high plasma membrane amount',
       'level_name': 'high',
       'level_recognized': True},
      {'gate': 'CD103-',
       'kind': 'http://purl.obolibrary.org/obo/PR_000001010',
       'kind_label': 'integrin alpha-E',
       'kind_name': 'CD103',
       'kind_recognized': True,
       'level': 'http://purl.obolibrary.org/obo/cl#lacks_plasma_membrane_part',
       'level_label': 'lacks plasma membrane part',
       'level_name': 'negative',
       'level_recognized': True},
      {'gate': 'itgax[This is a comment]++',
       'kind': 'http://purl.obolibrary.org/obo/PR_000001013',
       'kind_label': 'integrin alpha-X',
       'kind_name': 'itgax',
       'kind_recognized': True,
       'level': 'http://purl.obolibrary.org/obo/cl#has_high_plasma_membrane_amount',
       'level_label': 'has high plasma membrane amount',
       'level_name': 'high',
       'level_recognized': True},
      {'gate': 'ncam1+~',
       'kind': 'http://purl.obolibrary.org/obo/PR_000001024',
       'kind_label': 'neural cell adhesion molecule 1',
       'kind_name': 'ncam1',
       'kind_recognized': True,
       'level': 'http://purl.obolibrary.org/obo/RO_0002104',
       'level_label': 'has plasma membrane part',
       'level_name': 'medium',
       'level_recognized': True}]}
