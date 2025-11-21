/**
 * Entity Selector Module
 * 
 * Manages the entity selection modal for both Alert and Interest profiles.
 * Handles fetching monitored entities (channels/users), modal interaction,
 * badge rendering, and selection state management.
 * 
 * Public API:
 *   - init(): Initialize the entity selector module
 *   - fetchMonitoredEntities(): Fetch channels and users from API
 *   - setupEntitySelectionModal(): Set up modal event handlers
 *   - getSelectedEntityIds(profileType, entityType): Get selected IDs
 *   - setSelectedEntityIds(profileType, channelIds, userIds): Set selections
 *   - removeEntity(profileType, entityType, entityId): Remove single entity
 * 
 * Dependencies:
 *   - window.SharedUtils (escapeHtml)
 *   - Bootstrap 5 (modal)
 */

(function() {
    'use strict';
    
    // ============= MODULE STATE =============
    
    let monitoredChannels = [];
    let monitoredUsers = [];
    let entitiesFetched = false;
    
    // Current selections per profile type
    let selectedChannelsAlert = [];
    let selectedUsersAlert = [];
    let selectedChannelsInterest = [];
    let selectedUsersInterest = [];
    
    // Track which profile type triggered the modal
    let currentProfileTypeForModal = 'alert';
    
    // ============= PUBLIC API =============
    
    /**
     * Initialize the entity selector module
     */
    async function init() {
        await fetchMonitoredEntities();
        setupEntitySelectionModal();
    }
    
    /**
     * Fetch monitored entities (channels and users) from the API
     * @returns {Promise<void>}
     */
    async function fetchMonitoredEntities() {
        try {
            // Fetch channels
            const channelsResponse = await fetch('/api/config/channels');
            if (channelsResponse.ok) {
                const channelsData = await channelsResponse.json();
                monitoredChannels = channelsData.channels || [];
                console.log('Loaded', monitoredChannels.length, 'monitored channels');
            } else {
                console.error('Failed to fetch channels:', channelsResponse.statusText);
            }
            
            // Fetch users
            const usersResponse = await fetch('/api/config/users');
            if (usersResponse.ok) {
                const usersData = await usersResponse.json();
                monitoredUsers = usersData.users || [];
                console.log('Loaded', monitoredUsers.length, 'monitored users');
            } else {
                console.error('Failed to fetch users:', usersResponse.statusText);
            }
            
            entitiesFetched = true;
        } catch (error) {
            console.error('Error fetching monitored entities:', error);
            entitiesFetched = false;
        }
    }
    
    /**
     * Set up the entity selection modal handlers
     */
    function setupEntitySelectionModal() {
        const modal = document.getElementById('selectEntitiesModal');
        if (!modal) return;
        
        // When modal is shown, populate it based on which button was clicked
        modal.addEventListener('show.bs.modal', async function(event) {
            const button = event.relatedTarget;
            if (button) {
                // Determine which profile type triggered the modal
                if (button.id === 'btn-select-entities-alert') {
                    currentProfileTypeForModal = 'alert';
                } else if (button.id === 'btn-select-entities-interest') {
                    currentProfileTypeForModal = 'interest';
                }
            }
            
            // Show loading state
            const channelsLoading = document.getElementById('modal-channels-loading');
            const usersLoading = document.getElementById('modal-users-loading');
            if (channelsLoading) channelsLoading.classList.remove('d-none');
            if (usersLoading) usersLoading.classList.remove('d-none');
            
            // Fetch entities if not already loaded
            if (!entitiesFetched) {
                await fetchMonitoredEntities();
            }
            
            // Populate modal with current selections
            populateModalEntities();
        });
        
        // Handle Apply Selection button
        const applyButton = document.getElementById('btn-apply-entity-selection');
        if (applyButton) {
            applyButton.addEventListener('click', function() {
                applyModalSelection();
                const modalInstance = bootstrap.Modal.getInstance(modal);
                if (modalInstance) {
                    modalInstance.hide();
                }
            });
        }
        
        // Handle Select All buttons
        const selectAllChannelsBtn = document.getElementById('btn-select-all-modal-channels');
        if (selectAllChannelsBtn) {
            selectAllChannelsBtn.addEventListener('click', function() {
                const checkboxes = document.querySelectorAll('#modal-channels-checkboxes input[type="checkbox"]');
                const allChecked = Array.from(checkboxes).every(cb => cb.checked);
                checkboxes.forEach(cb => cb.checked = !allChecked);
            });
        }
        
        const selectAllUsersBtn = document.getElementById('btn-select-all-modal-users');
        if (selectAllUsersBtn) {
            selectAllUsersBtn.addEventListener('click', function() {
                const checkboxes = document.querySelectorAll('#modal-users-checkboxes input[type="checkbox"]');
                const allChecked = Array.from(checkboxes).every(cb => cb.checked);
                checkboxes.forEach(cb => cb.checked = !allChecked);
            });
        }
        
        // Handle search filtering
        const channelsSearch = document.getElementById('modal-channels-search');
        if (channelsSearch) {
            channelsSearch.addEventListener('input', function(e) {
                filterModalList('modal-channels-checkboxes', e.target.value);
            });
        }
        
        const usersSearch = document.getElementById('modal-users-search');
        if (usersSearch) {
            usersSearch.addEventListener('input', function(e) {
                filterModalList('modal-users-checkboxes', e.target.value);
            });
        }
    }
    
    /**
     * Get selected entity IDs for saving
     * @param {string} profileType - 'alert' or 'interest'
     * @param {string} entityType - 'channels' or 'users'
     * @returns {Array<number>} Array of selected numeric IDs
     */
    function getSelectedEntityIds(profileType, entityType) {
        if (profileType === 'alert') {
            return entityType === 'channels' ? selectedChannelsAlert : selectedUsersAlert;
        } else if (profileType === 'interest') {
            return entityType === 'channels' ? selectedChannelsInterest : selectedUsersInterest;
        }
        return [];
    }
    
    /**
     * Set selected entity IDs when loading a profile
     * @param {string} profileType - 'alert' or 'interest'
     * @param {Array<number>} channelIds - Array of numeric channel IDs
     * @param {Array<number>} userIds - Array of numeric user IDs
     */
    function setSelectedEntityIds(profileType, channelIds, userIds) {
        const channels = Array.isArray(channelIds) ? channelIds : [];
        const users = Array.isArray(userIds) ? userIds : [];
        
        if (profileType === 'alert') {
            selectedChannelsAlert = channels;
            selectedUsersAlert = users;
            updateEntityBadges('alert', channels, users);
        } else if (profileType === 'interest') {
            selectedChannelsInterest = channels;
            selectedUsersInterest = users;
            updateEntityBadges('interest', channels, users);
        }
    }
    
    /**
     * Remove a specific entity from selection
     * @param {string} profileType - 'alert' or 'interest'
     * @param {string} entityType - 'channel' or 'user'
     * @param {number} entityId - Numeric ID of entity to remove
     */
    function removeEntity(profileType, entityType, entityId) {
        if (profileType === 'alert') {
            if (entityType === 'channel') {
                selectedChannelsAlert = selectedChannelsAlert.filter(id => id !== entityId);
            } else {
                selectedUsersAlert = selectedUsersAlert.filter(id => id !== entityId);
            }
            updateEntityBadges('alert', selectedChannelsAlert, selectedUsersAlert);
        } else if (profileType === 'interest') {
            if (entityType === 'channel') {
                selectedChannelsInterest = selectedChannelsInterest.filter(id => id !== entityId);
            } else {
                selectedUsersInterest = selectedUsersInterest.filter(id => id !== entityId);
            }
            updateEntityBadges('interest', selectedChannelsInterest, selectedUsersInterest);
        }
    }
    
    // ============= PRIVATE HELPERS =============
    
    /**
     * Populate the modal with channels and users, pre-selecting current choices
     */
    function populateModalEntities() {
        const currentChannels = currentProfileTypeForModal === 'alert' ? selectedChannelsAlert : selectedChannelsInterest;
        const currentUsers = currentProfileTypeForModal === 'alert' ? selectedUsersAlert : selectedUsersInterest;
        
        // Populate channels
        const channelsContainer = document.getElementById('modal-channels-checkboxes');
        const channelsLoading = document.getElementById('modal-channels-loading');
        const channelsList = document.getElementById('modal-channels-list');
        const channelsError = document.getElementById('modal-channels-error');
        
        if (monitoredChannels.length === 0) {
            if (channelsLoading) channelsLoading.classList.add('d-none');
            if (channelsList) channelsList.classList.add('d-none');
            if (channelsError) {
                channelsError.classList.remove('d-none');
                channelsError.textContent = 'No monitored channels configured. Please add channels in Configuration first.';
            }
        } else {
            if (channelsLoading) channelsLoading.classList.add('d-none');
            if (channelsList) channelsList.classList.remove('d-none');
            if (channelsError) channelsError.classList.add('d-none');
            
            if (channelsContainer) {
                channelsContainer.innerHTML = monitoredChannels.map(channel => {
                    const isChecked = currentChannels.includes(channel.id);
                    const displayName = channel.name || channel.title || channel.username || `Channel ${channel.id}`;
                    const initials = displayName.split(' ').map(w => w[0]).slice(0, 2).join('').toUpperCase();
                    const isChat = channel.id < 0;
                    const prefix = isChat ? "chat" : "user";
                    const avatarUrl = `/api/avatar/${prefix}/${Math.abs(channel.id)}`;
                    const bgColor = channel.type === 'channel' ? '#0d6efd' : channel.type === 'supergroup' ? '#0dcaf0' : '#6c757d';
                    
                    return `
                        <label class="list-group-item list-group-item-action d-flex align-items-center py-2 gap-2" data-entity-name="${displayName.toLowerCase()}" style="cursor: pointer;">
                            <input class="form-check-input" type="checkbox" value="${channel.id}" ${isChecked ? 'checked' : ''} style="flex-shrink: 0;">
                            <div class="rounded-circle d-flex align-items-center justify-content-center" 
                                 style="width: 32px; height: 32px; flex-shrink: 0; font-size: 0.75rem; font-weight: 600; color: white; background-color: ${bgColor}; position: relative; overflow: hidden;">
                                <img src="${avatarUrl}" alt="${window.SharedUtils.escapeHtml(displayName)}" 
                                     style="width: 100%; height: 100%; object-fit: cover; border-radius: 50%;"
                                     onerror="this.style.display='none'; const initials = '${initials}'; this.parentElement.querySelector('span').textContent = initials || '?';">
                                <span style="position: relative; z-index: 1;"></span>
                            </div>
                            <div class="flex-grow-1" style="min-width: 0;">
                                <div class="fw-semibold text-truncate">${window.SharedUtils.escapeHtml(displayName)}</div>
                                <small class="text-muted">ID: ${channel.id}</small>
                            </div>
                        </label>
                    `;
                }).join('');
            }
        }
        
        const channelsCountBadge = document.getElementById('channels-count-badge');
        if (channelsCountBadge) {
            channelsCountBadge.textContent = monitoredChannels.length;
        }
        
        // Populate users
        const usersContainer = document.getElementById('modal-users-checkboxes');
        const usersLoading = document.getElementById('modal-users-loading');
        const usersList = document.getElementById('modal-users-list');
        const usersError = document.getElementById('modal-users-error');
        
        if (monitoredUsers.length === 0) {
            if (usersLoading) usersLoading.classList.add('d-none');
            if (usersList) usersList.classList.add('d-none');
            if (usersError) {
                usersError.classList.remove('d-none');
                usersError.textContent = 'No monitored users configured. Please add users in Configuration first.';
            }
        } else {
            if (usersLoading) usersLoading.classList.add('d-none');
            if (usersList) usersList.classList.remove('d-none');
            if (usersError) usersError.classList.add('d-none');
            
            if (usersContainer) {
                usersContainer.innerHTML = monitoredUsers.map(user => {
                    const isChecked = currentUsers.includes(user.id);
                    const displayName = [user.first_name, user.last_name].filter(Boolean).join(' ').trim() 
                        || user.name 
                        || user.username 
                        || `User ${user.id}`;
                    const initials = displayName.split(' ').map(w => w[0]).slice(0, 2).join('').toUpperCase();
                    const avatarUrl = `/api/avatar/user/${user.id}`;
                    
                    return `
                        <label class="list-group-item list-group-item-action d-flex align-items-center py-2 gap-2" data-entity-name="${displayName.toLowerCase()}" style="cursor: pointer;">
                            <input class="form-check-input" type="checkbox" value="${user.id}" ${isChecked ? 'checked' : ''} style="flex-shrink: 0;">
                            <div class="rounded-circle d-flex align-items-center justify-content-center" 
                                 style="width: 32px; height: 32px; flex-shrink: 0; font-size: 0.75rem; font-weight: 600; color: white; background-color: #6f42c1; position: relative; overflow: hidden;">
                                <img src="${avatarUrl}" alt="${window.SharedUtils.escapeHtml(displayName)}" 
                                     style="width: 100%; height: 100%; object-fit: cover; border-radius: 50%;"
                                     onerror="this.style.display='none'; const initials = '${initials}'; this.parentElement.querySelector('span').textContent = initials || '?';">
                                <span style="position: relative; z-index: 1;"></span>
                            </div>
                            <div class="flex-grow-1" style="min-width: 0;">
                                <div class="fw-semibold text-truncate">${window.SharedUtils.escapeHtml(displayName)}</div>
                                <small class="text-muted">ID: ${user.id}${user.username ? ' • @' + user.username : ''}</small>
                            </div>
                        </label>
                    `;
                }).join('');
            }
        }
        
        const usersCountBadge = document.getElementById('users-count-badge');
        if (usersCountBadge) {
            usersCountBadge.textContent = monitoredUsers.length;
        }
    }
    
    /**
     * Apply the modal selection to the current profile
     */
    function applyModalSelection() {
        // Get selected channels
        const selectedChannelIds = Array.from(
            document.querySelectorAll('#modal-channels-checkboxes input[type="checkbox"]:checked')
        ).map(cb => parseInt(cb.value, 10));
        
        // Get selected users
        const selectedUserIds = Array.from(
            document.querySelectorAll('#modal-users-checkboxes input[type="checkbox"]:checked')
        ).map(cb => parseInt(cb.value, 10));
        
        // Update the appropriate profile's selections
        if (currentProfileTypeForModal === 'alert') {
            selectedChannelsAlert = selectedChannelIds;
            selectedUsersAlert = selectedUserIds;
            updateEntityBadges('alert', selectedChannelIds, selectedUserIds);
        } else if (currentProfileTypeForModal === 'interest') {
            selectedChannelsInterest = selectedChannelIds;
            selectedUsersInterest = selectedUserIds;
            updateEntityBadges('interest', selectedChannelIds, selectedUserIds);
        }
    }
    
    /**
     * Update the badge display for selected entities
     * @param {string} profileType - 'alert' or 'interest'
     * @param {Array<number>} channelIds - Array of channel IDs
     * @param {Array<number>} userIds - Array of user IDs
     */
    function updateEntityBadges(profileType, channelIds, userIds) {
        const badgesContainer = document.getElementById(`entities-badges-${profileType}`);
        const placeholder = document.getElementById(`entities-placeholder-${profileType}`);
        
        if (!badgesContainer || !placeholder) return;
        
        const badges = [];
        
        // Add channel badges
        channelIds.forEach(id => {
            const channel = monitoredChannels.find(c => c.id === id);
            if (channel) {
                const displayName = channel.name || channel.title || channel.username || `Channel ${id}`;
                const initials = displayName.split(' ').map(w => w[0]).slice(0, 2).join('').toUpperCase();
                const isChat = id < 0;
                const prefix = isChat ? "chat" : "user";
                const avatarUrl = `/api/avatar/${prefix}/${Math.abs(id)}`;
                badges.push(`
                    <span class="badge bg-primary d-inline-flex align-items-center gap-2 py-2 px-2" style="font-size: 0.875rem; max-width: 250px;" title="${window.SharedUtils.escapeHtml(displayName)}">
                        <div class="rounded-circle d-flex align-items-center justify-content-center" 
                             style="width: 20px; height: 20px; font-size: 0.65rem; font-weight: 600; color: white; background-color: #0d6efd; position: relative; overflow: hidden; flex-shrink: 0;">
                            <img src="${avatarUrl}" alt="${window.SharedUtils.escapeHtml(displayName)}" 
                                 style="width: 100%; height: 100%; object-fit: cover; border-radius: 50%;"
                                 onerror="this.style.display='none'; this.parentElement.style.backgroundColor='#0d6efd'; const initials = '${initials}'; this.nextElementSibling.textContent = initials || '?';">
                            <span style="position: relative; z-index: 1;"></span>
                        </div>
                        <span class="text-truncate" style="min-width: 0;">${window.SharedUtils.escapeHtml(displayName)}</span>
                        <button type="button" class="btn-close btn-close-white ms-1" style="font-size: 0.65rem; flex-shrink: 0;" 
                                onclick="window.EntitySelector.removeEntity('${profileType}', 'channel', ${id})" aria-label="Remove"></button>
                    </span>
                `);
            }
        });
        
        // Add user badges
        userIds.forEach(id => {
            const user = monitoredUsers.find(u => u.id === id);
            if (user) {
                const displayName = [user.first_name, user.last_name].filter(Boolean).join(' ').trim() 
                    || user.name 
                    || user.username 
                    || `User ${id}`;
                const initials = displayName.split(' ').map(w => w[0]).slice(0, 2).join('').toUpperCase();
                const avatarUrl = `/api/avatar/user/${id}`;
                badges.push(`
                    <span class="badge bg-success d-inline-flex align-items-center gap-2 py-2 px-2" style="font-size: 0.875rem; max-width: 250px;" title="${window.SharedUtils.escapeHtml(displayName)}">
                        <div class="rounded-circle d-flex align-items-center justify-content-center" 
                             style="width: 20px; height: 20px; font-size: 0.65rem; font-weight: 600; color: white; background-color: #6f42c1; position: relative; overflow: hidden; flex-shrink: 0;">
                            <img src="${avatarUrl}" alt="${window.SharedUtils.escapeHtml(displayName)}" 
                                 style="width: 100%; height: 100%; object-fit: cover; border-radius: 50%;"
                                 onerror="this.style.display='none'; this.parentElement.style.backgroundColor='#6f42c1'; const initials = '${initials}'; this.nextElementSibling.textContent = initials || '?';">
                            <span style="position: relative; z-index: 1;"></span>
                        </div>
                        <span class="text-truncate" style="min-width: 0;">${window.SharedUtils.escapeHtml(displayName)}</span>
                        <button type="button" class="btn-close btn-close-white ms-1" style="font-size: 0.65rem; flex-shrink: 0;" 
                                onclick="window.EntitySelector.removeEntity('${profileType}', 'user', ${id})" aria-label="Remove"></button>
                    </span>
                `);
            }
        });
        
        if (badges.length > 0) {
            placeholder.classList.add('d-none');
            badgesContainer.innerHTML = badges.join('');
            badgesContainer.classList.remove('d-none');
        } else {
            placeholder.classList.remove('d-none');
            badgesContainer.innerHTML = '';
            badgesContainer.classList.add('d-none');
        }
    }
    
    /**
     * Filter modal list items by search text
     * @param {string} containerId - ID of the container element
     * @param {string} searchText - Search term
     */
    function filterModalList(containerId, searchText) {
        const container = document.getElementById(containerId);
        if (!container) return;
        
        const items = container.querySelectorAll('.list-group-item');
        const lowerSearch = searchText.toLowerCase();
        
        items.forEach(item => {
            const entityName = item.getAttribute('data-entity-name') || '';
            if (entityName.includes(lowerSearch)) {
                item.style.display = '';
            } else {
                item.style.display = 'none';
            }
        });
    }
    
    // ============= EXPORT PUBLIC API =============
    
    window.EntitySelector = {
        init,
        fetchMonitoredEntities,
        setupEntitySelectionModal,
        getSelectedEntityIds,
        setSelectedEntityIds,
        removeEntity
    };
    
})();
