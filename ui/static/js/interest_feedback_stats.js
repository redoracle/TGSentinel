/**
 * Interest Profile Feedback Statistics Module
 * Loads and displays feedback learning stats for interest/semantic profiles
 * including borderline FPs, severe FPs, strong TPs, threshold drift, and pending samples
 */

const InterestFeedbackStats = (function() {
    'use strict';
    
    const THRESHOLDS = {
        BORDERLINE_WINDOW: 0.05,   // ±0.05 from threshold
        SEVERE_FP_THRESHOLD: 0.15, // > 0.15 above threshold
        MAX_THRESHOLD_DRIFT: 0.25, // ±0.25 maximum drift
        PENDING_SAMPLE_MIN: 2      // ≥2 instances for sample augmentation
    };
    
    /**
     * Load feedback stats for a specific interest profile
     * @param {number|string} profileId - The interest profile ID
     * @returns {Promise<Object>} The feedback stats data
     */
    async function loadStats(profileId) {
        if (!profileId) {
            console.warn('[InterestFeedbackStats] No profile ID provided');
            return null;
        }
        
        // Create AbortController for timeout handling
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000); // 5 second timeout
        
        try {
            const response = await fetch(
                `/api/profiles/interest/${encodeURIComponent(profileId)}/feedback-stats`,
                { signal: controller.signal }
            );
            
            // Clear timeout on successful response
            clearTimeout(timeoutId);
            
            if (!response.ok) {
                if (response.status === 404) {
                    console.info('[InterestFeedbackStats] No feedback stats available yet');
                    return null;
                }
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const result = await response.json();

            if (result.status !== 'ok') {
                throw new Error(result.error || 'Failed to load feedback stats');
            }

            const payload =
                (result.data && result.data.stats && { stats: result.data.stats, history: result.data.history }) ||
                (result.stats && { stats: result.stats, history: result.history }) ||
                null;

            return payload;
        } catch (error) {
            // Clear timeout in case of error
            clearTimeout(timeoutId);
            
            // Handle abort (timeout) separately from other errors
            if (error.name === 'AbortError') {
                console.warn('[InterestFeedbackStats] Request timed out after 5 seconds');
                throw new Error('Request timed out. Please try again.');
            }
            
            console.error('[InterestFeedbackStats] Failed to load stats:', error);
            throw error;
        }
    }
    
    /**
     * Update the UI with loaded feedback stats
     * @param {Object} data - The feedback statistics data from API
     */
    function updateUI(data) {
        console.log('[InterestFeedbackStats] updateUI called with data:', data);
        
        if (!data || !data.stats) {
            console.warn('[InterestFeedbackStats] No data or stats, hiding section');
            hideStatsSection();
            return;
        }
        
        const stats = data.stats;
        console.log('[InterestFeedbackStats] Stats extracted:', stats);
        
        // Show the stats section with ARIA attributes
        const section = document.getElementById('interest-feedback-stats-section');
        if (section) {
            console.log('[InterestFeedbackStats] Showing section...');
            section.style.display = 'block';
            section.classList.remove('feedback-stats-hidden', 'feedback-stats-loading');
            section.classList.add('feedback-stats-visible');
            section.setAttribute('aria-busy', 'false');
            
            // Hide loading overlay
            const loadingOverlay = document.getElementById('interest-feedback-stats-loading');
            if (loadingOverlay) {
                loadingOverlay.style.display = 'none';
            }
            console.log('[InterestFeedbackStats] Section should now be visible');
        } else {
            console.error('[InterestFeedbackStats] Section element not found!');
        }
        
        // Update false positive stats (note: API uses singular form, UI uses plural IDs)
        updateElement('interest-borderline-fps', stats.borderline_fp || 0);
        updateElement('interest-severe-fps', stats.severe_fp || 0);
        updateElement('interest-strong-tps', stats.strong_tp || 0);
        
        // Update threshold drift information
        const thresholdDrift = stats.cumulative_threshold_delta || 0;
        const hasThresholdInfo =
            typeof stats.current_threshold === 'number' &&
            typeof stats.original_threshold === 'number';

        if (
            thresholdDrift !== undefined &&
            thresholdDrift !== null &&
            Math.abs(thresholdDrift) > 0.001 &&
            hasThresholdInfo
        ) {
            updateThresholdDrift(
                thresholdDrift,
                stats.current_threshold,
                stats.original_threshold
            );
        }
        
        // Update pending samples workflow (if available from separate API call)
        if (stats.pending_samples !== undefined) {
            updatePendingSamples(stats.pending_samples);
        }
        
        // Update last updated timestamp
        const lastUpdated = data.last_updated || stats.last_updated;
        updateElement('interest-feedback-last-updated', formatTimestamp(lastUpdated));
    }
    
    /**
     * Update threshold drift display
     */
    function updateThresholdDrift(drift, current, original) {
        const driftSection = document.getElementById('interest-threshold-adjustment-info');
        if (!driftSection) return;
        
        // Show section if drift is non-zero, with ARIA update
        if (Math.abs(drift) > 0.001) {
            driftSection.style.display = 'block';
            driftSection.setAttribute('aria-hidden', 'false');
            
            updateElement('interest-threshold-drift-value', 
                drift > 0 ? `+${drift.toFixed(3)}` : drift.toFixed(3));
            updateElement('interest-current-threshold', current.toFixed(3));
            updateElement('interest-original-threshold', original.toFixed(3));
            
            // Update progress bar (percentage of max drift)
            const driftBar = document.getElementById('interest-threshold-drift-bar');
            if (driftBar) {
                const percentage = Math.abs(drift) / THRESHOLDS.MAX_THRESHOLD_DRIFT * 100;
                const clamped = Math.max(0, Math.min(100, percentage));
                driftBar.style.width = `${clamped}%`;
                // Set ARIA attributes: valuenow uses 0-100 scale, valuetext shows actual drift
                driftBar.setAttribute('aria-valuenow', Math.round(clamped).toString());
                const driftText = drift > 0 ? `+${drift.toFixed(3)}` : drift.toFixed(3);
                driftBar.setAttribute('aria-valuetext', `${driftText} drift from original`);
                
                // Color based on drift magnitude (use clamped value)
                if (clamped > 80) {
                    driftBar.className = 'progress-bar bg-danger';
                } else if (clamped > 50) {
                    driftBar.className = 'progress-bar bg-warning';
                } else {
                    driftBar.className = 'progress-bar bg-info';
                }
            }
        } else {
            driftSection.style.display = 'none';
            driftSection.setAttribute('aria-hidden', 'true');
        }
    }
    
    /**
     * Update pending samples workflow UI
     */
    function updatePendingSamples(pendingSamples) {
        const pendingSection = document.getElementById('interest-pending-samples-section');
        if (!pendingSection) return;
        
        const count = pendingSamples?.length || 0;
        
        if (count > 0) {
            pendingSection.style.display = 'block';
            pendingSection.setAttribute('aria-hidden', 'false');
            
            updateElement('interest-pending-samples-count', `${count} pending`);
            updateElement('interest-pending-count-inline', count);
            
            // Enable/disable action buttons
            const commitBtn = document.getElementById('btn-commit-pending-samples');
            const rollbackBtn = document.getElementById('btn-rollback-pending-samples');
            
            if (commitBtn) commitBtn.disabled = false;
            if (rollbackBtn) rollbackBtn.disabled = false;
        } else {
            pendingSection.style.display = 'none';
            pendingSection.setAttribute('aria-hidden', 'true');
            
            // Disable action buttons when hidden
            const commitBtn = document.getElementById('btn-commit-pending-samples');
            const rollbackBtn = document.getElementById('btn-rollback-pending-samples');
            
            if (commitBtn) commitBtn.disabled = true;
            if (rollbackBtn) rollbackBtn.disabled = true;
        }
    }
    
    /**
     * Hide the stats section if no data is available
     * Fail-safe: Only hide if JS successfully loaded, otherwise leave visible
     */
    function hideStatsSection() {
        const section = document.getElementById('interest-feedback-stats-section');
        if (section && document.documentElement.classList.contains('js-enabled')) {
            section.style.display = 'none';
            section.classList.add('feedback-stats-hidden');
            section.classList.remove('feedback-stats-visible');
            section.setAttribute('aria-hidden', 'true');
        }
    }
    
    /**
     * Update a DOM element's text content
     */
    function updateElement(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = value;
        }
    }
    
    /**
     * Format timestamp for display
     */
    function formatTimestamp(timestamp) {
        if (!timestamp) return 'Never';
        
        try {
            const date = new Date(timestamp);
            const now = new Date();
            const diffMinutes = Math.floor((now - date) / 60000);
            
            if (diffMinutes < 1) return 'Just now';
            if (diffMinutes < 60) return `${diffMinutes}m ago`;
            if (diffMinutes < 1440) return `${Math.floor(diffMinutes / 60)}h ago`;
            
            return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
        } catch (error) {
            console.error('[InterestFeedbackStats] Error formatting timestamp:', error);
            return 'Invalid date';
        }
    }
    
    /**
     * Initialize the feedback stats module
     */
    function init() {
        console.log('[InterestFeedbackStats] Initializing module...');
        
        // Add js-enabled class for progressive enhancement
        document.documentElement.classList.add('js-enabled');
        
        const section = document.getElementById('interest-feedback-stats-section');
        if (section) {
            console.log('[InterestFeedbackStats] Section found, setting up...');
            section.setAttribute('aria-busy', 'true');
            section.classList.add('feedback-stats-loading');
            
            // Show loading overlay
            const loadingOverlay = document.getElementById('interest-feedback-stats-loading');
            if (loadingOverlay) {
                loadingOverlay.style.display = 'block';
            }
        } else {
            console.warn('[InterestFeedbackStats] Section #interest-feedback-stats-section not found!');
        }
        
        // Set up refresh button handler
        const refreshBtn = document.getElementById('btn-refresh-interest-feedback-stats');
        if (refreshBtn) {
            console.log('[InterestFeedbackStats] Refresh button found, setting up handler');
            refreshBtn.addEventListener('click', async () => {
                const profileId = document.getElementById('interest-profile-id')?.value;
                if (profileId) {
                    try {
                        refreshBtn.disabled = true;
                        // Add loading indicator
                        const originalText = refreshBtn.textContent;
                        refreshBtn.textContent = 'Refreshing...';
                        
                        const stats = await loadStats(profileId);
                        updateUI(stats);
                    } catch (error) {
                        console.error('[InterestFeedbackStats] Refresh failed:', error);
                        // Show user-facing error message
                        alert('Failed to refresh statistics. Please try again.');
                    } finally {
                        refreshBtn.textContent = originalText;
                        refreshBtn.disabled = false;
                    }
                }
            });
        }
        
        // Set up pending samples review button (Phase 2 feature)
        const reviewBtn = document.getElementById('btn-review-pending-samples');
        if (reviewBtn) {
            reviewBtn.addEventListener('click', () => {
                // TODO: Open modal to review pending samples
                console.info('[InterestFeedbackStats] Review pending samples - not yet implemented');
            });
        }
        
        // Set up commit/rollback buttons (Phase 2 feature)
        const commitBtn = document.getElementById('btn-commit-pending-samples');
        const rollbackBtn = document.getElementById('btn-rollback-pending-samples');
        
        if (commitBtn) {
            commitBtn.addEventListener('click', async () => {
                // TODO: Implement commit pending samples
                console.info('[InterestFeedbackStats] Commit pending samples - not yet implemented');
            });
        }
        
        if (rollbackBtn) {
            rollbackBtn.addEventListener('click', async () => {
                // TODO: Implement rollback pending samples
                console.info('[InterestFeedbackStats] Rollback pending samples - not yet implemented');
            });
        }
        
        // Set up adjustment history button
        const adjustmentHistoryBtn = document.getElementById('btn-view-interest-adjustment-history');
        if (adjustmentHistoryBtn) {
            adjustmentHistoryBtn.addEventListener('click', (event) => {
                event.preventDefault();
                const profileId = document.getElementById('interest-profile-id')?.value;
                if (profileId) {
                    window.open('/feedback-learning-monitor?profile_id=' + encodeURIComponent(profileId), '_blank');
                } else {
                    alert('Please save the profile first to view adjustment history.');
                }
            });
        }
    }
    
    /**
     * Load and display stats for a specific profile
     * @param {number|string} profileId - The interest profile ID
     */
    async function loadStatsForProfile(profileId) {
        if (!profileId) {
            console.log('[InterestFeedbackStats] No profile ID, hiding stats section');
            hideStatsSection();
            return;
        }
        
        console.log('[InterestFeedbackStats] Loading stats for profile:', profileId);
        
        try {
            const stats = await loadStats(profileId);
            console.log('[InterestFeedbackStats] Stats loaded:', stats);
            if (stats) {
                updateUI(stats);
            } else {
                console.warn('[InterestFeedbackStats] No stats returned, hiding section');
                hideStatsSection();
            }
        } catch (error) {
            console.error('[InterestFeedbackStats] Failed to load stats for profile:', error);
            // Show section with zero stats instead of hiding it
            updateUI({ stats: { profile_id: profileId, borderline_fp: 0, severe_fp: 0, strong_tp: 0, cumulative_threshold_delta: 0.0 } });
        }
    }
    
    // Public API
    return {
        loadStats,
        updateUI,
        hideStatsSection,
        loadStatsForProfile,
        init,
        THRESHOLDS
    };
})();

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', InterestFeedbackStats.init);
} else {
    InterestFeedbackStats.init();
}

// Export for use in other modules
window.InterestFeedbackStats = InterestFeedbackStats;
