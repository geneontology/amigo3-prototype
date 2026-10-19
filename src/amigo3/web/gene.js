const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = n => Number(n).toLocaleString();
const humanize = s => String(s || '').replaceAll('_', ' ').toLowerCase();
const aspects = {F:'Molecular function',P:'Biological process',C:'Cellular component'};
let data, entries, bibliography = [], assessmentSerial = 0;
let catalog = {collections:{annotations:{fields:{},facets:[],facet_groups:[],hierarchies:{}}}};
let state = {
  collection:'annotations',
  filters:[],
  facets:[],
  display:'compact',
  fields:[],
  omitFacets:['species','has_review'],
  forceVisibleGroups:[],
  sort:'priority',
  viewMode:'rows',
  defaultRedundantFilter:false
};

function filterBy(field, values) {
  if (field === 'is_redundant') state.defaultRedundantFilter = false;
  state.filters = state.filters.filter(f => f.field !== field);
  if (values.length) state.filters.push({id:field, field, op:'in', values});
  syncTopControls();
  renderAnnotations();
}

function syncTopControls() {
  const aspectFilter = state.filters.find(f => f.field === 'aspect');
  if ($('#aspect-filter')) $('#aspect-filter').value = aspectFilter?.values?.length === 1 ? aspectFilter.values[0] : '';
  const hasRev = state.filters.find(f => f.field === 'has_annotation_review');
  const revAct = state.filters.find(f => f.field === 'review_actions');
  if ($('#review-filter')) {
    if (revAct?.values?.length === 1) $('#review-filter').value = revAct.values[0];
    else if (hasRev?.values?.length === 1) $('#review-filter').value = hasRev.values[0] ? 'reviewed' : 'unreviewed';
    else $('#review-filter').value = '';
  }
  if ($('#annotation-sort')) $('#annotation-sort').value = state.sort || 'priority';
  document.querySelectorAll('[data-annotation-view]').forEach(btn => {
    btn.setAttribute('aria-pressed', String(btn.dataset.annotationView === state.viewMode));
  });
}

async function api(path) {
  const response = await fetch(path);
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Unable to load this record');
  return result;
}
// Header identifiers: the record's own ID plus these prefixes stay visible; other
// cross-references sit behind a disclosure. MOD/HGNC prefixes come first.
const PRIMARY_IDENTIFIER_PREFIXES=['HGNC','PomBase','WB','MGI','SGD','ZFIN','RGD','FB','AGI_LocusCode','TAIR','dictyBase','Xenbase','CGD','UniProtKB','ENSEMBL','NCBIGene'];
function isPrimaryIdentifier(prefix, local, unversionedEnsembl) {
  if(!PRIMARY_IDENTIFIER_PREFIXES.includes(prefix)) return false;
  if(prefix==='UniProtKB') return !/-PRO_/.test(local);
  // Ensembl gene IDs only; a versioned ID is shown only when no unversioned form exists.
  if(prefix==='ENSEMBL') {
    const [base,version]=local.split('.');
    return /^ENS[A-Z]*G\d+$/.test(base) && (!version || !unversionedEnsembl.has(base));
  }
  return true;
}
// Keep provider CURIEs visible even when their destination uses a bare accession.
function identifierLinks(record) {
  const prefixes={uniprotkb:'UniProtKB',hgnc:'HGNC',ensembl:'ENSEMBL',pombase:'PomBase',wb:'WB',wormbase:'WB',ncbigene:'NCBIGene',refseq:'RefSeq',rnacentral:'RNAcentral',complexportal:'ComplexPortal',embl:'EMBL',mim:'MIM',mgi:'MGI',sgd:'SGD',zfin:'ZFIN',rgd:'RGD',fb:'FB',flybase:'FB',tair:'TAIR'};
  const destinations={
    UniProtKB:local=>'https://www.uniprot.org/uniprotkb/'+local+'/entry',
    WB:local=>'https://wormbase.org/species/c_elegans/gene/'+local,
    PomBase:local=>'https://www.pombase.org/gene/'+local,
    FB:local=>'https://flybase.org/reports/'+local+'.html',
    AGI_LocusCode:local=>'https://www.arabidopsis.org/servlets/TairObject?type=locus&name='+local,
    dictyBase:local=>'https://dictybase.org/gene/'+local,
    HGNC:local=>'https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/HGNC:'+local,
    ENSEMBL:local=>'https://www.ensembl.org/id/'+local,
    NCBIGene:local=>'https://www.ncbi.nlm.nih.gov/gene/'+local,
    RefSeq:local=>'https://www.ncbi.nlm.nih.gov/'+(/^(AP|NP|XP|YP|WP|ZP)_/.test(local)?'protein/':'nuccore/')+local,
    RNAcentral:local=>'https://rnacentral.org/rna/'+local,
    ComplexPortal:local=>'https://www.ebi.ac.uk/complexportal/complex/'+local,
    EMBL:local=>'https://www.ebi.ac.uk/ena/browser/view/'+local,
    MIM:local=>'https://omim.org/entry/'+local
  };
  const seen=new Set();
  const ids=[record.id,...record.identifiers].flatMap(value=>{
    const match=String(value).trim().match(/^([A-Za-z][A-Za-z0-9_.-]*):([^\s]+)$/);
    if(!match) return [];
    const prefix=Object.hasOwn(prefixes,match[1].toLowerCase())?prefixes[match[1].toLowerCase()]:match[1];
    const local=prefix==='HGNC'?match[2].replace(/^HGNC:/i,''):match[2];
    const curie=prefix+':'+local;
    if(seen.has(curie)) return [];
    seen.add(curie);
    return [{prefix,local,curie}];
  });
  const unversionedEnsembl=new Set(ids.filter(id=>id.prefix==='ENSEMBL'&&!id.local.includes('.')).map(id=>id.local));
  const rank=id=>{const i=PRIMARY_IDENTIFIER_PREFIXES.indexOf(id.prefix);return i<0?PRIMARY_IDENTIFIER_PREFIXES.length:i;};
  const [own,...rest]=ids;
  const primary=rest.filter(id=>isPrimaryIdentifier(id.prefix,id.local,unversionedEnsembl)).sort((a,b)=>rank(a)-rank(b));
  const more=rest.filter(id=>!primary.includes(id));
  const link=(id,attrs='')=>{
    const href=Object.hasOwn(destinations,id.prefix)?destinations[id.prefix](encodeURIComponent(id.local)):null;
    return href?`<a${attrs} class="mono" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(id.curie)}</a>`:`<span${attrs} class="mono">${esc(id.curie)}</span>`;
  };
  if(!own) return '';
  const disclosure=more.length?`<details class="more-identifiers"><summary>${more.length} more identifier${more.length===1?'':'s'}</summary><div class="id-list">${more.map(id=>link(id)).join('')}</div></details>`:'';
  return link(own,' id="gene-id"')+primary.map(id=>link(id)).join('')+disclosure;
}
function externalReference(id) {
  if (id?.startsWith('PMID:')) return 'https://pubmed.ncbi.nlm.nih.gov/'+encodeURIComponent(id.slice(5))+'/';
  if (id?.startsWith('GO_REF:')) return 'https://geneontology.org/GO_REF/'+encodeURIComponent(id.slice(7));
  if (id?.startsWith('Reactome:')) return 'https://reactome.org/content/detail/'+encodeURIComponent(id.slice(9));
  return null;
}
function reference(id, reviewId) {
  const item = bibliography.find(item => item.reference.id === id && (!reviewId || item.review.id === reviewId));
  if (item) return `<a class="review-citation" href="#${esc(item.anchor)}" title="${esc(item.reference.title || id)}">${esc(id)}</a>`;
  return id ? `<a href="/references/${encodeURIComponent(id)}">${esc(id)}</a>` : '';
}
function curie(id, reviewId) {
  if (/^(PMID:|GO_REF:|Reactome:)/.test(id)) return reference(id, reviewId);
  let url;
  if (id.startsWith('GO:')) url = '/terms/'+encodeURIComponent(id);
  else if (id.startsWith('PANTHER:')) url = '/?collection=families&q='+encodeURIComponent(id);
  else if (id.startsWith('UniProtKB:')) url = 'https://www.uniprot.org/uniprotkb/'+encodeURIComponent(id.slice(10))+'/entry';
  else if (id.startsWith('gomodel:')) url = '/gocams/'+encodeURIComponent(id);
  else if (id.startsWith('MODULE:')) url = '/modules/'+encodeURIComponent(id);
  return url ? `<a href="${esc(url)}">${esc(id)}</a>` : esc(id);
}
function prose(text, reviewId) {
  return String(text ?? '').split(/((?:PMID:\d+|GO_REF:\d+|Reactome:R-[A-Z]+-\d+|GO:\d+|PANTHER:[A-Za-z0-9:]+|UniProtKB:[A-Za-z0-9_-]+))/g)
    .map((part, i) => i % 2 ? curie(part, reviewId) : esc(part)).join('');
}
function populated(value) {
  if (value == null || value === '') return false;
  if (Array.isArray(value)) return value.some(populated);
  if (typeof value === 'object') return Object.values(value).some(populated);
  return true; // Explicit false and zero are meaningful review content.
}
function fieldLabel(key) {
  const labels = {directly_involved_in:'Processes', in_complex:'Complex membership', supported_by:'Supporting evidence', reference_review:'Reference assessment', finding_review:'Finding assessment', source_entities:'Sources checked'};
  const text = labels[key] || key.replaceAll('_',' ');
  return text.charAt(0).toUpperCase()+text.slice(1);
}
function reviewValue(value, reviewId) {
  if (Array.isArray(value)) return '<ul>'+value.filter(populated).map(v=>'<li>'+reviewValue(v,reviewId)+'</li>').join('')+'</ul>';
  if (value && typeof value === 'object') {
    if (value.id && value.label) return term(value)+reviewFields(value, ['id','label'], reviewId);
    return reviewFields(value, [], reviewId);
  }
  return prose(value, reviewId);
}
// Explicit presentations below handle common fields; this fallback preserves new
// schema fields and nested judgments without dumping raw JSON or dropping data.
function reviewFields(object, omitted=[], reviewId) {
  const fields = Object.entries(object || {}).filter(([key,value])=>!omitted.includes(key) && populated(value));
  return fields.length ? '<dl class="review-fields">'+fields.map(([key,value])=>`<div><dt>${esc(fieldLabel(key))}</dt><dd>${key === 'supported_by' ? supports(value,reviewId) : reviewValue(value,reviewId)}</dd></div>`).join('')+'</dl>' : '';
}
function term(t) {
  if (!t) return '';
  return `<span>${t.id?.startsWith('GO:')?'<a href="/terms/'+encodeURIComponent(t.id)+'">'+esc(t.label || t.id)+'</a>':esc(t.label || t.id || t)}${t.id ? ' <span class="mono muted">'+esc(t.id)+'</span>' : ''}</span>`;
}
function supports(items, reviewId) {
  return (items || []).map(s => `<div class="support">${reference(s.reference_id,reviewId)}${s.supporting_text ? '<blockquote>'+esc(s.supporting_text)+'</blockquote>' : ''}${reviewFields(s,['reference_id','supporting_text'],reviewId)}</div>`).join('');
}
function actionClass(action) {
  return ['ACCEPT','KEEP_AS_NON_CORE'].includes(action) ? 'retain' : ['REMOVE','MODIFY','MARK_AS_OVER_ANNOTATED'].includes(action) ? 'reconsider' : 'pending';
}
function assessment(item) {
  const entry = item.entry, review = entry.review || {}, action = review.action || 'PENDING';
  const reason = review.reason || review.summary || 'No written assessment.';
  const propagation = populated(review.propagation_review) ? `<section class="propagation-review"><h5>Propagation assessment</h5>${reviewFields(review.propagation_review,[],item.review_id)}</section>` : '';
  const details = reviewFields(review,['action','reason','summary','proposed_replacement_terms','propagation_review'],item.review_id);
  return `<div class="assessment"><span class="action ${actionClass(action)}">${esc(humanize(action))}</span><p class="review-rationale"><span id="rationale-${++assessmentSerial}" data-review-id="${esc(item.review_id)}">${esc(reason)}</span></p>${(review.proposed_replacement_terms || []).length ? '<p><strong>Proposed replacement: </strong>'+review.proposed_replacement_terms.map(term).join('; ')+'</p>' : ''}${propagation}<details class="review-reason"><summary>Evidence &amp; review details</summary>${review.summary && review.summary !== reason ? '<p><strong>Summary: </strong>'+prose(review.summary,item.review_id)+'</p>' : ''}${details}${reviewFields(entry,['term','review','evidence_type','original_reference_id'],item.review_id)}<a class="source-ref" href="#review-${esc(item.review_id)}">Review source · ${esc(item.subject_id)}</a></details></div>`;
}
function annotationBadges(a) {
  const badges = [];
  if (a.core_agreement === 'exact') badges.push('<span class="annotation-badge badge-core-exact" title="Matches a core function in the gene review">Core function</span>');
  else if (a.core_agreement === 'narrower') badges.push('<span class="annotation-badge badge-core-close" title="More specific than a reviewed core function">Refines core</span>');
  else if (a.core_agreement === 'broader') badges.push('<span class="annotation-badge badge-core-close" title="Broader ancestor of a reviewed core function">Broader than core</span>');
  if (a.iba_agreement === 'exact') badges.push('<span class="annotation-badge badge-iba-agree" title="Agrees with a PAINT/IBA annotation on this gene">Agrees with IBA</span>');
  else if (a.iba_agreement === 'close') badges.push('<span class="annotation-badge badge-iba-agree" title="Closely related to a PAINT/IBA annotation on this gene">Close to IBA</span>');
  if (a.is_redundant) badges.push('<span class="annotation-badge badge-redundant" title="Broader term subsumed by a more specific annotation on this gene">Redundant</span>');
  return badges.join('');
}
function annotationRow(a) {
  const reviews = a.review_entry_ids.map(id => entries.get(id));
  const evGroup = a.evidence_group || 'other';
  return `<article class="gene-annotation${a.is_redundant ? ' is-redundant' : ''}" id="annotation-${esc(a.id)}"><div class="annotation-assertion"><div class="assertion-top">${a.negation ? '<span class="negative">NOT</span> ' : ''}<span class="relation">${esc(humanize(a.relation))}</span><span class="evidence-code evidence-${esc(evGroup)}">${esc(a.evidence_code)}</span>${a.method ? `<span class="annotation-method">${esc(a.method)}</span>` : ''}${annotationBadges(a)}</div><h4><a href="/terms/${encodeURIComponent(a.ontology_class_id)}">${esc(a.term_label)}</a></h4><div class="mono annotation-term-id">${esc(a.ontology_class_id)}</div><div class="annotation-references">${a.references.map(reference).join(' · ')}</div><p class="annotation-subject">${esc(a.db_object_id)} · ${esc(a.assigned_by)}</p><details class="annotation-provenance" data-annotation="${esc(a.id)}"><summary>Evidence &amp; source record</summary><dl><dt>Evidence</dt><dd>${esc(a.evidence_code)} · ${esc(a.evidence_type.join(', '))}</dd>${a.method ? `<dt>Method</dt><dd>${esc(a.method)}</dd>` : ''}<dt>With / from</dt><dd>${esc(a.with_or_from || '—')}</dd><dt>Extensions</dt><dd>${esc(a.annotation_extensions || '—')}</dd><dt>Annotation date</dt><dd>${esc(a.annotation_date)}</dd><dt>Source</dt><dd>${esc(a.source_id)} · line ${number(a.source_line)}</dd></dl><div class="raw-source"></div></details></div><div class="annotation-review">${reviews.length ? reviews.map(assessment).join('') : '<span class="no-review">No matching review entry</span>'}</div></article>`;
}
function sortAnnotations(rows) {
  const mode = state.sort || 'priority';
  return [...rows].sort((a, b) => {
    if (mode === 'agreement') {
      return (a.agreement_rank ?? 3) - (b.agreement_rank ?? 3)
        || (a.evidence_code === 'IBA' ? 0 : 1) - (b.evidence_code === 'IBA' ? 0 : 1)
        || Number(Boolean(a.is_redundant)) - Number(Boolean(b.is_redundant))
        || a.term_label.localeCompare(b.term_label)
        || a.id.localeCompare(b.id);
    }
    if (mode === 'term') {
      return a.term_label.localeCompare(b.term_label)
        || (a.evidence_code === 'IBA' ? 0 : 1) - (b.evidence_code === 'IBA' ? 0 : 1)
        || Number(Boolean(a.is_redundant)) - Number(Boolean(b.is_redundant))
        || a.id.localeCompare(b.id);
    }
    if (mode === 'id') return a.id.localeCompare(b.id);
    return (a.priority_rank ?? 4) - (b.priority_rank ?? 4)
      || Number(Boolean(a.is_redundant)) - Number(Boolean(b.is_redundant))
      || a.term_label.localeCompare(b.term_label)
      || a.id.localeCompare(b.id);
  });
}
function renderSummaryBoxes(rows) {
  const groups = new Map();
  for (const a of rows) {
    const key = `${a.ontology_class_id}:${a.negation ? 'NOT' : 'POS'}`;
    if (!groups.has(key)) {
      groups.set(key, {
        id: a.ontology_class_id,
        label: a.term_label,
        negation: a.negation,
        is_redundant: Boolean(a.is_redundant),
        core_agreement: a.core_agreement,
        iba_agreement: a.iba_agreement,
        evidences: new Map(),
        actions: new Set(),
        rows: []
      });
    }
    const g = groups.get(key);
    g.rows.push(a);
    g.is_redundant = g.is_redundant && Boolean(a.is_redundant);
    if (a.core_agreement === 'exact') g.core_agreement = 'exact';
    else if (!g.core_agreement && a.core_agreement) g.core_agreement = a.core_agreement;
    if (a.iba_agreement === 'exact') g.iba_agreement = 'exact';
    if (!g.evidences.has(a.evidence_code)) g.evidences.set(a.evidence_code, a.evidence_group || 'other');
    for (const act of a.review_actions || []) g.actions.add(act);
  }
  return `<div class="term-summary-grid">${[...groups.values()].map(g => {
    const evPills = [...g.evidences.entries()].map(([code, grp]) => `<span class="evidence-code evidence-${esc(grp)}">${esc(code)}</span>`).join('');
    const actionPills = [...g.actions].map(act => `<span class="action ${actionClass(act)}">${esc(humanize(act))}</span>`).join('');
    const coreBadge = g.core_agreement === 'exact' ? '<span class="annotation-badge badge-core-exact">Core</span>' : '';
    const ibaBadge = g.iba_agreement === 'exact' ? '<span class="annotation-badge badge-iba-agree">IBA match</span>' : '';
    const redBadge = g.is_redundant ? '<span class="annotation-badge badge-redundant">Redundant</span>' : '';
    return `<article class="term-summary-box${g.is_redundant ? ' is-redundant' : ''}" data-term-id="${esc(g.id)}"><div class="term-box-badges">${g.negation ? '<span class="negative">NOT</span>' : ''}${evPills}${coreBadge}${ibaBadge}${actionPills}${redBadge}</div><h4><a href="/terms/${encodeURIComponent(g.id)}">${esc(g.label)}</a></h4><div class="term-box-footer"><span class="mono annotation-term-id">${esc(g.id)}</span><button type="button" class="text-button inspect-term-rows" data-inspect-term="${esc(g.id)}">${number(g.rows.length)} ${g.rows.length === 1 ? 'annotation' : 'annotations'} →</button></div></article>`;
  }).join('')}</div>`;
}
function renderAnnotations() {
  assessmentSerial = 0;
  const search = $('#annotation-search').value.trim().toLowerCase();
  const matchesSearch = a => {
    if (!search) return true;
    const text = [a.term_label,a.ontology_class_id,a.relation,a.evidence_code,a.db_object_id,a.assigned_by,...a.references,...a.review_entry_ids.map(id => JSON.stringify(entries.get(id).entry.review))].join(' ').toLowerCase();
    return search.split(/\s+/).every(token => text.includes(token));
  };
  const facets = computeLocalFacets(data.annotations, matchesSearch);
  renderFacetPicker(renderAnnotations);
  renderFilterChips(() => { syncTopControls(); renderAnnotations(); });
  renderFacets(facets, renderAnnotations);

  const filtered = data.annotations.filter(a => matchesSearch(a) && state.filters.every(f => matchesFilter(a, f)));
  const annotations = sortAnnotations(filtered);
  const redundantTotal = data.annotations.filter(a => a.is_redundant).length;
  const redundantHidden = state.filters.some(f => f.field === 'is_redundant' && f.values.includes(false) && !f.values.includes(true)) ? redundantTotal : 0;
  $('#annotation-tally').textContent = annotations.length === data.annotation_count
    ? `All ${number(data.annotation_count)} annotations`
    : `${number(annotations.length)} of ${number(data.annotation_count)} annotations${redundantHidden ? ` · ${number(redundantHidden)} redundant hidden` : ''}`;

  if (state.viewMode === 'summary') {
    $('#annotation-list').innerHTML = annotations.length ? ['F','P','C'].map(aspect => {
      const rows = annotations.filter(a => a.aspect === aspect);
      const uniqueTerms = new Set(rows.map(r => r.ontology_class_id)).size;
      return rows.length ? `<section class="aspect-group"><h3>${aspects[aspect]} <span>${number(uniqueTerms)} ${uniqueTerms === 1 ? 'term' : 'terms'} · ${number(rows.length)} ${rows.length === 1 ? 'annotation' : 'annotations'}</span></h3>${renderSummaryBoxes(rows)}</section>` : '';
    }).join('') : '<p class="empty">No annotations match these filters.</p>';
    document.querySelectorAll('[data-inspect-term]').forEach(btn => btn.onclick = () => {
      state.viewMode = 'rows';
      $('#annotation-search').value = btn.dataset.inspectTerm;
      syncTopControls();
      renderAnnotations();
    });
  } else {
    $('#annotation-list').innerHTML = annotations.length ? ['F','P','C'].map(aspect => {
      const rows = annotations.filter(a => a.aspect === aspect);
      return rows.length ? `<section class="aspect-group"><h3>${aspects[aspect]} <span>${number(rows.length)}</span></h3><div class="annotation-columns"><span>Upstream annotation</span><span>Review context</span></div>${rows.map(annotationRow).join('')}</section>` : '';
    }).join('') : '<p class="empty">No annotations match these filters.</p>';
  }
  document.querySelectorAll('.annotation-provenance').forEach(details => {
    const annotation=annotations.find(a=>a.id===details.dataset.annotation);
    details.before(AmigoChat.annotationButton(annotation,data.build_id));
  });
  document.querySelectorAll('.annotation-provenance').forEach(details => details.addEventListener('toggle', async () => {
    if (!details.open || details.dataset.loaded) return;
    details.dataset.loaded = 'loading';
    const target = details.querySelector('.raw-source');
    target.textContent = 'Loading original row…';
    try {
      const p = await api('/api/provenance/'+encodeURIComponent(details.dataset.annotation));
      target.innerHTML = `<p><a href="${esc(p.source.url)}" target="_blank" rel="noopener">Upstream file ↗</a></p><p class="checksum">SHA-256: ${esc(p.source.sha256)}</p><pre>${esc(p.raw_record)}</pre>`;
      details.dataset.loaded = 'true';
    } catch (e) { target.textContent = e.message; delete details.dataset.loaded; }
  }));
  renderAdditionalReviews();
  document.querySelectorAll('.review-rationale > span').forEach(target => {
    AmigoDetail.collapseText(target, 650);
    const linkCitations = () => { target.innerHTML = prose(target.textContent, target.dataset.reviewId); };
    target.nextElementSibling?.addEventListener('click',() => { linkCitations(); renderReferences(); });
    linkCitations();
  });
  renderReferences();
}
function renderReviewDescription() {
  const descriptions = data.reviews.filter(review => review.document.description?.trim()).map(review => ({
    text: review.document.description.trim(), review
  }));
  if (!descriptions.length && data.record.review_description?.trim()) {
    descriptions.push({text: data.record.review_description.trim()});
  }
  const overview = $('#review-overview');
  overview.hidden = !descriptions.length;
  overview.innerHTML = descriptions.map(({text, review}, index) => `<div class="review-description"><p><span id="review-description-${index}">${esc(text)}</span></p>${review ? `<a class="source-ref" href="#review-${esc(review.id)}">${esc(review.document.gene_symbol || review.document.id)} · review source</a>` : ''}</div>`).join('');
  overview.querySelectorAll('p > span').forEach(target => AmigoDetail.collapseText(target));
}
function coreIbaBadge(index) {
  const info = (data.core_iba_agreements || [])[index];
  if (!info || info.status === 'none') return '';
  if (info.status === 'exact') {
    return `<div class="core-iba-badge iba-exact">✓ Agrees with PAINT / IBA (${info.matches.map(m => esc(m.term_label)).join(', ')})</div>`;
  }
  if (info.status === 'close') {
    return `<div class="core-iba-badge iba-close">≈ Close PAINT / IBA match (${info.matches.map(m => esc((m.relation === 'narrower_iba' ? 'more specific IBA: ' : 'broader IBA: ') + m.term_label)).join('; ')})</div>`;
  }
  return '<div class="core-iba-badge iba-unmatched">No matching PAINT / IBA molecular function</div>';
}
function renderIbaStandinCore(iba) {
  const listSection = (title, items) => items?.length ? `<div><dt>${esc(title)}</dt><dd><ul>${items.map(t => `<li>${term(t)}</li>`).join('')}</ul></dd></div>` : '';
  const families = iba.panther_families?.length ? `<div><dt>PANTHER ancestry</dt><dd>${iba.panther_families.map(id => curie(id)).join(' · ')}</dd></div>` : '';
  const refs = iba.references?.length ? `<div><dt>References</dt><dd>${iba.references.map(r => reference(r)).join(' · ')}</dd></div>` : '';
  const primary = iba.molecular_functions?.[0] || iba.directly_involved_in?.[0] || iba.locations?.[0] || 'Evolutionary function summary';
  return `<h3>Core function from evolutionary model (PAINT / IBA)</h3><div class="core-grid"><article class="core-card iba-standin-card" id="core-function-iba" data-citation-label="IBA core function"><div class="core-iba-badge iba-standin">Stand-in summary · No curated gene review yet</div><h4>${term(primary)}</h4><p>${esc(iba.description)}</p><dl class="review-fields">${listSection('Molecular functions', iba.molecular_functions)}${listSection('Processes', iba.directly_involved_in)}${listSection('Locations', iba.locations)}${families}${refs}</dl></article></div>`;
}
function renderCoreFunctions() {
  const core = data.reviews.flatMap(review => (review.document.core_functions || []).map(item => ({...item,review_id:review.id})));
  const ibaStandin = !core.length ? data.iba_core_function : null;
  $('#core-functions').innerHTML = core.length
    ? '<h3>Core functions from the review</h3><div class="core-grid">'+core.map((c,i) => `<article class="core-card" id="core-function-${i}" data-citation-label="Core function ${i+1}">${coreIbaBadge(i)}<h4>${term(c.molecular_function || c.contributes_to_molecular_function || c.proposed_molecular_function || 'Core activity')}</h4>${c.contributes_to_molecular_function ? '<p>Contributes to this activity as part of a complex.</p>' : ''}<p>${prose(c.description || '',c.review_id)}</p>${reviewFields(c,['review_id','description','molecular_function','contributes_to_molecular_function','proposed_molecular_function','supported_by'],c.review_id)}<details><summary>Supporting evidence</summary>${supports(c.supported_by,c.review_id)}<a class="source-ref" href="#review-${esc(c.review_id)}">Review source ↓</a></details></article>`).join('')+'</div>'
    : (ibaStandin ? renderIbaStandinCore(ibaStandin) : '');
  const sections = ['functional_isoforms','alternative_products','knowledge_gaps','proposed_new_terms'];
  const questionSections = ['suggested_questions','suggested_experiments'];
  const handled = ['id','gene_symbol','description','existing_annotations','core_functions','references',...sections,...questionSections];
  const reviewSections = (review, keys) => keys.filter(key=>populated(review.document[key])).map(key=>`<section class="review-section" id="${key.replaceAll('_','-')}-${esc(review.id)}"><h3>${esc(fieldLabel(key))}</h3>${review.document[key].map((item,i)=>`<article class="review-card" id="${key}-${esc(review.id)}-${i}" data-citation-label="${esc(fieldLabel(key))} ${i+1}">${reviewValue(key === 'alternative_products' && item.id && !item.id.includes(':') ? {...item,id:'UniProtKB:'+item.id} : item,review.id)}</article>`).join('')}</section>`).join('');
  $('#review-biology').innerHTML = data.reviews.map(review => {
    const doc = review.document;
    const other = reviewFields(doc,handled,review.id);
    return reviewSections(review,sections)+(other ? `<section class="review-section"><h3>Review details · ${esc(doc.gene_symbol || doc.id)}</h3>${other}<a class="source-ref" href="#review-${esc(review.id)}">Review source</a></section>` : '');
  }).join('');
  // Suggested follow-up work gets its own tab so it does not crowd the biology.
  const questionCount = data.reviews.reduce((total, review) => total + questionSections.reduce((n, key) => n + (populated(review.document[key]) ? review.document[key].length : 0), 0), 0);
  $('#review-questions').innerHTML = data.reviews.map(review => reviewSections(review,questionSections)).join('');
  $('#tab-questions').hidden = !questionCount;
  $('#tab-questions').textContent = `Questions & experiments (${number(questionCount)})`;
  $('#tab-core').hidden = !core.length && !ibaStandin && !$('#review-biology').innerHTML;
  $('#tab-core').textContent = core.length ? `Core Functions (${number(core.length)})` : ibaStandin ? 'Core Functions (IBA)' : 'Review overview';
}
function renderAdditionalReviews() {
  const proposed = data.review_entries.filter(item=>item.entry.review?.action === 'NEW');
  const unmatched = data.review_entries.filter(item=>!item.annotation_ids.length && item.entry.review?.action !== 'NEW');
  const matched = data.review_entries.filter(item=>item.annotation_ids.length).length;
  const covered = data.annotations.filter(a=>a.review_entry_ids.length).length;
  $('#review-coverage').innerHTML = data.reviews.length ? `<p><strong>${number(data.review_entries.length)} review entries</strong> · ${number(matched)} matched to ${number(covered)} of ${number(data.annotation_count)} upstream annotations. A review entry can match multiple upstream rows.</p><div>${unmatched.length ? `<a href="#unmatched-reviews">Unmatched reviews (${number(unmatched.length)})</a>` : ''}${proposed.length ? `<a href="#proposed-annotations">Proposed additions (${number(proposed.length)})</a>` : ''}</div>` : '';
  $('#other-reviews').innerHTML = [
    {id:'unmatched-reviews',label:'Unmatched reviews',items:unmatched,note:'Existing-annotation assessments with no matching row in this upstream snapshot. These are not proposed additions.'},
    {id:'proposed-annotations',label:'Proposed additions',items:proposed,note:'New annotation proposals from the review. They have not been inserted into the upstream annotation set.'}
  ].filter(group=>group.items.length).map(group=>`<section class="additional-reviews" id="${group.id}"><h3>${group.label} (${number(group.items.length)})</h3><p class="section-note">${group.note} Shown independently of the upstream annotation filters above.</p>${group.items.map(item=>`<article class="gene-annotation" id="review-entry-${esc(item.id)}"><div class="annotation-assertion"><h4>${term(item.entry.term)}</h4><p>${esc(item.entry.evidence_type)} · ${reference(item.entry.original_reference_id || '',item.review_id)}</p><p class="annotation-subject">${esc(item.subject_id)}${item.entry.isoform ? ' · '+esc(item.entry.isoform) : ''}${item.entry.negated ? ' · NOT' : ''}</p></div><div class="annotation-review">${assessment(item)}</div></article>`).join('')}</section>`).join('');
}
function renderReferences() {
  $('#tab-references').hidden = !bibliography.length;
  $('#tab-references').textContent = `References (${number(bibliography.length)})`;
  const query = $('#reference-search').value.trim().toLowerCase();
  const items = bibliography.filter(item=>!query || JSON.stringify(item.reference).toLowerCase().includes(query));
  $('#reference-tally').textContent = `${number(items.length)} of ${number(bibliography.length)} references`;
  // Links return to the exact context in the gene page, including unopened evidence.
  const citations = [...document.querySelectorAll('#gene-content .review-citation')].filter(a=>!a.closest('#references'));
  $('#reference-list').innerHTML = items.map(item=>{
    const ref = item.reference, url = externalReference(ref.id);
    const targets = new Map();
    for (const link of citations.filter(a=>a.getAttribute('href') === '#'+item.anchor)) {
      const article = link.closest('article[id]');
      if (article) targets.set(article.id, article.dataset.citationLabel || article.querySelector('h4')?.textContent || 'Review entry');
    }
    return `<article class="reference-card" id="${esc(item.anchor)}"><h3>${esc(ref.title || ref.id)}</h3><p>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(ref.id)} ↗</a>` : esc(ref.id)}${ref.id?.includes(':') && !ref.id.startsWith('file:') ? ` · <a href="/references/${encodeURIComponent(ref.id)}">All uses in AmiGO →</a>` : ''}</p>${reviewFields(ref,['id','title'],item.review.id)}${targets.size ? `<details class="citation-contexts"><summary>Cited in ${number(targets.size)} contexts on this page</summary><ul>${[...targets].map(([id,label])=>`<li><a href="#${esc(id)}">${esc(label)}</a></li>`).join('')}</ul></details>` : ''}<a class="source-ref" href="#review-${esc(item.review.id)}">Review source · ${esc(item.review.document.gene_symbol || item.review.document.id)}</a></article>`;
  }).join('') || '<p class="empty">No references match this filter.</p>';
}
function renderRelated() {
  const labels = {gocams:'GO-CAMs', modules:'Modules', families:'Families', reports:'Research reports', projects:'Projects', rules:'Rules & mappings'};
  function groups(collections) {
    return collections.flatMap(collection => {
      const group = data.related?.[collection];
      if (!group?.total) return [];
      return [`<h3>${esc(labels[collection])} (${number(group.total)})</h3><ul>${group.items.map(item => `<li><a href="/${collection}/${encodeURIComponent(item.id)}">${esc(item.label)}</a></li>`).join('')}</ul>${group.total > group.items.length ? `<a href="/?collection=${collection}&filters=${encodeURIComponent(JSON.stringify([{id:'gene',field:'gene_ids',values:[data.record.id]}]))}">Browse all ${number(group.total)} →</a>` : ''}`];
    }).join('');
  }
  const pathwayCollections = ['gocams', 'modules'];
  const pathwayCount = pathwayCollections.reduce((total, collection) => total + (data.related?.[collection]?.total || 0), 0);
  $('#tab-pathways').textContent = `Pathways (${number(pathwayCount)})`;
  $('#tab-evolution').textContent = `Evolution (${number(data.related?.families?.total || 0)})`;
  $('#tab-reports').textContent = `Reports (${number(data.related?.reports?.total || 0)})`;
  $('#gene-related').innerHTML = groups(pathwayCollections) || '<p>No linked pathways or modules in this snapshot.</p>';
  $('#gene-evolution').innerHTML = groups(['families']) || '<p>No linked families in this snapshot.</p>';
  $('#gene-reports').innerHTML = groups(['reports', 'projects', 'rules']) || '<p>No linked reports in this snapshot.</p>';
}
(async () => {
  const from = new URLSearchParams(location.search).get('from');
  if (from?.startsWith('?')) $('#back-results').href = '/'+from;
  try {
    const id = decodeURIComponent(location.pathname.slice('/genes/'.length));
    data = await api('/api/genes/'+encodeURIComponent(id));
    if (data.annotation_collection) {
      catalog = {build_id: data.build_id, collections: {annotations: data.annotation_collection}};
    }
    state.forceVisibleGroups = data.reviews.length ? ['review'] : [];
    const hasRedundant = data.annotations.some(a => a.is_redundant);
    const hasNonRedundant = data.annotations.some(a => !a.is_redundant);
    if (hasRedundant && hasNonRedundant) {
      state.filters = [{id:'is_redundant', field:'is_redundant', op:'in', values:[false]}];
      state.defaultRedundantFilter = true;
    }
    entries = new Map(data.review_entries.map(item => [item.id,item]));
    bibliography = data.reviews.flatMap(review=>(review.document.references || []).map((reference,index)=>({review,reference,anchor:`reference-${review.id}-${index}`})));
    const r = data.record;
    document.title = `${r.symbol} · AmiGO 3`;
    $('#gene-symbol').textContent = r.symbol;
    $('#tab-annotations').textContent = `Annotations (${number(data.annotation_count)})`;
    $('#gene-name').textContent = r.name;
    $('#gene-species').textContent = `${AmigoSpecies.label(r.species).toUpperCase()} / ${(r.gene_product_types || [humanize(r.record_kind)]).join(' · ')}`;
    $('#gene-identifiers').innerHTML = identifierLinks(r);
    $('#gene-synonyms').textContent = r.synonyms.length ? 'Also known as: '+r.synonyms.join(', ') : '';
    $('#review-provenance').innerHTML = data.reviews.map(review => `<article class="review-source" id="review-${esc(review.id)}"><h3>${esc(review.document.gene_symbol || review.document.id)}</h3><p>${esc(review.path)}</p><p class="checksum">SHA-256: ${esc(review.sha256)}</p></article>`).join('') || `<p>${r.has_review && !data.review_snapshots_available ? 'This older build contains only review summaries. Rebuild with --reviews to include annotation assessments.' : 'No review documents in this snapshot.'}</p>`;
    $('#match-policy').textContent = data.review_match_policy;
    $('#build').textContent = 'Snapshot '+data.build_id;
    const actions = [...new Set(data.review_entries.map(item => item.entry.review?.action || 'PENDING'))].sort();
    for (const action of actions) $('#review-filter').add(new Option(humanize(action), action));
    syncTopControls();
    renderReviewDescription(); renderCoreFunctions(); renderAnnotations(); renderRelated();
    AmigoGenePredictions.mount(data.record.id, data.build_id);
    $('#annotation-search').addEventListener('input',renderAnnotations);
    $('#aspect-filter').addEventListener('change', () => {
      const v = $('#aspect-filter').value;
      filterBy('aspect', v ? [v] : []);
    });
    $('#review-filter').addEventListener('change', () => {
      const v = $('#review-filter').value;
      state.filters = state.filters.filter(f => !['has_annotation_review','review_actions'].includes(f.field));
      if (state.defaultRedundantFilter && v) {
        state.filters = state.filters.filter(f => f.field !== 'is_redundant');
        state.defaultRedundantFilter = false;
      }
      if (v === 'reviewed') state.filters.push({id:'has_annotation_review', field:'has_annotation_review', op:'in', values:[true]});
      else if (v === 'unreviewed') state.filters.push({id:'has_annotation_review', field:'has_annotation_review', op:'in', values:[false]});
      else if (v) state.filters.push({id:'review_actions', field:'review_actions', op:'in', values:[v]});
      syncTopControls();
      renderAnnotations();
    });
    $('#annotation-sort').addEventListener('change', () => {
      state.sort = $('#annotation-sort').value;
      renderAnnotations();
    });
    document.querySelectorAll('[data-annotation-view]').forEach(btn => btn.addEventListener('click', () => {
      state.viewMode = btn.dataset.annotationView;
      syncTopControls();
      renderAnnotations();
    }));
    $('#reset-annotations').onclick = () => {
      $('#annotation-search').value = '';
      state.filters = [];
      state.defaultRedundantFilter = false;
      state.viewMode = 'rows';
      syncTopControls();
      renderAnnotations();
    };
    $('#reference-search').addEventListener('input',renderReferences);
    // Cross-panel citations must remain usable after filtering either list.
    function prepareCitationTarget() {
      let target;
      try { target = decodeURIComponent(location.hash.slice(1)); } catch { return; }
      if (target.startsWith('reference-') && $('#reference-search').value) {
        $('#reference-search').value = ''; renderReferences();
      }
      if (target.startsWith('annotation-') && !document.getElementById(target)) $('#reset-annotations').click();
    }
    // Restore filtered targets before the tab controller handles either event.
    window.addEventListener('hashchange',prepareCitationTarget);
    window.addEventListener('popstate',prepareCitationTarget);
    $('#gene-loading').hidden=true;$('#gene-content').hidden=false;
    AmigoTabs.mount({nav:$('.gene-nav'), aliases:{related:'pathways', experiments:'questions'}});
  } catch (e) {
    $('#gene-loading').hidden=true;$('#gene-error').hidden=false;$('#gene-error').textContent=e.message;
  }
})();

