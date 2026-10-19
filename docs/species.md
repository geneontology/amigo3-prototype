# Loaded annotation species

`config/sources.yaml` defines the annotation inputs:

| Browser species | Taxon | Source |
| --- | --- | --- |
| Human (`human`) | NCBITaxon:9606 | GO `HUMAN-uniprot` GAF/GPI, with HGNC mappings |
| Fission yeast (`pombe`) | NCBITaxon:4896 | GO `SCHPO-mod` GAF/GPI |
| C. elegans (`worm`) | NCBITaxon:6239 | GO `CAEEL-mod` GAF/GPI |
| Arabidopsis thaliana (`ARATH`) | NCBITaxon:3702 | GO `ARATH-mod` GAF/GPI |
| Fruit fly (`fly`) | NCBITaxon:7227 | GO `DROME-mod` GAF/GPI |
| Dictyostelium discoideum (`DICDI`) | NCBITaxon:44689 | GO `DICDI-mod` GAF/GPI |
| Neurospora crassa OR74A (`NEUCR`) | NCBITaxon:367110 | GO `NEUCR-uniprot` GAF/GPI |
| Populus trichocarpa (`POPTR`) | NCBITaxon:3694 | GO `POPTR-uniprot` GAF/GPI |
| P. putida KT2440 (`PSEPK`) | NCBITaxon:160488 | UniProt-GOA `109.P_putida_KT2440.goa` (GAF 2.2) |

The worm inputs are the [GO-published MOD annotations](https://current.geneontology.org/annotations/gaf/CAEEL-mod.gaf.gz) and [entity catalog](https://current.geneontology.org/annotations/gpi/CAEEL-mod.gpi.gz). WormBase `WB:WBGene…` identifiers remain primary; the GPI's explicit UniProt cross-references connect protein identifiers and repository reviews to those records. `WormBase:` and bare `WBGene…` identifiers also resolve through the API.

Arabidopsis and fly use the MOD files listed in [GO's annotation downloads](https://geneontology.org/docs/download-go-annotations/downloads/). Their primary identifiers remain `AGI_LocusCode:AT…` and `FB:FBgn…`; explicit GPI cross-references connect UniProt products and reviews. The species hierarchy places Arabidopsis under Viridiplantae and fly under Metazoa.

Dictyostelium uses the dictyBase MOD files; `dictyBase:DDB_G…` identifiers stay primary and GPI UniProt cross-references connect reviews, which ai-gene-review keys by UniProt accession. Neurospora and poplar have no MOD files, so they use GO's UniProt-sourced files and UniProtKB identifiers are primary. GO annotates Neurospora at the OR74A strain (367110), not the species taxon. GO's `-uniprot` files cover only each species' UniProt reference proteome, so entries outside it are absent even when they are Swiss-Prot and annotated in UniProt-GOA. For poplar (proteome UP000006729) this omits six Swiss-Prot entries that have ai-gene-review reviews (PAL, CCOAOMT1, CCOAOMT2, WIN6, PRX2 and NADP-ME), and about 3,200 mostly TrEMBL entries with GO annotations; their reviews cannot attach. The species filter places Dictyostelium under Amoebozoa, poplar under Viridiplantae and Neurospora under Fungi.

The [KT2440 file](https://ftp.ebi.ac.uk/pub/databases/GO/goa/proteomes/109.P_putida_KT2440.goa) is one strain, not all P. putida strains. Its GOA filename number **109 is not a taxonomy ID**. No separate GPI is configured: annotated UniProt subjects form gene-product records with their source names and synonyms. We do not infer equivalence from a shared gene symbol or turn locus tags into invented identifiers.

Reviews are discovered across repository organism directories, including `worm/`, `PSEPK/`, and `PSEPU/`, and attached only through matching identifiers. A bare review `id` is a UniProt accession, or an RNAcentral ID when it has the `URS…_taxon` form. GAF-only subjects participate in review matching. Reviews remain an overlay; upstream assertions are unchanged.

## Adding a species

1. Find GO's files for the species in the [annotation downloads](https://current.geneontology.org/annotations/index.html). Prefer a `-mod` GAF/GPI pair, whose MOD identifiers become primary, over `-uniprot`.
2. In `config/sources.yaml`, add an entry under `species:` with a key, the browser `label` and the NCBI `taxon` that the GAF uses. Use the ai-gene-review organism code as the key when you can. Otherwise list that code under `aliases` so projects and reports resolve to the species.
3. Add the GAF and GPI sources with `species: <key>`. Add `allowed_taxa` if the GAF also annotates strains.
4. Run `amigo3 fetch`, then build. Fetch downloads the new files and UniProt lineages for every configured taxon. The build places the species under its nearest clade in the species filter and gives gap filling its full lineage.
5. Only if the species falls outside the clades listed in `CLADES` (`src/amigo3/hierarchies.py`) and should not attach to Eukaryota, Bacteria or Archaea directly, add its clade there. Only if its MOD identifier prefix is new, add a link in `identifierLinks` (`src/amigo3/web/gene.js`).
6. Add a row to the table above.

## Refresh and publish

```bash
uv run amigo3 fetch
uv run amigo3 build --repository /path/to/ai-gene-review
uv run amigo3 publish
uv run amigo3 serve
```

Fetch reuses verified cached bytes and downloads newly configured sources. Use `fetch --refresh` deliberately to advance existing sources. Each manifest preserves checksums, source URLs, dates and raw headers; different providers may have different release dates.

GAF subject taxa must match the configured taxon or explicit `allowed_taxa`. PomBase includes the reference strain NCBITaxon:284812 as well as species NCBITaxon:4896. Browser species controls derive from the pinned manifest. `scripts/verify_live.py` compares per-species facets and totals between DuckDB and Solr.

## RefSeq prokaryotic GO pilot

NCBI assembly directories can provide `*_gene_ontology.gaf.gz` files with RefSeq
protein GO annotations. See the [KT2440 comparison and download instructions](refseq-go-comparison.md)
for a reproducible DuckDB experiment against the UniProt-GOA file used here.
The experiment also prepares inputs for the optional [NCBI preload](predictions.md),
which compares against the application's frozen baseline without adding NCBI rows
to the standard annotation or enrichment counts.
