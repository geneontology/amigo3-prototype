/* Module satisfiability: score a module against one species and propose gap-filling annotations. */
const AmigoSatisfiability = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const STATUS = {
    satisfied:['Satisfied','A gene of this species has the required function, or is a named participant'],
    gap:['Gap · candidates','No gene has the required function; candidates are proposed below'],
    missing:['Missing','No gene and no gap-filling candidate in this species'],
    unscorable:['Not scorable','No molecular function or concrete participant to test'],
    not_expected:['Not expected','Outside this step\'s taxon scope; no candidates are proposed']
  };
  const BASIS = {
    broader_term:'annotated to a broader term',
    subfamily_member:'member of the same PANTHER subfamily',
    family_member:'member of the PANTHER family',
    related_activity:'related activity under the same parent',
    pathway_context:'annotated to the pathway process, no step assigned'
  };
  let state = {};
  const badge = (status, tier) => `<span class="sat-badge sat-${esc(status)}" title="${esc(STATUS[status]?.[1])}">${esc(STATUS[status]?.[0] || status)}</span>${status==='satisfied' && tier?` <span class="sat-tier">${esc(tierLabel(tier))}</span>`:''}`;
  const tierLabel = t => ({4:'experimental',3:'curated participant',2:'curated computational',1:'IEA only'})[t] || '';
  const gene = g => `<a href="/genes/${encodeURIComponent(g.gene_id)}">${esc(g.symbol || g.gene_id)}</a>`;
  function basis(b) {
    const d = b.detail || {};
    const ref = state.ref;
    if (b.kind === 'broader_term') return `${BASIS[b.kind]}: ${ref(d.term.id, d.term.label)}`;
    if (b.kind === 'related_activity') return `${BASIS[b.kind]} ${ref(d.ancestor.id, d.ancestor.label)}: ${ref(d.term.id, d.term.label)}`;
    if (b.kind === 'pathway_context') return `${BASIS[b.kind]}: ${ref(d.annotated.id, d.annotated.label)}`;
    return `${BASIS[b.kind]} ${ref(d.subfamily || d.family)} (${esc(d.member)})`;
  }
  function scope(s) {
    if (s.kind === 'taxon') return `${esc(s.node)} is scoped to ${s.taxa.map(t => `${esc(t.label)} <small class="muted">${esc(t.id)}</small>`).join(', ')}`;
    const from = [];
    if (s.species.length) from.push('its curated participants are from ' + s.species.map(x => esc(AmigoSpecies.label(x))).join(', '));
    if (s.families.length) from.push(s.families.map(f => state.ref(f)).join(', ') + ' has no member in this species');
    return `${esc(s.node)} is an alternative by ${esc(s.axis)}; ${from.join('; ')}`;
  }
  function annoton(a) {
    const fn = a.function ? state.ref(a.function.id, a.function.label) : '<span class="muted">no molecular function</span>';
    let body = '';
    if (a.genes.length) {
      body = `<ul class="sat-genes">${a.genes.map(g => `<li>${gene(g)} <span class="sat-tier">${esc(g.tier_label)}</span>${g.evidence.length?' <small class="muted">'+esc(g.evidence.join(', '))+'</small>':''}${g.named_as?.length?' <small class="muted">named in module as '+esc(g.named_as.join(', '))+'</small>':''}${g.terms.length && !g.terms.some(t=>t.id===a.function?.id)?'<br><small>via '+g.terms.map(t=>state.ref(t.id,t.label)).join(', ')+'</small>':''}</li>`).join('')}</ul>`;
    } else if (a.scope) {
      body = `<p class="section-note sat-scope">Not expected in this species: ${scope(a.scope)}. No candidates are proposed.</p>`;
    } else if (a.candidates.length) {
      body = `<p class="section-note">${a.candidate_total} candidate${a.candidate_total===1?'':'s'}; top ${a.candidates.length} shown. Candidates are hypotheses, not annotations.</p><ol class="sat-candidates">${a.candidates.map(c => `<li>${gene(c)} <span class="sat-score" title="Sum of distinct support weights">score ${c.score}</span>${c.assigned_elsewhere?' <small class="muted">(already fills another step)</small>':''}<ul>${c.basis.map(b => '<li>'+basis(b)+'</li>').join('')}</ul></li>`).join('')}</ol>`;
    }
    return `<div class="sat-annoton sat-${esc(a.status)}"><div class="sat-head">${badge(a.status, a.tier)} <strong>${esc(a.label)}</strong>${!['satisfied','not_expected'].includes(a.status) && !a.needed?' <span class="muted">· not blocking (alternative or optional)</span>':''}</div><div class="sat-fn">${fn}</div>${body}</div>`;
  }
  function node(n, depth = 0) {
    const title = `${badge(n.status, n.tier)} <strong>${esc(n.label)}</strong>${n.role?' <span class="muted">· '+esc(n.role)+'</span>':''}${n.optional?' <span class="muted">(optional)</span>':''}`;
    const inner = n.annotons.map(annoton).join('')
      + n.parts.map(p => node(p, depth + 1)).join('')
      + n.variant_sets.map(v => `<fieldset class="module-variants"><legend>${esc(v.label)} · ${esc(v.selection || 'alternatives')}</legend>${badge(v.status, v.tier)}${v.optional?' <span class="muted">(optional set)</span>':''}${v.variants.map(x => node(x, depth + 1)).join('')}</fieldset>`).join('');
    const open = depth < 1 || !['satisfied','not_expected'].includes(n.status);
    return `<details class="module-node sat-node sat-${esc(n.status)}" ${open?'open':''}><summary>${title}</summary><div class="module-node-body">${inner}</div></details>`;
  }
  function proposalRows(result) {
    return result.proposals.map(p => [result.species, p.gene_id, p.symbol, p.relation, p.term.id, p.term.label, p.annoton_id, p.score, p.basis.map(b => b.kind).join('|'), p.needed]);
  }
  function render(result) {
    const counts = result.counts;
    const summary = `<div class="sat-summary sat-${esc(result.status)}"><div>${badge(result.status, result.tier)}<p><strong>${esc(AmigoSpecies.label(result.species))}</strong> · ${esc(result.evidence_label)}</p></div><dl>${['satisfied','gap','missing','unscorable','not_expected'].map(k => `<div><dt>${esc(STATUS[k][0])}</dt><dd>${counts[k] || 0}</dd></div>`).join('')}</dl></div>`;
    const proposals = result.proposals.length
      ? `<div class="table-scroll"><table class="record-table"><thead><tr><th scope="col">Step</th><th scope="col">Blocks module</th><th scope="col">Gene</th><th scope="col">Proposed annotation</th><th scope="col" class="num">Score</th><th scope="col">Support</th></tr></thead><tbody>${result.proposals.map(p => `<tr><td>${esc(p.annoton_label)}</td><td>${p.needed?'<strong>yes</strong>':'<span class="muted">no · alternative or optional</span>'}</td><td>${gene(p)}</td><td>${esc(p.relation)} ${state.ref(p.term.id, p.term.label)}</td><td class="num">${p.score}</td><td>${p.basis.map(b => esc(BASIS[b.kind])).join('<br>')}</td></tr>`).join('')}</tbody></table></div>`
      : '<p class="section-note">No gap-filling proposals: every scorable step is satisfied or has no candidate.</p>';
    state.el.querySelector('#sat-results').innerHTML = summary
      + `<h3>Steps</h3>${node(result.tree)}`
      + `<div class="section-heading"><h3>Gap-filling proposals (${result.proposals.length})</h3>${result.proposals.length?'<button type="button" id="sat-download" class="text-button">Download TSV</button>':''}</div>${proposals}`
      + `<details class="record-details"><summary>Method</summary><p class="preserve-lines">${esc(result.method)}</p></details>`;
    const download = state.el.querySelector('#sat-download');
    if (download) download.onclick = () => {
      const header = ['species','gene_id','symbol','relation','go_id','go_label','module_step','score','basis','blocks_module'];
      const tsv = [header, ...proposalRows(result)].map(r => r.map(x => String(x ?? '').replace(/[\t\n]/g, ' ')).join('\t')).join('\n') + '\n';
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([tsv], {type:'text/tab-separated-values'}));
      a.download = `${state.record.id.replace(/[^\w.-]+/g,'_')}-${result.species}-gapfill.tsv`;
      a.click(); URL.revokeObjectURL(a.href);
    };
  }
  async function run() {
    const species = state.el.querySelector('#sat-species').value, evidence = state.el.querySelector('#sat-evidence').value;
    const params = new URLSearchParams(location.search); params.set('species', species); params.set('evidence', evidence);
    history.replaceState(null, '', location.pathname + '?' + params + location.hash);
    const status = state.el.querySelector('#sat-status'); status.textContent = 'Evaluating…';
    try {
      const result = await state.api('/api/satisfiability', {module: state.record.id, species, evidence, view: state.build});
      status.textContent = '';
      render(result);
    } catch (e) { status.textContent = e.message; }
  }
  return {
    shell() {
      return `<p class="section-note">Choose a loaded species (one strain for P. putida KT2440) to test whether its GO annotations realize each step of this module. Steps need all of their parts and annotons; a set of alternatives needs one. Gaps get ranked candidate genes from broader-term annotations, PANTHER family membership and pathway-process annotations; steps outside their taxon scope are shown as not expected instead. Proposals are not applied to any annotation.</p><form id="sat-form" class="annotation-controls"><select id="sat-species" aria-label="Species"></select><select id="sat-evidence" aria-label="Evidence"></select><button type="submit" class="primary-button">Evaluate</button></form><p id="sat-status" role="status" aria-live="polite"></p><div id="sat-results"></div>`;
    },
    async mount({el, record, build, api, ref}) {
      if (state.el === el) return;
      state = {el, record, build, api, ref};
      const options = await api('/api/satisfiability?' + new URLSearchParams({module: record.id}));
      const params = new URLSearchParams(location.search);
      const species = options.species.includes(params.get('species')) ? params.get('species') : (options.default_species || options.species[0]);
      el.querySelector('#sat-species').innerHTML = options.species.map(s => `<option value="${esc(s)}" ${s===species?'selected':''}>${esc(AmigoSpecies.label(s))}</option>`).join('');
      el.querySelector('#sat-evidence').innerHTML = Object.entries(options.evidence).map(([k, v]) => `<option value="${esc(k)}" ${k===params.get('evidence')?'selected':''}>${esc(v)}</option>`).join('');
      el.querySelector('#sat-form').onsubmit = e => { e.preventDefault(); run(); };
      run();
    }
  };
})();
