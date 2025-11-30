(function(){
    'use strict';

    async function downloadSession() {
        try {
            const resp = await fetch('/api/session/download');
            if (!resp.ok) {
                const data = await resp.json().catch(() => ({}));
                throw new Error(data.message || `HTTP ${resp.status}`);
            }
            const blob = await resp.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'tgsentinel.session';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            if (window.showToast) {
                window.showToast('Session downloaded', 'success');
            }
        } catch (err) {
            console.error('Failed to download session:', err);
            if (window.showToast) {
                window.showToast(`Failed to download session: ${err.message}`, 'error');
            }
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        const downloadBtn = document.getElementById('btn-download-session');
        if (downloadBtn) {
            downloadBtn.addEventListener('click', downloadSession);
        }
    });
})();
