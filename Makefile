

build:
	mkdir $@

# Extract a table of oio:hasExactSynonyms for (proper) PR terms.
# We want the "PRO-short-label" but unfortunately that's in an OWL annotation property.
build/pr-exact-synonyms.tsv: build/pr.owl | build
	rapper $< \
	| grep '^<http://purl.obolibrary.org/obo/PR_0' \
	| grep '> <http://www.geneontology.org/formats/oboInOwl#hasExactSynonym> "' \
	| sed 's/^<\(.*\)> <.*> "\(.*\)".*$$/\1	\2/' \
	| sed 's!^http://purl.obolibrary.org/obo/PR_!PR:!' \
	> $@

build/normalized.tsv: src/normalize.py special-gates.tsv source.tsv | build
	$^ $@

build/known-gates.tsv: special-gates.tsv build/pr-exact-synonyms.tsv
	cat $^ | cut -f 1-2 > $@

build/gates.tsv: src/find-gates.py build/known-gates.tsv build/normalized.tsv
	$^ $@

build/report.tsv: src/report.py build/normalized.tsv build/gates.tsv
	$^ $@

build/summary.tsv: src/summarize.py build/report.tsv
	$^ $@
