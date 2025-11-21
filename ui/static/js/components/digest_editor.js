/**
 * Reusable digest schedule editor component
 * Can be embedded in any profile editor (global, alert, interest)
 */

class DigestScheduleEditor {
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        if (!this.container) {
            throw new Error(`DigestEditor: container '${containerId}' not found in DOM`);
        }

        this.options = {
            maxSchedules: 3,
            allowModeSelection: true,
            allowChannelTarget: true,
            onSave: null,
            onCancel: null,
            ...options
        };

        this.schedules = [];
        this.mode = "dm";
        this.targetChannel = "";
        this.topN = 10;
        this.minScore = 5.0;

        this.render();
    }

    /**
     * Load existing digest configuration
     */
    loadConfig(digestConfig) {
        if (!digestConfig) {
            this.schedules = [];
            this.mode = "dm";
            this.targetChannel = "";
            this.topN = 10;
            this.minScore = 5.0;
            this.render();
            return;
        }

        this.schedules = digestConfig.schedules || [];
        this.mode = digestConfig.mode || "dm";
        this.targetChannel = digestConfig.target_channel || "";
        this.topN = digestConfig.top_n || 10;
        this.minScore = digestConfig.min_score || 5.0;

        this.render();
    }

    /**
     * Get current configuration as object
     */
    getConfig() {
        // Update current values from DOM before returning
        this.syncFromDOM();

        return {
            schedules: this.schedules,
            mode: this.mode,
            target_channel: this.targetChannel,
            top_n: this.topN,
            min_score: this.minScore
        };
    }

    /**
     * Sync current form values from DOM to internal state
     */
    syncFromDOM() {
        const modeEl = document.getElementById("digest-mode");
        if (modeEl) this.mode = modeEl.value;

        const targetEl = document.getElementById("digest-target-channel");
        if (targetEl) this.targetChannel = targetEl.value.trim();

        const topNEl = document.getElementById("digest-top-n");
        if (topNEl) this.topN = parseInt(topNEl.value) || 10;

        const minScoreEl = document.getElementById("digest-min-score");
        if (minScoreEl) this.minScore = parseFloat(minScoreEl.value) || 5.0;

        // Sync schedule values
        this.schedules.forEach((sched, idx) => {
            const typeEl = document.querySelector(`.schedule-type[data-idx="${idx}"]`);
            if (typeEl) sched.schedule = typeEl.value;

            const enabledEl = document.querySelector(`.schedule-enabled[data-idx="${idx}"]`);
            if (enabledEl) sched.enabled = enabledEl.checked;

            if (sched.schedule === "daily") {
                const hourEl = document.querySelector(`.schedule-daily-hour[data-idx="${idx}"]`);
                if (hourEl) sched.daily_hour = parseInt(hourEl.value) || 8;
            } else if (sched.schedule === "weekly") {
                const dayEl = document.querySelector(`.schedule-weekly-day[data-idx="${idx}"]`);
                const hourEl = document.querySelector(`.schedule-weekly-hour[data-idx="${idx}"]`);
                if (dayEl) sched.weekly_day = parseInt(dayEl.value) || 0;
                if (hourEl) sched.weekly_hour = parseInt(hourEl.value) || 8;
            }

            // Sync overrides
            const minScoreOverride = document.querySelector(`.schedule-min-score[data-idx="${idx}"]`);
            const topNOverride = document.querySelector(`.schedule-top-n[data-idx="${idx}"]`);
            
            if (minScoreOverride && minScoreOverride.value) {
                sched.min_score = parseFloat(minScoreOverride.value);
            } else {
                sched.min_score = null;
            }

            if (topNOverride && topNOverride.value) {
                sched.top_n = parseInt(topNOverride.value);
            } else {
                sched.top_n = null;
            }
        });
    }

    /**
     * Render the editor UI
     */
    render() {
        this.container.innerHTML = `
            <div class="digest-editor">
                <!-- Schedules Section -->
                <div class="mb-4">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h5 class="mb-0">Digest Schedules</h5>
                        <button type="button" class="btn btn-sm btn-outline-primary"
                                id="btn-add-schedule"
                                ${this.schedules.length >= this.options.maxSchedules ? 'disabled' : ''}>
                            <i class="bi bi-plus-circle me-1"></i>
                            Add Schedule (${this.schedules.length}/${this.options.maxSchedules})
                        </button>
                    </div>
                    
                    <div class="alert alert-info small mb-3">
                        <i class="bi bi-info-circle me-1"></i>
                        Configure up to ${this.options.maxSchedules} digest schedules. Messages matching this profile will be batched and sent according to these schedules.
                    </div>
                    
                    <div id="schedules-list">
                        ${this.renderSchedulesList()}
                    </div>
                </div>
                
                <!-- Global Settings -->
                <div class="mb-4">
                    <h5 class="mb-3">Delivery Settings</h5>
                    
                    <div class="row g-3">
                        <div class="col-md-6">
                            <label class="form-label">Delivery Mode</label>
                            <select class="form-select" id="digest-mode">
                                <option value="dm" ${this.mode === "dm" ? "selected" : ""}>Saved Messages (DM)</option>
                                <option value="channel" ${this.mode === "channel" ? "selected" : ""}>Specific Channel</option>
                                <option value="both" ${this.mode === "both" ? "selected" : ""}>Both (DM + Channel)</option>
                            </select>
                            <small class="form-text text-muted">
                                Where to send digest notifications
                            </small>
                        </div>
                        
                        <div class="col-md-6">
                            <label class="form-label">Target Channel</label>
                            <input type="text" class="form-control" id="digest-target-channel"
                                   placeholder="@my_channel or -100123456789"
                                   value="${this.targetChannel || ""}"
                                   ${this.mode !== "channel" && this.mode !== "both" ? "disabled" : ""}>
                            <small class="form-text text-muted">
                                Required if mode is 'channel' or 'both'
                            </small>
                        </div>
                    </div>
                </div>
                
                <div class="mb-4">
                    <h5 class="mb-3">Message Filtering</h5>
                    
                    <div class="row g-3">
                        <div class="col-md-6">
                            <label class="form-label">Top N Messages</label>
                            <input type="number" class="form-control" id="digest-top-n"
                                   min="1" max="100" step="1" value="${this.topN}">
                            <small class="form-text text-muted">
                                Maximum messages per digest (1-100)
                            </small>
                        </div>
                        
                        <div class="col-md-6">
                            <label class="form-label">Minimum Score</label>
                            <input type="number" class="form-control" id="digest-min-score"
                                   min="0" max="10" step="0.1" value="${this.minScore}">
                            <small class="form-text text-muted">
                                Only include messages with score ≥ this value (0-10)
                            </small>
                        </div>
                    </div>
                </div>
                
                <!-- Precedence Info -->
                <div class="alert alert-secondary small">
                    <strong>Precedence:</strong> Channel-level > User-level > Profile-level > Global default.
                    Per-schedule overrides (min_score, top_n) take precedence over profile-level values.
                </div>
                
                <!-- Action Buttons -->
                <div class="d-flex justify-content-end gap-2">
                    <button type="button" class="btn btn-secondary" id="btn-cancel-digest">Cancel</button>
                    <button type="button" class="btn btn-primary" id="btn-save-digest">Save Configuration</button>
                </div>
            </div>
        `;

        this.attachEventListeners();
    }

    /**
     * Render the list of configured schedules
     */
    renderSchedulesList() {
        if (this.schedules.length === 0) {
            return `
                <div class="text-center text-muted p-4 border rounded">
                    <i class="bi bi-calendar-x" style="font-size: 2rem;"></i>
                    <p class="mb-0 mt-2">No schedules configured. Click "Add Schedule" to create one.</p>
                </div>
            `;
        }

        return this.schedules.map((sched, idx) => `
            <div class="card mb-3" data-schedule-idx="${idx}">
                <div class="card-body">
                    <div class="row g-3">
                        <div class="col-md-3">
                            <label class="form-label">Schedule Type</label>
                            <select class="form-select schedule-type" data-idx="${idx}">
                                <option value="hourly" ${sched.schedule === "hourly" ? "selected" : ""}>Hourly</option>
                                <option value="every_4h" ${sched.schedule === "every_4h" ? "selected" : ""}>Every 4 Hours</option>
                                <option value="every_6h" ${sched.schedule === "every_6h" ? "selected" : ""}>Every 6 Hours</option>
                                <option value="every_12h" ${sched.schedule === "every_12h" ? "selected" : ""}>Every 12 Hours</option>
                                <option value="daily" ${sched.schedule === "daily" ? "selected" : ""}>Daily</option>
                                <option value="weekly" ${sched.schedule === "weekly" ? "selected" : ""}>Weekly</option>
                            </select>
                        </div>
                        
                        ${this.renderScheduleTypeFields(sched, idx)}
                        
                        <div class="col-md-2 d-flex align-items-end">
                            <button type="button" class="btn btn-outline-danger w-100 btn-remove-schedule" data-idx="${idx}">
                                <i class="bi bi-trash"></i> Remove
                            </button>
                        </div>
                    </div>
                    
                    <!-- Optional per-schedule overrides -->
                    <div class="mt-3 pt-3 border-top">
                        <div class="form-check mb-2">
                            <input class="form-check-input schedule-override-toggle" type="checkbox" id="override-${idx}" data-idx="${idx}"
                                   ${sched.min_score !== null || sched.top_n !== null ? "checked" : ""}>
                            <label class="form-check-label" for="override-${idx}">
                                Override min_score/top_n for this schedule
                            </label>
                        </div>
                        
                        <div class="row g-2 override-fields" id="override-fields-${idx}" style="display: ${sched.min_score !== null || sched.top_n !== null ? "flex" : "none"}">
                            <div class="col-md-6">
                                <input type="number" class="form-control form-control-sm schedule-min-score" 
                                       data-idx="${idx}" placeholder="Min Score (optional)"
                                       min="0" max="10" step="0.1" value="${sched.min_score !== null ? sched.min_score : ""}">
                            </div>
                            <div class="col-md-6">
                                <input type="number" class="form-control form-control-sm schedule-top-n"
                                       data-idx="${idx}" placeholder="Top N (optional)"
                                       min="1" max="100" step="1" value="${sched.top_n !== null ? sched.top_n : ""}">
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `).join("");
    }

    /**
     * Render schedule-type-specific fields (daily_hour, weekly_day, etc.)
     */
    renderScheduleTypeFields(sched, idx) {
        if (sched.schedule === "daily") {
            return `
                <div class="col-md-3">
                    <label class="form-label">Hour (UTC)</label>
                    <input type="number" class="form-control schedule-daily-hour" data-idx="${idx}"
                           min="0" max="23" step="1" value="${sched.daily_hour !== undefined ? sched.daily_hour : 8}">
                </div>
                <div class="col-md-4">
                    <label class="form-label">Status</label>
                    <div class="form-check form-switch mt-2">
                        <input class="form-check-input schedule-enabled" type="checkbox" data-idx="${idx}"
                               ${sched.enabled !== false ? "checked" : ""}>
                        <label class="form-check-label">Enabled</label>
                    </div>
                </div>
            `;
        } else if (sched.schedule === "weekly") {
            const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
            return `
                <div class="col-md-3">
                    <label class="form-label">Day of Week</label>
                    <select class="form-select schedule-weekly-day" data-idx="${idx}">
                        ${days.map((day, i) => `
                            <option value="${i}" ${(sched.weekly_day !== undefined ? sched.weekly_day : 0) === i ? "selected" : ""}>${day}</option>
                        `).join("")}
                    </select>
                </div>
                <div class="col-md-2">
                    <label class="form-label">Hour (UTC)</label>
                    <input type="number" class="form-control schedule-weekly-hour" data-idx="${idx}"
                           min="0" max="23" step="1" value="${sched.weekly_hour !== undefined ? sched.weekly_hour : 8}">
                </div>
                <div class="col-md-2">
                    <label class="form-label">Status</label>
                    <div class="form-check form-switch mt-2">
                        <input class="form-check-input schedule-enabled" type="checkbox" data-idx="${idx}"
                               ${sched.enabled !== false ? "checked" : ""}>
                        <label class="form-check-label">Enabled</label>
                    </div>
                </div>
            `;
        } else {
            // hourly, every_4h, every_6h, every_12h
            return `
                <div class="col-md-7">
                    <label class="form-label">Status</label>
                    <div class="form-check form-switch mt-2">
                        <input class="form-check-input schedule-enabled" type="checkbox" data-idx="${idx}"
                               ${sched.enabled !== false ? "checked" : ""}>
                        <label class="form-check-label">Enabled</label>
                    </div>
                </div>
            `;
        }
    }

    /**
     * Attach event listeners
     */
    attachEventListeners() {
        // Add schedule button
        const addBtn = document.getElementById("btn-add-schedule");
        if (addBtn) {
            addBtn.addEventListener("click", () => {
                if (this.schedules.length < this.options.maxSchedules) {
                    this.schedules.push({
                        schedule: "hourly",
                        enabled: true,
                        daily_hour: 8,
                        weekly_day: 0,
                        weekly_hour: 8,
                        min_score: null,
                        top_n: null
                    });
                    this.render();
                }
            });
        }

        // Remove schedule buttons
        document.querySelectorAll(".btn-remove-schedule").forEach(btn => {
            btn.addEventListener("click", (e) => {
                const idx = parseInt(e.currentTarget.dataset.idx);
                this.schedules.splice(idx, 1);
                this.render();
            });
        });

        // Schedule type changes
        document.querySelectorAll(".schedule-type").forEach(select => {
            select.addEventListener("change", (e) => {
                const idx = parseInt(e.currentTarget.dataset.idx);
                this.schedules[idx].schedule = e.currentTarget.value;
                this.render();
            });
        });

        // Mode changes
        const modeEl = document.getElementById("digest-mode");
        if (modeEl) {
            modeEl.addEventListener("change", (e) => {
                this.mode = e.target.value;
                this.render();
            });
        }

        // Override toggle
        document.querySelectorAll(".schedule-override-toggle").forEach(checkbox => {
            checkbox.addEventListener("change", (e) => {
                const idx = parseInt(e.currentTarget.dataset.idx);
                const fieldsEl = document.getElementById(`override-fields-${idx}`);
                if (fieldsEl) {
                    fieldsEl.style.display = e.currentTarget.checked ? "flex" : "none";
                }
            });
        });

        // Save button
        const saveBtn = document.getElementById("btn-save-digest");
        if (saveBtn) {
            saveBtn.addEventListener("click", () => {
                if (this.validate()) {
                    const config = this.getConfig();
                    if (this.options.onSave) {
                        this.options.onSave(config);
                    }
                }
            });
        }

        // Cancel button
        const cancelBtn = document.getElementById("btn-cancel-digest");
        if (cancelBtn) {
            cancelBtn.addEventListener("click", () => {
                if (this.options.onCancel) {
                    this.options.onCancel();
                }
            });
        }
    }

    /**
     * Validate configuration before saving
     */
    validate() {
        this.syncFromDOM();
        const errors = [];

        // Validate mode + target_channel
        if ((this.mode === "channel" || this.mode === "both") && !this.targetChannel) {
            errors.push('Target channel is required when mode is "channel" or "both"');
        }

        // Validate global min_score range
        if (this.minScore < 0 || this.minScore > 10) {
            errors.push("Global minimum score must be between 0 and 10");
        }

        // Validate global top_n range
        if (this.topN < 1 || this.topN > 100) {
            errors.push("Global Top N must be between 1 and 100");
        }

        // Validate per-schedule overrides
        this.schedules.forEach((sched, idx) => {
            const scheduleLabel = `Schedule ${idx + 1} (${sched.schedule || 'unset'})`;
            
            // Validate min_score override if set
            if (sched.min_score !== null && sched.min_score !== undefined) {
                if (sched.min_score < 0 || sched.min_score > 10) {
                    errors.push(`${scheduleLabel}: minimum score override must be between 0 and 10 (got ${sched.min_score})`);
                }
            }
            
            // Validate top_n override if set
            if (sched.top_n !== null && sched.top_n !== undefined) {
                if (sched.top_n < 1 || sched.top_n > 100) {
                    errors.push(`${scheduleLabel}: top N override must be between 1 and 100 (got ${sched.top_n})`);
                }
            }
        });

        // Show errors
        if (errors.length > 0) {
            alert("Validation errors:\n\n" + errors.join("\n"));
            return false;
        }

        return true;
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DigestScheduleEditor;
}
