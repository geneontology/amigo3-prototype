const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = n => Number(n).toLocaleString();
const sci = p => p===0?'<1e-300':p<0.001?p.toExponential(2):p.toFixed(3);
const geneLink = g => `<a href="/genes/${encodeURIComponent(g.id)}">${esc(g.symbol || g.id)}</a>`;
// Sets are GO terms or GO-CAM models; each has its own detail page.
const setHref = id => `/${result.parameters.target==='gocam'?'gocams':'terms'}/${encodeURIComponent(id)}`;
const setLink = t => result.parameters.target === 'neighborhood'
  ? '<strong>' + esc(t.label) + '</strong><span class="term-id mono">' + esc(t.id) + '</span><span class="term-id">' + (t.representatives || []).map(g => esc(g.symbol)).join(' · ') + '</span>' : `<a href="${setHref(t.id)}">${esc(t.label)}</a><span class="term-id mono">${esc(t.id)}${t.status?' · '+esc(t.status):''}</span>`;
const split = text => text.split(/[\s,;]+/).filter(Boolean);
let loadedSet = '';
let prefillOnly = new URLSearchParams(location.search).get('run') === '0';
// MGSA reports a posterior (higher is better) where the hypergeometric test reports FDR.
const SCORE = {
  hypergeometric: {label: 'Show results with FDR ≤', column: 'FDR', threshold: '0.05', passes: (t, x) => t.fdr <= x, cell: t => sci(t.fdr), summary: 'FDR ≤ 0.05', short: x => `FDR ≤ ${x}`},
  mgsa: {label: 'Show results with posterior ≥', column: 'Posterior', threshold: '0.5', passes: (t, x) => t.posterior >= x, cell: t => t.posterior.toFixed(3), summary: 'posterior ≥ 0.5', short: x => `posterior ≥ ${x}`},
  network: {label: 'Show results with BY FDR ≤', column: 'BY FDR', threshold: '0.05', passes: (t, x) => t.by_fdr <= x, cell: t => sci(t.by_fdr), summary: 'BY FDR ≤ 0.05', short: x => `BY FDR ≤ ${x}`},
};
const scoreOf = data => SCORE[data.parameters.method || 'hypergeometric'];
const equivalents = t => t.equivalents?.length ? `<span class="term-id">same genes as ${t.equivalents.map(e => `<a href="${setHref(e.id)}">${esc(e.label)}</a>`).join(', ')}</span>` : '';
let result = null, options = null;
let runRequest = null, networkOptions = null, networkPromise = null;
let edgeCache = new Map();
const pinnedView = new URLSearchParams(location.search).get('view');
let requestedNetworkTaxon = new URLSearchParams(location.search).get('network_taxon');
const neighborhoodMode = () => $('#analysis-method').value === 'neighborhood';
const networkMode = () => ['network', 'neighborhood'].includes($('#analysis-method').value);
SCORE.neighborhood = SCORE.network;
const COPY = {
  neighborhood: {heading: 'Network neighborhoods', column: 'Neighborhood', hint: 'default: connected catalog genes in the GO-CAM graph', empty: 'neighborhoods'},
  go: {heading: 'Enriched terms', column: 'Term', hint: 'default: every gene with a qualifying annotation in the species', empty: 'terms'},
  gocam: {heading: 'Enriched GO-CAM models', column: 'GO-CAM model', hint: 'default: every gene of the species that appears in a GO-CAM model', empty: 'models'},
};

function applyMethod(resetThreshold) {
  const score = SCORE[$('#analysis-method').value] || SCORE.hypergeometric;
  $('#threshold-label').textContent = score.label;
  $('#min-size-label').textContent = neighborhoodMode() ? 'Min genes per neighborhood' : 'Min set size';
  $('#max-size-label').textContent = neighborhoodMode() ? 'Max genes per neighborhood' : 'Max set size';
  $('#neighborhood-size-help').hidden = !neighborhoodMode();
  if (resetThreshold) $('#threshold').value = score.threshold;
  $('#network-options').hidden = !networkMode();
  $('#network-options').disabled = !networkMode();
  const models = $('#target option[value=gocam]');
  if (models) models.disabled = networkMode() || !options.species.some(s => s.gocam_genes);
  $('#target option[value=neighborhood]').disabled = !neighborhoodMode();
  $('#target option[value=go]').disabled = neighborhoodMode();
  if (!networkMode() && $('#target').value === 'neighborhood') $('#target').value = 'go';
  if (networkMode()) { $('#target').value = neighborhoodMode() ? 'neighborhood' : 'go'; applyTarget(); loadNetworkOptions().catch(showNetworkError); }
  else applyTarget();
}

function showNetworkError(error) { $('#network-options-status').textContent = error.message; }

async function loadNetworkOptions() {
  if (!networkPromise) {
    $('#network-options-status').textContent = 'Loading the GO-CAM network…';
    networkPromise = fetch('/api/enrichment/network').then(async response => {
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Network unavailable');
      networkOptions = data; return data;
    }).catch(error => { networkPromise = null; throw error; });
  }
  await networkPromise;
  const current = $('#network-taxon').value || requestedNetworkTaxon;
  const taxa = networkOptions.taxa.filter(t => t.species === $('#species').value);
  $('#network-taxon').innerHTML = taxa.map(t => `<option value="${esc(t.taxon)}">${esc(AmigoSpecies.label(t.species))} · ${esc(t.taxon)} (${number(t.catalog_genes)} mapped genes)</option>`).join('');
  const unavailable = current && current === requestedNetworkTaxon && !taxa.some(t => t.taxon === current);
  if (unavailable) $('#network-taxon').insertAdjacentHTML('beforeend', `<option value="${esc(current)}">Unavailable taxon: ${esc(current)}</option>`);
  if (unavailable || taxa.some(t => t.taxon === current)) $('#network-taxon').value = current;
  $('#network-options-status').textContent = unavailable ? 'The shared network taxon is unavailable for this species. Choose an available taxon or species.' : taxa.length ? '' : 'No catalog-mapped GO-CAM network for this species. Choose another species.';
}

function applyTarget() {
  const target = $('#target').value, copy = COPY[target];
  document.querySelectorAll('.go-only').forEach(el => { el.hidden = target !== 'go'; });
  $('#background-hint').textContent = copy.hint;
  if (networkMode()) $('#background-hint').textContent = 'default: connected catalog genes in the GO-CAM graph; a custom background retains only edges between its genes';
  const current = $('#species').value;
  $('#species').innerHTML = options.species.map(s => `<option value="${esc(s.value)}"${target==='gocam'&&!s.gocam_genes?' disabled':''}>${esc(AmigoSpecies.label(s.value))} (${number(target==='gocam'?s.gocam_genes:s.annotated_genes)} ${target==='gocam'?'in models':'annotated'})</option>`).join('');
  const available = options.species.filter(s => target !== 'gocam' || s.gocam_genes);
  $('#species').value = available.find(s => s.value === current)?.value
    || available.find(s => s.value === 'human')?.value || available[0]?.value || '';
}

let loaded = null;
const sourceLink = g => g.url ? `<a href="${esc(g.url)}" target="_blank" rel="noopener">${esc(g.collection)} ${esc(g.id)} ↗</a>` : `${esc(g.collection)} ${esc(g.id)}`;
let runSet = null;

async function loadExamples() {
  const select = $('#example-set');
  select.innerHTML = '<option value="">Choose an example…</option>';
  try {
    const response = await fetch('/api/genesets?species=' + encodeURIComponent($('#species').value));
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Examples unavailable');
    select.innerHTML += data.examples.map(g => `<option value="${esc(g.id)}">${esc(g.name)} · ${esc(g.collection)} (${number(g.count)})</option>`).join('');
    select.disabled = !data.examples.length;
    if (!data.examples.length) select.options[0].textContent = 'No bundled examples for this species';
    $('#geneset-search-box').hidden = !data.search.enabled;
    if (!$('#geneset-collection').options.length) $('#geneset-collection').innerHTML = Object.entries(data.collections).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
    $('#geneset-results').hidden = true; $('#geneset-search-status').textContent = '';
  } catch (e) { select.disabled = true; select.options[0].textContent = e.message; }
}

async function searchGenesets() {
  const q = $('#geneset-query').value.trim();
  if (q.length < 2) return;
  const status = $('#geneset-search-status'), list = $('#geneset-results');
  status.textContent = 'Searching MyGeneset…'; list.hidden = true;
  try {
    const response = await fetch(`/api/genesets/search?species=${encodeURIComponent($('#species').value)}&collection=${encodeURIComponent($('#geneset-collection').value)}&q=${encodeURIComponent(q)}`);
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Search failed');
    status.textContent = data.hits.length ? `${number(data.total)} gene set${data.total===1?'':'s'} match${data.total===1?'es':''}; showing ${data.hits.length}.` : 'No gene sets in this collection match for this species.';
    list.innerHTML = data.hits.map(g => `<li><button type="button" class="geneset-hit" data-id="${esc(g.id)}"><strong>${esc(g.name)}</strong><span>${esc(g.collection)} · ${esc(g.id)} · ${number(g.count)} genes</span></button></li>`).join('');
    list.hidden = !data.hits.length;
  } catch (e) { status.textContent = e.message; }
}

function clearLoaded() { loadedSet = ''; loaded = null; $('#geneset-loaded').hidden = true; $('#example-set').value = ''; }

function showLoaded() {
  const box = $('#geneset-loaded');
  if (!loaded) { box.hidden = true; return; }
  const g = loaded, warnings = [];
  if (!g.species_match) warnings.push(`This set is for taxon ${esc(g.taxid)}, not the selected species; members were matched by identifier and symbol only within the selected species.`);
  const unmapped = g.members.filter(m => !m.gene).map(m => esc(m.label || '?'));
  const origin = g.origin === 'bundled' ? `bundled copy retrieved from MyGeneset on ${esc(g.provenance.retrieved)}` : 'fetched live from MyGeneset';
  box.innerHTML = `<p><strong>${esc(g.name)}</strong> — ${sourceLink(g)}, ${origin}. ${number(g.mapped)} of ${number(g.member_count)} members mapped to genes in this snapshot.</p>`
    + warnings.map(w => `<p class="geneset-warning">${w}</p>`).join('')
    + (g.expected_go.length ? `<details class="expected-go"><summary>Curated interpretation expects ${g.expected_go.length} GO terms</summary><ul>${g.expected_go.map(t => `<li><a href="/terms/${encodeURIComponent(t.id)}">${esc(t.label)}</a> <span class="mono muted">${esc(t.id)}</span> <small>${esc((t.category||'').replace(/_/g,' '))}${t.confidence?' · '+esc(t.confidence)+' confidence':''}</small></li>`).join('')}</ul><p class="muted">From the <a href="https://github.com/monarch-initiative/genesets" target="_blank" rel="noopener">genesets</a> curation corpus. Matching result rows are marked.</p></details>` : '')
    + (unmapped.length ? `<details><summary>${unmapped.length} unmapped member${unmapped.length>1?'s':''}</summary><p>${unmapped.join(', ')}</p><p class="muted">Kept in the list so they appear under “Not found” or “Ambiguous”.</p></details>` : '');
  box.hidden = false;
}

async function loadGeneset(id, keepGenes = false) {
  const status = $('#geneset-search-status');
  status.textContent = 'Loading gene set…';
  try {
    const response = await fetch(`/api/genesets/${encodeURIComponent(id)}?species=${encodeURIComponent($('#species').value)}`);
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Gene set unavailable');
    if (!keepGenes) $('#genes').value = data.genes.join('\n');
    loaded = data; loadedSet = data.id; status.textContent = '';
    if ([...$('#example-set').options].some(o => o.value === data.id)) $('#example-set').value = data.id;
    showLoaded();
    history.replaceState(null, '', shareUrl());
  } catch (e) { status.textContent = e.message; }
}

function fields() {
  return {
    genes: split($('#genes').value), species: $('#species').value, target: $('#target').value, aspect: $('#aspect').value,
    method: $('#analysis-method').value,
    ...(networkMode() ? {network_taxon: $('#network-taxon').value || null, network_alternative: $('#network-alternative').value, network_permutations: Number($('#network-permutations').value), network_seed: Number($('#network-seed').value)} : {}),
    evidence: $('#evidence').value, min_term_size: Number($('#min-size').value) || 1,
    max_term_size: Number($('#max-size').value) || 1000,
    background: split($('#background').value),
    ...exclusionFlags(),
  };
}

// Optional GO exclusions are checkboxes named after their API parameter; all default off.
const exclusionBoxes = () => [...document.querySelectorAll('#exclusions input[type=checkbox]')];
const exclusionFlags = () => Object.fromEntries(exclusionBoxes().map(b => [b.name, b.checked]));

function shareUrl() {
  const f = fields(), p = new URLSearchParams({genes: f.genes.join(','), species: f.species, target: f.target});
  if (f.method !== 'hypergeometric') p.set('method', f.method);
  if (networkMode()) for (const k of ['network_taxon','network_alternative','network_permutations','network_seed']) if (f[k] !== null) p.set(k, f[k]);
  if (pinnedView || options?.build_id) p.set('view', pinnedView || options.build_id);
  if (f.target === 'go') { p.set('aspect', f.aspect); p.set('evidence', f.evidence); }
  if (f.target === 'go') for (const b of exclusionBoxes()) if (b.checked) p.set(b.name, '1');
  for (const [k, v] of Object.entries({ min: f.min_term_size, max: f.max_term_size, fdr: $('#threshold').value})) p.set(k, v);
  if (f.background.length) p.set('background', f.background.join(','));
  if (loadedSet) p.set('geneset', loadedSet);
  if (networkMode()) {
    const graph = AmigoEnrichmentGraphs.state();
    if (graph.active) p.set('result_view', 'graphs');
    else if (neighborhoodMode()) p.set('result_view', 'table');
    if (graph.selected) p.set('graph_term', graph.selected);
    if (neighborhoodMode() && graph.fullContext) p.set('network_context', 'full');
    if (neighborhoodMode()) {
      p.set('overview_top', graph.overviewLimit);
      p.set('overview_min_study', graph.overviewMinStudy);
      p.set('overview_rank', graph.overviewRank);
      p.set('overview_fdr', graph.overviewUseFdr ? '1' : '0');
    }
    if ($('#show-all').checked) p.set('show_all', '1');
  }
  if (prefillOnly) p.set('run', '0');
  return location.pathname + '?' + p;
}

function restore(params) {
  $('#show-all').checked = params.get('show_all') === '1';
  if (params.get('genes')) $('#genes').value = split(params.get('genes')).join('\n');
  if (params.get('background')) { $('#background').value = split(params.get('background')).join('\n'); $('#background-panel').open = true; }
  if (params.get('target')) { $('#target').value = params.get('target'); applyTarget(); }
  if (params.get('method')) { $('#analysis-method').value = params.get('method'); applyMethod(true); }
  for (const [key, id] of [['species','#species'],['aspect','#aspect'],['evidence','#evidence'],['min','#min-size'],['max','#max-size'],['fdr','#threshold']])
    if (params.get(key)) $(id).value = params.get(key);
  for (const b of exclusionBoxes()) b.checked = params.get(b.name) === '1';
  for (const [key, id] of [['network_alternative','#network-alternative'],['network_permutations','#network-permutations'],['network_seed','#network-seed']])
    if (params.has(key)) $(id).value = params.get(key);
}

function renderMapping(data) {
  const s = data.study, groups = [];
  const list = (items, render) => '<ul>' + items.map(render).join('') + '</ul>';
  const used = s.matched.filter(g => !s.outside_background.includes(g.id));
  groups.push(`<div class="mapping-group"><h3>Analysed (${number(used.length)})</h3>${list(used, g => `<li>${geneLink(g)}${g.input.toUpperCase()!==(g.symbol||'').toUpperCase()?` <small class="muted">from ${esc(g.input)} (${esc(g.match)})</small>`:''}</li>`)}</div>`);
  const outside = s.matched.filter(g => s.outside_background.includes(g.id));
  if (data.network) {
    for (const [key, title] of [['not_in_network','Absent from the mapped network'],['isolated_in_background','No connections in the background graph'],['outside_custom_background','Outside the custom background']]) {
      const excluded = s.matched.filter(g => s[key].includes(g.id));
      if (excluded.length) groups.push(`<div class="mapping-group warn"><h3>${title} (${excluded.length})</h3>${list(excluded, g => `<li>${geneLink(g)}</li>`)}</div>`);
    }
  } else if (outside.length) groups.push(`<div class="mapping-group warn"><h3>${data.parameters.target==='gocam'?'Not in background models':'No qualifying annotation in background'} (${outside.length})</h3>${list(outside, g => `<li>${geneLink(g)}</li>`)}<small>Excluded: ${data.parameters.target==='gocam'?'these genes appear in no GO-CAM model':`these genes have no ${esc(data.parameters.aspect_label.replace('_',' '))} annotation under the evidence filter`}${data.background?', or are outside the custom background':''}.</small></div>`);
  if (s.ambiguous.length) groups.push(`<div class="mapping-group warn"><h3>Ambiguous (${s.ambiguous.length})</h3>${list(s.ambiguous, a => `<li>${esc(a.input)} → ${a.candidates.map(geneLink).join(', ')}</li>`)}<small>Not analysed. Use an identifier to choose one.</small></div>`);
  if (s.unresolved.length) groups.push(`<div class="mapping-group warn"><h3>Not found (${s.unresolved.length})</h3>${list(s.unresolved, u => `<li>${esc(u)}</li>`)}</div>`);
  if (s.duplicates.length) groups.push(`<div class="mapping-group"><h3>Duplicates ignored (${s.duplicates.length})</h3>${list(s.duplicates, u => `<li>${esc(u)}</li>`)}</div>`);
  if (data.background) {
    const b = data.background;
    groups.push(`<div class="mapping-group"><h3>Custom background</h3><p>${number(b.matched.length)} matched · ${data.network ? `${number(b.not_in_network.length)} absent from network · ${number(b.isolated_in_background.length)} isolated` : `${number(b.without_annotations.length)} without qualifying annotations`} · ${number(b.unresolved.length)} not found · ${number(b.ambiguous.length)} ambiguous</p></div>`);
  }
  $('#mapping-body').innerHTML = `<div class="mapping-groups">${groups.join('')}</div>`;
  $('#mapping-summary').textContent = `${number(data.counts.study)} of ${number(data.counts.input)} inputs analysed`;
}

function renderTerms() {
  if (!result) return;
  const threshold = Number($('#threshold').value), all = $('#show-all').checked;
  const score = scoreOf(result);
  const rows = result.terms.filter(t => all || score.passes(t, threshold));
  const maxFold = Math.max(1, ...rows.map(t => t.fold_enrichment));
  const expected = new Set(result.parameters.target === 'go' ? (runSet?.expected_go || []).map(t => t.id) : []);
  $('#term-table tbody').innerHTML = rows.map(t => `<tr${expected.has(t.id)?' class="expected-row"':''}><td>${setLink(t)}${equivalents(t)}${expected.has(t.id)?'<span class="expected-badge">curated expectation</span>':''}</td><td class="num">${number(result.network ? t.connections : t.study_count)}</td><td class="num">${number(t.term_size)}</td><td class="num">${t.expected.toFixed(2)}</td><td class="num">${t.fold_enrichment === null ? '—' : t.fold_enrichment.toFixed(2)}<span class="fold-bar" style="width:${Math.round(40*Math.log1p(t.fold_enrichment || 0)/Math.log1p(maxFold))}px" aria-hidden="true"></span></td><td class="num">${sci(t.p_value)}</td><td class="num">${score.cell(t)}</td><td class="gene-list">${result.network ? networkGenes(t) : t.genes.map(geneLink).join(' ')}</td></tr>`).join('')
    || `<tr><td colspan="8">No ${COPY[result.parameters.target].empty} pass ${esc(score.short(threshold))}. ${result.terms.length ? 'Select “Show all” to inspect the best-scoring terms.' : ''}</td></tr>`;
  const c = result.counts;
  const passing = result.terms.filter(t => score.passes(t, threshold)).length;
  $('#enrichment-stats').innerHTML = `<div><strong>${number(c.study)}</strong><span>study genes</span></div><div><strong>${number(c.population)}</strong><span>background</span></div><div><strong>${number(passing)}</strong><span>${COPY[result.parameters.target].empty} ${score.short(threshold)}</span></div>`;
  const noun = COPY[result.parameters.target].empty;
  const found = result.terms.filter(t => expected.has(t.id) && score.passes(t, threshold)).length;
  $('#term-note').textContent = (expected.size ? `${found} of ${expected.size} curated expected terms reach ${score.short(threshold)} (exact term match) · ` : '') + `${number(rows.length)} shown · ${number(c.terms_with_study_genes)} ${noun} contain study genes · ${number(c.tests)} ${noun} tested` + (result.network ? ' · terms can have connecting edges without any direct gene overlap' : '') + (result.truncated ? ` · only the top ${number(result.terms.length)} ${noun} by ${result.parameters.method === 'mgsa' ? 'posterior' : 'p-value'} were returned` : '');
  AmigoEnrichmentGraphs.update(result, rows, {
    loadEdges: loadNetworkEdges, renderEvidence: renderNetworkEvidence,
    onState: () => history.replaceState(null, '', shareUrl()),
  });
}

function networkGenes(t) {
  const context = result.network.kind === 'neighborhood' ? '<div class="neighborhood-go-context">Descriptive GO context: ' + (t.go_context || []).map(g => '<a href="/terms/' + encodeURIComponent(g.id) + '">' + esc(g.label) + '</a> (' + g.members + '/' + t.reference_size + ' reference members)').join('; ') + '</div>' : '';
  return context + `<div>Connected study genes: ${t.genes.map(geneLink).join(' ') || 'none'}</div><div>${result.network.kind === 'neighborhood' ? 'Neighborhood neighbors' : 'Term neighbors'}: ${t.neighbors.map(geneLink).join(' ') || 'none'}</div><div>Direct overlap: ${t.overlap_genes.map(geneLink).join(' ') || 'none'}</div>`
    + (t.connections ? `<details class="network-evidence" data-term="${esc(t.id)}"><summary>Inspect ${number(t.connections)} connecting pairs</summary><div class="network-evidence-body" role="status">Open to load source assertions.</div></details>` : '');
}

function renderNetworkSummary(data) {
  $('#network-summary').hidden = !data.network;
  if (!data.network) return;
  const c = data.network.coverage, n = data.network.null, p = c.projection;
  const space = n && n.possible_sets.length > 20 ? `approximately ${n.possible_sets[0]}.${n.possible_sets.slice(1,3)} × 10<sup>${n.possible_sets.length-1}</sup>` : esc(n?.possible_sets);
  const omissions = Object.entries(p.activity_omissions).map(([k,v]) => `${number(v)} ${esc(k.replaceAll('_',' '))}`).join('; ');
  $('#network-summary-body').innerHTML = `<p>${esc(data.parameters.network_taxon)}: ${number(c.background_network_nodes)} connected background genes and ${number(c.background_network_edges)} distinct pairs. ${number(c.isolated_background_nodes)} mapped background genes have no retained connections.</p>`
    + `<details><summary>Source coverage and omissions</summary><p>${number(p.models)} models · ${number(p.projectable_activities)} of ${number(p.activities)} activity rows projectable · ${number(p.source_identifier_genes)} source-only nodes excluded from this catalog-mapped analysis.</p><p>${omissions || 'No activity omissions.'}</p><p>Model statuses: ${Object.entries(p.model_statuses).map(([k,v]) => `${esc(k || 'unspecified')}: ${number(v)}`).join(', ')}.</p><a href="/api/gene-network?taxon=${encodeURIComponent(data.parameters.network_taxon)}&summary=true&view=${encodeURIComponent(data.build_id)}">Full coverage report</a></details>`
    + (n ? `<p>${n.sampling === 'exact' ? 'Exhaustive comparison of' : 'Seeded random comparison with'} ${number(n.samples)} degree-matched sets. Minimum attainable p-value: ${sci(n.minimum_p_value)}. Expected connections are calculated exactly under the degree-matched null.</p><p>Comparison space: ${space} possible sets; seed ${n.seed}. ${number(n.fixed_study_genes.length)} study genes are fixed by their degree strata.</p>` : '<p>No test was run: no eligible study genes or GO sets remain under these filters.</p>')
    + (n?.possible_sets === '1' ? '<p class="geneset-warning">Only the submitted set satisfies the degree constraints. This comparison has no power; all p-values are 1.</p>' : '')
    + '<p>GO evidence and exclusion filters apply to term memberships; the reference graph retains all recorded causal-edge evidence. BY FDR accounts for testing overlapping GO sets. Monte Carlo results have finite resolution; increase random sets for finer p-values. This functional network combines recorded contexts. The network and GO memberships may share curation, so enrichment describes existing GO knowledge.</p>';
  if (data.network.neighborhoods) {
    const n = data.network.neighborhoods;
    const paragraph = document.createElement('p');
    paragraph.textContent = n.communities + ' overlapping topology-derived neighborhoods on the reference network of ' + number(n.reference_nodes) + ' genes and ' + number(n.reference_edges) + ' pairs. ' + n.algorithm + '; restart probability ' + n.restart_probability + ', maximum ' + n.max_neighborhood_size + ' genes per neighborhood. Neighborhoods are built independently of this gene list, GO annotations and custom background.';
    $('#network-summary-body').prepend(paragraph);
    $('#network-summary-body').lastElementChild.textContent = 'The custom background restricts genes and edges after neighborhood construction; it does not redefine neighborhoods. Size filters determine the tested family. GO BP labels summarize reference-neighborhood membership after the connectivity test and do not affect scores. BY FDR covers all tested neighborhoods. This functional network combines recorded contexts.';
  }
}

function loadNetworkEdges(termId, includeContext = false) {
  const key = termId + ':' + includeContext;
  if (!edgeCache.has(key)) {
    const cache = edgeCache;
    const promise = fetch('/api/enrichment/network/edges', {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({...runRequest, term_id: termId, include_context: includeContext})})
      .then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load source edges');
        return data;
      }).catch(error => { cache.delete(key); throw error; });
    cache.set(key, promise);
  }
  return edgeCache.get(key);
}

function renderNetworkEvidence(data) {
    const genes = new Map(data.genes.map(g => [g.id,g]));
    const activities = new Map(data.activities.map(a => [a.id,a]));
    const models = new Map(data.models.map(m => [m.id,m]));
    const activityLink = key => { const a = activities.get(key); return `<a href="/gocams/${encodeURIComponent(a.model_id)}#activity-${encodeURIComponent(a.activity_id)}">${esc(a.label)} · ${esc(a.function_label || a.function)}</a>`; };
    return `<ul>${data.edges.map(e => `<li><strong>${geneLink(genes.get(e.source_gene))} ↔ ${geneLink(genes.get(e.target_gene))}</strong><ul>${e.assertions.map(a => { const m = models.get(a.model_id); return `<li>${activityLink(a.source)} → ${activityLink(a.target)}<br><span class="mono">${esc(a.predicate)}</span> · <a href="/gocams/${encodeURIComponent(m.id)}">${esc(m.label)}</a><details><summary>Evidence and context</summary><pre>${esc(JSON.stringify({assertion:a.assertion, source_activity:activities.get(a.source).record, target_activity:activities.get(a.target).record, model_source:m.source}, null, 2))}</pre></details></li>`; }).join('')}</ul></li>`).join('')}</ul>`;
}

async function inspectNetworkEdges(details) {
  if (!details.open || details.dataset.loaded || !runRequest) return;
  details.dataset.loaded = 'loading';
  const body = details.querySelector('.network-evidence-body');
  body.textContent = 'Loading source assertions…';
  try {
    const data = await loadNetworkEdges(details.dataset.term);
    body.innerHTML = renderNetworkEvidence(data);
    details.dataset.loaded = 'yes';
  } catch (error) { body.textContent = error.message + ' Close and reopen to retry.'; delete details.dataset.loaded; }
}

function render(data) {
  result = data;
  const p = data.parameters, score = scoreOf(data);
  $('#score-column').textContent = score.column;
  $('#study-column').textContent = data.network ? 'Connections' : 'Study';
  $('#background-column').textContent = data.network ? 'Set genes' : 'Background';
  $('#genes-column').textContent = data.network ? 'Genes and source edges' : 'Genes';
  $('#show-all-label').textContent = p.target === 'neighborhood' ? 'Show all returned table/chart rows' : data.network ? 'Show all returned terms' : 'Show all tested sets with study genes';
  const scope = p.target === 'neighborhood' ? 'overlapping local neighborhoods; GO labels are descriptive only' : p.target === 'gocam' ? p.target_label : `${p.aspect_label.replace('_',' ')} · ${p.evidence_label}${(p.exclusions || []).map(e => ' · ' + e).join('')}`;
  $('#method').textContent = `${AmigoSpecies.label(p.species)} · ${p.method_label} · ${scope} · set size ${p.min_term_size}–${p.max_term_size}. ${p.test[0].toUpperCase()+p.test.slice(1)}; ${p.sets}. Background: ${p.background}. ${p.correction}.`;
  $('#terms-heading').textContent = COPY[p.target].heading; $('#set-column').textContent = COPY[p.target].column;
  if (data.network) $('#terms-heading').textContent = (p.target === 'neighborhood' ? 'Neighborhoods' : 'GO terms') + ' with ' + (p.network_alternative === 'less' ? 'depleted' : 'enriched') + ' connectivity';
  renderNetworkSummary(data);
  renderMapping(data); renderTerms();
  $('#build').textContent = 'Snapshot ' + data.build_id;
  $('#enrichment-results').hidden = false;
  history.replaceState(null, '', shareUrl());
}

async function run(event) {
  event?.preventDefault();
  if (networkMode()) {
    try { await loadNetworkOptions(); } catch (error) { showNetworkError(error); return; }
  }
  const body = fields();
  if (!body.genes.length) return;
  prefillOnly = false;
  if (!body.background.length) delete body.background;
  const button = $('#enrichment-form button[type=submit]');
  button.disabled = true; $('#enrichment-error').hidden = true; $('#enrichment-status').textContent = 'Running enrichment…';
  history.replaceState(null, '', shareUrl());
  try {
    const submitted = {...body, max_results: 2000, view: pinnedView || options.build_id};
    const response = await fetch('/api/enrichment', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(submitted)});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : (data.detail || []).map(d => d.msg).join('; ') || 'Enrichment failed');
    runRequest = submitted; edgeCache = new Map(); runSet = loaded; render(data); $('#enrichment-status').textContent = '';
  } catch (e) {
    $('#enrichment-error').hidden = false; $('#enrichment-error').textContent = e.message; $('#enrichment-status').textContent = '';
  } finally { button.disabled = false; }
}

function download() {
  if (!result) return;
  const header = ['set_id','set_label','set_status','study_count','study_size','set_size','background_size','expected','fold_enrichment','p_value','fdr','bonferroni','posterior','equivalent_ids','gene_ids','gene_symbols', ...(result.network ? ['connections','by_fdr','neighbor_ids','overlap_ids', ...(result.network.kind === 'neighborhood' ? ['reference_size','go_context'] : [])] : [])];
  const clean = v => String(v ?? '').replace(/[\t\r\n]/g, ' ');
  const lines = result.terms.map(t => [t.id, t.label, t.status, t.study_count, result.counts.study, t.term_size, result.counts.population, t.expected, t.fold_enrichment, t.p_value, t.fdr, t.bonferroni, t.posterior ?? '', (t.equivalents || []).map(e=>e.id).join('|'), t.genes.map(g=>g.id).join('|'), t.genes.map(g=>g.symbol).join('|'), ...(result.network ? [t.connections,t.by_fdr,t.neighbors.map(g=>g.id).join('|'),t.overlap_genes.map(g=>g.id).join('|'), ...(result.network.kind === 'neighborhood' ? [t.reference_size, JSON.stringify(t.go_context)] : [])] : [])].map(clean).join('\t'));
  const meta = ['# AmiGO 3 gene set enrichment', '# build: ' + result.build_id, '# counts: '+JSON.stringify(result.counts), '# truncated: '+result.truncated, ...Object.entries(result.parameters).map(([k, v]) => `# ${k}: ${v}`), ...(result.network ? ['# network_null: '+JSON.stringify(result.network.null), '# network_coverage: '+JSON.stringify(result.network.coverage), '# network_policy: '+JSON.stringify(result.network.policy), ...(result.network.neighborhoods ? ['# neighborhood_partition: '+JSON.stringify(Object.fromEntries(Object.entries(result.network.neighborhoods).filter(([k])=>!['nodes','edges'].includes(k))))] : [])] : [])];
  const blob = new Blob([[...meta, header.join('\t'), ...lines].join('\n') + '\n'], {type: 'text/tab-separated-values'});
  const a = Object.assign(document.createElement('a'), {href: URL.createObjectURL(blob), download: `enrichment-${result.parameters.species}-${result.parameters.target==='neighborhood'?'neighborhood':result.parameters.target==='gocam'?'gocam':result.parameters.aspect}.tsv`});
  a.click(); URL.revokeObjectURL(a.href);
}

(async () => {
  try {
    const response = await fetch('/api/enrichment'); options = await response.json();
    if (!response.ok) throw new Error(options.detail || 'Enrichment unavailable');
    $('#target').innerHTML = Object.entries(options.targets).map(([k, v]) => `<option value="${k}"${k==='gocam'&&!options.species.some(s=>s.gocam_genes)?' disabled':''}>${esc(v)}</option>`).join('');
    applyTarget();
    $('#analysis-method').innerHTML = Object.entries(options.methods).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
    $('#aspect').innerHTML = Object.entries(options.aspects).map(([k, v]) => `<option value="${k}">${esc(v.replace('_',' '))}</option>`).join('');
    $('#evidence').innerHTML = Object.entries(options.evidence).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
    $('#exclusions').insertAdjacentHTML('beforeend', Object.entries(options.exclusions || {}).map(([k, e]) => `<label><input type="checkbox" name="${esc(k)}" id="${esc(k.replaceAll('_','-'))}"><span>${esc(e.label)}<small>${esc(e.description)}</small></span></label>`).join(''));
    $('#build').textContent = 'Snapshot ' + options.build_id;
    const params = new URLSearchParams(location.search);
    restore(params);
    applyMethod(false);
    $('#species').addEventListener('change', () => { clearLoaded(); loadExamples(); if (networkMode()) { requestedNetworkTaxon = null; $('#network-taxon').value = ''; loadNetworkOptions().catch(showNetworkError); } });
    $('#target').addEventListener('change', () => { applyTarget(); loadExamples(); showLoaded(); });
    await loadExamples();
    if (params.get('geneset')) await loadGeneset(params.get('geneset'), !!params.get('genes'));
    if ((params.get('genes') || (params.get('geneset') && loaded)) && params.get('run') !== '0') run();
  } catch (e) { $('#enrichment-error').hidden = false; $('#enrichment-error').textContent = e.message; }
})();
$('#enrichment-form').addEventListener('submit', run);
$('#example-set').addEventListener('change', () => { if ($('#example-set').value) loadGeneset($('#example-set').value); });
$('#geneset-search').addEventListener('click', searchGenesets);
$('#geneset-query').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); searchGenesets(); } });
$('#genes').addEventListener('input', () => { if (loadedSet) clearLoaded(); });
$('#threshold').addEventListener('input', () => { AmigoEnrichmentGraphs.enableFdrFilter(); renderTerms(); history.replaceState(null, '', shareUrl()); });
$('#show-all').addEventListener('change', () => { renderTerms(); history.replaceState(null, '', shareUrl()); });
$('#analysis-method').addEventListener('change', () => { applyMethod(true); history.replaceState(null, '', shareUrl()); });
$('#download').addEventListener('click', download);
$('#copy-link').addEventListener('click', async () => { await navigator.clipboard.writeText(location.origin + shareUrl()); $('#copy-link').textContent = 'Link copied'; setTimeout(() => { $('#copy-link').textContent = 'Copy link'; }, 1500); });
$('#geneset-results').addEventListener('click', e => { const hit = e.target.closest('.geneset-hit'); if (hit) loadGeneset(hit.dataset.id); });
$('#term-table').addEventListener('toggle', e => { if (e.target.matches('.network-evidence')) inspectNetworkEdges(e.target); }, true);
$('#network-options').addEventListener('change', () => history.replaceState(null, '', shareUrl()));
$('#network-taxon').addEventListener('change', () => { requestedNetworkTaxon = $('#network-taxon').value; });
