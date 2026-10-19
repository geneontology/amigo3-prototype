/* Read-only composition of pinned source models; candidate links never merge activities. */
window.AmigoNeighborhood = {async mount(container, seed) {
  const template=await fetch("/neighborhood.html");
  if(!template.ok)throw new Error("Could not load the neighborhood explorer.");
  container.innerHTML=await template.text();
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const palette = ['#5367b4','#298577','#a16c48','#9371aa','#657e99','#a6627c'];
  const criterionColors = {shared_gene:'#997ca6',gene_mf:'#7f64a5',full_activity_signature:'#487eae',terminal_to_initial:'#318c88',shared_chemical:'#ae934e',chemical_flow:'#b58028'};
  let catalog, relations, result, cy, models, selected=[], criteria=[], mode='models', serial=0, pending, lastSearch;
  const modelURL = id => '/gocams/'+encodeURIComponent(id);
  const activityURL = (model,id) => modelURL(model)+'#activity-'+encodeURIComponent(id);
  const modelLink = model => `<a href="${modelURL(model.id)}">${esc(model.label)}</a>`;
  const json = value => `<details><summary>Exact record & provenance</summary><pre>${esc(JSON.stringify(value,null,2))}</pre></details>`;
  const badge = kind => `<span class="link-badge">${esc(catalog.criteria[kind].label)}</span>`;
  function fail(message) {$('mega-error').hidden=!message;$('mega-error').textContent=message;}
  async function fetchJSON(url, options={}) {
    const response=await fetch(url,options),body=await response.json();
    if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:JSON.stringify(body.detail));
    return body;
  }
  function readURL() {
    lastSearch=location.search;
    const p=new URLSearchParams(location.search);
    selected=[seed,...new Set(p.getAll('neighbor').filter(id=>id!==seed))];
    criteria=p.has('criteria')?p.get('criteria').split(',').filter(x=>catalog.criteria[x]):[...catalog.defaults];
    mode=p.get('mode')==='models'?'models':'activities';
    $('chemical-location').value=['same','compatible','any'].includes(p.get('location'))?p.get('location'):'same';
    $('include-currency').checked=p.get('currency')==='1';
    $('link-criteria').innerHTML=Object.entries(catalog.criteria).map(([id,c])=>`<label class="check-line"><input type="checkbox" value="${id}" ${criteria.includes(id)?'checked':''}><span>${esc(c.label)}<small>${esc(c.description)}</small></span></label>`).join('');
    chemistry();
  }
  function writeURL() {
    const p=new URLSearchParams(location.search);p.delete('neighbor');selected.filter(id=>id!==seed).forEach(id=>p.append('neighbor',id));
    p.set('criteria',criteria.join(','));p.set('mode',mode);p.set('location',$('chemical-location').value);
    if($('include-currency').checked)p.set('currency','1');
    p.set('build',catalog.build_id);
    const next=location.pathname+'?'+p+'#neighbors';
    if(next!==location.pathname+location.search+location.hash)history.pushState({},'',next);
    lastSearch=location.search;
  }
  function chemistry() {$('chemical-options').hidden=!criteria.some(c=>c.includes('chemical'));}
  function search() {
    const q=$('model-search').value.trim().toLowerCase();
    const matches=q?catalog.models.filter(m=>!selected.includes(m.id)&&`${m.id} ${m.label} ${m.taxon_label} ${m.taxon}`.toLowerCase().includes(q)):[];
    $('model-results').innerHTML=matches.slice(0,25).map(m=>`<button data-add="${esc(m.id)}">${esc(m.label)}<small>${esc(m.id)} · ${m.activity_count} activities</small></button>`).join('')+(q&&!matches.length?'<p class="mega-note">No matching models.</p>':'')+(matches.length>25?'<p class="mega-note">Showing 25 matches. Refine your search.</p>':'');
  }
  function selection() {
    $('selected-count').textContent=`${selected.length} / 12`;
    $('selected-models').innerHTML=selected.map((id,i)=>{
      const m=models.get(id);return `<article class="mega-model" style="border-left:3px solid ${palette[i%palette.length]};padding-left:9px">${m?modelLink(m):esc(id)}<p class="mono">${esc(id)}</p><p>${m?`${m.activity_count} activities · ${esc(m.taxon_label)}`:''}</p>${id===seed?'<small>Starting model</small>':`<button data-remove="${esc(id)}" aria-label="Remove ${esc(m?.label||id)}">Remove</button>`}</article>`;
    }).join('')||'<p class="mega-note">Search above or open the glycolysis example.</p>';
    search();
  }
  function add(id) {
    if(selected.includes(id))return;
    if(selected.length>=12){fail('Choose at most 12 models. Remove one before adding another.');return;}
    selected.push(id);$('model-search').value='';refresh();
  }
  function candidateHTML(link) {
    const a=models.get(link.source_model),b=models.get(link.target_model);
    return `<article class="candidate-record">${badge(link.criterion)}<p><a href="${activityURL(a.id,link.source_activity)}">${esc(link.source_label)}</a> ${link.directed?'→':'↔'} <a href="${activityURL(b.id,link.target_activity)}">${esc(link.target_label)}</a></p><p class="mega-note">${modelLink(a)}<br>${modelLink(b)}</p><p class="mono">${esc(link.match.join(' · '))}</p>${link.participants.length?'<p class="mega-note">Chemical location: '+esc([...new Set(link.participants.map(p=>p.location_match))].join(', '))+'</p>':''}${json(link)}</article>`;
  }
  function inspectNode(id) {
    const model=models.get(id),activity=result?.activities.find(a=>a.id===id);
    if(model) {
      $('mega-inspector').innerHTML=`<p class="eyebrow">Source model</p><h2>${modelLink(model)}</h2><p class="mono">${esc(model.id)} · ${esc(model.taxon)}</p><p>${model.activity_count} activities. ${model.duplicate_activity_ids?`${model.duplicate_activity_ids} repeated activity IDs are preserved as separate source rows.`:''}</p><p><a href="/api/snapshots/${encodeURIComponent(model.source.sha256)}">Open pinned source YAML ↗</a></p>${json(model)}`;
    } else if(activity) {
      const a=activity;
      $('mega-inspector').innerHTML=`<p class="eyebrow">Source activity · ${esc(a.role)}</p><h2>${esc(a.label)}</h2><p>${esc(a.function_label)} <span class="mono">${esc(a.function)}</span></p><p>Part of ${esc(a.process_label||'not recorded')} · Location: ${esc(a.location_label||'not recorded')}</p><p class="mono">${esc(a.gene)}<br>${esc(a.activity_id)}</p><p>${modelLink(models.get(a.model_id))}</p>${a.duplicate_id?'<p>This ID occurs more than once in the source; this is row '+(a.source_row+1)+'. The exact record below disambiguates it.</p>':''}<p><a href="${activityURL(a.model_id,a.activity_id)}">Open activity and evidence →</a></p>${json(a)}`;
    }
    if(cy){cy.elements().removeClass('faded');const node=cy.getElementById(id);if(node.length){cy.elements().unselect();node.select();const nearby=node.closedNeighborhood().union(node.descendants()).union(node.ancestors());cy.elements().not(nearby).addClass('faded');}}
  }
  function inspectEdge(edge) {
    cy.elements().removeClass('faded').unselect();edge.select();
    const d=edge.data();
    if(d.candidates) {
      $('mega-inspector').innerHTML='<p class="eyebrow">Cross-model candidates</p><h2>Why these models connect</h2><p>These matches suggest relationships to investigate. They do not establish causality or justify merging activities.</p>'+d.candidates.slice(0,30).map(candidateHTML).join('')+(d.candidates.length>30?'<p>Showing 30 matches. Download JSON for all returned matches.</p>':'');
    } else {
      const e=d.record,relation=relations[e.predicate];
      $('mega-inspector').innerHTML=`<p class="eyebrow">Recorded causal relationship</p><h2>${esc(relation?.label||e.predicate)}</h2><p>${modelLink(models.get(e.model_id))}</p><p class="mono">${esc(e.predicate)}</p>${json(e)}`;
    }
  }
  function renderGraph() {
    if(cy)cy.destroy();cy=null;
    $('mega-canvas').dataset.ready='false';
    ['models','activities'].forEach(v=>$('view-'+v).setAttribute('aria-pressed',String(v===mode)));
    if(!result)return;
    const color=new Map(result.models.map((m,i)=>[m.id,palette[i%palette.length]]));
    const elements=[];
    if(mode==='models') {
      result.models.forEach(m=>elements.push({data:{id:m.id,label:(m.label.length>140?m.label.slice(0,139)+'…':m.label)+'\n'+m.activity_count+' activities',color:color.get(m.id)},classes:'model'}));
      const groups=new Map();
      for(const link of result.links){const ids=[link.source_model,link.target_model].sort(),key=ids.join('|');if(!groups.has(key))groups.set(key,{source:ids[0],target:ids[1],candidates:[]});groups.get(key).candidates.push(link);}
      [...groups.values()].forEach((g,i)=>elements.push({data:{...g,id:'candidate:'+i,label:[...new Set(g.candidates.map(c=>catalog.criteria[c.criterion].label))].join('\n'),color:'#a17b40',arrow:'none'},classes:'candidate'}));
    } else {
      result.models.forEach(m=>elements.push({data:{id:m.id,label:m.label.length>85?m.label.slice(0,84)+'…':m.label,color:color.get(m.id)},classes:'boundary'}));
      result.activities.forEach(a=>elements.push({data:{id:a.id,parent:a.model_id,label:a.label,color:color.get(a.model_id),image:AmigoGraph.card(a.label,a.function_label,a.location_label||'Location not recorded')},classes:'activity'}));
      result.causal_edges.forEach(e=>{const r=relations[e.predicate]||{};elements.push({data:{...e,record:e,label:'',color:{positive:'#21816b',negative:'#b44c68',neutral:'#6c7fa4'}[r.sign||'neutral'],arrow:r.sign==='negative'?'tee':'triangle'},classes:'causal'});});
      // Bundle criteria for one directed endpoint pair; inspect retains every exact match.
      const groups=new Map();
      result.links.forEach(link=>{const key=JSON.stringify([link.source,link.target,link.directed]);if(!groups.has(key))groups.set(key,{source:link.source,target:link.target,directed:link.directed,candidates:[]});groups.get(key).candidates.push(link);});
      [...groups.values()].forEach((g,i)=>elements.push({data:{...g,id:'candidate:'+i,label:'',color:criterionColors[g.candidates[0].criterion],arrow:g.directed?'triangle':'none'},classes:'candidate'}));
    }
    cy=cytoscape({container:$('mega-canvas'),elements,layout:{name:'preset'},minZoom:.04,maxZoom:2.5,boxSelectionEnabled:false,style:[
      {selector:'node',style:{shape:'round-rectangle','background-color':'#fff','border-width':2,'border-color':'data(color)','overlay-opacity':0}},
      {selector:'node.model',style:{width:260,height:124,label:'data(label)','text-wrap':'wrap','text-max-width':232,'text-valign':'center','font-size':14,color:'#20365b','font-family':'Lato','text-margin-y':0}},
      {selector:'node.activity',style:{width:204,height:116,'background-image':'data(image)','background-fit':'contain'}},
      {selector:'node.boundary',style:{label:'data(label)','font-size':16,'font-weight':'bold','text-wrap':'wrap','text-max-width':420,'text-valign':'top','text-margin-y':-12,'background-color':'#f1f5fa','background-opacity':.6,'border-opacity':.5,'border-width':1,padding:35}},
      {selector:'edge',style:{width:2,'curve-style':'bezier','line-color':'data(color)','target-arrow-color':'data(color)','target-arrow-shape':'data(arrow)',label:'data(label)','font-size':11,'text-wrap':'wrap','text-background-color':'#fff','text-background-opacity':.95,'text-background-padding':4,'control-point-step-size':50}},
      {selector:'edge.candidate',style:{'line-style':'dashed',width:2.3}},
      {selector:':selected',style:{'border-width':4,'border-color':'#e3ae25','underlay-color':'#e3ae25','underlay-opacity':.2,'underlay-padding':5}},
      {selector:'.faded',style:{opacity:.22}}
    ]});
    $('mega-canvas')._cy=cy;
    if(mode==='models')cy.layout({name:'dagre',rankDir:'LR',nodeSep:65,rankSep:160,fit:false}).run();
    else {
      // Layout each source model independently, then pack boxes; candidate density cannot distort source chains.
      const layouts=result.models.map(m=>{
        const nodes=result.activities.filter(a=>a.model_id===m.id).map(a=>({data:{id:a.id}}));
        if(!nodes.length)return {positions:[{id:m.id,x:130,y:65}],w:260,h:130};
        const edges=result.causal_edges.filter(e=>e.model_id===m.id).map(e=>({data:{id:e.id,source:e.source,target:e.target}}));
        const local=cytoscape({headless:true,styleEnabled:true,elements:[...nodes,...edges],style:[{selector:'node',style:{width:204,height:116}}]});
        const connected=local.nodes().filter(n=>n.degree()>0),isolated=local.nodes().filter(n=>n.degree()===0);
        if(connected.length)connected.union(local.edges()).layout({name:'dagre',rankDir:'LR',nodeSep:50,rankSep:80,fit:false}).run();
        const occupied=connected.length?connected.boundingBox():{x1:0,y2:-150};
        const columns=Math.max(1,Math.ceil(Math.sqrt(isolated.length*1.3)));
        isolated.forEach((n,i)=>n.position({x:occupied.x1+102+(i%columns)*245,y:occupied.y2+210+Math.floor(i/columns)*155}));
        const box=local.nodes().boundingBox();
        const positions=local.nodes().map(n=>({id:n.id(),x:n.position('x')-box.x1,y:n.position('y')-box.y1}));local.destroy();return {positions,w:box.w||260,h:box.h||130};
      });
      const columns=Math.ceil(Math.sqrt(layouts.length));let x=0,y=0,rowHeight=0;
      cy.batch(()=>layouts.forEach((l,i)=>{if(i&&i%columns===0){x=0;y+=rowHeight+170;rowHeight=0;}l.positions.forEach(p=>cy.getElementById(p.id).position({x:x+p.x,y:y+p.y}));x+=l.w+180;rowHeight=Math.max(rowHeight,l.h);}));
    }
    cy.fit(undefined,45);if(cy.zoom()>1.2)cy.zoom(1.2);
    AmigoGraph.observeSize($('mega-canvas'),cy,45);
    cy.on('tap','node',event=>{$('mega-node').value=event.target.id();inspectNode(event.target.id());});
    cy.on('tap','edge',event=>inspectEdge(event.target));
    cy.on('tap',event=>{if(event.target===cy)cy.elements().removeClass('faded').unselect();});
    $('mega-node').innerHTML='<option value="">Select a model or activity…</option>'+result.models.map(m=>`<option value="${esc(m.id)}">Model: ${esc(m.label)}</option>`).join('')+(mode==='activities'?result.activities.map(a=>`<option value="${esc(a.id)}">${esc(a.label)} · ${esc(a.function_label)} · ${esc(a.model_id)}</option>`).join(''):'');
    $('mega-node').disabled=false;
    $('mega-canvas').dataset.ready='true';
    const edges=cy.edges();
    $('connection-list').innerHTML=edges.slice(0,200).map(e=>`<button data-edge="${esc(e.id())}">${e.hasClass('candidate')?'Candidate: ':'Recorded: '}${esc(e.source().data('label'))} → ${esc(e.target().data('label'))}${e.data('candidates')?' · '+[...new Set(e.data('candidates').map(c=>catalog.criteria[c.criterion].label))].map(esc).join(', '):' · '+esc(relations[e.data('predicate')]?.label||e.data('predicate'))}</button>`).join('')+(edges.length>200?'<p>Showing 200 connections. Download JSON for all returned records.</p>':'')+json({unresolved_causal_edges:result.external_causal_edges});
    ['mega-fit','mega-reset','mega-download','mega-in','mega-out'].forEach(id=>$(id).disabled=false);
  }
  async function refresh(save=true) {
    if(save)writeURL();selection();fail('');
    const generation=++serial;if(pending)pending.abort();pending=new AbortController();
    result=null;if(cy)cy.destroy();cy=null;$('mega-canvas')._cy=null;$('mega-canvas').dataset.ready='false';
    ['mega-fit','mega-reset','mega-download','mega-node','mega-in','mega-out'].forEach(id=>$(id).disabled=true);
    $('neighbor-models').textContent='';$('neighbor-count').textContent='';$('connection-list').textContent='';
    $('mega-inspector').innerHTML='<h2>Inspect a connection</h2><p>Select a model, activity or connection to see its source and the exact reason for a match.</p>';
    const requestedBuild=new URLSearchParams(location.search).get('build');
    if(!selected.length){$('mega-status').textContent=`${catalog.models.length.toLocaleString()} models available. Search for a pathway or open the example.`;return;}
    $('mega-status').textContent='Finding connections in the pinned model corpus…';
    try {
      const response=await fetchJSON('/api/megamodels',{method:'POST',headers:{'Content-Type':'application/json'},signal:pending.signal,body:JSON.stringify({model_ids:selected,criteria,chemical_location:$('chemical-location').value,include_currency:$('include-currency').checked,view:requestedBuild||catalog.build_id})});
      if(generation!==serial)return;result=response;
      $('neighbor-count').textContent=`(${result.neighbor_count})`;
      $('neighbor-models').innerHTML=result.neighbors.map((n,i)=>`<article class="mega-model">${modelLink(n.model)}<p>${esc(n.model.id)} · ${n.model.activity_count} activities</p><div>${Object.keys(n.criteria).map(badge).join('')}</div><div class="model-actions"><button data-add="${esc(n.model.id)}">Add model</button><button data-neighbor="${i}">Why linked?</button></div></article>`).join('')||'<p class="mega-note">No other models match these criteria. Try a broader link type or another seed model.</p>';
      if(result.neighbor_count>result.neighbors.length)$('neighbor-models').insertAdjacentHTML('beforeend',`<p class="mega-note">Showing the first ${result.neighbors.length} neighbors. Refine the link criteria to explore others.</p>`);
      const duplicates=result.models.reduce((sum,m)=>sum+m.duplicate_activity_ids,0);
      $('mega-status').textContent=`${result.models.length} models · ${result.activities.length} activities · ${result.causal_edges.length} recorded causal links · ${result.link_count} candidate matches${result.links_truncated?' (first '+result.links.length+' shown)':''}${result.external_causal_edges.length?' · '+result.external_causal_edges.length+' unresolved causal links omitted; inspect source records below':''}${duplicates?' · '+duplicates+' repeated activity IDs kept as separate rows':''}`;
      renderGraph();
    } catch(error) {if(error.name==='AbortError'||generation!==serial)return;fail(error.message);$('mega-status').textContent='Could not build this workspace.';}
  }
  async function init() {
    try {
      [catalog,,relations]=await Promise.all([fetchJSON('/api/megamodels'),AmigoGraph.load(),fetchJSON('/assets/causal-relations.json')]);relations=relations.relations;
      models=new Map(catalog.models.map(m=>[m.id,m]));readURL();

      $('currency-terms').textContent=catalog.currency_terms.join(', ');
      $('model-search').disabled=false;
      $('model-search').oninput=search;
      $('link-criteria').onchange=()=>{criteria=[...$('link-criteria').querySelectorAll('input:checked')].map(x=>x.value);chemistry();refresh();};
      $('chemical-location').onchange=()=>refresh();$('include-currency').onchange=()=>refresh();
      container.addEventListener('click',event=>{
        const b=event.target.closest('button');if(!b)return;
        if(b.dataset.add)add(b.dataset.add);
        if(b.dataset.remove){selected=selected.filter(id=>id!==b.dataset.remove);refresh();}
        if(b.dataset.neighbor!==undefined&&result){const n=result.neighbors[Number(b.dataset.neighbor)];$('mega-inspector').innerHTML=`<p class="eyebrow">Neighbor discovery</p><h2>${modelLink(n.model)}</h2><p>Example anchors (up to five). Add the model to inspect the complete connection set.</p>`+n.anchors.map(candidateHTML).join('');}
        if(b.dataset.edge&&cy)inspectEdge(cy.getElementById(b.dataset.edge));
      });
      ['models','activities'].forEach(v=>$('view-'+v).onclick=()=>{mode=v;writeURL();renderGraph();});
      $('mega-node').onchange=()=>inspectNode($('mega-node').value);
      $('mega-fit').onclick=()=>cy?.fit(undefined,45);$('mega-reset').onclick=()=>renderGraph();
      const zoom=factor=>{if(cy)cy.zoom({level:Math.max(cy.minZoom(),Math.min(cy.maxZoom(),cy.zoom()*factor)),renderedPosition:{x:cy.width()/2,y:cy.height()/2}});};
      $('mega-in').onclick=()=>zoom(1.3);$('mega-out').onclick=()=>zoom(1/1.3);
      $('mega-download').onclick=()=>{const blob=new Blob([JSON.stringify(result,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='gocam-megamodel-'+catalog.build_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
      window.addEventListener('popstate',()=>{if(location.search!==lastSearch){readURL();refresh(false);}});
      await refresh(false);
    } catch(error){fail(error.message);$('mega-status').textContent='Could not load the explorer.';}
  }
  await init();
}};
