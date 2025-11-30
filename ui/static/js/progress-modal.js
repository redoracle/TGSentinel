/**
 * Progress Modal Component
 * Displays real-time progress feedback for login/logout operations
 * with sentinel log streaming
 */

(function() {
  'use strict';

  // Create progress modal HTML if not exists
  function ensureProgressModal() {
    if (document.getElementById('progressModal')) return;
    
    const modalHTML = `
      <div class="modal fade" id="progressModal" tabindex="-1" data-bs-backdrop="static" data-bs-keyboard="false" aria-labelledby="progressModalLabel" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered modal-lg">
          <div class="modal-content">
            <div class="modal-header">
              <h5 class="modal-title" id="progressModalLabel">
                <span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>
                <span id="progressTitle">Processing...</span>
              </h5>
            </div>
            <div class="modal-body">
              <div class="mb-3">
                <div class="progress" style="height: 25px;">
                  <div id="progressBar" class="progress-bar progress-bar-striped progress-bar-animated" 
                       role="progressbar" style="width: 0%" 
                       aria-valuenow="0" aria-valuemin="0" aria-valuemax="100">
                    <span id="progressPercent">0%</span>
                  </div>
                </div>
              </div>
              <div id="progressStatus" class="mb-2 text-muted">
                <i class="bi bi-hourglass-split me-2"></i>
                <span id="progressStatusText">Initializing...</span>
              </div>
              <div class="card bg-dark border-secondary" style="max-height: 300px; overflow-y: auto;">
                <div class="card-body">
                  <div id="progressLogs" class="font-monospace small text-muted">
                    <div class="text-center py-3">
                      <span class="spinner-border spinner-border-sm me-2" role="status"></span>
                      Waiting for sentinel logs...
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHTML);
  }

  // Progress stages for different operations
  const STAGES = {
    login_phone: [
      { percent: 10, status: 'Sending authentication code...' },
      { percent: 30, status: 'Waiting for code verification...' },
      { percent: 50, status: 'Verifying credentials...' },
      { percent: 70, status: 'Establishing Telegram connection...' },
      { percent: 90, status: 'Finalizing authentication...' },
      { percent: 100, status: 'Authentication complete!' }
    ],
    login_upload: [
      { percent: 10, status: 'Uploading session file...' },
      { percent: 30, status: 'Validating session...' },
      { percent: 50, status: 'Importing to sentinel...' },
      { percent: 70, status: 'Initializing Telegram client...' },
      { percent: 90, status: 'Verifying authorization...' },
      { percent: 100, status: 'Session restored successfully!' }
    ],
    logout: [
      { percent: 10, status: 'Initiating logout sequence...' },
      { percent: 30, status: 'Disconnecting from Telegram...' },
      { percent: 50, status: 'Clearing authentication state...' },
      { percent: 70, status: 'Cleaning up Redis keys...' },
      { percent: 90, status: 'Removing session files...' },
      { percent: 100, status: 'Logout complete!' }
    ]
  };

  class ProgressModal {
    constructor(options = {}) {
      ensureProgressModal();
      this.modal = null;
      this.modalEl = document.getElementById('progressModal');
      this.progressBar = document.getElementById('progressBar');
      this.progressPercent = document.getElementById('progressPercent');
      this.progressTitle = document.getElementById('progressTitle');
      this.progressStatusText = document.getElementById('progressStatusText');
      this.progressLogs = document.getElementById('progressLogs');
      this.currentStage = 0;
      this.stages = [];
      this.eventSource = null;
      this.logBuffer = [];
      this.maxLogs = 100;
      this.simulationTimer = null;
      // Configurable locale for timestamp formatting
      this.locale = options.locale || (typeof navigator !== 'undefined' && navigator.language) || 'en-GB';
    }

    show(operation, title) {
      this.currentStage = 0;
      this.stages = STAGES[operation] || STAGES.login_phone;
      this.logBuffer = [];
      
      if (this.progressTitle) this.progressTitle.textContent = title || 'Processing...';
      if (this.progressLogs) this.progressLogs.innerHTML = '<div class="text-muted">Connecting to sentinel...</div>';
      
      this.updateProgress(0, this.stages[0].status);
      
      if (!this.modal && this.modalEl) {
        this.modal = new bootstrap.Modal(this.modalEl, {
          backdrop: 'static',
          keyboard: false
        });
      }
      
      if (this.modal) {
        // Set up reliable backdrop class application
        const applyGlassBackdrop = () => {
          const backdrop = document.querySelector('.modal-backdrop');
          if (backdrop) {
            backdrop.classList.add('glass-backdrop');
          }
        };

        // Primary approach: Use Bootstrap's shown.bs.modal event
        if (this.modalEl && this.modalEl.addEventListener) {
          this.modalEl.addEventListener('shown.bs.modal', applyGlassBackdrop, { once: true });
        }

        // Fallback: MutationObserver for when Bootstrap events aren't available
        const observer = new MutationObserver((mutations, obs) => {
          const backdrop = document.querySelector('.modal-backdrop');
          if (backdrop) {
            backdrop.classList.add('glass-backdrop');
            obs.disconnect(); // Stop observing once backdrop is found and class is applied
          }
        });

        // Start observing before showing the modal
        observer.observe(document.body, {
          childList: true,
          subtree: true
        });

        // Disconnect observer after a reasonable timeout to prevent memory leaks
        setTimeout(() => observer.disconnect(), 1000);

        this.modal.show();
      }
    }

    hide() {
      if (this.modal) {
        this.modal.hide();
      }
      if (this.eventSource) {
        this.eventSource.close();
        this.eventSource = null;
      }
    }

    updateProgress(percent, statusText) {
      if (this.progressBar) {
        this.progressBar.style.width = `${percent}%`;
        this.progressBar.setAttribute('aria-valuenow', percent);
      }
      if (this.progressPercent) {
        this.progressPercent.textContent = `${Math.round(percent)}%`;
      }
      if (this.progressStatusText && statusText) {
        this.progressStatusText.textContent = statusText;
      }
      
      // Change color when complete
      if (percent >= 100 && this.progressBar) {
        this.progressBar.classList.remove('progress-bar-animated', 'progress-bar-striped');
        this.progressBar.classList.add('bg-success');
      }
    }

    nextStage() {
      this.currentStage++;
      if (this.currentStage < this.stages.length) {
        const stage = this.stages[this.currentStage];
        this.updateProgress(stage.percent, stage.status);
      }
    }

    addLog(message, level = 'info') {
      const timestamp = new Date().toLocaleTimeString(this.locale, { hour12: false });
      const icons = {
        info: 'bi-info-circle text-info',
        success: 'bi-check-circle text-success',
        warning: 'bi-exclamation-triangle text-warning',
        error: 'bi-x-circle text-danger',
        debug: 'bi-bug text-muted'
      };
      const icon = icons[level] || icons.info;
      
      const logEntry = {
        timestamp,
        message,
        level,
        icon
      };
      
      this.logBuffer.push(logEntry);
      if (this.logBuffer.length > this.maxLogs) {
        this.logBuffer.shift();
      }
      
      this.renderLogs();
      
      // Auto-scroll to bottom
      if (this.progressLogs && this.progressLogs.parentElement) {
        this.progressLogs.parentElement.scrollTop = this.progressLogs.parentElement.scrollHeight;
      }
    }

    renderLogs() {
      if (!this.progressLogs) return;
      
      const logsHTML = this.logBuffer.map(log => `
        <div class="d-flex align-items-start mb-1">
          <span class="text-muted me-2" style="min-width: 70px;">${log.timestamp}</span>
          <i class="bi ${log.icon} me-2"></i>
          <span class="text-${log.level === 'error' ? 'danger' : log.level === 'warning' ? 'warning' : 'light'}">${this.escapeHtml(log.message)}</span>
        </div>
      `).join('');
      
      this.progressLogs.innerHTML = logsHTML || '<div class="text-muted">No logs yet...</div>';
    }

    escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    streamLogs(endpoint, onComplete) {
      // Close existing stream if any
      if (this.eventSource) {
        this.eventSource.close();
      }

      this.addLog('Connecting to log stream...', 'debug');

      try {
        this.eventSource = new EventSource(endpoint);
      } catch (err) {
        this.addLog(`Failed to connect: ${err.message}`, 'error');
        if (onComplete) onComplete(false, { message: err.message });
        return;
      }
      this.eventSource.addEventListener('log', (event) => {
        try {
          const data = JSON.parse(event.data);
          const level = data.level || 'info';
          this.addLog(data.message, level);
          
          // Auto-advance stages based on keywords (only for non-error logs)
          this.autoAdvanceStage(data.message, level);
        } catch (err) {
          console.error('Failed to parse log event:', err);
        }
      });

      this.eventSource.addEventListener('progress', (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.percent !== undefined) {
            this.updateProgress(data.percent, data.status);
          }
        } catch (err) {
          console.error('Failed to parse progress event:', err);
        }
      });

      this.eventSource.addEventListener('complete', (event) => {
        try {
          const data = JSON.parse(event.data);
          this.updateProgress(100, data.message || 'Complete!');
          this.addLog(data.message || 'Operation completed successfully', 'success');
          
          setTimeout(() => {
            this.hide();
            if (onComplete) onComplete(true, data);
          }, 1500);
        } catch (err) {
          console.error('Failed to parse complete event:', err);
        }
      });

      this.eventSource.addEventListener('error', (event) => {
        console.error('EventSource error:', event);
        this.addLog('Connection to sentinel lost', 'error');
        
        // Try to parse error data if available
        if (event.data) {
          try {
            const data = JSON.parse(event.data);
            this.addLog(data.message || 'Operation failed', 'error');
            setTimeout(() => {
              this.hide();
              if (onComplete) onComplete(false, data);
            }, 2000);
          } catch (err) {
            // EventSource errors don't always have data
            this.eventSource.close();
          }
        }
      });

      this.eventSource.onerror = () => {
        this.addLog('Stream connection closed', 'warning');
        this.eventSource.close();
        this.eventSource = null;
      };
    }
    autoAdvanceStage(message, level = 'info') {
      if (!message) return;
      
      // Early return if message is error or warning level
      if (level === 'error' || level === 'warning' || level === 'warn') {
        return;
      }
      
      const lowerMsg = message.toLowerCase();
      
      // Filter out messages containing negative indicators
      const negativeWords = ['fail', 'failed', 'error', 'exception', 'unable', 'cannot', 'could not'];
      if (negativeWords.some(word => lowerMsg.includes(word))) {
        return;
      }
      
      // Use strict regex patterns with word boundaries for success indicators
      const successPattern = /\b(sent|verified successfully|connected|authorized|imported|disconnected|cleared|cleaned|removed|complete(d)?|connection established|successfully|cached|stored)\b/i;
      
      if (successPattern.test(message)) {
        this.nextStage();
      }
    }
    
    simulateProgress(operation, durationMs = 3000) {
      if (this.simulationTimer) {
        clearInterval(this.simulationTimer);
        this.simulationTimer = null;
      }
      
      this.currentStage = 0;
      this.stages = STAGES[operation] || STAGES.login_phone;
      
      if (this.stages.length === 0) {
        this.updateProgress(100, 'Complete');
        return;
      }
      
      const interval = durationMs / this.stages.length;
      const firstStage = this.stages[0];
      this.updateProgress(firstStage.percent || 0, firstStage.status);
      
      this.simulationTimer = setInterval(() => {
        this.nextStage();
        
        if (this.currentStage >= this.stages.length - 1) {
          clearInterval(this.simulationTimer);
          this.simulationTimer = null;
        }
      }, interval);
    }
  }

  // Expose globally
  window.ProgressModal = ProgressModal;

})();
