/**
 * Alert Profiles Module
 * Handles CRUD operations for keyword-based alert profiles
 * 
 * Dependencies: shared_utils.js, entity_selector.js, digest_editor.js
 * 
 * Public API:
 * - loadAlertProfiles()
 * - loadAlertProfile(profileId)
 * - saveAlertProfile(event)
 * - deleteAlertProfile()
 * - toggleAlertProfile(profileId, enabled)
 * - backtestAlertProfile()
 * - resetAlertProfileForm()
 * - newAlertProfile()
 * - filterAlertProfiles(searchTerm)
 * - exportAlertProfile(profileId)
 * - importAlertProfile()
 * - duplicateAlertProfile(profileId)
 */

(function() {
    'use strict';
    
    // Module state
    let currentAlertProfile = null;
    let allAlertProfiles = [];
    let filteredAlertProfiles = [];
    let alertProfileEndpoints = {};
    let alertProfilesLoading = false;
    
    /**
     * Initialize the Alert Profiles module
     * @param {Object} endpoints - API endpoint configuration
     */
    function init(endpoints) {
        alertProfileEndpoints = endpoints;
        
        // Attach max score calculator listeners after DOM is ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', attachMaxScoreListeners);
        } else {
            attachMaxScoreListeners();
        }
    }
    
    /**
     * Load all alert profiles from the server
     */
    async function loadAlertProfiles() {
        alertProfilesLoading = true;
        renderAlertProfiles([]);
        try {
            const response = await fetch(alertProfileEndpoints.list);
            if (!response.ok) throw new Error("Failed to load alert profiles");
            const data = await response.json();
            
            allAlertProfiles = data.profiles || [];
            filteredAlertProfiles = [...allAlertProfiles];
            alertProfilesLoading = false;
            
            // Update profile count
            const countEl = document.getElementById("alert-profiles-count");
            if (countEl) {
                countEl.textContent = allAlertProfiles.length;
            }
            
            renderAlertProfiles(filteredAlertProfiles);
        } catch (error) {
            console.error("Failed to load alert profiles:", error);
            window.SharedUtils.showToast("Failed to load alert profiles", "error");
            alertProfilesLoading = false;
            renderAlertProfiles(filteredAlertProfiles);
        }
    }
    
    /**
     * Render alert profiles list
     * @param {Array} profiles - Array of profile objects to render
     */
    function renderAlertProfiles(profiles) {
        const listEl = document.getElementById("alert-profiles-list");
        if (!listEl) return;
        
        if (alertProfilesLoading) {
            listEl.innerHTML = `
                <div class="text-center text-muted p-4 alert-profiles-loading">
                    <svg width="48" height="48" fill="currentColor" class="bi bi-collection mb-2" viewBox="0 0 16 16">
                        <path d="M2.5 3.5a.5.5 0 0 1 0-1h11a.5.5 0 0 1 0 1h-11zm2-2a.5.5 0 0 1 0-1h7a.5.5 0 0 1 0 1h-7zM0 13a1.5 1.5 0 0 0 1.5 1.5h13A1.5 1.5 0 0 0 16 13V6a1.5 1.5 0 0 0-1.5-1.5h-13A1.5 1.5 0 0 0 0 6v7zm1.5.5A.5.5 0 0 1 1 13V6a.5.5 0 0 1 .5-.5h13a.5.5 0 0 1 .5.5v7a.5.5 0 0 1-.5.5h-13z"/>
                    </svg>
                    <p class="mb-2">Loading alert profiles...</p>
                    <small>Create your first profile to get started</small>
                    <div class="spinner-border text-primary mt-3" role="status" aria-label="Loading alert profiles"></div>
                </div>
            `;
            return;
        }

        if (!profiles || profiles.length === 0) {
            listEl.innerHTML = `
                <div class="text-center text-muted p-4">
                    <svg width="48" height="48" fill="currentColor" class="bi bi-collection mb-2" viewBox="0 0 16 16">
                        <path d="M2.5 3.5a.5.5 0 0 1 0-1h11a.5.5 0 0 1 0 1h-11zm2-2a.5.5 0 0 1 0-1h7a.5.5 0 0 1 0 1h-7zM0 13a1.5 1.5 0 0 0 1.5 1.5h13A1.5 1.5 0 0 0 16 13V6a1.5 1.5 0 0 0-1.5-1.5h-13A1.5 1.5 0 0 0 0 6v7zm1.5.5A.5.5 0 0 1 1 13V6a.5.5 0 0 1 .5-.5h13a.5.5 0 0 1 .5.5v7a.5.5 0 0 1-.5.5h-13z"/>
                    </svg>
                    <p class="mb-2">No alert profiles yet</p>
                    <small>Create your first profile to get started</small>
                </div>
            `;
            return;
        }
        
        listEl.innerHTML = profiles.map(profile => {
            // Calculate metadata
            const keywordCount = (profile.action_keywords || []).length + 
                               (profile.decision_keywords || []).length +
                               (profile.urgency_keywords || []).length +
                               (profile.importance_keywords || []).length +
                               (profile.release_keywords || []).length +
                               (profile.security_keywords || []).length +
                               (profile.risk_keywords || []).length +
                               (profile.opportunity_keywords || []).length +
                               (profile.keywords || []).length;
            
            const entityCount = (profile.channels || []).length + (profile.users || []).length;
            const scheduleCount = (profile.digest_schedules || []).length;
            
            const hasActivity = profile.last_triggered_at;
            const activityIndicator = hasActivity ? '<span class="alert-profile-activity" title="Recently triggered"></span>' : '';
            
            const lastUpdated = profile.updated_at ? 
                window.SharedUtils.formatDate(profile.updated_at) : 
                'Never';
            
            return `
                <div class="list-group-item list-group-item-action alert-profile-item ${currentAlertProfile && currentAlertProfile.id === profile.id ? 'active' : ''}" 
                        data-profile-id="${profile.id}"
                        role="button"
                        tabindex="0"
                        onclick="window.AlertProfiles.loadAlertProfile('${profile.id}')"
                        onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.AlertProfiles.loadAlertProfile('${profile.id}');}">
                    <div class="d-flex w-100 justify-content-between align-items-start">
                        <div class="alert-profile-item-content flex-grow-1 min-w-0 me-2">
                            <div class="d-flex align-items-center gap-2 mb-1">
                                <h6 class="mb-0 text-truncate">${window.SharedUtils.escapeHtml(profile.name)}</h6>
                                <span class="badge ${profile.enabled ? 'bg-success' : 'bg-secondary'} badge-sm">${profile.enabled ? 'ON' : 'OFF'}</span>
                            </div>
                            ${profile.description ? `<p class="mb-1 small text-muted alert-profile-description">${window.SharedUtils.escapeHtml(profile.description)}</p>` : ''}
                            <div class="d-flex gap-2 flex-wrap">
                                <small class="text-muted">ID: ${profile.id}</small>
                                ${keywordCount > 0 ? `<small class="text-muted">🔑 ${keywordCount}</small>` : ''}
                                ${entityCount > 0 ? `<small class="text-muted">👥 ${entityCount}</small>` : ''}
                                ${scheduleCount > 0 ? `<small class="text-muted">📅 ${scheduleCount}</small>` : ''}
                                <small class="text-muted">⏰ ${lastUpdated}</small>
                                ${(profile.tags || []).length > 0 ? `<small class="text-muted">🏷️ ${(profile.tags || []).map(t => window.SharedUtils.escapeHtml(t)).join(', ')}</small>` : ''}
                            </div>
                        </div>
                        <div class="alert-profile-item-actions d-flex flex-column align-items-end gap-2">
                            <div class="form-check form-switch mb-0" onclick="event.stopPropagation()">
                                <input class="form-check-input" type="checkbox" 
                                       ${profile.enabled ? 'checked' : ''}
                                       onchange="window.AlertProfiles.toggleAlertProfile('${profile.id}', this.checked)"
                                       title="Enable/Disable">
                            </div>
                            <div class="btn-group btn-group-sm alert-profile-actions" role="group" onclick="event.stopPropagation()">
                                <button type="button" class="btn btn-outline-secondary" 
                                        onclick="window.AlertProfiles.backtestAlertProfile('${profile.id}')"
                                        title="Run Backtest">
                                    <svg width="14" height="14" fill="currentColor" viewBox="0 0 16 16">
                                        <path d="M8 2a.5.5 0 0 1 .5.5V4a.5.5 0 0 1-1 0V2.5A.5.5 0 0 1 8 2zM3.732 3.732a.5.5 0 0 1 .707 0l.915.914a.5.5 0 1 1-.708.708l-.914-.915a.5.5 0 0 1 0-.707zM2 8a.5.5 0 0 1 .5-.5h1.586a.5.5 0 0 1 0 1H2.5A.5.5 0 0 1 2 8zm9.5 0a.5.5 0 0 1 .5-.5h1.5a.5.5 0 0 1 0 1H12a.5.5 0 0 1-.5-.5zm.754-4.246a.389.389 0 0 0-.527-.02L7.547 7.31A.91.91 0 1 0 8.85 8.569l3.434-4.297a.389.389 0 0 0-.029-.518z"/>
                                        <path fill-rule="evenodd" d="M6.664 15.889A8 8 0 1 1 9.336.11a8 8 0 0 1-2.672 15.78zm-4.665-4.283A11.945 11.945 0 0 1 8 10c2.186 0 4.236.585 6.001 1.606a7 7 0 1 0-12.002 0z"/>
                                    </svg>
                                </button>
                                <button type="button" class="btn btn-outline-secondary" 
                                        onclick="window.AlertProfiles.importAlertProfile()"
                                        title="Import from JSON">
                                    <svg width="14" height="14" fill="currentColor" viewBox="0 0 16 16">
                                        <path d="M8.5 11.5a.5.5 0 0 1-1 0V7.707L6.354 8.854a.5.5 0 1 1-.708-.708l2-2a.5.5 0 0 1 .708 0l2 2a.5.5 0 0 1-.708.708L8.5 7.707V11.5z"/>
                                        <path d="M14 14V4.5L9.5 0H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2zM9.5 3A1.5 1.5 0 0 0 11 4.5h2V14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1h5.5v2z"/>
                                    </svg>
                                </button>
                                <button type="button" class="btn btn-outline-secondary" 
                                        onclick="window.AlertProfiles.exportAlertProfile('${profile.id}')"
                                        title="Export as JSON">
                                    <svg width="14" height="14" fill="currentColor" viewBox="0 0 16 16">
                                        <path d="M8.5 6.5a.5.5 0 0 0-1 0v3.793L6.354 9.146a.5.5 0 1 0-.708.708l2 2a.5.5 0 0 0 .708 0l2-2a.5.5 0 0 0-.708-.708L8.5 10.293V6.5z"/>
                                        <path d="M14 14V4.5L9.5 0H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2zM9.5 3A1.5 1.5 0 0 0 11 4.5h2V14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1h5.5v2z"/>
                                    </svg>
                                </button>
                                <button type="button" class="btn btn-outline-secondary" 
                                        onclick="window.AlertProfiles.duplicateAlertProfile('${profile.id}')"
                                        title="Duplicate">
                                    <svg width="14" height="14" fill="currentColor" viewBox="0 0 16 16">
                                        <path d="M4 2a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V2zm2-1a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V2a1 1 0 0 0-1-1H6zM2 5a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-1h1v1a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h1v1H2z"/>
                                    </svg>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }
    
    /**
     * Filter alert profiles by search term
     * @param {string} searchTerm - Search term to filter by
     */
    function filterAlertProfiles(searchTerm) {
        const term = searchTerm.toLowerCase().trim();
        
        if (!term) {
            filteredAlertProfiles = [...allAlertProfiles];
        } else {
            filteredAlertProfiles = allAlertProfiles.filter(profile => {
                const name = (profile.name || '').toLowerCase();
                const desc = (profile.description || '').toLowerCase();
                const keywords = [
                    ...(profile.action_keywords || []),
                    ...(profile.decision_keywords || []),
                    ...(profile.urgency_keywords || []),
                    ...(profile.importance_keywords || []),
                    ...(profile.release_keywords || []),
                    ...(profile.security_keywords || []),
                    ...(profile.risk_keywords || []),
                    ...(profile.opportunity_keywords || []),
                    ...(profile.keywords || [])
                ].join(' ').toLowerCase();
                
                return name.includes(term) || desc.includes(term) || keywords.includes(term);
            });
        }
        
        renderAlertProfiles(filteredAlertProfiles);
    }
    
    /**
     * Export alert profile as JSON file
     * @param {string} profileId - Profile ID to export
     */
    function exportAlertProfile(profileId) {
        const profile = allAlertProfiles.find(p => String(p.id) === String(profileId));
        if (!profile) return;
        
        const dataStr = JSON.stringify(profile, null, 2);
        const dataBlob = new Blob([dataStr], { type: 'application/json' });
        const url = URL.createObjectURL(dataBlob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `alert-profile-${profile.name.replace(/[^a-z0-9]/gi, '-').toLowerCase()}.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        
        window.SharedUtils.showToast('Profile exported', 'success');
    }
    
    /**
     * Import an alert profile from JSON file
     * Validates that the file contains an Alert profile (not Interest profile)
     * @returns {Promise<void>}
     */
    async function importAlertProfile() {
        // Create a temporary file input
        const fileInput = document.createElement('input');
        fileInput.type = 'file';
        fileInput.accept = '.json';
        fileInput.style.display = 'none';
        
        fileInput.onchange = async (event) => {
            const file = event.target.files[0];
            if (!file) return;
            
            try {
                const text = await file.text();
                const profile = JSON.parse(text);
                
                // Validate this is an Alert profile
                // Alert profiles have keyword categories and detection flags
                // Interest profiles have positive_samples, negative_samples, threshold
                const isAlertProfile = (
                    ('action_keywords' in profile || 'detect_questions' in profile || 
                     'security_keywords' in profile || 'urgency_keywords' in profile ||
                     'keywords' in profile) &&
                    !('positive_samples' in profile && 'threshold' in profile)
                );
                
                if (!isAlertProfile) {
                    window.SharedUtils.showToast(
                        'Invalid file: This appears to be an Interest profile. Please import it in the Interest Profiles section.',
                        'error'
                    );
                    return;
                }
                
                // Remove timestamps and ID to create as new profile
                const { id: _omitId, created_at: _omitCreated, updated_at: _omitUpdated, ...rest } = profile;
                
                const importedProfile = {
                    ...rest,
                    name: `${rest.name || 'Imported Profile'}`,
                    enabled: false, // Start disabled for safety
                    created_at: new Date().toISOString(),
                    updated_at: new Date().toISOString()
                };
                
                // Save the profile
                const response = await fetch(alertProfileEndpoints.upsert, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(importedProfile)
                });
                
                if (!response.ok) throw new Error('Failed to import profile');
                
                window.SharedUtils.showToast(`Imported: ${importedProfile.name}`, 'success');
                
                // Reload list
                await loadAlertProfiles();
            } catch (error) {
                console.error('Failed to import profile:', error);
                if (error instanceof SyntaxError) {
                    window.SharedUtils.showToast('Invalid JSON file', 'error');
                } else {
                    window.SharedUtils.showToast('Failed to import profile', 'error');
                }
            } finally {
                document.body.removeChild(fileInput);
            }
        };
        
        document.body.appendChild(fileInput);
        fileInput.click();
    }
    
    /**
     * Duplicate an existing alert profile
     * @param {string} profileId - Profile ID to duplicate
     */
    async function duplicateAlertProfile(profileId) {
        const profile = allAlertProfiles.find(p => String(p.id) === String(profileId));
        if (!profile) {
            window.SharedUtils.showToast("Profile not found", "error");
            return;
        }
        
        // Remove identifiers so backend creates a new record
        const { id: _omitId, created_at: _omitCreated, updated_at: _omitUpdated, ...rest } = profile;
        const duplicatedProfile = {
            ...rest,
            name: `${profile.name} (Copy)`,
            enabled: false
        };
        
        try {
            const response = await fetch(alertProfileEndpoints.upsert, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(duplicatedProfile)
            });
            
            if (!response.ok) throw new Error('Failed to duplicate profile');
            
            window.SharedUtils.showToast('Profile duplicated', 'success');
            await loadAlertProfiles();
        } catch (error) {
            console.error('Failed to duplicate profile:', error);
            window.SharedUtils.showToast('Failed to duplicate profile', 'error');
        }
    }
    
    /**
     * Load a specific alert profile into the form
     * @param {string} profileId - Profile ID to load
     */
    async function loadAlertProfile(profileId) {
        try {
            const response = await fetch(`${alertProfileEndpoints.get}?id=${encodeURIComponent(profileId)}`);
            if (!response.ok) throw new Error("Failed to load alert profile");
            const data = await response.json();
            
            currentAlertProfile = data.profile;
            
            // Populate form
            document.getElementById("alert-profile-id").value = data.profile.id;
            document.getElementById("alert-profile-id-display").value = data.profile.id || "";
            document.getElementById("alert-name").value = data.profile.name || "";
            document.getElementById("alert-description").value = data.profile.description || "";
            document.getElementById("alert-enabled").value = data.profile.enabled !== false ? "true" : "false";
            
            // Keywords
            document.getElementById("alert-action-keywords").value = (data.profile.action_keywords || []).join(", ");
            document.getElementById("alert-decision-keywords").value = (data.profile.decision_keywords || []).join(", ");
            document.getElementById("alert-urgency-keywords").value = (data.profile.urgency_keywords || []).join(", ");
            document.getElementById("alert-importance-keywords").value = (data.profile.importance_keywords || []).join(", ");
            document.getElementById("alert-release-keywords").value = (data.profile.release_keywords || []).join(", ");
            document.getElementById("alert-security-keywords").value = (data.profile.security_keywords || []).join(", ");
            document.getElementById("alert-risk-keywords").value = (data.profile.risk_keywords || []).join(", ");
            document.getElementById("alert-opportunity-keywords").value = (data.profile.opportunity_keywords || []).join(", ");
            document.getElementById("alert-keywords").value = (data.profile.keywords || []).join(", ");
            
            // Detection settings
            document.getElementById("alert-min-score").value = data.profile.min_score || 1.0;
            document.getElementById("alert-vip-senders").value = (data.profile.vip_senders || []).join(", ");
            document.getElementById("alert-excluded-users").value = (data.profile.excluded_users || []).join(", ");
            document.getElementById("alert-detect-questions").checked = data.profile.detect_questions || false;
            document.getElementById("alert-detect-mentions").checked = data.profile.detect_mentions || false;
            document.getElementById("alert-detect-links").checked = data.profile.detect_links || false;
            document.getElementById("alert-require-forwarded").checked = data.profile.require_forwarded || false;
            
            // Advanced detection settings (consistent with basic toggles: default to false for new profiles)
            document.getElementById("alert-detect-codes").checked = data.profile.detect_codes || false;
            document.getElementById("alert-detect-documents").checked = data.profile.detect_documents || false;
            document.getElementById("alert-detect-polls").checked = data.profile.detect_polls || false;
            document.getElementById("alert-prioritize-pinned").checked = data.profile.prioritize_pinned || false;
            document.getElementById("alert-prioritize-admin").checked = data.profile.prioritize_admin || false;
            
            // Set channel and user selections
            if (window.EntitySelector) {
                window.EntitySelector.setSelectedEntityIds('alert', data.profile.channels || [], data.profile.users || []);
            }
            
            // Set selected webhooks
            const webhookSelect = document.getElementById('alert-webhooks');
            if (webhookSelect && data.profile.webhooks) {
                // Deselect all first
                Array.from(webhookSelect.options).forEach(opt => opt.selected = false);
                // Select webhooks from profile
                data.profile.webhooks.forEach(webhookService => {
                    const option = Array.from(webhookSelect.options).find(opt => opt.value === webhookService);
                    if (option) option.selected = true;
                });
            }
            
            // Populate digest configuration
            if (window.DigestEditor && data.profile.digest_config) {
                window.DigestEditor.populateDigestConfigInForm(data.profile.digest_config, 'alert-');
            } else if (window.DigestEditor) {
                window.DigestEditor.populateDigestConfigInForm(null, 'alert-');
            }
            
            // Update buttons
            document.getElementById("alert-save-btn-text").textContent = "Update Profile";
            document.getElementById("btn-delete-alert-profile").classList.remove("d-none");
            
            // Highlight active item
            document.querySelectorAll(".alert-profile-item").forEach(item => {
                item.classList.remove("active");
            });
            document.querySelector(`[data-profile-id="${profileId}"]`)?.closest('.alert-profile-item')?.classList.add('active');
            
            // Update max score display after loading profile
            updateMaxScoreDisplay();
            
            window.SharedUtils.showToast(`Loaded: ${data.profile.name}`, "info");
        } catch (error) {
            console.error("Failed to load alert profile:", error);
            window.SharedUtils.showToast("Failed to load alert profile", "error");
        }
    }
    
    /**
     * Save alert profile (create or update)
     * @param {Event} event - Form submit event
     */
    async function saveAlertProfile(event) {
        event.preventDefault();
        
        const profileData = {
            id: document.getElementById("alert-profile-id").value || null,
            name: document.getElementById("alert-name").value.trim(),
            description: document.getElementById("alert-description").value.trim(),
            enabled: document.getElementById("alert-enabled").value === "true",
            action_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-action-keywords").value),
            decision_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-decision-keywords").value),
            urgency_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-urgency-keywords").value),
            importance_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-importance-keywords").value),
            release_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-release-keywords").value),
            security_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-security-keywords").value),
            risk_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-risk-keywords").value),
            opportunity_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-opportunity-keywords").value),
            keywords: window.SharedUtils.parseCSV(document.getElementById("alert-keywords").value),
            min_score: parseFloat(document.getElementById("alert-min-score").value) || 1.0,
            vip_senders: window.SharedUtils.parseCSV(document.getElementById("alert-vip-senders").value),
            excluded_users: window.SharedUtils.parseCSV(document.getElementById("alert-excluded-users").value),
            detect_questions: document.getElementById("alert-detect-questions").checked,
            detect_mentions: document.getElementById("alert-detect-mentions").checked,
            detect_links: document.getElementById("alert-detect-links").checked,
            require_forwarded: document.getElementById("alert-require-forwarded").checked,
            detect_codes: document.getElementById("alert-detect-codes").checked,
            detect_documents: document.getElementById("alert-detect-documents").checked,
            detect_polls: document.getElementById("alert-detect-polls").checked,
            prioritize_pinned: document.getElementById("alert-prioritize-pinned").checked,
            prioritize_admin: document.getElementById("alert-prioritize-admin").checked
        };
        
        // Get selected entities
        if (window.EntitySelector) {
            profileData.channels = window.EntitySelector.getSelectedEntityIds('alert', 'channels');
            profileData.users = window.EntitySelector.getSelectedEntityIds('alert', 'users');
        }
        
        // Get selected webhooks (multiple selection)
        const webhookSelect = document.getElementById('alert-webhooks');
        if (webhookSelect) {
            profileData.webhooks = Array.from(webhookSelect.selectedOptions).map(opt => opt.value);
        }
        
        // Extract digest configuration
        if (window.DigestEditor) {
            const digestConfig = window.DigestEditor.extractDigestConfigFromForm('alert-');
            if (digestConfig && digestConfig.schedules && digestConfig.schedules.length > 0) {
                profileData.digest_config = digestConfig;
            }
        }
        
        if (!profileData.name) {
            window.SharedUtils.showToast("Profile name is required", "warning");
            return;
        }
        
        try {
            const response = await fetch(alertProfileEndpoints.upsert, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(profileData)
            });
            
            if (!response.ok) throw new Error("Failed to save alert profile");
            
            window.SharedUtils.showToast(profileData.id ? "Profile updated" : "Profile created", "success");
            await loadAlertProfiles();
            
            if (!profileData.id) {
                resetAlertProfileForm();
            }
        } catch (error) {
            console.error("Failed to save alert profile:", error);
            window.SharedUtils.showToast("Failed to save alert profile", "error");
        }
    }
    
    /**
     * Delete the current alert profile
     */
    async function deleteAlertProfile() {
        const profileId = document.getElementById("alert-profile-id").value;
        if (!profileId) return;
        
        const profileName = document.getElementById("alert-name").value;
        if (!confirm(`Delete profile "${profileName}"?`)) return;
        
        try {
            const response = await fetch(`${alertProfileEndpoints.delete}?id=${encodeURIComponent(profileId)}`, {
                method: "DELETE"
            });
            
            if (!response.ok) throw new Error("Failed to delete alert profile");
            
            window.SharedUtils.showToast("Profile deleted", "success");
            resetAlertProfileForm();
            await loadAlertProfiles();
        } catch (error) {
            console.error("Failed to delete alert profile:", error);
            window.SharedUtils.showToast("Failed to delete alert profile", "error");
        }
    }
    
    /**
     * Toggle alert profile enabled/disabled state
     * @param {string} profileId - Profile ID to toggle
     * @param {boolean} enabled - New enabled state
     */
    async function toggleAlertProfile(profileId, enabled) {
        try {
            const response = await fetch(alertProfileEndpoints.toggle, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ id: profileId, enabled })
            });
            
            if (!response.ok) throw new Error("Failed to toggle alert profile");
            
            window.SharedUtils.showToast(`Profile ${enabled ? 'enabled' : 'disabled'}`, "info");
            await loadAlertProfiles();
        } catch (error) {
            console.error("Failed to toggle alert profile:", error);
            throw error;
        }
    }
    
    /**
     * Run backtest on alert profile
     * @param {string|number} [profileId] - Optional profile ID. If not provided, uses current form profile
     */
    async function backtestAlertProfile(profileId) {
        let targetProfileId = profileId;
        
        // If profileId not provided, get from form
        if (!targetProfileId) {
            targetProfileId = document.getElementById("alert-profile-id").value;
            if (!targetProfileId) {
                window.SharedUtils.showToast("Please select a profile first", "warning");
                return;
            }
        }
        
        const modalEl = document.getElementById('backtestModal');
        const modal = new bootstrap.Modal(modalEl);
        
        // Apply glass backdrop
        window.SharedUtils.applyGlassBackdrop(modalEl);
        modal.show();
        
        document.getElementById('backtest-loading').classList.remove('d-none');
        document.getElementById('backtest-results').classList.add('d-none');
        document.getElementById('backtest-error').classList.add('d-none');
        
        try {
            const response = await fetch(alertProfileEndpoints.backtest, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    id: targetProfileId,
                    hours_back: 24,
                    max_messages: 100
                })
            });
            
            if (!response.ok) throw new Error("Backtest failed");
            const data = await response.json();
            
            // Display results
            document.getElementById('backtest-loading').classList.add('d-none');
            document.getElementById('backtest-results').classList.remove('d-none');
            
            // Statistics
            document.getElementById('stat-total-messages').textContent = data.stats.total_messages || 0;
            document.getElementById('stat-matched').textContent = data.stats.matched_messages || 0;
            document.getElementById('stat-match-rate').textContent = (data.stats.match_rate || 0).toFixed(1) + '%';
            document.getElementById('stat-avg-score').textContent = (data.stats.avg_score || data.stats.average_score || 0).toFixed(2);
            
            // Update recommendations tooltip
            const recIcon = document.getElementById('backtest-recommendations-icon');
            if (recIcon) {
                let tooltipContent;
                if (data.recommendations && data.recommendations.length > 0) {
                    tooltipContent = '<ul class="mb-0 ps-3">' + 
                        data.recommendations.map(r => `<li>${window.SharedUtils.escapeHtml(r)}</li>`).join('') + 
                        '</ul>';
                } else {
                    tooltipContent = '<strong>✓ Profile is working well!</strong>';
                }
                // Dispose of existing tooltip and create new one with updated content
                const existingTooltip = bootstrap.Tooltip.getInstance(recIcon);
                if (existingTooltip) {
                    existingTooltip.dispose();
                }
                recIcon.setAttribute('data-bs-html', 'true');
                recIcon.setAttribute('data-bs-title', tooltipContent);
                new bootstrap.Tooltip(recIcon);
            }
            
            // For alert profiles, triggers are usually populated
            const hasTriggersData = data.matches && data.matches.some(m => m.triggers && m.triggers.length > 0);
            const triggersHeader = document.getElementById('triggers-header');
            
            // Hide Triggers column if no data available
            if (triggersHeader) {
                if (hasTriggersData) {
                    triggersHeader.style.display = '';
                } else {
                    triggersHeader.style.display = 'none';
                }
            }
            
            // Matches table
            const tbody = document.getElementById('backtest-matches-tbody');
            if (!tbody) {
                console.error('backtest-matches-tbody element not found in DOM');
                document.getElementById('backtest-loading').classList.add('d-none');
                document.getElementById('backtest-error').textContent = 'UI error: Table element not found';
                document.getElementById('backtest-error').classList.remove('d-none');
                return;
            }
            if (data.matches && data.matches.length > 0) {
                tbody.innerHTML = data.matches.map(match => {
                    const triggersCell = hasTriggersData 
                        ? `<td><small>${window.SharedUtils.escapeHtml((match.triggers || []).join(', '))}</small></td>`
                        : '';
                    
                    return `
                        <tr>
                            <td>${window.SharedUtils.escapeHtml(match.chat_title || 'Unknown')}</td>
                            <td>${match.message_id}</td>
                            <td><span class="badge bg-primary">${match.score.toFixed(2)}</span></td>
                            ${triggersCell}
                            <td>${match.would_alert ? '<span class="badge bg-success">Yes</span>' : '<span class="badge bg-secondary">No</span>'}</td>
                            <td><small>${window.SharedUtils.escapeHtml((match.text_preview || '').substring(0, 100))}</small></td>
                        </tr>
                    `;
                }).join('');
            } else {
                const colspan = hasTriggersData ? 6 : 5;
                tbody.innerHTML = `<tr><td colspan="${colspan}" class="text-center text-muted">No matches found</td></tr>`;
            }
        } catch (error) {
            console.error("Backtest failed:", error);
            document.getElementById('backtest-loading').classList.add('d-none');
            document.getElementById('backtest-error').textContent = `Backtest failed: ${error.message}`;
            document.getElementById('backtest-error').classList.remove('d-none');
        }
    }
    
    /**
     * Reset the alert profile form to create new profile
     */
    function resetAlertProfileForm() {
        document.getElementById("alert-profile-form").reset();
        document.getElementById("alert-profile-id").value = "";
        document.getElementById("alert-profile-id-display").value = "";
        document.getElementById("alert-save-btn-text").textContent = "Save Profile";
        document.getElementById("btn-delete-alert-profile").classList.add("d-none");
        document.getElementById("alert-min-score").value = "1.0";
        document.getElementById("alert-enabled").value = "true";
        
        // Explicitly uncheck all detection toggles (new profiles start with all disabled)
        document.getElementById("alert-detect-questions").checked = false;
        document.getElementById("alert-detect-mentions").checked = false;
        document.getElementById("alert-detect-links").checked = false;
        document.getElementById("alert-require-forwarded").checked = false;
        document.getElementById("alert-detect-codes").checked = false;
        document.getElementById("alert-detect-documents").checked = false;
        document.getElementById("alert-detect-polls").checked = false;
        document.getElementById("alert-prioritize-pinned").checked = false;
        document.getElementById("alert-prioritize-admin").checked = false;
        
        // Clear digest configuration
        if (window.DigestEditor) {
            window.DigestEditor.populateDigestConfigInForm(null, 'alert-');
        }
        
        document.querySelectorAll(".alert-profile-item").forEach(item => {
            item.classList.remove("active");
        });
        
        currentAlertProfile = null;
        
        // Update max score display after reset
        updateMaxScoreDisplay();
    }
    
    /**
     * Create a new alert profile
     */
    function newAlertProfile() {
        resetAlertProfileForm();
        updateMaxScoreDisplay();  // Calculate initial max score
        window.SharedUtils.showToast("Create a new alert profile", "info");
    }
    
    /**
     * Calculate and display maximum possible score based on enabled toggles and filled keywords
     */
    function updateMaxScoreDisplay() {
        let maxScore = 0;
        
        // Keyword categories with their scores (only count if fields have content)
        const keywordCategories = [
            { id: 'alert-action-keywords', score: 1.0, name: 'Action' },
            { id: 'alert-decision-keywords', score: 1.1, name: 'Decision' },
            { id: 'alert-urgency-keywords', score: 1.5, name: 'Urgency' },
            { id: 'alert-importance-keywords', score: 0.9, name: 'Importance' },
            { id: 'alert-release-keywords', score: 0.8, name: 'Release' },
            { id: 'alert-security-keywords', score: 1.2, name: 'Security' },
            { id: 'alert-risk-keywords', score: 1.0, name: 'Risk' },
            { id: 'alert-opportunity-keywords', score: 0.6, name: 'Opportunity' },
            { id: 'alert-keywords', score: 0.8, name: 'Keywords' }
        ];
        
        // Count keyword categories that have content
        keywordCategories.forEach(category => {
            const element = document.getElementById(category.id);
            if (element && element.value && element.value.trim().length > 0) {
                maxScore += category.score;
            }
        });
        
        // Detection toggles (only count if enabled)
        const detectionToggles = [
            { id: 'alert-detect-questions', score: 1.2, name: 'Questions' },  // Approximate, varies by context
            { id: 'alert-detect-mentions', score: 2.0, name: 'Mentions' },    // Highest priority
            { id: 'alert-detect-links', score: 0.5, name: 'Links' },          // Approximate
            { id: 'alert-require-forwarded', score: 0.0, name: 'Forwards' },  // Filter, not score boost
            { id: 'alert-detect-codes', score: 1.3, name: 'Code' },
            { id: 'alert-detect-documents', score: 0.7, name: 'Documents' },
            { id: 'alert-detect-polls', score: 1.0, name: 'Polls' },
            { id: 'alert-prioritize-pinned', score: 1.2, name: 'Pinned' },
            { id: 'alert-prioritize-admin', score: 0.9, name: 'Admin' }
        ];
        
        detectionToggles.forEach(toggle => {
            const element = document.getElementById(toggle.id);
            if (element && element.checked) {
                maxScore += toggle.score;
            }
        });
        
        // VIP Senders (adds 1.0 per VIP if configured)
        const vipSenders = document.getElementById('alert-vip-senders');
        if (vipSenders && vipSenders.value && vipSenders.value.trim().length > 0) {
            const vipCount = vipSenders.value.split(',').filter(v => v.trim().length > 0).length;
            maxScore += 1.0;
        }
        
        // Reactions and replies (potential additional boosts, not directly configurable here)
        // These are runtime conditions, so we show a note rather than adding to max
        
        // Update display
        const displayElement = document.getElementById('alert-max-score-display');
        if (displayElement) {
            displayElement.textContent = maxScore.toFixed(1);
            
            // Color code based on score range
            displayElement.className = 'badge fs-5 ';
            if (maxScore === 0) {
                displayElement.className += 'bg-secondary';
            } else if (maxScore < 5) {
                displayElement.className += 'bg-warning';
            } else if (maxScore < 10) {
                displayElement.className += 'bg-info';
            } else {
                displayElement.className += 'bg-success';
            }
        }
        
        // Update the threshold max value in the Minimum Score help text
        const thresholdMaxElement = document.getElementById('alert-threshold-max');
        if (thresholdMaxElement) {
            thresholdMaxElement.textContent = maxScore.toFixed(1);
        }
    }
    
    // Attach event listeners to update max score when form changes
    function attachMaxScoreListeners() {
        // Listen to all keyword textarea changes
        const keywordFields = [
            'alert-action-keywords', 'alert-decision-keywords', 'alert-urgency-keywords',
            'alert-importance-keywords', 'alert-release-keywords', 'alert-security-keywords',
            'alert-risk-keywords', 'alert-opportunity-keywords', 'alert-keywords'
        ];
        
        keywordFields.forEach(fieldId => {
            const element = document.getElementById(fieldId);
            if (element) {
                element.addEventListener('input', updateMaxScoreDisplay);
            }
        });
        
        // Listen to all detection toggle changes
        const toggleIds = [
            'alert-detect-questions', 'alert-detect-mentions', 'alert-detect-links',
            'alert-require-forwarded', 'alert-detect-codes', 'alert-detect-documents',
            'alert-detect-polls', 'alert-prioritize-pinned', 'alert-prioritize-admin'
        ];
        
        toggleIds.forEach(toggleId => {
            const element = document.getElementById(toggleId);
            if (element) {
                element.addEventListener('change', updateMaxScoreDisplay);
            }
        });
        
        // Listen to VIP senders changes
        const vipSenders = document.getElementById('alert-vip-senders');
        if (vipSenders) {
            vipSenders.addEventListener('input', updateMaxScoreDisplay);
        }
    }
    
    // Export public API
    window.AlertProfiles = {
        init,
        loadAlertProfiles,
        loadAlertProfile,
        saveAlertProfile,
        deleteAlertProfile,
        toggleAlertProfile,
        backtestAlertProfile,
        resetAlertProfileForm,
        newAlertProfile,
        exportAlertProfile,
        importAlertProfile,
        duplicateAlertProfile,
        filterAlertProfiles,
        updateMaxScoreDisplay,  // Export for external use
        attachMaxScoreListeners  // Export for initialization
    };
})();
