/* Shared external-claim rendering; comparisons remain scoped to each protein. */
const AmigoPredictions = (() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = n => Number(n || 0).toLocaleString();
  const termLink = (id, label = id) => `<a href="/terms/${encodeURIComponent(id)}">${esc(label)}</a>`;
  const labels = {aspect:{F:'Molecular function',P:'Biological process',C:'Cellular component'},model_type:{HMM:'HMM families',ArchId:'CDD architecture (ArchId)',BlastRule:'NCBI BlastRules',cluster:'Cluster identifiers',Unclassified:'Other identifiers',None:'No model identifier'},mapping_status:{mapped:'Mapped',identity:'Same identifier',unmapped:'Unmapped',ambiguous:'Ambiguous',out_of_scope:'Outside catalog'}};
  const proteinLink = id => id.startsWith('RefSeq:') ? `<a href="https://www.ncbi.nlm.nih.gov/protein/${encodeURIComponent(id.slice(7))}" target="_blank" rel="noopener">${esc(id)} ↗</a>` : esc(id);
  function geneLinks(refs) {
    if (!refs.length) return '';
    return `<details class="ncbi-gene-links"><summary>NCBI Gene cross-references (${refs.length})</summary><ul>${refs.map(r=>`<li><a href="https://www.ncbi.nlm.nih.gov/gene/${encodeURIComponent(r.id.replace('NCBIGene:',''))}" target="_blank" rel="noopener">${esc(r.id)} ↗</a><small>${esc(r.label)} · ${esc(r.organism || 'Taxon unverified')} ${esc(r.taxon_id)}</small><strong>${r.taxon_status==='same_taxon'?'Same taxon':r.taxon_status==='other_taxon'?'Different taxon — cross-reference only':'Taxon unverified'}</strong><small>${esc(r.source)}</small></li>`).join('')}</ul></details>`;
  }
  function valueLabel(field, value, statuses) { return (field==='status'?statuses[value]:labels[field]?.[value]) || value; }
  function facets(container, data, selected, fieldLabels, statuses, onChange) {
    container.innerHTML = Object.entries(fieldLabels).map(([field,label])=>`<fieldset class="prediction-facet"><legend>${esc(label)}</legend>${(data[field] || []).map(b=>`<label><input type="checkbox" data-field="${field}" value="${esc(b.value)}" ${selected[field]?.includes(b.value)?'checked':''} ${!b.count && !selected[field]?.includes(b.value)?'disabled':''}><span>${esc(valueLabel(field,b.value,statuses))}</span><span class="facet-count">${number(b.count)}</span></label>`).join('')}</fieldset>`).join('');
    container.querySelectorAll('input').forEach(input=>input.addEventListener('change',()=>onChange(input.dataset.field,input.value,input.checked)));
  }
  function row(row, statuses) {
    const protein = row.gene_id ? `<a href="/genes/${encodeURIComponent(row.gene_id)}">${esc(row.symbol || row.source_subject_id)}</a>` : esc(row.symbol || row.source_subject_id);
    const evidence = row.baseline_matches.map(b => `<li>${termLink(b.term,b.label)} · ${esc(b.negation ? 'NOT ' : '')}${esc(b.relation)} · ${esc(b.evidence_code)}<br><small>${esc(b.source_id)} · ${esc(b.with_or_from)} · ${esc(b.references.join(' | '))}</small></li>`).join('');
    return `<tr><td>${protein}<small>${proteinLink(row.source_subject_id)}</small><small>→ ${esc(row.target_subject_id || 'Unresolved')}</small><small>${esc(row.name)}</small>${geneLinks(row.ncbi_gene_xrefs || [])}</td><td>${row.term_available ? termLink(row.term_id,row.term_label) : esc(row.term_id)}<small>${esc(row.term_id)} · ${esc(row.aspect)} · ${row.negation ? 'NOT ' : ''}${esc(row.relation)}</small>${row.term_id !== row.ontology_class_id ? `<small>Original: ${esc(row.ontology_class_id)}</small>` : ''}</td><td><span class="status status-${esc(row.status)}">${esc(statuses[row.status])}</span>${row.assertion_match ? '<small>Assertion also matches</small>' : ''}${row.opposite_assertion_ids.length ? '<small>⚑ Opposite-polarity assertion; inspect context</small>' : ''}</td><td>${esc(row.evidence_code)} · ${esc(row.with_or_from || 'No model ID supplied')}<small>${esc(row.references.join(' | '))}</small><details><summary>Baseline matches (${row.baseline_matches.length})</summary>${evidence ? `<ul>${evidence}</ul>` : '<p>No comparable baseline term.</p>'}</details><details><summary>Original row and mapping</summary><pre>${esc(row.raw_record)}</pre><pre>${esc(JSON.stringify(row.mapping_evidence,null,2))}</pre><small>Source line ${row.source_line}; extension: ${esc(row.annotation_extensions || 'none')}; product form: ${esc(row.gene_product_form_id || 'none')}</small></details></td></tr>`;
  }
  return {row, geneLinks, facets, valueLabel};
})();
