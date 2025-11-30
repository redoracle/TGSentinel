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
    
    // ============= HELPER FUNCTIONS =============
    
    /**
     * Toggle visibility of schedule-specific fields based on schedule type
     * @param {number} scheduleNum - Schedule number (1-3)
     * @param {string} scheduleType - Schedule type ('daily', 'weekly', etc.)
     */
    function toggleInterestScheduleFields(scheduleNum, scheduleType) {
        const dailySettings = document.getElementById(`interest-digest-schedule-${scheduleNum}-daily-settings`);
        const weeklySettings = document.getElementById(`interest-digest-schedule-${scheduleNum}-weekly-settings`);
        const weeklyHourSettings = document.getElementById(`interest-digest-schedule-${scheduleNum}-weekly-hour-settings`);
        
        // Hide all first
        if (dailySettings) dailySettings.classList.add('d-none');
        if (weeklySettings) weeklySettings.classList.add('d-none');
        if (weeklyHourSettings) weeklyHourSettings.classList.add('d-none');
        
        // Show relevant fields based on schedule type
        if (scheduleType === 'daily' && dailySettings) {
            dailySettings.classList.remove('d-none');
        } else if (scheduleType === 'weekly') {
            if (weeklySettings) weeklySettings.classList.remove('d-none');
            if (weeklyHourSettings) weeklyHourSettings.classList.remove('d-none');
        }
    }
    
    /**
     * Toggle visibility of digest batch settings (Top N, Min Score) based on Delivery Mode
     * These fields only apply when Digest or Both modes are selected
     * @param {number} scheduleNum - Schedule number (1-3)
     * @param {string} deliveryMode - Delivery mode ('none', 'dm', 'digest', 'both')
     */
    function toggleInterestDigestBatchSettings(scheduleNum, deliveryMode) {
        const topNWrapper = document.getElementById(`interest-digest-schedule-${scheduleNum}-topn-wrapper`);
        const minScoreWrapper = document.getElementById(`interest-digest-schedule-${scheduleNum}-minscore-wrapper`);
        
        // Show batch settings only for digest or both modes
        const showBatchSettings = (deliveryMode === 'digest' || deliveryMode === 'both');
        
        if (topNWrapper) {
            topNWrapper.classList.toggle('d-none', !showBatchSettings);
        }
        if (minScoreWrapper) {
            minScoreWrapper.classList.toggle('d-none', !showBatchSettings);
        }
    }
    
    /**
     * Attach change listeners to schedule type dropdowns
     */
    function attachScheduleTypeListeners() {
        for (let i = 1; i <= 3; i++) {
            const typeSelect = document.getElementById(`interest-digest-schedule-${i}-type`);
            const modeSelect = document.getElementById(`interest-digest-schedule-${i}-mode`);
            
            if (typeSelect) {
                // Capture the current value of i
                const scheduleNum = i;
                typeSelect.addEventListener('change', function() {
                    toggleInterestScheduleFields(scheduleNum, this.value);
                });
                // Initialize visibility on page load
                toggleInterestScheduleFields(scheduleNum, typeSelect.value);
            }
            
            if (modeSelect) {
                const scheduleNum = i;
                modeSelect.addEventListener('change', function() {
                    toggleInterestDigestBatchSettings(scheduleNum, this.value);
                });
                // Initialize visibility on page load
                toggleInterestDigestBatchSettings(scheduleNum, modeSelect.value);
            }
        }
    }
    
    // ============= PUBLIC API =============
    
    /**
     * Initialize the Interest Profiles module with API endpoints
     * @param {Object} endpoints - Object containing API endpoint URLs
     */
    function init(endpoints) {
        interestProfileEndpoints = endpoints;
        
        // Attach schedule type listeners after DOM is ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', attachScheduleTypeListeners);
        } else {
            attachScheduleTypeListeners();
        }
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
            const profile = data.data;
            
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
            document.getElementById("positive-weight").value = profile.positive_weight || 1.0;
            document.getElementById("negative-weight").value = profile.negative_weight || 0.15;
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
            
            // Digest configuration (handle both nested 'digest' object and legacy flat fields)
            const digest = profile.digest || {};
            const digestSchedules = digest.schedules || profile.digest_schedules || [];
            
            populateInterestDigestSchedules(digestSchedules);
            
            // Update UI state
            const deleteBtn = document.getElementById("btn-delete-interest-profile");
            if (deleteBtn) {
                deleteBtn.classList.remove("d-none");
            }
            
            // Highlight in list
            renderInterestProfiles(filteredInterestProfiles);
            
            // Load feedback stats for this profile
            if (window.InterestFeedbackStats && window.InterestFeedbackStats.loadStatsForProfile) {
                window.InterestFeedbackStats.loadStatsForProfile(profile.id);
            }
            
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
            positive_weight: parseFloat(document.getElementById("positive-weight")?.value) || 1.0,
            negative_weight: parseFloat(document.getElementById("negative-weight")?.value) || 0.15,
            vip_senders: document.getElementById("interest-vip-senders").value
                .split(",").map(s => s.trim()).filter(s => s).map(s => parseInt(s)).filter(n => !isNaN(n)),
            excluded_users: document.getElementById("interest-excluded-users").value
                .split(",").map(s => s.trim()).filter(s => s).map(s => parseInt(s)).filter(n => !isNaN(n)),
            channels: window.EntitySelector ? window.EntitySelector.getSelectedEntityIds('interest', 'channels') : [],
            users: window.EntitySelector ? window.EntitySelector.getSelectedEntityIds('interest', 'users') : [],
            tags: document.getElementById("profile-tags").value
                .split(",").map(s => s.trim()).filter(s => s),
            webhooks: Array.from(document.getElementById('interest-webhooks').selectedOptions || []).map(opt => opt.value),
            digest: {
                schedules: extractInterestDigestSchedules()
            }
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
                        if (data.status === 'ok' && data.data) {
                            targetProfileName = data.data.name;
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
            const avgScore = data.stats.avg_matched_score || data.stats.avg_score || data.stats.average_score || 0;
            document.getElementById('stat-avg-score').textContent = avgScore.toFixed(3);
            
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
            
            // Matches table - NOW SHOWS ALL MESSAGES (matched + unmatched)
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
                    // Use badges to indicate match status (no row classes)
                    const matchBadge = match.matched 
                        ? '<span class="badge bg-success">✓ Matched</span>' 
                        : '<span class="badge bg-secondary">✗ Not Matched</span>';
                    
                    // Format semantic score with coefficient info
                    const scoreDisplay = match.semantic_score !== null && match.semantic_score !== undefined
                        ? `<span class="badge ${match.matched ? 'bg-primary' : 'bg-dark'}">${match.semantic_score.toFixed(3)}</span><br>
                           <small class="text-muted">threshold: ${match.threshold?.toFixed(2) || 'N/A'}</small>`
                        : '<span class="text-muted">N/A</span>';
                    
                    // Format reason with proper styling
                    const reasonDisplay = match.reason 
                        ? `<small class="${match.matched ? 'text-success' : 'text-warning'}">${window.SharedUtils.escapeHtml(match.reason)}</small>`
                        : '<small class="text-muted">No reason provided</small>';
                    
                    const webhookPayload = {
                        event: "interest_backtest",
                        profile_id: data.profile_id || profileId,
                        profile_name: data.profile_name || document.getElementById("topic-name")?.value || "",
                        chat_id: match.chat_id || match.channel_id || null,
                        chat_title: match.chat_title || "Unknown",
                        message_id: match.message_id,
                        semantic_score: match.semantic_score,
                        matched: match.matched,
                        reason: match.reason,
                        text_preview: match.text_preview || "",
                    };
                    const webhookTooltip = window.SharedUtils.escapeHtml(JSON.stringify(webhookPayload, null, 2));
                    
                    return `
                        <tr>
                            <td>${window.SharedUtils.escapeHtml(match.chat_title || 'Unknown')}</td>
                            <td>${match.message_id}</td>
                            <td>${scoreDisplay}</td>
                            <td style="display: none;"></td>
                            <td>${matchBadge}</td>
                            <td>
                                <i class="bi bi-webhook text-info" data-bs-toggle="tooltip" data-bs-placement="top" title="${webhookTooltip}"></i>
                            </td>
                            <td>
                                <small>${window.SharedUtils.escapeHtml((match.text_preview || '').substring(0, 100))}</small><br>
                                ${reasonDisplay}
                            </td>
                        </tr>
                    `;
                }).join('');
            } else {
                tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">No messages tested</td></tr>`;
            }
            const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle=\"tooltip\"]'));
            tooltipTriggerList.forEach(function (tooltipTriggerEl) {
                new bootstrap.Tooltip(tooltipTriggerEl);
            });
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
        document.getElementById("positive-weight").value = "1.0";
        document.getElementById("negative-weight").value = "0.15";
        document.getElementById("profile-enabled").value = "true";
        document.getElementById("interest-vip-senders").value = "";
        document.getElementById("interest-excluded-users").value = "";
        document.getElementById("profile-tags").value = "";
        
        // Clear digest schedules and hide schedule-specific fields
        for (let i = 1; i <= 3; i++) {
            document.getElementById(`interest-digest-schedule-${i}-type`).value = "";
            document.getElementById(`interest-digest-schedule-${i}-top-n`).value = 10 * i;
            document.getElementById(`interest-digest-schedule-${i}-min-score`).value = 0.45;
            toggleInterestScheduleFields(i, '');  // Hide all schedule-specific fields
            toggleInterestDigestBatchSettings(i, 'dm');  // Hide batch settings for DM mode
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
                throw new Error(errorData.message || "Failed to test similarity");
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
            
            // Get max sample similarity if available (for exact match detection)
            const maxSampleSim = payload.max_sample_similarity;
            const threshold = payload.threshold || 0.45;
            const willMatch = payload.will_match || (clampedScore >= threshold);
            
            // Update score display with additional context
            if (scoreValueEl) {
                let scoreText = formatted;
                // Always show max sample similarity when available (useful for debugging)
                // This shows the best match to any individual training sample
                if (maxSampleSim !== undefined && maxSampleSim !== null) {
                    scoreText += ` (best match: ${maxSampleSim.toFixed(3)})`;
                }
                scoreValueEl.textContent = scoreText;
            }
            
            // Update progress bar
            if (scoreBarEl) {
                scoreBarEl.style.width = `${percentage}%`;
                scoreBarEl.setAttribute("aria-valuenow", percentage.toString());
                
                // Color code the progress bar based on whether it will match the threshold
                scoreBarEl.className = "progress-bar";
                if (willMatch) {
                    scoreBarEl.classList.add("bg-success");
                } else if (clampedScore >= threshold * 0.8) {
                    scoreBarEl.classList.add("bg-primary");
                } else if (clampedScore >= threshold * 0.5) {
                    scoreBarEl.classList.add("bg-info");
                } else {
                    scoreBarEl.classList.add("bg-secondary");
                }
            }
            
            // Show result container
            if (resultContainer) {
                resultContainer.classList.remove("d-none", "alert-info", "alert-secondary", "alert-success", "alert-warning");
                
                // Color code the alert based on match status
                if (willMatch) {
                    resultContainer.classList.add("alert-success");
                } else if (clampedScore >= threshold * 0.8) {
                    resultContainer.classList.add("alert-warning");
                } else {
                    resultContainer.classList.add("alert-info");
                }
            }
            
            // Build interpretation message
            const interpretation = payload.interpretation || "";
            let toastMessage = `Similarity: ${formatted}`;
            if (interpretation) {
                toastMessage += ` - ${interpretation}`;
            }
            if (willMatch) {
                toastMessage = `✓ ${toastMessage} (matches threshold ${threshold.toFixed(2)})`;
            }
            
            console.log(`Similarity test result: score=${score}, maxSampleSim=${maxSampleSim}, threshold=${threshold}, willMatch=${willMatch}`);
            
            window.SharedUtils.showToast(toastMessage, willMatch ? "success" : "info");
            
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
     * 
     * Schedule semantics:
     * - schedule: "none" = instant alerts mode (no batching)
     * - schedule: "hourly"|"daily"|etc = batched digest mode
     * - mode: "none" = save only, "dm" = instant DM, "digest" = digest only, "both" = dm + digest
     * 
     * Schedule 1 is ALWAYS saved (even with schedule="none") to preserve delivery mode.
     * Schedules 2-3 are only saved if they have a non-empty schedule type.
     */
    function extractInterestDigestSchedules() {
        const schedules = [];
        const defaultChannel = document.querySelector('[data-default-channel]')?.dataset.defaultChannel || '';
        
        for (let i = 1; i <= 3; i++) {
            const typeEl = document.getElementById(`interest-digest-schedule-${i}-type`);
            const modeEl = document.getElementById(`interest-digest-schedule-${i}-mode`);
            const type = typeEl ? typeEl.value : '';
            const mode = modeEl ? modeEl.value : 'dm';
            
            // Schedule 1 is ALWAYS saved to preserve the primary delivery mode
            // Schedules 2-3 require a schedule type to be saved
            if (i > 1 && !type) continue;
            
            const schedule = {
                // Use "none" for empty schedule type (instant alerts mode)
                schedule: type || "none",
                top_n: parseInt(document.getElementById(`interest-digest-schedule-${i}-top-n`).value) || 10,
                min_score: parseFloat(document.getElementById(`interest-digest-schedule-${i}-min-score`).value) || 5.0,
                mode: mode,
                target_channel: document.getElementById(`interest-digest-schedule-${i}-target-channel`)?.value.trim() || null
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
        const defaultChannel = document.querySelector('[data-default-channel]')?.dataset.defaultChannel || '';
        
        // Clear all first and hide schedule-specific fields
        for (let i = 1; i <= 3; i++) {
            document.getElementById(`interest-digest-schedule-${i}-type`).value = "";
            document.getElementById(`interest-digest-schedule-${i}-top-n`).value = 10 * i;
            document.getElementById(`interest-digest-schedule-${i}-min-score`).value = 0.45;
            document.getElementById(`interest-digest-schedule-${i}-mode`).value = "dm";
            document.getElementById(`interest-digest-schedule-${i}-target-channel`).value = defaultChannel;
            toggleInterestScheduleFields(i, '');  // Hide all schedule-specific fields
            toggleInterestDigestBatchSettings(i, 'dm');  // Hide batch settings for DM mode
        }
        
        // Populate provided schedules
        schedules.forEach((schedule, index) => {
            if (index >= 3) return;
            const i = index + 1;
            // Backend always returns 'schedule' field
            let scheduleType = schedule.schedule || "";
            // Map "none" schedule type back to empty string for UI dropdown
            // "none" in YAML means "instant alerts mode" which is displayed as "None (Instant Alerts)"
            if (scheduleType === "none") {
                scheduleType = "";
            }
            const schedMode = schedule.mode || "dm";
            document.getElementById(`interest-digest-schedule-${i}-type`).value = scheduleType;
            document.getElementById(`interest-digest-schedule-${i}-top-n`).value = schedule.top_n || 10;
            document.getElementById(`interest-digest-schedule-${i}-min-score`).value = schedule.min_score || 0.45;
            document.getElementById(`interest-digest-schedule-${i}-mode`).value = schedMode;
            document.getElementById(`interest-digest-schedule-${i}-target-channel`).value = schedule.target_channel || defaultChannel;
            
            // Set schedule-specific fields
            if (scheduleType === 'daily') {
                document.getElementById(`interest-digest-schedule-${i}-daily-hour`).value = schedule.daily_hour || 8;
            } else if (scheduleType === 'weekly') {
                document.getElementById(`interest-digest-schedule-${i}-weekly-day`).value = schedule.weekly_day || 0;
                document.getElementById(`interest-digest-schedule-${i}-weekly-hour`).value = schedule.weekly_hour || 8;
            }
            
            // Toggle visibility of schedule-specific fields
            toggleInterestScheduleFields(i, scheduleType);
            // Toggle visibility of batch settings based on delivery mode
            toggleInterestDigestBatchSettings(i, schedMode);
        });
    }
    
    /**
     * Set coefficient values (for preset buttons)
     * @param {number} positiveWeight - Positive weight value
     * @param {number} negativeWeight - Negative weight value
     */
    function setCoefficients(positiveWeight, negativeWeight) {
        document.getElementById('positive-weight').value = positiveWeight.toFixed(2);
        document.getElementById('negative-weight').value = negativeWeight.toFixed(2);
        window.SharedUtils.showToast(`Coefficients set: pos=${positiveWeight}, neg=${negativeWeight}`, 'info');
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
        runSimilarityTest,
        setCoefficients
    };
    
})();
