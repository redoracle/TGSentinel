/**
 * Shared Feed Renderer Module
 * 
 * Provides reusable functions for rendering and managing feed lists (Alerts and Interests).
 * Maintains consistent UI/UX patterns across different feed types.
 */

/**
 * Safely convert a value to string for tooltip display
 * @param {*} value - Any value to convert
 * @returns {string} Safe string representation
 */
function safeTooltipString(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'string') return value;
    if (typeof value === 'number') return String(value);
    if (typeof value === 'object') {
        try {
            return JSON.stringify(value);
        } catch (e) {
            return '[object]';
        }
    }
    return String(value);
}

/**
 * Render a single feed item (alert or interest match)
 * 
 * @param {Object} item - Feed item data
 * @param {string} feedType - 'alert' or 'interest'
 * @returns {string} HTML string for the list item
 * 
 * IMPORTANT: Alert profiles (1000-1999) use keyword-based heuristic scores.
 * Interest profiles (3000-3999) use semantic similarity scores.
 * These are fundamentally different scoring mechanisms and should not be mixed.
 */
function renderFeedItem(item, feedType = 'alert') {
    const badgeClass = feedType === 'alert' ? 'bg-danger' : 'bg-info';
    const feedSemanticType = feedType === 'interest' ? 'interest_semantic' : 'alert_keyword';
    
    // Parse timestamp
    const dateParts = (item.created_at || '').split(' ');
    const date = dateParts[0] || '';
    const time = dateParts[1] || '';
    
    // Get matched profiles (ensure it's an array)
    const matchedProfiles = Array.isArray(item.matched_profiles) ? item.matched_profiles : [];
    // Convert to JSON and escape only single quotes for safe embedding in single-quoted attributes
    // This keeps the JSON parseable while preventing attribute injection
    const matchedProfilesJson = JSON.stringify(matchedProfiles).replace(/'/g, '&#39;');
    
    // For ALERTS: Use heuristic score (keyword-based)
    // For INTERESTS: Use semantic score from the item or matched profiles
    let displayScore = 0;
    let matchedProfilesBadges = '';
    let profileMetadataHTML = '';
    
    if (feedType === 'alert') {
        // ALERT PROFILE LOGIC
        // Score is the heuristic/keyword score from the worker
        displayScore = typeof item.score === 'number' ? item.score : parseFloat(item.score) || 0;
        
        // Show matched Alert Profile names (1000-1999 range)
        // Filter to only show alert profile IDs (exclude interest profile IDs 3000+)
        const alertProfileIds = matchedProfiles.filter(pid => {
            const numId = typeof pid === 'string' ? parseInt(pid, 10) : pid;
            return numId >= 1000 && numId < 3000; // Alert profiles are 1000-1999
        });
        
        if (alertProfileIds.length > 0) {
            matchedProfilesBadges = alertProfileIds.map(profileId => {
                // Look up profile name from cache, fallback to ID
                const profileName = (window.alertProfileCache && window.alertProfileCache[profileId]) 
                    ? window.alertProfileCache[profileId] 
                    : `Profile ${profileId}`;
                const profileUrl = `/profiles#alert-${profileId}`;
                return `<a href="${profileUrl}" class="badge bg-primary text-decoration-none profile-badge-link" 
                           style="max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: middle;"
                           title="${escapeHtml(profileName)} (ID: ${profileId}) - Click to edit">${escapeHtml(profileName)}</a>`;
            }).join(' ');
            profileMetadataHTML = `<small class="text-primary">${matchedProfilesBadges}</small>`;
        }
        
    } else {
        // INTEREST PROFILE LOGIC
        // Score should be the semantic similarity score
        // Try to get it from matched_profiles first, then fall back to item.score
        displayScore = typeof item.score === 'number' ? item.score : parseFloat(item.score) || 0;
        
        // For interests, matched_profiles contains objects with profile details
        if (matchedProfiles.length > 0) {
            matchedProfilesBadges = matchedProfiles.map(mp => {
                if (typeof mp === 'object' && mp !== null) {
                    const profileName = mp.profile_name || 'Unknown';
                    const profileId = mp.profile_id || mp.id || '';
                    const semanticScore = typeof mp.semantic_score === 'number' ? mp.semantic_score.toFixed(3) : '0.000';
                    const threshold = mp.threshold || 'N/A';
                    const profileUrl = `/profiles#interest-${profileId}`;
                    return `<a href="${profileUrl}" class="badge bg-info text-decoration-none profile-badge-link" 
                               style="max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: middle;"
                               title="${escapeHtml(profileName)}: ${semanticScore} (threshold: ${threshold}) - Click to edit">${escapeHtml(profileName)}</a>`;
                } else {
                    // Fallback for simple profile IDs
                    const profileId = String(mp);
                    const profileName = (window.interestProfileCache && window.interestProfileCache[profileId]) 
                        ? window.interestProfileCache[profileId] 
                        : `Profile ${profileId}`;
                    const profileUrl = `/profiles#interest-${profileId}`;
                    return `<a href="${profileUrl}" class="badge bg-info text-decoration-none profile-badge-link" 
                               style="max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: middle;"
                               title="${escapeHtml(profileName)} - Click to edit">${escapeHtml(profileName)}</a>`;
                }
            }).join(' ');
            profileMetadataHTML = `<small class="text-info">${matchedProfilesBadges}</small>`;
        }
        
        // Show semantic scores for interest feed (these are the Interest Profile scores)
        const semanticScores = (item.semantic_scores && typeof item.semantic_scores === 'object') 
            ? item.semantic_scores 
            : {};
        if (Object.keys(semanticScores).length > 0) {
            const scores = Object.entries(semanticScores)
                .map(([pid, s]) => `${escapeHtml(pid)}=${Number(s).toFixed(3)}`)
                .join(', ');
            const semanticScoresTooltip = `Semantic scores: ${scores}`;
            profileMetadataHTML += `
                <small class="text-info" data-bs-toggle="tooltip" data-bs-html="true" 
                       data-bs-title="${escapeHtml(semanticScoresTooltip)}">
                    🧠 ${Object.keys(semanticScores).length} semantic
                </small>
            `;
        }
    }
    
    // Build webhook payload for alerts only
    let webhookHTML = '';
    if (feedType === 'alert') {
        const webhookPayload = JSON.stringify({
            event: "alert_triggered",
            chat_id: item.chat_id,
            chat_name: item.chat_name,
            message_id: item.msg_id || 0,
            sender: item.sender,
            score: displayScore,
            matched_profiles: matchedProfiles.filter(pid => {
                const numId = typeof pid === 'string' ? parseInt(pid, 10) : pid;
                return numId >= 1000 && numId < 3000;
            }),
            triggers: item.trigger,
            text_preview: item.excerpt,
            sent_to: item.sent_to
        });
        webhookHTML = `
            <span class="text-info badge-raised"
                  role="img"
                  aria-label="Webhook payload"
                  data-bs-toggle="tooltip"
                  data-bs-placement="top"
                  data-bs-html="true"
                  data-bs-title="${escapeHtml(webhookPayload)}">
                <i class="bi bi-webhook"></i>
            </span>
        `;
    }
    
    // Safe tooltip content for message excerpt
    const tooltipContent = safeTooltipString(item.message_text || item.excerpt || '');
    
    return `
        <div class="list-group-item list-group-item-action alert-list-item" data-${feedType}-id="${item.msg_id}">
            <div class="d-flex w-100 justify-content-between align-items-start gap-3">
                <div class="flex-grow-1 min-w-0">
                    <!-- Channel and Sender -->
                    <div class="d-flex align-items-center gap-2 mb-2 flex-wrap">
                        <a href="#" class="text-decoration-none channel-link fw-semibold" 
                           data-chat-id="${item.chat_id}" 
                           data-chat-name="${escapeHtml(safeTooltipString(item.chat_name))}" 
                           data-sender="${escapeHtml(safeTooltipString(item.sender || 'Unknown'))}" 
                           data-sender-id="${item.sender_id || ''}" 
                           data-score="${displayScore.toFixed(2)}" 
                           data-trigger="${escapeHtml(safeTooltipString(item.trigger || ''))}" 
                           data-created-at="${escapeHtml(safeTooltipString(item.created_at || ''))}" 
                           data-msg-id="${item.msg_id || ''}" 
                           data-feed-type="${feedType}" 
                           data-profiles='${matchedProfilesJson}' 
                           data-bs-toggle="modal" 
                           data-bs-target="#channelActionsModal"
                           title="Click to view details">
                            ${escapeHtml(safeTooltipString(item.chat_name))}
                        </a>
                        <span class="text-muted">•</span>
                        <small class="text-muted">${escapeHtml(safeTooltipString(item.sender || 'Unknown'))}</small>
                        <span class="badge ${badgeClass} ms-auto">${displayScore.toFixed(2)}</span>
                    </div>
                    
                    <!-- Message Excerpt -->
                    <p class="mb-2 text-truncate" 
                       data-bs-toggle="tooltip"
                       data-bs-placement="auto"
                       data-bs-custom-class="excerpt-tooltip"
                       data-bs-title="${escapeHtml(tooltipContent)}">
                        ${escapeHtml(safeTooltipString(item.excerpt))}
                    </p>
                    
                    <!-- Metadata Row -->
                    <div class="d-flex gap-2 flex-wrap align-items-center">
                        ${item.trigger && item.trigger !== 'N/A' ? `<small class="text-muted">🔔 ${escapeHtml(safeTooltipString(item.trigger))}</small>` : ''}
                        ${profileMetadataHTML}
                        ${date ? `
                            <small class="text-muted" 
                                   data-bs-toggle="tooltip"
                                   data-bs-html="true"
                                   data-bs-placement="top"
                                   data-bs-title="<div><div><strong>Date:</strong> ${escapeHtml(date)}</div><div><strong>Time:</strong> ${escapeHtml(time)}</div><div><strong>Destination:</strong> ${escapeHtml(safeTooltipString(item.sent_to))}</div></div>">
                                ⏰ ${escapeHtml(date)} ${escapeHtml(time)}
                            </small>
                        ` : ''}
                        ${webhookHTML}
                    </div>
                </div>
                
                <!-- Feedback Actions -->
                <div class="d-flex flex-column gap-2">
                    <div class="btn-group btn-group-sm" role="group" aria-label="Feedback controls">
                        <button class="btn btn-outline-warning btn-feedback"
                                data-score="up"
                                data-chat-id="${item.chat_id}"
                                data-msg-id="${item.msg_id || 0}"
                                data-feed-type="${feedType}"
                                data-semantic-type="${feedSemanticType}"
                                data-profiles='${matchedProfilesJson}'
                                type="button" aria-label="Give positive feedback" title="Helpful">👍</button>
                        <button class="btn btn-outline-warning btn-feedback"
                                data-score="down"
                                data-chat-id="${item.chat_id}"
                                data-msg-id="${item.msg_id || 0}"
                                data-feed-type="${feedType}"
                                data-semantic-type="${feedSemanticType}"
                                data-profiles='${matchedProfilesJson}'
                                type="button" aria-label="Give negative feedback" title="Not helpful">👎</button>
                    </div>
                </div>
            </div>
        </div>
    `;
}

/**
 * Render empty state for a feed
 * 
 * @param {string} feedType - 'alert' or 'interest'
 * @returns {string} HTML string for empty state
 */
function renderEmptyState(feedType = 'alert') {
    const messages = {
        alert: {
            title: 'No alerts recorded yet',
            subtitle: 'System will populate this list as triggers fire'
        },
        interest: {
            title: 'No interest matches yet',
            subtitle: 'Interest Profile matches will appear here once semantic scoring identifies relevant content'
        }
    };
    
    const msg = messages[feedType] || messages.alert;
    
    return `
        <div class="alert-profiles-empty">
            <svg width="48" height="48" fill="currentColor" viewBox="0 0 16 16">
                <path d="M8 16a2 2 0 0 0 2-2H6a2 2 0 0 0 2 2zM8 1.918l-.797.161A4.002 4.002 0 0 0 4 6c0 .628-.134 2.197-.459 3.742-.16.767-.376 1.566-.663 2.258h10.244c-.287-.692-.502-1.49-.663-2.258C12.134 8.197 12 6.628 12 6a4.002 4.002 0 0 0-3.203-3.92L8 1.917zM14.22 12c.223.447.481.801.78 1H1c.299-.199.557-.553.78-1C2.68 10.2 3 6.88 3 6c0-2.42 1.72-4.44 4.005-4.901a1 1 0 1 1 1.99 0A5.002 5.002 0 0 1 13 6c0 .88.32 4.2 1.22 6z"/>
            </svg>
            <p class="mb-2">${msg.title}</p>
            <small>${msg.subtitle}</small>
        </div>
    `;
}

/**
 * Export feed data to CSV
 * 
 * @param {Array} items - Array of feed items
 * @param {string} feedType - 'alert' or 'interest'
 * @param {string} filename - Output filename (without extension)
 */
function exportFeedToCSV(items, feedType = 'alert', filename = 'tgsentinel_feed') {
    if (!items || items.length === 0) {
        alert('No data to export');
        return;
    }
    
    // Build CSV header
    const headers = ['Chat ID', 'Chat Name', 'Sender', 'Score', 'Trigger', 'Excerpt', 'Timestamp'];
    if (feedType === 'interest') {
        headers.splice(4, 0, 'Profile Name');
    }
    
    // Build CSV rows
    const rows = items.map(item => {
        const row = [
            item.chat_id,
            `"${(item.chat_name || '').replace(/"/g, '""')}"`,
            `"${(item.sender || '').replace(/"/g, '""')}"`,
            item.score,
            `"${(item.trigger || '').replace(/"/g, '""')}"`,
            `"${(item.excerpt || '').replace(/"/g, '""')}"`,
            item.created_at || ''
        ];
        
        if (feedType === 'interest') {
            row.splice(4, 0, `"${(item.profile_name || '').replace(/"/g, '""')}"`);
        }
        
        return row.join(',');
    });
    
    // Combine header and rows
    const csv = [headers.join(','), ...rows].join('\n');
    
    // Create download link
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    
    link.setAttribute('href', url);
    link.setAttribute('download', `${filename}_${new Date().toISOString().split('T')[0]}.csv`);
    link.style.visibility = 'hidden';
    
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

/**
 * Escape HTML to prevent XSS
 * 
 * @param {string} text - Text to escape
 * @returns {string} Escaped text
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Refresh feed data from API
 * 
 * @param {string} feedType - 'alert' or 'interest'
 * @param {string} containerId - ID of the container element
 * @param {number} limit - Number of items to fetch
 * @returns {Promise<Array>} Array of feed items
 */
async function refreshFeed(feedType = 'alert', containerId = 'alerts-list', limit = 100) {
    const container = document.getElementById(containerId);
    if (!container) {
        console.error(`Container #${containerId} not found`);
        return [];
    }
    
    // Show loading state
    container.innerHTML = `
        <div class="text-center text-muted py-4">
            <div class="spinner-border spinner-border-sm me-2" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            Loading ${feedType}s...
        </div>
    `;
    
    try {
        const endpoint = feedType === 'alert' ? '/api/alerts' : '/api/interests';
        const response = await fetch(`${endpoint}?limit=${limit}`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        if (data.status !== 'ok' || !data.data) {
            throw new Error(data.error || 'Invalid API response');
        }
        
        const items = feedType === 'alert' ? data.data.alerts : data.data.interests;
        
        // Render items
        if (items && items.length > 0) {
            container.innerHTML = items.map(item => renderFeedItem(item, feedType)).join('');
            
            // Re-initialize tooltips
            const tooltipTriggerList = container.querySelectorAll('[data-bs-toggle="tooltip"]');
            [...tooltipTriggerList].forEach(el => new bootstrap.Tooltip(el));
        } else {
            container.innerHTML = renderEmptyState(feedType);
        }
        
        return items;
        
    } catch (error) {
        console.error(`Failed to refresh ${feedType}s:`, error);
        container.innerHTML = `
            <div class="alert alert-danger m-3">
                <i class="bi bi-exclamation-triangle-fill me-2"></i>
                Failed to load ${feedType}s: ${error.message}
            </div>
        `;
        return [];
    }
}

/**
 * Initialize feed with event listeners
 * 
 * @param {Object} config - Configuration object
 * @param {string} config.feedType - 'alert' or 'interest'
 * @param {string} config.containerId - ID of the feed container
 * @param {string} config.refreshButtonId - ID of the refresh button
 * @param {string} config.exportButtonId - ID of the export button
 * @param {number} config.limit - Number of items to fetch
 */
function initializeFeed(config) {
    const {
        feedType = 'alert',
        containerId = 'alerts-list',
        refreshButtonId = 'btn-refresh-alerts',
        exportButtonId = 'btn-export-alerts',
        limit = 100
    } = config;
    
    let feedData = [];
    
    // Refresh button
    const refreshBtn = document.getElementById(refreshButtonId);
    if (refreshBtn) {
        refreshBtn.addEventListener('click', async () => {
            feedData = await refreshFeed(feedType, containerId, limit);
        });
    }
    
    // Export button
    const exportBtn = document.getElementById(exportButtonId);
    if (exportBtn) {
        exportBtn.addEventListener('click', () => {
            exportFeedToCSV(feedData, feedType, `tgsentinel_${feedType}s`);
        });
    }
    
    // Store feed data for export (use existing data from server-side render)
    const container = document.getElementById(containerId);
    if (container) {
        const items = container.querySelectorAll(`.list-group-item[data-${feedType}-id]`);
        // If items exist, we need to extract data for CSV export
        // For now, we'll fetch fresh data on first export
        feedData = [];
    }
}

// Export functions for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        renderFeedItem,
        renderEmptyState,
        exportFeedToCSV,
        refreshFeed,
        initializeFeed,
        escapeHtml
    };
}
