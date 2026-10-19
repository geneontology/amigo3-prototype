/* Graphs use the exact contributing pairs from the pinned enrichment request. */
window.AmigoEnrichmentGraphs = (() => {
  const el = id => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  let active = params.get('result_view') === 'graphs' || (params.get('method') === 'neighborhood' && params.get('result_view') !== 'table');
  let selected = params.get('graph_term') || '';
  let fullContext = params.get('network_context') === 'full';
  let result, rows = [], cy, detail, generation = 0, library, drawnTerm = '';
  let loadEdges, renderEvidence, onState;
  let overviewCy, overviewGeneration = 0, overviewIds = new Set(), overviewThreshold;
  let overviewLimit = [10, 20, 40, 60].includes(Number(params.get('overview_top'))) ? Number(params.get('overview_top')) : 20;
  let overviewMinStudy = params.has('overview_min_study') && /^\d+$/.test(params.get('overview_min_study')) ? Math.min(200000, Number(params.get('overview_min_study'))) : 2;
  let overviewRank = ['p_value', 'study_count', 'fraction', 'fold'].includes(params.get('overview_rank')) ? params.get('overview_rank') : 'p_value';
  let overviewUseFdr = params.get('overview_fdr') === '1';
  const fdrThreshold = () => Number(el('threshold').value);
  const passesFdr = n => n.by_fdr !== null && n.by_fdr <= fdrThreshold();
  function overviewSelectionNote() {
    const term = rows.find(t => t.id === selected);
    el('overview-selection-note').textContent = term && !overviewIds.has(selected)
      ? term.label + ' is outside the overview filters or top ' + overviewLimit + '; its gene network remains available below.' : '';
  }
  function refreshOverview() { clearOverview(); drawOverview(); onState?.(); }
  el('overview-limit').value = overviewLimit;
  el('overview-min-study').value = overviewMinStudy;
  el('overview-rank').value = overviewRank;
  el('overview-use-fdr').checked = overviewUseFdr;
  const isNeighborhood = () => result?.network?.kind === 'neighborhood';
  const canvas = el('enrichment-network-canvas');
  const observer = new ResizeObserver(() => {
    if (cy && canvas.clientWidth) { cy.resize(); cy.fit(undefined, 40); }
  });
  observer.observe(canvas);
  const overviewCanvas = el('neighborhood-overview-canvas');
  new ResizeObserver(() => {
    if (overviewCy && overviewCanvas.clientWidth) { overviewCy.resize(); overviewCy.fit(undefined, 35); }
  }).observe(overviewCanvas);

  function loadLibrary() {
    if (window.cytoscape) return Promise.resolve();
    if (!library) library = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/vendor/cytoscape.min.js';
      script.onload = resolve;
      script.onerror = () => { script.remove(); library = null; reject(new Error('Could not load the graph viewer.')); };
      document.head.append(script);
    });
    return library;
  }

  function destroy() {
    generation++;
    if (cy) cy.destroy();
    cy = null; detail = null; drawnTerm = '';
    delete canvas._cy; delete canvas.dataset.ready;
    el('network-fit').disabled = el('network-png').disabled = true;
    el('network-retry').hidden = true;
    el('graph-pair').innerHTML = '<option value="">Choose a connecting pair…</option>';
    el('network-graph-inspector').textContent = 'Select a gene or pair to inspect its sources.';
    el('network-graph-status').textContent = '';
  }

  function clearOverview() {
    overviewGeneration++;
    overviewCy?.destroy(); overviewCy = null;
    delete overviewCanvas._cy; delete overviewCanvas.dataset.ready;
    el('overview-png').disabled = true;
    el('overview-status').textContent = '';
  }

  async function drawOverview() {
    if (!active || !isNeighborhood() || overviewCy) return;
    const token = ++overviewGeneration;
    const data = result.network.neighborhoods;
    const eligible = data.nodes.filter(n => n.tested && n.returned && n.study_count >= overviewMinStudy
      && (!overviewUseFdr || passesFdr(n)));
    const value = n => overviewRank === 'fraction' ? n.study_count / n.size
      : overviewRank === 'fold' ? (n.expected > 0 ? n.connections / n.expected : -Infinity) : n.study_count;
    const ordered = eligible.sort((a, b) => (overviewRank === 'p_value' ? 0 : value(b) - value(a))
      || (a.p_value ?? 2) - (b.p_value ?? 2) || b.study_count - a.study_count || a.id.localeCompare(b.id));
    const shown = ordered.slice(0, overviewLimit);
    overviewIds = new Set(shown.map(n => n.id));
    overviewSelectionNote();
    const edges = data.edges.filter(e => overviewIds.has(e.source) && overviewIds.has(e.target));
    el('overview-fdr-value').textContent = fdrThreshold();
    el('overview-fdr-legend').textContent = 'BY FDR ≤ ' + fdrThreshold();
    el('overview-status').textContent = 'Laying out neighborhoods…';
    try {
      await loadLibrary();
      if (token !== overviewGeneration) return;
      overviewCy = window.cytoscape({
        container: overviewCanvas,
        elements: [
          ...shown.map(n => {
            const bp = n.go_context?.[0]?.label || n.seeds?.slice(0, 2).map(g => g.symbol).join(', ') || 'No BP context';
            return {data: {id: n.id, label: n.label.replace('Neighborhood ', 'N')
              + ' · ' + n.study_count + '/' + n.size + '\n' + (bp.length > 85 ? bp.slice(0, 82) + '…' : bp),
              size: 30 * Math.sqrt(Math.max(0.5, n.study_count)), color: n.study_count ? '#4252af' : '#c1cbd8'},
              classes: passesFdr(n) ? 'significant' : ''};
          }),
          ...edges.map((e,i) => ({data: {id: 'between:' + i, source: e.source, target: e.target, width: 1 + Math.log1p(e.shared_genes), count: e.shared_genes}})),
        ],
        layout: {name: 'preset'},
        minZoom: 0.06, maxZoom: 4, wheelSensitivity: 0.2,
        style: [
          {selector: 'node', style: {label: 'data(label)', width: 'data(size)', height: 'data(size)', 'background-color': 'data(color)', color: '#00174f', 'font-size': 16, 'text-wrap': 'wrap', 'text-max-width': 200, 'text-valign': 'bottom', 'text-margin-y': 8, 'text-background-color': '#fbfcff', 'text-background-opacity': 0.9, 'text-background-padding': 3, 'border-color': '#fff', 'border-width': 2}},
          {selector: 'node.significant', style: {'border-color': '#28652e', 'border-width': 6}},
          {selector: 'node.untested', style: {'border-style': 'dashed', 'border-color': '#778399'}},
          {selector: 'node:selected', style: {'underlay-color': '#e7bb38', 'underlay-opacity': 0.35, 'underlay-padding': 7}},
          {selector: 'edge', style: {width: 'data(width)', 'line-color': '#b5bfcc', 'opacity': 0.18, 'curve-style': 'bezier'}},
        ],
      });
      // Rank order and a rectangular layout leave room for BP labels. Positions
      // are for reading, not a second inference about neighborhood similarity.
      const largest = Math.max(30, ...shown.map(n => 30 * Math.sqrt(Math.max(0.5, n.study_count))));
      const cellWidth = Math.max(235, largest + 30), cellHeight = largest + 125;
      const aspect = Math.max(0.5, overviewCanvas.clientWidth / Math.max(1, overviewCanvas.clientHeight));
      const columns = Math.max(1, Math.ceil(Math.sqrt(shown.length * aspect * cellHeight / cellWidth)));
      overviewCy.nodes().positions((n, i) => ({x: (i % columns) * cellWidth, y: Math.floor(i / columns) * cellHeight}));
      overviewCy.fit(undefined, 40);
      overviewCanvas._cy = overviewCy; overviewCanvas.dataset.ready = 'true';
      const present = new Map(data.nodes.map(n => [n.id,n]));
      overviewCy.on('tap', 'node', event => {
        const n = present.get(event.target.id());
        if (rows.some(t => t.id === n.id)) {
          chooseTerm(n.id);
          el('graph-term').scrollIntoView({block: 'start', behavior: 'smooth'});
        } else {
          el('overview-status').textContent = n.label + ': ' + n.size + ' background genes; ' + (n.tested ? 'outside the returned result limit.' : 'not tested: outside size filters or no usable study genes.');
        }
      });
      overviewCy.on('tap', 'edge', event => {
        el('overview-status').textContent = event.target.data('count') + ' shared genes between these overlapping neighborhoods in the analysis background; this line is not an interaction or an enrichment score.';
      });
      overviewCy.getElementById(selected).select();
      const scope = overviewUseFdr ? 'BY FDR ≤ ' + fdrThreshold() : 'exploratory; BY FDR filter off';
      el('overview-status').textContent = shown.length + ' of ' + eligible.length + ' matching returned neighborhoods shown · '
        + 'top ' + overviewLimit + ' by ' + el('overview-rank').selectedOptions[0].textContent.toLowerCase()
        + ' · at least ' + overviewMinStudy + ' study members · ' + scope + '. '
        + shown.filter(passesFdr).length + ' shown pass BY FDR ≤ ' + fdrThreshold() + '. '
        + (!shown.length ? 'No neighborhoods match. Lower the minimum study count or turn off the BY FDR filter to explore.' : 'Circle area reflects study members. BP labels are descriptive.')
        + (result.truncated ? ' Results were truncated by the API; ranking uses returned neighborhoods.' : '');
      el('overview-png').disabled = !shown.length;
    } catch (error) {
      if (token === overviewGeneration) el('overview-status').textContent = error.message + ' Use the neighborhood selector below, or Fit neighborhoods to retry.';
    }
  }

  function selectView(graphs, notify = true) {
    active = !!graphs && !!result?.network;
    for (const [id, enabled] of [['table', !active], ['graphs', active]]) {
      el(id + '-tab').setAttribute('aria-selected', String(enabled));
      el(id + '-tab').tabIndex = enabled ? 0 : -1;
      el(id + '-panel').hidden = !enabled;
    }
    if (active) { drawOverview(); draw(); }
    if (notify) onState?.();
  }

  function chooseTerm(id) {
    if (!rows.some(t => t.id === id)) return;
    selected = id;
    el('graph-term').value = selected;
    updateChartSelection();
    overviewSelectionNote();
    overviewCy?.nodes().unselect(); overviewCy?.getElementById(selected).select();
    draw();
    onState?.();
  }

  function updateChartSelection() {
    el('connection-chart').querySelectorAll('button').forEach(b => {
      b.setAttribute('aria-pressed', String(b.dataset.term === selected));
    });
  }

  function update(data, visible, callbacks) {
    ({loadEdges, renderEvidence, onState} = callbacks);
    const changed = data !== result, previousKind = result?.network?.kind;
    if (changed) { destroy(); clearOverview(); }
    if (data.network?.kind === 'neighborhood' && previousKind !== 'neighborhood') active = params.get('result_view') !== 'table';
    result = data; rows = isNeighborhood() ? data.terms : visible;
    if (isNeighborhood() && overviewThreshold !== fdrThreshold()) { clearOverview(); overviewThreshold = fdrThreshold(); }
    el('neighborhood-overview').hidden = !isNeighborhood();
    el('graph-selector-label').textContent = isNeighborhood() ? 'Expand neighborhood' : 'GO term';
    el('graphs-tab').textContent = isNeighborhood() ? 'Network' : 'Graphs';
    el('graph-neighbor-label').textContent = isNeighborhood() ? 'Neighborhood only' : 'Term only';
    el('graph-overlap-label').textContent = isNeighborhood() ? 'Study and neighborhood' : 'Study and term';
    el('neighborhood-context').hidden = !isNeighborhood();
    el('network-context-control').hidden = !isNeighborhood();
    el('network-full-context').checked = fullContext;
    el('graph-edge-description').textContent = isNeighborhood()
      ? 'Solid lines count toward the score; dashed lines are context only. Connections are functional, not necessarily physical.'
      : 'Each line counts one functional connection, not necessarily a physical interaction. Select a pair for sources.';
    if (changed) {
      el('count-chart-section').open = !isNeighborhood();
      if (isNeighborhood()) el('graph-content').append(el('count-chart-section'));
      else el('graph-content').prepend(el('count-chart-section'));
    }
    el('result-tabs').hidden = !data.network;
    el('table-panel').setAttribute('role', data.network ? 'tabpanel' : 'region');
    el('table-panel').setAttribute('aria-labelledby', data.network ? 'table-tab' : 'terms-heading');
    if (!data.network) { selectView(false, false); return; }
    if (!rows.some(t => t.id === selected)) selected = rows[0]?.id || '';
    const shown = visible.slice(0, 15);
    el('graph-filter-note').textContent = rows.length
      ? 'Chart: first ' + shown.length + ' of ' + rows.length.toLocaleString() + ' terms under the current display filter, in table order. The selector includes all ' + rows.length.toLocaleString() + '.'
      : 'No terms under the current display filter. Select “Show all returned terms” to explore the returned results.';
    if (isNeighborhood()) el('graph-filter-note').textContent = 'Explore all ' + rows.length + ' returned neighborhoods below. Table and chart: ' + visible.length + ' rows shown.';
    el('graph-content').hidden = !rows.length;
    el('graph-term').innerHTML = rows.map(t => '<option value="' + esc(t.id) + '">' + esc(t.label) + (t.seeds?.length ? ' · ' + esc(t.seeds.slice(0, 3).map(g => g.symbol).join(', ')) + (t.seeds.length > 3 ? '…' : '') : '') + ' · ' + esc(t.id) + '</option>').join('');
    el('graph-term').value = selected;
    const max = Math.max(1, ...shown.flatMap(t => [t.connections, t.expected]));
    el('connection-chart').innerHTML = shown.map(t => {
      const observed = 100 * t.connections / max, expected = 100 * t.expected / max;
      return '<button type="button" class="connection-chart-row" data-term="' + esc(t.id) + '" aria-pressed="' + (t.id === selected) + '">'
        + '<span class="connection-chart-label">' + esc(t.label) + '</span>'
        + '<span class="connection-bars" aria-hidden="true"><i class="observed-bar" style="width:' + observed + '%"></i><i class="expected-bar" style="width:' + expected + '%"></i></span>'
        + '<span class="connection-chart-value">' + t.connections + ' / ' + t.expected.toFixed(2) + '<small>observed / expected · BY ' + sci(t.by_fdr) + '</small></span></button>';
    }).join('');
    if (!rows.length) destroy();
    selectView(active, false);
  }

  function inspectPair(index) {
    const edge = detail?.edges[index];
    if (!edge) return;
    el('graph-pair').value = String(index);
    cy?.elements().unselect();
    cy?.getElementById('pair:' + index).select();
    el('network-graph-inspector').innerHTML = (isNeighborhood() ? '<p><strong>' + (edge.counted ? 'Counted connection' : 'Internal context only — not counted') + '</strong></p>' : '') + renderEvidence({...detail, edges: [edge]});
  }

  async function draw() {
    const term = rows.find(t => t.id === selected);
    if (!active || !term || drawnTerm === selected) {
      if (cy && active) { cy.resize(); cy.fit(undefined, 40); }
      return;
    }
    destroy();
    drawnTerm = selected;
    const token = generation;
    el('graph-term-score').textContent = term.label + ' (' + term.id + ') · ' + term.connections
      + ' connections · ' + term.expected.toFixed(2) + ' expected · '
      + (term.fold_enrichment === null ? 'undefined' : term.fold_enrichment.toFixed(2) + '×')
      + ' fold · p ' + term.p_value.toPrecision(3) + ' · BY FDR ' + term.by_fdr.toPrecision(3)
      + (term.by_fdr <= 0.05 ? ' (≤ 0.05)' : ' (does not pass 0.05)');
    if (!term.connections && !isNeighborhood()) {
      el('network-graph-status').textContent = 'No contributing pairs for this term. A depleted result can have zero connections.';
      return;
    }
    if (isNeighborhood()) el('neighborhood-context').innerHTML = '<strong>Seed genes:</strong> ' + (term.seeds || []).map(geneLink).join(' ')
      + ' · ' + term.reference_size + ' reference members · boundary conductance ' + term.conductance.toFixed(3)
      + '<br><strong>Descriptive GO BP context (not enrichment targets):</strong> '
      + ((term.go_context || []).map(t => '<a href="/terms/' + encodeURIComponent(t.id) + '">' + esc(t.label) + '</a> (' + t.members + '/' + term.reference_size + ' reference members)').join('; ') || 'none recorded');
    el('network-graph-status').textContent = 'Loading contributing pairs…';
    try {
      const data = await loadEdges(term.id, isNeighborhood() && fullContext);
      if (token !== generation) return;
      detail = data;
      const genes = new Map(data.genes.map(g => [g.id, g]));
      el('graph-pair').innerHTML = '<option value="">Choose a connecting pair…</option>' + data.edges.map((e, i) =>
        '<option value="' + i + '">' + esc(genes.get(e.source_gene)?.symbol || e.source_gene)
        + ' ↔ ' + esc(genes.get(e.target_gene)?.symbol || e.target_gene) + (e.counted === false ? ' (context only)' : '') + '</option>').join('');
      // Keep evidence usable even when the optional canvas library cannot load.
      await loadLibrary();
      if (token !== generation) return;
      const study = new Set(result.study.matched.filter(g => !result.study.outside_background.includes(g.id)).map(g => g.id));
      const members = new Set(isNeighborhood() ? data.member_ids : [...term.neighbors, ...term.overlap_genes].map(g => g.id));
      const elements = data.genes.map(g => ({data: {id: g.id, label: g.symbol || g.id},
        classes: study.has(g.id) ? (members.has(g.id) ? 'both' : 'study') : 'neighbor'}));
      elements.push(...data.edges.map((e, i) => ({data: {id: 'pair:' + i, source: e.source_gene, target: e.target_gene, index: i}, classes: e.counted === false ? 'context' : 'counted'})));
      cy = window.cytoscape({
        container: canvas, elements, layout: {name: 'cose', animate: false, randomize: true, fit: true, padding: 40, nodeRepulsion: () => 12000},
        minZoom: 0.08, maxZoom: 4, wheelSensitivity: 0.2,
        style: [
          {selector: 'node', style: {label: 'data(label)', 'font-size': 12, 'font-family': 'Lato, sans-serif', 'text-valign': 'bottom', 'text-margin-y': 6, color: '#00174f', width: 26, height: 26, 'border-width': 2, 'border-color': '#fff'}},
          {selector: 'node.study', style: {'background-color': '#4252af', shape: 'ellipse'}},
          {selector: 'node.neighbor', style: {'background-color': '#23816b', shape: 'round-rectangle'}},
          {selector: 'node.both', style: {'background-color': '#bd7616', shape: 'diamond', width: 32, height: 32}},
          {selector: 'edge', style: {width: 2, 'line-color': '#a6b3c7', 'curve-style': 'bezier'}},
          {selector: 'edge.context', style: {'line-style': 'dashed', 'line-color': '#c4cedb', width: 1}},
          {selector: 'node:selected', style: {'border-color': '#00174f', 'border-width': 4}},
          {selector: 'edge:selected', style: {'line-color': '#4252af', width: 4}},
        ],
      });
      // The element handle also supports the same canvas checks as the model viewer.
      canvas._cy = cy; canvas.dataset.ready = 'true';
      cy.on('tap', 'edge', e => inspectPair(e.target.data('index')));
      cy.on('tap', 'node', e => {
        const node = e.target, gene = genes.get(node.id());
        el('graph-pair').value = '';
        const role = study.has(node.id()) ? (members.has(node.id()) ? (isNeighborhood() ? 'Study gene and neighborhood member' : 'Study gene and term member') : 'Study gene') : (isNeighborhood() ? 'Neighborhood member' : 'Term member');
        el('network-graph-inspector').innerHTML = '<h4>' + geneLink(gene) + '</h4><p>' + role + ' · '
          + node.degree() + ' displayed connections.</p><p>Select an incident pair to inspect its source assertions.</p>';
      });
      el('network-fit').disabled = el('network-png').disabled = false;
      el('network-graph-status').textContent = data.genes.length + ' genes · ' + data.edges.length
        + (isNeighborhood() ? ' displayed pairs (' + data.counted_connections + ' counted; ' + (data.edges.length - data.counted_connections) + ' context only) · ' : ' distinct contributing pairs · ') + data.models.length + ' source models. Drag nodes to arrange; drag background to pan; scroll to zoom.';
    } catch (error) {
      if (token !== generation) return;
      drawnTerm = '';
      el('network-graph-status').textContent = error.message + (detail ? ' Pair evidence remains available below.' : '');
      el('network-retry').hidden = false;
    }
  }

  el('overview-limit').addEventListener('change', () => { overviewLimit = Number(el('overview-limit').value); refreshOverview(); });
  el('overview-min-study').addEventListener('change', () => {
    overviewMinStudy = Math.min(200000, Math.max(0, Math.floor(Number(el('overview-min-study').value) || 0)));
    el('overview-min-study').value = overviewMinStudy; refreshOverview();
  });
  el('overview-rank').addEventListener('change', () => { overviewRank = el('overview-rank').value; refreshOverview(); });
  el('overview-use-fdr').addEventListener('change', () => { overviewUseFdr = el('overview-use-fdr').checked; refreshOverview(); });
  el('overview-fit').addEventListener('click', () => overviewCy ? overviewCy.fit(undefined, 35) : drawOverview());
  el('overview-png').addEventListener('click', () => {
    if (!overviewCy) return;
    const link = document.createElement('a');
    link.href = overviewCy.png({full: true, bg: '#fff', maxWidth: 2400, maxHeight: 2400});
    link.download = 'neighborhood-network.png'; link.click();
  });
  el('table-tab').addEventListener('click', () => selectView(false));
  el('graphs-tab').addEventListener('click', () => selectView(true));
  el('result-tabs').addEventListener('keydown', e => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    selectView(e.key === 'Home' ? false : e.key === 'End' ? true : !active);
    el(active ? 'graphs-tab' : 'table-tab').focus();
  });
  el('graph-term').addEventListener('change', () => chooseTerm(el('graph-term').value));
  el('connection-chart').addEventListener('click', e => {
    const row = e.target.closest('button[data-term]');
    if (row) chooseTerm(row.dataset.term);
  });
  el('graph-pair').addEventListener('change', () => {
    if (el('graph-pair').value !== '') inspectPair(Number(el('graph-pair').value));
  });
  el('network-full-context').addEventListener('change', () => {
    fullContext = el('network-full-context').checked;
    destroy(); draw(); onState?.();
  });
  el('network-fit').addEventListener('click', () => cy?.fit(undefined, 40));
  el('network-retry').addEventListener('click', draw);
  el('network-png').addEventListener('click', () => {
    if (!cy) return;
    const link = document.createElement('a');
    link.href = cy.png({full: true, bg: '#fff', maxWidth: 2400, maxHeight: 2400});
    link.download = 'connections-' + selected.replaceAll(':', '-') + '.png';
    link.click();
  });
  return {update,
    enableFdrFilter: () => { if (isNeighborhood()) { overviewUseFdr = true; el('overview-use-fdr').checked = true; clearOverview(); } },
    state: () => ({active, selected, fullContext, overviewLimit, overviewMinStudy, overviewRank, overviewUseFdr})};
})();
