/* Collection presentation rules; filters remain independent of display choices. */
let resultRows=[], reviewCache=new Map(), presentationGeneration=0, expandReviews=false;
function normalizeDisplay() {
  const config=catalog.collections[state.collection];
  if(state.display!=='custom' && !Object.hasOwn(config.display_presets || {},state.display)) state.display='compact';
  state.fields=[...new Set(state.fields)].filter(f=>Object.hasOwn(config.display_fields || {},f));
}
function displayFields() {
  const config=catalog.collections[state.collection];
  const fields=state.display==='custom'?state.fields:(config.display_presets?.[state.display]?.fields || []);
  return fields.filter(f=>Object.hasOwn(config.display_fields || {},f));
}
function facetLayout() {
  const config=catalog.collections[state.collection], extra=state.facets || [];
  // Range filters have their own slider facets (ranges.js), not checkbox facets.
  const active=state.filters.filter(f=>f.op!=='between').map(f=>f.field);
  const omit=new Set(state.omitFacets || []), forceGroups=new Set(state.forceVisibleGroups || []);
  const groups=config.facet_groups || [{id:'default',label:'',fields:config.facets}];
  const layout=groups.map(group=>{
    const rule=group.visible_when;
    const visible=!rule || forceGroups.has(group.id) || (rule.display_fields || []).some(f=>displayFields().includes(f)) || state.filters.some(f=>(rule.any_true_filters || []).includes(f.field) && ['in','eq'].includes(f.op || 'in') && f.values.includes(true));
    const members=[...group.fields,...(group.more_fields || [])].filter(f=>!omit.has(f));
    const fields=members.filter(f=>config.fields[f] && ((visible && group.fields.includes(f)) || active.includes(f) || extra.includes(f)));
    return {...group,fields,more_fields:(group.more_fields || []).filter(f=>!omit.has(f)),visible};
  });
  const assigned=layout.flatMap(g=>g.fields);
  const others=[...new Set([...active,...extra])].filter(f=>config.fields[f] && !omit.has(f) && !assigned.includes(f));
  if(others.length) layout.push({id:'additional',label:'Additional filters',fields:others});
  return layout;
}
function renderFacetPicker(onUpdate) {
  const config=catalog.collections[state.collection], layout=facetLayout();
  if($('#review-count-note')) {
    $('#review-count-note').textContent=config.review_count_note || '';
    $('#review-count-note').hidden=!config.review_count_note || !layout.some(g=>g.id==='review' && g.fields.length);
  }
  if(!$('#facet-picker')) return;
  const omit=new Set(state.omitFacets || []), visible=layout.flatMap(g=>g.fields);
  $('#facet-picker').innerHTML='<option value="">Select a field…</option>'+Object.entries(config.fields).filter(([id,f])=>f.facetable && !omit.has(id) && !visible.includes(id)).map(([id,f])=>`<option value="${esc(id)}">${esc(f.label)}</option>`).join('');
  $('#facet-picker').onchange=()=>{if($('#facet-picker').value){state.facets=[...new Set([...state.facets,$('#facet-picker').value])];onUpdate();}};
}
function renderDisplayTools() {
  const config=catalog.collections[state.collection], fields=displayFields();
  $('#display-tools').hidden=!config.display_presets;
  $('#customize-fields summary').textContent=state.display==='custom'?`Customize fields (${fields.length})`:'Customize fields';
  $('.display-presets').innerHTML=Object.entries(config.display_presets || {}).map(([id,p])=>`<button data-display="${esc(id)}" aria-pressed="${state.display===id}">${esc(p.label)}</button>`).join('');
  document.querySelectorAll('[data-display]').forEach(b=>b.onclick=()=>{state.display=b.dataset.display;expandReviews=false;cursors=[null];load();});
  $('#display-fields').innerHTML=Object.entries(config.display_fields || {}).map(([id,label])=>`<label><input type="checkbox" data-display-field="${esc(id)}" ${fields.includes(id)?'checked':''}> ${esc(label)}</label>`).join('');
  document.querySelectorAll('[data-display-field]').forEach(b=>b.onchange=()=>{state.fields=b.checked?[...fields,b.dataset.displayField]:fields.filter(f=>f!==b.dataset.displayField);state.display='custom';cursors=[null];load();});
  $('#expand-reviews').hidden=!fields.includes('assessments');
  $('#expand-reviews').textContent=expandReviews?'Collapse reviews on this page':'Expand reviews on this page';
  $('#expand-reviews').onclick=()=>{expandReviews=!expandReviews;renderDisplayTools();document.querySelectorAll('.card-review').forEach(d=>d.open=expandReviews);};
  renderFacetPicker(()=>{cursors=[null];load();});
}
function reviewTerm(t) {
  return t.id?.startsWith('GO:')?`<a href="${esc(resourceURL('terms',t.id))}">${esc(t.label || t.id)}</a> <small>${esc(t.id)}</small>`:esc(t.label || t.id || '—');
}
function reviewReference(id) {
  return id?.startsWith('PMID:')?`<a href="https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(id.slice(5))}/" target="_blank" rel="noopener">${esc(id)}</a>`:esc(id || '');
}
function reviewTree(value) {
  if(value==null) return '—';
  if(Array.isArray(value)) return '<ul>'+value.map(v=>'<li>'+reviewTree(v)+'</li>').join('')+'</ul>';
  if(typeof value==='object') {
    if(value.id) return reviewTerm(value);
    return '<dl>'+Object.entries(value).map(([k,v])=>`<dt>${esc(k.replaceAll('_',' '))}</dt><dd>${reviewTree(v)}</dd>`).join('')+'</dl>';
  }
  return esc(value);
}
function reviewSummary(item) {
  const r=item.entry.review || {}, action=r.action || 'PENDING';
  const text=r.summary || r.reason || 'No written assessment.';
  return `<article class="assessment-preview"><span class="action-badge action-${esc(action.toLowerCase())}">${esc(action)}</span><p>${esc(text.length>280?text.slice(0,280)+'…':text)}</p>${r.reason && r.summary?'<p class="fine">'+esc(r.reason.length>220?r.reason.slice(0,220)+'…':r.reason)+'</p>':''}${r.proposed_replacement_terms?.length?'<p><strong>Proposed replacement: </strong>'+r.proposed_replacement_terms.map(reviewTerm).join('; ')+'</p>':''}</article>`;
}
function reviewDetails(item,row) {
  const r=item.entry.review || {};
  const extra=Object.fromEntries(Object.entries(r).filter(([k])=>!['action','summary','reason','supported_by','proposed_replacement_terms','additional_reference_ids'].includes(k)));
  return `<article class="assessment-full"><h4>${esc(r.action || 'PENDING')} · ${esc(item.subject_id)}</h4>${r.summary?'<p>'+esc(r.summary)+'</p>':''}${r.reason?'<p class="preserve-lines">'+esc(r.reason)+'</p>':''}${r.proposed_replacement_terms?.length?'<p><strong>Proposed replacement: </strong>'+r.proposed_replacement_terms.map(reviewTerm).join('; ')+'</p>':''}${(r.supported_by || []).map(s=>`<div class="review-support">${reviewReference(s.reference_id)}${s.supporting_text?'<blockquote>'+esc(s.supporting_text)+'</blockquote>':''}</div>`).join('')}${r.additional_reference_ids?.length?'<p>Additional references: '+r.additional_reference_ids.map(reviewReference).join(' · ')+'</p>':''}${Object.keys(extra).length?reviewTree(extra):''}<p class="fine">${esc(item.path)}<br>Assessment ${esc(item.id)}<br>SHA-256: <span class="mono">${esc(item.sha256)}</span></p><a href="${esc(geneURL(row.gene_id))}#review-${encodeURIComponent(item.sha256.slice(0,16))}">Gene review and source context →</a></article>`;
}
function fieldValue(value) {
  if(value==null || value==='' || (Array.isArray(value) && !value.length)) return '—';
  if(Array.isArray(value)) return value.map(fieldValue).join(' · ');
  if(typeof value==='boolean') return value?'Yes':'No';
  if(typeof value==='number') return number(value);
  return String(value);
}
function extraFields(row) {
  const config=catalog.collections[state.collection];
  return displayFields().filter(f=>f!=='assessments').map(f=>{
    const text=fieldValue(row[f]);
    const value=text.length>320?`<details class="field-value"><summary>${esc(text.slice(0,240))}… <span class="field-more">Show all</span></summary><span>${esc(text)}</span></details>`:`<span class="field-value">${esc(text)}</span>`;
    return `<div class="extra-field" data-field="${esc(f)}"><strong>${esc(config.display_fields[f])}</strong>${value}</div>`;
  }).join('');
}
function recordExtras(row) {
  const html=extraFields(row);
  return html?`<div class="record-extra">${html}</div>`:'';
}
function annotationExtras(row) {
  const fields=displayFields();
  let html=extraFields(row);
  if(fields.includes('assessments')) html+=row.has_annotation_review?'<div class="review-preview" aria-live="polite">Loading matching assessments…</div>':'<p class="no-assessment">'+(row.has_review?'Gene review available; no matching annotation assessment.':'No gene review in this snapshot.')+'</p>';
  if(row.has_annotation_review) html+=`<details class="card-review" ${expandReviews && fields.includes('assessments')?'open':''}><summary>Review details (${number(row.review_count)})</summary><div class="review-detail-content"></div><p class="fine">Matched by subject, term, evidence, reference, negation and isoform. Relation, extensions and with/from are not adjudicated by this match.</p></details>`;
  return html?`<div class="annotation-extra" data-annotation="${esc(row.id)}">${html}</div>`:'';
}
async function ensureReviews(ids) {
  const missing=[...new Set(ids)].filter(id=>!reviewCache.has(id));
  if(missing.length){const result=await api('/api/annotation-reviews',{annotation_ids:missing,view:catalog.build_id});for(const [id,items] of Object.entries(result.assessments)) reviewCache.set(id,items);}
}
function renderResults(rows) {
  resultRows=rows;const generation=++presentationGeneration;
  $('#rows').innerHTML=rows.length?rows.map(renderRow).join(''):'<div class="empty">No matches in this snapshot.<br>Try another identifier or reset the filters.</div>';
  document.querySelectorAll('[data-record]').forEach(b=>b.onclick=()=>showRecord(state.collection,b.dataset.record));
  if(state.collection==='annotations') document.querySelectorAll('#rows [data-record]').forEach(button=>{
    const row=rows.find(r=>r.id===button.dataset.record);
    button.after(AmigoChat.annotationButton(row,catalog.build_id));
  });
  document.querySelectorAll('.annotation-extra').forEach(container=>{
    const id=container.dataset.annotation,row=rows.find(r=>r.id===id),details=container.querySelector('.card-review');
    if(details) details.ontoggle=async()=>{
      if(!details.open) return;
      const target=details.querySelector('.review-detail-content');target.textContent='Loading assessment evidence…';
      try{await ensureReviews([id]);if(container.isConnected) target.innerHTML=reviewCache.get(id).map(item=>reviewDetails(item,row)).join('');}
      catch(e){target.textContent=e.message;}
    };
  });
  if(state.collection==='annotations' && displayFields().includes('assessments')) {
    const ids=rows.filter(r=>r.has_annotation_review).map(r=>r.id);
    ensureReviews(ids).then(()=>{
      if(generation!==presentationGeneration) return;
      document.querySelectorAll('.annotation-extra').forEach(container=>{const preview=container.querySelector('.review-preview');if(preview) preview.innerHTML=(reviewCache.get(container.dataset.annotation) || []).map(reviewSummary).join('');});
    }).catch(e=>{if(generation===presentationGeneration) document.querySelectorAll('.review-preview').forEach(el=>el.textContent=e.message);});
  }
}
