#!/usr/bin/env python3

import argparse
import csv
import re
import sys


def main():
    # Define command-line parameters
    parser = argparse.ArgumentParser(description='Parse PRO-short-labels from a file that has been '
                                     'generated using the rapper Raptor RDF parsing and '
                                     'serializing utility')
    parser.add_argument('infile', type=argparse.FileType('r'),
                        help='a file generated by rapper consisting of N-triples')

    # Parse command-line parameters
    args = parser.parse_args()
    # Each line is composed of four fields, the last being a '.' end of line marker which we ignore
    ntriples = csv.DictReader(
        args.infile, delimiter=' ', fieldnames=['first', 'second', 'third', 'end'])
    w = csv.writer(sys.stdout, delimiter='\t', lineterminator='\n')

    last_genid = None
    ontology_url = None
    label = None
    has_exact_synonym = False
    is_pro_short_label = False
    for ntriple in ntriples:
        # We are only concerned with lines whose first entry contains a genid:
        if not re.match('_:genid', ntriple['first']):
            continue

        # Every genid will have a block of lines associated with it, which will be encountered in
        # the file sequentially. Once we see a new one, the previous one has been processed.
        if ntriple['first'] != last_genid:
            last_genid = ntriple['first']
            ontology_url = None
            label = None
            has_exact_synonym = False
            is_pro_short_label = False

        # Check the current line to see if it provides information regarding whether this block
        # describes the short label of an exact synonym, and what the short label and ontology is.
        if not has_exact_synonym and 'oboInOwl#hasExactSynonym' in ntriple['third']:
            has_exact_synonym = True
        if not is_pro_short_label and 'pr#PRO-short-label' in ntriple['third']:
            is_pro_short_label = True
        if (not ontology_url and 'owl#annotatedSource' in ntriple['second'] and
            re.match('<http://purl.obolibrary.org/obo/PR_0',  ntriple['third'])):
            ontology_url = ntriple['third'].strip('<>')
        if not label and 'owl#annotatedTarget' in ntriple['second']:
            label = ntriple['third'].split('^^')[0].strip('"')

        # Only report something if this block describes something which has an exact synonym
        # and a short label and we have found the label and ontology id.
        if has_exact_synonym and is_pro_short_label and ontology_url and label:
            w.writerow([ontology_url, label])


if __name__ == "__main__":
    main()
