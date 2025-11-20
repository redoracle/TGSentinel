// UI Lock client logic: lock button + idle auto-lock
// Server-side renders locked_ui.html when UI is locked, so we just trigger page reload
(function(){
  const enabled = !!window.UI_LOCK_ENABLED;
  const timeoutSec = Number(window.UI_LOCK_TIMEOUT || 0) || 900;
  let idleTimer = null;

  async function lockUI(){
    const resp = await fetch('/api/ui/lock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'lock' })
    });
    
    if (!resp.ok) {
      throw new Error(`Lock failed: HTTP ${resp.status}`);
    }
    
    // Reload page - server will render locked_ui.html
    window.location.reload();
  }

  function resetIdleTimer(){
    if (!enabled || !timeoutSec) return;
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(async () => {
      try{
        await lockUI();
      }catch(e){
        console.error('Failed to lock UI on idle timeout:', e);
      }
    }, timeoutSec * 1000);
  }

  // Redirect to main page on 423 (locked) - server will render locked_ui.html
  (function(){
    if (!window.fetch) return;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const resp = await nativeFetch(...args);
      if (resp && resp.status === 423){
        window.location.href = '/';
        // Clone the response and add sentinel header so callers can detect redirect
        const clonedResp = resp.clone();
        const headers = new Headers(clonedResp.headers);
        headers.set('X-Redirected', '1');
        return new Response(clonedResp.body, {
          status: clonedResp.status,
          statusText: clonedResp.statusText,
          headers: headers
        });
      }
      return resp;
    };
  })();

  document.addEventListener('DOMContentLoaded', async () => {
    const btnLock = document.getElementById('btn-ui-lock');
    const btnLockMobile = document.getElementById('btn-ui-lock-mobile');
    
    const handleLock = async (ev) => {
      ev.stopPropagation();
      try{ 
        await lockUI();
      }catch(e){
        console.error('Failed to lock UI:', e);
      }
    };
    
    if (btnLock){
      btnLock.addEventListener('click', handleLock, true);
    }
    if (btnLockMobile){
      btnLockMobile.addEventListener('click', handleLock, true);
    }

    // Idle auto-lock
    if (enabled && timeoutSec > 0){
      ['mousemove','keydown','scroll','click','touchstart'].forEach(evt=>{
        window.addEventListener(evt, resetIdleTimer, { passive: true, capture: true });
      });
      resetIdleTimer();
    }
  });
})();
