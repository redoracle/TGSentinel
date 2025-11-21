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
 * - exportAlertProfile(profileId)
 * - duplicateAlertProfile(profileId)
 * - filterAlertProfiles(searchTerm)
 */

(function() {
    'use strict';
    
    // Module state
    let currentAlertProfile = null;
    let allAlertProfiles = [];
    let filteredAlertProfiles = [];
    let alertProfileEndpoints = {};
    
    /**
     * Initialize the Alert Profiles module
     * @param {Object} endpoints - API endpoint configuration
     */
    function init(endpoints) {
        alertProfileEndpoints = endpoints;
    }
    
    /**
     * Load all alert profiles from the server
     */
    async function loadAlertProfiles() {
        try {
            const response = await fetch(alertProfileEndpoints.list);
            if (!response.ok) throw new Error("Failed to load alert profiles");
            const data = await response.json();
            
            allAlertProfiles = data.profiles || [];
            filteredAlertProfiles = [...allAlertProfiles];
            
            // Update profile count
            const countEl = document.getElementById("alert-profiles-count");
            if (countEl) {
                countEl.textContent = allAlertProfiles.length;
            }
            
            renderAlertProfiles(filteredAlertProfiles);
        } catch (error) {
            console.error("Failed to load alert profiles:", error);
            window.SharedUtils.showToast("Failed to load alert profiles", "error");
        }
    }
    
    /**
     * Render alert profiles list
     * @param {Array} profiles - Array of profile objects to render
     */
    function renderAlertProfiles(profiles) {
        const listEl = document.getElementById("alert-profiles-list");
        if (!listEl) return;
        
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
            const keywordCount = (profile.critical_keywords || []).length + 
                               (profile.security_keywords || []).length +
                               (profile.urgency_keywords || []).length +
                               (profile.financial_keywords || []).length +
                               (profile.technical_keywords || []).length +
                               (profile.project_keywords || []).length +
                               (profile.community_keywords || []).length +
                               (profile.general_keywords || []).length;
            
            const hasActivity = profile.last_triggered_at;
            const activityIndicator = hasActivity ? '<span class="alert-profile-activity" title="Recently triggered"></span>' : '';
            
            const lastUpdated = profile.updated_at ? 
                window.SharedUtils.formatDate(profile.updated_at) : 
                'Never';
            
            return `
                <a href="#" class="list-group-item list-group-item-action alert-profile-item" 
                   data-profile-id="${profile.id}"
                   data-profile-name="${window.SharedUtils.escapeHtml(profile.name)}"
                   data-enabled="${profile.enabled}">
                    ${activityIndicator}
                    <div class="d-flex justify-content-between align-items-start">
                        <div class="flex-grow-1 pe-2">
                            <div class="d-flex align-items-center gap-2 mb-1">
                                <h6 class="mb-0">${window.SharedUtils.escapeHtml(profile.name)}</h6>
                                <span class="badge bg-secondary" style="font-size: 0.7em;">ID: ${profile.id}</span>
                            </div>
                            ${profile.description ? `<small class="text-muted d-block mb-2">${window.SharedUtils.escapeHtml(profile.description)}</small>` : ''}
                            <div class="alert-profile-metadata">
                                ${keywordCount > 0 ? `
                                    <span class="alert-profile-meta-badge">
                                        <svg fill="currentColor" viewBox="0 0 16 16">
                                            <path d="M10.97 4.97a.75.75 0 0 1 1.07 1.05l-3.99 4.99a.75.75 0 0 1-1.08.02L4.324 8.384a.75.75 0 1 1 1.06-1.06l2.094 2.093 3.473-4.425a.267.267 0 0 1 .02-.022z"/>
                                        </svg>
                                        ${keywordCount} rules
                                    </span>
                                ` : ''}
                                <span class="alert-profile-meta-badge">
                                    <svg fill="currentColor" viewBox="0 0 16 16">
                                        <path d="M8 3.5a.5.5 0 0 0-1 0V9a.5.5 0 0 0 .252.434l3.5 2a.5.5 0 0 0 .496-.868L8 8.71V3.5z"/>
                                        <path d="M8 16A8 8 0 1 0 8 0a8 8 0 0 0 0 16zm7-8A7 7 0 1 1 1 8a7 7 0 0 1 14 0z"/>
                                    </svg>
                                    ${lastUpdated}
                                </span>
                            </div>
                        </div>
                        <div class="d-flex flex-column align-items-end gap-2">
                            <div class="alert-profile-toggle-wrapper">
                                <div class="form-check form-switch mb-0">
                                    <input class="form-check-input alert-profile-toggle" type="checkbox" 
                                           data-profile-id="${profile.id}" 
                                           ${profile.enabled ? 'checked' : ''}
                                           onclick="event.stopPropagation()"
                                           title="${profile.enabled ? 'Disable' : 'Enable'} profile">
                                </div>
                            </div>
                            <div class="alert-profile-actions">
                                <button class="alert-profile-action-btn" 
                                        data-action="export" 
                                        data-profile-id="${profile.id}"
                                        onclick="event.stopPropagation(); window.AlertProfiles.exportAlertProfile('${profile.id}')"
                                        title="Export profile">
                                    <svg width="12" height="12" fill="currentColor" viewBox="0 0 16 16">
                                        <path d="M.5 9.9a.5.5 0 0 1 .5.5v2.5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-2.5a.5.5 0 0 1 1 0v2.5a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2v-2.5a.5.5 0 0 1 .5-.5z"/>
                                        <path d="M7.646 11.854a.5.5 0 0 0 .708 0l3-3a.5.5 0 0 0-.708-.708L8.5 10.293V1.5a.5.5 0 0 0-1 0v8.793L5.354 8.146a.5.5 0 1 0-.708.708l3 3z"/>
                                    </svg>
                                </button>
                                <button class="alert-profile-action-btn" 
                                        data-action="duplicate" 
                                        data-profile-id="${profile.id}"
                                        onclick="event.stopPropagation(); window.AlertProfiles.duplicateAlertProfile('${profile.id}')"
                                        title="Duplicate profile">
                                    <svg width="12" height="12" fill="currentColor" viewBox="0 0 16 16">
                                        <path d="M4 2a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V2zm2-1a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V2a1 1 0 0 0-1-1H6zM2 5a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-1h1v1a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h1v1H2z"/>
                                    </svg>
                                </button>
                            </div>
                        </div>
                    </div>
                </a>
            `;
        }).join('');
        
        // Attach click handlers
        listEl.querySelectorAll('.alert-profile-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                loadAlertProfile(item.dataset.profileId);
            });
        });
        
        listEl.querySelectorAll('.alert-profile-toggle').forEach(toggle => {
            toggle.addEventListener('change', async (e) => {
                e.stopPropagation();
                const previousState = !toggle.checked;
                toggle.disabled = true;
                
                try {
                    await toggleAlertProfile(toggle.dataset.profileId, toggle.checked);
                } catch (error) {
                    toggle.checked = previousState;
                    console.error('Failed to toggle alert profile:', error);
                    window.SharedUtils.showToast('Failed to toggle profile. Please try again.', 'error');
                } finally {
                    toggle.disabled = false;
                }
            });
        });
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
                    ...(profile.critical_keywords || []),
                    ...(profile.security_keywords || []),
                    ...(profile.urgency_keywords || []),
                    ...(profile.financial_keywords || []),
                    ...(profile.technical_keywords || []),
                    ...(profile.project_keywords || []),
                    ...(profile.community_keywords || []),
                    ...(profile.general_keywords || [])
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
        const profile = allAlertProfiles.find(p => p.id === profileId);
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
     * Duplicate an existing alert profile
     * @param {string} profileId - Profile ID to duplicate
     */
    async function duplicateAlertProfile(profileId) {
        const profile = allAlertProfiles.find(p => p.id === profileId);
        if (!profile) return;
        
        const duplicatedProfile = {
            ...profile,
            id: `${profile.id}-copy-${Date.now()}`,
            name: `${profile.name} (Copy)`,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString()
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
            document.getElementById("alert-critical-keywords").value = (data.profile.critical_keywords || []).join(", ");
            document.getElementById("alert-security-keywords").value = (data.profile.security_keywords || []).join(", ");
            document.getElementById("alert-urgency-keywords").value = (data.profile.urgency_keywords || []).join(", ");
            document.getElementById("alert-financial-keywords").value = (data.profile.financial_keywords || []).join(", ");
            document.getElementById("alert-technical-keywords").value = (data.profile.technical_keywords || []).join(", ");
            document.getElementById("alert-project-keywords").value = (data.profile.project_keywords || []).join(", ");
            document.getElementById("alert-community-keywords").value = (data.profile.community_keywords || []).join(", ");
            document.getElementById("alert-general-keywords").value = (data.profile.general_keywords || []).join(", ");
            
            // Detection settings
            document.getElementById("alert-min-score").value = data.profile.min_score || 1.0;
            document.getElementById("alert-vip-senders").value = (data.profile.vip_senders || []).join(", ");
            document.getElementById("alert-excluded-users").value = (data.profile.excluded_users || []).join(", ");
            document.getElementById("alert-detect-questions").checked = data.profile.detect_questions || false;
            document.getElementById("alert-detect-mentions").checked = data.profile.detect_mentions || false;
            document.getElementById("alert-detect-links").checked = data.profile.detect_links || false;
            document.getElementById("alert-require-forwarded").checked = data.profile.require_forwarded || false;
            
            // Set channel and user selections
            if (window.EntitySelector) {
                window.EntitySelector.setSelectedEntityIds('alert', data.profile.channels || [], data.profile.users || []);
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
            critical_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-critical-keywords").value),
            security_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-security-keywords").value),
            urgency_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-urgency-keywords").value),
            financial_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-financial-keywords").value),
            technical_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-technical-keywords").value),
            project_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-project-keywords").value),
            community_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-community-keywords").value),
            general_keywords: window.SharedUtils.parseCSV(document.getElementById("alert-general-keywords").value),
            min_score: parseFloat(document.getElementById("alert-min-score").value) || 1.0,
            vip_senders: window.SharedUtils.parseCSV(document.getElementById("alert-vip-senders").value),
            excluded_users: window.SharedUtils.parseCSV(document.getElementById("alert-excluded-users").value),
            detect_questions: document.getElementById("alert-detect-questions").checked,
            detect_mentions: document.getElementById("alert-detect-mentions").checked,
            detect_links: document.getElementById("alert-detect-links").checked,
            require_forwarded: document.getElementById("alert-require-forwarded").checked
        };
        
        // Get selected entities
        if (window.EntitySelector) {
            profileData.channels = window.EntitySelector.getSelectedEntityIds('alert', 'channels');
            profileData.users = window.EntitySelector.getSelectedEntityIds('alert', 'users');
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
     * Run backtest on current alert profile
     */
    async function backtestAlertProfile() {
        const profileId = document.getElementById("alert-profile-id").value;
        if (!profileId) {
            window.SharedUtils.showToast("Please select a profile first", "warning");
            return;
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
                    id: profileId,
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
            
            // Recommendations
            const recsList = document.getElementById('recommendations-list');
            if (data.recommendations && data.recommendations.length > 0) {
                recsList.innerHTML = data.recommendations.map(r => `<li>${window.SharedUtils.escapeHtml(r)}</li>`).join('');
            } else {
                recsList.innerHTML = '<li>Profile is working well!</li>';
            }
            
            // Matches table
            const tbody = document.getElementById('backtest-matches-tbody');
            if (data.matches && data.matches.length > 0) {
                tbody.innerHTML = data.matches.map(match => `
                    <tr>
                        <td>${window.SharedUtils.escapeHtml(match.chat_title || 'Unknown')}</td>
                        <td>${match.message_id}</td>
                        <td><span class="badge bg-primary">${match.score.toFixed(2)}</span></td>
                        <td><small>${window.SharedUtils.escapeHtml((match.triggers || []).join(', '))}</small></td>
                        <td>${match.would_alert ? '<span class="badge bg-success">Yes</span>' : '<span class="badge bg-secondary">No</span>'}</td>
                        <td><small>${window.SharedUtils.escapeHtml((match.text_preview || '').substring(0, 100))}</small></td>
                    </tr>
                `).join('');
            } else {
                tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">No matches found</td></tr>';
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
        
        // Clear digest configuration
        if (window.DigestEditor) {
            window.DigestEditor.populateDigestConfigInForm(null, 'alert-');
        }
        
        document.querySelectorAll(".alert-profile-item").forEach(item => {
            item.classList.remove("active");
        });
        
        currentAlertProfile = null;
    }
    
    /**
     * Create a new alert profile
     */
    function newAlertProfile() {
        resetAlertProfileForm();
        window.SharedUtils.showToast("Create a new alert profile", "info");
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
        duplicateAlertProfile,
        filterAlertProfiles
    };
})();
