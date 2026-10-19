# Gene-set screen for network illustrations

## What this screen demonstrates

Hallmarks are constructed summary signatures, not gene lists from individual
experimental contrasts. MSigDB generates them by combining overlapping source
sets and selecting coherently expressed representatives; see the
[official collection description](https://www.gsea-msigdb.org/gsea/msigdb/collections.jsp).
Their biological coherence is partly built into their construction. Consequently,
this screen supplies curated demonstration examples, not a realistic experimental
benchmark or independent validation of neighborhood discovery.

A stronger follow-up would use original perturbation-versus-control contrasts
with source publication/accession, cell type, treatment, time point and gene
selection thresholds recorded. Up- and down-regulated genes should be handled
separately, with the measured/tested genes as the custom background where
available. Select a biologically varied panel before inspecting its network
scores and retain negative results. Published study-specific signatures can be
an initial source, provided their original provenance and selection rules are
checked rather than assuming every MSigDB set represents one experiment.

That follow-up is now recorded in the
[study-derived screen beyond Hallmarks](study-derived-network-illustrations.md):
402 signatures from 222 publications, with ten candidates rerun at higher
precision. None of the selected neighborhoods passed BY 0.05 across all three
seeds; EZH2 knockdown was borderline. Those results provide a useful comparison
with the stronger constructed Hallmark examples below.

## Scope and settings

We screened **all 50 human MSigDB Hallmark sets**, rather than selecting only favorable signatures. Memberships were fetched from MyGeneset (MSigDB 2026.1), mapped to build `48879e043488a9a3`, and frozen in [inputs.json](data/network-example-screen/inputs.json). The source URLs, retrieval time, response hashes, license and mapped gene inputs are retained there.

Every analysis used the same human GO-CAM graph (1,631 eligible genes, 2,293 pairs), all 741 local PageRank neighborhoods, enriched connectivity, the default connected-gene background and size bounds 2–1,000. No background, neighborhood or size settings were tuned for individual sets. The construction cap remains 50.

1. Screen every set with 1,999 random draws and seed 0.
2. Rank sets by their minimum within-set BY-adjusted p-value.
3. Rerun the leading ten sets with 19,999 draws and seeds 0, 1 and 2.
4. For each candidate, choose its leading neighborhood at seed 0 and report that **same neighborhood** across the three seeds.

**No Hallmark set passed BY FDR ≤ 0.05 at the default 1,999 draws.** The best default value was 0.1109 for IL6/JAK/STAT3 signaling. Several higher-precision runs do pass; the examples below therefore require 19,999 draws. The smaller p-value floor is 0.00005 rather than 0.0005. Many leading scores still reach that floor, so their exact ordering is unresolved.

These BY values correct across all 741 neighborhoods within each gene set. They do **not** correct for selecting examples across 50 gene sets. This is an exploratory illustration screen, not new biological discovery or a panel-wide significance claim. Independent random seeds check Monte Carlo stability; they are not independent biological replications. Overlapping significant neighborhoods are also not independent discoveries.

## Higher-precision comparison

The table is ordered by the worst BY value for the selected neighborhood across the three seeds. “Passing neighborhoods” counts the complete 741-test family separately for each seed.

| Hallmark set | Eligible / input genes | Selected neighborhood | Study / members | Connections / expected | BY, seed 0 | Same-neighborhood BY range | Passing neighborhoods, seeds 0/1/2 |
|---|---:|---|---:|---:|---:|---:|---|
| IL6_JAK_STAT3_SIGNALING | 50/90 | Neighborhood 110 (STAT6,JAK1) | 17/49 | 50 / 8.86 | 0.0127 | 0.0116–0.0127 | 23 / 24 / 23 |
| TGF_BETA_SIGNALING | 27/58 | Neighborhood 100 (SMAD1) | 11/49 | 36 / 5.01 | 0.0133 | 0.0133–0.0148 | 21 / 20 / 21 |
| APOPTOSIS | 62/179 | Neighborhood 009 (YY1,CASP10,CASP7) | 11/50 | 46 / 12.69 | 0.0266 | 0.0266–0.0266 | 10 / 10 / 11 |
| BILE_ACID_METABOLISM | 14/122 | Neighborhood 496 (PEX26,PEX1,PEX10,PEX12,PEX6,PEX2) | 6/11 | 11 / 0.21 | 0.0266 | 0.0266–0.0296 | 10 / 9 / 10 |
| INTERFERON_GAMMA_RESPONSE | 80/209 | Neighborhood 085 (IL9) | 13/50 | 41 / 14.03 | 0.0296 | 0.0296–0.0444 | 13 / 12 / 11 |
| ALLOGRAFT_REJECTION | 102/210 | Neighborhood 110 (STAT6,JAK1) | 14/49 | 36 / 12.23 | 0.0205 | 0.0190–0.0499 | 13 / 14 / 16 |
| INFLAMMATORY_RESPONSE | 69/210 | Neighborhood 085 (IL9) | 12/50 | 29 / 7.00 | 0.0666 | 0.0266–0.0666 | 0 / 11 / 9 |
| E2F_TARGETS | 48/215 | Neighborhood 203 (RNF8) | 8/41 | 36 / 10.84 | 0.0887 | 0.0887–0.0998 | 0 / 0 / 0 |
| UV_RESPONSE_DN | 26/157 | Neighborhood 185 (ACVR1C) | 5/43 | 23 / 3.63 | 0.1775 | 0.0666–0.1775 | 0 / 0 / 0 |
| KRAS_SIGNALING_UP | 27/207 | Neighborhood 145 (SOCS3) | 5/47 | 17 / 2.33 | 0.1775 | 0.0887–0.1775 | 0 / 0 / 0 |

## Suggested illustrations

Use IL6/JAK/STAT3 and TGF-beta signaling as the primary pair: they are strong across seeds and show distinct, interpretable signaling neighborhoods. Bile acid metabolism offers a compact peroxisomal transport neighborhood, useful for a readable gene-level illustration. Apoptosis provides a cell-death example; allograft rejection provides another strong but more similar immune-signaling example. The links below embed the frozen genes, pinned build, 19,999 draws and seed 0; they enable the BY ≤ 0.05 overview filter and show at most ten neighborhoods. They do not require the live gene-set search service.

### HALLMARK_IL6_JAK_STAT3_SIGNALING

[Open this analysis](http://127.0.0.1:8041/enrichment?genes=A2M%2CIGHA2%2CACVR1B%2CACVRL1%2CBAK1%2CCBL%2CCCL7%2CCCR1%2CCD14%2CCD36%2CCD38%2CCD44%2CCD9%2CCNTFR%2CCRLF2%2CCSF1%2CCSF2%2CCSF2RA%2CCSF2RB%2CCSF3R%2CCXCL1%2CCXCL10%2CCXCL11%2CCXCL13%2CCXCL3%2CCXCL9%2CDNTT%2CEBI3%2CFASN%2CFAS%2CGRB2%2CHAX1%2CHMOX1%2CIFNAR1%2CIFNGR1%2CIFNGR2%2CIL10RB%2CIL12RB1%2CIL13RA1%2CIL15RA%2CIL17RA%2CIL17RB%2CIL18R1%2CIL1B%2CIL1R1%2CIL1R2%2CIL2RA%2CIL2RG%2CIL3RA%2CIL4R%2CIL6%2CIL6ST%2CIL7%2CIL9R%2CINHBE%2CIRF1%2CIRF9%2CITGA4%2CITGB3%2CJUN%2CLEPR%2CLTB%2CLTBR%2CMAP3K8%2CMYD88%2COSMR%2CPDGFC%2CPF4%2CPIK3R5%2CLONP1%2CPIM1%2CPLA2G2A%2CPTPN1%2CPTPN11%2CPTPN2%2CREG1A%2CSOCS1%2CSOCS3%2CSTAM2%2CSTAT1%2CSTAT2%2CSTAT3%2CTGFB1%2CTLR2%2CTNF%2CTNFRSF12A%2CTNFRSF1A%2CTNFRSF1B%2CTNFRSF21%2CTYK2&species=human&method=neighborhood&view=48879e043488a9a3&network_permutations=19999&network_seed=0&graph_term=GN%3A17c8f2a0893fb62a&overview_top=10&overview_min_study=2&overview_fdr=1&fdr=0.05). Selected target: `GN:17c8f2a0893fb62a`.

Seed genes: STAT6,JAK1. Descriptive BP context: cell surface receptor signaling pathway via JAK-STAT; cell surface receptor signaling pathway via STAT; regulation of peptidyl-tyrosine phosphorylation. 50 connections versus 8.86 expected (5.64×).

### HALLMARK_TGF_BETA_SIGNALING

[Open this analysis](http://127.0.0.1:8041/enrichment?genes=ACVR1%2CPROC%2CAPC%2CARID4B%2CBCAR3%2CBMP2%2CBMPR1A%2CBMPR2%2CFZR1%2CCDH1%2CCDK9%2CCDKN1C%2CCTNNB1%2CENG%2CFKBP1A%2CFNTA%2CFURIN%2CHDAC1%2CHIPK2%2CID1%2CID2%2CID3%2CIFNGR2%2CJUNB%2CKLF10%2CLEFTY2%2CLTBP3%2CLTBP2%2CMAP3K7%2CNCOR2%2CNOG%2CPMEPA1%2CPPM1A%2CPPP1CA%2CPPP1R15A%2CRAB31%2CRHOA%2CSERPINE1%2CSKI%2CSKIL%2CSLC20A1%2CGARS1%2CSMAD1%2CSMAD3%2CSMAD6%2CSMAD7%2CSMURF1%2CSMURF2%2CSPTBN1%2CTGFB1%2CTGFBR1%2CTGIF1%2CTHBS1%2CTJP1%2CTRIM33%2CUBE2D3%2CWWTR1%2CXIAP&species=human&method=neighborhood&view=48879e043488a9a3&network_permutations=19999&network_seed=0&graph_term=GN%3Aa6e4e45134d743d7&overview_top=10&overview_min_study=2&overview_fdr=1&fdr=0.05). Selected target: `GN:a6e4e45134d743d7`.

Seed genes: SMAD1. Descriptive BP context: cell surface receptor protein serine/threonine kinase signaling pathway; transforming growth factor beta receptor superfamily signaling pathway; BMP signaling pathway. 36 connections versus 5.01 expected (7.18×).

### HALLMARK_BILE_ACID_METABOLISM

[Open this analysis](http://127.0.0.1:8041/enrichment?genes=ABCA1%2CABCA2%2CABCA3%2CABCA4%2CABCA5%2CABCA6%2CABCA8%2CABCA9%2CABCD1%2CABCD2%2CABCD3%2CABCG4%2CABCG8%2CACSL1%2CACSL5%2CAGXT%2CAKR1D1%2CALDH1A1%2CALDH8A1%2CALDH9A1%2CAMACR%2CAPOA1%2CAQP9%2CAREG%2CAKR1B1%2CAR%2CATXN1%2CBBOX1%2CBCAR3%2CBMP6%2CCRAT%2CGLYAT%2CCAT%2CCH25H%2CCROT%2CCYP27A1%2CCYP39A1%2CCYP46A1%2CCYP7A1%2CCYP7B1%2CCYP8B1%2CDHCR24%2CDIDO1%2CDIO1%2CDIO2%2CEFHC1%2CEPHX2%2CMUSK%2CFADS1%2CFADS2%2CFDXR%2CGC%2CGCLM%2CGNMT%2CGNPAT%2CGSTK1%2CHACL1%2CHAO1%2CHSD17B11%2CHSD17B4%2CHSD17B6%2CHSD3B1%2CHSD3B7%2CIDH1%2CIDH2%2CIDI1%2CISOC1%2CKLF1%2CLCK%2CLIPE%2CLONP2%2CMLYCD%2CNEDD4%2CNPC1%2CNR0B2%2CNR1H4%2CNR1I2%2CNR3C2%2CNUDT12%2COPTN%2CPAOX%2CPECR%2CZNF260%2CPEX1%2CPEX11A%2CPEX11G%2CPEX12%2CPEX13%2CPEX16%2CPEX19%2CPEX26%2CPEX6%2CPEX7%2CPFKM%2CPHYH%2CPIPOX%2CPNPLA8%2CPRDX5%2CPXMP2%2CARID4A%2CRBP1%2CRETSAT%2CRXRA%2CRXRG%2CCTDSP2%2CSYCP2%2CSCP2%2CSERPINA6%2CSLC23A2%2CSLC23A1%2CSLC27A2%2CSLC27A5%2CSLC29A1%2CSLC35B2%2CSLC67A1%2CSLCO1A2%2CSOAT2%2CSOD1%2CSULT1B1%2CSULT2B1%2CTFCP2L1%2CTTR&species=human&method=neighborhood&view=48879e043488a9a3&network_permutations=19999&network_seed=0&graph_term=GN%3A33b52b2486f02bb8&overview_top=10&overview_min_study=2&overview_fdr=1&fdr=0.05). Selected target: `GN:33b52b2486f02bb8`.

Seed genes: PEX26,PEX1,PEX10,PEX12,PEX6,PEX2. Descriptive BP context: peroxisomal membrane transport; protein import into peroxisome matrix; peroxisomal transport. 11 connections versus 0.21 expected (51.60×).

Only 14 of the 122 submitted bile-acid-metabolism genes are present in the analysis network. This example illustrates its represented peroxisomal transport region, rather than coverage of the entire signature.

### HALLMARK_ALLOGRAFT_REJECTION

[Open this analysis](http://127.0.0.1:8041/enrichment?genes=AARS1%2CABCE1%2CABI1%2CACHE%2CACVR2A%2CAKT1%2CAPBB1%2CB2M%2CBCAT1%2CBCL10%2CBCL3%2CBRCA1%2CC2%2CNCAPG%2CCAPG%2CCARTPT%2CCCL11%2CCCL13%2CCCL19%2CCCL2%2CCCL22%2CCCL4%2CCCL5%2CCCL7%2CCCND2%2CCCND3%2CCCR1%2CCCR2%2CCCR5%2CCD1D%2CCD2%2CCD247%2CCD28%2CCD3D%2CCD3E%2CCD3G%2CCD4%2CCD40%2CCD40LG%2CCD47%2CCD7%2CCD74%2CCD79A%2CCD80%2CCD86%2CCD8A%2CCD8B%2CCD96%2CCDKN2A%2CCFP%2CCRTAM%2CCSF1%2CCSK%2CCTSS%2CCXCL13%2CCXCL9%2CCXCR3%2CDARS1%2CDEGS1%2CDYRK3%2CEGFR%2CEIF6%2CEIF3A%2CEIF3D%2CEIF3J%2CEIF4G3%2CEIF5A%2CELANE%2CELF4%2CEREG%2CETS1%2CF2%2CF2R%2CFASN%2CFAS%2CFASLG%2CFCGR2B%2CFGR%2CFLNA%2CFYB1%2CGALNT1%2CGBP2%2CGCNT1%2CGLMN%2CGPR65%2CGZMA%2CGZMB%2CHCLS1%2CHDAC9%2CHIF1A%2CHLA-A%2CHLA-DMA%2CHLA-DMB%2CHLA-DOA%2CHLA-DOB%2CHLA-DQA1%2CHLA-DRA%2CHLA-E%2CHLA-G%2CICAM1%2CICOSLG%2CIFNAR2%2CIFNG%2CIFNGR1%2CIFNGR2%2CIGSF6%2CIKBKB%2CIL10%2CIL11%2CIL12A%2CIL12B%2CIL12RB1%2CIL13%2CIL15%2CIL16%2CIL18%2CIL18RAP%2CIL1B%2CIL2%2CIL27RA%2CIL2RA%2CIL2RB%2CIL2RG%2CIL4%2CIL4R%2CIL6%2CIL7%2CIL9%2CINHBA%2CINHBB%2CIRF4%2CIRF7%2CIRF8%2CITGAL%2CITGB2%2CITK%2CJAK2%2CKLRD1%2CKRT1%2CLCK%2CLCP2%2CLIF%2CLTB%2CLY75%2CLY86%2CLYN%2CMAP3K7%2CMAP4K1%2CMBL2%2CMMP9%2CMRPL3%2CMTIF2%2CNCF4%2CNCK1%2CNCR1%2CNLRP3%2CRMRP%2CNME1%2CNANOS2%2CNOS2%2CNPM1%2CPF4%2CZNF395%2CPRF1%2CPRKCB%2CPRKCG%2CPSMB10%2CPTPN6%2CPTPRC%2CRARS1%2CRIPK2%2CRPL39%2CRPL3L%2CRPL9%2CRPS19%2CRPS3A%2CRPS9%2CSLC6A20%2CSIT1%2CSOCS1%2CSOCS5%2CSPI1%2CSRGN%2CST8SIA4%2CSTAB1%2CSTAT1%2CSTAT4%2CSEC14L2%2CTAP1%2CSEC14L3%2CTAP2%2CTAPBP%2CTGFB1%2CTGFB2%2CTHY1%2CTIMP1%2CTLR1%2CTLR2%2CTLR3%2CTLR6%2CTNF%2CTPD52%2CTANK%2CTRAF2%2CTRAT1%2CUBE2D1%2CUBE2N%2CWARS1%2CWAS%2CZAP70&species=human&method=neighborhood&view=48879e043488a9a3&network_permutations=19999&network_seed=0&graph_term=GN%3A17c8f2a0893fb62a&overview_top=10&overview_min_study=2&overview_fdr=1&fdr=0.05). Selected target: `GN:17c8f2a0893fb62a`.

Seed genes: STAT6,JAK1. Descriptive BP context: cell surface receptor signaling pathway via JAK-STAT; cell surface receptor signaling pathway via STAT; regulation of peptidyl-tyrosine phosphorylation. 36 connections versus 12.23 expected (2.94×).

### HALLMARK_APOPTOSIS

[Open this analysis](http://127.0.0.1:8041/enrichment?genes=ADD1%2CAIFM3%2CANKH%2CANXA1%2CAPP%2CATF3%2CAVPR1A%2CBAX%2CBCAP31%2CBCL10%2CBCL2L1%2CBCL2L10%2CBCL2L11%2CHGNC%3A995%2CBGN%2CBID%2CBIK%2CBIRC3%2CBMF%2CBMP2%2CBNIP3L%2CBRCA1%2CBTG2%2CBTG3%2CCASP1%2CCASP2%2CCASP3%2CCASP4%2CCASP6%2CCASP7%2CCASP8%2CCASP9%2CCAV1%2CCCNA1%2CCCND1%2CCCND2%2CCD14%2CCD2%2CCD38%2CCD44%2CCD69%2CCDC25B%2CCDK2%2CCDPF1%2CCDKN1A%2CCDKN1B%2CCFLAR%2CCLU%2CCREBBP%2CVSIG2%2CCTH%2CCTNNB1%2CCYLD%2CDNPEP%2CIAPP%2CDAP%2CDLGAP3%2CDAP3%2CDCN%2CDDIT3%2CDFFA%2CDIABLO%2CDNAJA1%2CDNAJC3%2CDNM1L%2CDPYD%2CGLB1%2CSH3D19%2CEBP%2CEGR3%2CEMP1%2CENO2%2CERBB2%2CERBB3%2CEREG%2CETF1%2CF2%2CF2R%2CFASN%2CFAS%2CFASLG%2CFDXR%2CLZTS1%2CFEZ1%2CGADD45A%2CGADD45B%2CGCH1%2CGNA15%2CGPX1%2CGPX3%2CGPX4%2CGSN%2CGSR%2CGSTM1%2CGUCY2D%2CH1-0%2CSOS1%2CIL6%2CHGF%2CHMGB2%2CHMOX1%2CHSPB1%2CIER3%2CIFITM3%2CIFNB1%2CIFNGR1%2CIGF2R%2CIGFBP6%2CIL18%2CIL1A%2CIL1B%2CIRF1%2CISG20%2CJUN%2CKRT18%2CLEF1%2CLGALS3%2CLMNA%2CLUM%2CETFA%2CETFB%2CETFDH%2CMADD%2CMCL1%2CMGMT%2CMMP2%2CNEDD9%2CNEFH%2CPKN1%2CPAK1%2CPDCD4%2CPDGFRB%2CPEA15%2CPLAT%2CPLCB2%2CPLPPR4%2CPMAIP1%2CPPP2R5B%2CPPP3R1%2CPPT1%2CZNF395%2CPRF1%2CPSEN1%2CPSEN2%2CPTK2%2CRARA%2CRELA%2CRETSAT%2CRHOB%2CRHOT2%2CRNASEL%2CROCK1%2CSLC38A1%2CSLC26A1%2CSAT1%2CSATB1%2CSC5D%2CSLC20A1%2CSMAD7%2CSOD1%2CSOD2%2CSPTAN1%2CSQSTM1%2CSEC14L2%2CTAP1%2CTGFB2%2CTGFBR3%2CTIMP1%2CTIMP2%2CTIMP3%2CTNF%2CTNFRSF12A%2CTNFSF10%2CTOP2A%2CTSPO%2CTXNIP%2CVDAC2%2CWEE1%2CXIAP&species=human&method=neighborhood&view=48879e043488a9a3&network_permutations=19999&network_seed=0&graph_term=GN%3A801fc3e541ec1fa7&overview_top=10&overview_min_study=2&overview_fdr=1&fdr=0.05). Selected target: `GN:801fc3e541ec1fa7`.

Seed genes: YY1,CASP10,CASP7. Descriptive BP context: pyroptotic inflammatory response; programmed cell death; cell death. 46 connections versus 12.69 expected (3.62×).

## Complete screen and reproduction

- [Default-precision screen](data/network-example-screen/screen.tsv): top ten neighborhoods for each of all 50 sets, including negative results.
- [Higher-precision reruns](data/network-example-screen/confirm.tsv): top ten neighborhoods for each of 30 runs.
- [Fixed-neighborhood comparison](data/network-example-screen/summary.json): selected neighborhood values and complete-family significant counts for each seed.
- [Screen policy](data/network-example-screen/screen-protocol.json) and [rerun policy](data/network-example-screen/confirm-protocol.json): projection, neighborhood construction, graph counts, parameters and selected sets.
- [Screening script](../scripts/screen_network_examples.py): calls the same enrichment implementation as the UI; full responses are written under `.runtime/network-example-screen/`.

```sh
BUILD=/path/to/data/builds/48879e043488a9a3
# Use the committed frozen inputs for exact reproduction; fetch would refresh them.
uv run python scripts/screen_network_examples.py screen --build "$BUILD"
uv run python scripts/screen_network_examples.py confirm --build "$BUILD" --top 10 --seeds 0 1 2
```

The original retrieval command was `uv run python scripts/screen_network_examples.py fetch --build "$BUILD"`. Re-fetching is optional and may change source membership or mapping; preserve the frozen inputs when comparing these results.

IL6/JAK/STAT3 and TGF-beta were also checked through the running browser UI: each shows ten overview nodes passing BY 0.05. N110 reports 50 connections / 8.86 expected and BY 0.0127; N100 reports 36 / 5.01 and BY 0.0133. Neither produced browser errors.
