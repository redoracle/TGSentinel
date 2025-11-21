/**
 * Shared Utility Functions for Profile Management
 * 
 * Pure utility functions with no profile-specific logic.
 * Used by both Alert Profiles and Interest Profiles.
 */

/**
 * Parse comma-separated values into array
 * @param {string} str - Comma-separated string
 * @returns {string[]} Array of trimmed values
 */
function parseCSV(str) {
    if (!str || typeof str !== 'string') return [];
    return str.split(",").map(s => s.trim()).filter(s => s);
}

/**
 * Parse textarea lines into array
 * @param {string} text - Multi-line text
 * @returns {string[]} Array of non-empty lines
 */
function parseTextareaLines(text) {
    if (!text || text.trim() === "") return [];
    return text.split('\n')
        .map(line => line.trim())
        .filter(line => line.length > 0);
}

/**
 * Escape HTML special characters
 * @param {string} text - Text to escape
 * @returns {string} HTML-escaped text
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Show toast notification
 * @param {string} message - Message to display
 * @param {string} type - Toast type: 'success', 'error', 'warning', 'info'
 */
function showToast(message, type = 'info') {
    // Check if toast container exists, create if not
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'position-fixed bottom-0 end-0 p-3';
        container.style.zIndex = '11';
        document.body.appendChild(container);
    }

    // Create toast element
    const toastId = `toast-${Date.now()}`;
    const bgClass = {
        'success': 'bg-success',
        'error': 'bg-danger',
        'warning': 'bg-warning',
        'info': 'bg-info'
    }[type] || 'bg-info';

    const toastHtml = `
        <div id="${toastId}" class="toast align-items-center text-white ${bgClass} border-0" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="d-flex">
                <div class="toast-body">
                    ${escapeHtml(message)}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        </div>
    `;

    container.insertAdjacentHTML('beforeend', toastHtml);
    
    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement, { delay: 3000 });
    toast.show();

    // Remove from DOM after hide
    toastElement.addEventListener('hidden.bs.modal', () => {
        toastElement.remove();
    });
}

/**
 * Apply glass backdrop effect to modal
 * Should be called when modal is shown
 */
function applyGlassBackdrop() {
    setTimeout(() => {
        const backdrop = document.querySelector('.modal-backdrop');
        if (backdrop && !backdrop.classList.contains('glass-backdrop')) {
            backdrop.classList.add('glass-backdrop');
        }
    }, 50);
}

/**
 * Filter list items by search text
 * @param {string} containerId - ID of container element
 * @param {string} searchText - Text to search for
 */
function filterList(containerId, searchText) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const items = container.querySelectorAll('.list-group-item, .form-check');
    const lowerSearch = searchText.toLowerCase();

    items.forEach(item => {
        const text = item.textContent.toLowerCase();
        if (text.includes(lowerSearch)) {
            item.style.display = '';
        } else {
            item.style.display = 'none';
        }
    });
}

/**
 * Format date for display
 * @param {string|Date} date - Date to format
 * @param {object} options - Intl.DateTimeFormat options
 * @returns {string} Formatted date string
 */
function formatDate(date, options = { month: 'short', day: 'numeric' }) {
    if (!date) return 'Never';
    try {
        return new Date(date).toLocaleDateString('en-US', options);
    } catch (e) {
        return 'Invalid date';
    }
}

/**
 * Debounce function execution
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in milliseconds
 * @returns {Function} Debounced function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Export all utilities to window.SharedUtils for use by other modules
window.SharedUtils = {
    parseCSV,
    parseTextareaLines,
    escapeHtml,
    showToast,
    filterList,
    formatDate,
    debounce,
    applyGlassBackdrop
};
