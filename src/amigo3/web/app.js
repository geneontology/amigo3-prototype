const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = (n) => Number(n).toLocaleString();
let catalog, activeRequest, nextCursor, currentQuery;
function stateFromURL() {
  const params=new URLSearchParams(location.search);
  const value={collection:params.get('collection') || 'genes',search:params.get('q') || '',filters:[],display:params.get('display') || 'compact',fields:(params.get('fields') || '').split(',').filter(Boolean),facets:(params.get('facets') || '').split(',').filter(Boolean)};
  value.resultView=params.get('view')==='dashboard'?'dashboard':'list';
  value.sort=params.get('sort') || 'recommended';
  value.includeObsolete=params.get('obsolete')==='include';
  value.dashboard=dashboardFromURL(params);
  try { value.filters=JSON.parse(params.get('filters') || '[]'); } catch { value.filters=[]; }
  return value;
}
let state=stateFromURL(), historyReady=false;
let cursors=[null];
const collectionDisplays=new Map();

async function api(path, body) {
  const response = await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
  const result=await response.json();
  if(!response.ok) throw new Error(typeof result.detail==='string' ? result.detail : JSON.stringify(result.detail));
  return result;
}
function saveURL(updateHistory=true) {
  const p=new URLSearchParams({collection:state.collection});
  if(state.search) p.set('q',state.search);
  if(state.filters.length) p.set('filters',JSON.stringify(state.filters));
  if(state.display!=='compact') p.set('display',state.display);
  if(state.display==='custom') p.set('fields',state.fields.join(','));
  if(state.facets.length) p.set('facets',state.facets.join(','));
  if(state.sort!=='recommended') p.set('sort',state.sort);
  if(state.collection==='terms' && state.includeObsolete) p.set('obsolete','include');
  saveDashboardURL(p);
  const url='?'+p.toString();
  if(location.search!==url) {
    if(historyReady && updateHistory) history.pushState(null,'',url);
    else history.replaceState(null,'',url);
  }
  historyReady=true;
}
function filterBy(field, values) {
  if(field==='obsolete') state.includeObsolete=true;
  state.filters=state.filters.filter(f=>f.field!==field);
  if(values.length) state.filters.push({id:field,field,op:'in',values});
  cursors=[null]; load();
}
function selectCollection(name) {
  if(name!==state.collection) {
    collectionDisplays.set(state.collection,{display:state.display,fields:[...state.fields],sort:state.sort,dashboard:{...state.dashboard}});
    Object.assign(state,collectionDisplays.get(name) || {display:'compact',fields:[],sort:'recommended',dashboard:dashboardFromURL(new URLSearchParams())});
    expandReviews=false;
    $('#customize-fields').open=false;
  }
  state.collection=name;
  state.filters=state.filters.filter(f=>catalog.collections[name].fields[f.field]);
  cursors=[null]; load();
}
async function load(updateHistory=true) {
  normalizeDisplay();
  normalizeDashboard();
  const config=catalog.collections[state.collection];
  if(!Object.hasOwn(config.sort_options,state.sort)) state.sort='recommended';
  if(state.collection==='terms' && !state.includeObsolete && !state.filters.some(f=>f.field==='obsolete'))
    state.filters.push({id:'obsolete',field:'obsolete',op:'in',values:[false]});
  saveURL(updateHistory);
  $('#gocam-network').hidden=state.collection!=='gocams';
  $('#gocam-network').href='/megamodels'+location.search;
  $('#search').value=state.search;
  document.querySelectorAll('[data-collection]').forEach(b=>b.setAttribute('aria-selected',b.dataset.collection===state.collection));
  $('#result-sort').innerHTML=Object.entries(config.sort_options).map(([key,option])=>`<option value="${esc(key)}">${esc(option.label)}</option>`).join('');
  $('#result-sort').value=state.sort;
  $('#sort-description').textContent=config.sort_options[state.sort].description || '';
  $('#sort-controls').hidden=dashboardActive();
  renderDisplayTools();
  prepareDashboard();
  const fields=[...new Set(facetLayout().flatMap(g=>g.fields))];
  const facets=Object.fromEntries(fields.map(field=>[field,{field,limit:Math.max(config.facet_limits?.[field] || 12,Object.keys(hierarchyFor(field)?.nodes || {}).length),exclude_filters:state.filters.filter(f=>f.field===field).map(f=>f.id)}]));
  currentQuery={collection:state.collection,view:catalog.build_id,search:state.search,filters:state.filters,facets,sort:state.sort,page:{size:25,cursor:cursors.at(-1)}};
  if(dashboardActive()) currentQuery.breakdowns=dashboardQueries();
  const histograms=rangeQueries();
  if(Object.keys(histograms).length) currentQuery.histograms=histograms;
  if(activeRequest) activeRequest.abort();
  activeRequest=new AbortController();
  $('#error').hidden=true; $('#status').textContent='Searching…';
  $('#next').disabled=true;
  try {
    const response=await fetch('/api/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(currentQuery),signal:activeRequest.signal});
    const result=await response.json();
    if(!response.ok) throw new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));
    $('#status').innerHTML=`<strong>${number(result.total)}</strong> ${esc(config.label.toLowerCase())}${state.search?' matching “'+esc(state.search)+'”':''}`;
    renderFilterChips(()=>{cursors=[null];load();});
    renderFacets(result.facets);
    renderRangeFacets(result.histograms);
    if(dashboardActive()) renderDashboard(result);
    else renderResults(result.hits);
    nextCursor=result.next_cursor;
    $('#next').disabled=!nextCursor;
    $('#previous').disabled=cursors.length<=1;
    $('#page-label').textContent=`Page ${cursors.length} · ${result.hits.length} shown`;
  } catch(error) {
    if(error.name==='AbortError') return;
    if(dashboardActive()) dashboardFailed();
    $('#status').textContent='Search unavailable'; $('#error').hidden=false; $('#error').textContent=error.message;
  }
}
function resourceURL(collection,id) { return '/'+collection+'/'+encodeURIComponent(id)+'?from='+encodeURIComponent(location.search); }
function referenceLinks(ids) { return ids.map(id=>`<a href="${esc(resourceURL('references',id))}">${esc(id)}</a>`).join(' · '); }
function geneURL(id) { return "/genes/"+encodeURIComponent(id)+"?from="+encodeURIComponent(location.search); }
function reportText(s) { return String(s || '').replace(/\[([^\]]+)\]\([^)]+\)/g,'$1').replace(/(\*\*|__|`|\*)/g,''); }
function renderRow(row) {
  if(state.collection==='reactions' || state.collection==='reaction_participants') {
    const id=row.reaction_id || row.id;
    const target=resourceURL('terms',row.go_term_id)+'#'+encodeURIComponent('reaction-'+id);
    const title=row.label || row.chemical_id;
    return `<article class="row"><div class="row-top"><a class="row-title" href="${esc(target)}">${esc(title)}</a><span class="row-tag">${esc(row.rhea_id)}</span></div><div class="row-meta"><span>${esc(row.go_term_id)}</span><span>${esc((row.direction || row.side).replaceAll('_',' '))}</span>${row.side?'<span>Coefficient: '+esc(row.stoichiometry_expression || row.stoichiometry)+'</span>':''}</div>${recordExtras(row)}</article>`;
  }
  if(state.collection==='cell_types' || state.collection==='differentiations') {
    const target=state.collection==='cell_types'?resourceURL('cell_types',row.id):resourceURL('terms',row.go_term_id)+'#'+encodeURIComponent('differentiation-'+row.id);
    return `<article class="row"><div class="row-top"><a class="row-title" href="${esc(target)}">${esc(row.label)}</a><span class="row-tag">${esc(row.go_term_id || row.id)}</span></div><p>${row.target_cell_type_id?'<a href="'+esc(resourceURL('cell_types',row.target_cell_type_id))+'">'+esc(row.target_cell_type_id)+'</a>':esc(row.definition)}</p>${recordExtras(row)}</article>`;
  }
  if(state.collection==='chemicals') {
    const target=resourceURL('chemicals',row.id);
    return `<article class="row"><div class="row-top"><a class="row-title" href="${esc(target)}">${esc(row.label)}</a><span class="row-tag">${esc(row.id)}</span></div><p>${esc(row.formula)}</p><p class="fine">Explore reactions and models →</p>${recordExtras(row)}</article>`;
  }
  if(state.collection==='projects') return `<article class="row"><div class="row-top"><a class="row-title" href="${esc(resourceURL('projects',row.id))}">${esc(reportText(row.label))}</a><span class="row-tag">${esc(row.maturity.replaceAll('_',' '))}</span></div><p class="row-description">${esc(reportText(row.description))}${row.description.length>=400?'…':''}</p><div class="row-meta"><span>${row.tags.map(x=>esc(x.replaceAll('_',' '))).join(' · ')}</span><span>${row.project_species.map(pretty).map(esc).join(' · ')}</span><span>${number(row.gene_ids.length)} referenced genes</span><span>${number(row.report_ids.length)} reports</span></div>${recordExtras(row)}</article>`;
  if(state.collection==='rules') return `<article class="row"><div class="row-top"><a class="row-title" href="${esc(resourceURL('rules',row.id))}">${esc(row.label)}</a><span class="row-tag">${esc(row.kind)}</span></div><p class="row-description">${esc(row.description)}${row.description.length>=600?'…':''}</p><div class="row-meta"><span>${esc(row.rule_source)}</span><span>${esc([row.predicate_modifier,row.predicate_id].filter(Boolean).join(' '))}</span><span>${esc(row.review_action || row.review_status || 'Source assertion')}</span><span>${esc(row.mapping_scope)}</span></div>${recordExtras(row)}</article>`;
  if(state.collection==='reports') return `<article class="row"><div class="row-top"><a class="row-title" href="${esc(resourceURL('reports',row.id))}">${esc(reportText(row.label))}</a><span class="row-tag">${esc(pretty(row.provider))}</span></div><p class="row-description">${esc(reportText(row.description))}${row.description.length>=400?'…':''}</p><div class="row-meta"><span>${esc(row.kind)}</span><span>${esc(row.subject_label)}${row.organism?' · '+esc(row.organism):''}</span>${row.created_at?'<span>'+esc(row.created_at.slice(0,10))+'</span>':''}<span>${number(row.word_count)} words</span>${row.artifact_count?'<span>'+number(row.artifact_count)+' artifacts</span>':''}</div>${recordExtras(row)}</article>`;
  if(state.collection==='references') return `<article class="row"><div class="row-top"><a class="row-title" href="${esc(resourceURL('references',row.id))}">${esc(row.label)}</a><span class="row-tag">${esc(row.kind)}${row.year?' · '+esc(row.year):''}</span></div>${row.description?`<p class="row-description">${esc(row.description)}</p>`:''}<div class="row-meta"><span class="mono">${esc(row.id)}</span><span>${number(row.annotation_count)} annotations</span><span>${number(row.gene_count)} genes</span>${row.has_review?`<span class="review-badge">Cited in ${number(row.review_count)} gene review${row.review_count===1?'':'s'}</span>`:''}</div>${recordExtras(row)}</article>`;
  if(state.collection==='genes') return `<article class="row"><div class="row-top"><a class="row-title" href="${esc(geneURL(row.id))}">${esc(row.symbol)}</a><span class="row-tag">${esc(pretty(row.species))}${row.gene_product_types?.length?' · '+row.gene_product_types.map(esc).join(' · '):row.record_kind!=='gene'?' · '+esc(pretty(row.record_kind)):''}</span></div><p class="row-description">${esc(row.name)}</p><div class="row-meta"><span class="mono">${esc(row.id)}</span><span>${number(row.annotation_count)} annotations</span>${row.has_review?'<span class="review-badge">Review available</span>':''}</div>${recordExtras(row)}</article>`;
  if(state.collection==='annotations') return `<article class="row"><div class="row-top"><a class="row-title" href="${esc(geneURL(row.gene_id))}">${esc(row.gene_symbol)}</a><span class="row-tag">${esc(pretty(row.species))} · ${esc(row.evidence_code)}${row.method?` · <span class="annotation-method">${esc(row.method)}</span>`:''}</span></div><p class="row-description">${row.negation?'<span class="negative">NOT</span> ':''}${esc(row.relation.replaceAll('_',' '))} <a class="row-title annotation-term" href="${esc(resourceURL('terms',row.ontology_class_id))}">${esc(row.term_label)}</a></p><div class="row-meta"><span class="mono">${esc(row.ontology_class_id)}</span><span>${esc(row.assigned_by)}</span><span>${referenceLinks(row.references)}</span>${row.is_redundant?'<span class="redundant-badge" title="Subsumed by a more specific term on this gene">Redundant</span>':''}${row.has_annotation_review?'<span class="review-badge">Matching assessment available</span>':row.has_review?'<span class="review-badge">Gene review available</span>':''}<button class="text-button" data-record="${esc(row.id)}">Evidence &amp; source</button></div>${annotationExtras(row)}</article>`;
  if(['gocams','modules','families'].includes(state.collection)) return `<article class="row"><div class="row-top"><a class="row-title" href="${esc(resourceURL(state.collection,row.id))}">${esc(row.label)}</a><span class="row-tag">${esc(row.kind)} · ${esc(row.status)}</span></div><p class="row-description">${esc(row.description.length>320?row.description.slice(0,320)+'…':row.description)}</p><div class="row-meta"><span class="mono">${esc(row.id)}</span><span>${state.collection==='gocams'?number(row.activity_count)+' activities':state.collection==='modules'?number(row.node_count)+' module nodes':number(row.member_count)+' cached members'}</span><span>${number(row.gene_ids.length)} indexed genes</span>${row.has_review?'<span class="review-badge">Review available</span>':''}</div>${recordExtras(row)}</article>`;
  return `<article class="row"><div class="row-top"><a class="row-title annotation-term" href="${esc(resourceURL('terms',row.id))}">${esc(row.label)}</a><span class="row-tag">${esc(pretty(row.aspect))}</span></div><p class="row-description">${esc(row.definition.length>240?row.definition.slice(0,240)+'…':row.definition)}</p><div class="row-meta"><span class="mono">${esc(row.id)}</span>${row.obsolete?'<span class="negative">Obsolete</span>':''}</div>${recordExtras(row)}</article>`;
}
async function showRecord(collection,id) {
  if(collection!=='annotations') { location.href=resourceURL(collection,id); return; }
  $('#detail-type').textContent=collection==='terms'?'ONTOLOGY TERM':'ANNOTATION & EVIDENCE';
  $('#detail-content').textContent='Loading…'; $('#detail').showModal();
  try {
    const {record:r}=await api('/api/records/'+collection+'/'+encodeURIComponent(id));
    let html='';
    const pair=(k,v)=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`;

      html=`<h2>${esc(r.gene_symbol)}</h2><p>${r.negation?'NOT ':''}${esc(r.relation.replaceAll('_',' '))} <strong>${esc(r.term_label)}</strong></p><dl>${pair('Annotated subject',r.db_object_id)}${pair('Gene grouping',r.gene_id)}${pair('GO term',r.ontology_class_id)}${pair('Evidence',r.evidence_code+' · '+r.evidence_type.join(', '))}${r.method?pair('Method',r.method):''}<dt>References</dt><dd>${referenceLinks(r.references)}</dd>${pair('With / from',r.with_or_from || '—')}${pair('Extensions',r.annotation_extensions || '—')}${pair('Assigned by',r.assigned_by)}${pair('Annotation date',r.annotation_date)}${pair('Mapping status',r.mapping_status)}</dl><h3>Source provenance</h3><p class="fine">${esc(r.source_id)} · line ${number(r.source_line)} · build ${esc(catalog.build_id)}</p><button class="primary" id="show-source">Inspect original record</button><div id="source-record"></div>`;
    $('#detail-content').innerHTML=html;
    $('#show-source').before(AmigoChat.annotationButton(r,catalog.build_id));
    if($('#show-source')) $('#show-source').onclick=async()=>{
      try { const p=await api('/api/provenance/'+encodeURIComponent(r.id));
        $('#source-record').innerHTML=`<p class="fine"><a href="${esc(p.source.url)}" target="_blank" rel="noopener">Upstream file ↗</a><br>SHA-256: ${esc(p.source.sha256)}</p><pre>${esc(p.raw_record)}</pre>`;
      } catch(e) { $('#source-record').textContent=e.message; }
    };
  } catch(e) {$('#detail-content').textContent=e.message;}
}
$('#close-detail').onclick=()=>$('#detail').close();
$('#detail').onclick=e=>{if(e.target===$('#detail')) $('#detail').close();};
$('#search-form').onsubmit=e=>{e.preventDefault();state.search=$('#search').value.trim();cursors=[null];load();};
$('#clear').onclick=()=>{state.filters=[];state.search='';state.includeObsolete=false;cursors=[null];load();};
$('#result-sort').onchange=()=>{state.sort=$('#result-sort').value;cursors=[null];load();};
window.addEventListener('popstate',()=>{
  state=stateFromURL();
  if(!catalog.collections[state.collection]) state.collection='genes';
  cursors=[null];load(false);
});
$('#next').onclick=()=>{if(nextCursor){cursors.push(nextCursor);load();}};
$('#previous').onclick=()=>{cursors.pop();load();};
$('#copy-query').onclick=async()=>{await navigator.clipboard.writeText(JSON.stringify(currentQuery,null,2));$('#copy-query').textContent='Copied';setTimeout(()=>$('#copy-query').textContent='Copy query',1500);};

document.querySelectorAll('[data-example]').forEach(b=>b.onclick=()=>{state.search=b.dataset.example;state.filters=[];selectCollection('genes');});
(async()=>{
  try {
    catalog=await api('/api/collections');
    if(!catalog.collections[state.collection]) state.collection='genes';
    $('.tabs').innerHTML=Object.entries(catalog.collections).map(([name,config])=>`<button role="tab" data-collection="${esc(name)}" aria-selected="false">${esc(config.label)} <span>${number(config.count)}</span></button>`).join('');
    document.querySelectorAll('[data-collection]').forEach(b=>b.onclick=()=>selectCollection(b.dataset.collection));
    $('#build').textContent='Snapshot '+catalog.build_id+' · '+catalog.backend;
    const manifest=await api('/api/manifest');
    $('#species-scope').textContent=AmigoSpecies.loaded(manifest).map(AmigoSpecies.label).join(' · ');
    await load();
  } catch(e) {$('#status').textContent='Unable to connect';$('#error').hidden=false;$('#error').textContent=e.message;}
})();
