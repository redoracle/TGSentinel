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
    codeSent: false,
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

  /**
   * Shared workflow for sending/resending authentication codes.
   * @param {string} endpoint - API endpoint path (e.g., '/api/session/login/start' or '/api/session/login/resend')
   * @param {string} successMessage - Default message to show on success
   * @param {string} errorLogPrefix - Prefix for console error logging
   * @param {string} errorToastMessage - Toast message on failure
   * @param {string} errorAlertMessage - Alert message on failure
   * @param {string} buttonText - Text to set on send button after success
   */
  async function performSendCode(endpoint, successMessage, errorLogPrefix, errorToastMessage, errorAlertMessage, buttonText){
    clearAlert();
    
    // Rate limiting / cooldown guard
    const now = Date.now();
    if (state.cooldownUntil && now < state.cooldownUntil) {
      const secs = Math.max(1, Math.ceil((state.cooldownUntil - now) / 1000));
      if (window.showToast) window.showToast(`Please wait ${secs}s before resending`, 'warning');
      else setAlert(`Please wait ${secs}s before resending`, 'warning');
      return;
    }
    
    // Validate input before disabling buttons
    const phone = (phoneInput && phoneInput.value || '').trim();
    if (!phone){ setAlert('Phone is required', 'warning'); return; }
    
    // Disable buttons after validation passes
    if (btnSend) btnSend.disabled = true;
    if (btnVerify) btnVerify.disabled = true;
    
    try{
      const resp = await fetch(endpoint, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({phone})
      });
      
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
        // Handle Telegram rate limiting with friendly messaging
        if (resp.status === 429 && data) {
          const reason = data.reason;
          const retryAfter = data.retry_after || 0;
          const waitText = data.message || `Please wait before trying again.`;
          setAlert(waitText, 'warning');
          if (retryAfter) {
            state.cooldownUntil = Date.now() + (retryAfter * 1000);
            startCountdown(retryAfter);
          }
          // For resend_unavailable, nudge user to use the original code or wait
          if (reason === 'resend_unavailable') {
            if (window.showToast) window.showToast('Resend not available yet. Use the original code or wait.', 'warning');
          } else if (reason === 'flood_wait') {
            if (window.showToast) window.showToast('Rate limited. Please wait.', 'warning');
          }
          return; // Don't throw - handled
        }
        const errorMsg = (data && data.message) || `HTTP ${resp.status}`;
        throw new Error(errorMsg); 
      }
      
      // Success - update UI
      setAlert((data && data.message) || successMessage, 'success');
      if (codeRow) codeRow.classList.remove('d-none');
      if (pwdRow) pwdRow.classList.remove('d-none');
      if (btnVerify) btnVerify.classList.remove('d-none');
      // Ensure standalone resend link stays hidden; we use the main button as CTA
      if (btnResend) btnResend.classList.add('d-none');
      if (btnSend) btnSend.textContent = buttonText;
      
      // Start cooldown after sending to prevent spam
      bumpCooldown();
      state.codeSent = true;
      
    } catch(err){
      console.error(errorLogPrefix, err);
      if (window.showToast) window.showToast(errorToastMessage, 'error');
      setAlert(errorAlertMessage, 'danger');
    } finally {
      if (btnSend) btnSend.disabled = false;
      if (btnVerify) btnVerify.disabled = false;
    }
  }

  async function sendCode(){
    await performSendCode(
      '/api/session/login/start',
      'Code sent',
      'Send code failed',
      'Failed to send code. Try again shortly.',
      'Failed to send code. Check backend logs.',
      'Resend Code'
    );
  }

  async function resendCode(){
    await performSendCode(
      '/api/session/login/resend',
      'Code resent',
      'Resend code failed',
      'Failed to resend code. Try again shortly.',
      'Failed to resend code. Check backend logs.',
      'Resend Code'
    );
  }

  async function verifyCode(){
    clearAlert();
    if (btnVerify) btnVerify.disabled = true;
    if (btnSend) btnSend.disabled = true;
    
    // Show progress modal
    const progress = window.ProgressModal ? new window.ProgressModal() : null;
    
    try{
      const payload = { phone: (phoneInput && phoneInput.value || '').trim(), code: (codeInput && codeInput.value || '').trim() };
      if (pwdInput && pwdInput.value) payload.password = pwdInput.value;
      
      if (progress) {
        progress.show('login_phone', 'Authenticating Telegram Session');
        progress.addLog('Sending verification request...', 'info');
      }
      
      const resp = await fetch('/api/session/login/verify', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
      
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
      
      if (resp.status === 410) {
        if (progress) progress.hide();
        setAlert((data && data.message) || 'Session expired. Please resend code.', 'warning');
        if (btnResend) btnResend.classList.remove('d-none');
        if (btnSend) btnSend.textContent = 'Resend Code';
        bumpCooldown();
        if (window.showToast) window.showToast('Code expired. Resend required.', 'warning');
        return;
      }
      
      if(!resp.ok){ 
        if (progress) {
          progress.addLog(`Error: ${data && data.message || resp.status}`, 'error');
          progress.hide();
        }
        throw new Error((data && data.message) ? data.message : `HTTP ${resp.status}`); 
      }
      
      if (progress) {
        progress.addLog('Code verified successfully', 'success');
        progress.nextStage();
        
        // Hide the login modal behind the progress modal
        try { 
          const loginModalEl = qs('loginModal');
          if (loginModalEl) {
            const modalInstance = bootstrap.Modal.getInstance(loginModalEl);
            if (modalInstance) modalInstance.hide();
          }
        } catch(e) { console.debug('Could not hide login modal:', e); }
        
        // Poll for login progress from sentinel (no timeout - wait for completion)
        const pollProgress = async () => {
          let lastStage = null;
          let lastMessage = null;
          
          const checkProgress = async () => {
            try {
              const progressResp = await fetch('/api/worker/login-progress');
              if (progressResp.ok) {
                const progressData = await progressResp.json();
                
                if (progressData.stage && progressData.stage !== 'unknown') {
                  const percent = progressData.percent || 0;
                  const message = progressData.message || 'Processing...';
                  
                  // Only update progress bar (always)
                  progress.updateProgress(percent, message);
                  
                  // Only add log if stage or message changed
                  if (progressData.stage !== lastStage || message !== lastMessage) {
                    progress.addLog(message, 'info');
                    lastStage = progressData.stage;
                    lastMessage = message;
                  }
                  
                  if (progressData.stage === 'completed' || percent >= 100) {
                    progress.addLog('Authentication complete!', 'success');
                    
                    setAlert('Authenticated. Updating session…', 'success');
                    try{ if (window.refreshSessionInfo) window.refreshSessionInfo(); }catch(e){}
                    if (window.showToast) window.showToast('Authenticated', 'success');
                    
                    setTimeout(()=>{
                      if (progress) progress.hide();
                      // Force browser reload to refresh UI state
                      setTimeout(()=>{ try { window.location.reload(); } catch(e) {} }, 1000);
                    }, 2000);
                    return;
                  }
                }
              }
            } catch (pollErr) {
              console.debug('Progress poll error:', pollErr);
            }
            
            // Continue polling indefinitely until completion
            setTimeout(checkProgress, 500);
          };
          
          // Start polling after a short delay
          setTimeout(checkProgress, 500);
        };
        
        pollProgress();
      }
    } catch(err){
      console.error('Verify failed', err);
      if (progress) progress.hide();
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
    if (btnSend) btnSend.addEventListener('click', ()=>{
      if (state.codeSent) {
        resendCode();
      } else {
        sendCode();
      }
    });
    if (btnVerify) btnVerify.addEventListener('click', verifyCode);
    if (btnResend) btnResend.addEventListener('click', resendCode);

  // Method toggle handling
  const methodPhone = qs('methodPhone');
  const methodUpload = qs('methodUpload');
  const loginForm = qs('loginForm');
  const uploadForm = qs('uploadForm');
  const btnUploadSession = qs('btnUploadSession');

  function showPhoneMethod() {
    if (loginForm) loginForm.classList.remove('d-none');
    if (uploadForm) uploadForm.classList.add('d-none');
    if (btnSendCode) btnSendCode.classList.remove('d-none');
    if (btnVerifyCode && !state.codeSent) btnVerifyCode.classList.add('d-none');
    if (btnUploadSession) btnUploadSession.classList.add('d-none');
    clearAlert();
  }

  function showUploadMethod() {
    if (loginForm) loginForm.classList.add('d-none');
    if (uploadForm) uploadForm.classList.remove('d-none');
    if (btnSendCode) btnSendCode.classList.add('d-none');
    if (btnVerifyCode) btnVerifyCode.classList.add('d-none');
    if (btnResend) btnResend.classList.add('d-none');
    if (btnUploadSession) btnUploadSession.classList.remove('d-none');
    clearAlert();
  }

  if (methodPhone) methodPhone.addEventListener('change', showPhoneMethod);
  if (methodUpload) methodUpload.addEventListener('change', showUploadMethod);

  // Session file upload handler
  async function uploadSession() {
    clearAlert();
    const fileInput = qs('sessionFile');
    if (!fileInput || !fileInput.files || !fileInput.files[0]) {
      setAlert('Please select a session file', 'warning');
      return;
    }

    const file = fileInput.files[0];
    
    // Client-side validation
    if (!file.name.endsWith('.session')) {
      setAlert('Please select a .session file', 'warning');
      return;
    }

    if (file.size > 10 * 1024 * 1024) {
      setAlert('File too large (max 10MB)', 'warning');
      return;
    }

    if (btnUploadSession) btnUploadSession.disabled = true;
    
    // Show progress modal with log streaming
    const progress = window.ProgressModal ? new window.ProgressModal() : null;
    
    try {
      const formData = new FormData();
      formData.append('session_file', file);

      if (progress) {
        progress.show('login_upload', 'Restoring Telegram Session');
        progress.addLog(`Uploading session file: ${file.name}`, 'info');
        progress.addLog(`File size: ${(file.size / 1024).toFixed(2)} KB`, 'debug');
      }

      const resp = await fetch('/api/session/upload', {
        method: 'POST',
        body: formData
      });

      let data = null;
      try {
        data = await resp.json();
      } catch (parseErr) {
        console.error('Failed to parse response:', parseErr);
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status} - Invalid response format`);
        }
      }

      if (!resp.ok) {
        const errorMsg = (data && data.message) || `Upload failed (HTTP ${resp.status})`;
        if (progress) {
          progress.addLog(`Upload failed: ${errorMsg}`, 'error');
          setTimeout(() => progress.hide(), 2000);
        }
        throw new Error(errorMsg);
      }

      if (progress) {
        progress.addLog('Session file uploaded successfully', 'success');
        progress.nextStage();
        
        // Hide the login modal behind the progress modal
        try { 
          const loginModalEl = qs('loginModal');
          if (loginModalEl) {
            const modalInstance = bootstrap.Modal.getInstance(loginModalEl);
            if (modalInstance) modalInstance.hide();
          }
        } catch(e) { console.debug('Could not hide login modal:', e); }
        
        // Poll for login progress from sentinel (no timeout - wait for completion)
        const pollProgress = async () => {
          let lastStage = null;
          let lastMessage = null;
          
          const checkProgress = async () => {
            try {
              const progressResp = await fetch('/api/worker/login-progress');
              if (progressResp.ok) {
                const progressData = await progressResp.json();
                
                if (progressData.stage && progressData.stage !== 'unknown') {
                  const percent = progressData.percent || 0;
                  const message = progressData.message || 'Processing...';
                  
                  // Only update progress bar (always)
                  progress.updateProgress(percent, message);
                  
                  // Only add log if stage or message changed
                  if (progressData.stage !== lastStage || message !== lastMessage) {
                    progress.addLog(message, 'info');
                    lastStage = progressData.stage;
                    lastMessage = message;
                  }
                  
                  if (progressData.stage === 'completed' || percent >= 100) {
                    progress.addLog('Session restored successfully!', 'success');
                    
                    setAlert((data && data.message) || 'Session restored successfully!', 'success');
                    if (window.showToast) window.showToast('Session restored', 'success');
                    
                    // Refresh session info and reload
                    try { if (window.refreshSessionInfo) window.refreshSessionInfo(); } catch(e) {}
                    setTimeout(() => {
                      if (progress) progress.hide();
                      // Force browser reload to refresh UI state
                      setTimeout(() => { try { window.location.reload(); } catch(e) {} }, 1000);
                    }, 2000);
                    return;
                  }
                }
              }
            } catch (pollErr) {
              console.debug('Progress poll error:', pollErr);
            }
            
            // Continue polling indefinitely until completion
            setTimeout(checkProgress, 500);
          };
          
          // Start polling after a short delay
          setTimeout(checkProgress, 500);
        };
        
        pollProgress();
      }

    } catch (err) {
      console.error('Upload failed:', err);
      if (progress) setTimeout(() => progress.hide(), 2000);
      setAlert(String(err.message || err), 'danger');
      if (window.showToast) window.showToast('Upload failed', 'error');
    } finally {
      if (btnUploadSession) btnUploadSession.disabled = false;
    }
  }

  if (btnUploadSession) btnUploadSession.addEventListener('click', uploadSession);

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
      
      // Reset state so reopening the modal starts fresh
      state.codeSent = false;
      state.attempts = 0;
      state.cooldownUntil = 0;
      if (state.timer) {
        clearInterval(state.timer);
        state.timer = null;
      }
      
      // Reset UI elements to initial state
      if (btnSend) {
        btnSend.textContent = 'Send Code';
        btnSend.classList.remove('d-none');
        btnSend.disabled = false;
      }
      if (btnResend) btnResend.classList.add('d-none');
      if (resendCountdown) {
        resendCountdown.textContent = '';
        resendCountdown.classList.add('d-none');
      }
      if (codeRow) codeRow.classList.add('d-none');
      if (pwdRow) pwdRow.classList.add('d-none');
      if (btnVerify) btnVerify.classList.add('d-none');
      clearAlert();
    });
  }
});
})();
