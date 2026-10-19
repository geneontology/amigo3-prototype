const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const link = (type,item) => `<a href="/${type}/${encodeURIComponent(item.id)}">${esc(item.label)}</a>`;
const fileLink = f => f.available?`<a href="${esc(f.url)}" download>${esc(f.filename)}</a>`:`<span>${esc(f.filename)} <small class="muted">(not in source snapshot)</small></span>`;
(async()=>{
  const from=new URLSearchParams(location.search).get('from');
  $('#back-results').href=from?.startsWith('?')?'/'+from:'/?collection=reports';
  try {
    const id=decodeURIComponent(location.pathname.split('/').slice(2).join('/'));
    const response=await fetch('/api/reports/'+encodeURIComponent(id));
    const data=await response.json(); if(!response.ok) throw new Error(data.detail || 'Report unavailable');
    const r=data.record;
    document.title=r.label+' · AmiGO 3';
    $('#resource-kind').textContent=r.kind+' · '+r.provider;
    $('#resource-title').textContent=r.label;
    $('#resource-id').textContent=r.subject_label+(r.organism?' · '+r.organism:'');
    const subjects=[...data.genes.map(x=>link('genes',x)),...data.modules.map(x=>link('modules',x))];
    const projects=(data.related?.projects?.items || []).map(x=>link('projects',x));
    const contents=data.toc.filter(x=>x.level<=3).map(x=>`<li class="toc-level-${x.level}"><a href="#${esc(x.id)}">${esc(x.label)}</a></li>`).join('');
    $('#resource-body').innerHTML=`<div id="report-narrative">
      <section class="gene-section report-context"><dl class="structured"><dt>Research provider</dt><dd>${esc(r.provider)}${r.model?' · '+esc(r.model):''}</dd><dt>Created</dt><dd>${esc(r.created_at.slice(0,19).replace('T',' ') || 'Not recorded')}</dd>${projects.length?'<dt>Projects</dt><dd>'+projects.join(' · ')+'</dd>':''}<dt>Subject</dt><dd>${subjects.join(' · ') || esc(r.subject_label)}${r.subject_ids.length?'<br><span class="mono">'+r.subject_ids.map(esc).join(' · ')+'</span>':''}</dd></dl><p class="section-note">Research report from the repository. Conclusions and hypotheses are preserved as written; they are not automatically applied as GO annotations.</p></section>
      <div class="report-layout"><aside class="report-contents"><details open><summary>On this page</summary><ol>${contents || '<li><a href="#report-text">Report</a></li>'}</ol></details></aside><article id="report-text" class="gene-section report-prose" aria-label="Research report">${data.html || '<p>No report body recorded.</p>'}</article></div></div>
      <section id="report-files" class="gene-section"><h2>Files & citations</h2><p>${fileLink(data.source)} <span class="muted">— original Markdown</span></p>${data.files.length?'<ul class="report-files">'+data.files.map(f=>`<li>${fileLink(f)} <span class="muted">${esc(f.role)}</span>${f.sha256?'<details><summary>Checksum</summary><code class="checksum">'+esc(f.sha256)+'</code></details>':''}</li>`).join('')+'</ul>':'<p>No separate artifacts or citation files recorded.</p>'}</section>
      <section id="report-source" class="gene-section"><h2>Provenance</h2><p class="mono">${esc(r.source_path)}</p><p class="checksum">SHA-256: ${esc(data.source.sha256)}</p><p class="section-note">Report and downloadable files belong to snapshot ${esc(data.build_id)}.</p>${data.prompt?'<details><summary>Original question & context</summary><pre>'+esc(data.prompt)+'</pre></details>':''}<details><summary>Complete source metadata</summary><pre>${esc(JSON.stringify(data.metadata,null,2))}</pre></details></section>`;
    AmigoDetail.description($('#resource-description'),AmigoDetail.narrativeDescription($('#report-text')) || r.description);
    $('#build').textContent='Snapshot '+data.build_id;
    $('#resource-loading').hidden=true;$('#resource-content').hidden=false;
    AmigoTabs.create({nav:$('#resource-nav'),specs:[
      {id:'report-text',label:'Report',panel:$('#report-narrative')},
      {id:'report-files',label:'Files & citations',count:data.files.length+1,panel:$('#report-files')},
      {id:'report-source',label:'Provenance',panel:$('#report-source')}
    ]});
  } catch(e) {$('#resource-loading').hidden=true;$('#resource-error').hidden=false;$('#resource-error').textContent=e.message;}
})();
