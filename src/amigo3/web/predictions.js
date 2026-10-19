/* Frozen external sets share a build-pinned comparison and retain source evidence. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = n => Number(n || 0).toLocaleString();
  let catalog, selected, offset = 0, serial = 0;
  const pageSize = 50;
  let filters = {}, gene = "";
  const state = () => new URLSearchParams({set: selected.id, view: catalog.build_id, gene, ...Object.fromEntries(Object.entries(filters).map(([k,v])=>[k,v.join(',')])), q: $('search').value});
  const apiParams = () => { const p = state(); p.delete('set'); p.set('view', catalog.build_id); return p; };
  async function get(url) { const r = await fetch(url); const data = await r.json(); if (!r.ok) throw new Error(data.detail || 'Comparison unavailable'); return data; }
  function error(e) { $('error').textContent = e.message; $('error').hidden = false; }
  function renderSet() {
    const s = selected.summary;
    $('description').textContent = selected.description || selected.label;
    $('baseline-warning').hidden = selected.baseline_available;
    $('metrics').innerHTML = [
      [s.input_rows,'source annotation rows'], [s.input_subjects,'source proteins'],
      [s.shared_pairs,'shared protein–GO pairs','exact'], [s.status_pair_counts.covered,'pairs already covered','covered'],
      [s.status_pair_counts.refinement,'candidate refinements','refinement'], [s.status_pair_counts.unmatched,'other unmatched pairs','unmatched'],
      [s.proteins_without_baseline_annotations,'proteins without baseline annotations']
    ].map(([n,label,status],i) => `<div>${status?`<button type="button" data-status="${status}" title="Filter source rows in this category">`:''}<strong>${i > 1 && !selected.baseline_available ? '—' : number(n)}</strong><span>${esc(label)}</span>${status?'</button>':''}</div>`).join('');
    $('metrics').querySelectorAll('[data-status]').forEach(button=>button.addEventListener('click',()=>{filters.status=[button.dataset.status]; offset=0; loadRows();}));
    $('scope').textContent = `Comparison scope: ${number(s.comparable_proteins)} proteins with mapped, active positive external claims (${number(s.comparable_pairs)} distinct protein–GO pairs). Baseline-only pairs on this cohort: ${number(s.baseline_only_pairs)}. Taxon: ${selected.taxon}.`;
    const sourceInfo = Object.entries(selected.baseline_sources).map(([id,b]) => `${id}\n${(b.headers || []).join('\n')}\nSHA-256: ${b.sha256}`).join('\n\n');
    $('provenance').innerHTML = `<p><strong>Ontology:</strong> ${esc(selected.ontology_version)} · <strong>Build:</strong> ${esc(catalog.build_id)}</p><p><strong>Protein mappings:</strong> ${esc(Object.entries(s.mapping_subject_counts).map(([k,v]) => `${k}: ${number(v)}`).join(' · '))}</p><p>Subject catalog: ${number(s.subject_catalog_size)} proteins. Empty-symbol rows retained: ${number(s.missing_symbol_rows)}. Exact assertion matches: ${number(s.assertion_match_rows)} rows. Explicit opposite-polarity flags: ${number(s.opposite_assertion_rows)} rows.</p><h3>External GAF</h3><pre>${esc(selected.headers.join('\n'))}</pre><h3>Baseline sources</h3><pre>${esc(sourceInfo || 'Unavailable')}</pre><p>Release dates may differ. Download the provenance and mappings to inspect the source versions and matching evidence.</p>`;
    const base = `/api/predictions/${encodeURIComponent(selected.id)}`;
    $('source-downloads').innerHTML = Object.keys(selected.files).map(k => `<a href="${base}/sources/${encodeURIComponent(k)}?view=${catalog.build_id}">Download ${esc(k)}</a>`).join('');
    $('summary-download').href = `${base}/export?format=json&view=${catalog.build_id}`;
    $('gene-scope').hidden = !gene;
    $('gene-scope').textContent = gene ? `Showing predictions linked to ${gene}. Protein comparisons remain separate.` : '';
  }
  function changeFacet(field,value,checked) {
    const values = new Set(filters[field] || []);
    if (checked) values.add(value); else values.delete(value);
    filters[field] = [...values]; offset=0; loadRows();
  }
  function renderFacets(data) {
    AmigoPredictions.facets($('prediction-facets'),data,filters,catalog.facet_labels,catalog.statuses,changeFacet);
    $('prediction-chips').innerHTML = Object.entries(filters).flatMap(([field,values])=>values.map(value=>`<button type="button" data-field="${field}" data-value="${esc(value)}">${esc(AmigoPredictions.valueLabel(field,value,catalog.statuses))} ×</button>`)).join('');
    $('prediction-chips').querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>changeFacet(button.dataset.field,button.dataset.value,false)));
  }
  async function loadRows() {
    const request = ++serial;
    $('error').hidden = true; $('row-count').textContent = 'Loading rows…';
    $('rows').replaceChildren(); $('previous').disabled = true; $('next').disabled = true;
    const params = apiParams();
    $('export').href = `/api/predictions/${encodeURIComponent(selected.id)}/export?${params}`;
    history.replaceState(null,'','/predictions?' + state());
    params.set('offset',offset); params.set('size',pageSize);
    try {
      const data = await get(`/api/predictions/${encodeURIComponent(selected.id)}/claims?${params}`);
      if (request !== serial) return;
      renderFacets(data.facets);
      $('rows').innerHTML = data.rows.map(row=>AmigoPredictions.row(row,catalog.statuses)).join('');
      $('row-count').textContent = data.total ? `${number(offset+1)}–${number(offset+data.rows.length)} of ${number(data.total)} source annotation rows` : 'No source rows match these filters.';
      $('previous').disabled = offset === 0; $('next').disabled = offset + data.rows.length >= data.total;
    } catch(e) { if (request === serial) { error(e); $('row-count').textContent = 'Rows unavailable'; } }
  }
  $('reset-predictions').addEventListener('click',()=>{filters={}; gene=''; $('search').value=''; offset=0; renderSet(); loadRows();});
  $('filters').addEventListener('submit', e => { e.preventDefault(); offset=0; loadRows(); });
  $('prediction-set').addEventListener('change', () => { selected=catalog.sets.find(s=>s.id===$('prediction-set').value); offset=0; renderSet(); loadRows(); });
  $('previous').addEventListener('click', () => { offset=Math.max(0,offset-pageSize); loadRows(); });
  $('next').addEventListener('click', () => { offset+=pageSize; loadRows(); });
  get('/api/predictions').then(data => {
    catalog=data; $('loading').hidden=true; $('build').textContent=`Build ${data.build_id}`;
    if (!data.sets.length) { $('empty').hidden=false; return; }
    const p = new URLSearchParams(location.search);
    if (p.has('view') && p.get('view')!==catalog.build_id) throw new Error('This link refers to a different build. Open /predictions to compare against the current release.');
    if (p.has('set') && !data.sets.some(s=>s.id===p.get('set'))) throw new Error('The requested set is unavailable in this release.');
    selected=data.sets.find(s=>s.id===p.get('set')) || data.sets[0];
    $('prediction-set').innerHTML=data.sets.map(s=>`<option value="${esc(s.id)}">${esc(s.label)}</option>`).join('');
    $('prediction-set').value=selected.id; renderSet();
    filters=Object.fromEntries(Object.keys(catalog.facet_labels).map(k=>[k,(p.get(k)||'').split(',').filter(Boolean)])); gene=p.get('gene') || ''; renderSet(); $('search').value=p.get('q') || '';
    $('workspace').hidden=false; loadRows();
  }).catch(e=>{ $('loading').hidden=true; error(e); });
})();
