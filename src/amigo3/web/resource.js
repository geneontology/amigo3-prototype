const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = n => Number(n).toLocaleString();
const humanize = s => String(s || '').replaceAll('_',' ');
const titles = {cell_types:'Cell type',chemicals:'Chemical',terms:'Ontology term',gocams:'GO-CAM',modules:'Module',families:'PANTHER family',rules:'Rule / mapping'};
const collection = location.pathname.split('/')[1];
let data, record, labels={}, geneLinks={}, loadedSpecies=[], relatedTabs=[], ontologyViewer;
async function api(path,body) {
  const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  const result=await response.json();
  if(!response.ok) throw new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));
  return result;
}
function url(type,id) { return '/'+type+'/'+encodeURIComponent(id); }
function browse(type,field,value) {
  return '/?'+new URLSearchParams({collection:type,filters:JSON.stringify([{id:'context',field,values:[value]}])});
}
function ref(id,label) {
  if(!id) return '—';
  id=String(id);label=label || labels[id] || id;
  const text=esc(label);
  if(id.startsWith('RULE:')) return `<a href="${url('rules',id)}">${text}</a>`;
  if(id.startsWith('PROJECT:')) return `<a href="${url('projects',id)}">${text}</a>`;
  if(id.startsWith('REPORT:')) return `<a href="${url('reports',id)}">${text}</a>`;
  if(id.startsWith('GO:')) return `<a href="${url('terms',id)}">${text}</a>${label!==id?' <small class="mono muted">'+esc(id)+'</small>':''}`;
  if(id.startsWith('MODULE:')) return `<a href="${url('modules',id)}">${text}</a>`;
  if(id.startsWith('gomodel:')) {
    const [model,activity]=id.split('/');
    const kind=data?.documents?.model?.molecules?.some(m=>m.id===id)?'molecule':'activity';
    return `<a href="${url('gocams',model)}${activity?'#'+kind+'-'+encodeURIComponent(id):''}">${text}</a>`;
  }
  if(id.startsWith('PANTHER:PTHR')) {
    const parent=id.split(':').slice(0,2).join(':');
    return `<a href="${url('families',parent)}${id!==parent?'#subfamilies':''}">${text}</a>`;
  }
  if(geneLinks[id]?.length) return geneLinks[id].map(g=>`<a href="${url('genes',g.id)}">${text} <small>(${esc(g.symbol)})</small></a>`).join(' / ');
  if(id.startsWith('UniProtKB:')) return `<a href="https://www.uniprot.org/uniprotkb/${encodeURIComponent(id.slice(10))}" target="_blank" rel="noopener">${text} ↗</a>`;
  if(/^(RO|BFO):/.test(id)) return `<a href="https://purl.obolibrary.org/obo/${encodeURIComponent(id.replace(':','_'))}" target="_blank" rel="noopener">${text} ↗</a>`;
  if(id.startsWith('InterPro:')) return `<a href="https://www.ebi.ac.uk/interpro/entry/InterPro/${encodeURIComponent(id.split(':')[1])}" target="_blank" rel="noopener">${text} ↗</a>`;
  if(id.startsWith('Pfam:')) return `<a href="https://www.ebi.ac.uk/interpro/entry/pfam/${encodeURIComponent(id.split(':')[1])}" target="_blank" rel="noopener">${text} ↗</a>`;
  if(id.startsWith('RHEA:')) return `<a href="https://www.rhea-db.org/rhea/${encodeURIComponent(id.split(':')[1])}" target="_blank" rel="noopener">${text} ↗</a>`;
  if(id.startsWith('CL:')) return `<a href="${url('cell_types',id)}">${text}</a>`;
  if(/^(CHEBI|GENERIC|POLYMER):/.test(id)) return `<a href="${url('chemicals',id)}">${text}</a>`;
  if(id.startsWith('PMID:')) return `<a href="https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(id.slice(5))}/" target="_blank" rel="noopener">${text} ↗</a>`;
  return `<span>${text}</span>`;
}
function tree(value) {
  if(value===null || value===undefined || value==='') return '<span class="muted">—</span>';
  if(Array.isArray(value)) return value.length?'<ul class="structured-list">'+value.map(v=>'<li>'+tree(v)+'</li>').join('')+'</ul>':'<span class="muted">None recorded</span>';
  if(typeof value==='object' && value.id && Object.keys(value).every(k=>['id','label'].includes(k))) return ref(value.id,value.label);
  if(typeof value==='object' && value.term?.id && (value.preferred_term || value.description)) {
    const {preferred_term,term,description,...rest}=value;
    return `<div class="descriptor">${preferred_term?'<strong>'+esc(preferred_term)+'</strong>':''}<div>${ref(term.id,term.label)}</div>${description?'<p>'+esc(description)+'</p>':''}${Object.keys(rest).length?tree(rest):''}</div>`;
  }
  if(typeof value==='object') return '<dl class="structured">'+Object.entries(value).map(([k,v])=>`<dt>${esc(humanize(k))}</dt><dd>${k==='supporting_text'?'<blockquote>'+esc(v)+'</blockquote>':k==='template'&&typeof v==='string'?ref('MODULE:'+v.replace(/^MODULE:|^modules\//,'').replace(/\.yaml$/,'').split('#')[0],v):tree(v)}</dd>`).join('')+'</dl>';
  if(typeof value==='string' && /^(CL:|CHEBI:|GENERIC:|POLYMER:|GO:|PANTHER:PTHR|MODULE:|gomodel:|UniProtKB:|PMID:)[^\s]+$/.test(value)) return ref(value);
  return `<span class="preserve-lines">${esc(value)}</span>`;
}
function table(headers,rows) { return rows.length?`<div class="table-scroll"><table class="record-table"><thead><tr>${headers.map(x=>'<th scope="col">'+esc(x)+'</th>').join('')}</tr></thead><tbody>${rows.map(r=>'<tr>'+r.map(x=>'<td>'+x+'</td>').join('')+'</tr>').join('')}</tbody></table></div>`:'<p class="section-note">None recorded in this snapshot.</p>'; }
function section(id,title,html) { return `<section class="gene-section" id="${id}"><h2>${esc(title)}</h2>${html}</section>`; }
// Keep section IDs as permanent deep links, even when several sections share a tab.
function resourceTabs() {
  const common=[...relatedTabs,...(collection==='terms'?[['metadata','Metadata']]:[]),['sources','Sources',['terms','chemicals','cell_types'].includes(collection)?1:(data.sources || []).length,['sources','structure-source','mapping-fields','rule-record','mapping-set']]];
  const module=data.documents?.module, family=data.documents;
  const subfamilies=document.querySelectorAll('#subfamilies tbody tr').length;
  const groups={
    cell_types:[['overview','Overview'],['differentiation','Differentiation',data.differentiations?.exact?.total],['ontology','Ontology',data.parents?.length+data.children?.length]],
    chemicals:[['overview','Overview',undefined,['overview','index-status']],['reactions','Reactions',data.reactions?.total]],
    terms:[['overview','Overview'],['ontology','Ontology',data.parents?.length+data.children?.length],['annotations','Annotations',data.counts?.annotation_count]],
    gocams:[['graph','Pathway'],['activities','Activities',record.activity_count,['activities','connections','molecules']],['review','Review',data.documents?.review?.activity_reviews?.length,undefined,!data.documents?.review],['neighbors','Neighboring models'],['overview','Overview']],
    modules:[['structure','Structure',record.node_count],['satisfiability','Species satisfiability'],['overview','Overview'],['evidence','Evidence',undefined,undefined,![module?.evidence,module?.references,module?.knowledge_gaps].some(value=>value && (typeof value!=='object' || Object.keys(value).length))]],
    families:[['members','Members',record.member_count],['subfamilies','Subfamilies',subfamilies,undefined,!subfamilies],['assessments','Review',undefined,undefined,!family?.review],['paint','Ancestral annotations',family?.paint?.length,undefined,!family?.paint?.length],['overview','Overview']],
    rules:[['mapping','Mapping',undefined,['mapping','interpretation']],['conditions','Conditions & conclusions',undefined,['conditions','conclusions']],['review','Review'],['overview','Overview'],['terms','GO terms',record.term_ids?.length]],
  }[collection];
  const tabs=[];
  for(const [id,title,count,ids=[id],omit=false] of [...groups,...common]) {
    const contents=ids.map(id=>document.getElementById(id)).filter(Boolean);
    if(omit) {contents.forEach(element=>element.remove());continue;}
    if(!contents.length) continue;
    const panel=document.createElement('div');
    panel.id='panel-'+id;panel.hidden=true;
    $('#resource-body').append(panel);panel.append(...contents);
    tabs.push({id,label:title,count,panel});
  }
  return tabs;
}
function disclosures(value,label='Evidence & complete record') { return `<details class="record-details"><summary>${esc(label)}</summary>${tree(value)}</details>`; }
function inlineTermValues(values,links=false) {
  if(!values?.length)return '<span class="muted">None recorded</span>';
  return '<ul class="term-values">'+values.map(value=>`<li>${links?ref(value):esc(value)}</li>`).join('')+'</ul>';
}
function mappingPredicate(mapping) {
  const id=mapping.predicate_id || '';
  const labels={'skos:exactMatch':'Exact match','skos:closeMatch':'Close match','skos:broadMatch':'Broad match','skos:narrowMatch':'Narrow match','skos:relatedMatch':'Related match'};
  const key=id.replace('http://www.w3.org/2004/02/skos/core#','skos:');
  const label=labels[key] || mapping.predicate_label;
  const modifier=mapping.predicate_modifier;
  return `<div class="mapping-predicate">${modifier?`<strong class="mapping-modifier">${esc(modifier.toLowerCase()==='not'?'NOT':modifier)}</strong> `:''}${label?`<strong>${esc(label)}</strong>`:''}<code>${esc(id || 'Not recorded')}</code></div>`;
}
function mappingEndpoint(mapping,side) {
  const id=mapping[side+'_id'],label=mapping[side+'_label'];
  if(id==='sssom:NoTermFound')return '<strong>No term found</strong>';
  if(!id)return esc(label || 'Not recorded');
  return ref(id,label)+(label && label!==id && !id.startsWith('GO:')?`<small class="mono mapping-id">${esc(id)}</small>`:'');
}
function relatedRules(items) {
  const mappings=items.filter(item=>item.kind==='SSSOM mapping');
  const rules=items.filter(item=>item.kind!=='SSSOM mapping');
  let html='';
  if(mappings.length)html+='<h3>SSSOM mappings</h3><div class="mapping-table">'+table(['Subject','Predicate','Object','Record'],mappings.map(item=>[
    mappingEndpoint(item,'subject'),mappingPredicate(item),mappingEndpoint(item,'object'),
    `<a href="${url('rules',item.id)}">Mapping details →</a><small class="mapping-id">${esc(item.rule_source || '')}</small>`
  ]))+'</div>';
  if(rules.length)html+='<h3>Rules & other mappings</h3><ul>'+rules.map(item=>`<li>${ref(item.id,item.label)} <small class="muted">${esc(item.kind)}</small>${item.predicate_id?mappingPredicate(item):''}</li>`).join('')+'</ul>';
  return html;
}
function related() {
  const names={gocams:'GO-CAMs',modules:'Modules',families:'Families',reports:'Reports',projects:'Projects',rules:'Rules & mappings'};
  return [
    ['related-pathways','Pathways',['gocams','modules']],
    ['related-evolution','Evolution',['families']],
    ['related-reports','Reports',['reports']],
    ['related-projects','Projects',['projects']],
    ['related-rules','Rules & mappings',['rules']]
  ].map(([id,label,collections])=>{
    const groups=collections.filter(type=>data.related?.[type]?.total);
    if(!groups.length)return '';
    relatedTabs.push([id,label,groups.reduce((total,type)=>total+data.related[type].total,0)]);
    return section(id,label,groups.map(type=>{
      const group=data.related[type];
      return `${type==='rules'?relatedRules(group.items):`<h3>${esc(names[type])} (${number(group.total)})</h3><ul>${group.items.map(item=>`<li>${ref(item.id,item.label)}</li>`).join('')}</ul>`}${group.total>group.items.length?`<a href="${esc(browse(type,group.field,group.value))}">Browse all ${number(group.total)} →</a>`:''}`;
    }).join(''));
  }).join('');
}
function sources() {
  return section('sources','Source snapshots',(data.sources || []).map(s=>`<article class="review-source"><h3>${esc(humanize(s.role))}</h3><p>${esc(s.path)}</p><p class="checksum">SHA-256: ${esc(s.sha256)}</p><a href="/api/snapshots/${encodeURIComponent(s.sha256)}" target="_blank" rel="noopener">Open original source ↗</a></article>`).join(''));
}
function structureFigure(chemical, coefficient='') {
  const structure=chemical.structure;
  return `<figure class="chemical-figure">${coefficient?'<span class="structure-coefficient">'+esc(coefficient)+'</span>':''}${structure?`<button class="structure-expand" data-structure="${esc(structure.url)}" data-label="${esc(chemical.label)}" aria-label="Expand structure of ${esc(chemical.label)}"><img src="${esc(structure.url)}" alt="2D structure of ${esc(chemical.label)}" loading="lazy"></button>`:'<div class="structure-unavailable">No structure in this snapshot</div>'}<figcaption>${esc(chemical.label)}<small>${esc(chemical.id)}</small></figcaption></figure>`;
}
function structureDialog() {
  const dialog=document.createElement('dialog');dialog.className='structure-dialog';
  dialog.innerHTML='<form method="dialog"><button aria-label="Close structure">Close ×</button></form><h2></h2><div class="structure-full"><img alt=""></div><a target="_blank" rel="noopener">Open SVG ↗</a>';
  document.body.append(dialog);
  document.querySelectorAll('[data-structure]').forEach(button=>button.onclick=()=>{
    const img=dialog.querySelector('img');img.src=button.dataset.structure;img.alt='2D structure of '+button.dataset.label;
    dialog.querySelector('h2').textContent=button.dataset.label;dialog.querySelector('a').href=button.dataset.structure;dialog.showModal();
  });
  dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close();});
}
function differentiationCards() {
  if(!data.differentiations?.length)return '';
  return '<div class="term-differentiation"><h2>Cell differentiation</h2>'+data.differentiations.map(d=>{
    const source=data.differentiation_sources?.[d.source_id];
    return `<article class="differentiation-card" id="differentiation-${esc(d.id)}"><span class="capability-label">Results in acquisition of features of</span><h3>${ref(d.target_cell_type.id,d.target_cell_type.label)}</h3><p class="mono">${esc(d.target_cell_type.id)}</p><details class="reaction-provenance"><summary>Ontology source</summary><p>${source?'<a href="'+esc(source.url)+'">GO-plus snapshot</a>':esc(d.source_id)} · ${esc(d.predicate)}</p><p class="checksum">SHA-256: ${esc(d.source_sha256)}</p><p>${d.axiom_kinds.map(humanize).map(esc).join(' · ')}</p></details></article>`;
  }).join('')+'</div>';
}
function cellTypePage() {
  let html=section('overview','Cell type',`<p>${record.obsolete?'Obsolete cell type. ':''}${esc(record.definition)}</p>`+table(['Property','Value'],[['Identifier',esc(record.id)],['Synonyms',inlineTermValues(record.synonyms)]]));
  html+=section('differentiation','Differentiation processes',['exact','subtypes'].map(key=>{
    const g=data.differentiations[key];
    return `<h3>${key==='exact'?'This exact cell type':'This cell type and its subtypes'} (${number(g.total)})</h3>`+table(['GO process','Target cell'],g.items.map(d=>[ref(d.go_term_id,d.label),ref(d.target_cell_type_id)]))+`<p><a href="${esc(browse('differentiations',g.field,g.value))}">Browse ${key==='exact'?'exact targets':'targets including subtypes'} →</a></p>`;
  }).join('')+'<p class="section-note">These links describe differentiation targets. They do not assert precursor lineage or expression in these cells.</p>');
  const list=rows=>rows.length?'<ul>'+rows.map(r=>'<li>'+ref(r.id,r.label)+'</li>').join('')+'</ul>':'<p class="section-note">None recorded.</p>';
  html+=section('ontology','Cell type hierarchy','<div class="neighborhood"><div><h3>Parent types</h3>'+list(data.parents)+'</div><div><h3>Subtypes</h3>'+list(data.children)+'</div></div>');
  html+=section('sources','Cell Ontology source',`<p>${data.source?'<a href="'+esc(data.source.url)+'">Cell Ontology snapshot</a>':esc(record.source_id)}</p><p class="checksum">SHA-256: ${esc(record.source_sha256)}</p><p class="section-note">Cell type labels and hierarchy come from CL. Differentiation descriptions come from the separately pinned GO-plus snapshot.</p>`);
  return html;
}
function reactionCards() {
  if(!data.reactions?.length) return '';
  const directions={unspecified:['=','Direction unspecified'],left_to_right:['→','Left to right'],right_to_left:['←','Right to left'],bidirectional:['⇌','Bidirectional']};
  function participant(p) {
    const coefficient=p.stoichiometry_expression || (p.stoichiometry===1?'':p.stoichiometry);
    const location=p.compartment?` <small class="reaction-location">[${esc(p.compartment.split('/').at(-1))}]</small>`:'';
    return `<span class="reaction-participant">${coefficient?'<strong>'+esc(coefficient)+' </strong>':''}${ref(p.chemical.id,p.chemical.label)}${location}</span>`;
  }
  return '<div class="term-reactions"><h2>Reactions</h2>'+data.reactions.map(r=>{
    const [arrow,direction]=directions[r.direction] || ['?',r.direction];
    const side=name=>r[name].map(participant).join(' <span class="reaction-plus" aria-label="plus">+</span> ');
    const source=data.reaction_sources?.[r.source_id];
    const figures=name=>r[name].map(p=>structureFigure(p.chemical,p.stoichiometry_expression || (p.stoichiometry===1?'':p.stoichiometry))).join('<span class="structure-plus" aria-label="plus">+</span>');
    const structures=r.left.concat(r.right).some(p=>p.chemical.structure)?`<details class="reaction-structures" open><summary>Chemical structures</summary><div class="structure-equation"><div class="structure-side">${figures('left')}</div><span class="reaction-arrow" aria-label="${esc(direction)}">${arrow}</span><div class="structure-side">${figures('right')}</div></div></details>`:'';
    return `<article class="reaction-card" id="reaction-${esc(r.id)}"><div class="reaction-heading"><h3>${ref(r.rhea_id)}</h3><span class="reaction-direction">${esc(direction)}</span></div><div class="reaction-equation" aria-label="Reaction equation"><div class="reaction-side">${side('left')}</div><span class="reaction-arrow" aria-label="${esc(direction)}">${arrow}</span><div class="reaction-side">${side('right')}</div></div>${structures}<p class="fine"><a href="${esc(browse('reactions','go_term_id',record.id))}">Browse this term’s reactions</a> · <a href="/api/reactions/${encodeURIComponent(r.id)}">Structured data</a></p><details class="reaction-provenance"><summary>Reaction source &amp; mappings</summary><p>${source?'<a href="'+esc(source.url)+'">Rhea snapshot</a>':esc(r.source_id)} · <span class="mono">SHA-256 ${esc(r.source_sha256)}</span></p>${r.mappings.map(m=>'<p>'+esc(m.predicate)+' · '+(m.rule_id?ref(m.rule_id):esc(m.source_id))+'</p>').join('')}</details></article>`;
  }).join('')+'</div>';
}
function chemicalPage() {
  const chebi=record.id.startsWith('CHEBI:')?`<a href="https://www.ebi.ac.uk/chebi/searchId.do?chebiId=${encodeURIComponent(record.id)}" target="_blank" rel="noopener">View in ChEBI ↗</a>`:'';
  const metadata=table(['Property','Value'],[['Identifier',esc(record.id)],['Formula',esc(record.formula || 'Not supplied')],['Charge',esc(record.charge || 'Not supplied')],...(record.underlying_chebi_id?[['Underlying ChEBI class',esc(record.underlying_chebi_id)]]:[]),...(record.polymerization_index?[['Polymerization index',esc(record.polymerization_index)]]:[])]);
  let html=section('overview','Chemical information','<div class="chemical-overview">'+structureFigure({...record,structure:data.structure})+'<div>'+metadata+'</div></div>'+`<p>${chebi}</p>`);
  const r=data.reactions;
  const rows=r.items.map(item=>{
    const target=url('terms',item.go_term_id)+'#'+encodeURIComponent('reaction-'+item.id);
    return [`<a href="${esc(target)}">${esc(item.label)}</a>`,ref(item.go_term_id,item.go_term_label),ref(item.rhea_id),esc(item.on_left && item.on_right?'Both sides':item.on_left?'Left':'Right'),esc(humanize(item.direction))];
  });
  html+=section('reactions','Reactions',`<p class="section-note">${number(r.total)} GO-owned descriptions of ${number(r.distinct_rhea_count)} Rhea reactions. Left and right are equation sides; flux direction is recorded separately.</p>`+table(['Reaction','GO term','Rhea','Side','Direction'],rows)+(r.total>r.items.length?`<a href="${esc(browse('reactions','chemical_ids',record.id))}">Browse all ${number(r.total)} descriptions →</a>`:''));
  html+=related();
  if(!data.knowledge_links_available)html+=section('index-status','Model links','<p class="section-note">This snapshot predates chemical links to GO-CAMs and modules. Rebuild the data to enable them.</p>');
  const sourceLink=record.source_id.startsWith('knowledge:')?'/api/snapshots/'+encodeURIComponent(record.source_sha256):data.source?.url;
  html+=section('sources','Chemical source',`<p>${esc(record.source_id)}</p><p class="mono">${esc(record.source_uri)}</p><p class="checksum">SHA-256: ${esc(record.source_sha256)}</p>${sourceLink?'<a href="'+esc(sourceLink)+'" target="_blank" rel="noopener">Open source ↗</a>':''}<p class="section-note">GO-CAM and module links use explicit chemical identifiers in their source records. Sharing a mapped function alone does not establish chemical participation.</p>`);
  if(data.structure)html+=section('structure-source','Structure provenance',`<p>${esc(data.structure.renderer)}</p><p>Source: ${esc(data.structure.source_id)}</p><p class="checksum">Source SHA-256: ${esc(data.structure.source_sha256)}</p><p class="checksum">Molfile SHA-256: ${esc(data.structure.mol_sha256)}</p>`);
  return html;
}
function termPage() {
  let html='<section class="gene-section term-overview" id="overview" aria-label="Ontology overview">'+reactionCards()+differentiationCards()+AmigoOntology.shell(loadedSpecies)+'</section>';
  html+=section('metadata','Term metadata',`<dl class="structured"><dt>Aspect</dt><dd>${esc({F:'Molecular function',P:'Biological process',C:'Cellular component'}[record.aspect] || record.aspect)}</dd><dt>GO subsets / slims</dt><dd>${record.subsets?.length?record.subsets.map(subset=>`<a href="${esc(browse('terms','subsets',subset))}">${esc(subset)}</a>`).join(', '):'None recorded'}</dd><dt>Synonyms</dt><dd>${inlineTermValues(record.synonyms)}</dd><dt>Alternate IDs</dt><dd>${inlineTermValues(record.alt_ids,true)}</dd><dt>Status</dt><dd>${record.obsolete?'Obsolete':'Current'}</dd></dl>`);
  const relations = rows => table(['Relationship','Term'],rows.map(e=>[esc(humanize(e.predicate)),ref(e.id,e.label)]));
  html+=section('ontology','Ontology neighborhood',`<div class="neighborhood"><div><h3>Parents / outgoing relationships</h3>${relations(data.parents)}</div><div><h3>Children / incoming relationships</h3>${relations(data.children)}</div></div><p class="section-note">Relationship types are preserved. Descendant annotation browsing follows is_a and part_of; it does not traverse regulatory relationships.</p>`);
  html+=section('annotations','Annotations',`<p class="section-note">${number(data.counts.direct_annotation_count)} rows use this exact term; ${number(data.counts.annotation_count)} use it or an is_a/part_of descendant, across ${number(data.counts.gene_count)} genes/products. This includes ${number(data.counts.negated_annotation_count)} NOT rows. These are source annotation counts, not inferred assertions.</p><div class="annotation-controls"><select id="term-scope" aria-label="Annotation term scope"><option value="isa_partof_closure">Term and descendants</option><option value="ontology_class_id">Exact term only</option></select><select id="term-species" aria-label="Annotation species"><option value="">All loaded species</option>${loadedSpecies.map(s=>`<option value="${esc(s)}">${esc(AmigoSpecies.label(s))}</option>`).join('')}</select><select id="term-evidence" aria-label="Annotation evidence"><option value="">All evidence</option></select><label><input type="checkbox" id="include-not" checked> Include NOT</label></div><p id="term-annotation-status" role="status"></p><div id="term-annotations"></div><div class="pagination"><button id="term-prev" disabled>← Previous</button><span id="term-page"></span><button id="term-next" disabled>Next →</button></div>`);
  html+=related();
  html+=section('sources','Ontology source','<p>This term and its relationships come from the GO-basic ontology pinned in this build.</p><a href="/api/manifest">View ontology version and source checksum →</a>');
  return html;
}
// GoCamReview (ai-gene-review): per-activity verdicts, QC flags and gene-review
// consistency. Enum wording follows the source schema; findings stay attributed.
const gocamVerdicts={OK:['retain','Well supported and follows GO-CAM best practice'],UNCERTAIN:['reconsider','Defensible but imprecise or under-supported'],WRONG:['reject','Contradicts the evidence or violates a hard best-practice rule']};
const gocamConsistency={CONSISTENT:['badge-core-exact','Matches an accepted core function in the gene review'],MORE_SPECIFIC:['badge-core-close','More specific than the gene review'],MORE_GENERAL:['badge-core-close','More general than the gene review'],RELATED:['badge-core-close','Same general area as the gene review'],CONFLICT:['badge-qc','Asserts a function the gene review removed, negated or marked over-annotated'],NOT_IN_REVIEW:['badge-muted','Function not represented in the gene review (candidate gap)'],NO_GENE_REVIEW:['badge-muted','No gene review exists yet for this gene product']};
const gocamFlags={BINDING_AS_FUNCTION:'Bare binding term used as the molecular function',GENERIC_MF:'Overly generic molecular function',HAS_INPUT_MISUSE:'has input used incorrectly',DIRECT_VS_INDIRECT_CAUSAL:'Direct regulation asserted for an indirect mechanism, or vice versa',INCORRECT_DIRECTIONALITY:'Causal edge direction appears reversed',MISSING_LOCATION:'No occurs in cellular component',MISSING_PROCESS:'Not part of a biological process',ORPHAN_ACTIVITY:'No causal connections to the rest of the model',MISSING_EVIDENCE:'Activity or relationship lacks evidence',COMPLEX_SUBUNIT_REPRESENTATION:'Complex vs active subunit representation'};
// Flags that describe this activity's outgoing causal edges rather than the activity itself.
const causalFlags=['DIRECT_VS_INDIRECT_CAUSAL','INCORRECT_DIRECTIONALITY'];
function activityReviews() { return new Map((data.documents.review?.activity_reviews || []).map(r=>[r.activity_id,r])); }
function verdictBadge(verdict) {
  const [style,title]=gocamVerdicts[verdict] || ['pending','Not yet assessed'];
  return `<span class="action ${style}" title="${esc(title)}">${esc(humanize(verdict || 'PENDING'))}</span>`;
}
function flagBadges(flags) { return (flags || []).map(f=>`<span class="annotation-badge badge-qc" title="${esc(gocamFlags[f] || f)}">${esc(humanize(f))}</span>`).join(''); }
function reviewBadges(r) {
  const [style,title]=gocamConsistency[r.consistency] || ['badge-muted',''];
  return `<div class="term-box-badges">${verdictBadge(r.verdict)}${r.consistency?`<span class="annotation-badge ${style}" title="${esc(title)}">${esc(humanize(r.consistency))}</span>`:''}${flagBadges(r.qc_flags)}</div>`;
}
function reviewLink(id,text='Review finding →') { return `<a class="review-finding-link" href="#activity-review-${encodeURIComponent(id)}">${esc(text)}</a>`; }
function modelReview(model,review) {
  const activities=new Map((model.activities || []).map(a=>[a.id,a]));
  const items=review.activity_reviews || [];
  const tally=Object.keys(gocamVerdicts).map(v=>[v,items.filter(r=>r.verdict===v).length]).filter(([,n])=>n);
  const cards=items.map(r=>{
    const a=activities.get(r.activity_id);
    const name=ref(r.gene_product || a?.enabled_by?.term);
    const context=[['Function',r.molecular_function],['Part of',r.biological_process],['Occurs in',r.cellular_component]].filter(([,v])=>v).map(([k,v])=>`<strong>${k} </strong>${ref(v)}`).join('<br>');
    const extra=Object.fromEntries(['supporting_text','evidence'].filter(k=>r[k]?.length).map(k=>[k,r[k]]));
    return `<article class="activity-card review-finding" id="activity-review-${esc(r.activity_id)}"><div class="activity-heading"><h3>${name}</h3>${a?`<a class="mono" href="#activity-${encodeURIComponent(r.activity_id)}">Activity ${esc(r.activity_id.split('/').at(-1))}</a>`:`<span class="mono muted" title="Not in the cached model">${esc(r.activity_id)}</span>`}</div>${reviewBadges(r)}<div class="activity-context">${context}</div>${r.notes?`<p class="preserve-lines">${esc(r.notes)}</p>`:''}${r.gene_review?`<p class="section-note">Compared with gene review <span class="mono">${esc(r.gene_review.replace(/^file:/,''))}</span></p>`:''}${Object.keys(extra).length?disclosures(extra,'Evidence & supporting text'):''}</article>`;
  }).join('');
  return `<dl class="structured"><dt>Review status</dt><dd>${esc(humanize(review.status || 'not specified'))}</dd><dt>Activity verdicts</dt><dd>${tally.length?tally.map(([v,n])=>verdictBadge(v)+' '+number(n)).join(' &nbsp; '):'None recorded'}</dd></dl><p class="section-note">Repository assessment of this model against GO-CAM best practice and the gene reviews. Findings are reviewer judgments; they do not change the model.</p>${review.summary?`<h3>Assessment</h3><p class="preserve-lines">${esc(review.summary)}</p>`:''}${items.length?'<h3>Activity findings</h3>'+cards:''}${review.notes?`<h3>Notes</h3><p class="preserve-lines">${esc(review.notes)}</p>`:''}${review.references?.length?'<h3>References</h3><ul>'+review.references.map(x=>`<li>${ref(x.id,x.title || x.id)}${x.title?` <small class="mono muted">${esc(x.id)}</small>`:''}</li>`).join('')+'</ul>':''}${disclosures(review,'Complete review record')}`;
}
function modelPage() {
  const model=data.documents.model,review=data.documents.review,findings=activityReviews();
  let html=section('overview','Model overview',`<dl class="structured"><dt>Status</dt><dd>${esc(model.status)}</dd><dt>Taxon</dt><dd>${esc(model.taxon)}</dd><dt>Modified</dt><dd>${esc(model.date_modified)}</dd></dl>${review?'<p><a href="#review">Read the repository review →</a></p>':''}${disclosures({comments:model.comments,provenances:model.provenances},'Model provenance & comments')}`);
  html+=section('graph','Activity connections',AmigoGraph.shell(model));
  if(review) html+=section('review','Model review',modelReview(model,review));
  html+=section('neighbors','Neighboring models','<p>Extend this model through shared activities or matching chemical outputs and inputs.</p><button id="open-neighbors" class="primary-button">Explore neighboring models</button><div id="model-neighborhood" class="model-neighborhood"></div>');
  html+=section('activities','Activities',`<p class="section-note">An enabled_by participant is distinguished from molecules used as inputs or outputs. Links to local gene pages are available for identifiers in the loaded catalogs.</p>`+(model.activities || []).map(a=>{const r=findings.get(a.id);return `<article class="activity-card" id="activity-${esc(a.id)}"><div class="activity-heading"><h3>${ref(a.enabled_by?.term)}</h3><span class="mono">${esc(a.id.split('/').at(-1))}</span></div>${r?`<div class="activity-review">${reviewBadges(r)}${reviewLink(a.id)}</div>`:''}<p>${ref(a.molecular_function?.term)}</p><div class="activity-context">${a.part_of?'<strong>Part of </strong>'+ref(a.part_of.term):''}${a.occurs_in?'<br><strong>Occurs in </strong>'+ref(a.occurs_in.term):''}</div>${disclosures(a)}</article>`;}).join(''));
  // The review schema flags activities, not edges: surface causal flags on that activity's outgoing edges.
  const edgeFlags=id=>{const flags=(findings.get(id)?.qc_flags || []).filter(f=>causalFlags.includes(f));return flags.length?`<div class="term-box-badges">${flagBadges(flags)}${reviewLink(id,'Review →')}</div>`:'';};
  const activityName=id=>{const a=model.activities.find(a=>a.id===id);return a?(labels[a.enabled_by?.term] || a.enabled_by?.term || id):id;};
  html+=section('connections','Causal relationships',table(['Source activity','Predicate','Target activity','Evidence'],model.activities.flatMap(a=>(a.causal_associations || []).map(e=>[`<a href="#activity-${encodeURIComponent(a.id)}">${esc(activityName(a.id))}</a>`,ref(e.predicate)+edgeFlags(a.id),`<a href="#activity-${encodeURIComponent(e.downstream_activity)}">${esc(activityName(e.downstream_activity))}</a>`,disclosures(e)]))));
  html+=section('molecules','Molecules & participant roles',table(['Molecule instance','Entity'],(model.molecules || []).map(m=>[`<span id="molecule-${esc(m.id)}">${esc(m.id)}</span>`,ref(m.term)]))+table(['Activity','Predicate','Molecule'],model.activities.flatMap(a=>(a.molecular_associations || []).map(e=>[esc(activityName(a.id)),ref(e.predicate),ref((model.molecules || []).find(m=>m.id===e.molecule)?.term || e.molecule)]))));
  return html+related()+sources();
}
// The diagram and the tree share path keys (m, m-p0, m-v0-1, ...) so a diagram node can open its tree entry.
function moduleNodeId(path) { return 'module-node-'+path; }
const MODULE_DIAGRAM_DEPTH=3, MODULE_SELECTION={ONE:'one of',ONE_OR_MORE:'one or more of',ZERO_OR_MORE:'any of (optional)'};
// Compact and/or overview: parts left to right in step order, variant sets as bracketed
// "one of" stacks, optional steps dashed. Deeper levels collapse to a count; the tree has the rest.
function moduleDiagramNode(node,path,depth,optional=false,step) {
  const parts=(node.parts || []).map((p,i)=>({...p,i})).sort((a,b)=>((a.order ?? Infinity)-(b.order ?? Infinity)) || a.i-b.i);
  const sets=node.variant_sets || [], inner=parts.length+sets.length;
  const collapsed=inner && depth>=MODULE_DIAGRAM_DEPTH;
  const box=`<button type="button" class="md-node${optional && (!inner || collapsed)?' md-optional':''}" data-module-node="${path}" title="Show in structure tree">${step!==undefined?'<span class="md-step">Step '+esc(step)+(optional?' · optional':'')+'</span>':optional?'<span class="md-step">optional</span>':''}<span class="md-label">${esc(node.label || node.id)}</span>${node.module_type?'<small>'+esc(humanize(node.module_type).toLowerCase())+'</small>':''}${collapsed?'<span class="md-more">+'+inner+' inside</span>':''}</button>`;
  if(!inner || collapsed) return box;
  const items=[...parts.map(p=>({order:p.order,html:moduleDiagramNode(p.node || {},path+'-p'+p.i,depth+1,p.optional,p.order)})),
    ...sets.map((set,si)=>({html:moduleDiagramChoice(set,path+'-v'+si,depth+1)}))];
  const flow=items.map((item,i)=>(i?`<span class="md-join" title="${item.order!==undefined && items[i-1].order!==undefined?'then':'and'}">${item.order!==undefined && items[i-1].order!==undefined?'→':'+'}</span>`:'')+item.html).join('');
  return `<div class="md-cluster${optional?' md-optional':''}">${box}<div class="md-flow">${flow}</div></div>`;
}
function moduleDiagramChoice(set,path,depth) {
  const selection=MODULE_SELECTION[set.selection] || 'one of';
  return `<div class="md-choice${set.selection==='ZERO_OR_MORE'?' md-optional':''}" role="group" aria-label="${esc((set.label || set.id || 'Alternatives')+': '+selection)}"><span class="md-choice-label">${esc(selection)}${set.label?' · '+esc(set.label):''}</span><div class="md-choice-options">${(set.variants || []).map((v,vi)=>moduleDiagramNode(v,path+'-'+vi,depth)).join('<span class="md-or">or</span>')}</div></div>`;
}
function moduleDiagram(root) {
  if(!root?.parts?.length && !root?.variant_sets?.length) return '';
  return `<figure class="module-diagram"><figcaption><strong>Overview</strong><span>→ then</span><span>+ also required</span><span><span class="md-key-choice">[ ]</span> alternatives</span><span><span class="md-key-optional"></span> optional</span><span>Select a box to open it below</span></figcaption><div class="md-scroll">${moduleDiagramNode(root,'m',0)}</div></figure>`;
}
function showModuleNode(path) {
  const target=document.getElementById(moduleNodeId(path));
  if(!target) return;
  for(let el=target;el;el=el.parentElement) if(el.tagName==='DETAILS') el.open=true;
  target.scrollIntoView({block:'start'});
  target.classList.remove('module-node-flash');void target.offsetWidth;target.classList.add('module-node-flash');
  target.querySelector(':scope > summary')?.focus({preventScroll:true});
}
function moduleNode(node,depth=0,role='',path='m') {
  return `<details class="module-node" id="${moduleNodeId(path)}" ${depth<2?'open':''}><summary><strong>${esc(node.label || node.id)}</strong> <span class="muted">${esc(node.module_type || '')}</span>${role?' · '+esc(role):''}</summary><div class="module-node-body"><p class="mono">${esc(node.id)}</p>${node.description?'<p>'+esc(node.description)+'</p>':''}${node.concepts?.length?'<h4>Concepts</h4>'+tree(node.concepts):''}${node.context?disclosures(node.context,'Context'):''}${(node.annotons || []).map(a=>`<article class="annoton-card"><h4>${esc(a.label || a.id)}</h4>${a.role_description?'<p>'+esc(a.role_description)+'</p>':''}${tree({participant:a.participant,function:a.function,processes:a.processes,locations:a.locations})}${disclosures({gocam_associations:a.gocam_associations,evidence:a.evidence,notes:a.notes},'GO-CAM grounding & evidence')}</article>`).join('')}${(node.parts || []).map((p,i)=>moduleNode(p.node,depth+1,`${p.order!==undefined?'Step '+p.order+' · ':''}${p.role || ''}${p.optional?' (optional)':''}`,path+'-p'+i)).join('')}${(node.variant_sets || []).map((v,si)=>`<fieldset class="module-variants"><legend>${esc(v.label || v.id)} · alternatives</legend><p class="section-note">Axis: ${esc(v.axis || 'not specified')} · selection: ${esc(v.selection || 'not specified')}</p>${(v.variants || []).map((n,vi)=>moduleNode(n,depth+1,'',path+'-v'+si+'-'+vi)).join('')}${v.notes?'<p>'+esc(v.notes)+'</p>':''}</fieldset>`).join('')}${node.connections?.length?'<h4>Connections</h4>'+tree(node.connections):''}${node.gocam_associations?.length?'<h4>GO-CAM grounding</h4>'+tree(node.gocam_associations):''}${node.conforms_to?.length?'<h4>Template conformance</h4>'+tree(node.conforms_to):''}${node.notes?'<p class="section-note">'+esc(node.notes)+'</p>':''}${node.evidence?.length?disclosures(node.evidence,'Node evidence'):''}</div></details>`;
}
function modulePage() {
  const doc=data.documents.module;
  return section('overview','Module overview',`<p class="section-note">Status: ${esc(doc.status || 'not specified')} · scope: ${esc(doc.scope || 'not specified')}. Concepts ground the module; representative proteins illustrate roles and are not exhaustive member lists.</p>${doc.notes?disclosures(doc.notes,'Module notes'):''}`)+section('structure','Module structure',moduleDiagram(doc.module)+moduleNode(doc.module))+section('satisfiability','Species satisfiability & gap filling',AmigoSatisfiability.shell())+section('evidence','Evidence & open questions',tree({evidence:doc.evidence,references:doc.references,knowledge_gaps:doc.knowledge_gaps}))+related()+sources();
}
function rulePage() {
  const d=data.documents;
  let html=section('overview','Rule / mapping overview',`<dl class="structured"><dt>Record type</dt><dd>${esc(record.kind)}</dd><dt>Source collection</dt><dd>${esc(record.rule_source)}</dd><dt>Source review status</dt><dd>${esc(record.review_status || 'No structured status recorded')}</dd><dt>Source review action</dt><dd>${esc(record.review_action || '—')}</dd><dt>Propagation scope</dt><dd>${esc(record.mapping_scope || 'Not specified')}</dd></dl><p class="section-note">These are source assertions and assessments. Browsing them does not apply rules to genes or create annotations.</p>`);
  if(d.mapping) {
    const m=d.mapping;
    html+=section('mapping','Mapping assertion',table(['Subject','Relation','Object'],[[ref(m.subject_id,m.subject_label),mappingPredicate(m),m.object_id==='sssom:NoTermFound'?'<strong>No term found</strong>':ref(m.object_id,m.object_label)]]));
    html+=section('interpretation','Source interpretation',`<p class="section-note">Read the mapping-set description alongside the predicate. Some AIGR sets use SKOS predicates as local propagation verdicts. A mapping modifier is not a NOT annotation on a gene.</p>${tree({description:d.mapping_set.mapping_set_description || d.mapping_set.description,comment:m.comment,mapping_justification:m.mapping_justification})}`);
    html+=section('mapping-fields','SSSOM fields',disclosures(d.effective_mapping,'All mapping fields (including inherited SSSOM defaults)')+disclosures(d.mapping_set,'Mapping-set metadata and prefix definitions'));
  }
  if(d.rule) {
    const groups=d.rule.condition_sets || [];
    html+=section('conditions','Conditions',groups.length?`<p>Match <strong>any one</strong> of these groups (OR). Within a group, <strong>all</strong> conditions must hold (AND). Negated conditions remain explicit.</p>${groups.map((group,i)=>`<article class="activity-card"><h3>Group ${esc(group.number ?? i+1)} · AND</h3>${(group.conditions || []).length?table(['Condition','Value','Polarity'],group.conditions.map(c=>[esc(c.condition_type),ref(c.curie || c.value,c.label),c.negated?'<strong>NOT</strong>':'Positive'])):'<p class="section-note">Conditions not captured for this group; no unconditional match is inferred.</p>'}${disclosures(group,'Condition details and overlap evidence')}</article>`).join('<p class="logic-or"><strong>OR</strong></p>')}`:'<p class="section-note">Antecedent not captured in this review snapshot. The source rule may have conditions which were omitted here; this is not an unconditional rule.</p>');
    html+=section('conclusions','Predicted annotations',tree(d.rule.go_annotations || []));
    html+=section('review','Rule review',`<p class="section-note">Assessment reproduced from the repository review, including its evidence and confidence.</p>${tree(d.review)}`);
    html+=section('rule-record','Complete embedded rule',disclosures(d.rule,'Entries, relationships, counts and original rule fields'));
  }
  if(d.curation) {
    const c=d.curation;
    html+=section('conditions','Source subject and context',tree({subject_code:c.subject_code,subject_level:c.subject_level,conditions:c.conditions,excluded_subjects:c.excluded_subjects}));
    html+=section('conclusions','Target and scope',tree({target_term:c.target_term,mapping_scope:c.mapping_scope,curation_status:c.curation_status}));
    html+=section('review','Mapping review',tree(c));
    html+=section('mapping-set','Mapping set',disclosures(d.mapping_set,'Source context and references'));
  }
  if(d.proposal) {
    html+=section('conditions','Family membership',`<p>Membership in ${esc(record.subject_id)} is the proposed premise. Supporting proteins and counterexamples below retain their source roles.</p>${tree(d.entry)}`);
    html+=section('review','Annotation proposal and review',tree(d.proposal));
  }
  if(record.term_ids.length) html+=section('terms','Referenced GO terms',tree(record.term_ids));
  return html+related()+sources();
}

// Optional PANTHER IDs (member/review subfamilies) without the prefix; missing values become ''.
const pantherLocal=id=>String(id ?? '').replace(/^PANTHER:/,'');
function familyPage() {
  const docs=data.documents,meta=docs.metadata?.metadata || {},review=docs.review;
  let html=section('overview','Family overview',`<dl class="structured"><dt>Official family name</dt><dd>${esc(record.label)}</dd><dt>Preferred review name</dt><dd>${esc(review?.preferred_name || '—')}</dd><dt>Review status</dt><dd>${esc(record.status)}</dd><dt>Functional coherence</dt><dd>${esc(review?.functional_coherence || 'Not assessed')}</dd></dl>${review?.coherence_reason?'<p>'+esc(review.coherence_reason)+'</p>':''}<p class="section-note">${number(record.member_count)} members in the cached inventory. ${meta.counters?.proteins != null?'The source metadata reports '+number(record.reported_protein_count)+' proteins across its full catalog.':'The full catalog count is unavailable in this snapshot.'} These counts describe different source scopes; cached members are not a complete family census.</p>${disclosures(meta,'Source metadata')}`);
  const subfamilies=new Map();
  for(const m of docs.entries || []) if(m.subfamily) { const id='PANTHER:'+pantherLocal(m.subfamily);const s=subfamilies.get(id) || {id,label:m.subfamily_name || id,count:0};s.count++;subfamilies.set(id,s); }
  // Review groupings may lack a subfamily_id (e.g. "members without resolved subfamily placement").
  (review?.subfamilies || []).forEach((s,i)=>{const id=s.subfamily_id;subfamilies.set(id || 'review-group-'+i,{...(id && subfamilies.get(id)),...s,...(id && {id})});});
  const placed=[...subfamilies.values()].filter(s=>s.id);
  html+=section('subfamilies','Subfamilies',table(['Subfamily','Cached members','Review'],[...subfamilies.values()].map(s=>[s.id?`<button class="text-button subfamily-link" data-subfamily="${esc(pantherLocal(s.id))}">${esc(s.label || s.id)}</button><br><span class="mono">${esc(s.id)}</span>`:esc(s.label || 'Unnamed grouping'),number(s.count || 0),s.description?'<p>'+esc(s.description)+'</p>'+disclosures(s):'No assessment recorded'])));
  html+=section('members','Cached family members',`<div class="annotation-controls"><input id="member-search" type="search" aria-label="Filter family members" placeholder="Gene, protein or organism…"><select id="member-subfamily" aria-label="Filter by subfamily"><option value="">All subfamilies</option>${placed.map(s=>`<option value="${esc(pantherLocal(s.id))}">${esc(s.id)}</option>`).join('')}</select></div><p id="member-status" role="status"></p><div id="family-members"></div><div class="pagination"><button id="member-prev">← Previous</button><span id="member-page"></span><button id="member-next">Next →</button></div>`);
  html+=section('assessments','Function & ancestral-node assessments',review?tree({term_assessments:review.term_assessments,node_assessments:review.node_assessments,residue_sites:review.residue_sites,notes:review.notes,references:review.references}):'<p>No structured family review in this snapshot.</p>');
  html+=section('paint','PAINT ancestral assertions',`<p class="section-note">These are the cached PAINT rows, including evidence codes, ancestral node IDs, negation and seeds. They do not assert that every family member has every function; no phylogenetic topology is reconstructed from this table.</p>`+table(['Node','GO term','Evidence','NOT','Taxon','Seeds'],(docs.paint || []).map(p=>[esc(p.node),ref(p.go_id),esc(p.evidence),esc(p.negated),esc(p.taxon),esc(p.seeds)])));
  return html+related()+sources();
}
let termCursors=[null],termNext=null,termRequest;
async function loadTermAnnotations(reset=false) {
  if(reset) termCursors=[null];
  const filters=[{id:'term',field:$('#term-scope').value,values:[record.id]}];
  if($('#term-species').value) filters.push({id:'species',field:'species',values:[$('#term-species').value]});
  if($('#term-evidence').value) filters.push({id:'evidence',field:'evidence_code',values:[$('#term-evidence').value]});
  if(!$('#include-not').checked) filters.push({id:'negation',field:'negation',values:[false]});
  const token=Symbol();termRequest=token;
  $('#term-annotation-status').textContent='Loading annotations…';$('#term-next').disabled=true;$('#term-prev').disabled=true;
  try {
    const result=await api('/api/query',{collection:'annotations',view:data.build_id,filters,page:{size:25,cursor:termCursors.at(-1)}});
    if(termRequest!==token) return;
    $('#term-annotation-status').textContent=`${number(result.total)} matching annotations`;
    $('#term-annotations').innerHTML=table(['Gene / product','Annotation','Evidence / reference'],result.hits.map(a=>[`<a href="${url('genes',a.gene_id)}">${esc(a.gene_symbol)}</a><br><small>${esc(a.db_object_id)}</small>`,`${a.negation?'<span class="negative">NOT</span> ':''}${esc(humanize(a.relation))}<br>${ref(a.ontology_class_id,a.term_label)}<details class="source-row" data-source="${esc(a.id)}"><summary>Original annotation &amp; provenance</summary><div></div></details>`,`${esc(a.evidence_code)}${a.method?' · '+esc(a.method):''} · ${esc(a.assigned_by)}<br>${a.references.map(x=>ref(x)).join(' · ')}`]));
    document.querySelectorAll('.source-row').forEach(el=>el.ontoggle=async()=>{if(!el.open || el.dataset.loaded) return;el.dataset.loaded='true';try {const p=await api('/api/provenance/'+encodeURIComponent(el.dataset.source));el.querySelector('div').innerHTML=`<p class="checksum">${esc(p.source.id)} · line ${p.line}<br>SHA-256: ${esc(p.source.sha256)}</p><pre>${esc(p.raw_record)}</pre>`;} catch(e){el.querySelector('div').textContent=e.message;delete el.dataset.loaded;}});
    termNext=result.next_cursor;$('#term-next').disabled=!termNext;$('#term-prev').disabled=termCursors.length===1;$('#term-page').textContent='Page '+termCursors.length;
  } catch(e) {if(termRequest===token) $('#term-annotation-status').textContent=e.message;}
}
let memberPage=0;
function renderMembers(reset=false) {
  if(reset) memberPage=0;
  const query=$('#member-search').value.trim().toLowerCase();
  const subfamily=$('#member-subfamily').value;
  const rows=(data.documents.entries || []).filter(m=>(!subfamily || pantherLocal(m.subfamily)===subfamily) && Object.values(m).join(' ').toLowerCase().includes(query));
  const page=rows.slice(memberPage*50,(memberPage+1)*50);
  $('#member-status').textContent=`${number(rows.length)} cached members${query || subfamily?' matching filter':''}`;
  $('#family-members').innerHTML=table(['Protein','Gene','Organism','Subfamily'],page.map(m=>[ref(m.id.includes(':')?m.id:'UniProtKB:'+m.id,m.name || m.id),esc(m.gene || '—'),esc(m.source_tax_name || m.source_tax_id),esc(m.subfamily || '—')]));
  $('#member-prev').disabled=memberPage===0;$('#member-next').disabled=(memberPage+1)*50>=rows.length;$('#member-page').textContent='Page '+(memberPage+1);
}
function resourceDescription() {
  if(['terms','cell_types'].includes(collection))return record.definition;
  if(collection==='modules')return data.documents.module.description;
  if(collection==='gocams')return data.documents.review?.summary || data.documents.review?.description || record.description;
  if(collection==='families')return data.documents.review?.summary || record.description;
  return record.description;
}
// Hero chips for the biological processes a GO-CAM's activities are part of.
const PROCESS_CHIP_LIMIT=6;
function renderProcesses(el,processes) {
  if(!processes?.length) return;
  const chip=p=>`<a class="process-chip" href="${url('terms',p.id)}" title="${esc(p.id)} · ${number(p.activity_count)} ${p.activity_count===1?'activity':'activities'}">${esc(p.label)}</a>`;
  const extra=processes.length-PROCESS_CHIP_LIMIT;
  el.innerHTML=`<span class="process-chips-label">Biological process${processes.length===1?'':'es'}</span>${processes.slice(0,PROCESS_CHIP_LIMIT).map(chip).join('')}${extra>0?`<span class="process-chips-more" hidden>${processes.slice(PROCESS_CHIP_LIMIT).map(chip).join('')}</span><button type="button" class="text-button process-chips-toggle" aria-expanded="false">+${number(extra)} more</button>`:''}`;
  const toggle=el.querySelector('.process-chips-toggle');
  if(toggle) toggle.onclick=()=>{
    const more=el.querySelector('.process-chips-more');more.hidden=!more.hidden;
    toggle.setAttribute('aria-expanded',String(!more.hidden));toggle.textContent=more.hidden?`+${number(extra)} more`:'Show fewer';
  };
  el.hidden=false;
}
(async()=>{
  const from=new URLSearchParams(location.search).get('from');$('#back-results').href=from?.startsWith('?')?'/'+from:'/?collection='+collection;
  try {
    let id=decodeURIComponent(location.pathname.split('/').slice(2).join('/'));
    if(!id.includes(':')) id=({gocams:'gomodel:',modules:'MODULE:',families:'PANTHER:'}[collection] || '')+id;
    data=await api(['terms','chemicals','cell_types'].includes(collection)?'/api/'+collection+'/'+encodeURIComponent(id):'/api/resources/'+collection+'/'+encodeURIComponent(id));
    if(collection==='terms') loadedSpecies=AmigoSpecies.loaded(await api('/api/manifest'));
    record=data.record;labels=data.labels || {};geneLinks=data.gene_links || {};
    $('#resource-kind').textContent=titles[collection];$('#resource-title').textContent=record.label;$('#resource-id').textContent=record.id;
    document.title=record.label+' · AmiGO 3';
    if(collection==='gocams') renderProcesses($('#resource-processes'),data.processes);
    AmigoDetail.description($('#resource-description'),resourceDescription());
    $('#resource-body').innerHTML=({cell_types:cellTypePage,chemicals:chemicalPage,terms:termPage,gocams:modelPage,modules:modulePage,families:familyPage,rules:rulePage}[collection])();
    const specs=resourceTabs();
    structureDialog();
    $('#build').textContent='Snapshot '+data.build_id;$('#resource-loading').hidden=true;$('#resource-content').hidden=false;
    if(collection==='terms') {
      ontologyViewer=AmigoOntology.mount({data,record,api,
        onFiltersChange:state=>{
          if(state.species && ![...$('#term-species').options].some(o=>o.value===state.species))$('#term-species').add(new Option(state.species,state.species));
          $('#term-species').value=state.species;
          if(state.evidence && ![...$('#term-evidence').options].some(o=>o.value===state.evidence))$('#term-evidence').add(new Option(state.evidence,state.evidence));
          $('#term-evidence').value=state.evidence;$('#include-not').checked=state.includeNot;
          loadTermAnnotations(true);
        },
        onEvidence:(values,selected)=>{
          $('#term-evidence').innerHTML='<option value="">All evidence</option>'+values.map(v=>`<option>${esc(v)}</option>`).join('');
          $('#term-evidence').value=selected;
        }
      });
      $('#term-scope').onchange=()=>loadTermAnnotations(true);
      for(const id of ['term-species','term-evidence','include-not']) $('#'+id).onchange=()=>ontologyViewer.setFilters({species:$('#term-species').value,evidence:$('#term-evidence').value,includeNot:$('#include-not').checked});
      $('#term-next').onclick=()=>{if(termNext){termCursors.push(termNext);loadTermAnnotations();}};
      $('#term-prev').onclick=()=>{termCursors.pop();loadTermAnnotations();};
    }
    if(collection==='families') {renderMembers();$('#member-search').oninput=()=>renderMembers(true);$('#member-subfamily').onchange=()=>renderMembers(true);$('#member-prev').onclick=()=>{memberPage--;renderMembers();};$('#member-next').onclick=()=>{memberPage++;renderMembers();};document.querySelectorAll('.subfamily-link').forEach(b=>b.onclick=()=>{$('#member-search').value='';$('#member-subfamily').value=b.dataset.subfamily;renderMembers(true);$('#tab-members').click();});}
    if(collection==='modules') $('#resource-body').addEventListener('click',e=>{const node=e.target.closest('[data-module-node]');if(node)showModuleNode(node.dataset.moduleNode);});
    if(collection==='gocams') {
      const open=$('#open-neighbors');open.onclick=async()=>{open.disabled=true;try {await AmigoNeighborhood.mount($('#model-neighborhood'),record.id);open.hidden=true;}catch(e){open.disabled=false;open.textContent='Retry: '+e.message;}};
    }
    let graphStarted=false;
    AmigoTabs.create({nav:$('#resource-nav'),specs,
      aliases:{related:relatedTabs[0]?.[0] || specs[0].id,...(collection==='terms'?{definition:'metadata'}:{})},
      initial:collection==='gocams' && new URLSearchParams(location.search).has('neighbor')?'panel-neighbors':undefined,
      onSelect:panel=>{
        if(panel==='panel-overview' && ontologyViewer)ontologyViewer.show();
        if(panel==='panel-graph') {
          if(!graphStarted) {graphStarted=true;AmigoGraph.mount({model:data.documents.model,labels,ref,tree});}
          else requestAnimationFrame(()=>$('#pathway-canvas')?._cy?.resize());
        }
        if(panel==='panel-satisfiability') {
          AmigoSatisfiability.mount({el:$('#satisfiability'),record,build:data.build_id,api,ref}).catch(e=>{$('#sat-status').textContent=e.message;});
        }
        if(panel==='panel-neighbors') {
          const open=$('#open-neighbors');
          if(!open.disabled && !open.hidden) open.onclick();
          else requestAnimationFrame(()=>$('#mega-canvas')?._cy?.resize());
        }
      }
    });
  } catch(e) {$('#resource-loading').hidden=true;$('#resource-error').hidden=false;$('#resource-error').textContent=e.message;}
})();
