/**
 * Feeds Page JavaScript
 * Handles both Alerts Feed and Interests Feed interactions
 */

const alertsEndpoint = "/api/alerts";
const interestsEndpoint = "/api/interests";
const digestsEndpoint = "/api/digests";

// Profile cache for mapping IDs to names
window.alertProfileCache = {};
window.interestProfileCache = {};

/**
 * Load alert profile names into cache for display in feed items
 */
async function loadAlertProfileCache() {
    try {
        const response = await fetch('/api/profiles/alert/list');
        if (!response.ok) return;
        const data = await response.json();
        // API returns { status: 'ok', profiles: [...] }
        const profiles = data.profiles || data.data || [];
        if (data.status === 'ok' && Array.isArray(profiles)) {
            profiles.forEach(profile => {
                if (profile.id && profile.name) {
                    window.alertProfileCache[profile.id] = profile.name;
                }
            });
            console.log(`[FEEDS] Cached ${Object.keys(window.alertProfileCache).length} alert profile(s):`, window.alertProfileCache);
        }
    } catch (error) {
        console.warn('[FEEDS] Could not load alert profile cache:', error);
    }
}

/**
 * Load interest profile names into cache for display in feed items
 */
async function loadInterestProfileCache() {
    try {
        const response = await fetch('/api/profiles/interest/list');
        if (!response.ok) return;
        const data = await response.json();
        // API returns { status: 'ok', profiles: [...] }
        const profiles = data.profiles || data.data || [];
        if (data.status === 'ok' && Array.isArray(profiles)) {
            profiles.forEach(profile => {
                if (profile.id && profile.name) {
                    window.interestProfileCache[profile.id] = profile.name;
                }
            });
            console.log(`[FEEDS] Cached ${Object.keys(window.interestProfileCache).length} interest profile(s):`, window.interestProfileCache);
        }
    } catch (error) {
        console.warn('[FEEDS] Could not load interest profile cache:', error);
    }
}

function escapeHtml(value) {
    if (value === null || value === undefined) {
        return "";
    }
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

async function refreshAlerts() {
    try {
        const response = await fetch(`${alertsEndpoint}?limit=250`);
        if (!response.ok) {
            throw new Error("Alerts fetch failed");
        }
        const payload = await response.json();
        if (payload.status === 'ok' && payload.data) {
            renderAlertsTable(payload.data.alerts || []);
        } else {
            throw new Error(payload.error || "Invalid response");
        }
    } catch (error) {
        console.error(error);
        showToast("Unable to refresh alerts", "error");
    }
}

async function refreshInterests() {
    try {
        const response = await fetch(`${interestsEndpoint}?limit=250`);
        if (!response.ok) {
            throw new Error("Interests fetch failed");
        }
        const payload = await response.json();
        if (payload.status === 'ok' && payload.data) {
            renderInterestsTable(payload.data.interests || []);
        } else {
            throw new Error(payload.error || "Invalid response");
        }
    } catch (error) {
        console.error(error);
        showToast("Unable to refresh interests", "error");
    }
}

function renderAlertsTable(alerts) {
    const listContainer = document.querySelector("#alerts-list");
    if (!listContainer) {
        return;
    }
    if (!alerts.length) {
        listContainer.innerHTML = renderEmptyState('alert');
        return;
    }
    const items = alerts.map((alert) => renderFeedItem(alert, 'alert')).join("");
    listContainer.innerHTML = items;
    initTooltips(listContainer);
}

function renderInterestsTable(interests) {
    const listContainer = document.querySelector("#interests-list");
    if (!listContainer) {
        return;
    }
    if (!interests.length) {
        listContainer.innerHTML = renderEmptyState('interest');
        return;
    }
    
    // Data is already transformed by data_service.load_interests()
    // with UI-friendly field names: chat_name, sender, excerpt, msg_id, score, etc.
    const items = interests.map((interest) => renderFeedItem(interest, 'interest')).join("");
    listContainer.innerHTML = items;
    initTooltips(listContainer);
}

function truncateText(text, limit) {
    if (!text || text.length <= limit) return text;
    return text.substring(0, limit) + '...';
}

async function refreshDigests() {
    try {
        const response = await fetch(digestsEndpoint);
        if (!response.ok) {
            throw new Error("Digests fetch failed");
        }
        const payload = await response.json();
        if (payload.status === 'ok' && payload.data) {
            renderDigests(payload.data.digests || []);
        } else {
            throw new Error(payload.error || "Invalid response");
        }
    } catch (error) {
        console.error(error);
        showToast("Unable to refresh digests", "error");
    }
}

function initTooltips(context=document) {
    try {
        const nodes = (context || document).querySelectorAll('[data-bs-toggle="tooltip"]');
        nodes.forEach(el => {
            const existing = bootstrap.Tooltip.getInstance(el);
            if (existing) existing.dispose();
            
            // Validate that data-bs-title is a string, not an object
            let title = el.getAttribute('data-bs-title');
            if (title === null || title === undefined) {
                title = el.getAttribute('title') || '';
            }
            // If title is somehow an object string representation, skip
            if (typeof title !== 'string' || title === '[object Object]') {
                console.debug('Skipping tooltip with invalid title:', title);
                return;
            }
            
            new bootstrap.Tooltip(el, { 
                container: 'body', 
                html: true, 
                delay: { show: 0, hide: 100 },
                title: title  // Explicitly pass the validated title
            });
        });
    } catch (e) {
        console.debug('Tooltip init failed:', e);
    }
}

function renderDigests(digests) {
    const timeline = document.getElementById("digest-timeline");
    if (!timeline) {
        return;
    }
    if (!digests.length) {
        timeline.innerHTML = '<p class="text-muted">No digest batches yet.</p>';
        return;
    }
    timeline.innerHTML = digests.map((digest) => `
        <div class="card mb-3" role="listitem">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <h3 class="h5 mb-0">${escapeHtml(digest.date)}</h3>
                    <span class="badge bg-info" 
                          style="z-index: 1; cursor: help;"
                          data-bs-toggle="tooltip"
                          data-bs-placement="left"
                          data-bs-html="true"
                          data-bs-title="<div><strong>${escapeHtml(digest.items)} messages</strong> on this day</div>">
                        ${escapeHtml(digest.items)} messages
                    </span>
                </div>
                <p class="mb-0 text-muted">Average score: ${escapeHtml(digest.avg_score)}</p>
            </div>
        </div>`).join("");
    
    initTooltips(timeline);
}

function setupFeedbackDelegation() {
    const alertsList = document.querySelector("#alerts-list");
    const interestsList = document.querySelector("#interests-list");
    
    [alertsList, interestsList].forEach(listEl => {
        if (!listEl) return;
        
        // Create a fresh element to clear all listeners
        const newList = listEl.cloneNode(true);
        listEl.parentNode.replaceChild(newList, listEl);
        
        newList.addEventListener("click", async (event) => {
            const button = event.target.closest(".btn-feedback");
            if (!button) {
                return;
            }
            
            const chatId = button.getAttribute("data-chat-id");
            const msgId = button.getAttribute("data-msg-id");
            const label = button.getAttribute("data-score");
            const feedTypeAttr = button.getAttribute("data-feed-type");
            const feedType = feedTypeAttr === "interest" ? "interest" : "alert";
            const semanticTypeAttr = button.getAttribute("data-semantic-type");
            const semanticType = semanticTypeAttr || (feedType === "interest" ? "interest_semantic" : "alert_keyword");
            
            if (!chatId || !msgId) {
                showToast("Unable to record feedback: missing data", "error");
                return;
            }
            
            const parsedChatId = parseInt(chatId, 10);
            const parsedMsgId = parseInt(msgId, 10);
            
            if (!Number.isInteger(parsedChatId) || Number.isNaN(parsedChatId)) {
                console.error("Invalid chat_id for feedback:", chatId);
                showToast("Unable to record feedback: invalid chat ID", "error");
                return;
            }
            
            if (!Number.isInteger(parsedMsgId) || Number.isNaN(parsedMsgId)) {
                console.error("Invalid msg_id for feedback:", msgId);
                showToast("Unable to record feedback: invalid message ID", "error");
                return;
            }
            
            let profileIdsPayload = [];
            const rawProfiles = button.getAttribute("data-profiles");
            if (rawProfiles) {
                try {
                    const parsedProfiles = JSON.parse(rawProfiles);
                    if (Array.isArray(parsedProfiles)) {
                        profileIdsPayload = parsedProfiles
                            .map((profile) => {
                                if (profile && typeof profile === "object") {
                                    if (profile.profile_id !== undefined) {
                                        return String(profile.profile_id);
                                    }
                                    if (profile.id !== undefined) {
                                        return String(profile.id);
                                    }
                                }
                                if (profile === null || profile === undefined) {
                                    return null;
                                }
                                return String(profile);
                            })
                            .filter((pid) => pid && pid !== "undefined");
                    }
                } catch (profileError) {
                    console.debug("Unable to parse profile metadata for feedback", profileError);
                }
            }

            const apiPath = feedType === "interest" ? "/api/interests/feedback" : "/api/alerts/feedback";

            try {
                const response = await fetch(apiPath, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({
                        chat_id: parsedChatId,
                        msg_id: parsedMsgId,
                        label,
                        semantic_type: semanticType,
                        profile_ids: profileIdsPayload,
                    })
                });
                
                if (!response.ok) {
                    throw new Error("Feedback submission failed");
                }
                
                const result = await response.json();
                if (result.status === "ok") {
                    const direction = label === "up" ? "success" : "warning";
                    showToast("Feedback recorded", direction);
                    
                    button.classList.add(label === "up" ? "btn-success" : "btn-danger");
                    button.classList.remove("btn-outline-warning");
                    setTimeout(() => {
                        button.classList.remove("btn-success", "btn-danger");
                        button.classList.add("btn-outline-warning");
                    }, 1500);
                } else {
                    throw new Error(result.message || "Unknown error");
                }
            } catch (error) {
                console.error("Feedback error:", error);
                showToast("Failed to record feedback", "error");
            }
        });
    });
}

/**
 * Fetch channel configuration to check monitoring status
 * @param {number} chatId - The Telegram chat ID
 * @returns {Promise<Object|null>} Channel config or null if not monitored
 */
async function fetchChannelConfig(chatId) {
    const response = await fetch(`/api/config/channels/${chatId}`);
    if (!response.ok) {
        // 404 means channel is not monitored - this is expected for some channels
        if (response.status === 404) {
            return null;
        }
        let errorMessage = 'Failed to load channel configuration';
        try {
            const errorData = await response.json();
            errorMessage = errorData.message || errorMessage;
        } catch {
            // ignore JSON parse errors
        }
        throw new Error(errorMessage);
    }

    const data = await response.json();
    if (data.status !== 'ok' || !data.channel) {
        throw new Error(data.message || 'Channel configuration unavailable');
    }

    return data.channel;
}

/**
 * Safely hide a Bootstrap modal by blurring any focused element first.
 * This prevents the aria-hidden accessibility violation.
 * @param {HTMLElement|string} modal - The modal element or ID
 */
function safeHideModal(modal) {
    const modalEl = typeof modal === 'string' ? document.getElementById(modal) : modal;
    if (!modalEl) return;
    
    // Get the Bootstrap modal instance
    const modalInstance = bootstrap.Modal.getInstance(modalEl);
    if (!modalInstance) return;
    
    // Blur any focused element inside the modal to prevent aria-hidden violation
    const focusedElement = modalEl.querySelector(':focus');
    if (focusedElement) {
        focusedElement.blur();
    }
    
    // Also blur document.activeElement if it's inside the modal
    if (document.activeElement && modalEl.contains(document.activeElement)) {
        document.activeElement.blur();
    }
    
    // Small delay to ensure blur completes before hiding
    setTimeout(() => {
        modalInstance.hide();
    }, 10);
}

/**
 * Initialize the channel information modal
 * Displays comprehensive information about the clicked channel/user
 */

    function initChannelModal() {
        const modal = document.getElementById('channelActionsModal');
        if (!modal) return;

        // Update modal when it opens
        modal.addEventListener('show.bs.modal', async function(event) {
            const button = event.relatedTarget;
            const chatId = parseInt(button.getAttribute('data-chat-id'), 10);
            const chatName = button.getAttribute('data-chat-name') || 'Unknown';
            const sender = button.getAttribute('data-sender') || 'Unknown';
            const senderIdRaw = button.getAttribute('data-sender-id');
            const senderId = (senderIdRaw && senderIdRaw !== '' && senderIdRaw !== 'null' && senderIdRaw !== 'undefined') ? senderIdRaw : 'N/A';
            const score = button.getAttribute('data-score') || '0.00';
            const trigger = button.getAttribute('data-trigger') || 'None';
            const createdAt = button.getAttribute('data-created-at') || 'Unknown';
            const msgId = button.getAttribute('data-msg-id') || 'N/A';
            const feedType = button.getAttribute('data-feed-type') || 'Unknown';
            const profilesJson = button.getAttribute('data-profiles') || '[]';
            
            // Update title
            const modalTitle = document.getElementById('modal-channel-name');
            if (modalTitle) {
                modalTitle.textContent = chatName;
            }
            
            // Populate channel information
            const chatNameEl = document.getElementById('modal-info-chat-name');
            if (chatNameEl) chatNameEl.textContent = chatName;
            
            const chatIdEl = document.getElementById('modal-info-chat-id');
            if (chatIdEl) chatIdEl.textContent = chatId;
            
            // Setup copy button
            const copyBtn = document.getElementById('copy-chat-id-btn');
            if (copyBtn) {
                copyBtn.onclick = () => {
                    navigator.clipboard.writeText(chatId.toString()).then(() => {
                        const icon = copyBtn.querySelector('i');
                        icon.classList.remove('bi-clipboard');
                        icon.classList.add('bi-check-lg');
                        setTimeout(() => {
                            icon.classList.remove('bi-check-lg');
                            icon.classList.add('bi-clipboard');
                        }, 1500);
                    });
                };
            }
            
            // Check monitoring status - check both channels and users
            let isMonitored = false;
            try {
                // First try channels endpoint
                const channelConfig = await fetchChannelConfig(chatId);
                if (channelConfig !== null) {
                    isMonitored = true;
                } else {
                    // If not found in channels, try users endpoint
                    const userResponse = await fetch(`/api/config/users/${chatId}`);
                    if (userResponse.ok) {
                        const userData = await userResponse.json();
                        if (userData.status === 'ok' && userData.user) {
                            isMonitored = true;
                        }
                    }
                }
            } catch (error) {
                console.warn('Error checking monitoring status:', error);
            }
            
            const statusEl = document.getElementById('modal-info-monitoring-status');
            if (statusEl) {
                if (isMonitored) {
                    statusEl.textContent = 'Monitored';
                    statusEl.className = 'badge bg-success';
                } else {
                    statusEl.textContent = 'Not Monitored';
                    statusEl.className = 'badge bg-secondary';
                }
            }
            
            // Populate message context
            const senderEl = document.getElementById('modal-info-sender');
            if (senderEl) senderEl.textContent = sender;
            
            const senderIdEl = document.getElementById('modal-info-sender-id');
            if (senderIdEl) senderIdEl.textContent = senderId || 'N/A';
            
            const msgIdEl = document.getElementById('modal-info-msg-id');
            if (msgIdEl) msgIdEl.textContent = msgId || 'N/A';
            
            const createdAtEl = document.getElementById('modal-info-created-at');
            if (createdAtEl) {
                try {
                    const date = new Date(createdAt);
                    createdAtEl.textContent = date.toLocaleString();
                } catch {
                    createdAtEl.textContent = createdAt;
                }
            }
            
            // Populate scoring information
            const scoreEl = document.getElementById('modal-info-score');
            if (scoreEl) scoreEl.textContent = score;
            
            const feedTypeEl = document.getElementById('modal-info-feed-type');
            if (feedTypeEl) {
                const typeCapitalized = feedType.charAt(0).toUpperCase() + feedType.slice(1);
                feedTypeEl.textContent = typeCapitalized;
            }
            
            const triggerEl = document.getElementById('modal-info-trigger');
            if (triggerEl) triggerEl.textContent = trigger || 'None';
            
            // Parse and display matched profiles
            const profilesEl = document.getElementById('modal-info-profiles');
            if (profilesEl) {
                try {
                    const profiles = JSON.parse(profilesJson);
                    if (profiles && profiles.length > 0) {
                        // Clear existing content
                        profilesEl.textContent = '';
                        // Create DOM nodes to prevent XSS
                        const fragment = document.createDocumentFragment();
                        profiles.forEach(p => {
                            const span = document.createElement('span');
                            span.className = 'badge bg-info me-1';
                            span.textContent = p;
                            fragment.appendChild(span);
                        });
                        profilesEl.appendChild(fragment);
                    } else {
                        profilesEl.textContent = 'None';
                    }
                } catch {
                    profilesEl.textContent = 'None';
                }
            }
        });
        
        // Custom close button handler to prevent aria-hidden violation
        const closeButton = document.getElementById('close-channel-modal-btn');
        if (closeButton) {
            closeButton.addEventListener('click', function() {
                safeHideModal(modal);
            });
        }
    }

function bindInteractions() {
    setupFeedbackDelegation();
    
    document.getElementById("btn-refresh-alerts")?.addEventListener("click", refreshAlerts);
    document.getElementById("btn-refresh-interests")?.addEventListener("click", refreshInterests);
    document.getElementById("btn-download-digest")?.addEventListener("click", refreshDigests);
    
    document.getElementById("btn-export-alerts")?.addEventListener("click", () => {
        const alerts = Array.from(document.querySelectorAll("#alerts-list [data-alert-id]")).map(el => ({
            chat_id: el.querySelector('[data-chat-id]')?.getAttribute('data-chat-id'),
            chat_name: el.querySelector('[data-chat-name]')?.getAttribute('data-chat-name'),
            sender: el.querySelector('.text-muted')?.textContent.trim(),
            score: parseFloat(el.querySelector('.badge')?.textContent) || 0,
            trigger: el.querySelector('small.text-muted')?.textContent.replace('🔔 ', '') || '',
            excerpt: el.querySelector('.text-truncate')?.textContent.trim() || '',
            created_at: el.querySelector('small[data-bs-title*="Date"]')?.textContent.replace('⏰ ', '') || ''
        }));
        exportFeedToCSV(alerts, 'alert', 'tgsentinel_alerts');
    });
    
    document.getElementById("btn-export-interests")?.addEventListener("click", () => {
        const interests = Array.from(document.querySelectorAll("#interests-list [data-interest-id]")).map(el => ({
            chat_id: el.querySelector('[data-chat-id]')?.getAttribute('data-chat-id'),
            chat_name: el.querySelector('[data-chat-name]')?.getAttribute('data-chat-name'),
            sender: el.querySelector('.text-muted')?.textContent.trim(),
            score: parseFloat(el.querySelector('.badge')?.textContent) || 0,
            profile_name: el.querySelector('.badge.bg-info')?.textContent.trim() || '',
            trigger: 'semantic',
            excerpt: el.querySelector('.text-truncate')?.textContent.trim() || '',
            created_at: el.querySelector('small[data-bs-title*="Date"]')?.textContent.replace('⏰ ', '') || ''
        }));
        exportFeedToCSV(interests, 'interest', 'tgsentinel_interests');
    });
}

let alertsIntervalId = null;
let interestsIntervalId = null;
let digestsIntervalId = null;

function startIntervals() {
    stopIntervals();
    alertsIntervalId = setInterval(refreshAlerts, 120000);
    interestsIntervalId = setInterval(refreshInterests, 120000);
    digestsIntervalId = setInterval(refreshDigests, 300000);
}

function stopIntervals() {
    if (alertsIntervalId !== null) {
        clearInterval(alertsIntervalId);
        alertsIntervalId = null;
    }
    if (interestsIntervalId !== null) {
        clearInterval(interestsIntervalId);
        interestsIntervalId = null;
    }
    if (digestsIntervalId !== null) {
        clearInterval(digestsIntervalId);
        digestsIntervalId = null;
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    // Load profile caches first so feed items can show profile names
    await Promise.all([loadAlertProfileCache(), loadInterestProfileCache()]);
    
    refreshAlerts();
    refreshInterests();
    refreshDigests();
    bindInteractions();
    initChannelModal();
    initTooltips();
    startIntervals();
});

window.addEventListener("beforeunload", stopIntervals);

document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
        stopIntervals();
    } else {
        startIntervals();
    }
});
