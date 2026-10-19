const AmigoGenePredictions = (() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function mount(gene, build) {
    let selected=new Set(), offset=0, serial=0;
    const size=50;
    async function load() {
      const request=++serial;
      $('gene-prediction-error').hidden=true;
      $('gene-prediction-count').textContent='Loading predictions…';
      $('gene-prediction-previous').disabled=true; $('gene-prediction-next').disabled=true;
      const params=new URLSearchParams({view:build,status:[...selected].join(','),offset,size});
      try {
        const response=await fetch(`/api/predictions/gene/${encodeURIComponent(gene)}?${params}`);
        const result=await response.json();
        if (!response.ok) throw new Error(result.detail || 'Unable to load predictions');
        if (request!==serial) return;
        const categories=result.facets.status || [];
        const total=categories.reduce((n,b)=>n+b.count,0);
        $('tab-predictions').textContent=`Predictions (${total.toLocaleString()})`;
        $('gene-prediction-count').textContent=result.total ? `${offset+1}–${offset+result.rows.length} of ${result.total} source annotation rows` : total ? 'No predictions match these categories.' : 'No preloaded predictions are linked to this gene in this build.';
        $('gene-prediction-categories').innerHTML=categories.filter(b=>b.count || selected.has(b.value)).map(b=>`<button type="button" data-status="${esc(b.value)}" aria-pressed="${selected.has(b.value)}">${esc(result.statuses[b.value])} (${b.count})</button>`).join('')+(selected.size?'<button type="button" data-clear>All categories</button>':'');
        $('gene-prediction-categories').querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>{const value=button.dataset.status; if(!value) selected.clear(); else if(selected.has(value)) selected.delete(value); else selected.add(value); offset=0; load();}));
        const sets=new Map(result.sets.map(s=>[s.id,s.label]));
        $('gene-prediction-rows').innerHTML=result.rows.map(row=>AmigoPredictions.row(row,result.statuses).replace('<td>',`<td><strong>${esc(sets.get(row.set_id)||row.set_id)}</strong><br>`)).join('');
        const refs=[...new Map(result.rows.flatMap(row=>row.ncbi_gene_xrefs || []).map(r=>[r.id,r])).values()];
        $('gene-prediction-identifiers').innerHTML=AmigoPredictions.geneLinks(refs);
        $('gene-prediction-links').innerHTML=result.sets.map(s=>{
          const p=new URLSearchParams({set:s.id,gene,view:build,status:[...selected].join(',')});
          return `<a href="/predictions?${esc(p.toString())}">Explore ${esc(s.label)} with facets →</a><a href="/api/predictions/${encodeURIComponent(s.id)}/export?${esc(p.toString())}">Download this gene’s rows</a>`;
        }).join('');
        $('gene-prediction-previous').disabled=offset===0;
        $('gene-prediction-next').disabled=offset+result.rows.length>=result.total;
      } catch(e) { if(request===serial) { $('gene-prediction-error').textContent=e.message; $('gene-prediction-error').hidden=false; $('gene-prediction-count').textContent='Predictions unavailable'; } }
    }
    $('gene-prediction-previous').addEventListener('click',()=>{offset=Math.max(0,offset-size);load();});
    $('gene-prediction-next').addEventListener('click',()=>{offset+=size;load();});
    load();
  }
  return {mount};
})();
