/**
 * Job Intelligence Agent — Dashboard Client Logic
 *
 * Handles:
 *   - Stats loading
 *   - Job cards rendering (saved + pending)
 *   - Filter/search with debounce
 *   - Status updates
 *   - Settings modal (filter configuration)
 *   - Job detail modal
 *   - Learning stats
 *   - Agent trigger
 *   - Toast notifications
 */

// ═══════════════════════════════════════════
//  STATE
// ═══════════════════════════════════════════

const API = window.location.origin;
let currentTab = 'saved';
let currentStatus = 'all';
let currentSearch = '';
let searchTimer = null;
let settingsData = [];

// ═══════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    loadStats();
    loadJobs();
    loadLearningStats();

    // Auto-refresh stats every 30s
    setInterval(loadStats, 30000);
});

// ═══════════════════════════════════════════
//  STATS
// ═══════════════════════════════════════════

async function loadStats() {
    try {
        const res = await fetch(`${API}/api/stats`);
        const stats = await res.json();

        animateValue('stat-saved-val', stats.total_saved || 0);
        animateValue('stat-applied-val', stats.applied || 0);
        animateValue('stat-interviewing-val', stats.interviewing || 0);
        animateValue('stat-offered-val', stats.offered || 0);
        animateValue('stat-pending-val', stats.pending_review || 0);
        animateValue('stat-feedback-val', stats.feedback_given || 0);
    } catch (e) {
        console.error('Failed to load stats:', e);
    }
}

function animateValue(elementId, target) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const current = parseInt(el.textContent) || 0;
    if (current === target) return;

    const duration = 400;
    const start = performance.now();

    function update(now) {
        const elapsed = now - start;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
        el.textContent = Math.round(current + (target - current) * eased);
        if (progress < 1) requestAnimationFrame(update);
    }
    requestAnimationFrame(update);
}

// ═══════════════════════════════════════════
//  TABS
// ═══════════════════════════════════════════

function switchTab(tab) {
    currentTab = tab;

    // Update tab buttons
    document.querySelectorAll('.tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tab);
    });

    // Update tab content
    document.querySelectorAll('.tab-content').forEach(c => {
        c.classList.remove('active');
    });
    document.getElementById(`tab-${tab}`).classList.add('active');

    // Show/hide filter bar (only for saved tab)
    document.getElementById('filter-bar').style.display =
        tab === 'saved' ? 'flex' : 'none';

    // Load data for the tab
    if (tab === 'saved') loadJobs();
    else if (tab === 'pending') loadPendingJobs();
    else if (tab === 'learning') loadLearningStats();
}

// ═══════════════════════════════════════════
//  SAVED JOBS
// ═══════════════════════════════════════════

async function loadJobs() {
    const grid = document.getElementById('jobs-grid');

    try {
        let url = `${API}/api/jobs?limit=100`;
        if (currentStatus && currentStatus !== 'all') url += `&status=${currentStatus}`;
        if (currentSearch) url += `&search=${encodeURIComponent(currentSearch)}`;

        const res = await fetch(url);
        const jobs = await res.json();

        if (jobs.length === 0) {
            grid.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📭</div>
                    <h3>No jobs found</h3>
                    <p>${currentSearch ? 'Try a different search term' : 'Jobs you approve via Telegram will appear here'}</p>
                </div>`;
            return;
        }

        grid.innerHTML = jobs.map(job => renderJobCard(job)).join('');
    } catch (e) {
        console.error('Failed to load jobs:', e);
        grid.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">❌</div>
                <h3>Error loading jobs</h3>
                <p>${e.message}</p>
            </div>`;
    }
}

function renderJobCard(job) {
    const scoreClass = job.fit_score >= 70 ? 'high' : job.fit_score >= 50 ? 'mid' : 'low';
    const statusClass = job.status || 'saved';

    return `
        <div class="job-card" onclick="openDetail('${job.hash}')">
            <div class="job-card-header">
                <div class="job-card-title">${escapeHtml(job.title)}</div>
                <div class="job-card-company">${escapeHtml(job.company || 'Unknown Company')}</div>
            </div>

            <div class="job-card-meta">
                ${job.location ? `<span class="meta-tag">📍 ${escapeHtml(job.location)}</span>` : ''}
                ${job.salary ? `<span class="meta-tag">💰 ${escapeHtml(job.salary)}</span>` : ''}
                ${job.source ? `<span class="meta-tag source">${escapeHtml(job.source)}</span>` : ''}
                ${job.date_posted ? `<span class="meta-tag">📅 ${escapeHtml(job.date_posted)}</span>` : ''}
            </div>

            <div class="score-bar-container">
                <div class="score-bar-header">
                    <span class="score-label">Fit Score</span>
                    <span class="score-value ${scoreClass}">${job.fit_score}/100</span>
                </div>
                <div class="score-bar">
                    <div class="score-bar-fill ${scoreClass}" style="width: ${job.fit_score}%"></div>
                </div>
            </div>

            ${job.match_reason ? `<div class="job-card-reason">${escapeHtml(job.match_reason)}</div>` : ''}

            <div class="job-card-actions" onclick="event.stopPropagation()">
                <select class="status-select" onchange="updateJobStatus('${job.hash}', this.value)">
                    <option value="saved" ${statusClass === 'saved' ? 'selected' : ''}>💾 Saved</option>
                    <option value="applied" ${statusClass === 'applied' ? 'selected' : ''}>📤 Applied</option>
                    <option value="interviewing" ${statusClass === 'interviewing' ? 'selected' : ''}>🎤 Interview</option>
                    <option value="offered" ${statusClass === 'offered' ? 'selected' : ''}>🎉 Offered</option>
                    <option value="rejected" ${statusClass === 'rejected' ? 'selected' : ''}>❌ Rejected</option>
                </select>
                ${job.url ? `<a href="${escapeHtml(job.url)}" target="_blank" class="btn btn-sm btn-primary" onclick="event.stopPropagation()">🔗 Apply</a>` : ''}
            </div>
        </div>`;
}

// ═══════════════════════════════════════════
//  PENDING JOBS
// ═══════════════════════════════════════════

async function loadPendingJobs() {
    const grid = document.getElementById('pending-grid');

    try {
        const res = await fetch(`${API}/api/pending`);
        const jobs = await res.json();

        if (jobs.length === 0) {
            grid.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">⏳</div>
                    <h3>No pending jobs</h3>
                    <p>Jobs awaiting your review will appear here</p>
                </div>`;
            return;
        }

        grid.innerHTML = jobs.map(job => renderPendingCard(job)).join('');
    } catch (e) {
        console.error('Failed to load pending jobs:', e);
    }
}

function renderPendingCard(job) {
    const scoreClass = job.fit_score >= 70 ? 'high' : job.fit_score >= 50 ? 'mid' : 'low';

    return `
        <div class="job-card">
            <div class="job-card-header">
                <div class="job-card-title">${escapeHtml(job.title)}</div>
                <div class="job-card-company">${escapeHtml(job.company || 'Unknown Company')}</div>
            </div>

            <div class="job-card-meta">
                ${job.location ? `<span class="meta-tag">📍 ${escapeHtml(job.location)}</span>` : ''}
                ${job.salary ? `<span class="meta-tag">💰 ${escapeHtml(job.salary)}</span>` : ''}
                ${job.source ? `<span class="meta-tag source">${escapeHtml(job.source)}</span>` : ''}
            </div>

            <div class="score-bar-container">
                <div class="score-bar-header">
                    <span class="score-label">Fit Score</span>
                    <span class="score-value ${scoreClass}">${job.fit_score}/100</span>
                </div>
                <div class="score-bar">
                    <div class="score-bar-fill ${scoreClass}" style="width: ${job.fit_score}%"></div>
                </div>
            </div>

            ${job.match_reason ? `<div class="job-card-reason">${escapeHtml(job.match_reason)}</div>` : ''}

            ${job.red_flags && job.red_flags.toLowerCase() !== 'none' && job.red_flags.toLowerCase() !== 'none detected'
                ? `<div class="job-card-reason" style="color: var(--accent-yellow)">⚠️ ${escapeHtml(job.red_flags)}</div>`
                : ''}

            <div class="pending-actions" onclick="event.stopPropagation()">
                <button class="btn btn-sm btn-success" onclick="approveJob('${job.hash}')">👍 Save</button>
                <button class="btn btn-sm btn-danger" onclick="rejectJob('${job.hash}')">👎 Pass</button>
                ${job.url ? `<a href="${escapeHtml(job.url)}" target="_blank" class="btn btn-sm btn-ghost">🔗 View</a>` : ''}
            </div>
        </div>`;
}

async function approveJob(hash) {
    try {
        const res = await fetch(`${API}/api/jobs/${hash}/approve`, { method: 'POST' });
        if (res.ok) {
            showToast('Job saved to tracker! 👍', 'success');
            loadPendingJobs();
            loadStats();
        } else {
            showToast('Failed to approve job', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

async function rejectJob(hash) {
    try {
        const res = await fetch(`${API}/api/jobs/${hash}/reject`, { method: 'POST' });
        if (res.ok) {
            showToast('Job rejected 👎', 'info');
            loadPendingJobs();
            loadStats();
        } else {
            showToast('Failed to reject job', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// ═══════════════════════════════════════════
//  JOB DETAIL MODAL
// ═══════════════════════════════════════════

async function openDetail(hash) {
    const overlay = document.getElementById('detail-overlay');
    const titleEl = document.getElementById('detail-title');
    const bodyEl = document.getElementById('detail-body');

    overlay.classList.add('open');
    bodyEl.innerHTML = '<p style="color: var(--text-muted)">Loading...</p>';

    try {
        const res = await fetch(`${API}/api/jobs/${hash}`);
        const job = await res.json();

        titleEl.textContent = job.title;
        bodyEl.innerHTML = `
            <div class="detail-section">
                <h4>Company & Location</h4>
                <p>${escapeHtml(job.company || 'N/A')} • ${escapeHtml(job.location || 'N/A')}</p>
            </div>

            <div class="detail-section">
                <h4>Salary</h4>
                <p>${escapeHtml(job.salary || 'Not disclosed')}</p>
            </div>

            <div class="detail-section">
                <h4>Source</h4>
                <p>${escapeHtml(job.source || 'N/A')} • Posted: ${escapeHtml(job.date_posted || 'N/A')}</p>
            </div>

            <div class="detail-section">
                <h4>Match Analysis</h4>
                <p>Score: <strong>${job.fit_score}/100</strong></p>
                <p>${escapeHtml(job.role_match || '')}</p>
                <p>${escapeHtml(job.match_reason || '')}</p>
            </div>

            <div class="detail-section">
                <h4>Description</h4>
                <div class="detail-description">${escapeHtml(job.description || 'No description available')}</div>
            </div>

            <div class="detail-section detail-notes">
                <h4>Notes</h4>
                <textarea id="detail-notes-input" placeholder="Add notes about this job...">${escapeHtml(job.notes || '')}</textarea>
                <button class="btn btn-sm btn-ghost" style="margin-top: 0.5rem"
                    onclick="saveNotes('${hash}')">💾 Save Notes</button>
            </div>

            <div class="detail-actions">
                ${job.url ? `<a href="${escapeHtml(job.url)}" target="_blank" class="btn btn-primary">🔗 Apply Now</a>` : ''}
                <select class="status-select" id="detail-status-select"
                    onchange="updateJobStatus('${hash}', this.value)">
                    <option value="saved" ${job.status === 'saved' ? 'selected' : ''}>💾 Saved</option>
                    <option value="applied" ${job.status === 'applied' ? 'selected' : ''}>📤 Applied</option>
                    <option value="interviewing" ${job.status === 'interviewing' ? 'selected' : ''}>🎤 Interviewing</option>
                    <option value="offered" ${job.status === 'offered' ? 'selected' : ''}>🎉 Offered</option>
                    <option value="rejected" ${job.status === 'rejected' ? 'selected' : ''}>❌ Rejected</option>
                </select>
                <button class="btn btn-sm btn-danger" onclick="deleteJob('${hash}')">🗑️ Delete</button>
            </div>`;
    } catch (e) {
        bodyEl.innerHTML = `<p style="color: var(--accent-red)">Error loading job: ${e.message}</p>`;
    }
}

function closeDetailModal() {
    document.getElementById('detail-overlay').classList.remove('open');
}

function closeDetail(event) {
    if (event.target === event.currentTarget) closeDetailModal();
}

async function saveNotes(hash) {
    const notes = document.getElementById('detail-notes-input').value;
    try {
        const res = await fetch(`${API}/api/jobs/${hash}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ notes }),
        });
        if (res.ok) showToast('Notes saved!', 'success');
        else showToast('Failed to save notes', 'error');
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

async function deleteJob(hash) {
    if (!confirm('Delete this job from your tracker?')) return;
    try {
        const res = await fetch(`${API}/api/jobs/${hash}`, { method: 'DELETE' });
        if (res.ok) {
            showToast('Job deleted', 'info');
            closeDetailModal();
            loadJobs();
            loadStats();
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// ═══════════════════════════════════════════
//  STATUS UPDATE
// ═══════════════════════════════════════════

async function updateJobStatus(hash, status) {
    try {
        const res = await fetch(`${API}/api/jobs/${hash}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status }),
        });
        if (res.ok) {
            showToast(`Status updated: ${status}`, 'success');
            loadStats();
        } else {
            showToast('Failed to update status', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// ═══════════════════════════════════════════
//  FILTER & SEARCH
// ═══════════════════════════════════════════

function filterByStatus(status) {
    currentStatus = status;
    document.querySelectorAll('.chip').forEach(c => {
        c.classList.toggle('active', c.dataset.status === status);
    });
    loadJobs();
}

function debouncedSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
        currentSearch = document.getElementById('search-input').value.trim();
        loadJobs();
    }, 300);
}

// ═══════════════════════════════════════════
//  SETTINGS MODAL
// ═══════════════════════════════════════════

async function toggleSettings() {
    const overlay = document.getElementById('settings-overlay');
    const isOpen = overlay.classList.contains('open');

    if (isOpen) {
        overlay.classList.remove('open');
    } else {
        overlay.classList.add('open');
        await loadSettings();
    }
}

function closeSettings(event) {
    if (event.target === event.currentTarget) {
        document.getElementById('settings-overlay').classList.remove('open');
    }
}

async function loadSettings() {
    const body = document.getElementById('settings-body');

    try {
        const res = await fetch(`${API}/api/filters`);
        settingsData = await res.json();

        body.innerHTML = settingsData.map(filter => renderSettingField(filter)).join('');
    } catch (e) {
        body.innerHTML = `<p style="color: var(--accent-red)">Error loading settings: ${e.message}</p>`;
    }
}

function renderSettingField(filter) {
    const key = filter.key;
    const value = filter.value;
    const desc = filter.description || '';
    const inputId = `setting-${key}`;

    // Sources: render as checkboxes
    if (key === 'sources') {
        const available = settingsData.find(f => f.key === 'sources_available');
        const allSources = available ? available.value : ['indeed', 'linkedin', 'glassdoor', 'google', 'zip_recruiter'];
        const active = value || [];

        return `
            <div class="setting-group">
                <label>Job Sources</label>
                <div class="hint">${escapeHtml(desc)}</div>
                <div class="source-toggles" id="${inputId}">
                    ${allSources.map(s => `
                        <div class="source-toggle">
                            <input type="checkbox" id="src-${s}" value="${s}"
                                ${active.includes(s) ? 'checked' : ''}>
                            <label for="src-${s}">${s}</label>
                        </div>
                    `).join('')}
                </div>
            </div>`;
    }

    // Skip internal-only keys
    if (key === 'sources_available') return '';

    // Arrays: render as comma-separated textarea
    if (Array.isArray(value)) {
        return `
            <div class="setting-group">
                <label>${formatLabel(key)}</label>
                <div class="hint">${escapeHtml(desc)} (comma-separated)</div>
                <textarea id="${inputId}">${value.join(', ')}</textarea>
            </div>`;
    }

    // Numbers
    if (typeof value === 'number') {
        return `
            <div class="setting-group">
                <label>${formatLabel(key)}</label>
                <div class="hint">${escapeHtml(desc)}</div>
                <input type="number" id="${inputId}" value="${value}">
            </div>`;
    }

    // Strings
    return `
        <div class="setting-group">
            <label>${formatLabel(key)}</label>
            <div class="hint">${escapeHtml(desc)}</div>
            <input type="text" id="${inputId}" value="${escapeHtml(String(value))}">
        </div>`;
}

async function saveAllSettings() {
    let saved = 0;
    let errors = 0;

    for (const filter of settingsData) {
        const key = filter.key;
        if (key === 'sources_available') continue;

        const inputId = `setting-${key}`;
        let newValue;

        if (key === 'sources') {
            // Collect checked source checkboxes
            const container = document.getElementById(inputId);
            const checked = container.querySelectorAll('input:checked');
            newValue = Array.from(checked).map(cb => cb.value);
        } else {
            const el = document.getElementById(inputId);
            if (!el) continue;

            if (Array.isArray(filter.value)) {
                newValue = el.value.split(',').map(s => s.trim()).filter(Boolean);
            } else if (typeof filter.value === 'number') {
                newValue = parseFloat(el.value) || 0;
            } else {
                newValue = el.value;
            }
        }

        try {
            const res = await fetch(`${API}/api/filters/${key}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ value: newValue }),
            });
            if (res.ok) saved++;
            else errors++;
        } catch (e) {
            errors++;
        }
    }

    if (errors === 0) {
        showToast(`All ${saved} settings saved!`, 'success');
        toggleSettings();
    } else {
        showToast(`Saved ${saved}, ${errors} failed`, 'error');
    }
}

// ═══════════════════════════════════════════
//  LEARNING STATS
// ═══════════════════════════════════════════

async function loadLearningStats() {
    const container = document.getElementById('learning-stats');

    try {
        const res = await fetch(`${API}/api/learning`);
        const stats = await res.json();

        const simProgress = Math.min(100, (stats.total_feedback / stats.min_for_similarity) * 100);
        const classProgress = Math.min(100, (stats.total_feedback / stats.min_for_classifier) * 100);

        container.innerHTML = `
            <div class="learning-stat-row">
                <span class="learning-stat-key">Total Feedback</span>
                <span class="learning-stat-val">${stats.total_feedback}</span>
            </div>
            <div class="learning-stat-row">
                <span class="learning-stat-key">Liked Jobs</span>
                <span class="learning-stat-val" style="color: var(--accent-green)">${stats.liked} 👍</span>
            </div>
            <div class="learning-stat-row">
                <span class="learning-stat-key">Disliked Jobs</span>
                <span class="learning-stat-val" style="color: var(--accent-red)">${stats.disliked} 👎</span>
            </div>
            <div class="learning-stat-row">
                <span class="learning-stat-key">Similarity Scoring</span>
                <span class="learning-stat-val ${stats.similarity_active ? 'active' : 'inactive'}">
                    ${stats.similarity_active ? '✅ Active' : '⏳ Needs ' + (stats.min_for_similarity - stats.total_feedback) + ' more'}
                </span>
            </div>
            <div class="learning-stat-row">
                <span class="learning-stat-key">ML Classifier</span>
                <span class="learning-stat-val ${stats.classifier_active ? 'active' : 'inactive'}">
                    ${stats.classifier_active ? '✅ Active' : '⏳ Needs ' + (stats.min_for_classifier - stats.total_feedback) + ' more'}
                </span>
            </div>

            <div class="progress-section">
                <h4>Similarity Learning Progress</h4>
                <div class="progress-bar-bg">
                    <div class="progress-bar-fill" style="width: ${simProgress}%"></div>
                </div>
                <div class="progress-label">${stats.total_feedback}/${stats.min_for_similarity} feedback needed</div>
            </div>

            <div class="progress-section">
                <h4>ML Classifier Progress</h4>
                <div class="progress-bar-bg">
                    <div class="progress-bar-fill" style="width: ${classProgress}%"></div>
                </div>
                <div class="progress-label">${stats.total_feedback}/${stats.min_for_classifier} feedback needed</div>
            </div>`;
    } catch (e) {
        container.innerHTML = `<p style="color: var(--accent-red)">Error: ${e.message}</p>`;
    }
}

// ═══════════════════════════════════════════
//  AGENT TRIGGER
// ═══════════════════════════════════════════

async function triggerAgent() {
    const btn = document.getElementById('btn-trigger');
    btn.disabled = true;
    btn.innerHTML = '<span>⏳</span> Running...';

    try {
        const res = await fetch(`${API}/api/trigger`, { method: 'POST' });
        if (res.ok) {
            showToast('Agent run started! Check Telegram for results.', 'success');
        } else {
            showToast('Failed to trigger agent', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }

    // Re-enable after 30s
    setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = '<span>🚀</span> Run Agent';
    }, 30000);
}

// ═══════════════════════════════════════════
//  TOAST NOTIFICATIONS
// ═══════════════════════════════════════════

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ═══════════════════════════════════════════
//  UTILITIES
// ═══════════════════════════════════════════

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function formatLabel(key) {
    return key
        .replace(/_/g, ' ')
        .replace(/\b\w/g, l => l.toUpperCase());
}
