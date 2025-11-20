/**
 * Global Profiles Management (Two-Layer Architecture)
 * Handles CRUD operations for global profiles in config/profiles.yml
 */

// API endpoints
const globalProfileEndpoints = {
    list: '/api/profiles/global/list',
    get: (id) => `/api/profiles/global/${id}`,
    create: '/api/profiles/global/create',
    update: (id) => `/api/profiles/global/${id}`,
    delete: (id) => `/api/profiles/global/${id}`,
    validate: '/api/profiles/global/validate',
    usage: (id) => `/api/profiles/global/${id}/usage`,
};

// State
let globalProfiles = [];
let selectedGlobalProfile = null;
let globalProfilesInitialized = false;

/**
 * Load all global profiles from API
 */
async function loadGlobalProfiles() {
    try {
        const response = await fetch(globalProfileEndpoints.list);
        
        if (!response.ok) {
            // Try to read error details from response
            let errorMessage = `HTTP ${response.status} ${response.statusText}`;
            try {
                const errorData = await response.json();
                if (errorData.message) {
                    errorMessage += `: ${errorData.message}`;
                }
            } catch (parseError) {
                // If JSON parsing fails, try text
                try {
                    const errorText = await response.text();
                    if (errorText) {
                        errorMessage += `: ${errorText.substring(0, 100)}`;
                    }
                } catch (textError) {
                    // Keep the basic error message
                }
            }
            console.error('Failed to load global profiles:', errorMessage);
            showToast('Failed to load global profiles', 'error');
            return;
        }
        
        const data = await response.json();
        
        if (data.status === 'ok') {
            globalProfiles = data.profiles || [];
            renderGlobalProfilesList();
            updateGlobalProfilesCount();
        } else {
            console.error('Failed to load global profiles:', data.message);
            showToast('Failed to load global profiles', 'error');
        }
    } catch (error) {
        console.error('Error loading global profiles:', error);
        showToast('Error loading global profiles', 'error');
    }
}

/**
 * Render the global profiles list
 */
function renderGlobalProfilesList() {
    const container = document.getElementById('global-profiles-list');
    if (!container) return;
    
    if (globalProfiles.length === 0) {
        container.innerHTML = `
            <div class="text-center text-muted p-4">
                <svg width="48" height="48" fill="currentColor" class="bi bi-collection mb-2" viewBox="0 0 16 16">
                    <path d="M2.5 3.5a.5.5 0 0 1 0-1h11a.5.5 0 0 1 0 1h-11zm2-2a.5.5 0 0 1 0-1h7a.5.5 0 0 1 0 1h-7zM0 13a1.5 1.5 0 0 0 1.5 1.5h13A1.5 1.5 0 0 0 16 13V6a1.5 1.5 0 0 0-1.5-1.5h-13A1.5 1.5 0 0 0 0 6v7zm1.5.5A.5.5 0 0 1 1 13V6a.5.5 0 0 1 .5-.5h13a.5.5 0 0 1 .5.5v7a.5.5 0 0 1-.5.5h-13z"/>
                </svg>
                <p>No global profiles defined</p>
                <p><small>Create a new profile to get started</small></p>
            </div>
        `;
        return;
    }
    
    const html = globalProfiles.map(profile => `
        <a href="#" class="list-group-item list-group-item-action ${selectedGlobalProfile?.id === profile.id ? 'active' : ''}"
           data-profile-id="${profile.id}">
            <div class="d-flex w-100 justify-content-between align-items-center">
                <div>
                    <h6 class="mb-1">${escapeHtml(profile.name || profile.id)}</h6>
                    <small class="text-muted">${profile.id}</small>
                </div>
                <div>
                    <span class="badge bg-secondary">${countTotalKeywords(profile)} keywords</span>
                </div>
            </div>
        </a>
    `).join('');
    
    container.innerHTML = html;
    
    // Add click handlers
    container.querySelectorAll('[data-profile-id]').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const profileId = item.dataset.profileId;
            loadGlobalProfile(profileId);
        });
    });
}

/**
 * Count total keywords in a profile
 */
function countTotalKeywords(profile) {
    const categories = [
        'keywords', 'action_keywords', 'decision_keywords', 'urgency_keywords',
        'release_keywords', 'security_keywords'
    ];
    
    let total = 0;
    categories.forEach(cat => {
        if (Array.isArray(profile[cat])) {
            total += profile[cat].length;
        }
    });
    
    return total;
}

/**
 * Update the profiles count badge
 */
function updateGlobalProfilesCount() {
    const countElement = document.getElementById('global-profiles-count');
    if (countElement) {
        countElement.textContent = globalProfiles.length;
    }
}

/**
 * Load a specific global profile
 */
async function loadGlobalProfile(profileId) {
    try {
        const response = await fetch(globalProfileEndpoints.get(profileId));
        
        if (!response.ok) {
            // Try to read error details from response
            let errorMessage = `HTTP ${response.status} ${response.statusText}`;
            try {
                const errorData = await response.json();
                if (errorData.message) {
                    errorMessage = errorData.message;
                }
            } catch (parseError) {
                // If JSON parsing fails, try text
                try {
                    const errorText = await response.text();
                    if (errorText) {
                        errorMessage = errorText.substring(0, 200);
                    }
                } catch (textError) {
                    // Keep the basic error message
                }
            }
            console.error('Failed to load profile:', errorMessage);
            showToast(`Failed to load profile: ${errorMessage}`, 'error');
            return;
        }
        
        const data = await response.json();
        
        if (data.status === 'ok') {
            selectedGlobalProfile = data.profile;
            populateGlobalProfileForm(data.profile);
            renderGlobalProfilesList(); // Update selection
            
            // Load usage information
            loadProfileUsage(profileId);
        } else {
            console.error('Failed to load profile:', data.message);
            showToast(`Failed to load profile: ${data.message}`, 'error');
        }
    } catch (error) {
        console.error('Error loading profile:', error);
        showToast('Error loading profile', 'error');
    }
}

/**
 * Populate the form with profile data
 */
function populateGlobalProfileForm(profile) {
    // Hide empty state, show form
    const emptyState = document.getElementById('global-profile-empty-state');
    if (emptyState) emptyState.classList.add('d-none');
    
    // Set mode to edit
    const modeInput = document.getElementById('global-profile-mode');
    if (modeInput) modeInput.value = 'edit';
    
    // Set profile ID (hidden)
    const idInput = document.getElementById('global-profile-id');
    if (idInput) idInput.value = profile.id;
    
    // Set profile ID display (readonly)
    const idDisplayInput = document.getElementById('global-profile-id-input');
    if (idDisplayInput) {
        idDisplayInput.value = profile.id;
        idDisplayInput.readOnly = true;
    }
    
    // Set name
    const nameElem = document.getElementById('global-profile-name');
    if (nameElem) nameElem.value = profile.name || '';
    
    // Set keyword categories
    const categories = {
        'keywords': 'global-keywords',
        'action_keywords': 'global-action-keywords',
        'decision_keywords': 'global-decision-keywords',
        'urgency_keywords': 'global-urgency-keywords',
        'release_keywords': 'global-release-keywords',
        'security_keywords': 'global-security-keywords'
    };
    
    Object.entries(categories).forEach(([key, elemId]) => {
        const elem = document.getElementById(elemId);
        if (elem && Array.isArray(profile[key])) {
            elem.value = profile[key].join('\n');
        }
    });
    
    // Set scoring weights
    const weights = profile.scoring_weights || {};
    const weightMappings = {
        'keywords': 'weight-keywords',
        'action': 'weight-action',
        'decision': 'weight-decision',
        'urgency': 'weight-urgency',
        'security': 'weight-security',
        'release': 'weight-release'
    };
    
    Object.entries(weightMappings).forEach(([key, elemId]) => {
        const elem = document.getElementById(elemId);
        if (elem) {
            elem.value = weights[key] || 1.0;
            elem.dispatchEvent(new Event('input')); // Trigger update of display value
        }
    });
    
    // Set detection flags
    const detectCodes = document.getElementById('global-detect-codes');
    const detectDocs = document.getElementById('global-detect-documents');
    const prioritizePinned = document.getElementById('global-prioritize-pinned');
    if (detectCodes) detectCodes.checked = profile.detect_codes !== false;
    if (detectDocs) detectDocs.checked = profile.detect_documents !== false;
    if (prioritizePinned) prioritizePinned.checked = profile.prioritize_pinned !== false;
    
    // Show delete button
    const deleteBtn = document.getElementById('btn-delete-global-profile');
    if (deleteBtn) deleteBtn.classList.remove('d-none');
}

/**
 * Load and display profile usage information
 */
async function loadProfileUsage(profileId) {
    try {
        const response = await fetch(globalProfileEndpoints.usage(profileId));
        
        if (!response.ok) {
            console.error('Failed to load profile usage: HTTP', response.status);
            const usageDiv = document.getElementById('global-profile-usage');
            if (usageDiv) usageDiv.classList.add('d-none');
            return;
        }
        
        const data = await response.json();
        
        if (data.status === 'ok' && data.in_use) {
            const usageDiv = document.getElementById('global-profile-usage');
            const contentDiv = document.getElementById('global-profile-usage-content');
            
            if (usageDiv && contentDiv) {
                let html = '<ul class="mb-0 mt-2">';
                
                if (data.usage.channels.length > 0) {
                    html += '<li><strong>Channels:</strong> ' + 
                           data.usage.channels.map(c => escapeHtml(c.name)).join(', ') + 
                           '</li>';
                }
                
                if (data.usage.users.length > 0) {
                    html += '<li><strong>Users:</strong> ' + 
                           data.usage.users.map(u => escapeHtml(u.name)).join(', ') + 
                           '</li>';
                }
                
                html += '</ul>';
                contentDiv.innerHTML = html;
                usageDiv.classList.remove('d-none');
            }
        } else {
            const usageDiv = document.getElementById('global-profile-usage');
            if (usageDiv) usageDiv.classList.add('d-none');
        }
    } catch (error) {
        console.error('Error loading profile usage:', error);
    }
}

/**
 * Reset the global profile form
 */
function resetGlobalProfileForm() {
    selectedGlobalProfile = null;
    
    // Show empty state
    const emptyState = document.getElementById('global-profile-empty-state');
    if (emptyState) emptyState.classList.remove('d-none');
    
    // Set mode to create
    const modeInput = document.getElementById('global-profile-mode');
    if (modeInput) modeInput.value = 'create';
    
    // Clear ID
    const idInput = document.getElementById('global-profile-id');
    if (idInput) idInput.value = '';
    
    const idDisplayInput = document.getElementById('global-profile-id-input');
    if (idDisplayInput) {
        idDisplayInput.value = '';
        idDisplayInput.readOnly = false;
    }
    
    // Reset form
    document.getElementById('global-profile-form').reset();
    
    // Hide delete button and usage info
    document.getElementById('btn-delete-global-profile')?.classList.add('d-none');
    document.getElementById('global-profile-usage')?.classList.add('d-none');
    
    // Deselect from list
    renderGlobalProfilesList();
}

/**
 * Save global profile (create or update)
 */
async function saveGlobalProfile(event) {
    event.preventDefault();
    
    const mode = document.getElementById('global-profile-mode').value;
    const profileIdInput = document.getElementById('global-profile-id-input');
    const profileId = profileIdInput.value.trim();
    
    if (!profileId) {
        showToast('Profile ID is required', 'error');
        profileIdInput.focus();
        return;
    }
    
    // Collect form data
    const profileData = {
        name: document.getElementById('global-profile-name').value.trim(),
        keywords: parseKeywords('global-keywords'),
        action_keywords: parseKeywords('global-action-keywords'),
        decision_keywords: parseKeywords('global-decision-keywords'),
        urgency_keywords: parseKeywords('global-urgency-keywords'),
        security_keywords: parseKeywords('global-security-keywords'),
        release_keywords: parseKeywords('global-release-keywords'),
        scoring_weights: {
            keywords: parseFloat(document.getElementById('weight-keywords').value),
            action: parseFloat(document.getElementById('weight-action').value),
            decision: parseFloat(document.getElementById('weight-decision').value),
            urgency: parseFloat(document.getElementById('weight-urgency').value),
            security: parseFloat(document.getElementById('weight-security').value),
            release: parseFloat(document.getElementById('weight-release').value),
        },
        detect_codes: document.getElementById('global-detect-codes').checked,
        detect_documents: document.getElementById('global-detect-documents').checked,
        prioritize_pinned: document.getElementById('global-prioritize-pinned').checked,
    };
    
    try {
        let response;
        
        if (mode === 'create') {
            // Create new profile
            response = await fetch(globalProfileEndpoints.create, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: profileId, profile: profileData })
            });
        } else {
            // Update existing profile
            response = await fetch(globalProfileEndpoints.update(profileId), {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ profile: profileData })
            });
        }
        
        if (!response.ok) {
            // Try to read error details from response
            let errorMessage = `HTTP ${response.status} ${response.statusText}`;
            try {
                const errorData = await response.json();
                if (errorData.message) {
                    errorMessage = errorData.message;
                }
            } catch (parseError) {
                // If JSON parsing fails, try text
                try {
                    const errorText = await response.text();
                    if (errorText) {
                        errorMessage = errorText.substring(0, 200);
                    }
                } catch (textError) {
                    // Keep the basic error message
                }
            }
            showToast(`Failed to ${mode === 'create' ? 'create' : 'update'} profile: ${errorMessage}`, 'error');
            return;
        }
        
        const data = await response.json();
        
        if (data.status === 'ok') {
            showToast(`Profile ${mode === 'create' ? 'created' : 'updated'} successfully`, 'success');
            await loadGlobalProfiles();
            await loadGlobalProfile(profileId);
        } else {
            showToast(`Failed to ${mode === 'create' ? 'create' : 'update'} profile: ${data.message}`, 'error');
        }
    } catch (error) {
        console.error('Error saving profile:', error);
        showToast('Error saving profile', 'error');
    }
}

/**
 * Delete global profile
 */
async function deleteGlobalProfile() {
    if (!selectedGlobalProfile) return;
    
    if (!confirm(`Are you sure you want to delete the profile "${selectedGlobalProfile.name}"?\n\nThis cannot be undone.`)) {
        return;
    }
    
    try {
        const response = await fetch(globalProfileEndpoints.delete(selectedGlobalProfile.id), {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            // Try to read error details from response
            let errorMessage = `HTTP ${response.status} ${response.statusText}`;
            try {
                const errorData = await response.json();
                if (errorData.message) {
                    errorMessage = errorData.message;
                }
            } catch (parseError) {
                // If JSON parsing fails, try text
                try {
                    const errorText = await response.text();
                    if (errorText) {
                        errorMessage = errorText.substring(0, 200);
                    }
                } catch (textError) {
                    // Keep the basic error message
                }
            }
            showToast(`Failed to delete profile: ${errorMessage}`, 'error');
            return;
        }
        
        const data = await response.json();
        
        if (data.status === 'ok') {
            showToast('Profile deleted successfully', 'success');
            resetGlobalProfileForm();
            await loadGlobalProfiles();
        } else {
            showToast(`Failed to delete profile: ${data.message}`, 'error');
        }
    } catch (error) {
        console.error('Error deleting profile:', error);
        showToast('Error deleting profile', 'error');
    }
}

/**
 * Parse keywords from textarea (one per line)
 */
function parseKeywords(elemId) {
    const elem = document.getElementById(elemId);
    if (!elem) return [];
    
    return elem.value
        .split(/\r?\n/)
        .map(line => line.trim())
        .filter(line => line.length > 0);
}

/**
 * Search/filter profiles
 */
function filterGlobalProfiles(searchTerm) {
    const items = document.querySelectorAll('#global-profiles-list [data-profile-id]');
    const term = searchTerm.toLowerCase();
    
    items.forEach(item => {
        const profileId = item.dataset.profileId;
        const profile = globalProfiles.find(p => p.id === profileId);
        
        if (!profile) {
            item.classList.add('d-none');
            return;
        }
        
        const matches = 
            profile.id.toLowerCase().includes(term) ||
            (profile.name && profile.name.toLowerCase().includes(term));
        
        item.classList.toggle('d-none', !matches);
    });
}

/**
 * Initialize global profiles management
 */
function initGlobalProfiles() {
    // Guard against duplicate initialization
    if (globalProfilesInitialized) {
        return;
    }
    
    // Mark as initialized immediately to prevent race conditions
    globalProfilesInitialized = true;
    
    // Load profiles on page load
    loadGlobalProfiles();
    
    // New profile button
    const newBtn = document.getElementById('btn-new-global-profile');
    if (newBtn) {
        newBtn.addEventListener('click', resetGlobalProfileForm);
    }
    
    // Form submit
    const form = document.getElementById('global-profile-form');
    if (form) {
        form.addEventListener('submit', saveGlobalProfile);
    }
    
    // Reset button
    const resetBtn = document.getElementById('btn-reset-global-profile');
    if (resetBtn) {
        resetBtn.addEventListener('click', resetGlobalProfileForm);
    }
    
    // Delete button
    const deleteBtn = document.getElementById('btn-delete-global-profile');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', deleteGlobalProfile);
    }
    
    // Search
    const searchInput = document.getElementById('global-profiles-search');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => filterGlobalProfiles(e.target.value));
    }
    
    // Weight sliders - update display values
    const weightSliders = [
        'weight-keywords', 'weight-action', 'weight-decision',
        'weight-urgency', 'weight-security', 'weight-release'
    ];
    
    weightSliders.forEach(id => {
        const slider = document.getElementById(id);
        if (slider) {
            slider.addEventListener('input', (e) => {
                const valueSpan = document.getElementById(`${id}-value`);
                if (valueSpan) {
                    valueSpan.textContent = parseFloat(e.target.value).toFixed(1);
                }
            });
        }
    });
}

// Initialize when DOM is ready and tab is shown
document.addEventListener('DOMContentLoaded', () => {
    // Initialize immediately if global profiles tab is active
    const globalTab = document.getElementById('global-profiles-tab');
    if (globalTab && globalTab.classList.contains('active')) {
        initGlobalProfiles();
    }
    
    // Or initialize when tab is shown
    if (globalTab) {
        globalTab.addEventListener('shown.bs.tab', () => {
            initGlobalProfiles();
        });
    }
});

/**
 * Utility: Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Utility: Show toast notification (assumes global showToast function exists)
 */
if (typeof showToast === 'undefined') {
    window.showToast = function(message, type = 'info') {
        console.log(`[${type.toUpperCase()}] ${message}`);
        alert(message);
    };
}
