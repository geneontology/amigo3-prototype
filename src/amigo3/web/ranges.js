/* Quantitative facets: range sliders over binned integer fields, plus dashboard
 * distributions. Bins come from the server; filters are inclusive `between` ranges. */
function rangeConfig() { return catalog.collections[state.collection].range_facets || {}; }
function rangeLabel(field) { return catalog.collections[state.collection].fields[field]?.label || field; }
function rangeFilter(field) { return state.filters.find(f=>f.field===field && f.op==='between'); }
function rangeText(start,end) { return start===end?number(start):`${number(start)}–${number(end)}`; }
function setRange(field,low,high,histogram) {
  state.filters=state.filters.filter(f=>!(f.field===field && f.op==='between'));
  if(low>high) [low,high]=[high,low];
  if(!histogram || low>histogram.min || high<histogram.max) state.filters.push({id:field,field,op:'between',values:[low,high]});
  cursors=[null];load();
}
function rangeQueries() {
  const queries={};
  for(const [field,options] of Object.entries(rangeConfig()))
    queries['range:'+field]={field,scale:options.scale || 'linear',bins:16,exclude_filters:state.filters.filter(f=>f.field===field).map(f=>f.id)};
  if(dashboardActive()) {
    const d=state.dashboard,config=dashboardConfig(),scale=f=>rangeConfig()[f]?.scale || 'linear';
    for(const field of config.distributions || []) queries['dist:'+field]={field,scale:scale(field),bins:12};
    if(d.x && d.y && d.x!==d.y) queries.joint={field:d.x,y_field:d.y,scale:scale(d.x),y_scale:scale(d.y),bins:10,y_bins:8};
  }
  return queries;
}
function rangeBars(histogram,low,high) {
  const max=Math.max(1,...histogram.buckets.map(b=>b.count));
  return `<div class="range-histogram" aria-hidden="true">${histogram.buckets.map(b=>`<span class="${b.end>=low && b.start<=high?'in-range':''}" style="height:${b.count?Math.max(6,100*b.count/max):0}%" title="${esc(rangeText(b.start,b.end))}: ${number(b.count)}"></span>`).join('')}</div>`;
}
function renderRangeFacets(histograms) {
  const fields=Object.keys(rangeConfig()).filter(field=>histograms?.['range:'+field]);
  if(!fields.length) return;
  const section=document.createElement('section');
  section.className='facet-group range-group';
  section.dataset.group='structure';
  section.innerHTML='<h2>Size &amp; structure</h2>'+fields.map(field=>{
    const h=histograms['range:'+field],label=rangeLabel(field),selected=rangeFilter(field);
    if(h.min===null) return `<section class="facet range-facet" data-range-field="${esc(field)}"><h3>${esc(label)}</h3><p class="range-empty">No values in this selection</p></section>`;
    const [low,high]=selected?selected.values:[h.min,h.max],last=h.buckets.length-1;
    const position=value=>{const i=h.buckets.findIndex(b=>value<=b.end);return i<0?last:i;};
    return `<section class="facet range-facet" data-range-field="${esc(field)}"><h3>${esc(label)}</h3>${rangeBars(h,low,high)}<div class="range-slider"><input type="range" min="0" max="${last}" step="1" value="${position(low)}" data-range-thumb="low" aria-label="Minimum ${esc(label)}"><input type="range" min="0" max="${last}" step="1" value="${position(high)}" data-range-thumb="high" aria-label="Maximum ${esc(label)}"></div><div class="range-inputs"><input type="number" min="${h.min}" max="${h.max}" value="${low}" data-range-input="low" aria-label="Minimum ${esc(label)}"><span>to</span><input type="number" min="${h.min}" max="${h.max}" value="${high}" data-range-input="high" aria-label="Maximum ${esc(label)}">${selected?`<button type="button" class="text-button" data-range-clear>Any</button>`:''}</div>${h.missing?`<small class="range-note">${number(h.missing)} without a value</small>`:''}</section>`;
  }).join('');
  $('#facets').append(section);
  section.querySelectorAll('[data-range-field]').forEach(facet=>{
    const field=facet.dataset.rangeField,h=histograms['range:'+field];
    if(h.min===null) return;
    const [lowThumb,highThumb]=['low','high'].map(k=>facet.querySelector(`[data-range-thumb="${k}"]`));
    const [lowInput,highInput]=['low','high'].map(k=>facet.querySelector(`[data-range-input="${k}"]`));
    const bars=[...facet.querySelectorAll('.range-histogram span')];
    const preview=()=>{
      if(Number(lowThumb.value)>Number(highThumb.value)) {
        if(document.activeElement===highThumb) lowThumb.value=highThumb.value;
        else highThumb.value=lowThumb.value;
      }
      lowInput.value=h.buckets[lowThumb.value].start;
      highInput.value=h.buckets[highThumb.value].end;
      bars.forEach((bar,i)=>bar.classList.toggle('in-range',i>=Number(lowThumb.value) && i<=Number(highThumb.value)));
    };
    lowThumb.oninput=highThumb.oninput=preview;
    lowThumb.onchange=highThumb.onchange=()=>{preview();setRange(field,Number(lowInput.value),Number(highInput.value),h);};
    lowInput.onchange=highInput.onchange=()=>{
      const clamp=v=>Math.min(h.max,Math.max(h.min,Math.round(Number(v))));
      if(lowInput.value==='' || highInput.value==='') return;
      setRange(field,clamp(lowInput.value),clamp(highInput.value),h);
    };
    const clear=facet.querySelector('[data-range-clear]');
    if(clear) clear.onclick=()=>setRange(field,h.min,h.max,h);
  });
}

/* Dashboard: one column histogram per configured field and a joint heatmap. */
function rangeFilterButton(pairs) { return `data-range-filter="${esc(JSON.stringify(pairs))}"`; }
function distributionCard(field,h) {
  const label=rangeLabel(field),scale=rangeConfig()[field]?.scale==='log'?'log-spaced bins':'equal-width bins';
  if(!h || h.min===null) return `<section class="chart-card distribution-card"><div class="chart-heading"><h3>${esc(label)}</h3></div><p class="empty">No values in this selection.</p></section>`;
  const max=Math.max(1,...h.buckets.map(b=>b.count));
  const columns=h.buckets.map(b=>{
    const text=`${label} ${rangeText(b.start,b.end)}: ${number(b.count)} ${dashboardNoun()}`;
    return `<button class="histogram-column" ${rangeFilterButton([[field,b.start,b.end]])} ${b.count?'':'disabled'} title="${esc(text)}" aria-label="${esc(text)}; filter"><span class="histogram-count">${b.count?number(b.count):''}</span><span class="histogram-bar" style="height:${100*b.count/max}%"></span><span class="histogram-tick">${esc(rangeText(b.start,b.end))}</span></button>`;
  }).join('');
  return `<section class="chart-card distribution-card"><div class="chart-heading"><h3>${esc(label)}</h3><span>range ${esc(rangeText(h.min,h.max))} · ${scale}</span></div><div class="histogram" role="group" aria-label="${esc(label)} distribution">${columns}</div>${h.missing?`<p class="chart-note">${number(h.missing)} ${esc(dashboardNoun())} have no value.</p>`:''}</section>`;
}
function jointCard(h) {
  const d=state.dashboard,options=Object.keys(rangeConfig()).map(f=>`<option value="${esc(f)}">${esc(rangeLabel(f))}</option>`).join('');
  const controls=`<div class="joint-controls"><label>Rows<select id="dashboard-joint-x">${options}</select></label><label>Columns<select id="dashboard-joint-y">${options}</select></label></div>`;
  let body='<p class="empty">Choose two different measures.</p>';
  if(h && h.min!==null) {
    const max=Math.max(1,...h.cells.flat());
    body=`<div class="heatmap-scroll" tabindex="0" role="region" aria-label="Joint distribution heatmap"><table class="joint-heatmap"><caption>${esc(rangeLabel(h.field))} (rows) × ${esc(rangeLabel(h.y_field))} (columns) · ${esc(dashboardNoun())} per cell; darker cells hold more</caption><thead><tr><th scope="col">${esc(rangeLabel(h.field))} ↓ / ${esc(rangeLabel(h.y_field))} →</th>${h.y_buckets.map(c=>`<th scope="col"><button ${rangeFilterButton([[h.y_field,c.start,c.end]])}>${esc(rangeText(c.start,c.end))}</button></th>`).join('')}<th scope="col">Total</th></tr></thead><tbody>${h.buckets.map((r,i)=>`<tr><th scope="row"><button ${rangeFilterButton([[h.field,r.start,r.end]])}>${esc(rangeText(r.start,r.end))}</button></th>${h.cells[i].map((count,j)=>{
      const c=h.y_buckets[j],strength=count/max,text=`${rangeLabel(h.field)} ${rangeText(r.start,r.end)} · ${rangeLabel(h.y_field)} ${rangeText(c.start,c.end)}: ${number(count)} ${dashboardNoun()}`;
      return `<td><button ${rangeFilterButton([[h.field,r.start,r.end],[h.y_field,c.start,c.end]])} ${count?'':'disabled'} style="background:${count?`color-mix(in srgb, #3267aa ${8+strength*92}%, white)`:'var(--surface-muted)'};color:${strength>.55?'white':'#163451'}" title="${esc(text)}" aria-label="${esc(text)}">${count?number(count):''}</button></td>`;
    }).join('')}<td class="heatmap-total">${number(r.count)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  return `<section class="chart-card joint-card"><div class="chart-heading"><h3>Joint distribution</h3><span>${esc(dashboardCountLabel())}</span></div>${controls}${body}</section>`;
}
function renderDistributions(histograms) {
  const config=dashboardConfig();
  if(!(config.distributions || []).length) return '';
  return `<h3 class="distributions-heading">Size &amp; structure</h3><p class="chart-note">Distributions across your selection. Click a column or cell to filter to that range.</p><div class="distribution-grid">${config.distributions.map(f=>distributionCard(f,histograms['dist:'+f])).join('')}</div>${config.joint?.length?jointCard(histograms.joint):''}`;
}
function bindDistributions() {
  document.querySelectorAll('[data-range-filter]').forEach(button=>button.onclick=()=>{
    for(const [field,low,high] of JSON.parse(button.dataset.rangeFilter)) {
      state.filters=state.filters.filter(f=>!(f.field===field && f.op==='between'));
      state.filters.push({id:field,field,op:'between',values:[low,high]});
    }
    cursors=[null];load();
  });
  for(const axis of ['x','y']) {
    const select=$('#dashboard-joint-'+axis);
    if(!select) continue;
    select.value=state.dashboard[axis];
    select.onchange=()=>{state.dashboard[axis]=select.value;cursors=[null];load();};
  }
}
