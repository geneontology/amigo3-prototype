/* Interactive, source-grounded GO-CAM activity view. Libraries load only here. */
window.AmigoGraph = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const byId = id => document.getElementById(id);
  const colors = {positive:'#21816b',negative:'#b44c68',neutral:'#6c7fa4'};
  let libraries;
  function script(src) {
    return new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=src;s.onload=resolve;s.onerror=()=>reject(new Error('Could not load the pathway viewer.'));document.head.append(s);});
  }
  function load() {
    return libraries ||= script('/vendor/cytoscape.min.js').then(()=>script('/vendor/cytoscape-dagre.min.js'));
  }
  // A request can finish after its tab was hidden. Fit that first render when shown.
  function observeSize(container,cy,padding=36) {
    let needsFit=container.clientWidth===0;
    const observer=new ResizeObserver(()=>{
      if(!container.clientWidth)return;
      cy.resize();
      if(needsFit){cy.fit(undefined,padding);needsFit=false;}
    });
    observer.observe(container);
    cy.on('destroy',()=>observer.disconnect());
    return observer;
  }
  function shell(model) {
    if(!model.activities?.length) return '<p>No activities recorded.</p>';
    return `<div class="pathway-viewer" id="pathway-viewer">
      <div class="pathway-toolbar"><div class="pathway-heading"><strong>Pathway explorer</strong><span id="graph-summary">${model.activities.length} activities</span></div>
        <div class="pathway-controls"><label>Flow <select id="graph-direction"><option value="LR">Left to right</option><option value="TB">Top to bottom</option></select></label><button id="graph-fit" disabled>Fit</button><button id="graph-zoom-in" aria-label="Zoom in" disabled>+</button><button id="graph-zoom-out" aria-label="Zoom out" disabled>−</button><button id="graph-reset" disabled>Reset layout</button><button id="graph-expand" disabled>Expand</button><button id="graph-export" disabled>Save PNG</button></div></div>
      <div class="pathway-legend"><span><i class="edge-key positive"></i>Positive effect</span><span><i class="edge-key negative"></i>Negative effect</span><span><i class="edge-key neutral"></i>Other causal relation</span><span class="graph-hint">Drag to pan · scroll to zoom · select an activity or connection</span></div>
      <div id="pathway-canvas" class="pathway-canvas" role="img" aria-label="Interactive GO-CAM activity network. Use the activity selector below for keyboard access."></div>
      <p id="graph-status" class="graph-status" role="status">Arranging activities…</p>
      <div class="pathway-selection"><label for="graph-activity">Explore activity</label><select id="graph-activity"><option value="">Select an activity…</option></select><button id="graph-clear" disabled>Clear selection</button><a href="#activities">All activities & evidence ↓</a></div>
      <div id="graph-inspector" class="graph-inspector" aria-live="polite"><div><strong>Follow the causal connections</strong><p>Select a card to highlight its immediate neighborhood. Select a connection to inspect the exact relation and its evidence.</p></div><p class="section-note">Each card is a distinct activity in this model. The small caption gives its recorded location; arrows and inhibition bars follow the source assertions.</p></div>
    </div>`;
  }
  function wrap(text,limit,max) {
    const words=String(text).split(/\s+/),lines=[];let line='';
    for(const word of words) {if(line && (line+' '+word).length>limit){lines.push(line);line=word;}else line+=(line?' ':'')+word;}
    if(line)lines.push(line);
    if(lines.length>max){lines.length=max;lines[max-1]=lines[max-1].slice(0,limit-1)+'…';}
    return lines;
  }
  function card(name,mf,location) {
    // SVG background gives the canvas cards typographic hierarchy without DOM overlays.
    const lines=wrap(mf,29,3),caption=wrap(location,31,1)[0];
    const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="204" height="116" viewBox="0 0 204 116"><rect x="0" y="0" width="4" height="116" fill="#5367b4"/><text x="14" y="27" font-family="Arial,sans-serif" font-size="17" font-weight="700" fill="#152a50">${esc(name.length>23?name.slice(0,22)+'…':name)}</text>${lines.map((line,i)=>`<text x="14" y="${48+i*15}" font-family="Arial,sans-serif" font-size="12.5" fill="#40536c">${esc(line)}</text>`).join('')}<line x1="14" x2="190" y1="91" y2="91" stroke="#e4eaf3"/><text x="14" y="106" font-family="Arial,sans-serif" font-size="10.5" fill="#687b91">${esc(caption)}</text></svg>`;
    return 'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(svg);
  }
  async function mount({model,labels,ref,tree}) {
    if(!model.activities?.length)return;
    const container=byId('pathway-canvas'),status=byId('graph-status'),inspector=byId('graph-inspector');
    const initial=inspector.innerHTML,selector=byId('graph-activity');
    const name=id=>labels[id] || id || 'Not recorded';
    const activities=new Map(model.activities.map(a=>[a.id,a]));
    let cy;
    try {
      const [,relationsResponse]=await Promise.all([load(),fetch('/assets/causal-relations.json')]);
      if(!relationsResponse.ok)throw new Error('Could not load relationship labels.');
      const relations=(await relationsResponse.json()).relations;
      const nodes=model.activities.map(a=>{
        const title=name(a.enabled_by?.term || a.id),mf=name(a.molecular_function?.term),location=a.occurs_in?name(a.occurs_in.term):'Location not recorded';
        const option=document.createElement('option');option.value=a.id;option.textContent=title+' · '+mf;selector.append(option);
        return {data:{id:a.id,name:title,function:mf,image:card(title,mf,location)}};
      });
      const edges=[];let omitted=0;
      for(const a of model.activities)for(const [i,e] of (a.causal_associations || []).entries()) {
        if(!activities.has(e.downstream_activity)){omitted++;continue;}
        const relation=relations[e.predicate] || {label:e.predicate,sign:'neutral'};
        edges.push({data:{id:'edge:'+a.id+':'+i,source:a.id,target:e.downstream_activity,predicate:e.predicate,relation:relation.label,sign:relation.sign,color:colors[relation.sign],association:e},classes:relation.sign+(a.id===e.downstream_activity?' loop':'')});
      }
      cy=cytoscape({container,elements:[...nodes,...edges],layout:{name:'preset',fit:false},minZoom:0.08,maxZoom:3,boxSelectionEnabled:false,selectionType:'single',
        style:[
          {selector:'node',style:{shape:'round-rectangle',width:204,height:116,'background-color':'#fff','background-image':'data(image)','background-fit':'contain','background-clip':'node','border-width':1.3,'border-color':'#bdcbe0','overlay-opacity':0}},
          {selector:'edge',style:{width:2.2,'curve-style':'bezier','line-color':'data(color)','target-arrow-color':'data(color)','target-arrow-shape':'triangle','arrow-scale':1.15,'overlay-opacity':0,'underlay-padding':5}},
          {selector:'edge.loop',style:{'curve-style':'bezier','loop-direction':'-45deg','loop-sweep':'70deg','control-point-step-size':180}},
          {selector:'edge.routed',style:{'curve-style':'unbundled-bezier','control-point-weights':e=>e.scratch('controlPointWeights') || [0.5],'control-point-distances':e=>e.scratch('controlPointDistances') || [0]}},
          {selector:'edge.negative',style:{'target-arrow-shape':'tee'}},
          {selector:'node.isolated',style:{'border-style':'dashed'}},
          {selector:'.faded',style:{opacity:0.14}},
          {selector:'node:selected',style:{'border-width':3,'border-color':'#4252af','underlay-color':'#dfe6fc','underlay-opacity':0.7,'underlay-padding':5}},
          {selector:'edge:selected',style:{width:4,'underlay-color':'#b6c5e6','underlay-opacity':0.35,'underlay-padding':5}},
          {selector:'.neighbor',style:{'border-color':'#8496bf','border-width':2}},
        ]});
      cy.nodes().filter(n=>n.degree()===0).addClass('isolated');
      const viewer=byId('pathway-viewer');
      // Expose the live instance on its own container for diagnostics and browser verification.
      container._cy=cy;
      byId('graph-summary').textContent=`${nodes.length} activities · ${edges.length} causal connections`;
      const fit=()=>{cy.resize();cy.fit(cy.elements(),36);};
      function layout() {
        status.textContent='Arranging activities…';
        const disconnected=cy.nodes().filter(n=>n.degree()===0);
        const connected=cy.elements().not(disconnected);
        // Dagre's control-point routing does not support self-loops. Cytoscape
        // renders those separately; they do not change the rank assignment.
        const routed=connected.not(cy.edges().filter(e=>e.source().id()===e.target().id()));
        cy.edges().removeClass('routed');
        routed.layout({name:'dagre',rankDir:byId('graph-direction').value,nodeSep:38,rankSep:52,edgeSep:20,ranker:'network-simplex',fit:false,animate:false,nodeDimensionsIncludeLabels:false,useDagreEdgeControlPoints:true,automaticDagreEdgeStyle:false}).run();
        routed.edges().addClass('routed');
        if(disconnected.length) {
          const box=connected.nodes().length?connected.boundingBox():{x1:0,y2:-100,w:0};
          const columns=Math.max(1,Math.ceil(Math.sqrt(disconnected.length*(cy.width()/cy.height())*0.55)));
          disconnected.forEach((node,i)=>node.position({x:box.x1+102+(i%columns)*240,y:box.y2+160+Math.floor(i/columns)*150}));
        }
        fit();
        const isolated=disconnected.length;
        status.textContent=`${isolated?isolated+' activities have no causal connections in this model. ':''}${omitted?omitted+' connections refer to activities outside this model; see the causal relationship table. ':''}Layout follows recorded connections; it does not infer new relationships.`;
        container.dataset.ready='true';
      }
      function clear() {cy.elements().unselect().removeClass('faded neighbor');selector.value='';inspector.innerHTML=initial;byId('graph-clear').disabled=true;}
      function inspect(element) {
        cy.elements().removeClass('faded neighbor');
        const nearby=element.isNode()?element.closedNeighborhood():element.union(element.connectedNodes());
        cy.elements().not(nearby).addClass('faded');nearby.nodes().not(element).addClass('neighbor');
        byId('graph-clear').disabled=false;
        if(element.isNode()) {
          const a=activities.get(element.id());selector.value=a.id;
          const incident=element.connectedEdges();
          inspector.innerHTML=`<div><p class="eyebrow">Selected activity</p><h3>${ref(a.enabled_by?.term,name(a.enabled_by?.term || a.id))}</h3><p>${ref(a.molecular_function?.term)}</p><p class="section-note">${a.occurs_in?'Occurs in '+ref(a.occurs_in.term):'Location not recorded'}</p>${a.part_of?'<p>Part of '+ref(a.part_of.term)+'</p>':''}<a class="graph-evidence-link" href="#activity-${encodeURIComponent(a.id)}">Activity evidence & full record ↓</a></div><div><h4>Connections <span class="muted">(${incident.length})</span></h4>${incident.length?'<ul class="graph-connections">'+incident.map(e=>`<li><button class="graph-edge-link" data-edge="${esc(e.id())}"><span class="connection-dot ${e.data('sign')}"></span>${esc(e.source().data('name'))} <span class="muted">${esc(e.data('relation'))}</span> ${esc(e.target().data('name'))}</button></li>`).join('')+'</ul>':'<p>No causal connections recorded for this activity.</p>'}</div>`;
          inspector.querySelectorAll('[data-edge]').forEach(b=>b.onclick=()=>select(cy.getElementById(b.dataset.edge)));
        } else {
          selector.value='';const e=element.data(),a=activities.get(e.source),b=activities.get(e.target);
          inspector.innerHTML=`<div><p class="eyebrow">Selected connection</p><h3>${esc(name(a.enabled_by?.term || a.id))} → ${esc(name(b.enabled_by?.term || b.id))}</h3><p><strong>${esc(e.relation)}</strong> ${ref(e.predicate,e.predicate)}</p><p class="section-note">The assertion and evidence below come from this model.</p></div><div><details open><summary>Connection evidence & provenance</summary>${tree(e.association)}</details></div>`;
        }
      }
      function select(element) {cy.elements().unselect();element.select();inspect(element);}
      cy.on('tap','node, edge',evt=>select(evt.target));
      cy.on('tap',evt=>{if(evt.target===cy)clear();});
      cy.on('mouseover','node, edge',()=>{container.style.cursor='pointer';});
      cy.on('mouseout','node, edge',()=>{container.style.cursor='grab';});
      selector.onchange=()=>{if(!selector.value){clear();return;}const node=cy.getElementById(selector.value);select(node);cy.animate({center:{eles:node},zoom:Math.max(cy.zoom(),0.9)},{duration:200});};
      byId('graph-clear').onclick=clear;byId('graph-fit').onclick=fit;
      byId('graph-direction').onchange=()=>{clear();layout();};
      byId('graph-reset').onclick=()=>{clear();layout();};
      const zoom=factor=>cy.zoom({level:Math.max(cy.minZoom(),Math.min(cy.maxZoom(),cy.zoom()*factor)),renderedPosition:{x:cy.width()/2,y:cy.height()/2}});
      byId('graph-zoom-in').onclick=()=>zoom(1.25);byId('graph-zoom-out').onclick=()=>zoom(0.8);
      byId('graph-expand').onclick=()=>{viewer.classList.toggle('expanded');byId('graph-expand').textContent=viewer.classList.contains('expanded')?'Collapse':'Expand';fit();};
      byId('graph-export').onclick=async()=>{
        const button=byId('graph-export');button.disabled=true;
        try {const blob=await cy.png({output:'blob-promise',bg:'#f8faff',full:true,maxWidth:6000,maxHeight:6000});const href=URL.createObjectURL(blob),link=document.createElement('a');link.href=href;link.download=model.id.replace(/[^a-zA-Z0-9_-]/g,'_')+'-activities.png';link.click();setTimeout(()=>URL.revokeObjectURL(href),1000);}catch(e){status.textContent='Image export failed: '+e.message;}finally{button.disabled=false;}
      };
      const observer=observeSize(container,cy);
      window.addEventListener('pagehide',event=>{if(!event.persisted){observer.disconnect();cy.destroy();}},{once:true});
      byId('graph-direction').value=window.innerWidth<700?'TB':'LR';
      layout();viewer.querySelectorAll('.pathway-controls button').forEach(b=>b.disabled=false);
    } catch(e) {status.textContent=e.message+' The activity and causal relationship tables remain available in the Activities tab.';container.dataset.error='true';if(cy)cy.destroy();}
  }
  return {shell,mount,load,card,observeSize};
})();
