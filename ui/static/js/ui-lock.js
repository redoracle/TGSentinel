// UI Lock client logic: lock button + idle auto-lock + unlock modal
(function(){
  const enabled = !!window.UI_LOCK_ENABLED;
  const timeoutSec = Number(window.UI_LOCK_TIMEOUT || 0) || 900;
  let idleTimer = null;
  let uiLocked = false;

  function setLockedState(locked){
    uiLocked = !!locked;
    document.body.classList.toggle('locked-ui', uiLocked);
  }

  function showUnlockModal(){
    try{
      const el = document.getElementById('unlockModal');
      if (!el) return;
      const modal = bootstrap.Modal.getInstance(el) || new bootstrap.Modal(el, { backdrop: 'static', keyboard: false });
      modal.show();
      setLockedState(true);
    }catch(e){ console.debug('Unlock modal not available', e); }
  }

  async function lockUI(){
    try{
      await fetch('/api/ui/lock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'lock' })
      });
      // Force a reload so server-side gating renders locked UI
      try { window.location.reload(true); } catch(e) { window.location.href = window.location.href; }
    }catch(e){ /* ignore */ }
  }

  async function unlockUI(){
    const alertBox = document.getElementById('unlockAlert');
    const setAlert = (msg, variant='info') => {
      if (!alertBox) return;
      alertBox.className = `alert alert-${variant}`;
      alertBox.textContent = msg || '';
      alertBox.classList.remove('d-none');
    };
    try{
      const pwd = (document.getElementById('unlockPassword')?.value || '');
      const resp = await fetch('/api/ui/lock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'unlock', password: pwd })
      });
      const data = await resp.json().catch(()=>({}));
      if(!resp.ok){
        setAlert(data && data.message ? data.message : `HTTP ${resp.status}`, 'danger');
        return;
      }
      // Close modal and clear password
      try{ bootstrap.Modal.getInstance(document.getElementById('unlockModal')).hide(); }catch(e){}
      try{ document.getElementById('unlockPassword').value = ''; }catch(e){}
      setLockedState(false);
    }catch(e){
      setAlert('Failed to unlock. Please try again.', 'danger');
    }
  }

  function resetIdleTimer(){
    if (!enabled || !timeoutSec) return;
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(async () => {
      try{
        await lockUI();
        showUnlockModal();
      }catch(e){}
    }, timeoutSec * 1000);
  }

  // Intercept navigation/interaction when locked
  function captureGuard(e){
    if (!uiLocked) return;
    // Allow interactions inside unlock modal
    const modal = document.getElementById('unlockModal');
    if (modal && modal.contains(e.target)) return;
    e.stopPropagation();
    e.preventDefault();
  }

  // Wrap fetch to react to 423 immediately
  (function(){
    if (!window.fetch) return;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const resp = await nativeFetch(...args);
      if (resp && resp.status === 423){
        setLockedState(true);
        showUnlockModal();
      }
      return resp;
    };
  })();

  document.addEventListener('DOMContentLoaded', async () => {
    const btnLock = document.getElementById('btn-ui-lock');
    if (btnLock){
      btnLock.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        try{ await lockUI(); }catch(e){}
        showUnlockModal();
      }, true);
    }

    const btnUnlockNow = document.getElementById('btnUnlockNow');
    if (btnUnlockNow){
      btnUnlockNow.addEventListener('click', (ev) => {
        ev.stopPropagation();
        unlockUI();
      }, true);
    }

    // Check current lock status to enforce immediately
    try{
      const resp = await fetch('/api/ui/lock/status');
      const data = await resp.json().catch(()=>({}));
      if (resp.ok && data && data.locked){
        showUnlockModal();
      }
    }catch(e){}

    // Idle auto-lock
    if (enabled && timeoutSec > 0){
      ['mousemove','keydown','scroll','click','touchstart'].forEach(evt=>{
        window.addEventListener(evt, resetIdleTimer, { passive: true, capture: true });
      });
      resetIdleTimer();
    }

    // Block interactions while locked
    ['click','mousedown','mouseup','touchstart','keydown'].forEach(evt => {
      document.addEventListener(evt, captureGuard, true);
    });
  });
})();
