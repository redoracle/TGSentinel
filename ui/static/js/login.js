// Login workflow handlers
(function initLoginWorkflow(){
  function qs(id){ return document.getElementById(id); }
  const btnSend = qs('btnSendCode');
  const btnVerify = qs('btnVerifyCode');
  const btnResend = qs('btnResendCode');
  const resendCountdown = qs('resendCountdown');
  const phoneInput = qs('loginPhone');
  const codeInput = qs('loginCode');
  const pwdInput = qs('loginPassword');
  const codeRow = qs('codeRow');
  const pwdRow = qs('passwordRow');
  const alertBox = qs('loginAlert');

  // Private rate limit state (module-scoped)
  const state = {
    attempts: 0,
    cooldownUntil: 0,
    timer: null,
  };

  function setAlert(msg, variant){
    if (!alertBox) return;
    const v = variant || 'info';
    alertBox.className = `alert alert-${v}`;
    alertBox.textContent = msg || '';
    alertBox.classList.remove('d-none');
  }

  function clearAlert(){
    if (!alertBox) return;
    alertBox.classList.add('d-none');
    alertBox.textContent = '';
  }

  async function sendCode(){
    clearAlert();
    // Rate limiting / cooldown guard
    const now = Date.now();
    if (state.cooldownUntil && now < state.cooldownUntil) {
      const secs = Math.max(1, Math.ceil((state.cooldownUntil - now) / 1000));
      if (window.showToast) window.showToast(`Please wait ${secs}s before resending`, 'warning');
      else setAlert(`Please wait ${secs}s before resending`, 'warning');
      return;
    }
    if (btnSend) btnSend.disabled = true;
    if (btnVerify) btnVerify.disabled = true;
    try{
      const phone = (phoneInput && phoneInput.value || '').trim();
      if (!phone){ setAlert('Phone is required', 'warning'); return; }
      const resp = await fetch('/api/session/login/start', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({phone})});
      
      // Safely parse JSON response
      let data = null;
      try {
        data = await resp.json();
      } catch (parseErr) {
        console.error('Failed to parse response as JSON:', parseErr);
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status} - Invalid response format`);
        }
      }
      
      if(!resp.ok){ 
        const errorMsg = (data && data.message) || `HTTP ${resp.status}`;
        throw new Error(errorMsg); 
      }
      
      setAlert((data && data.message) || 'Code sent', 'success');
      if (codeRow) codeRow.classList.remove('d-none');
      if (pwdRow) pwdRow.classList.remove('d-none');
      if (btnVerify) btnVerify.classList.remove('d-none');
      // Ensure standalone resend link stays hidden; we use the main button as CTA
      if (btnResend) btnResend.classList.add('d-none');
      if (btnSend) btnSend.textContent = 'Resend Code';
      // Start cooldown after sending to prevent spam (replaces the CTA while active)
      bumpCooldown();
    } catch(err){
      console.error('Send code failed', err);
      if (window.showToast) window.showToast('Failed to send code. Try again shortly.', 'error');
      setAlert('Failed to send code. Check backend logs.', 'danger');
    } finally {
      if (btnSend) btnSend.disabled = false;
      if (btnVerify) btnVerify.disabled = false;
    }
  }

  async function verifyCode(){
    clearAlert();
    if (btnVerify) btnVerify.disabled = true;
    if (btnSend) btnSend.disabled = true;
    try{
      const payload = { phone: (phoneInput && phoneInput.value || '').trim(), code: (codeInput && codeInput.value || '').trim() };
      if (pwdInput && pwdInput.value) payload.password = pwdInput.value;
      const resp = await fetch('/api/session/login/verify', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
      
      // Safely parse JSON response
      let data = null;
      try {
        data = await resp.json();
      } catch (parseErr) {
        console.error('Failed to parse response as JSON:', parseErr);
        // If we can't parse JSON and response is not ok, throw with status
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status} - Invalid response format`);
        }
      }
      
      if (resp.status === 410) {
        // Session missing/expired — prompt to resend with cooldown
        setAlert((data && data.message) || 'Session expired. Please resend code.', 'warning');
        if (btnResend) btnResend.classList.remove('d-none');
        if (btnSend) btnSend.textContent = 'Resend Code';
        bumpCooldown();
      if (window.showToast) window.showToast('Code expired. Resend required.', 'warning');
      return;
    }
      if(!resp.ok){ throw new Error((data && data.message) ? data.message : `HTTP ${resp.status}`); }
      setAlert('Authenticated. Updating session…', 'success');
      try{ if (window.refreshSessionInfo) window.refreshSessionInfo(); }catch(e){}
      if (window.showToast) window.showToast('Authenticated', 'success');
      setTimeout(()=>{
        try{ bootstrap.Modal.getInstance(qs('loginModal')).hide(); }catch(e){}
        // Ensure gated pages unlock: reload after auth
        setTimeout(()=>{ try { window.location.reload(); } catch(e) {} }, 400);
      }, 600);
    } catch(err){
      console.error('Verify failed', err);
      setAlert(String(err.message || err), 'danger');
    } finally {
      if (btnVerify) btnVerify.disabled = false;
      if (btnSend) btnSend.disabled = false;
    }
  }

  // Private rate-limit functions (module-scoped)
  function bumpCooldown(){
    state.attempts += 1;
    // Base cooldown 30s; increase after multiple attempts
    let seconds = 30;
    if (state.attempts >= 3) seconds = 60;
    if (state.attempts >= 5) seconds = 120;
    const now = Date.now();
    state.cooldownUntil = now + seconds * 1000;
    // Update UI
    if (btnResend) btnResend.classList.add('d-none');
    if (btnSend) btnSend.classList.add('d-none');
    if (resendCountdown) resendCountdown.classList.remove('d-none');
    startCountdown(seconds);
  }

  function startCountdown(totalSeconds){
    let remaining = totalSeconds;
    updateCountdownUI(remaining);
    if (state.timer) clearInterval(state.timer);
    state.timer = setInterval(()=>{
      remaining -= 1;
      if (remaining <= 0){
        clearInterval(state.timer);
        state.timer = null;
        if (resendCountdown){
          resendCountdown.textContent = '';
          resendCountdown.classList.add('d-none');
        }
        // Reveal the main CTA again and keep the standalone link hidden
        if (btnSend){
          btnSend.disabled = false;
          btnSend.classList.remove('d-none');
        }
        if (btnResend){
          btnResend.classList.add('d-none');
        }
        return;
      }
      updateCountdownUI(remaining);
    }, 1000);
    // Ensure standalone resend link is hidden during cooldown
    if (btnResend) btnResend.classList.add('d-none');
  }

  function updateCountdownUI(remaining){
    if (!resendCountdown) return;
    resendCountdown.textContent = `Wait ${remaining}s to resend`;
  }

  document.addEventListener('DOMContentLoaded', function(){
    if (btnSend) btnSend.addEventListener('click', sendCode);
    if (btnVerify) btnVerify.addEventListener('click', verifyCode);
    if (btnResend) btnResend.addEventListener('click', sendCode);

  // Apply glass backdrop only when login modal is open
  const loginModalEl = document.getElementById('loginModal');
  if (loginModalEl) {
    loginModalEl.addEventListener('shown.bs.modal', () => {
      const backdrop = document.querySelector('.modal-backdrop');
      if (backdrop) backdrop.classList.add('glass-backdrop');
    });
    loginModalEl.addEventListener('hidden.bs.modal', () => {
      // Remove the class so other modals aren't affected
      const backdrop = document.querySelector('.modal-backdrop');
      if (backdrop) backdrop.classList.remove('glass-backdrop');
    });
  }
});
})();
