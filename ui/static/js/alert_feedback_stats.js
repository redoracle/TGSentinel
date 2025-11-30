/**
 * Alert Profile Feedback Statistics Module
 * Loads and displays feedback learning stats for alert/keyword profiles
 * including positive/negative counts, negative rate, min_score drift, and adjustment recommendations
 */

const AlertFeedbackStats = (function() {
    'use strict';
    
    const THRESHOLDS = {
        NEGATIVE_RATE_WARNING: 0.30, // 30% negative rate triggers warning
        MAX_MIN_SCORE_DRIFT: 0.50,   // +0.5 maximum drift for min_score
        HIGH_NEGATIVE_RATE: 0.40     // 40% triggers strong recommendation
    };
    
    /**
     * Load feedback stats for a specific alert profile
     * @param {number|string} profileId - The alert profile ID
     * @returns {Promise<Object>} The feedback stats data
     */
    async function loadStats(profileId) {
        if (!profileId) {
            console.warn('[AlertFeedbackStats] No profile ID provided');
            return null;
        }
        
        try {
            const response = await fetch(`/api/profiles/alert/${profileId}/feedback-stats`);
            
            if (!response.ok) {
                if (response.status === 404) {
                    console.info('[AlertFeedbackStats] No feedback stats available yet');
                    return null;
                }
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const result = await response.json();
            
            if (result.status !== 'ok') {
                throw new Error(result.error || 'Failed to load feedback stats');
            }
            
            const payload = result.data || result;
            return payload;
        } catch (error) {
            console.error('[AlertFeedbackStats] Failed to load stats:', error);
            throw error;
        }
    }
    
    /**
     * Update the UI with loaded feedback stats
     * @param {Object} data - The feedback statistics data object from API
     */
    function updateUI(data) {
        if (!data) {
            hideStatsSection();
            return;
        }
        
        // Extract stats from aggregator_stats (in-memory) or db_stats (persistent)
        // Prefer aggregator_stats if available, fall back to db_stats
        const stats = data.aggregator_stats || data.db_stats;
        
        if (!stats) {
            if (data.message) {
                showFeedbackPlaceholder(data.message);
                showStatsSection();
            } else {
                hideStatsSection();
            }
            return;
        }
        hideFeedbackPlaceholder();
        showStatsSection();
        
        // Update feedback counts (handle both naming conventions)
        const positiveCount = stats.positive_feedback || stats.positive_count || 0;
        const negativeCount = stats.negative_feedback || stats.negative_count || 0;
        const totalCount = stats.total_feedback || (positiveCount + negativeCount);
        
        updateElement('alert-positive-count', positiveCount);
        updateElement('alert-negative-count', negativeCount);
        
        // Calculate negative rate (API might return approval_rate instead)
        let negativeRate = 0;
        if (stats.negative_rate !== undefined) {
            negativeRate = stats.negative_rate;
        } else if (stats.approval_rate !== undefined) {
            // Convert approval_rate (60%) to negative_rate (40%)
            negativeRate = 1.0 - (stats.approval_rate / 100.0);
        } else if (totalCount > 0) {
            negativeRate = negativeCount / totalCount;
        }
        
        updateElement('alert-negative-rate', `${(negativeRate * 100).toFixed(1)}%`);
        
        // Update negative rate badge color based on threshold
        const negativeRateBadge = document.getElementById('alert-negative-rate');
        if (negativeRateBadge) {
            if (negativeRate >= THRESHOLDS.HIGH_NEGATIVE_RATE) {
                negativeRateBadge.className = 'badge bg-danger';
            } else if (negativeRate >= THRESHOLDS.NEGATIVE_RATE_WARNING) {
                negativeRateBadge.className = 'badge bg-warning text-dark';
            } else {
                negativeRateBadge.className = 'badge bg-success';
            }
        }
        
        // Update min_score drift information
        // Check both data-level and stats-level fields
        const minScoreDrift = data.cumulative_drift || stats.cumulative_drift || 0;
        const currentMinScore = data.db_stats?.current_min_score || stats.current_min_score;
        const originalMinScore = stats.original_min_score;
        
        if (minScoreDrift !== undefined && minScoreDrift !== null && Math.abs(minScoreDrift) > 0.001) {
            updateMinScoreDrift(minScoreDrift, currentMinScore, originalMinScore);
        }
        
        // Update adjustment recommendation
        if (data.recommendation) {
            updateRecommendation(data.recommendation, negativeRate);
        }
        
        // Show/hide adjustment history link based on db_stats
        const hasHistory = data.db_stats && data.db_stats.total_feedback > 0;
        showAdjustmentHistoryLink(hasHistory);
        
        // Update last updated timestamp
        const lastUpdated = new Date().toISOString();
        updateElement('alert-feedback-last-updated', formatTimestamp(lastUpdated));
    }
    
    /**
     * Update min_score drift display
     */
    function updateMinScoreDrift(drift, current, original) {
        const driftSection = document.getElementById('alert-min-score-adjustment-info');
        if (!driftSection) return;
        
        // Show section if drift is non-zero, with ARIA update
        if (Math.abs(drift) > 0.001) {
            driftSection.classList.remove('d-none');
            driftSection.setAttribute('aria-hidden', 'false');
            
            updateElement('alert-min-score-drift-value', 
                drift > 0 ? `+${drift.toFixed(3)}` : drift.toFixed(3));
            
            // Safe display of current and original with null/undefined checks
            updateElement('alert-current-min-score', 
                Number.isFinite(current) ? current.toFixed(3) : '—');
            updateElement('alert-original-min-score', 
                Number.isFinite(original) ? original.toFixed(3) : '—');
            
            // Update progress bar (percentage of max drift)
            const driftBar = document.getElementById('alert-min-score-drift-bar');
            if (driftBar) {
                const percentage = Math.min(100, (Math.abs(drift) / THRESHOLDS.MAX_MIN_SCORE_DRIFT) * 100);
                driftBar.style.width = `${percentage}%`;
                // Set ARIA attributes: valuenow uses 0-100 scale, valuetext shows actual drift
                driftBar.setAttribute('aria-valuenow', percentage.toFixed(0));
                const driftText = drift > 0 ? `+${drift.toFixed(3)}` : drift.toFixed(3);
                driftBar.setAttribute('aria-valuetext', `${driftText} drift from original`);
                
                // Color based on drift magnitude
                if (percentage > 80) {
                    driftBar.className = 'progress-bar bg-danger';
                } else if (percentage > 50) {
                    driftBar.className = 'progress-bar bg-warning';
                } else {
                    driftBar.className = 'progress-bar bg-info';
                }
            }
        } else {
            driftSection.classList.add('d-none');
            driftSection.setAttribute('aria-hidden', 'true');
        }
    }
    
    /**
     * Update adjustment recommendation banner
     */
    function updateRecommendation(recommendation, negativeRate) {
        const recommendationSection = document.getElementById('alert-adjustment-recommendation');
        if (!recommendationSection) return;
        
        if (recommendation && recommendation.action) {
            recommendationSection.classList.remove('d-none');
            recommendationSection.setAttribute('aria-hidden', 'false');
            
            let message = '';
            if (negativeRate >= THRESHOLDS.HIGH_NEGATIVE_RATE) {
                message = `High false positive rate (${(negativeRate * 100).toFixed(1)}%). ` +
                         `Consider raising min_score to ${recommendation.suggested_min_score?.toFixed(2) || 'higher value'}.`;
            } else if (negativeRate >= THRESHOLDS.NEGATIVE_RATE_WARNING) {
                message = `Elevated false positive rate (${(negativeRate * 100).toFixed(1)}%). ` +
                         `Monitor alerts and consider raising min_score if trend continues.`;
            }
            
            const messageElement = document.getElementById('alert-recommendation-message');
            if (messageElement && message) {
                messageElement.textContent = message;
            }
        } else {
            recommendationSection.classList.add('d-none');
            recommendationSection.setAttribute('aria-hidden', 'true');
        }
    }
    
    /**
     * Show or hide adjustment history link
     */
    function showAdjustmentHistoryLink(show) {
        const historyLink = document.getElementById('alert-adjustment-history-link');
        if (historyLink) {
            historyLink.style.display = show ? 'block' : 'none';
        }
    }

    function showStatsSection() {
        const section = document.getElementById('alert-feedback-stats-section');
        if (section) {
            section.style.display = 'block';
            section.classList.remove('feedback-stats-hidden', 'feedback-stats-loading');
            section.classList.add('feedback-stats-visible');
            section.setAttribute('aria-busy', 'false');

            const loadingOverlay = document.getElementById('alert-feedback-stats-loading');
            if (loadingOverlay) {
                loadingOverlay.style.display = 'none';
            }
        }
    }

    function showFeedbackPlaceholder(message) {
        const placeholder = document.getElementById('alert-feedback-placeholder');
        const text = document.getElementById('alert-feedback-placeholder-text');
        if (text && message) {
            text.textContent = message;
        }
        if (placeholder) {
            placeholder.classList.remove('d-none');
        }
    }

    function hideFeedbackPlaceholder() {
        const placeholder = document.getElementById('alert-feedback-placeholder');
        if (placeholder) {
            placeholder.classList.add('d-none');
        }
    }
    
    /**
     * Hide the stats section if no data is available
     * Fail-safe: Only hide if JS successfully loaded, otherwise leave visible
     */
    function hideStatsSection() {
        const section = document.getElementById('alert-feedback-stats-section');
        if (section && document.documentElement.classList.contains('js-enabled')) {
            section.style.display = 'none';
            section.classList.add('feedback-stats-hidden');
            section.classList.remove('feedback-stats-visible');
            section.setAttribute('aria-hidden', 'true');
            hideFeedbackPlaceholder();
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
            return timestamp;
        }
    }
    
    /**
     * Initialize the feedback stats module
     * Marks document as JS-enabled and sets up event handlers
     */
    function init() {
        // Mark HTML as JS-enabled for progressive enhancement
        document.documentElement.classList.add('js-enabled');
        
        // Mark section as loading initially
        const section = document.getElementById('alert-feedback-stats-section');
        if (section) {
            section.classList.add('feedback-stats-loading');
            section.setAttribute('aria-busy', 'true');
            
            // Show loading overlay
            const loadingOverlay = document.getElementById('alert-feedback-stats-loading');
            if (loadingOverlay) {
                loadingOverlay.style.display = 'block';
            }
        }
        
        // Set up refresh button handler
        const refreshBtn = document.getElementById('btn-refresh-alert-feedback-stats');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', async () => {
                const profileId = document.getElementById('alert-profile-id')?.value;
                if (profileId) {
                    try {
                        refreshBtn.disabled = true;
                        const stats = await loadStats(profileId);
                        updateUI(stats);
                    } catch (error) {
                        console.error('[AlertFeedbackStats] Refresh failed:', error);
                    } finally {
                        refreshBtn.disabled = false;
                    }
                }
            });
        }
        
        // Set up adjustment history button
        const historyBtn = document.getElementById('btn-view-alert-adjustment-history');
        if (historyBtn) {
            historyBtn.addEventListener('click', (e) => {
                e.preventDefault();
                const profileId = document.getElementById('alert-profile-id')?.value;
                if (profileId) {
                    // TODO: Open modal with adjustment history
                    console.info('[AlertFeedbackStats] View adjustment history - not yet implemented');
                }
            });
        }
    }
    
    /**
     * Load and display stats for a specific profile
     * @param {number|string} profileId - The alert profile ID
     */
    async function loadStatsForProfile(profileId) {
        if (!profileId) {
            console.log('[AlertFeedbackStats] No profile ID, hiding stats section');
            hideStatsSection();
            return;
        }
        
        console.log('[AlertFeedbackStats] Loading stats for profile:', profileId);
        
        try {
            const stats = await loadStats(profileId);
            console.log('[AlertFeedbackStats] Stats loaded:', stats);
            if (stats) {
                updateUI(stats);
            } else {
                console.warn('[AlertFeedbackStats] No stats returned, hiding section');
                hideStatsSection();
            }
        } catch (error) {
            console.error('[AlertFeedbackStats] Failed to load stats for profile:', error);
            // Show section with zero stats instead of hiding it
            updateUI({ db_stats: { profile_id: profileId, positive_feedback: 0, negative_feedback: 0, total_feedback: 0, approval_rate: 100.0 } });
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
    document.addEventListener('DOMContentLoaded', AlertFeedbackStats.init);
} else {
    AlertFeedbackStats.init();
}

// Export for use in other modules
window.AlertFeedbackStats = AlertFeedbackStats;
