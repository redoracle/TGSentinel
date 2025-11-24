/**
 * Profiles Initialization Module
 * 
 * Initializes all profile management modules and sets up page-level event handlers.
 * This module orchestrates the initialization sequence, tab persistence,
 * form event handlers, and bulk action dropdowns.
 * 
 * Dependencies:
 *   - window.SharedUtils
 *   - window.DigestEditor
 *   - window.EntitySelector
 *   - window.AlertProfiles
 *   - window.InterestProfiles
 *   - Bootstrap 5
 * 
 * Initialization Order:
 *   1. Tab persistence (restore from localStorage)
 *   2. Entity selector (fetch channels/users)
 *   3. Alert Profiles (load list, set up handlers)
 *   4. Interest Profiles (load list, set up handlers)
 *   5. Bulk action dropdowns
 *   6. Search inputs
 *   7. Advanced section toggles
 *   8. Export/import handlers
 */

(function() {
    'use strict';
    
    /**
     * Initialize all profiles functionality
     */
    function init() {
        // Restore active tab from localStorage
        restoreActiveTab();
        
        // Save active tab on change
        saveActiveTabOnChange();
        
        // Load available webhooks for profile forms
        loadAvailableWebhooks();
        
        // Initialize entity selector (fetch monitored channels/users)
        if (window.EntitySelector) {
            window.EntitySelector.init();
        }
        
        // Load Alert Profiles
        if (window.AlertProfiles) {
            window.AlertProfiles.loadAlertProfiles();
            setupAlertProfileHandlers();
        }
        
        // Load Interest Profiles
        if (window.InterestProfiles) {
            window.InterestProfiles.loadInterestProfiles();
            setupInterestProfileHandlers();
        }
        
        // Setup bulk action dropdowns
        setupBulkActionsDropdown();
        setupInterestBulkActionsDropdown();
        
        // Setup advanced section toggles
        setupAdvancedSectionToggles();
        
        // Setup digest schedule toggles
        setupDigestScheduleToggles();
        
        // Setup export/import handlers
        setupExportImportHandlers();
    }
    
    /**
     * Load available webhooks from /api/webhooks and populate select fields
     */
    async function loadAvailableWebhooks() {
        const alertSelect = document.getElementById('alert-webhooks');
        const interestSelect = document.getElementById('interest-webhooks');
        
        try {
            const response = await fetch('/api/webhooks', {
                signal: AbortSignal.timeout(10000) // 10 second timeout
            });
            
            if (!response.ok) {
                console.warn('[WEBHOOKS] Failed to load webhooks, status:', response.status);
                // Show error state
                if (alertSelect) {
                    alertSelect.innerHTML = '<option disabled selected>Failed to load webhooks</option>';
                    alertSelect.setAttribute('aria-busy', 'false');
                }
                if (interestSelect) {
                    interestSelect.innerHTML = '<option disabled selected>Failed to load webhooks</option>';
                    interestSelect.setAttribute('aria-busy', 'false');
                }
                return;
            }
            
            const data = await response.json();
            const webhooks = data.webhooks || [];
            const webhooksEnabled = data.enabled !== false;
            
            if (!webhooksEnabled) {
                console.info('[WEBHOOKS] Webhook feature is disabled (WEBHOOK_SECRET_KEY not set)');
                // Hide webhook sections if webhooks are disabled
                document.querySelectorAll('.webhook-integration-section').forEach(el => el.style.display = 'none');
                // Update aria-busy state
                if (alertSelect) alertSelect.setAttribute('aria-busy', 'false');
                if (interestSelect) interestSelect.setAttribute('aria-busy', 'false');
                return;
            }
            
            // Populate alert profile webhook select
            if (alertSelect) {
                if (webhooks.length === 0) {
                    alertSelect.innerHTML = '<option disabled selected>No webhooks available</option>';
                } else {
                    alertSelect.innerHTML = webhooks.map(wh => 
                        `<option value="${wh.service}">${wh.service}</option>`
                    ).join('');
                }
                alertSelect.setAttribute('aria-busy', 'false');
            }
            
            // Populate interest profile webhook select
            if (interestSelect) {
                if (webhooks.length === 0) {
                    interestSelect.innerHTML = '<option disabled selected>No webhooks available</option>';
                } else {
                    interestSelect.innerHTML = webhooks.map(wh => 
                        `<option value="${wh.service}">${wh.service}</option>`
                    ).join('');
                }
                interestSelect.setAttribute('aria-busy', 'false');
            }
            
            console.info(`[WEBHOOKS] Loaded ${webhooks.length} webhook(s) for profile forms`);
            
        } catch (error) {
            console.error('[WEBHOOKS] Error loading webhooks:', error);
            // Show error state on exception (network error, timeout, etc.)
            if (alertSelect) {
                alertSelect.innerHTML = '<option disabled selected>Failed to load webhooks</option>';
                alertSelect.setAttribute('aria-busy', 'false');
            }
            if (interestSelect) {
                interestSelect.innerHTML = '<option disabled selected>Failed to load webhooks</option>';
                interestSelect.setAttribute('aria-busy', 'false');
            }
        }
    }
    
    /**
     * Restore active tab from localStorage
     */
    function restoreActiveTab() {
        const savedTab = localStorage.getItem('profiles-active-tab');
        if (savedTab) {
            const tabTrigger = document.querySelector(`button[data-bs-target="${savedTab}"]`);
            if (tabTrigger) {
                const tab = new bootstrap.Tab(tabTrigger);
                tab.show();
            }
        }
    }
    
    /**
     * Save active tab to localStorage on tab change
     */
    function saveActiveTabOnChange() {
        document.querySelectorAll('button[data-bs-toggle="tab"]').forEach(tabButton => {
            tabButton.addEventListener('shown.bs.tab', (event) => {
                const targetTab = event.target.getAttribute('data-bs-target');
                localStorage.setItem('profiles-active-tab', targetTab);
            });
        });
    }
    
    /**
     * Setup Alert Profile form handlers and search
     */
    function setupAlertProfileHandlers() {
        // Form submission
        const form = document.getElementById("alert-profile-form");
        if (form) {
            form.addEventListener("submit", window.AlertProfiles.saveAlertProfile);
        }
        
        // Action buttons
        const newBtn = document.getElementById("btn-new-alert-profile");
        if (newBtn) {
            newBtn.addEventListener("click", window.AlertProfiles.newAlertProfile);
        }
        
        const resetBtn = document.getElementById("btn-reset-alert-profile");
        if (resetBtn) {
            resetBtn.addEventListener("click", window.AlertProfiles.resetAlertProfileForm);
        }
        
        const deleteBtn = document.getElementById("btn-delete-alert-profile");
        if (deleteBtn) {
            deleteBtn.addEventListener("click", window.AlertProfiles.deleteAlertProfile);
        }
        
        const backtestBtn = document.getElementById("btn-backtest-alert-profile");
        if (backtestBtn) {
            backtestBtn.addEventListener("click", () => window.AlertProfiles.backtestAlertProfile());
        }
        
        // Search functionality with debounce
        const searchInput = document.getElementById("alert-profiles-search");
        if (searchInput) {
            let searchTimeout;
            searchInput.addEventListener("input", (e) => {
                clearTimeout(searchTimeout);
                searchTimeout = setTimeout(() => {
                    window.AlertProfiles.filterAlertProfiles(e.target.value);
                }, 300);
            });
        }
    }
    
    /**
     * Setup Interest Profile form handlers and search
     */
    function setupInterestProfileHandlers() {
        // Form submission
        const form = document.getElementById("interest-profile-form");
        if (form) {
            form.addEventListener("submit", window.InterestProfiles.saveInterestProfile);
        }
        
        // Action buttons
        const newBtn = document.getElementById("btn-new-interest-profile");
        if (newBtn) {
            newBtn.addEventListener("click", window.InterestProfiles.resetInterestProfileForm);
        }
        
        const resetBtn = document.getElementById("btn-reset-interest-profile");
        if (resetBtn) {
            resetBtn.addEventListener("click", window.InterestProfiles.resetInterestProfileForm);
        }
        
        const deleteBtn = document.getElementById("btn-delete-interest-profile");
        if (deleteBtn) {
            deleteBtn.addEventListener("click", window.InterestProfiles.deleteInterestProfile);
        }
        
        const backtestBtn = document.getElementById("btn-backtest-interest-profile");
        if (backtestBtn) {
            backtestBtn.addEventListener("click", () => window.InterestProfiles.backtestInterestProfile());
        }
        
        const runTestBtn = document.getElementById("btn-run-test");
        if (runTestBtn) {
            runTestBtn.addEventListener("click", window.InterestProfiles.runSimilarityTest);
        }
        
        // Search functionality with debounce
        const searchInput = document.getElementById("interest-profiles-search");
        if (searchInput) {
            let searchTimeout;
            searchInput.addEventListener("input", (e) => {
                clearTimeout(searchTimeout);
                searchTimeout = setTimeout(() => {
                    window.InterestProfiles.filterInterestProfiles(e.target.value);
                }, 300);
            });
        }
    }
    
    /**
     * Setup Alert Profiles bulk actions dropdown
     */
    function setupBulkActionsDropdown() {
        const bulkActionsBtn = document.getElementById("btn-alert-bulk-actions");
        if (!bulkActionsBtn) return;
        
        // Create dropdown if it doesn't exist
        let dropdown = document.querySelector(".alert-bulk-dropdown");
        if (!dropdown) {
            dropdown = document.createElement("div");
            dropdown.className = "alert-bulk-dropdown";
            dropdown.innerHTML = `
                <button class="alert-bulk-dropdown-item" data-action="enable-all">Enable All</button>
                <button class="alert-bulk-dropdown-item" data-action="disable-all">Disable All</button>
                <button class="alert-bulk-dropdown-item" data-action="import">Import</button>
                <button class="alert-bulk-dropdown-item" data-action="export-all">Export All</button>
            `;
            bulkActionsBtn.parentElement.style.position = "relative";
            bulkActionsBtn.parentElement.appendChild(dropdown);
            
            // Add click handlers to dropdown items
            dropdown.querySelectorAll(".alert-bulk-dropdown-item").forEach(item => {
                item.addEventListener("click", async (e) => {
                    e.stopPropagation();
                    const action = item.dataset.action;
                    dropdown.classList.remove("show");
                    
                    if (action === "enable-all") {
                        await window.AlertProfiles.bulkToggleAlertProfiles(true);
                    } else if (action === "disable-all") {
                        await window.AlertProfiles.bulkToggleAlertProfiles(false);
                    } else if (action === "import") {
                        window.AlertProfiles.importAlertProfile();
                    } else if (action === "export-all") {
                        window.AlertProfiles.exportAllAlertProfiles();
                    }
                });
            });
        }
        
        // Button click handler - toggle dropdown
        bulkActionsBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            dropdown.classList.toggle("show");
        });
        
        // Document-level click handler to close dropdown when clicking outside
        document.addEventListener("click", () => {
            const dropdown = document.querySelector(".alert-bulk-dropdown");
            if (dropdown && dropdown.classList.contains("show")) {
                dropdown.classList.remove("show");
            }
        });
    }
    
    /**
     * Setup Interest Profiles bulk actions dropdown
     */
    function setupInterestBulkActionsDropdown() {
        const bulkActionsBtn = document.getElementById("btn-interest-bulk-actions");
        if (!bulkActionsBtn) return;
        
        // Create dropdown if it doesn't exist
        let dropdown = document.querySelector(".interest-bulk-dropdown");
        if (!dropdown) {
            dropdown = document.createElement("div");
            dropdown.className = "alert-bulk-dropdown interest-bulk-dropdown";
            dropdown.innerHTML = `
                <button class="alert-bulk-dropdown-item" data-action="enable-all">Enable All</button>
                <button class="alert-bulk-dropdown-item" data-action="disable-all">Disable All</button>
                <button class="alert-bulk-dropdown-item" data-action="import">Import</button>
                <button class="alert-bulk-dropdown-item" data-action="export-all">Export All</button>
            `;
            bulkActionsBtn.parentElement.style.position = "relative";
            bulkActionsBtn.parentElement.appendChild(dropdown);
            
            // Add click handlers to dropdown items
            dropdown.querySelectorAll(".alert-bulk-dropdown-item").forEach(item => {
                item.addEventListener("click", async (e) => {
                    e.stopPropagation();
                    const action = item.dataset.action;
                    dropdown.classList.remove("show");
                    
                    if (action === "enable-all") {
                        await window.InterestProfiles.bulkToggleInterestProfiles(true);
                    } else if (action === "disable-all") {
                        await window.InterestProfiles.bulkToggleInterestProfiles(false);
                    } else if (action === "import") {
                        window.InterestProfiles.importInterestProfile();
                    } else if (action === "export-all") {
                        window.InterestProfiles.exportAllInterestProfiles();
                    }
                });
            });
        }
        
        // Button click handler - toggle dropdown
        bulkActionsBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            dropdown.classList.toggle("show");
        });
        
        // Document-level click handler to close dropdown when clicking outside
        document.addEventListener("click", () => {
            const dropdown = document.querySelector(".interest-bulk-dropdown");
            if (dropdown && dropdown.classList.contains("show")) {
                dropdown.classList.remove("show");
            }
        });
    }
    
    /**
     * Setup advanced section collapse icon rotation
     */
    function setupAdvancedSectionToggles() {
        const advancedCollapse = document.getElementById("advanced-config");
        const collapseIcon = document.getElementById("collapse-icon");
        
        if (advancedCollapse && collapseIcon) {
            // Set initial state
            if (advancedCollapse.classList.contains("show")) {
                collapseIcon.style.transform = "rotate(90deg)";
            }
            collapseIcon.style.transition = "transform 0.2s ease";
            
            // Listen for collapse events
            advancedCollapse.addEventListener("show.bs.collapse", () => {
                collapseIcon.style.transform = "rotate(90deg)";
            });
            
            advancedCollapse.addEventListener("hide.bs.collapse", () => {
                collapseIcon.style.transform = "";
            });
        }
    }
    
    /**
     * Setup digest schedule collapse icon rotation for all schedules
     */
    function setupDigestScheduleToggles() {
        document.querySelectorAll('[data-bs-toggle="collapse"][data-bs-target^="#alert-schedule-"], [data-bs-toggle="collapse"][data-bs-target^="#interest-schedule-"]').forEach(button => {
            const chevron = button.querySelector('.schedule-chevron');
            const targetId = button.getAttribute('data-bs-target');
            const targetElement = document.querySelector(targetId);
            
            if (chevron && targetElement) {
                // Set initial state
                if (targetElement.classList.contains('show')) {
                    chevron.style.transform = 'rotate(90deg)';
                }
                chevron.style.transition = 'transform 0.2s ease';
                
                // Listen for collapse events
                targetElement.addEventListener('show.bs.collapse', () => {
                    chevron.style.transform = 'rotate(90deg)';
                });
                
                targetElement.addEventListener('hide.bs.collapse', () => {
                    chevron.style.transform = '';
                });
            }
        });
    }
    
    /**
     * Setup export and import handlers
     */
    function setupExportImportHandlers() {
        // Export handler (Interest Profiles)
        const exportBtn = document.getElementById("btn-export-profiles");
        if (exportBtn) {
            exportBtn.addEventListener("click", async () => {
                try {
                    const response = await fetch('/api/profiles/export'); // TODO: Update with actual endpoint
                    if (!response.ok) {
                        throw new Error("Export failed");
                    }
                    
                    const blob = await response.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `interests_${new Date().toISOString().replace(/[:.]/g, '-')}.yml`;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    document.body.removeChild(a);
                    
                    window.SharedUtils.showToast("Interests exported", "success");
                } catch (error) {
                    console.error("Export failed:", error);
                    window.SharedUtils.showToast("Export failed. Check console for details.", "error");
                }
            });
        }
        
        // Import handler placeholder
        const importBtn = document.getElementById("btn-import-profiles");
        if (importBtn) {
            importBtn.addEventListener("click", () => {
                window.SharedUtils.showToast("Import not yet implemented", "info");
            });
        }
    }
    
    // ============= INITIALIZE ON DOM READY =============
    
    document.addEventListener("DOMContentLoaded", init);
    
})();
