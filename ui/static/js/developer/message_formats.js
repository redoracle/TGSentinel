/**
 * Message Formats Editor
 * 
 * Provides Monaco Editor integration for editing message format templates
 * with live preview and test sending capabilities.
 */

// Store editor instances
const editors = {};
let formatsData = null;
let isModified = false;

// Sentinel API base URL (proxy routes in admin.py blueprint)
const SENTINEL_API_BASE = '/sentinel';

const VARIABLE_CATEGORIES = [
    {
        id: 'message',
        title: 'Message / Chat Metadata',
        keys: ['chat_id', 'chat_title', 'msg_id', 'message_link', 'message_text', 'message_preview', 'timestamp'],
        note: 'Shared across DM, Saved, Digest, and Webhook formats.'
    },
    {
        id: 'sender',
        title: 'Sender Information',
        keys: ['sender_id', 'sender_name', 'is_vip'],
        note: ''
    },
    {
        id: 'profile',
        title: 'Profile Matching',
        keys: ['profile_id', 'profile_name'],
        note: 'Available in all profile-related notifications (including digests and webhooks).'
    },
    {
        id: 'scoring',
        title: 'Scoring & Ranking',
        keys: ['score', 'keyword_score', 'semantic_score', 'reactions', 'rank'],
        note: 'Use {?...} syntax for optional values.'
    },
    {
        id: 'triggers',
        title: 'Triggers',
        keys: ['triggers', 'triggers_json', 'triggers_formatted'],
        note: 'Prefer triggers_formatted for UI display and triggers_json for webhook JSON payloads.'
    },
    {
        id: 'formatted_lines',
        title: 'Formatted Line Variables (Optional)',
        keys: [
            'sender_line',
            'vip_line',
            'triggers_line',
            'message_link_line',
            'message_line',
            'reactions_line',
            'semantic_score_line',
            'keyword_score_line',
            'profile_line',
        ],
        note: 'Pre-formatted optional lines with icons. Use {?variable} syntax to conditionally include them. Available in DM Notifications, Saved Messages, and Digests. These are auto-generated when their base values exist (e.g., sender_line is generated when sender_name exists).'
    },
    {
        id: 'digest',
        title: 'Digest-specific',
        keys: [
            'digest_type',
            'channel_count',
            'schedule',
            'time_range',
            'top_n',
        ],
        note: 'Visible in the Digest Header and Entry templates. digest_type is the digest category (e.g., "Alerts Digest", "Interests Digest"), while profile_name (in Profile Matching) shows actual matched profile names.'
    },
];

// Monaco configuration (done once, deferred until loader script is ready)
let monacoLoaded = false;

function ensureMonacoLoaded() {
    if (window.__monacoReadyPromise) {
        return window.__monacoReadyPromise;
    }

    const loaderPromise = window.__monacoLoaderPromise || Promise.resolve();

    window.__monacoReadyPromise = loaderPromise.then(() => new Promise((resolve, reject) => {
        const attemptLoad = () => {
            if (window.monaco) {
                monacoLoaded = true;
                resolve(window.monaco);
                return;
            }

            if (typeof window.require === 'function' && typeof window.require.config === 'function') {
                if (!window.__monacoConfigured) {
                    window.__monacoConfigured = true;
                    window.require.config({
                        paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs' },
                        'vs/nls': { availableLanguages: {} },
                    });
                }

                window.require(['vs/editor/editor.main'], () => {
                    monacoLoaded = true;
                    resolve(window.monaco);
                }, (err) => {
                    console.error('[TG Sentinel] Failed to load Monaco modules', err);
                    reject(err);
                });
                return;
            }

            // Loader not ready yet
            setTimeout(attemptLoad, 50);
        };

        attemptLoad();
    }));

    return window.__monacoReadyPromise;
}

/**
 * Initialize Monaco Editor for a container
 */
function initMonacoEditor(containerId, language, initialValue = '') {
    return ensureMonacoLoaded().then((monacoInstance) => {
        if (!monacoInstance) {
            console.error('Monaco Editor failed to load');
            return null;
        }

        const container = document.getElementById(containerId);
        if (!container) {
            console.error(`Container not found: ${containerId}`);
            return null;
        }

        // Use dark theme - TG Sentinel uses Bootstrap dark theme (data-bs-theme="dark")
        const isDark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
        const editor = monacoInstance.editor.create(container, {
            value: initialValue,
            language: language,
            theme: isDark ? 'vs-dark' : 'vs-light',
            minimap: { enabled: false },
            lineNumbers: 'on',
            wordWrap: 'on',
            scrollBeyondLastLine: false,
            automaticLayout: true,
            fontSize: 14,
            tabSize: 2,
            folding: true,
            renderLineHighlight: 'line',
        });

        // Track modifications
        editor.onDidChangeModelContent(() => {
            isModified = true;
            updateSaveButton();
        });

        return editor;
    }).catch((error) => {
        console.error('Monaco loader failed:', error);
        showToast('Monaco Editor failed to initialize. Check network/CSP settings.', 'danger');
        return null;
    });
}

/**
 * Load message formats from the API
 */
async function loadFormats() {
    try {
        const response = await fetch(`${SENTINEL_API_BASE}/message-formats`);
        const data = await response.json();
        
        if (data.status !== 'ok') {
            throw new Error(data.error?.message || 'Failed to load formats');
        }
        
        formatsData = data.data;
        return formatsData;
    } catch (error) {
        console.error('Error loading formats:', error);
        showToast('Error loading message formats', 'danger');
        return null;
    }
}

/**
 * Populate editors with loaded format data
 */
function populateEditors() {
    if (!formatsData) return;
    
    const formats = formatsData.formats;
    
    // DM Notification
    if (editors['dm-alerts'] && formats.dm_alerts) {
        editors['dm-alerts'].setValue(formats.dm_alerts.template || '');
    }
    
    // Saved Messages
    if (editors['saved-messages'] && formats.saved_messages) {
        editors['saved-messages'].setValue(formats.saved_messages.template || '');
    }
    
    // Digest Header
    if (editors['digest-header'] && formats.digest?.header) {
        editors['digest-header'].setValue(formats.digest.header.template || '');
    }
    
    // Digest Entry
    if (editors['digest-entry'] && formats.digest?.entry) {
        editors['digest-entry'].setValue(formats.digest.entry.template || '');
    }
    
    // Webhook
    if (editors['webhook'] && formats.webhook_payload) {
        editors['webhook'].setValue(formats.webhook_payload.template || '');
    }
    
    isModified = false;
    updateSaveButton();
}

function buildVariableDefinitions(defaults = {}) {
    const map = {};

    function addVariables(vars) {
        if (!vars || typeof vars !== 'object') return;
        Object.entries(vars).forEach(([key, value]) => {
            if (!map[key]) {
                map[key] = value;
            }
        });
    }

    Object.entries(defaults).forEach(([formatKey, definition]) => {
        if (!definition || typeof definition !== 'object') return;
        if (formatKey === 'digest') {
            ['header', 'entry', 'trigger_format'].forEach((sub) => {
                addVariables(definition[sub]?.variables);
            });
        } else {
            addVariables(definition.variables);
        }
    });

    // Add fallback descriptions for formatted line variables if not provided by backend
    const formattedLineDefaults = {
        'profile_line': 'Pre-formatted: `🎯 {profile_name}` when a digest profile matched (optional)',
        'sender_line': 'Pre-formatted: `👤 {sender_name}` (optional, includes newline when present)',
        'vip_line': 'Pre-formatted: `🧘 VIP` (optional, only if sender is a VIP)',
        'triggers_line': 'Pre-formatted: `⚡ {triggers_formatted}` (optional, omitted if empty)',
        'message_link_line': 'Pre-formatted: `🔗 [View](https://...)` (optional, only when message_link exists)',
        'message_line': 'Pre-formatted: `📝 {message_text}` (optional, full message line when needed)',
        'reactions_line': 'Pre-formatted: `👍 {reactions}` (optional, only when reaction count is provided)',
        'semantic_score_line': 'Pre-formatted: `🧠 {semantic_score:.2f}` (optional, AI similarity score with icon)',
        'keyword_score_line': 'Pre-formatted: `🔑 {keyword_score:.2f}` (optional, keyword match score with icon)',
    };

    // Ensure all formatted line variables have descriptions
    Object.entries(formattedLineDefaults).forEach(([key, desc]) => {
        if (!map[key]) {
            map[key] = desc;
        }
    });

    return map;
}

function renderVariablesReference() {
    const container = document.getElementById('variablesModalContent');
    if (!container) return;

    if (!formatsData || !formatsData.defaults) {
        container.innerHTML = `
            <div class="alert alert-warning small mb-0">
                <i class="bi bi-exclamation-triangle me-1"></i>
                Unable to load the variables reference right now. Please try again later.
            </div>
        `;
        return;
    }

    const definitions = buildVariableDefinitions(formatsData.defaults);
    if (Object.keys(definitions).length === 0) {
        container.innerHTML = `
            <div class="alert alert-info small mb-0">
                <i class="bi bi-info-circle me-1"></i>
                No variable descriptions are available. Check your format configuration.
            </div>
        `;
        return;
    }

    const accordionHtml = VARIABLE_CATEGORIES.map((category, index) => {
        const collapseId = `vars-${category.id}`;
        const isExpanded = index === 0;
        const collapseClass = isExpanded ? 'show' : '';
        const btnClass = isExpanded ? '' : 'collapsed';

        const rows = category.keys.map((key) => {
            const description = definitions[key] || 'Description not available.';
            return `
                <dt class="col-sm-4"><code>{${key}}</code></dt>
                <dd class="col-sm-8 small text-muted">${escapeHtml(description)}</dd>
            `;
        }).join('');

        const noteHtml = category.note ? `<div class="text-muted small">${category.note}</div>` : '';

        return `
            <div class="accordion-item">
                <h2 class="accordion-header" id="heading-${category.id}">
                    <button class="accordion-button ${btnClass}" type="button" data-bs-toggle="collapse" data-bs-target="#${collapseId}" aria-expanded="${isExpanded}" aria-controls="${collapseId}">
                        ${category.title}
                    </button>
                </h2>
                <div id="${collapseId}" class="accordion-collapse collapse ${collapseClass}" aria-labelledby="heading-${category.id}" data-bs-parent="#variablesModalContent">
                    <div class="accordion-body">
                        <dl class="row mb-0 small">
                            ${rows}
                        </dl>
                        ${noteHtml}
                    </div>
                </div>
            </div>
        `;
    }).join('');

    // Populate the accordion items directly into the container (which already has class="accordion")
    container.innerHTML = accordionHtml;
    
    // Initialize Bootstrap accordion behavior after content injection
    if (typeof bootstrap !== 'undefined') {
        const accordionElement = document.getElementById('variablesModalContent');
        if (accordionElement) {
            // Bootstrap 5 automatically initializes accordions with data-bs-toggle attributes
            // but we can ensure proper initialization by triggering a reflow
            accordionElement.offsetHeight; // Force reflow for proper rendering
        }
    }
}

/**
 * Get current formats from editors
 */
function getCurrentFormats() {
    const formats = JSON.parse(JSON.stringify(formatsData?.formats || {}));
    
    if (editors['dm-alerts']) {
        formats.dm_alerts = formats.dm_alerts || {};
        formats.dm_alerts.template = editors['dm-alerts'].getValue();
    }
    
    if (editors['saved-messages']) {
        formats.saved_messages = formats.saved_messages || {};
        formats.saved_messages.template = editors['saved-messages'].getValue();
    }
    
    if (editors['digest-header']) {
        formats.digest = formats.digest || {};
        formats.digest.header = formats.digest.header || {};
        formats.digest.header.template = editors['digest-header'].getValue();
    }
    
    if (editors['digest-entry']) {
        formats.digest = formats.digest || {};
        formats.digest.entry = formats.digest.entry || {};
        formats.digest.entry.template = editors['digest-entry'].getValue();
    }
    
    if (editors['webhook']) {
        formats.webhook_payload = formats.webhook_payload || {};
        formats.webhook_payload.template = editors['webhook'].getValue();
    }
    
    return formats;
}

/**
 * Save formats to the API
 */
async function saveFormats() {
    const formats = getCurrentFormats();
    
    try {
        const response = await fetch(`${SENTINEL_API_BASE}/message-formats`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ formats }),
        });
        
        const data = await response.json();
        
        if (data.status !== 'ok') {
            if (data.data?.validation_errors) {
                showToast(`Validation failed: ${data.data.validation_errors.join(', ')}`, 'danger');
            } else {
                showToast(data.error?.message || 'Failed to save formats', 'danger');
            }
            return false;
        }
        
        showToast('Message formats saved successfully', 'success');
        isModified = false;
        updateSaveButton();
        return true;
    } catch (error) {
        console.error('Error saving formats:', error);
        showToast('Error saving message formats', 'danger');
        return false;
    }
}

/**
 * Preview a format template
 */
async function previewFormat(formatType) {
    let template = '';
    let editorKey = formatType.replace('.', '-').replace('_', '-');
    
    // Map format types to editor keys
    const editorMap = {
        'dm_alerts': 'dm-alerts',
        'saved_messages': 'saved-messages',
        'digest.header': 'digest-header',
        'digest.entry': 'digest-entry',
        'webhook_payload': 'webhook',
    };
    
    editorKey = editorMap[formatType] || editorKey;
    
    if (editors[editorKey]) {
        template = editors[editorKey].getValue();
    }
    
    try {
        const response = await fetch(`${SENTINEL_API_BASE}/message-formats/preview`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                format_type: formatType,
                template: template,
            }),
        });
        
        const data = await response.json();
        
        if (data.status !== 'ok') {
            throw new Error(data.error?.message || 'Preview failed');
        }
        
        const previewContainer = document.getElementById(`preview-${editorKey}`);
        if (previewContainer) {
            // Convert newlines to <br> and preserve formatting
            // Also handle basic Markdown: **bold**, *italic*
            // Preserve leading spaces for indentation (e.g., profile_line)
            let rendered = escapeHtml(data.data.rendered);
            rendered = rendered
                .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')  // **bold**
                .replace(/\*(.+?)\*/g, '<em>$1</em>')              // *italic*
                .replace(/^( +)/gm, (match) => '&nbsp;'.repeat(match.length))  // Preserve leading spaces
                .replace(/\n/g, '<br>');                           // newlines
            previewContainer.innerHTML = rendered;
        }
        
    } catch (error) {
        console.error('Error previewing format:', error);
        showToast(`Preview failed: ${error.message}`, 'danger');
    }
}

/**
 * Test send a format
 */
async function testSendFormat(formatType, buttonElement) {
    let template = '';
    let editorKey = formatType.replace('.', '-').replace('_', '-');
    
    const editorMap = {
        'dm_alerts': 'dm-alerts',
        'saved_messages': 'saved-messages',
        'digest.header': 'digest-header',
        'digest.entry': 'digest-entry',
        'webhook_payload': 'webhook',
    };
    
    editorKey = editorMap[formatType] || editorKey;
    
    if (editors[editorKey]) {
        template = editors[editorKey].getValue();
    }
    
    // Show loading state on button
    const originalContent = buttonElement ? buttonElement.innerHTML : '';
    if (buttonElement) {
        buttonElement.disabled = true;
        buttonElement.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>Sending...';
    }
    
    // Show toast for feedback
    showToast('Sending test message...', 'info', 1500);
    
    try {
        // Create abort controller for timeout
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 30000); // 30 second timeout
        
        const response = await fetch(`${SENTINEL_API_BASE}/message-formats/test`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                format_type: formatType,
                template: template,
            }),
            signal: controller.signal,
        });
        
        clearTimeout(timeoutId);
        
        const data = await response.json();
        
        if (data.status !== 'ok') {
            throw new Error(data.error?.message || 'Test send failed');
        }
        
        // Success feedback
        showToast('✓ Test message sent successfully to Saved Messages', 'success', 3000);
        
    } catch (error) {
        console.error('Error sending test message:', error);
        
        // Provide detailed error feedback
        let errorMessage = 'Test send failed';
        if (error.name === 'AbortError') {
            errorMessage = 'Request timeout - The server took too long to respond. Check if Sentinel service is running.';
        } else if (error.message) {
            errorMessage = error.message;
        }
        
        showToast(`✗ ${errorMessage}`, 'danger', 5000);
    } finally {
        // Restore button state
        if (buttonElement) {
            buttonElement.disabled = false;
            buttonElement.innerHTML = originalContent;
        }
    }
}

/**
 * Export formats as YAML file
 */
async function exportFormats() {
    try {
        const response = await fetch(`${SENTINEL_API_BASE}/message-formats/export`);
        
        if (!response.ok) {
            throw new Error('Export failed');
        }
        
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'message_formats.yml';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        showToast('Formats exported successfully', 'success');
        
    } catch (error) {
        console.error('Error exporting formats:', error);
        showToast('Export failed', 'danger');
    }
}

/**
 * Import formats from YAML file
 */
async function importFormats(file) {
    try {
        const formData = new FormData();
        formData.append('file', file);
        
        const response = await fetch(`${SENTINEL_API_BASE}/message-formats/import`, {
            method: 'POST',
            body: formData,
        });
        
        const data = await response.json();
        
        if (data.status !== 'ok') {
            if (data.data?.validation_errors) {
                showToast(`Import failed: ${data.data.validation_errors.join(', ')}`, 'danger');
            } else {
                showToast(data.error?.message || 'Import failed', 'danger');
            }
            return false;
        }
        
        // Reload formats
        await loadFormats();
        populateEditors();
        
        showToast('Formats imported successfully', 'success');
        return true;
        
    } catch (error) {
        console.error('Error importing formats:', error);
        showToast('Import failed', 'danger');
        return false;
    }
}

/**
 * Reset formats to defaults
 */
async function resetFormats() {
    try {
        const response = await fetch(`${SENTINEL_API_BASE}/message-formats/reset`, {
            method: 'POST',
            headers: { 'X-Admin-Token': getAdminToken() },
        });
        
        const data = await response.json();
        
        if (data.status !== 'ok') {
            throw new Error(data.error?.message || 'Reset failed');
        }
        
        // Reload formats
        await loadFormats();
        populateEditors();
        
        showToast('Formats reset to defaults', 'success');
        
        // Close modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('resetModal'));
        if (modal) modal.hide();
        
    } catch (error) {
        console.error('Error resetting formats:', error);
        showToast(`Reset failed: ${error.message}`, 'danger');
    }
}

/**
 * Get admin token from local storage or cookie
 */
function getAdminToken() {
    return localStorage.getItem('adminToken') || '';
}

/**
 * Update save button state
 */
function updateSaveButton() {
    const btn = document.getElementById('btn-save-formats');
    if (btn) {
        if (isModified) {
            btn.classList.remove('btn-primary');
            btn.classList.add('btn-warning');
            btn.innerHTML = '<i class="bi bi-save"></i> Save All *';
        } else {
            btn.classList.remove('btn-warning');
            btn.classList.add('btn-primary');
            btn.innerHTML = '<i class="bi bi-save"></i> Save All';
        }
    }
}

/**
 * Show toast notification
 */
function showToast(message, type = 'info', duration = 5000) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-bg-${type} border-0`;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');
    
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">${escapeHtml(message)}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
    `;
    
    container.appendChild(toast);
    const bsToast = new bootstrap.Toast(toast, { autohide: true, delay: duration });
    bsToast.show();
    
    toast.addEventListener('hidden.bs.toast', () => {
        toast.remove();
    });
}

/**
 * Initialize the page
 */
async function init() {
    // Load formats first
    await loadFormats();
    
    // Initialize Monaco editors
    editors['dm-alerts'] = await initMonacoEditor('editor-dm-alerts', 'plaintext');
    editors['saved-messages'] = await initMonacoEditor('editor-saved-messages', 'markdown');
    editors['digest-header'] = await initMonacoEditor('editor-digest-header', 'plaintext');
    editors['digest-entry'] = await initMonacoEditor('editor-digest-entry', 'markdown');
    editors['webhook'] = await initMonacoEditor('editor-webhook', 'json');
    
    // Populate editors with data
    populateEditors();
    // Render the unified variables reference
    renderVariablesReference();
    
    // Set up event listeners
    setupEventListeners();
}

/**
 * Set up event listeners
 */
function setupEventListeners() {
    // Save button
    document.getElementById('btn-save-formats')?.addEventListener('click', saveFormats);
    
    // Export button
    document.getElementById('btn-export-formats')?.addEventListener('click', exportFormats);
    
    // Import button
    document.getElementById('btn-import-formats')?.addEventListener('click', () => {
        document.getElementById('import-file-input')?.click();
    });
    
    // File input change
    document.getElementById('import-file-input')?.addEventListener('change', (e) => {
        const file = e.target.files?.[0];
        if (file) {
            importFormats(file);
            e.target.value = ''; // Reset input
        }
    });
    
    // Reset button
    document.getElementById('btn-reset-formats')?.addEventListener('click', () => {
        const modal = new bootstrap.Modal(document.getElementById('resetModal'));
        modal.show();
    });
    
    // Confirm reset
    document.getElementById('btn-confirm-reset')?.addEventListener('click', resetFormats);
    
    // Preview and test buttons
    document.querySelectorAll('[data-format-type][data-action]').forEach(btn => {
        btn.addEventListener('click', () => {
            const formatType = btn.dataset.formatType;
            const action = btn.dataset.action;
            
            if (action === 'preview') {
                previewFormat(formatType);
            } else if (action === 'test') {
                testSendFormat(formatType, btn);
            }
        });
    });
    
    // Refresh messages button
    document.getElementById('btn-refresh-messages')?.addEventListener('click', loadRecentMessages);
    
    // Load messages when Digest tab is shown
    document.getElementById('digest-tab')?.addEventListener('shown.bs.tab', loadRecentMessages);
    
    // Warn before leaving with unsaved changes
    window.addEventListener('beforeunload', (e) => {
        if (isModified) {
            e.preventDefault();
            e.returnValue = '';
        }
    });
    
    // Initial load of messages if digest tab is active
    if (document.getElementById('digest-tab')?.classList.contains('active')) {
        loadRecentMessages();
    }
}

/**
 * Load recent messages from the API
 */
async function loadRecentMessages() {
    const container = document.getElementById('recent-messages-container');
    const loading = document.getElementById('messages-loading');
    const list = document.getElementById('messages-list');
    const empty = document.getElementById('messages-empty');
    
    if (!container) return;
    
    // Show loading state
    loading?.classList.remove('d-none');
    list?.classList.add('d-none');
    empty?.classList.add('d-none');
    
    try {
        const response = await fetch(`${SENTINEL_API_BASE}/alerts?limit=20`);
        const data = await response.json();
        
        if (data.status !== 'ok' || !data.data?.alerts) {
            throw new Error(data.error?.message || 'Failed to load messages');
        }
        
        const messages = data.data.alerts;
        loading?.classList.add('d-none');
        
        if (messages.length === 0) {
            empty?.classList.remove('d-none');
            return;
        }
        
        // Render messages
        list.innerHTML = messages.map((msg, idx) => `
            <div class="message-item border-bottom pb-2 mb-2 ${idx === messages.length - 1 ? 'border-bottom-0 mb-0 pb-0' : ''}">
                <div class="d-flex justify-content-between align-items-start mb-1">
                    <div>
                        <strong class="text-primary">${escapeHtml(msg.chat_title || 'Unknown Chat')}</strong>
                        <span class="text-muted small ms-2">
                            <i class="bi bi-person"></i> ${escapeHtml(msg.sender_name || 'Unknown')}
                        </span>
                    </div>
                    <div class="text-end">
                        <span class="badge bg-${getScoreBadgeClass(msg.score)}">${(msg.score * 100).toFixed(0)}%</span>
                        <small class="text-muted d-block">${formatTimestamp(msg.timestamp)}</small>
                    </div>
                </div>
                <p class="mb-1 small text-break">${escapeHtml(truncateText(msg.message_text || '', 200))}</p>
                ${Array.isArray(msg.triggers) && msg.triggers.length > 0 ? `
                    <div class="triggers small">
                        ${msg.triggers.map(t => `<span class="badge bg-secondary me-1">${escapeHtml(t)}</span>`).join('')}
                    </div>
                ` : ''}
            </div>
        `).join('');
        
        list?.classList.remove('d-none');
        
    } catch (error) {
        console.error('Error loading messages:', error);
        loading?.classList.add('d-none');
        list.innerHTML = `
            <div class="alert alert-warning mb-0">
                <i class="bi bi-exclamation-triangle me-2"></i>
                Failed to load messages: ${escapeHtml(error.message)}
            </div>
        `;
        list?.classList.remove('d-none');
    }
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Truncate text to specified length
 */
function truncateText(text, maxLength) {
    if (!text || text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
}

/**
 * Get Bootstrap badge class based on score
 */
function getScoreBadgeClass(score) {
    if (score >= 0.8) return 'success';
    if (score >= 0.6) return 'primary';
    if (score >= 0.4) return 'warning';
    return 'secondary';
}

/**
 * Format timestamp for display
 */
function formatTimestamp(timestamp) {
    if (!timestamp) return '';
    try {
        const date = new Date(timestamp);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);
        
        if (diffMins < 1) return 'just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        if (diffHours < 24) return `${diffHours}h ago`;
        if (diffDays < 7) return `${diffDays}d ago`;
        
        return date.toLocaleDateString();
    } catch {
        return timestamp;
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', init);
