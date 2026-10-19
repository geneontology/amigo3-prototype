# Deployment sizing and cost notes

Planning assessment, 2026-10-10. These are estimates for modest public traffic,
not load-tested capacity guarantees or provider quotes. Prices exclude tax,
domain registration and engineering/operations labor. No deployment was purchased.

## Current prototype

Build `8753cdac17b0fd64`: 990,132 annotations; 76,562 gene/product records;
48,340 GO terms; 2,036 GO-CAMs; 436 modules; 3,768 families.
Observed disk use: about 2.3 GB of build artifacts (2.2 GB DuckDB, 96 MiB annotation
Parquet) plus 1 GB for the Solr core. Observed process RSS: about 2 GB Solr and
370 MB API; these are observations, not peak rebuild/concurrency measurements.
Start with 8 GB RAM and build snapshots separately from serving.

| Provider / configuration | Monthly price or allowance | Notes |
| --- | ---: | --- |
| Hetzner EU CX33, 8 GB | €8.49 base; €12–20 planning allowance | IPv4 and backups extra; availability must be checked |
| AWS Lightsail, 8 GB | $44 base; $45–55 with snapshot allowance | 160 GB disk; bundled transfer allowance |
| DigitalOcean Basic, 8 GB | $48; $57.60 with weekly backups | 160 GB disk; backups add 20% |
| SearchStax managed Solr | Advertised $53/node or $367/cluster, plus app hosting | Entry sizes require workload sizing; quoted rates billed annually |

Sources checked for this assessment: [Hetzner pricing](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/),
[Lightsail bundles](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html),
[Lightsail snapshots](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-faq-snapshots.html),
[DigitalOcean VMs](https://www.digitalocean.com/pricing/droplets),
[DigitalOcean backups](https://docs.digitalocean.com/products/backups/details/pricing/),
[SearchStax](https://www.searchstax.com/pricing/cloud/serverless-solr-service-pricing-details/).

Elastic Serverless advertises an illustrative $24/month search-only example for a
lightly used 2 GB dataset. This is not a quote for AmiGO: usage and idle search
baseline matter, and we would need an Elasticsearch adapter and app hosting.
See [Elastic pricing](https://www.elastic.co/pricing/serverless-search/).

## Reference species and all-UniProt projections

The June 2026 GO release reported 14,736,040 annotations and 1,750,636 gene products
across 171 species. Use 20–30 million annotation rows as initial headroom for the
roughly 200-species tier. [GO release announcement](https://community.alliancegenome.org/t/june-2026-go-release-2026-06-19-now-available-breaking-changes/8689).

UniProtKB 2026_03 lists roughly 150 million entries (575,748 reviewed and
149,430,635 unreviewed). [UniProt statistics](https://www.uniprot.org/entry).
GOA release 236 (2026-07-30) reports 675,087,194 associations across 116,660,498 gene
products; its universe is not identical to UniProtKB, but anchors full annotation
scale. [GOA release statistics](https://www.ebi.ac.uk/inc/drupal/goa/uniprot_release.html).
These are release-specific observations, not synchronized totals or a count of UniParc.

| Projection | Estimated primary search index | Starting serving hardware | Monthly serving budget |
| --- | ---: | --- | ---: |
| ~200 species, rich annotation browsing | 20–60 GB | One 16–32 GB server | $150–350 |
| All UniProtKB, compact document per protein | 150–400 GB | One 64–128 GB server | $600–1,500 |
| All UniProtKB plus every annotation row, replicated search | 0.5–1.5 TB | Several nodes; roughly 192–512 GB aggregate RAM | $1,500–5,000 |

Index sizes are extrapolations that require representative builds. Budgets assume
self-managed cloud VMs, supporting services and modest backups; transient rebuild
workers are separate. For grounding, DigitalOcean lists storage-optimized 64 GB
RAM/1.17 TB SSD at $524/month and 128 GB/2.34 TB at $1,048/month. These are hardware
price anchors, not evidence that a given query workload fits those machines.
[VM pricing](https://www.digitalocean.com/pricing/droplets).

## Recommended separation

1. Reference-species projection: full annotations, closures, evidence/provenance,
   review actions, modules and GO-CAM links.
2. All-UniProt projection: protein-level identifiers, taxonomy, families, direct GO
   terms and closures. Preserve evidence-bearing source annotations in partitioned
   DuckDB/Parquet for detail retrieval.
3. Add globally searchable annotation rows later if required. Protein facets count
   proteins; annotation facets count annotation rows. These are different products.

Plan around $1,000–2,000/month for the first two tiers together. A single server
requires recovery after host failure; backups alone are not high availability.
One replica doubles index storage. Keeping old/new releases during publication can
double that again, before merge space and source/build artifacts. Retain only a
few published releases locally and archive others separately.

For rebuild unit costs, 100 worker-hours on DigitalOcean 64–128 GB memory-optimized
VMs costs roughly $50–100 in compute at the listed $0.50–1/hour rates. This is an
illustration, not a measured build duration. Staging storage and transfer add costs.

The prototype currently materializes substantial Python collections. Before
all-UniProt, implement streaming/partitioned ingestion and selective detail reads;
then benchmark bytes per document, indexing throughput, peak memory, exact facet
latency and concurrency. Use those measurements to select shard/replica counts and
obtain a managed-service quote if operational support is worth the premium.
