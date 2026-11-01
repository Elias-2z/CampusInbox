'use strict';
(() => {
  const params = new URLSearchParams(location.search);
  if (params.get('embedded') !== '1' || parent === window) return;
  document.body.classList.add('ci-embedded');
  const panel = params.get('panel');
  if (panel) document.body.classList.add('ci-embed-panel');
  const initialView = location.hash.slice(1);
  let ready = false, transitioning = false;
  const send = (type, extra = {}) => parent.postMessage({ type: 'campusinbox:' + type, ...extra }, location.origin);
  const originalFetch = window.fetch;
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    const method = (args[1]?.method || (args[0] instanceof Request ? args[0].method : 'GET')).toUpperCase();
    if (response.ok && !['GET', 'HEAD'].includes(method)) send('changed');
    return response;
  };
  // These actions deliberately close one dialog before awaiting another surface.
  document.addEventListener('click', event => {
    if (event.target instanceof Element && event.target.closest('[data-detail], [data-message-proposal]')) transitioning = true;
  }, true);
  let activeDialog = null;
  const observer = new MutationObserver(() => {
    const dialog = document.querySelector('dialog[open]');
    if (dialog) {
      transitioning = false;
      if (!panel && dialog !== activeDialog) send('dialog-open');
    }
    activeDialog = dialog;
  });
  observer.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['open'] });
  window.addEventListener('campusinbox:view', event => {
    if (!panel || (ready && event.detail !== initialView)) send('navigate', { view: event.detail });
  });
  window.addEventListener('campusinbox:ready', () => {
    if (panel === 'import') document.querySelector('#importOpen')?.click();
    if (panel === 'assistant') document.querySelector('#assistantOpen')?.click();
    ready = true;
    send('ready');
  });
  document.addEventListener('close', () => {
    if (panel && !transitioning && !document.querySelector('dialog[open]')) send('close');
  }, true);
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || event.source !== parent) return;
    if (event.data?.type === 'campusinbox:refresh') document.querySelector('#refreshButton')?.click();
  });
})();
