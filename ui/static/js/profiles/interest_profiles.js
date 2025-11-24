/**
 * Interest Profiles Module
 * 
 * Manages Interest (semantic-based) Profiles CRUD operations and UI rendering.
 * 
 * Public API:
 *   - init(endpoints): Initialize with API endpoints
 *   - loadInterestProfiles(): Fetch and render all profiles
 *   - selectInterestProfile(id): Load profile into form
 *   - saveInterestProfile(event): Create/update profile
 *   - deleteInterestProfile(): Delete current profile
 *   - toggleInterestProfile(id, event): Enable/disable profile
 *   - backtestInterestProfile(): Run backtest for current profile
 *   - resetInterestProfileForm(): Clear form for new profile
 *   - newInterestProfile(): Reset form and show toast
 *   - filterInterestProfiles(searchTerm): Filter profiles by name/description
 *   - exportInterestProfile(id): Export single profile as JSON
 *   - importInterestProfile(): Import profile from JSON file
 *   - duplicateInterestProfile(id): Clone existing profile
 *   - exportAllInterestProfiles(): Export all profiles as JSON
 *   - bulkToggleInterestProfiles(enabled): Enable/disable all profiles
 *   - runSimilarityTest(): Test phrase against selected profile
 * 
 * Dependencies:
 *   - window.SharedUtils (escapeHtml, formatDate, showToast, parseCSV)
 *   - window.EntitySelector (getSelectedEntityIds, setSelectedEntityIds)
 *   - window.DigestEditor (extractDigestConfigFromForm, populateDigestConfigInForm)
 *   - Bootstrap 5 (modal, form controls)
 */

(function() {
    'use strict';
    
    // ============= MODULE STATE =============
    
    let currentInterestProfile = null;
    let allInterestProfiles = [];
    let filteredInterestProfiles = [];
    let interestProfileEndpoints = {};
    let interestProfilesLoading = false;
    const defaultInterestTargetChannel = document.getElementById("interest-digest-target-channel")?.dataset.defaultChannel || "";
    
    // ============= PUBLIC API =============
    
    /**
     * Initialize the Interest Profiles module with API endpoints
     * @param {Object} endpoints - Object containing API endpoint URLs
     */
    function init(endpoints) {
        interestProfileEndpoints = endpoints;
    }
    
    /**
     * Load all interest profiles from the server
     * @param {number} retryCount - Internal retry counter (default: 0)
     * @returns {Promise<void>}
     */
    async function loadInterestProfiles(retryCount = 0) {
        interestProfilesLoading = true;
        renderInterestProfiles([]);
        try {
            const response = await fetch(interestProfileEndpoints.list);
            if (!response.ok) throw new Error("Failed to load interest profiles");
            const data = await response.json();
            
            allInterestProfiles = data.profiles || [];
            filteredInterestProfiles = [...allInterestProfiles];
            interestProfilesLoading = false;
            
            // Update profile count
            const countEl = document.getElementById("interest-profiles-count");
            if (countEl) {
                countEl.textContent = allInterestProfiles.length;
            }
            
            // Update profile selector for similarity tester
            updateProfileSelector();
            
            renderInterestProfiles(filteredInterestProfiles);
        } catch (error) {
            console.error("Failed to load interest profiles:", error);
            
            // Retry up to 3 times with increasing delays (for Sentinel startup race condition)
            if (retryCount < 3) {
                const delay = (retryCount + 1) * 2000; // 2s, 4s, 6s
                console.log(`Retrying in ${delay}ms... (attempt ${retryCount + 1}/3)`);
                setTimeout(() => loadInterestProfiles(retryCount + 1), delay);
            } else {
                window.SharedUtils.showToast("Failed to load interest profiles", "error");
            }
            interestProfilesLoading = false;
            renderInterestProfiles(filteredInterestProfiles);
        }
    }
    
    /**
     * Update the profile selector dropdown in the similarity tester
     */
    function updateProfileSelector() {
        const selectEl = document.getElementById("profile-select");
        if (!selectEl) return;
        
        // Clear existing options except the first (placeholder)
        selectEl.innerHTML = '<option value="">Choose a profile...</option>';
        
        // Add option for each enabled profile
        allInterestProfiles.forEach(profile => {
            const isEnabled = profile.enabled !== false;
            const option = document.createElement("option");
            option.value = profile.id || profile.name;
            option.textContent = profile.name + (isEnabled ? "" : " (disabled)");
            if (!isEnabled) {
                option.disabled = true;
                option.classList.add("text-muted");
            }
            selectEl.appendChild(option);
        });
    }
    
    /**
     * Render the list of interest profiles
     * @param {Array} profiles - Array of profile objects to render
     */
    function renderInterestProfiles(profiles) {
        const listEl = document.getElementById("interest-profiles-list");
        if (!listEl) return;
        
        if (interestProfilesLoading) {
            listEl.innerHTML = `
                <div class="text-center text-muted p-4">
                    <svg width="48" height="48" fill="currentColor" class="bi bi-collection mb-2" viewBox="0 0 16 16">
                        <path d="M2.5 3.5a.5.5 0 0 1 0-1h11a.5.5 0 0 1 0 1h-11zm2-2a.5.5 0 0 1 0-1h7a.5.5 0 0 1 0 1h-7zM0 13a1.5 1.5 0 0 0 1.5 1.5h13A1.5 1.5 0 0 0 16 13V6a1.5 1.5 0 0 0-1.5-1.5h-13A1.5 1.5 0 0 0 0 6v7zm1.5.5A.5.5 0 0 1 1 13V6a.5.5 0 0 1 .5-.5h13a.5.5 0 0 1 .5.5v7a.5.5 0 0 1-.5.5h-13z"/>
                    </svg>
                    <p class="mb-2">Loading interest profiles...</p>
                    <small>Please wait while we fetch your profiles</small>
                    <div class="spinner-border text-primary mt-3" role="status" aria-label="Loading interest profiles"></div>
                </div>
            `;
            return;
        }

        if (!profiles || profiles.length === 0) {
            listEl.innerHTML = `
                <div class="text-center text-muted p-4">
                    <svg width="48" height="48" fill="currentColor" class="bi bi-collection mb-2" viewBox="0 0 16 16">
                        <path d="M2.5 3.5a.5.5 0 0 1 0-1h11a.5.5 0 0 1 0 1h-11zm2-2a.5.5 0 0 1 0-1h7a.5.5 0 0 1 0 1h-7zM0 13a1.5 1.5 0 0 0 1.5 1.5h13A1.5 1.5 0 0 0 16 13V6a.5.5 0 0 0-1.5-1.5h-13A1.5 1.5 0 0 0 0 6v7zm1.5.5A.5.5 0 0 1 1 13V6a.5.5 0 0 1 .5-.5h13a.5.5 0 0 1 .5.5v7a.5.5 0 0 1-.5.5h-13z"/>
                    </svg>
                    <p class="mb-2">No interest profiles yet</p>
                    <small>Create your first profile to get started</small>
                </div>
            `;
            return;
        }
        
        listEl.innerHTML = profiles.map(profile => {
            const isEnabled = profile.enabled !== false;
            const scheduleCount = (profile.digest_schedules || []).length;
            const keywordCount = (profile.keywords || []).length;
            const entityCount = (profile.channels || []).length + (profile.users || []).length;
            const sampleCount = (profile.positive_samples || []).length + (profile.negative_samples || []).length;
            
            return `
                <div class="list-group-item list-group-item-action alert-profile-item ${currentInterestProfile && currentInterestProfile.id === profile.id ? 'active' : ''}" 
                        data-profile-id="${profile.id}"
                        role="button"
                        tabindex="0"
                        onclick="window.InterestProfiles.selectInterestProfile(${profile.id})"
                        onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.InterestProfiles.selectInterestProfile(${profile.id});}">
                    <div class="d-flex w-100 justify-content-between align-items-start">
                        <div class="alert-profile-item-content flex-grow-1 min-w-0 me-2">
                            <div class="d-flex align-items-center gap-2 mb-1">
                                <h6 class="mb-0 text-truncate">${window.SharedUtils.escapeHtml(profile.name)}</h6>
                                <span class="badge ${isEnabled ? 'bg-success' : 'bg-secondary'} badge-sm">${isEnabled ? 'ON' : 'OFF'}</span>
                            </div>
                            ${profile.description ? `<p class="mb-1 small text-muted alert-profile-description">${window.SharedUtils.escapeHtml(profile.description)}</p>` : ''}
                            <div class="d-flex gap-2 flex-wrap">
                                <small class="text-muted">ID: ${profile.id}</small>
                                ${sampleCount > 0 ? `<small class="text-muted">📝 ${sampleCount} samples</small>` : ''}
                                ${keywordCount > 0 ? `<small class="text-muted">🔑 ${keywordCount}</small>` : ''}
                                ${entityCount > 0 ? `<small class="text-muted">👥 ${entityCount}</small>` : ''}
                                ${scheduleCount > 0 ? `<small class="text-muted">📅 ${scheduleCount}</small>` : ''}
                                ${profile.threshold ? `<small class="text-muted">θ=${profile.threshold.toFixed(2)}</small>` : ''}
                                ${(profile.tags || []).length > 0 ? `<small class="text-muted">🏷️ ${(profile.tags || []).map(t => window.SharedUtils.escapeHtml(t)).join(', ')}</small>` : ''}
                            </div>
                        </div>
                        <div class="alert-profile-item-actions d-flex flex-column align-items-end gap-2">
                            <div class="form-check form-switch mb-0" onclick="event.stopPropagation()">
                                <input class="form-check-input" type="checkbox" 
                                       ${isEnabled ? 'checked' : ''}
                                       onchange="window.InterestProfiles.toggleInterestProfile(${profile.id}, event)"
                                       title="Enable/Disable">
                            </div>
                            <div class="btn-group btn-group-sm alert-profile-actions" role="group" onclick="event.stopPropagation()">
                                <button type="button" class="btn btn-outline-secondary" 
                                        onclick="window.InterestProfiles.backtestInterestProfile(${profile.id})"
                                        title="Run Backtest">
                                    <svg width="14" height="14" fill="currentColor" viewBox="0 0 16 16">
                                        <path d="M8 2a.5.5 0 0 1 .5.5V4a.5.5 0 0 1-1 0V2.5A.5.5 0 0 1 8 2zM3.732 3.732a.5.5 0 0 1 .707 0l.915.914a.5.5 0 1 1-.708.708l-.914-.915a.5.5 0 0 1 0-.707zM2 8a.5.5 0 0 1 .5-.5h1.586a.5.5 0 0 1 0 1H2.5A.5.5 0 0 1 2 8zm9.5 0a.5.5 0 0 1 .5-.5h1.5a.5.5 0 0 1 0 1H12a.5.5 0 0 1-.5-.5zm.754-4.246a.389.389 0 0 0-.527-.02L7.547 7.31A.91.91 0 1 0 8.85 8.569l3.434-4.297a.389.389 0 0 0-.029-.518z"/>
                                        <path fill-rule="evenodd" d="M6.664 15.889A8 8 0 1 1 9.336.11a8 8 0 0 1-2.672 15.78zm-4.665-4.283A11.945 11.945 0 0 1 8 10c2.186 0 4.236.585 6.001 1.606a7 7 0 1 0-12.002 0z"/>
                                    </svg>
                                </button>
                                <button type="button" class="btn btn-outline-secondary" 
                                        onclick="window.InterestProfiles.importInterestProfile()"
                                        title="Import from JSON">
                                    <svg width="14" height="14" fill="currentColor" viewBox="0 0 16 16">
                                        <path d="M8.5 11.5a.5.5 0 0 1-1 0V7.707L6.354 8.854a.5.5 0 1 1-.708-.708l2-2a.5.5 0 0 1 .708 0l2 2a.5.5 0 0 1-.708.708L8.5 7.707V11.5z"/>
                                        <path d="M14 14V4.5L9.5 0H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2zM9.5 3A1.5 1.5 0 0 0 11 4.5h2V14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1h5.5v2z"/>
                                    </svg>
                                </button>
                                <button type="button" class="btn btn-outline-secondary" 
                                        onclick="window.InterestProfiles.exportInterestProfile(${profile.id})"
                                        title="Export as JSON">
                                    <svg width="14" height="14" fill="currentColor" viewBox="0 0 16 16">
                                        <path d="M8.5 6.5a.5.5 0 0 0-1 0v3.793L6.354 9.146a.5.5 0 1 0-.708.708l2 2a.5.5 0 0 0 .708 0l2-2a.5.5 0 0 0-.708-.708L8.5 10.293V6.5z"/>
                                        <path d="M14 14V4.5L9.5 0H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2zM9.5 3A1.5 1.5 0 0 0 11 4.5h2V14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1h5.5v2z"/>
                                    </svg>
                                </button>
                                <button type="button" class="btn btn-outline-secondary" 
                                        onclick="window.InterestProfiles.duplicateInterestProfile(${profile.id})"
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
     * Select and load a specific interest profile into the form
     * @param {number} profileId - ID of profile to load
     * @returns {Promise<void>}
     */
    async function selectInterestProfile(profileId) {
        try {
            const response = await fetch(`${interestProfileEndpoints.get}/${profileId}`);
            if (!response.ok) throw new Error("Failed to load profile");
            
            const data = await response.json();
            const profile = data.profile;
            
            currentInterestProfile = profile;
            
            // Update form fields
            document.getElementById("interest-profile-id").value = profile.id || "";
            document.getElementById("interest-profile-id-display").value = profile.id || "Auto-generated";
            document.getElementById("topic-name").value = profile.name || "";
            document.getElementById("topic-description").value = profile.description || "";
            
            // Samples
            document.getElementById("positive-samples").value = (profile.positive_samples || []).join("\n");
            document.getElementById("negative-samples").value = (profile.negative_samples || []).join("\n");
            
            // Advanced fields
            document.getElementById("similarity-threshold").value = profile.threshold || 0.42;
            document.getElementById("profile-enabled").value = profile.enabled !== false ? "true" : "false";
            
            // VIP and excluded users
            document.getElementById("interest-vip-senders").value = (profile.vip_senders || []).join(", ");
            document.getElementById("interest-excluded-users").value = (profile.excluded_users || []).join(", ");
            
            // Set channel and user selections using the helper function
            if (window.EntitySelector) {
                window.EntitySelector.setSelectedEntityIds('interest', profile.channels || [], profile.users || []);
            }
            
            // Set selected webhooks
            const webhookSelect = document.getElementById('interest-webhooks');
            if (webhookSelect && profile.webhooks) {
                // Deselect all first
                Array.from(webhookSelect.options).forEach(opt => opt.selected = false);
                // Select webhooks from profile
                profile.webhooks.forEach(webhookService => {
                    const option = Array.from(webhookSelect.options).find(opt => opt.value === webhookService);
                    if (option) option.selected = true;
                });
            }
            
            document.getElementById("profile-tags").value = (profile.tags || []).join(", ");
            document.getElementById("profile-notify-always").checked = profile.notify_always || false;
            document.getElementById("profile-include-in-digest").checked = profile.include_digest !== false;
            
            // Digest schedules
            populateInterestDigestSchedules(profile.digest_schedules || []);
            document.getElementById("interest-digest-mode").value = profile.digest_mode || "dm";
            document.getElementById("interest-digest-target-channel").value = profile.digest_target_channel || defaultInterestTargetChannel;
            
            // Update UI state
            const deleteBtn = document.getElementById("btn-delete-interest-profile");
            if (deleteBtn) {
                deleteBtn.classList.remove("d-none");
            }
            
            // Highlight in list
            renderInterestProfiles(filteredInterestProfiles);
            
            window.SharedUtils.showToast(`Loaded: ${profile.name}`, "info");
        } catch (error) {
            console.error("Failed to select profile:", error);
            window.SharedUtils.showToast("Failed to load profile", "error");
        }
    }
    
    /**
     * Save the current interest profile (create or update)
     * @param {Event} event - Form submission event
     * @returns {Promise<void>}
     */
    async function saveInterestProfile(event) {
        event.preventDefault();
        
        const profileId = document.getElementById("interest-profile-id").value;
        const name = document.getElementById("topic-name").value.trim();
        
        if (!name) {
            window.SharedUtils.showToast("Profile name is required", "warning");
            return;
        }
        
        // Collect all form data
        const profileData = {
            id: profileId ? parseInt(profileId) : undefined,
            name: name,
            description: document.getElementById("topic-description").value.trim(),
            enabled: document.getElementById("profile-enabled").value === "true",
            positive_samples: document.getElementById("positive-samples").value
                .split("\n").map(s => s.trim()).filter(s => s),
            negative_samples: document.getElementById("negative-samples").value
                .split("\n").map(s => s.trim()).filter(s => s),
            threshold: parseFloat(document.getElementById("similarity-threshold").value) || 0.42,
            vip_senders: document.getElementById("interest-vip-senders").value
                .split(",").map(s => s.trim()).filter(s => s).map(s => parseInt(s)).filter(n => !isNaN(n)),
            excluded_users: document.getElementById("interest-excluded-users").value
                .split(",").map(s => s.trim()).filter(s => s).map(s => parseInt(s)).filter(n => !isNaN(n)),
            channels: window.EntitySelector ? window.EntitySelector.getSelectedEntityIds('interest', 'channels') : [],
            users: window.EntitySelector ? window.EntitySelector.getSelectedEntityIds('interest', 'users') : [],
            tags: document.getElementById("profile-tags").value
                .split(",").map(s => s.trim()).filter(s => s),
            notify_always: document.getElementById("profile-notify-always").checked,
            include_digest: document.getElementById("profile-include-in-digest").checked,
            webhooks: Array.from(document.getElementById('interest-webhooks').selectedOptions || []).map(opt => opt.value),
            digest_schedules: extractInterestDigestSchedules(),
            digest_mode: document.getElementById("interest-digest-mode").value,
            digest_target_channel: document.getElementById("interest-digest-target-channel").value.trim()
        };
        
        try {
            const response = await fetch(interestProfileEndpoints.upsert, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(profileData)
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.message || "Failed to save profile");
            }
            
            const result = await response.json();
            window.SharedUtils.showToast(profileId ? "Profile updated" : "Profile created", "success");
            
            // Reload list and select the profile
            await loadInterestProfiles();
            if (result.profile_id) {
                await selectInterestProfile(result.profile_id);
            }
        } catch (error) {
            console.error("Failed to save profile:", error);
            window.SharedUtils.showToast(error.message || "Failed to save profile", "error");
        }
    }
    
    /**
     * Delete the currently selected interest profile
     * @returns {Promise<void>}
     */
    async function deleteInterestProfile() {
        const profileId = document.getElementById("interest-profile-id").value;
        const profileName = document.getElementById("topic-name").value;
        
        if (!profileId) {
            window.SharedUtils.showToast("No profile selected", "warning");
            return;
        }
        
        if (!confirm(`Delete profile "${profileName}"?`)) {
            return;
        }
        
        try {
            const response = await fetch(`${interestProfileEndpoints.delete}/${profileId}`, {
                method: "DELETE"
            });
            
            if (!response.ok) throw new Error("Failed to delete profile");
            
            window.SharedUtils.showToast("Profile deleted", "success");
            resetInterestProfileForm();
            await loadInterestProfiles();
        } catch (error) {
            console.error("Failed to delete profile:", error);
            window.SharedUtils.showToast("Failed to delete profile", "error");
        }
    }
    
    /**
     * Toggle enable/disable state of a profile
     * @param {number} profileId - ID of profile to toggle
     * @param {Event} event - Change event from checkbox
     * @returns {Promise<void>}
     */
    async function toggleInterestProfile(profileId, event) {
        event.stopPropagation();
        
        const checkbox = event.target;
        const newEnabledState = checkbox.checked;
        
        try {
            const response = await fetch(`${interestProfileEndpoints.toggle}/${profileId}/toggle`, {
                method: "POST"
            });
            
            if (!response.ok) throw new Error("Failed to toggle profile");
            
            const data = await response.json();
            window.SharedUtils.showToast(`Profile ${data.enabled ? 'enabled' : 'disabled'}`, "success");
            
            // Reload list
            await loadInterestProfiles();
        } catch (error) {
            console.error("Failed to toggle profile:", error);
            window.SharedUtils.showToast("Failed to toggle profile", "error");
            // Revert checkbox
            checkbox.checked = !newEnabledState;
        }
    }
    
    /**
     * Run backtest for an interest profile
     * @param {number} [profileId] - Optional profile ID. If not provided, uses current form profile
     * @returns {Promise<void>}
     */
    async function backtestInterestProfile(profileId) {
        let targetProfileId = profileId;
        let targetProfileName = '';
        
        // If profileId not provided, get from form
        if (!targetProfileId) {
            targetProfileId = document.getElementById("interest-profile-id").value;
            targetProfileName = document.getElementById("topic-name").value;
            
            if (!targetProfileId) {
                window.SharedUtils.showToast("Please select or save a profile first", "warning");
                return;
            }
        } else {
            // Load profile name from list with type coercion
            const profile = allInterestProfiles.find(p => String(p.id) === String(profileId));
            if (profile) {
                targetProfileName = profile.name;
            } else {
                // Fallback: fetch profile from API if not in local cache
                try {
                    const response = await fetch(`${interestProfileEndpoints.get}/${profileId}`);
                    if (response.ok) {
                        const data = await response.json();
                        if (data.status === 'ok' && data.profile) {
                            targetProfileName = data.profile.name;
                        }
                    }
                } catch (error) {
                    console.error("Failed to fetch profile for backtest:", error);
                }
                
                // If still not found, show warning and return
                if (!targetProfileName) {
                    window.SharedUtils.showToast(`Profile ${profileId} not found`, "warning");
                    return;
                }
            }
        }
        
        const modalEl = document.getElementById('backtestModal');
        const modal = new bootstrap.Modal(modalEl);
        
        // Apply glass backdrop effect
        window.SharedUtils.applyGlassBackdrop(modalEl);
        
        modal.show();
        
        document.getElementById('backtest-loading').classList.remove('d-none');
        document.getElementById('backtest-results').classList.add('d-none');
        document.getElementById('backtest-error').classList.add('d-none');
        
        try {
            const response = await fetch(interestProfileEndpoints.backtest, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    name: targetProfileName,
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
            
            // Check if any match has triggers data (for interest profiles, triggers are usually empty)
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
     * Reset the interest profile form to create a new profile
     */
    function resetInterestProfileForm() {
        currentInterestProfile = null;
        document.getElementById("interest-profile-id").value = "";
        document.getElementById("interest-profile-id-display").value = "Auto-generated";
        document.getElementById("topic-name").value = "";
        document.getElementById("topic-description").value = "";
        document.getElementById("positive-samples").value = "";
        document.getElementById("negative-samples").value = "";
        document.getElementById("similarity-threshold").value = "0.42";
        document.getElementById("profile-enabled").value = "true";
        document.getElementById("interest-vip-senders").value = "";
        document.getElementById("interest-excluded-users").value = "";
        document.getElementById("profile-tags").value = "";
        document.getElementById("profile-notify-always").checked = false;
        document.getElementById("profile-include-in-digest").checked = true;
        
        // Clear digest schedules
        for (let i = 1; i <= 3; i++) {
            document.getElementById(`interest-digest-schedule-${i}-type`).value = "";
            document.getElementById(`interest-digest-schedule-${i}-top-n`).value = 10 * i;
            document.getElementById(`interest-digest-schedule-${i}-min-score`).value = 6.0 - i;
        }
        
        document.getElementById("interest-digest-mode").value = "dm";
        document.getElementById("interest-digest-target-channel").value = defaultInterestTargetChannel;
        
        const deleteBtn = document.getElementById("btn-delete-interest-profile");
        if (deleteBtn) {
            deleteBtn.classList.add("d-none");
        }
        
        renderInterestProfiles(filteredInterestProfiles);
    }
    
    /**
     * Reset form and show "new profile" toast
     */
    function newInterestProfile() {
        resetInterestProfileForm();
        window.SharedUtils.showToast("Create new interest profile", "info");
    }
    
    /**
     * Filter profiles by search term
     * @param {string} searchTerm - Search term to filter by
     */
    function filterInterestProfiles(searchTerm) {
        if (!searchTerm || searchTerm.trim() === "") {
            filteredInterestProfiles = [...allInterestProfiles];
        } else {
            const lowerSearch = searchTerm.toLowerCase();
            filteredInterestProfiles = allInterestProfiles.filter(profile => {
                const name = (profile.name || "").toLowerCase();
                const description = (profile.description || "").toLowerCase();
                return name.includes(lowerSearch) || description.includes(lowerSearch);
            });
        }
        renderInterestProfiles(filteredInterestProfiles);
    }
    
    /**
     * Export a single profile as JSON file
     * @param {number} profileId - ID of profile to export
     */
    function exportInterestProfile(profileId) {
        const profile = allInterestProfiles.find(p => String(p.id) === String(profileId));
        if (!profile) {
            window.SharedUtils.showToast("Profile not found", "error");
            return;
        }
        
        const dataStr = JSON.stringify(profile, null, 2);
        const dataBlob = new Blob([dataStr], { type: 'application/json' });
        const url = URL.createObjectURL(dataBlob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `interest-profile-${profile.name.replace(/[^a-z0-9]/gi, '_').toLowerCase()}-${new Date().toISOString().split('T')[0]}.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        
        window.SharedUtils.showToast(`Exported: ${profile.name}`, 'success');
    }
    
    /**
     * Import an interest profile from JSON file
     * Validates that the file contains an Interest profile (not Alert profile)
     * @returns {Promise<void>}
     */
    async function importInterestProfile() {
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
                
                // Validate this is an Interest profile
                // Interest profiles have: positive_samples, negative_samples, threshold
                // Alert profiles have: action_keywords, decision_keywords, detect_questions, keywords, etc.
                const isInterestProfile = (
                    ('positive_samples' in profile || 'negative_samples' in profile || 'threshold' in profile) &&
                    !('action_keywords' in profile || 'detect_questions' in profile || 'keywords' in profile)
                );
                
                if (!isInterestProfile) {
                    window.SharedUtils.showToast(
                        'Invalid file: This appears to be an Alert profile. Please import it in the Alert Profiles section.',
                        'error'
                    );
                    return;
                }
                
                // Remove timestamps and ID to create as new profile
                const { id: _omitId, created_at: _omitCreated, updated_at: _omitUpdated, ...rest } = profile;
                
                // Compute next available ID
                const existingIds = allInterestProfiles
                    .map(p => parseInt(p.id, 10))
                    .filter(id => Number.isFinite(id) && id >= 3000 && id < 4000);
                const nextId = existingIds.length ? Math.max(...existingIds) + 1 : 3000;
                
                const importedProfile = {
                    ...rest,
                    id: nextId,
                    name: `${rest.name || 'Imported Profile'}`,
                    enabled: false, // Start disabled for safety
                    created_at: new Date().toISOString(),
                    updated_at: new Date().toISOString()
                };
                
                // Save the profile
                const response = await fetch(interestProfileEndpoints.upsert, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(importedProfile)
                });
                
                if (!response.ok) throw new Error("Failed to import profile");
                
                const result = await response.json();
                window.SharedUtils.showToast(`Imported: ${importedProfile.name}`, "success");
                
                // Reload list and select the new profile
                await loadInterestProfiles();
                if (result.profile_id) {
                    await selectInterestProfile(result.profile_id);
                }
            } catch (error) {
                console.error("Failed to import profile:", error);
                if (error instanceof SyntaxError) {
                    window.SharedUtils.showToast("Invalid JSON file", "error");
                } else {
                    window.SharedUtils.showToast("Failed to import profile", "error");
                }
            } finally {
                document.body.removeChild(fileInput);
            }
        };
        
        document.body.appendChild(fileInput);
        fileInput.click();
    }
    
    /**
     * Duplicate an existing profile
     * @param {number} profileId - ID of profile to duplicate
     * @returns {Promise<void>}
     */
    async function duplicateInterestProfile(profileId) {
        const profile = allInterestProfiles.find(p => p.id === profileId);
        if (!profile) {
            window.SharedUtils.showToast("Profile not found", "error");
            return;
        }
        
        // Compute a client-side next ID to avoid overwriting existing entries
        const existingIds = allInterestProfiles
            .map(p => parseInt(p.id, 10))
            .filter(id => Number.isFinite(id) && id >= 3000 && id < 4000);
        const nextId = existingIds.length ? Math.max(...existingIds) + 1 : 3000;

        // Create a copy with modified name
        const { id: _omitId, created_at: _omitCreated, updated_at: _omitUpdated, ...rest } = profile;
        const copy = {
            ...rest,
            id: nextId,
            name: `${profile.name} (Copy)`,
            enabled: false, // Start disabled
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString()
        };

        try {
            const response = await fetch(interestProfileEndpoints.upsert, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(copy)
            });
            
            if (!response.ok) throw new Error("Failed to duplicate profile");
            
            const result = await response.json();
            window.SharedUtils.showToast(`Duplicated: ${copy.name}`, "success");
            
            // Reload list and select the new profile
            await loadInterestProfiles();
            if (result.profile_id) {
                await selectInterestProfile(result.profile_id);
            }
        } catch (error) {
            console.error("Failed to duplicate profile:", error);
            window.SharedUtils.showToast("Failed to duplicate profile", "error");
        }
    }
    
    /**
     * Export all profiles as JSON file
     */
    function exportAllInterestProfiles() {
        const dataStr = JSON.stringify(allInterestProfiles, null, 2);
        const dataBlob = new Blob([dataStr], { type: 'application/json' });
        const url = URL.createObjectURL(dataBlob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `interest-profiles-${new Date().toISOString().split('T')[0]}.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        
        window.SharedUtils.showToast(`Exported ${allInterestProfiles.length} profiles`, 'success');
    }
    
    /**
     * Bulk enable or disable all profiles
     * @param {boolean} enabled - True to enable, false to disable
     * @returns {Promise<void>}
     */
    async function bulkToggleInterestProfiles(enabled) {
        if (!confirm(`Are you sure you want to ${enabled ? 'enable' : 'disable'} all interest profiles?`)) {
            return;
        }
        
        // Create AbortController with timeout
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 30000); // 30 second timeout
        
        try {
            // Build array of fetch promises
            const togglePromises = allInterestProfiles.map(profile =>
                fetch(`${interestProfileEndpoints.toggle}/${profile.id}/toggle`, {
                    method: 'POST',
                    signal: controller.signal
                })
                .then(response => ({ profile, response, success: response.ok }))
                .catch(error => ({ profile, error, success: false }))
            );
            
            // Wait for all requests to complete
            const results = await Promise.allSettled(togglePromises);
            
            // Process results and count successes/failures
            let successCount = 0;
            let failCount = 0;
            
            results.forEach(result => {
                if (result.status === 'fulfilled') {
                    const { profile, success, error } = result.value;
                    if (success) {
                        successCount++;
                    } else {
                        failCount++;
                        console.error(`Failed to toggle profile ${profile.id}:`, error || 'Request failed');
                    }
                } else {
                    // Promise was rejected
                    failCount++;
                    console.error('Toggle request failed:', result.reason);
                }
            });
            
            // Show consolidated toast
            window.SharedUtils.showToast(
                `${successCount} profiles ${enabled ? 'enabled' : 'disabled'}${failCount > 0 ? `, ${failCount} failed` : ''}`, 
                failCount > 0 ? 'warning' : 'success'
            );
            
        } catch (error) {
            console.error('Bulk toggle operation failed:', error);
            window.SharedUtils.showToast('Bulk toggle operation failed', 'error');
        } finally {
            clearTimeout(timeoutId);
        }
        
        // Reload profiles after all requests complete
        await loadInterestProfiles();
    }
    
    /**
     * Test similarity of a phrase against the selected profile
     * @returns {Promise<void>}
     */
    async function runSimilarityTest() {
        const sample = document.getElementById("test-phrase")?.value.trim();
        if (!sample) {
            window.SharedUtils.showToast("Enter a phrase to test", "warning");
            return;
        }
        
        const selectEl = document.getElementById("profile-select");
        if (!selectEl) {
            console.error("Profile select element not found");
            window.SharedUtils.showToast("Profile selector unavailable", "error");
            return;
        }
        
        const interest = selectEl.value?.trim();
        if (!interest) {
            window.SharedUtils.showToast("Select an interest profile", "warning");
            return;
        }

        // Show loading state
        const btnTest = document.getElementById("btn-run-test");
        const resultContainer = document.getElementById("similarity-result-container");
        const scoreValueEl = document.getElementById("similarity-score-value");
        const scoreBarEl = document.getElementById("similarity-score-bar");
        
        if (btnTest) {
            btnTest.disabled = true;
            btnTest.innerHTML = `
                <span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>
                Testing...
            `;
        }
        
        try {
            const response = await fetch(interestProfileEndpoints.test, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sample, interest }),
            });
            
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || "Test failed");
            }
            
            const payload = await response.json();
            
            if (typeof payload !== "object" || payload === null) {
                console.warn("Unexpected similarity payload", payload);
                throw new Error("Malformed response");
            }
            
            const score = Number(payload.score);
            if (!Number.isFinite(score)) {
                console.warn("Similarity score missing or invalid", payload);
                throw new Error("Invalid score");
            }
            
            // Clamp score to [0, 1] range
            const clampedScore = Math.max(0, Math.min(score, 1));
            const formatted = clampedScore.toFixed(3);
            const percentage = Math.round(clampedScore * 100);
            
            // Update score display
            if (scoreValueEl) {
                scoreValueEl.textContent = formatted;
            }
            
            // Update progress bar
            if (scoreBarEl) {
                scoreBarEl.style.width = `${percentage}%`;
                scoreBarEl.setAttribute("aria-valuenow", percentage.toString());
                
                // Color code the progress bar based on score
                scoreBarEl.className = "progress-bar";
                if (clampedScore < 0.30) {
                    scoreBarEl.classList.add("bg-secondary");
                } else if (clampedScore < 0.50) {
                    scoreBarEl.classList.add("bg-info");
                } else if (clampedScore < 0.70) {
                    scoreBarEl.classList.add("bg-primary");
                } else {
                    scoreBarEl.classList.add("bg-success");
                }
            }
            
            // Show result container
            if (resultContainer) {
                resultContainer.classList.remove("d-none");
                resultContainer.classList.remove("alert-info");
                
                // Color code the alert based on score
                if (clampedScore < 0.30) {
                    resultContainer.classList.add("alert-secondary");
                } else if (clampedScore < 0.70) {
                    resultContainer.classList.add("alert-info");
                } else {
                    resultContainer.classList.add("alert-success");
                }
            }
            
            // Show interpretation if provided
            const interpretation = payload.interpretation || "";
            if (interpretation) {
                console.log(`Interpretation: ${interpretation}`);
            }
            
            window.SharedUtils.showToast(
                `Similarity score: ${formatted} - ${interpretation}`,
                clampedScore >= 0.50 ? "success" : "info"
            );
            
        } catch (error) {
            console.error("Failed to run similarity test:", error);
            window.SharedUtils.showToast(
                error.message || "Failed to test similarity",
                "error"
            );
        } finally {
            // Restore button state
            if (btnTest) {
                btnTest.disabled = false;
                btnTest.innerHTML = `
                    <svg width="16" height="16" fill="currentColor" class="bi bi-lightning-charge" viewBox="0 0 16 16">
                        <path d="M11.251.068a.5.5 0 0 1 .227.58L9.677 6.5H13a.5.5 0 0 1 .364.843l-8 8.5a.5.5 0 0 1-.842-.49L6.323 9.5H3a.5.5 0 0 1-.364-.843l8-8.5a.5.5 0 0 1 .615-.09z"/>
                    </svg>
                    Test Similarity
                `;
            }
        }
    }
    
    // ============= PRIVATE HELPERS =============
    
    /**
     * Extract digest schedules from form fields
     * @returns {Array} Array of digest schedule objects
     */
    function extractInterestDigestSchedules() {
        const schedules = [];
        for (let i = 1; i <= 3; i++) {
            const type = document.getElementById(`interest-digest-schedule-${i}-type`).value;
            if (!type) continue;
            
            const schedule = {
                schedule_type: type,
                top_n: parseInt(document.getElementById(`interest-digest-schedule-${i}-top-n`).value) || 10,
                min_score: parseFloat(document.getElementById(`interest-digest-schedule-${i}-min-score`).value) || 5.0
            };
            
            if (type === 'daily') {
                schedule.daily_hour = parseInt(document.getElementById(`interest-digest-schedule-${i}-daily-hour`).value) || 8;
            } else if (type === 'weekly') {
                schedule.weekly_day = parseInt(document.getElementById(`interest-digest-schedule-${i}-weekly-day`).value) || 0;
                schedule.weekly_hour = parseInt(document.getElementById(`interest-digest-schedule-${i}-weekly-hour`).value) || 8;
            }
            
            schedules.push(schedule);
        }
        return schedules;
    }
    
    /**
     * Populate digest schedule fields from data
     * @param {Array} schedules - Array of schedule objects
     */
    function populateInterestDigestSchedules(schedules) {
        // Clear all first
        for (let i = 1; i <= 3; i++) {
            document.getElementById(`interest-digest-schedule-${i}-type`).value = "";
            document.getElementById(`interest-digest-schedule-${i}-top-n`).value = 10 * i;
            document.getElementById(`interest-digest-schedule-${i}-min-score`).value = 6.0 - i;
        }
        
        // Populate provided schedules
        schedules.forEach((schedule, index) => {
            if (index >= 3) return;
            const i = index + 1;
            document.getElementById(`interest-digest-schedule-${i}-type`).value = schedule.schedule_type || "";
            document.getElementById(`interest-digest-schedule-${i}-top-n`).value = schedule.top_n || 10;
            document.getElementById(`interest-digest-schedule-${i}-min-score`).value = schedule.min_score || 5.0;
            
            if (schedule.schedule_type === 'daily') {
                document.getElementById(`interest-digest-schedule-${i}-daily-hour`).value = schedule.daily_hour || 8;
            } else if (schedule.schedule_type === 'weekly') {
                document.getElementById(`interest-digest-schedule-${i}-weekly-day`).value = schedule.weekly_day || 0;
                document.getElementById(`interest-digest-schedule-${i}-weekly-hour`).value = schedule.weekly_hour || 8;
            }
        });
    }
    
    // ============= EXPORT PUBLIC API =============
    
    window.InterestProfiles = {
        init,
        loadInterestProfiles,
        selectInterestProfile,
        saveInterestProfile,
        deleteInterestProfile,
        toggleInterestProfile,
        backtestInterestProfile,
        resetInterestProfileForm,
        newInterestProfile,
        filterInterestProfiles,
        exportInterestProfile,
        importInterestProfile,
        duplicateInterestProfile,
        exportAllInterestProfiles,
        bulkToggleInterestProfiles,
        runSimilarityTest
    };
    
})();
