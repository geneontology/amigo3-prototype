/* Charts share the browser's filters; unlike sidebar facets, every filter applies. */
let dashboardFields={};
function dashboardConfig() { return catalog.collections[state.collection].dashboard; }
function dashboardNoun() { return catalog.collections[state.collection].label.toLowerCase(); }
function dashboardCountLabel() { return state.collection==='annotations'?'Annotation count':'Record count'; }
function dashboardFromURL(params) {
  return {group:params.get('group'),split:params.get('split'),chart:params.get('chart')==='heatmap'?'heatmap':'stacked',scale:params.get('scale')==='percent'?'percent':'count',limit:[5,10,20].includes(Number(params.get('top')))?Number(params.get('top')):10,x:params.get('jx'),y:params.get('jy')};
}
function normalizeDashboard() {
  const config=dashboardConfig(),fields=catalog.collections[state.collection].fields,d=state.dashboard;
  dashboardFields=Object.fromEntries(config.fields.map(field=>[field,field==='aspect'?'GO aspect':fields[field].label]));
  if(!Object.hasOwn(dashboardFields,d.group)) d.group=config.group;
  if(d.split===null) d.split=config.split;
  if(!Object.hasOwn(dashboardFields,d.split) || d.split===d.group || fields[d.split]?.multivalued) d.split='';
  const numeric=Object.keys(catalog.collections[state.collection].range_facets || {}),joint=config.joint || [];
  if(!numeric.includes(d.x)) d.x=joint[0] || null;
  if(!numeric.includes(d.y)) d.y=joint[1] || null;
}
function dashboardActive() { return state.resultView==='dashboard'; }
function saveDashboardURL(params) {
  if(state.resultView==='dashboard') params.set('view','dashboard');
  const d=state.dashboard,config=dashboardConfig();
  if(d.group!==config.group) params.set('group',d.group);
  if(d.split!==config.split) params.set('split',d.split);
  if(d.chart!=='stacked') params.set('chart',d.chart);
  if(d.scale!=='count') params.set('scale',d.scale);
  if(d.limit!==10) params.set('top',String(d.limit));
  if(d.x && d.x!==config.joint?.[0]) params.set('jx',d.x);
  if(d.y && d.y!==config.joint?.[1]) params.set('jy',d.y);
}
function prepareDashboard() {
  const active=dashboardActive(), d=state.dashboard;
  $('#result-views').hidden=false;
  document.querySelectorAll('[data-result-view]').forEach(button=>{
    button.setAttribute('aria-pressed',String(button.dataset.resultView===state.resultView));
    button.onclick=()=>{state.resultView=button.dataset.resultView;cursors=[null];load();};
  });
  $('#dashboard').hidden=!active;
  $('#rows').hidden=active;
  $('.pagination').hidden=active;
  if(!active) return;
  $('#dashboard').setAttribute('aria-label',catalog.collections[state.collection].label+' dashboard');
  $('#dashboard-eyebrow').textContent=catalog.collections[state.collection].label+' overview';
  $('#dashboard-summary').textContent=`All matching ${dashboardNoun()}, across every results page. Click a chart to refine your selection.`;
  $('#dashboard-total-label').textContent='matching '+dashboardNoun();
  $('#dashboard-scale option[value="count"]').textContent=dashboardCountLabel();
  $('#display-tools').hidden=true;
  $('#review-count-note').hidden=true;
  $('#dashboard-group').innerHTML=Object.entries(dashboardFields).map(([key,label])=>`<option value="${esc(key)}">${esc(label)}</option>`).join('');
  $('#dashboard-split').innerHTML='<option value="">None</option>'+Object.entries(dashboardFields).filter(([key])=>key!==d.group && !catalog.collections[state.collection].fields[key].multivalued).map(([key,label])=>`<option value="${esc(key)}">${esc(label)}</option>`).join('');
  for(const key of ['group','split','chart','scale','limit']) {
    const select=$('#dashboard-'+key);
    select.value=d[key];
    select.onchange=()=>{
      d[key]=key==='limit'?Number(select.value):select.value;
      if(d.group===d.split) d.split='';
      cursors=[null];load();
    };
  }
  $('#dashboard-scale option[value="percent"]').textContent=d.split?'Percent within group':'Percent of selection';
  $('#dashboard-chart').disabled=!d.split;
  if(!d.split) $('#dashboard-chart').value='stacked';
  $('#dashboard-total').textContent='—';
  $('#dashboard-charts').setAttribute('aria-busy','true');
  $('#dashboard-charts').innerHTML='<p class="dashboard-loading">Counting your selection…</p>';
}
function dashboardFailed() {
  $('#dashboard-charts').setAttribute('aria-busy','false');
  $('#dashboard-charts').innerHTML='<p class="empty">Charts could not be loaded. Change a filter or try again.</p>';
}
function dashboardQueries() {
  const d=state.dashboard;
  return {comparison:{field:d.group,split_by:d.split || null,limit:d.limit},...Object.fromEntries(dashboardConfig().cards.map((field,index)=>['summary'+index,{field,limit:d.limit}]))};
}
function dashboardColor(field,value) {
  const colors=['#3267aa','#b05735','#258071','#8864ac','#956b09','#ad4f78','#517e39','#537785','#9b5b58','#7367ba','#7b652e','#367996','#aa5460','#608237','#696f9c','#995e24','#476a70','#895285','#56794c','#965f49','#5871a1','#94607c','#6c6c37','#40816b','#8d6870','#5d7385'];
  const known={species:AmigoSpecies.keys(),aspect:['P','F','C'],evidence_code:['IPI','IEA','IDA','IBA','TAS','IMP','ISS','NAS','HDA','EXP','IGI','IC','IEP','ND','ISO','ISA','ISM','IGC','RCA','IBD','IKR','IRD','HTP','HMP','HGI','HEP']};
  const index=known[field]?.indexOf(String(value)) ?? -1;
  if(index>=0) return colors[index];
  let hash=0;for(const c of field+':'+String(value)) hash=(Math.imul(hash,31)+c.charCodeAt(0))|0;
  return colors[(hash>>>0)%colors.length];
}
function dashboardPercent(count,total) { return total?(100*count/total).toLocaleString(undefined,{maximumFractionDigits:1})+'%':'0%'; }
function dashboardSelection(pairs) { return `data-dashboard-filter="${esc(JSON.stringify(pairs))}"`; }
function dashboardLabel(field,value) { return field==='aspect'?pretty(value):facetValueLabel(field,value); }
function dashboardRemainder(data) {
  const overlap=data.group_multivalued?'<p class="chart-note">Groups can overlap: a record is counted once in each group it belongs to. Percentages across groups may add up to more than 100%.</p>':'';
  return overlap+(data.remainder?`<p class="chart-note">${number(data.remainder)} ${esc(dashboardNoun())} have none of the displayed ${esc(dashboardFields[data.field].toLowerCase())} values.</p>`:'');
}
function dashboardBars(data,percent=false) {
  if(!data.buckets.length) return '<p class="empty">No reported values for this grouping.</p>'+dashboardRemainder(data);
  const maximum=Math.max(1,...data.buckets.map(b=>b.count));
  return `<div class="breakdown-bars">${data.buckets.map(b=>{
    const label=dashboardLabel(data.field,b.value);
    return `<button class="breakdown-bar" ${dashboardSelection([[data.field,b.value]])} aria-label="${esc(label)}: ${number(b.count)} ${esc(dashboardNoun())}; filter ${esc(dashboardFields[data.field])}"><span class="bar-label">${esc(label)}</span><span class="bar-track" aria-hidden="true"><span style="width:${100*b.count/(percent?Math.max(1,data.total):maximum)}%;background:${dashboardColor(data.field,b.value)}"></span></span><span class="bar-value">${percent?dashboardPercent(b.count,data.total):number(b.count)}<small>${percent?number(b.count):dashboardPercent(b.count,data.total)}</small></span></button>`;
  }).join('')}</div>${dashboardRemainder(data)}`;
}
function dashboardStacks(data) {
  const percent=state.dashboard.scale==='percent',maximum=Math.max(1,...data.buckets.map(b=>b.count));
  if(!data.split_by) return dashboardBars(data,percent);
  return `<div class="stacked-bars">${data.buckets.map(b=>{
    const label=dashboardLabel(data.field,b.value),denominator=percent?b.count:maximum;
    return `<div class="stack-row"><button class="stack-label" ${dashboardSelection([[data.field,b.value]])}>${esc(label)}</button><div class="stack-track">${b.segments.filter(s=>s.count).map(s=>{
      const text=`${label} · ${dashboardLabel(data.split_by,s.value)}: ${number(s.count)} ${dashboardNoun()} (${dashboardPercent(s.count,b.count)} of group)`;
      return `<button class="stack-segment" style="width:${100*s.count/denominator}%;background:${dashboardColor(data.split_by,s.value)}" ${dashboardSelection([[data.field,b.value],[data.split_by,s.value]])} title="${esc(text)}" aria-label="${esc(text)}"><span>${esc(percent?dashboardPercent(s.count,b.count):number(s.count))}</span></button>`;
    }).join('')}${b.remainder?`<span class="stack-remainder" style="width:${100*b.remainder/denominator}%" title="${number(b.remainder)} other or unreported"><span>${percent?dashboardPercent(b.remainder,b.count):number(b.remainder)}</span></span>`:''}</div><span class="stack-total">${number(b.count)}</span></div>`;
  }).join('')}</div>`;
}
function dashboardHeatmap(data) {
  const percent=state.dashboard.scale==='percent';
  const max=Math.max(1,...data.buckets.flatMap(b=>b.segments.map(s=>percent?100*s.count/b.count:s.count)));
  const remainder=data.buckets.some(b=>b.remainder);
  return `<div class="heatmap-scroll" tabindex="0" role="region" aria-label="Scrollable ${esc(dashboardNoun())} heatmap"><table class="dashboard-heatmap"><caption>${esc(dashboardFields[data.field])} × ${esc(dashboardFields[data.split_by])} · ${percent?'percent within each group':dashboardCountLabel().toLowerCase()}; darker cells indicate higher values</caption><thead><tr><th scope="col">${esc(dashboardFields[data.field])}</th>${data.columns.map(c=>`<th scope="col"><button ${dashboardSelection([[data.split_by,c.value]])}>${esc(dashboardLabel(data.split_by,c.value))}</button></th>`).join('')}${remainder?'<th scope="col">Other / unreported</th>':''}<th scope="col">Total ${esc(dashboardNoun())}</th></tr></thead><tbody>${data.buckets.map(b=>`<tr><th scope="row"><button ${dashboardSelection([[data.field,b.value]])}>${esc(dashboardLabel(data.field,b.value))}</button></th>${b.segments.map(s=>{
    const value=percent?100*s.count/b.count:s.count,strength=value/max;
    const label=`${dashboardLabel(data.field,b.value)} · ${dashboardLabel(data.split_by,s.value)}: ${number(s.count)} ${dashboardNoun()} (${dashboardPercent(s.count,b.count)} of group)`;
    return `<td><button ${dashboardSelection([[data.field,b.value],[data.split_by,s.value]])} ${s.count?'':'disabled'} style="background:color-mix(in srgb, #3267aa ${8+strength*92}%, white);color:${strength>.55?'white':'#163451'}" aria-label="${esc(label)}" title="${esc(label)}">${percent?dashboardPercent(s.count,b.count):number(s.count)}</button></td>`;
  }).join('')}${remainder?`<td class="heatmap-remainder">${percent?dashboardPercent(b.remainder,b.count):number(b.remainder)}</td>`:''}<td class="heatmap-total">${number(b.count)}</td></tr>`).join('')}</tbody></table></div>`;
}
function renderDashboard(result) {
  const charts=$('#dashboard-charts'),d=state.dashboard;
  charts.setAttribute('aria-busy','false');
  $('#dashboard-total').textContent=number(result.total);
  if(!result.total) {charts.innerHTML=`<div class="empty">No matching ${esc(dashboardNoun())}. Remove a selection or reset the filters to explore again.</div>`;return;}
  const {comparison}=result.breakdowns;
  const title=dashboardFields[d.group]+(d.split?' × '+dashboardFields[d.split]:'');
  const omittedSplit=comparison.buckets.some(b=>b.remainder);
  const legend=d.split && d.chart!=='heatmap'?`<div class="chart-legend" aria-label="${esc(dashboardFields[d.split])} legend">${comparison.columns.map(c=>`<button ${dashboardSelection([[d.split,c.value]])}><span style="background:${dashboardColor(d.split,c.value)}"></span>${esc(dashboardLabel(d.split,c.value))}</button>`).join('')}${omittedSplit?'<span class="remainder-legend"><i></i>Other / unreported</span>':''}</div>`:'';
  charts.innerHTML=`<section class="chart-card comparison-card"><div class="chart-heading"><h3>${esc(title)}</h3><span>${d.scale==='percent'?(d.split?'Percent within group':'Percent of selection'):dashboardCountLabel()}</span></div><p class="chart-note">Top ${d.limit} values${d.split?' in each dimension':''}, ranked across your full selection. ${d.split?'Row totals include all split values.':'Percentages are of all matching records.'}</p>${comparison.buckets.length?(d.split && d.chart==='heatmap'?dashboardHeatmap(comparison):dashboardStacks(comparison)):'<p class="empty">No reported values for this grouping.</p>'}${legend}${d.split?dashboardRemainder(comparison):''}${omittedSplit?'<p class="chart-note">Other / unreported includes split values outside the top values and missing values. Percentages include these records.</p>':''}</section><div class="dashboard-breakdowns">${dashboardConfig().cards.map((field,index)=>`<section class="chart-card"><div class="chart-heading"><h3>By ${esc(dashboardFields[field].toLowerCase())}</h3><span>${dashboardCountLabel()}</span></div>${dashboardBars(result.breakdowns['summary'+index])}</section>`).join('')}</div>${renderDistributions(result.histograms || {})}`;
  bindDistributions();
  charts.querySelectorAll('[data-dashboard-filter]').forEach(button=>button.onclick=()=>{
    const pairs=JSON.parse(button.dataset.dashboardFilter),fields=pairs.map(([field])=>field);
    state.filters=state.filters.filter(f=>!fields.includes(f.field));
    for(const [field,value] of pairs) state.filters.push({id:field,field,op:'in',values:[value]});
    cursors=[null];load();
  });
}
