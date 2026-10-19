const AmigoTabs = {
  create({nav, specs, ...options}) {
    nav.replaceChildren();
    nav.setAttribute('role', 'tablist');
    for (const {id, label, count, panel} of specs) {
      const tab = document.createElement('a');
      tab.id = 'tab-' + id;
      tab.href = '#' + id;
      tab.textContent = label + (count == null ? '' : ` (${Number(count).toLocaleString()})`);
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-controls', panel.id);
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', tab.id);
      panel.tabIndex = 0;
      nav.append(tab);
    }
    return this.mount({nav, ...options});
  },
  mount({nav, aliases={}, initial, onSelect=()=>{}}) {
    const allTabs = [...nav.querySelectorAll('[role="tab"]')];
    const tabs = allTabs.filter(tab => !tab.hidden);
    const defaultPanel = initial || tabs[0].getAttribute('aria-controls');
    const panels = allTabs.map(tab => document.getElementById(tab.getAttribute('aria-controls')));
    let followedUrl;
    function select(panelId) {
      if (!tabs.some(tab => tab.getAttribute('aria-controls') === panelId)) panelId = defaultPanel;
      for (const panel of panels) panel.hidden = panel.id !== panelId;
      for (const tab of allTabs) {
        const active = tab.getAttribute('aria-controls') === panelId;
        tab.setAttribute('aria-selected', String(active));
        tab.tabIndex = active ? 0 : -1;
      }
      onSelect(panelId);
    }
    function followHash() {
      // A history traversal can emit both events. Query-string changes can
      // also suppress hashchange, so listen to popstate and handle each URL once.
      if (followedUrl === location.href) return;
      followedUrl = location.href;
      let id;
      try { id = decodeURIComponent(location.hash.slice(1)); } catch { id = ''; }
      id = Object.hasOwn(aliases, id) ? aliases[id] : id;
      const target = document.getElementById(id);
      const panel = target?.closest('[role="tabpanel"]');
      select(panel?.id || defaultPanel);
      if (target && panel && target !== panel && target !== panel.firstElementChild && !panel.hidden) {
        for (let parent = target.parentElement; parent; parent = parent.parentElement) {
          if (parent.tagName === 'DETAILS') parent.open = true;
        }
        target.scrollIntoView();
      }
    }
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', event => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        if (location.hash !== tab.hash) history.pushState(null, '', tab.hash);
        followedUrl = location.href;
        select(tab.getAttribute('aria-controls'));
      });
      tab.addEventListener('keydown', event => {
        const next = {ArrowRight:(index + 1) % tabs.length, ArrowLeft:(index + tabs.length - 1) % tabs.length, Home:0, End:tabs.length - 1}[event.key];
        if (next === undefined) return;
        event.preventDefault();
        tabs[next].focus();
        tabs[next].click();
      });
    });
    window.addEventListener('hashchange', followHash);
    window.addEventListener('popstate', followHash);
    followHash();
    return {select};
  }
};
