// Inlined with a CSP content hash: recovery must survive a failed module fetch.
(() => {
  const key = 'cmmn.boot.reloads';
  let failed = false, complete = false, seenLoading = false, retryTimer;
  const clearSaved = () => { try { sessionStorage.removeItem(key); } catch {} };
  const stop = () => { clearTimeout(watchdog); clearTimeout(retryTimer); };
  const fail = () => {
    if (failed || complete || !document.querySelector('[data-module-loading]')) return;
    failed = true; stop();
    document.querySelector('[data-module-status]').textContent = 'Could not reach Commonplace, retrying...';
    const retry = document.querySelector('[data-module-retry]');
    retry.hidden = false;
    retry.onclick = () => { stop(); clearSaved(); location.reload(); };
    // Persist only a retry count/time, never a URL, identity or credential.
    // Without session storage, manual Retry avoids an unbounded reload loop.
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
      const attempt = saved && Date.now() - saved.at < 60000 && Number.isInteger(saved.attempt)
        ? Math.max(0, saved.attempt) : 0;
      if (attempt >= 3) return;
      sessionStorage.setItem(key, JSON.stringify({attempt:attempt + 1, at:Date.now()}));
      retryTimer = setTimeout(() => location.reload(), [1000, 2000, 4000][attempt]);
    } catch { /* The visible Retry remains usable. */ }
  };
  const watchdog = setTimeout(fail, 30000);
  globalThis.commonplaceBootGuard = {started() { stop(); }};
  addEventListener('error', event => {
    if (event.target?.type === 'module' || /\/app\/[^?#]+\.js(?:[?#]|$)/.test(event.filename || '')) fail();
  }, true);
  addEventListener('unhandledrejection', fail);
  const observer = new MutationObserver(() => {
    if (document.querySelector('[data-module-loading]')) seenLoading = true;
    else if (seenLoading || document.readyState !== 'loading') {
      complete = true; stop(); clearSaved(); observer.disconnect();
    }
  });
  observer.observe(document, {childList:true, subtree:true});
})();
