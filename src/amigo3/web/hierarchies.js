/* Tree presentation adapted from ai-gene-review's annotation browser.
 * Counts and selections use ordinary indexed facet fields on the server. */
const pretty = (v) => ({openscientist:'OpenScientist',falcon:'Falcon / Edison',gene:'Gene',gene_product:'Gene product',complex:'Complex',F:'Molecular function',P:'Biological process',C:'Cellular component',true:'Yes',false:'No'}[String(v)] ?? AmigoSpecies.label(String(v)));
const hierarchyExpanded=new Map(), hierarchyModes=new Map(), hierarchySearch=new Map();
let lastFacets={};

function hierarchyFor(field) { return catalog.collections[state.collection].hierarchies?.[field]; }
function facetValueLabel(field,value) { return hierarchyFor(field)?.nodes[value]?.label ?? pretty(value); }

function renderHierarchy(field,facet,hierarchy) {
  const {nodes}=hierarchy, selected=new Set(state.filters.filter(f=>f.field===field && ['in','eq'].includes(f.op || 'in')).flatMap(f=>f.values));
  const counts=new Map(facet.buckets.map(b=>[b.value,b.count])), children=new Map();
  const mode=hierarchyModes.get(field) || 'tree', query=hierarchySearch.get(field) || '', needle=query.trim().toLowerCase();
  for(const [id,node] of Object.entries(nodes)) {
    for(const parent of node.parents.length?node.parents:['']) {
      if(!children.has(parent)) children.set(parent,[]);
      children.get(parent).push(id);
    }
  }
  for(const ids of children.values()) ids.sort((a,b)=>nodes[a].label.localeCompare(nodes[b].label));
  // Open small species roots or a single GO root; reveal shared-URL selections.
  const reveal=(id,seen=new Set())=>{
    if(seen.has(id)) return;
    seen.add(id);
    for(const parent of nodes[id]?.parents || []) {
      if(!hierarchyExpanded.has(`${field}:${parent}`)) hierarchyExpanded.set(`${field}:${parent}`,true);
      reveal(parent,seen);
    }
  };
  selected.forEach(id=>reveal(id));
  const matches=id=>!needle || `${id} ${nodes[id].label}`.toLowerCase().includes(needle);
  const matching=new Map();
  const subtreeMatches=(id,path=new Set())=>{
    if(path.has(id)) return false;
    if(!matching.has(id)) matching.set(id,matches(id) || (children.get(id) || []).some(child=>subtreeMatches(child,new Set([...path,id]))));
    return matching.get(id);
  };
  const row=(id,path,expandable,expanded)=>{
    const label=nodes[id].label, key=`${field}:${path.join('/')}`;
    return `<div class="hierarchy-row">${expandable?`<button type="button" class="hierarchy-expand" data-tree-key="${esc(key)}:expand" data-hierarchy-expand="${esc(id)}" data-field="${esc(field)}" aria-expanded="${expanded}" aria-label="${expanded?'Collapse':'Expand'} ${esc(label)}">${expanded?'▾':'▸'}</button>`:'<span class="hierarchy-spacer"></span>'}<label title="${esc(id)}"><input type="checkbox" data-tree-key="${esc(key)}:select" data-hierarchy-value="${esc(id)}" data-field="${esc(field)}" aria-label="${esc(hierarchy.label)}: ${esc(label)}" ${selected.has(id)?'checked':''}><span>${esc(label)}</span><small>${number(counts.get(id) || 0)}</small></label></div>`;
  };
  const branch=(id,path=[])=>{
    if(path.includes(id) || !subtreeMatches(id)) return '';
    const next=[...path,id], descendants=children.get(id) || [];
    const openRoot=path.length===0 && (field==='taxon_groups' || (children.get('') || []).length===1);
    const expanded=Boolean(needle) || (hierarchyExpanded.get(`${field}:${id}`) ?? openRoot);
    return `<li>${row(id,next,descendants.length>0,expanded)}${expanded && descendants.length?`<ul>${descendants.map(child=>branch(child,next)).join('')}</ul>`:''}</li>`;
  };
  const body=mode==='tree'?(children.get('') || []).map(id=>branch(id)).join(''):Object.keys(nodes).filter(matches)
    .sort((a,b)=>(counts.get(b)||0)-(counts.get(a)||0) || nodes[a].label.localeCompare(nodes[b].label))
    .map(id=>`<li>${row(id,[id],false,false)}</li>`).join('');
  return `<section class="facet hierarchy" data-hierarchy="${esc(field)}"><h3>${esc(hierarchy.label)}</h3><div class="hierarchy-controls">${[['tree','Hierarchy'],['flat','Most used']].map(([value,label])=>`<button type="button" data-tree-key="${esc(field)}:${value}" data-hierarchy-mode="${value}" data-field="${esc(field)}" aria-pressed="${mode===value}">${label}</button>`).join('')}</div><input class="hierarchy-search" type="search" data-tree-key="${esc(field)}:search" data-field="${esc(field)}" value="${esc(query)}" placeholder="Find a category…" aria-label="Search ${esc(hierarchy.label)} categories"><p class="hierarchy-help">${esc(hierarchy.description)}</p><div class="hierarchy-body">${body?`<ul>${body}</ul>`:'<p class="hierarchy-help">No matching categories</p>'}</div></section>`;
}

function bindHierarchies() {
  document.querySelectorAll('[data-hierarchy-expand]').forEach(button=>button.onclick=()=>{
    hierarchyExpanded.set(`${button.dataset.field}:${button.dataset.hierarchyExpand}`,button.getAttribute('aria-expanded')!=='true');
    renderFacets(lastFacets);
  });
  document.querySelectorAll('[data-hierarchy-mode]').forEach(button=>button.onclick=()=>{
    hierarchyModes.set(button.dataset.field,button.dataset.hierarchyMode);renderFacets(lastFacets);
  });
  document.querySelectorAll('.hierarchy-search').forEach(input=>input.oninput=()=>{
    hierarchySearch.set(input.dataset.field,input.value);renderFacets(lastFacets);
  });
  document.querySelectorAll('[data-hierarchy-value]').forEach(input=>input.onchange=()=>{
    const field=input.dataset.field,value=input.dataset.hierarchyValue;
    const selected=state.filters.filter(f=>f.field===field && ['in','eq'].includes(f.op || 'in')).flatMap(f=>f.values);
    filterBy(field,input.checked?[...new Set([...selected,value])]:selected.filter(v=>v!==value));
  });
}

function renderFilterChips(onRemove) {
  if(!$('#chips')) return;
  const config=catalog.collections[state.collection];
  $('#chips').innerHTML=state.filters.map((f,i)=>`<button class="chip" data-remove="${i}">${esc(config.fields[f.field]?.label || f.field)}: ${f.op==='exists'?'present':f.op==='between'?esc(f.values[0]===f.values[1]?number(f.values[0]):number(f.values[0])+'–'+number(f.values[1])):(f.values || []).map(v=>facetValueLabel(f.field,v)).map(esc).join(', ')} ×</button>`).join('');
  document.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{const [removed]=state.filters.splice(Number(b.dataset.remove),1);if(removed.field==='obsolete') state.includeObsolete=true;onRemove();});
}

function renderFacets(facets, onMoreFacets) {
  lastFacets=facets;
  const focused=document.activeElement,focusKey=focused?.dataset.treeKey,selection=focused?.selectionStart;
  const scrolls=new Map([...document.querySelectorAll('[data-hierarchy]')].map(section=>[section.dataset.hierarchy,section.querySelector('.hierarchy-body').scrollTop]));
  const facetHTML=([key,f])=>{
    const hierarchy=hierarchyFor(f.field);
    if(hierarchy) return renderHierarchy(f.field,f,hierarchy);
    const selected=state.filters.find(x=>x.field===f.field)?.values || [];
    const buckets=[...f.buckets];
    for(const value of selected) if(!buckets.some(b=>b.value===value)) buckets.push({value,count:0});
    return `<section class="facet" data-facet-field="${esc(f.field)}"><h3>${esc(catalog.collections[state.collection].fields[f.field].label)}</h3>${buckets.map((b,i)=>`<label><input type="checkbox" data-facet="${esc(key)}" data-bucket="${i}" ${selected.includes(b.value)?'checked':''}><span>${esc(pretty(b.value))}</span><small>${number(b.count)}</small></label>`).join('')}${f.truncated?'<small>Top values shown</small>':''}</section>`;
  };
  $('#facets').innerHTML=facetLayout().filter(g=>g.fields.length).map(g=>`<section class="facet-group" data-group="${esc(g.id)}">${g.label?'<h2>'+esc(g.label)+'</h2>':''}${g.fields.filter(f=>facets[f]).map(f=>facetHTML([f,facets[f]])).join('')}${g.visible && (g.more_fields || []).some(f=>!g.fields.includes(f))?'<button class="text-button more-facets" data-group-more="'+esc(g.id)+'">More review filters</button>':''}</section>`).join('');
  document.querySelectorAll('[data-group-more]').forEach(b=>b.onclick=()=>{
    const g=catalog.collections[state.collection].facet_groups.find(g=>g.id===b.dataset.groupMore);
    state.facets=[...new Set([...state.facets,...g.more_fields])];
    if(onMoreFacets) onMoreFacets();
    else if(typeof load==='function') { if(typeof cursors!=='undefined') cursors=[null]; load(); }
  });
  document.querySelectorAll('[data-facet]').forEach(input=>input.onchange=()=>{
    const f=facets[input.dataset.facet], selected=state.filters.find(x=>x.field===f.field)?.values || [];
    const buckets=[...f.buckets];
    for(const value of selected) if(!buckets.some(b=>b.value===value)) buckets.push({value,count:0});
    const value=buckets[Number(input.dataset.bucket)].value;
    filterBy(f.field,input.checked?[...selected,value]:selected.filter(v=>v!==value));
  });
  bindHierarchies();
  for(const section of document.querySelectorAll('[data-hierarchy]')) section.querySelector('.hierarchy-body').scrollTop=scrolls.get(section.dataset.hierarchy) || 0;
  if(focusKey) {
    const replacement=[...document.querySelectorAll('[data-tree-key]')].find(el=>el.dataset.treeKey===focusKey);
    replacement?.focus({preventScroll:true});
    if(replacement?.matches('.hierarchy-search') && selection!=null) replacement.setSelectionRange(selection,selection);
  }
}

function matchesFilter(row, filter) {
  const raw=row[filter.field];
  if(filter.op==='exists') return Array.isArray(raw)?raw.length>0:(raw!=null && raw!=='');
  const values=Array.isArray(raw)?raw:(raw==null?[]:[raw]);
  const match=filter.values.some(v=>values.includes(v));
  return filter.op==='nin'?!match:match;
}

function computeLocalFacets(rows, searchPredicate=()=>true) {
  const config=catalog.collections[state.collection];
  const fields=[...new Set(facetLayout().flatMap(g=>g.fields))];
  const baseRows=rows.filter(searchPredicate);
  return Object.fromEntries(fields.map(field=>{
    const activeOthers=state.filters.filter(f=>f.field!==field);
    const matching=baseRows.filter(row=>activeOthers.every(f=>matchesFilter(row,f)));
    const counts=new Map();
    for(const row of matching) {
      const raw=row[field];
      const vals=Array.isArray(raw)?[...new Set(raw)]:(raw==null || raw===''?[]:[raw]);
      for(const v of vals) counts.set(v,(counts.get(v) || 0)+1);
    }
    const limit=Math.max(config.facet_limits?.[field] || 12,Object.keys(hierarchyFor(field)?.nodes || {}).length);
    const all=[...counts.entries()].map(([value,count])=>({value,count})).sort((a,b)=>b.count-a.count || String(a.value).localeCompare(String(b.value)));
    return [field,{field,buckets:all.slice(0,limit),truncated:all.length>limit}];
  }));
}

