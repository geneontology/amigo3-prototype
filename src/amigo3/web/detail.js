/* Expandable plain-text summaries shared by record detail pages. */
window.AmigoDetail = (() => {
  function collapseText(target, limit = 320) {
    const full = target.textContent.trim();
    if (full.length <= limit) return;
    const preview = full.slice(0, limit).replace(/\s+\S*$/, '') + '…';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'text-button';
    button.setAttribute('aria-controls', target.id);
    function show(expanded) {
      target.textContent = expanded ? full : preview;
      button.textContent = expanded ? '[less]' : '[more…]';
      button.setAttribute('aria-expanded', String(expanded));
    }
    button.addEventListener('click', () => show(button.getAttribute('aria-expanded') !== 'true'));
    target.after(document.createTextNode(' '), button);
    show(false);
  }
  function description(container, text) {
    container.replaceChildren();
    container.hidden = !text?.trim();
    if (container.hidden) return;
    const paragraph = document.createElement('p'), span = document.createElement('span');
    span.id = container.id + '-text';
    span.textContent = text.trim();
    paragraph.append(span);
    container.append(paragraph);
    collapseText(span);
  }
  function narrativeDescription(article) {
    // Use the rendered source so Markdown formatting never leaks into the header.
    const summary = [...article.querySelectorAll('h1,h2,h3')].find(heading => /^(summary|abstract|executive summary)$/i.test(heading.textContent.trim()));
    let paragraph = summary?.nextElementSibling;
    while (paragraph && !/^H[1-6]$/.test(paragraph.tagName) && paragraph.tagName !== 'P') paragraph = paragraph.nextElementSibling;
    if (paragraph?.tagName !== 'P') paragraph = article.querySelector('p');
    return paragraph?.textContent.replace(/\s+/g, ' ').trim() || '';
  }
  return {collapseText, description, narrativeDescription};
})();
