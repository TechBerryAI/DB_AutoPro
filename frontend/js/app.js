const API_BASE = 'http://localhost:8000/api/v1';
let token = localStorage.getItem('dap_token') || '';

async function apiFetch(path, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

document.getElementById('login-btn').addEventListener('click', async () => {
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    try {
        const data = await apiFetch('/auth/login', {
            method: 'POST',
            body: JSON.stringify({ username, password }),
        });
        token = data.access_token;
        localStorage.setItem('dap_token', token);
        document.getElementById('auth-status').textContent = `Logged in as ${username}`;
        loadDashboard();
    } catch (e) {
        document.getElementById('auth-status').textContent = 'Login failed';
    }
});

document.querySelectorAll('[data-view]').forEach(link => {
    link.addEventListener('click', (e) => {
        e.preventDefault();
        const view = link.dataset.view;
        document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        document.querySelectorAll('[data-view]').forEach(l => l.classList.remove('active'));
        document.getElementById(view)?.classList.add('active');
        link.classList.add('active');
        if (view === 'dashboard') loadDashboard();
        if (view === 'backups') loadBackups();
    });
});

async function loadDashboard() {
    if (!token) return;
    try {
        const stats = await apiFetch('/monitoring/dashboard');
        const grid = document.getElementById('stats-grid');
        grid.innerHTML = '';
        const items = [
            ['Total Backups', stats.total_backups],
            ['Successful', stats.successful_backups],
            ['Patches', stats.patch_count],
            ['Storage (MB)', stats.storage_used_mb.toFixed(1)],
            ['Connections', stats.database_connections],
        ];
        items.forEach(([label, value]) => {
            grid.innerHTML += `<div class="stat-card"><div class="value">${value}</div><div class="label">${label}</div></div>`;
        });
    } catch (e) { console.error(e); }
}

async function loadBackups() {
    if (!token) return;
    try {
        const backups = await apiFetch('/backups/');
        const tbody = document.querySelector('#backups-table tbody');
        tbody.innerHTML = backups.map(b =>
            `<tr><td>${b.name}</td><td>${b.size_bytes}</td><td>${b.created_at}</td></tr>`
        ).join('');
    } catch (e) { console.error(e); }
}

document.getElementById('refresh-backups')?.addEventListener('click', loadBackups);

document.getElementById('run-health')?.addEventListener('click', async () => {
    if (!token) return;
    try {
        const results = await apiFetch('/monitoring/health');
        document.getElementById('health-results').textContent = JSON.stringify(results, null, 2);
    } catch (e) { console.error(e); }
});

if (token) {
    document.getElementById('auth-status').textContent = 'Authenticated';
    loadDashboard();
}
