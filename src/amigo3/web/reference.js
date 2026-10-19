const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = n => Number(n || 0).toLocaleString();
const link = (type,id,label) => `<a href="/${type}/${encodeURIComponent(id)}">${esc(label || id)}</a>`;
const external = (url,label) => `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)} ↗</a>`;
function actionClass(action) {
  return ['ACCEPT','KEEP_AS_NON_CORE'].includes(action) ? 'retain' : ['REMOVE','MODIFY','MARK_AS_OVER_ANNOTATED'].includes(action) ? 'reconsider' : 'pending';
}
function table(headers,rows) { return rows.length?`<div class="table-scroll"><table class="record-table"><thead><tr>${headers.map(x=>'<th scope="col">'+esc(x)+'</th>').join('')}</tr></thead><tbody>${rows.map(r=>'<tr>'+r.map(x=>'<td>'+x+'</td>').join('')+'</tr>').join('')}</tbody></table></div>`:'<p class="section-note">None recorded in this snapshot.</p>'; }
function externalLinks(r) {
  const [prefix, local] = [r.id.split(':',1)[0], r.id.slice(r.id.indexOf(':')+1)];
  const links = [];
  if (prefix==='PMID') links.push(external('https://pubmed.ncbi.nlm.nih.gov/'+encodeURIComponent(local)+'/','PubMed'), external('https://europepmc.org/article/MED/'+encodeURIComponent(local),'Europe PMC'));
  if (prefix==='GO_REF') links.push(external('https://geneontology.org/GO_REF/'+encodeURIComponent(local),'GO reference'));
  if (prefix==='Reactome') links.push(external('https://reactome.org/content/detail/'+encodeURIComponent(local),'Reactome'));
  if (r.doi) links.push(external('https://doi.org/'+r.doi,'DOI '+r.doi));
  return links;
}
function annotationsURL(id) {
  return '/?collection=annotations&filters='+encodeURIComponent(JSON.stringify([{id:'reference',field:'references',values:[id]}]));
}
function reviewCard(review) {
  const bib = review.bibliography || {}, assessment = bib.reference_review || {};
  const judgement = ['relevance','correctness'].filter(k=>assessment[k]).map(k=>`<dt>${k==='relevance'?'Relevance':'Correctness'}</dt><dd>${esc(assessment[k])}</dd>`).join('')
    + (assessment.review_notes?`<dt>Reviewer notes</dt><dd>${esc(assessment.review_notes)}</dd>`:'')
    + (bib.is_invalid?'<dt>Status</dt><dd class="negative">Marked invalid</dd>':'');
  const findings = (bib.findings || []).filter(f=>f && (f.statement || f.supporting_text)).map(f=>`<li>${esc(f.statement || '')}${f.supporting_text?'<blockquote>'+esc(f.supporting_text)+'</blockquote>':''}</li>`).join('');
  const term = d => d.term_id ? link('terms',d.term_id,d.term_label || d.term_id) : '';
  const annotations = review.annotations.map(a=>`<li>${a.negated?'<span class="negative">NOT</span> ':''}${term(a)} <span class="muted">${esc(a.evidence_type || '')}</span> ${a.action?`<span class="action ${actionClass(a.action)}">${esc(a.action.replaceAll('_',' '))}</span>`:''}</li>`).join('');
  const supports = review.supports.map(s=>`<div class="support"><span class="muted">${esc(s.section.replaceAll('_',' '))}</span>${s.term_id?' · '+term(s):''}${s.supporting_text?'<blockquote>'+esc(s.supporting_text)+'</blockquote>':''}</div>`).join('');
  return `<article class="gene-section reference-review"><h3>${link('genes',review.gene_id,review.symbol)} <a class="fine" href="/genes/${encodeURIComponent(review.gene_id)}#references">review references →</a></h3>
    ${judgement?'<dl class="structured">'+judgement+'</dl>':''}
    ${findings?'<h4>Findings recorded</h4><ul class="structured-list">'+findings+'</ul>':''}
    ${annotations?'<h4>Annotations reviewed or proposed from this reference</h4><ul class="structured-list">'+annotations+'</ul>':''}
    ${supports?'<h4>Quoted as support</h4>'+supports:''}
    <p class="fine mono">${esc(review.path)}</p></article>`;
}
(async()=>{
  const from=new URLSearchParams(location.search).get('from');
  $('#back-results').href=from?.startsWith('?')?'/'+from:'/?collection=references';
  try {
    const id=decodeURIComponent(location.pathname.split('/').slice(2).join('/'));
    const response=await fetch('/api/references/'+encodeURIComponent(id));
    const data=await response.json(); if(!response.ok) throw new Error(data.detail || 'Reference unavailable');
    const r=data.record, p=data.publication, a=data.annotations;
    document.title=r.label+' · AmiGO 3';
    $('#resource-kind').textContent=['Reference',r.kind,r.year].filter(Boolean).join(' · ');
    $('#resource-title').textContent=r.label;
    $('#resource-id').textContent=r.id;
    AmigoDetail.description($('#resource-description'),r.description);
    const usage=[r.annotation_count?`${number(r.annotation_count)} annotations on ${number(r.gene_count)} genes`:'',r.review_count?`cited in ${number(r.review_count)} gene reviews`:'',r.model_ids.length?`${number(r.model_ids.length)} GO-CAMs`:'',r.module_ids.length?`${number(r.module_ids.length)} modules`:'',r.family_ids.length?`${number(r.family_ids.length)} families`:''].filter(Boolean);
    const overview=`<section id="reference-overview" class="gene-section"><h2>Overview</h2><dl class="structured">
      ${p?.authors?.length?'<dt>Authors</dt><dd>'+esc(p.authors.join(', '))+'</dd>':''}
      ${r.journal?'<dt>Journal</dt><dd>'+esc(r.journal)+(r.year?' ('+esc(r.year)+')':'')+'</dd>':''}
      ${r.publication_type?'<dt>Publication type</dt><dd>'+esc(r.publication_type.replaceAll('_',' ').toLowerCase())+'</dd>':''}
      <dt>Links</dt><dd>${externalLinks(r).join(' · ') || '<span class="muted">No resolver known for this identifier type</span>'}</dd>
      <dt>Used by</dt><dd>${usage.join(' · ') || '<span class="muted">No uses in this snapshot</span>'}</dd>
      ${r.evidence_codes.length?'<dt>Evidence codes</dt><dd>'+r.evidence_codes.map(esc).join(' · ')+'</dd>':''}
      ${r.annotation_species.length?'<dt>Species</dt><dd>'+r.annotation_species.map(esc).join(' · ')+'</dd>':''}
      ${r.assigned_by_groups.length?'<dt>Assigned by</dt><dd>'+r.assigned_by_groups.map(esc).join(' · ')+'</dd>':''}
    </dl>${p?.abstract?'<h3>Abstract</h3><div class="reference-abstract">'+esc(p.abstract)+'</div><p class="section-note">Abstract as cached in the review repository from PubMed.</p>':''}</section>`;
    const annotations=`<section id="reference-annotations" class="gene-section"><h2>GO annotations</h2>
      ${r.annotation_count?`<p>${number(r.annotation_count)} annotations · ${number(r.gene_count)} genes · ${number(r.term_count)} GO terms. <a href="${esc(annotationsURL(r.id))}">Browse and filter all annotations →</a></p>
      <p class="section-note">Evidence: ${a.evidence.map(e=>esc(e.value)+' ('+number(e.count)+')').join(' · ')}</p>
      <h3>Genes${r.gene_count>a.genes.length?` (top ${number(a.genes.length)} of ${number(r.gene_count)})`:''}</h3>
      ${table(['Gene','Species','Annotations','GO terms'],a.genes.map(g=>[link('genes',g.id,g.symbol),esc(g.species),number(g.annotation_count),g.term_ids.slice(0,8).map(t=>link('terms',t)).join(' ')+(g.term_ids.length>8?' …':'')]))}
      <h3>GO terms${r.term_count>a.terms.length?` (top ${number(a.terms.length)} of ${number(r.term_count)})`:''}</h3>
      ${table(['Term','Aspect','Annotations','Genes'],a.terms.map(t=>[link('terms',t.id,t.label)+' <span class="mono fine">'+esc(t.id)+'</span>',esc(t.aspect),number(t.annotation_count),number(t.gene_count)]))}`
      :'<p class="section-note">No GO annotation in this snapshot cites this reference.</p>'}</section>`;
    const reviews=`<section id="reference-reviews" class="gene-section"><h2>Gene reviews</h2><p class="section-note">How AI gene reviews in the repository use this reference: their assessment of it, annotations reviewed from it, and quotations taken from it. Review decisions are not applied to GO annotations.</p>${data.reviews.map(reviewCard).join('') || '<p class="section-note">No gene review cites this reference.</p>'}</section>`;
    const labels={gocams:'GO-CAMs',modules:'Modules',families:'Families'};
    const related=`<section id="reference-related" class="gene-section"><h2>Models & modules</h2><p class="section-note">Records whose evidence cites this reference.</p>${Object.entries(data.related).map(([c,items])=>`<h3>${esc(labels[c]||c)} (${number(items.length)})</h3><ul>${items.map(x=>'<li>'+link(c,x.id,x.label)+'</li>').join('')}</ul>`).join('') || '<p class="section-note">None recorded.</p>'}</section>`;
    const provenance=`<section id="reference-provenance" class="gene-section"><h2>Provenance</h2>${p?`<p class="mono">${esc(p.source_path)}</p><p class="checksum">SHA-256: ${esc(p.sha256)}</p>`:'<p class="section-note">No cached publication for this reference; the title, if any, comes from a citing review.</p>'}<p class="section-note">Counts and uses belong to snapshot ${esc(data.build_id)}.</p></section>`;
    $('#resource-body').innerHTML=overview+annotations+reviews+related+provenance;
    $('#build').textContent='Snapshot '+data.build_id;
    $('#resource-loading').hidden=true;$('#resource-content').hidden=false;
    const relatedCount=Object.values(data.related).reduce((n,x)=>n+x.length,0);
    AmigoTabs.create({nav:$('#resource-nav'),specs:[
      {id:'reference-overview',label:'Overview',panel:$('#reference-overview')},
      {id:'reference-annotations',label:'Annotations',count:r.annotation_count,panel:$('#reference-annotations')},
      {id:'reference-reviews',label:'Gene reviews',count:data.reviews.length,panel:$('#reference-reviews')},
      {id:'reference-related',label:'Models & modules',count:relatedCount,panel:$('#reference-related')},
      {id:'reference-provenance',label:'Provenance',panel:$('#reference-provenance')}
    ]});
  } catch(e) {$('#resource-loading').hidden=true;$('#resource-error').hidden=false;$('#resource-error').textContent=e.message;}
})();
