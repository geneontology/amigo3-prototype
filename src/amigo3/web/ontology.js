/* Typed ancestor DAG and lazy descendant facets. All data uses the pinned build. */
window.AmigoOntology = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = n => Number(n).toLocaleString();
  const relation = p => p==='is_a'?'is a':'part of';
  const byId = id => document.getElementById(id);
  function shell(species) {
    return `<div class="ontology-explorer" id="ontology-explorer">
      <div class="ontology-heading"><h3>Paths to root</h3><div class="ontology-actions"><button id="ontology-reset" hidden>Reset graph</button><button id="ontology-zoom-out" aria-label="Zoom out" disabled>−</button><button id="ontology-zoom-in" aria-label="Zoom in" disabled>+</button><button id="ontology-fit" disabled>Fit graph</button><button id="ontology-enlarge" aria-expanded="false">Enlarge</button></div></div>
      <div class="ontology-legend"><span><i class="ontology-isa"></i>is a</span><span><i class="ontology-partof"></i>part of</span><span>Arrows point to parents · all ancestor paths</span></div>
      <div id="ontology-canvas" role="img" aria-label="Ancestor graph. Use the term selector or relationship list below for keyboard access."></div>
      <p id="ontology-status" class="section-note" role="status">Loading ancestry…</p>
      <div class="ontology-inspect"><label for="ontology-node">Inspect term</label><select id="ontology-node"></select><a id="ontology-open">Open term page →</a><p id="ontology-definition"></p></div>
      <details class="ontology-relationships"><summary>Graph terms and relationships as text</summary><div id="ontology-text"></div></details>
      <div class="ontology-heading"><h3>Explore children</h3><a id="ontology-browse">Browse annotations →</a></div>
      <div class="ontology-filters"><label>Species<select id="ontology-species"><option value="">All loaded species</option>${species.map(s=>`<option value="${esc(s)}">${esc(AmigoSpecies.label(s))}</option>`).join('')}</select></label><label>Evidence<select id="ontology-evidence"><option value="">All evidence</option></select></label><label class="ontology-check"><input type="checkbox" id="ontology-not" checked> Include NOT</label><label class="ontology-check"><input type="checkbox" id="ontology-hide-empty"> Hide empty branches</label></div>
      <p class="section-note">Counts are annotation records for each term and its is a / part of descendants. Shared descendants count once per branch; sibling counts can overlap. Expand to navigate, check to combine branches, or add a term’s paths to the graph.</p>
      <p id="ontology-count-status" class="section-note" role="status"></p>
      <div id="ontology-children"></div>
      <button id="ontology-clear" hidden>Clear branch selection</button>
    </div>`;
  }
  function mount({data, record, api, onFiltersChange, onEvidence}) {
    const root = data.ontology, params = new URLSearchParams(location.search);
    const filterState = {species:params.get('species') || '', evidence:params.get('evidence') || '', includeNot:params.get('include_not')!=='false'};
    const selected = new Set((params.get('branches') || '').split(',').filter(Boolean).slice(0,200));
    const expanded = new Set(), pages = new Map(), cache = new Map([[root.focus,root]]), pending = new Map();
    const graphNodes = new Map(root.nodes.map(n=>[n.id,n])), graphEdges = new Map(root.edges.map(e=>[edgeKey(e),e]));
    let counts = new Map(), countsToken = 0, cy, graphPromise, evidenceValues = [];
    const filters = () => [
      ...(filterState.species?[{id:'species',field:'species',values:[filterState.species]}]:[]),
      ...(filterState.evidence?[{id:'evidence',field:'evidence_code',values:[filterState.evidence]}]:[]),
      ...(!filterState.includeNot?[{id:'negation',field:'negation',values:[false]}]:[])
    ];
    function edgeKey(e) { return JSON.stringify([e.subject,e.predicate,e.object]); }
    function save() {
      const url = new URL(location.href);
      for(const [key,value] of Object.entries({species:filterState.species,evidence:filterState.evidence,include_not:filterState.includeNot?'':'false',branches:[...selected].join(',')})) {
        if(value)url.searchParams.set(key,value);else url.searchParams.delete(key);
      }
      history.replaceState(null,'',url);
    }
    function termUrl(id) {
      const p = new URLSearchParams();
      if(filterState.species)p.set('species',filterState.species);
      if(filterState.evidence)p.set('evidence',filterState.evidence);
      if(!filterState.includeNot)p.set('include_not','false');
      return '/terms/'+encodeURIComponent(id)+(p.size?'?'+p:'');
    }
    function browse(ids, direct=false) {
      return '/?'+new URLSearchParams({collection:'annotations',filters:JSON.stringify([{id:'term',field:direct?'ontology_class_id':'isa_partof_closure',values:ids},...filters()])});
    }
    function syncBrowse() {
      byId('ontology-browse').href = browse(selected.size?[...selected]:[record.id]);
      byId('ontology-browse').textContent = selected.size?`Browse ${selected.size} selected ${selected.size===1?'branch':'branches'} →`:'Browse annotations →';
      byId('ontology-clear').hidden = !selected.size;
    }
    async function getGraph(id) {
      if(cache.has(id))return cache.get(id);
      if(!pending.has(id))pending.set(id,api('/api/ontology/'+encodeURIComponent(id)+'?'+new URLSearchParams({view:data.build_id})).then(value=>{cache.set(id,value);return value;}).finally(()=>pending.delete(id)));
      return pending.get(id);
    }
    function children(id) {
      const unique = new Map();
      for(const child of cache.get(id)?.children || []) {
        if(!unique.has(child.id))unique.set(child.id,{...child,relations:[]});
        unique.get(child.id).relations.push(relation(child.predicate));
      }
      return [...unique.values()];
    }
    function renderChildren() {
      const activeKey=document.activeElement?.dataset.ontologyKey;
      const hideEmpty = byId('ontology-hide-empty').checked;
      function branch(parent,path) {
        const all = children(parent), limit = pages.get(parent) || 30;
        const rows = all.filter(n=>!path.includes(n.id));
        // Unknown counts stay visible while loading; zero is never inferred from an omitted bucket.
        const shown = rows.filter(n=>!hideEmpty || !counts.has(n.id) || counts.get(n.id).total>0 || selected.has(n.id));
        const html = shown.slice(0,limit).map(n=>{
          const count=counts.get(n.id), open=expanded.has(n.id), key=JSON.stringify([...path,n.id]);
          return `<li><div class="ontology-child" data-term="${esc(n.id)}">
            ${n.has_children?`<button class="ontology-expand" data-expand="${esc(n.id)}" data-ontology-key="${esc(key+':expand')}" aria-expanded="${open}" aria-label="${open?'Collapse':'Expand'} ${esc(n.label)}">${open?'−':'+'}</button>`:'<span class="ontology-leaf"></span>'}
            <input type="checkbox" data-branch="${esc(n.id)}" data-ontology-key="${esc(key+':select')}" aria-label="Select ${esc(n.label)} branch" ${selected.has(n.id)?'checked':''}>
            <div class="ontology-child-label"><a data-ontology-key="${esc(key+':term')}" href="${esc(termUrl(n.id))}">${esc(n.label)}</a><small>${esc(n.relations.join(' / '))} · ${esc(n.id)}</small></div>
            <div class="ontology-count"><a data-ontology-key="${esc(key+':count')}" href="${esc(browse([n.id]))}" aria-label="Browse annotations for ${esc(n.label)}">${count?number(count.total):'…'}</a><a class="ontology-direct" data-ontology-key="${esc(key+':direct')}" href="${esc(browse([n.id],true))}">${count?number(count.direct):'…'} direct</a></div>
            <button data-graph="${esc(n.id)}" data-ontology-key="${esc(key+':graph')}" aria-label="Add ${esc(n.label)} paths to graph">+ graph</button></div>
            ${open?(cache.has(n.id)?branch(n.id,[...path,n.id]):'<p class="section-note">Loading children…</p>'):''}</li>`;
        }).join('');
        return `<ul>${html}</ul>${shown.length>limit?`<button class="ontology-more" data-more="${esc(parent)}" data-ontology-key="${esc(JSON.stringify(path)+':more')}">Show more children (${shown.length-limit} remaining)</button>`:''}${!shown.length?'<p class="section-note">'+(rows.length?'No children match these filters.':'No is a / part of children in this snapshot.')+'</p>':''}`;
      }
      byId('ontology-children').innerHTML = branch(record.id,[record.id]);
      if(activeKey)[...byId('ontology-children').querySelectorAll('[data-ontology-key]')].find(el=>el.dataset.ontologyKey===activeKey)?.focus({preventScroll:true});
      syncBrowse();
    }
    async function refreshCounts() {
      const token = ++countsToken;
      counts = new Map(); renderChildren();
      // Count all loaded children, including hidden zero-hit branches, so changing
      // a filter can bring them back. Only requested buckets are returned.
      const ids = [...new Set([record.id,...[...cache.values()].flatMap(v=>v.children.map(n=>n.id))])];
      byId('ontology-count-status').textContent = 'Loading annotation counts…';
      try {
        const collected = new Map();
        for(let i=0;i<ids.length;i+=200) {
          const values=ids.slice(i,i+200);
          const result=await api('/api/query',{collection:'annotations',view:data.build_id,filters:filters(),select:['id'],page:{size:1},facets:{descendants:{field:'isa_partof_closure',values},direct:{field:'ontology_class_id',values}}});
          if(token!==countsToken)return;
          const direct=new Map(result.facets.direct.buckets.map(b=>[b.value,b.count]));
          for(const b of result.facets.descendants.buckets)collected.set(b.value,{total:b.count,direct:direct.get(b.value)});
        }
        counts=collected;renderChildren();
        byId('ontology-count-status').textContent=`${number(counts.get(record.id)?.total || 0)} ${counts.get(record.id)?.total===1?'annotation':'annotations'} in this branch · ${filterState.includeNot?'including':'excluding'} NOT`;
      } catch(e) {if(token===countsToken)byId('ontology-count-status').textContent='Counts unavailable: '+e.message;}
    }
    function setFilters(value,persist=true) {
      Object.assign(filterState,value);
      if(filterState.species && ![...byId('ontology-species').options].some(o=>o.value===filterState.species))byId('ontology-species').add(new Option(filterState.species,filterState.species));
      byId('ontology-species').value=filterState.species;
      if(filterState.evidence && ![...byId('ontology-evidence').options].some(o=>o.value===filterState.evidence))byId('ontology-evidence').add(new Option(filterState.evidence,filterState.evidence));
      byId('ontology-evidence').value=filterState.evidence;
      byId('ontology-not').checked=filterState.includeNot;
      if(persist)save();
      syncBrowse();onFiltersChange({...filterState});refreshCounts();
      if(byId('ontology-node').value)inspect(byId('ontology-node').value);
    }
    function highlight(id) {
      if(!cy)return;
      cy.elements().removeClass('faded');
      const node=cy.getElementById(id);
      if(node.length)cy.elements().not(node.union(node.successors()).union(node.predecessors())).addClass('faded');
    }
    function inspect(id) {
      const node=graphNodes.get(id);if(!node)return;
      byId('ontology-node').value=id;
      byId('ontology-definition').textContent=node.definition || 'No definition recorded in this snapshot.';
      byId('ontology-open').href=termUrl(id);
      byId('ontology-open').textContent=id===record.id?'Current term':`Open ${id} →`;
      highlight(id);
      if(cy){
        cy.nodes().unselect();const item=cy.getElementById(id);item.select();
        const box=item.renderedBoundingBox();
        if(box.x1<0 || box.y1<0 || box.x2>cy.width() || box.y2>cy.height())cy.center(item);
      }
    }
    function renderGraphText() {
      const selectedId=byId('ontology-node').value || record.id;
      const nodes=[...graphNodes.values()].sort((a,b)=>a.label.localeCompare(b.label));
      byId('ontology-node').innerHTML=nodes.map(n=>`<option value="${esc(n.id)}">${esc(n.label)} · ${esc(n.id)}</option>`).join('');
      byId('ontology-text').innerHTML=`<ul>${nodes.map(n=>`<li><a href="${esc(termUrl(n.id))}">${esc(n.label)}</a> <small>${esc(n.id)}</small></li>`).join('')}</ul><ul>${[...graphEdges.values()].map(e=>`<li>${esc(graphNodes.get(e.subject).label)} <strong>${relation(e.predicate)}</strong> ${esc(graphNodes.get(e.object).label)}</li>`).join('')}</ul>`;
      inspect(selectedId);
    }
    const measure=document.createElement('canvas').getContext('2d');measure.font='14px Lato, sans-serif';
    function labelLines(label) {
      const lines=[];let line='';
      for(const word of label.split(/\s+/)) {
        if(line && measure.measureText(line+' '+word).width>172){lines.push(line);line='';}
        // Long unbroken labels are uncommon, but must not escape their node.
        for(const char of (line?' ':'')+word) {
          if(measure.measureText(line+char).width>172){lines.push(line);line='';}
          line+=char;
        }
      }
      if(line)lines.push(line);
      return lines;
    }
    function elements() {
      return [
        ...[...graphNodes.values()].map(n=>{const lines=labelLines(n.label);return {data:{id:n.id,label:lines.join('\n'),height:Math.max(42,lines.length*18+20)},classes:n.id===record.id?'focus':''};}),
        ...[...graphEdges.values()].map(e=>({data:{id:'edge:'+edgeKey(e),source:e.subject,target:e.object},classes:e.predicate==='is_a'?'isa':'partof'}))
      ];
    }
    function fit(readable=true) {
      if(!cy || !byId('ontology-canvas').clientWidth)return;
      cy.resize();cy.fit(undefined,25);
      // Preserve readable text; large graphs can be panned or enlarged.
      if(readable && cy.zoom()<0.8){
        cy.zoom(0.8);
        const box=cy.elements().boundingBox();
        if(box.w*.8<=cy.width()-20 && box.h*.8<=cy.height()-20)cy.center();
        else cy.center(cy.getElementById(record.id));
      }
      if(cy.zoom()>1.15){cy.zoom(1.15);cy.center();}
    }
    function layout() {
      if(!cy)return;
      cy.layout({name:'dagre',rankDir:'BT',nodeSep:24,rankSep:44,edgeSep:12,ranker:'network-simplex',fit:false,animate:false,useDagreEdgeControlPoints:true,automaticDagreEdgeStyle:true}).run();
      const box=cy.elements().boundingBox();
      byId('ontology-canvas').style.height=Math.min(480,Math.max(220,box.h+50))+'px';
      fit();
      byId('ontology-status').textContent=`${graphNodes.size} terms · ${graphEdges.size} relationships · Drag to pan; use Fit graph or Enlarge for more space.`;
      byId('ontology-canvas').dataset.ready='true';
      for(const id of ['ontology-fit','ontology-zoom-in','ontology-zoom-out'])byId(id).disabled=false;
      byId('ontology-reset').hidden=graphNodes.size===root.nodes.length;
    }
    function show() {
      if(cy){cy.resize();requestAnimationFrame(()=>{cy.resize();if(!cy.scratch('visibleFit')){fit();cy.scratch('visibleFit',true);}});return;}
      if(graphPromise)return graphPromise;
      graphPromise=(async()=>{
        try {
          await AmigoGraph.load();
          const container=byId('ontology-canvas');
          cy=cytoscape({container,elements:elements(),layout:{name:'preset'},minZoom:.05,maxZoom:2,boxSelectionEnabled:false,autoungrabify:true,wheelSensitivity:.2,userZoomingEnabled:false,style:[
            {selector:'node',style:{shape:'round-rectangle',width:196,height:'data(height)',label:'data(label)','font-family':'Lato, sans-serif','font-size':14,'text-wrap':'wrap','text-valign':'center','text-halign':'center','background-color':'#fff','border-color':'#9aaac3','border-width':1,color:'#152a50','overlay-opacity':0}},
            {selector:'node.focus',style:{'background-color':'#e9effa','border-color':'#4252af','border-width':2,'font-weight':'bold'}},
            {selector:'edge',style:{width:1.5,'curve-style':'bezier','line-color':'#64748b','target-arrow-color':'#64748b','target-arrow-shape':'triangle','arrow-scale':.9}},
            {selector:'edge.partof',style:{'line-color':'#4252af','target-arrow-color':'#4252af','line-style':'dashed'}},
            {selector:'.faded',style:{opacity:.17}},
            {selector:'node:selected',style:{'border-width':3,'border-color':'#4252af'}}
          ]});
          container._cy=cy;
          cy.on('tap','node',event=>inspect(event.target.id()));
          cy.on('mouseover','node',event=>highlight(event.target.id()));
          cy.on('mouseout','node',()=>highlight(byId('ontology-node').value));
          layout();inspect(byId('ontology-node').value);
          let lastWidth=0;
          const observer=new ResizeObserver(()=>{
            const width=container.clientWidth;if(!width)return;
            cy.resize();
            if(!cy.scratch('visibleFit') || (lastWidth && lastWidth!==width)){fit();cy.scratch('visibleFit',true);}
            lastWidth=width;
          });observer.observe(container);
          window.addEventListener('pagehide',e=>{if(!e.persisted){observer.disconnect();cy.destroy();}},{once:true});
        }catch(e){byId('ontology-status').textContent='Graph unavailable. The complete term and relationship list below remains available. '+e.message;}
      })();return graphPromise;
    }
    byId('ontology-fit').onclick=()=>fit(false);
    for(const [id,factor] of [['ontology-zoom-in',1.25],['ontology-zoom-out',.8]])byId(id).onclick=()=>{if(cy)cy.zoom({level:cy.zoom()*factor,renderedPosition:{x:cy.width()/2,y:cy.height()/2}});};
    byId('ontology-reset').onclick=()=>{
      graphNodes.clear();graphEdges.clear();
      for(const node of root.nodes)graphNodes.set(node.id,node);
      for(const edge of root.edges)graphEdges.set(edgeKey(edge),edge);
      if(cy){cy.elements().remove();cy.add(elements());layout();}
      byId('ontology-node').value=record.id;renderGraphText();inspect(record.id);
    };
    byId('ontology-enlarge').onclick=()=>{
      const open=byId('ontology-explorer').classList.toggle('enlarged');
      byId('ontology-enlarge').setAttribute('aria-expanded',String(open));
      byId('ontology-enlarge').textContent=open?'Compact view':'Enlarge';requestAnimationFrame(()=>fit());
    };
    byId('ontology-node').onchange=e=>inspect(e.target.value);
    byId('ontology-node').onfocus=e=>highlight(e.target.value);
    byId('ontology-species').onchange=e=>setFilters({species:e.target.value});
    byId('ontology-evidence').onchange=e=>setFilters({evidence:e.target.value});
    byId('ontology-not').onchange=e=>setFilters({includeNot:e.target.checked});
    byId('ontology-hide-empty').onchange=renderChildren;
    byId('ontology-clear').onclick=()=>{selected.clear();save();renderChildren();};
    byId('ontology-children').onchange=e=>{
      const id=e.target.dataset.branch;if(!id)return;
      if(e.target.checked && selected.size>=200){e.target.checked=false;byId('ontology-count-status').textContent='Select up to 200 branches at once.';return;}
      if(e.target.checked)selected.add(id);else selected.delete(id);
      save();renderChildren();
    };
    byId('ontology-children').onclick=async e=>{
      const button=e.target.closest('button');if(!button)return;
      const {expand,graph,more}=button.dataset;
      if(more){pages.set(more,(pages.get(more) || 30)+30);renderChildren();return;}
      if(expand && expanded.has(expand)){expanded.delete(expand);renderChildren();return;}
      const focusKey=document.activeElement===button?button.dataset.ontologyKey:null;
      button.disabled=true;
      try {
        const value=await getGraph(expand || graph);
        if(expand){expanded.add(expand);renderChildren();refreshCounts();}
        else {
          for(const node of value.nodes)graphNodes.set(node.id,node);
          for(const edge of value.edges)graphEdges.set(edgeKey(edge),edge);
          renderGraphText();await show();
          if(cy){cy.add(elements().filter(e=>!cy.getElementById(e.data.id).length));layout();inspect(graph);}
          byId('ontology-canvas').scrollIntoView({block:'center',behavior:'instant'});
        }
      }catch(error){byId('ontology-count-status').textContent='Could not load term: '+error.message;}
      finally{
        button.disabled=false;
        if(focusKey && (document.activeElement===document.body || document.activeElement===button))
          [...byId('ontology-children').querySelectorAll('[data-ontology-key]')].find(el=>el.dataset.ontologyKey===focusKey)?.focus({preventScroll:true});
      }
    };
    async function loadEvidence() {
      try {
        const result=await api('/api/query',{collection:'annotations',view:data.build_id,filters:[{id:'term',field:'isa_partof_closure',values:[record.id]}],select:['id'],page:{size:1},facets:{evidence:{field:'evidence_code',limit:100}}});
        evidenceValues=[...new Set([...result.facets.evidence.buckets.map(b=>b.value),filterState.evidence].filter(Boolean))].sort();
        byId('ontology-evidence').innerHTML='<option value="">All evidence</option>'+evidenceValues.map(v=>`<option>${esc(v)}</option>`).join('');
        byId('ontology-evidence').value=filterState.evidence;onEvidence(evidenceValues,filterState.evidence);
      }catch(e){byId('ontology-count-status').textContent='Evidence options unavailable: '+e.message;}
    }
    renderGraphText();setFilters({});loadEvidence();
    // Restore checked branches outside the initially visible level without
    // pretending the ontology has a single breadcrumb path.
    Promise.all([...selected].map(async id=>{
      try {const value=await getGraph(id);for(const node of value.nodes)if(node.id!==id && value.nodes.some(n=>n.id===record.id)){await getGraph(node.id);expanded.add(node.id);}}catch{/* Counts and term links still work for saved selections. */}
    })).then(()=>{if(selected.size){renderChildren();refreshCounts();}});
    window.addEventListener('popstate',()=>{
      const p=new URLSearchParams(location.search);
      selected.clear();for(const id of (p.get('branches') || '').split(',').filter(Boolean).slice(0,200))selected.add(id);
      setFilters({species:p.get('species') || '',evidence:p.get('evidence') || '',includeNot:p.get('include_not')!=='false'},false);
    });
    return {show,setFilters};
  }
  return {shell,mount};
})();
