/**
 * DigestEditor - Utility for extracting digest configuration from UI forms
 * 
 * This module provides standardized extraction of digest configuration from
 * profile edit forms, ensuring consistent structure for both Alert and Interest profiles.
 */

export class DigestEditor {
    /**
     * Extract digest configuration from a form element
     * @param {string} formType - 'alert' or 'interest'
     * @returns {Object} - Digest configuration object with mode, target_channel, schedules
     */
    static extractDigestConfigFromForm(formType) {
        const prefix = formType === 'alert' ? 'alert' : 'interest';
        
        const mode = document.getElementById(`${prefix}-digest-mode`)?.value || 'dm';
        const targetChannel = document.getElementById(`${prefix}-digest-target-channel`)?.value?.trim() || null;
        
        // Extract schedules based on profile type
        let schedules = [];
        
        if (formType === 'alert') {
            schedules = this._extractAlertDigestSchedules();
        } else if (formType === 'interest') {
            schedules = this._extractInterestDigestSchedules();
        }
        
        return {
            mode: mode,
            target_channel: targetChannel,
            schedules: schedules
        };
    }
    
    /**
     * Extract digest schedules from Alert profile form
     * @private
     */
    static _extractAlertDigestSchedules() {
        const schedules = [];
        const rows = document.querySelectorAll('#alert-digest-schedule-container .digest-schedule-row');
        
        rows.forEach(row => {
            const scheduleType = row.querySelector('.digest-schedule-type')?.value;
            if (!scheduleType) return;
            
            const schedule = { schedule: scheduleType };
            
            // Add schedule-type-specific fields
            if (scheduleType === 'daily') {
                const hour = parseInt(row.querySelector('.digest-daily-hour')?.value, 10);
                if (!isNaN(hour)) schedule.daily_hour = hour;
            } else if (scheduleType === 'weekly') {
                const dayOfWeek = parseInt(row.querySelector('.digest-weekly-day')?.value, 10);
                const hour = parseInt(row.querySelector('.digest-weekly-hour')?.value, 10);
                if (!isNaN(dayOfWeek)) schedule.day_of_week = dayOfWeek;
                if (!isNaN(hour)) schedule.weekly_hour = hour;
            }
            
            // Add common fields
            const topN = parseInt(row.querySelector('.digest-top-n')?.value, 10);
            const minScore = parseFloat(row.querySelector('.digest-min-score')?.value);
            
            if (!isNaN(topN)) schedule.top_n = topN;
            if (!isNaN(minScore)) schedule.min_score = minScore;
            
            schedules.push(schedule);
        });
        
        return schedules;
    }
    
    /**
     * Extract digest schedules from Interest profile form
     * @private
     */
    static _extractInterestDigestSchedules() {
        const schedules = [];
        const rows = document.querySelectorAll('#interest-digest-schedule-container .digest-schedule-row');
        
        rows.forEach(row => {
            const scheduleType = row.querySelector('.digest-schedule-type')?.value;
            if (!scheduleType) return;
            
            const schedule = { schedule: scheduleType };
            
            // Add schedule-type-specific fields
            if (scheduleType === 'daily') {
                const hour = parseInt(row.querySelector('.digest-daily-hour')?.value, 10);
                if (!isNaN(hour)) schedule.daily_hour = hour;
            } else if (scheduleType === 'weekly') {
                const dayOfWeek = parseInt(row.querySelector('.digest-weekly-day')?.value, 10);
                const hour = parseInt(row.querySelector('.digest-weekly-hour')?.value, 10);
                if (!isNaN(dayOfWeek)) schedule.day_of_week = dayOfWeek;
                if (!isNaN(hour)) schedule.weekly_hour = hour;
            }
            
            // Add common fields
            const topN = parseInt(row.querySelector('.digest-top-n')?.value, 10);
            const minScore = parseFloat(row.querySelector('.digest-min-score')?.value);
            
            if (!isNaN(topN)) schedule.top_n = topN;
            if (!isNaN(minScore)) schedule.min_score = minScore;
            
            schedules.push(schedule);
        });
        
        return schedules;
    }
}

// Make available globally for legacy code
window.DigestEditor = DigestEditor;
