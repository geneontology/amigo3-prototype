const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = n => Number(n).toLocaleString();
const names = {genes:'Referenced genes',terms:'Referenced terms',gocams:'Referenced GO-CAMs',modules:'Referenced modules',families:'Referenced families',reports:'Reports',projects:'Related projects',rules:'Rules & mappings'};
// Link text or project-relative path; the path disambiguates the many index.html artifacts.
const label = r => r.label || r.title || r.filename || r.path;
const where = r => r.display_path && r.display_path!==label(r)?` <small class="mono muted resource-path">${esc(r.display_path)}</small>`:'';
const resource = r => (!r.available?`${esc(label(r))} <small>(not in snapshot)</small>`:`<a href="${esc(r.url)}" ${r.external?'rel="noopener noreferrer"':r.sha256?'download':''}>${esc(label(r))}</a>${r.external?' ↗':''}`)+where(r);
const summary = (r,s) => {
  const facts=[['Status',esc(r.maturity.replaceAll('_',' '))],...(s.dates||[]).map(d=>[d.label,esc(d.value)]),...(s.people||[]).map(p=>[p.label,p.values.map(esc).join(', ')])];
  if(r.tags.length)facts.push(['Tags',r.tags.map(t=>`<span class="chip">${esc(t)}</span>`).join(' ')]);
  if(r.project_species.length)facts.push(['Species',r.project_species.map(esc).join(' · ')]);
  const primary=(s.primary||[]).map(x=>`<li>${resource(x)}</li>`).join('');
  return (primary?`<div id="project-primary"><p class="eyebrow">${s.basis==='inferred'?'Main artifact (top-level linked page)':'Main artifact'}</p><ul>${primary}</ul></div>`:'')+`<dl class="project-facts">${facts.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>`;
};
(async()=>{
  const from=new URLSearchParams(location.search).get('from');
  $('#back-results').href=from?.startsWith('?')?'/'+from:'/?collection=projects';
  try {
    const id=decodeURIComponent(location.pathname.split('/').slice(2).join('/'));
    const response=await fetch('/api/projects/'+encodeURIComponent(id));
    const data=await response.json();if(!response.ok)throw new Error(data.detail || 'Project unavailable');
    const r=data.record;
    document.title=r.label+' · AmiGO 3';
    $('#resource-kind').textContent='Project · '+r.maturity.replaceAll('_',' ');
    $('#resource-title').textContent=r.label;$('#resource-id').textContent=r.id;
    const s=data.summary || {};$('#project-summary').innerHTML=summary(r,s);$('#project-summary').hidden=false;
    // Main artifacts lead the resource list, so they are not lost among dozens of files.
    const primaryPaths=new Set((s.primary||[]).map(x=>x.path));
    const resources=[...data.resources.filter(f=>primaryPaths.has(f.path)),...data.resources.filter(f=>!primaryPaths.has(f.path))];
    const related=Object.entries(data.related).map(([type,g])=>{
      const ids=r[g.field];const browse='/?'+new URLSearchParams({collection:type,filters:JSON.stringify([{id:'project-records',field:type==='rules'?'project_ids':'id',values:type==='rules'?[r.id]:ids.slice(0,200)}])});
      return `<section><h3>${names[type]} (${number(g.total)})</h3><ul>${g.items.map(x=>`<li><a href="/${type}/${encodeURIComponent(x.id)}">${esc(x.label)}</a> <small class="mono muted">${esc(x.id)}</small></li>`).join('')}</ul>${g.total>g.items.length?(type==='rules' || g.total<=200)?`<a href="${esc(browse)}">Browse all ${number(g.total)} →</a>`:'<p>First 100 records shown. Complete IDs are in the source metadata below.</p>':''}</section>`;
    }).join('');
    $('#resource-body').innerHTML=`<div id="project-narrative">${r.project_collections.length || r.declared_genes.length?`<section class="gene-section"><dl class="structured">${r.project_collections.length?'<dt>Collections</dt><dd>'+r.project_collections.map(esc).join(' · ')+'</dd>':''}${r.declared_genes.length?'<dt>Declared genes</dt><dd>'+r.declared_genes.map(esc).join(' · ')+'</dd>':''}</dl></section>`:''}
    <div class="report-layout"><aside class="report-contents"><details open><summary>On this page</summary><ol>${data.toc.filter(x=>x.level<=3).map(x=>`<li class="toc-level-${x.level}"><a href="#${esc(x.id)}">${esc(x.label)}</a></li>`).join('')}</ol></details></aside><article id="project-text" class="gene-section report-prose" aria-label="Project narrative">${data.html}</article></div></div>
    <section id="project-related" class="gene-section"><h2>Related records</h2><p class="section-note">Links reflect declared genes, identifiers and source references. They do not imply completed reviews or project membership for every mentioned record. Gene links cover the loaded species catalogs.</p>${related || '<p>No linked records in this snapshot.</p>'}</section>
    <section id="project-resources" class="gene-section"><h2>Resources</h2><ul class="report-files">${resources.map(f=>`<li>${resource(f)}${primaryPaths.has(f.path)?' <span class="chip">Main artifact</span>':''}${f.sha256?'<details><summary>Checksum</summary><code class="checksum">'+esc(f.sha256)+'</code></details>':''}</li>`).join('') || '<li>No additional linked resources.</li>'}</ul></section>
    <section id="project-source" class="gene-section"><h2>Provenance</h2><p class="mono">${esc(r.source_path)}</p><p>${resource(data.source)} — original Markdown</p><p class="checksum">SHA-256: ${esc(data.source.sha256)}</p><p>Snapshot ${esc(data.build_id)}</p><details><summary>Source metadata & referenced identifiers</summary><pre>${esc(JSON.stringify({metadata:data.metadata,referenced_ids:Object.fromEntries(Object.values(data.related).map(g=>[g.field,r[g.field]]))},null,2))}</pre></details></section>`;
    AmigoDetail.description($('#resource-description'),AmigoDetail.narrativeDescription($('#project-text')) || r.description);
    $('#build').textContent='Snapshot '+data.build_id;$('#resource-loading').hidden=true;$('#resource-content').hidden=false;
    AmigoTabs.create({nav:$('#resource-nav'),specs:[
      {id:'project-text',label:'Project',panel:$('#project-narrative')},
      {id:'project-related',label:'Related records',count:Object.values(data.related).reduce((total,group)=>total+group.total,0),panel:$('#project-related')},
      {id:'project-resources',label:'Resources',count:data.resources.length,panel:$('#project-resources')},
      {id:'project-source',label:'Provenance',panel:$('#project-source')}
    ]});
  }catch(e){$('#resource-loading').hidden=true;$('#resource-error').hidden=false;$('#resource-error').textContent=e.message;}
})();
