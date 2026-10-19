/* The full GO-CAM collection as a model-level network. Filtering uses the list backend. */
(() => {
  const $=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const taxonNames={'NCBITaxon:9606':'Human','NCBITaxon:10090':'Mouse','NCBITaxon:10116':'Rat','NCBITaxon:4896':'Fission yeast','NCBITaxon:559292':'Budding yeast','NCBITaxon:7227':'Fruit fly','NCBITaxon:6239':'C. elegans','NCBITaxon:7955':'Zebrafish'};
  const taxonLabel=id=>taxonNames[id]||id||'Species not recorded';
  const palette=['#4269b3','#3a9b87','#dd9850','#9c70b3','#d56e86','#678c54','#60a1bc','#b18b48','#827ba5','#977366'];
  let options,config,build,result,cy,layout,request,inspection,serial=0,inspectSerial=0;
  let state={search:'',filters:[],criteria:[],location:'same',currency:false,layout:'cose',facets:[],build:null};
  const colors=new Map();
  const initial=$('network-inspector').innerHTML;
  const pretty=value=>typeof value==='boolean'?(value?'Yes':'No'):taxonLabel(value);
  const json=value=>`<details><summary>Exact records & provenance</summary><pre>${esc(JSON.stringify(value,null,2))}</pre></details>`;
  const badge=kind=>`<span class="link-badge">${esc(options.criteria[kind].label)}</span>`;
  function fail(message){$('network-error').hidden=!message;$('network-error').textContent=message;}
  async function api(url,body,signal){const response=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal}:{signal});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;}
  function readURL(){
    const p=new URLSearchParams(location.search);
    state.search=p.get('q')||'';state.filters=JSON.parse(p.get('filters')||'[]');
    if(!Array.isArray(state.filters))throw new Error('Invalid filters in this URL.');
    // Preserve early bookmarks as an explicit model filter, not an empty seed workspace.
    if(p.getAll('model').length)state.filters.push({id:'models',field:'id',values:p.getAll('model')});
    state.criteria=p.has('criteria')?p.get('criteria').split(',').filter(Boolean):[...options.defaults];
    state.location=p.get('location')||'same';state.currency=p.get('currency')==='1';state.build=p.get('build')||build;
    state.layout=['cose','concentric','circle'].includes(p.get('layout'))?p.get('layout'):'cose';
    state.facets=(p.get('facets')||'').split(',').filter(Boolean);
    $('network-query').value=state.search;$('network-layout').value=state.layout;
    $('network-location').value=state.location;$('network-currency').checked=state.currency;
    $('network-criteria').innerHTML=Object.entries(options.criteria).map(([id,c])=>`<label class="network-check"><input type="checkbox" value="${id}" ${state.criteria.includes(id)?'checked':''}><span>${esc(c.label)}<small>${esc(c.description)}</small></span></label>`).join('');
    chemistry();
  }
  function collectionParams(){const p=new URLSearchParams({collection:'gocams'});if(state.search)p.set('q',state.search);if(state.filters.length)p.set('filters',JSON.stringify(state.filters));if(state.facets.length)p.set('facets',state.facets.join(','));return p;}
  function saveURL(){const p=collectionParams();p.set('criteria',state.criteria.join(','));p.set('location',state.location);if(state.currency)p.set('currency','1');p.set('layout',state.layout);p.set('build',state.build);const url='/megamodels?'+p;if(location.pathname+location.search!==url)history.pushState({},'',url);}
  const modelURL=(id,neighbors=false)=>'/gocams/'+encodeURIComponent(id)+'?from='+encodeURIComponent('?'+collectionParams())+(neighbors?'#neighbors':'');
  const modelLink=m=>`<a href="${modelURL(m.id)}">${esc(m.label)}</a>`;
  function chemistry(){$('network-chemistry').hidden=!state.criteria.some(x=>x.includes('chemical'));}
  function setFilter(field,values){state.filters=state.filters.filter(f=>f.field!==field);if(values.length)state.filters.push({id:field,field,op:'in',values});refresh();}
  function facets(data){
    $('network-facets').innerHTML=Object.entries(data).map(([key,f])=>{const selected=state.filters.filter(x=>x.field===f.field&&['in','eq',undefined].includes(x.op)).flatMap(x=>x.values||[]),buckets=[...f.buckets];for(const value of selected)if(!buckets.some(b=>b.value===value))buckets.push({value,count:0});return `<section class="facet"><h3>${esc(f.field==='taxon_ids'?'Species':config.fields[f.field]?.label||f.field)}</h3>${buckets.map((b,i)=>(i===8?'<details class="facet-more"><summary>More values ('+(buckets.length-8)+')</summary>':'')+`<label><input type="checkbox" data-facet="${esc(key)}" data-value="${esc(JSON.stringify(b.value))}" ${selected.includes(b.value)?'checked':''}><span>${esc(pretty(b.value))}</span><small>${b.count.toLocaleString()}</small></label>`).join('')}${buckets.length>8?'</details>':''}${f.truncated?'<small>Top values shown</small>':''}</section>`;}).join('');
    $('network-facets').querySelectorAll('input').forEach(input=>input.onchange=()=>{const value=JSON.parse(input.dataset.value),field=input.dataset.facet,old=state.filters.filter(f=>f.field===field&&['in','eq',undefined].includes(f.op)).flatMap(f=>f.values||[]);setFilter(field,input.checked?[...new Set([...old,value])]:old.filter(v=>v!==value));});
    $('network-chips').innerHTML=state.filters.map((f,i)=>`<button data-remove="${i}">${esc(config.fields[f.field]?.label||f.field)} ${esc(f.op||'in')}: ${esc((f.values||[]).map(pretty).join(', ')||'present')} ×</button>`).join('');
    $('network-chips').querySelectorAll('button').forEach(b=>b.onclick=()=>{state.filters.splice(Number(b.dataset.remove),1);refresh();});
  }
  function clearSelection(){inspectSerial++;inspection?.abort();cy?.elements().removeClass('dim').unselect();$('network-node').value='';$('network-inspector').innerHTML=initial;}
  function selectNode(id){
    if(!cy||!id)return;inspectSerial++;inspection?.abort();const node=cy.getElementById(id);if(!node.length)return;
    cy.elements().removeClass('dim').unselect();node.select();cy.elements().not(node.closedNeighborhood()).addClass('dim');$('network-node').value=id;
    const m=node.data('record'),links=node.connectedEdges();
    $('network-inspector').innerHTML=`<p class="eyebrow">GO-CAM model</p><h2>${modelLink(m)}</h2><p class="mono">${esc(m.id)}</p><p>${m.taxon_ids.map(taxonLabel).map(esc).join(', ')||'Species not recorded'} · ${m.activity_count} activities</p><p>${esc(m.status)}${m.has_review?' · Review available':''}</p><p><a href="${modelURL(id,true)}">Open model & explore neighbors →</a></p><h3>${links.length} connected models in this view</h3>${links.slice(0,60).map(e=>{const other=e.source().id()===id?e.target():e.source();return `<button data-edge="${esc(e.id())}">${esc(other.data('record').label)}<br><small>${e.data('match_count')} matching activity pairs across criteria</small></button>`;}).join('')}${links.length>60?'<p>First 60 listed; all remain in the network.</p>':''}`;
    $('network-inspector').querySelectorAll('[data-edge]').forEach(b=>b.onclick=()=>selectEdge(cy.getElementById(b.dataset.edge)));
  }
  async function selectEdge(edge){
    const generation=++inspectSerial;inspection?.abort();inspection=new AbortController();
    cy.elements().removeClass('dim').unselect();edge.select();cy.elements().not(edge.union(edge.connectedNodes())).addClass('dim');
    const a=edge.source().data('record'),b=edge.target().data('record');
    $('network-inspector').innerHTML=`<p class="eyebrow">Candidate connection</p><h2>${esc(a.label)} ↔ ${esc(b.label)}</h2><p>Loading exact matches…</p>`;
    try {
      const d=await api('/api/megamodels/connection',{...linkOptions(),model_ids:[a.id,b.id]},inspection.signal);if(generation!==inspectSerial)return;
      $('network-inspector').innerHTML=`<p class="eyebrow">Candidate connection</p><h2>${modelLink(a)} ↔ ${modelLink(b)}</h2><p>${d.match_count.toLocaleString()} matches across selected criteria. These do not assert causality or activity identity.</p>${Object.entries(d.criteria).map(([kind,count])=>badge(kind)+' '+count).join('<br>')}${d.anchors.slice(0,30).map(c=>`<article class="candidate-record">${badge(c.criterion)}<p><a href="${modelURL(c.source_model)}#activity-${encodeURIComponent(c.source_activity)}">${esc(c.source_label)}</a> ${c.directed?'→':'↔'} <a href="${modelURL(c.target_model)}#activity-${encodeURIComponent(c.target_activity)}">${esc(c.target_label)}</a></p><p class="mono">${esc(c.match.join(' · '))}</p>${json(c)}</article>`).join('')}${d.match_count>30?'<p>Showing the first 30 anchors.</p>':''}${json({models:d.models,criteria:d.criteria,match_count:d.match_count,anchors_returned:d.anchors.length,truncated:d.truncated})}`;
    }catch(e){if(e.name!=='AbortError'&&generation===inspectSerial)$('network-inspector').textContent=e.message;}
  }
  function arrange(){
    if(!cy)return;layout?.stop();$('network-stop').hidden=false;$('network-canvas').dataset.ready='false';
    const instance=cy;
    const settings=state.layout==='cose'?{name:'cose',animate:true,animationThreshold:0,refresh:10,randomize:false,numIter:350,initialTemp:100,coolingFactor:.95,minTemp:1,nodeRepulsion:()=>9000,idealEdgeLength:()=>85,edgeElasticity:()=>100,gravity:.12,componentSpacing:65,nodeOverlap:10}:state.layout==='concentric'?{name:'concentric',concentric:n=>n.degree(),levelWidth:()=>5,minNodeSpacing:15,animate:false}:{name:'circle',animate:false};
    layout=cy.layout({...settings,fit:true,padding:45,stop:()=>{if(cy!==instance)return;$('network-stop').hidden=true;$('network-canvas').dataset.ready='true';}});layout.run();
    if(!cy.nodes().length){$('network-stop').hidden=true;$('network-canvas').dataset.ready='true';}
  }
  function render(){
    const models=result.models,elements=[];
    models.forEach((m,i)=>{const taxon=m.primary_taxon||'',angle=i*2.3999632297,radius=17*Math.sqrt(i);elements.push({data:{id:m.id,label:m.label.length>80?m.label.slice(0,79)+'…':m.label,record:m,color:colors.get(taxon)||'#9ba3af',size:14+Math.min(25,Math.sqrt(m.activity_count)*1.7)},position:{x:radius*Math.cos(angle),y:radius*Math.sin(angle)}});});
    result.edges.forEach(e=>elements.push({data:e}));
    cy=cytoscape({container:$('network-canvas'),elements,layout:{name:'preset'},minZoom:.015,maxZoom:5,hideEdgesOnViewport:true,textureOnViewport:true,selectionType:'single',style:[
      {selector:'node',style:{width:'data(size)',height:'data(size)','background-color':'data(color)','border-color':'#fff','border-width':1,label:'','font-size':11,'text-valign':'bottom','text-margin-y':7,'text-wrap':'wrap','text-max-width':145,'min-zoomed-font-size':8,'overlay-opacity':0}},
      {selector:'edge',style:{width:1,'line-color':'#98a6bd',opacity:.36,'curve-style':'haystack','haystack-radius':0}},
      {selector:'node.labels',style:{label:'data(label)'}},
      {selector:'node:selected',style:{label:'data(label)','border-color':'#162d59','border-width':3,'z-index':10,'text-background-color':'white','text-background-opacity':.95,'text-background-padding':4}},
      {selector:'edge:selected',style:{width:2.5,opacity:1,'line-color':'#bd842c','z-index':8}},
      {selector:'.dim',style:{opacity:.08}}
    ]});
    $('network-canvas')._cy=cy;
    if($('network-labels').checked)cy.nodes().addClass('labels');
    cy.on('tap','node',e=>selectNode(e.target.id()));cy.on('tap','edge',e=>selectEdge(e.target));cy.on('tap',e=>{if(e.target===cy)clearSelection();});
    cy.on('mouseover','node',e=>{if(!$('network-labels').checked)e.target.addClass('labels');});cy.on('mouseout','node',e=>{if(!$('network-labels').checked)e.target.removeClass('labels');});
    $('network-node').innerHTML='<option value="">Select a model…</option>'+[...models].sort((a,b)=>a.label.localeCompare(b.label)).map(m=>`<option value="${esc(m.id)}">${esc(m.label)} · ${esc(m.id)}</option>`).join('');$('network-node').disabled=!models.length;
    const taxa=new Map();models.forEach(m=>{const id=m.primary_taxon||'';taxa.set(id,(taxa.get(id)||0)+1);});
    $('network-legend').innerHTML=[...taxa].sort((a,b)=>b[1]-a[1]).map(([id,n])=>`<span><i style="background:${colors.get(id)||'#9ba3af'}"></i>${esc(taxonLabel(id))} (${n.toLocaleString()})</span>`).join('');
    $('network-download').disabled=false;$('network-png').disabled=!models.length;
    arrange();
  }
  const linkOptions=()=>({criteria:state.criteria,chemical_location:state.location,include_currency:state.currency,view:state.build});
  async function refresh(save=true){
    if(save)saveURL();$('network-list').href='/?'+collectionParams();
    const generation=++serial;request?.abort();request=new AbortController();clearSelection();layout?.stop();cy?.destroy();cy=null;result=null;$('network-canvas')._cy=null;$('network-canvas').dataset.ready='false';
    $('network-download').disabled=true;$('network-png').disabled=true;$('network-node').disabled=true;fail('');$('network-status').textContent='Loading the complete matching collection…';
    const fields=[...new Set([...config.facets,...state.facets,...state.filters.map(f=>f.field).filter(f=>config.fields[f]?.facetable)])];
    const query={collection:'gocams',view:state.build,search:state.search,filters:state.filters,facets:Object.fromEntries(fields.map(field=>[field,{field,limit:30,exclude_filters:state.filters.filter(f=>f.field===field).map(f=>f.id)}]))};
    try {
      const data=await api('/api/megamodels/network',{...linkOptions(),query},request.signal);if(generation!==serial)return;result=data;
      facets(data.facets);$('network-status').textContent=`${data.total.toLocaleString()} models · ${data.edge_count.toLocaleString()} connections · ${data.isolated_count.toLocaleString()} disconnected models${data.missing_sources.length?' · '+data.missing_sources.length+' models lack source activity data':''}`;
      render();
    }catch(e){if(e.name==='AbortError'||generation!==serial)return;fail(e.message);$('network-status').textContent='Network unavailable';}
  }
  async function init(){
    try {
      const [catalog,collections]=await Promise.all([api('/api/megamodels'),api('/api/collections'),AmigoGraph.load()]);options=catalog;build=catalog.build_id;config=collections.collections.gocams;
      const taxonCounts=new Map();options.models.forEach(m=>taxonCounts.set(m.taxon,(taxonCounts.get(m.taxon)||0)+1));
      [...taxonCounts].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).forEach(([id],i)=>colors.set(id,id?(palette[i]||`hsl(${Math.round(i*137.5)%360}, 45%, 45%)`):'#9ba3af'));
      readURL();$('build').textContent='Pinned build '+build;$('network-currencies').textContent=options.currency_terms.join(', ');
      $('network-facet-picker').innerHTML+=[...Object.entries(config.fields)].filter(([,f])=>f.facetable).map(([id,f])=>`<option value="${esc(id)}">${esc(f.label)}</option>`).join('');
      $('network-search').onsubmit=e=>{e.preventDefault();state.search=$('network-query').value.trim();refresh();};
      $('network-clear').onclick=()=>{state.search='';state.filters=[];state.facets=[];$('network-query').value='';refresh();};
      $('network-facet-picker').onchange=e=>{if(e.target.value){state.facets=[...new Set([...state.facets,e.target.value])];e.target.value='';refresh();}};
      $('network-criteria').onchange=()=>{state.criteria=[...$('network-criteria').querySelectorAll('input:checked')].map(i=>i.value);chemistry();refresh();};
      $('network-location').onchange=e=>{state.location=e.target.value;refresh();};$('network-currency').onchange=e=>{state.currency=e.target.checked;refresh();};
      $('network-layout').onchange=e=>{state.layout=e.target.value;saveURL();arrange();};$('network-relayout').onclick=arrange;$('network-stop').onclick=()=>layout?.stop();$('network-fit').onclick=()=>cy?.fit(undefined,40);
      const zoom=f=>{if(cy)cy.zoom({level:Math.max(cy.minZoom(),Math.min(cy.maxZoom(),cy.zoom()*f)),renderedPosition:{x:cy.width()/2,y:cy.height()/2}});};$('network-in').onclick=()=>zoom(1.3);$('network-out').onclick=()=>zoom(1/1.3);
      $('network-labels').onchange=e=>cy?.nodes().toggleClass('labels',e.target.checked);
      $('network-node').onchange=e=>{selectNode(e.target.value);const n=cy?.getElementById(e.target.value);if(n?.length)cy.animate({center:{eles:n},zoom:Math.max(cy.zoom(),1)},{duration:180});};$('network-deselect').onclick=clearSelection;
      function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
      $('network-download').onclick=()=>download(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}),'gocam-network-'+build+'.json');
      $('network-png').onclick=async()=>{try{download(await cy.png({output:'blob-promise',bg:'#fff',full:true,maxWidth:5000,maxHeight:5000}),'gocam-network.png');}catch(e){fail(e.message);}};
      window.addEventListener('popstate',()=>{try{readURL();refresh(false);}catch(e){fail(e.message);}});
      await refresh(false);
    }catch(e){fail(e.message);$('network-status').textContent='Could not load the network.';}
  }
  init();
})();
