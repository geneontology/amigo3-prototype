/* Shared entry point for components that can seed a research conversation. */
window.AmigoChat = (() => {
  const availability = window.amigoAuth.then(async auth => {
    if (auth.login_enabled && !auth.user) return false;
    const response = await fetch('/api/chat/config', {cache: 'no-store'});
    return response.ok && (await response.json()).enabled;
  }).catch(() => false);

  function annotationButton(record, buildId) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'text-button chat-about';
    button.textContent = 'Chat about this'; button.hidden = true;
    availability.then(enabled => { button.hidden = !enabled; });
    button.onclick = () => {
      const back = document.querySelector('#back-results');
      const breadcrumbs = [back?.textContent.trim(), document.querySelector('h1')?.textContent.trim(), 'Annotation ' + record.id].filter(Boolean);
      const params = new URLSearchParams({annotation: record.id, build: buildId,
        from: location.pathname + location.search + location.hash, breadcrumbs: JSON.stringify(breadcrumbs)});
      location.href = '/chat?' + params;
    };
    return button;
  }
  return {annotationButton};
})();
