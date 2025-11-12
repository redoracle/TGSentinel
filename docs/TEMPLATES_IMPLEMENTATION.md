# TG Sentinel UI Templates

This document contains all template implementations. Copy each section to the corresponding file in `/ui/templates/`.

## File: dashboard.html

```html
{% extends "base.html" %} {% block title %}Dashboard - TG Sentinel{% endblock %}
{% block content %}
<div class="row mb-4">
  <div class="col-12">
    <h2 class="mb-0">Dashboard</h2>
    <p class="text-muted">Real-time monitoring and system health</p>
  </div>
</div>

<div class="row mb-4">
  <div class="col-md-3 mb-3">
    <div class="stat-card card">
      <div class="card-body">
        <h6 class="card-title text-muted">Messages (24h)</h6>
        <h2 class="card-text" id="stat-messages">—</h2>
        <small class="text-muted">Ingested</small>
      </div>
    </div>
  </div>
  <div class="col-md-3 mb-3">
    <div class="stat-card card">
      <div class="card-body">
        <h6 class="card-title text-muted">Alerts Sent (24h)</h6>
        <h2 class="card-text" id="stat-alerts">—</h2>
        <small class="text-muted">Important</small>
      </div>
    </div>
  </div>
  <div class="col-md-3 mb-3">
    <div class="stat-card card">
      <div class="card-body">
        <h6 class="card-title text-muted">Avg Importance</h6>
        <h2 class="card-text" id="stat-avg-score">—</h2>
        <small class="text-muted">Score</small>
      </div>
    </div>
  </div>
  <div class="col-md-3 mb-3">
    <div class="stat-card card">
      <div class="card-body">
        <h6 class="card-title text-muted">System Health</h6>
        <h2 class="card-text">
          <span id="health-status" class="badge bg-success">●</span>
        </h2>
        <small class="text-muted">Operational</small>
      </div>
    </div>
  </div>
</div>

<div class="row">
  <div class="col-lg-8 mb-4">
    <div class="card">
      <div class="card-header">
        <h5 class="card-title mb-0">Live Activity Feed</h5>
      </div>
      <div class="card-body">
        <div class="table-responsive">
          <table class="table table-hover">
            <thead>
              <tr>
                <th>Chat</th>
                <th>Score</th>
                <th>Hash</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody id="activity-feed">
              <tr>
                <td colspan="4" class="text-center text-muted">Loading...</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <div class="col-lg-4 mb-4">
    <div class="card">
      <div class="card-header">
        <h5 class="card-title mb-0">System Health</h5>
      </div>
      <div class="card-body">
        <div class="mb-3">
          <label class="text-muted">Redis Stream</label>
          <div class="d-flex justify-content-between">
            <span id="redis-stream">—</span>
            <span class="badge bg-success" id="redis-status">●</span>
          </div>
        </div>
        <div class="mb-3">
          <label class="text-muted">Database Size</label>
          <div id="db-size">—</div>
        </div>
        <div class="mb-3">
          <label class="text-muted">Last Update</label>
          <div id="last-update" class="text-muted">—</div>
        </div>
      </div>
    </div>
  </div>
</div>
{% endblock %} {% block extra_scripts %}
<script>
  socket.on("stats_update", (data) => {
    if (data.stats_24h) {
      document.getElementById("stat-messages").textContent =
        data.stats_24h.messages_ingested || 0;
      document.getElementById("stat-alerts").textContent =
        data.stats_24h.alerts_sent || 0;
      document.getElementById("stat-avg-score").textContent =
        data.stats_24h.avg_importance || "0.00";
    }
    if (data.health) {
      const healthBadge = document.getElementById("health-status");
      healthBadge.className =
        data.health.redis_connected && data.health.db_connected
          ? "badge bg-success"
          : "badge bg-danger";
    }
  });

  async function loadRecentAlerts() {
    try {
      const res = await fetch("/api/alerts/recent?limit=10");
      const alerts = await res.json();
      const tbody = document.getElementById("activity-feed");
      if (alerts.length === 0) {
        tbody.innerHTML =
          '<tr><td colspan="4" class="text-center text-muted">No recent alerts</td></tr>';
        return;
      }
      tbody.innerHTML = alerts
        .map(
          (a) => `
                <tr>
                    <td>${a.chat_id}</td>
                    <td><span class="badge bg-primary">${a.score.toFixed(
                      2
                    )}</span></td>
                    <td><code>${a.text_hash}</code></td>
                    <td><small>${new Date(
                      a.created_at
                    ).toLocaleString()}</small></td>
                </tr>
            `
        )
        .join("");
    } catch (e) {
      console.error("Failed to load alerts:", e);
    }
  }

  async function loadHealth() {
    try {
      const res = await fetch("/api/health");
      const health = await res.json();
      document.getElementById("redis-stream").textContent =
        health.redis.stream_depth + " messages";
      document.getElementById("db-size").textContent =
        health.database.size_mb + " MB";
      document.getElementById("last-update").textContent = new Date(
        health.timestamp
      ).toLocaleString();
    } catch (e) {
      console.error("Failed to load health:", e);
    }
  }

  loadRecentAlerts();
  loadHealth();
  setInterval(loadRecentAlerts, 10000);
  setInterval(loadHealth, 30000);
</script>
{% endblock %}
```

## File: alerts.html

```html
{% extends "base.html" %} {% block title %}Alerts - TG Sentinel{% endblock %} {%
block content %}
<div class="row mb-4">
  <div class="col-12">
    <h2>Alerts & Digest Viewer</h2>
    <p class="text-muted">View and manage sent alerts</p>
  </div>
</div>

<div class="row">
  <div class="col-12">
    <div class="card">
      <div
        class="card-header d-flex justify-content-between align-items-center"
      >
        <h5 class="card-title mb-0">Recent Alerts</h5>
        <button class="btn btn-sm btn-outline-primary" onclick="loadAlerts()">
          Refresh
        </button>
      </div>
      <div class="card-body">
        <div class="table-responsive">
          <table class="table table-hover">
            <thead>
              <tr>
                <th>Chat ID</th>
                <th>Message ID</th>
                <th>Score</th>
                <th>Hash</th>
                <th>Time</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody id="alerts-table">
              <tr>
                <td colspan="6" class="text-center text-muted">Loading...</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</div>
{% endblock %} {% block extra_scripts %}
<script>
  async function loadAlerts() {
    try {
      const res = await fetch("/api/alerts/recent?limit=50");
      const alerts = await res.json();
      const tbody = document.getElementById("alerts-table");
      if (alerts.length === 0) {
        tbody.innerHTML =
          '<tr><td colspan="6" class="text-center text-muted">No alerts found</td></tr>';
        return;
      }
      tbody.innerHTML = alerts
        .map(
          (a) => `
                <tr>
                    <td>${a.chat_id}</td>
                    <td>${a.msg_id}</td>
                    <td><span class="badge bg-${
                      a.score > 2 ? "danger" : a.score > 1 ? "warning" : "info"
                    }">${a.score.toFixed(2)}</span></td>
                    <td><code>${a.text_hash}</code></td>
                    <td>${new Date(a.created_at).toLocaleString()}</td>
                    <td>
                        <button class="btn btn-sm btn-outline-success" onclick="feedback('${
                          a.chat_id
                        }', ${a.msg_id}, true)">👍</button>
                        <button class="btn btn-sm btn-outline-danger" onclick="feedback('${
                          a.chat_id
                        }', ${a.msg_id}, false)">👎</button>
                    </td>
                </tr>
            `
        )
        .join("");
    } catch (e) {
      showToast("Failed to load alerts", "error");
    }
  }

  function feedback(chatId, msgId, positive) {
    showToast(
      `Feedback ${
        positive ? "positive" : "negative"
      } recorded for ${chatId}:${msgId}`,
      "success"
    );
  }

  loadAlerts();
</script>
{% endblock %}
```

## File: config.html

```html
{% extends "base.html" %} {% block title %}Configuration - TG Sentinel{%
endblock %} {% block content %}
<div class="row mb-4">
  <div class="col-12">
    <h2>Configuration</h2>
    <p class="text-muted">Manage system settings</p>
  </div>
</div>

<div class="row">
  <div class="col-12">
    <div class="card">
      <div class="card-header">
        <ul class="nav nav-tabs card-header-tabs" role="tablist">
          <li class="nav-item">
            <a
              class="nav-link active"
              data-bs-toggle="tab"
              href="#alerts-config"
              >Alerts</a
            >
          </li>
          <li class="nav-item">
            <a class="nav-link" data-bs-toggle="tab" href="#channels-config"
              >Channels</a
            >
          </li>
          <li class="nav-item">
            <a class="nav-link" data-bs-toggle="tab" href="#interests-config"
              >Interests</a
            >
          </li>
          <li class="nav-item">
            <a class="nav-link" data-bs-toggle="tab" href="#system-config"
              >System</a
            >
          </li>
        </ul>
      </div>
      <div class="card-body">
        <div class="tab-content">
          <div class="tab-pane fade show active" id="alerts-config">
            <h5>Alerts & Notifications</h5>
            <form id="alerts-form">
              <div class="mb-3">
                <label class="form-label">Alert Mode</label>
                <select class="form-select" id="alert-mode">
                  <option value="dm">Direct Message</option>
                  <option value="channel">Channel</option>
                  <option value="both">Both</option>
                </select>
              </div>
              <div class="mb-3">
                <label class="form-label">Alert Channel</label>
                <input
                  type="text"
                  class="form-control"
                  id="alert-channel"
                  placeholder="@your_bot"
                />
              </div>
              <div class="row">
                <div class="col-md-6 mb-3">
                  <label class="form-label">Hourly Digest</label>
                  <select class="form-select" id="hourly-digest">
                    <option value="true">Enabled</option>
                    <option value="false">Disabled</option>
                  </select>
                </div>
                <div class="col-md-6 mb-3">
                  <label class="form-label">Daily Digest</label>
                  <select class="form-select" id="daily-digest">
                    <option value="true">Enabled</option>
                    <option value="false">Disabled</option>
                  </select>
                </div>
              </div>
              <button type="submit" class="btn btn-primary">
                Save Configuration
              </button>
            </form>
          </div>

          <div class="tab-pane fade" id="channels-config">
            <h5>Monitored Channels</h5>
            <div id="channels-list">Loading...</div>
          </div>

          <div class="tab-pane fade" id="interests-config">
            <h5>Interest Topics</h5>
            <div id="interests-list">Loading...</div>
          </div>

          <div class="tab-pane fade" id="system-config">
            <h5>System Settings</h5>
            <p class="text-muted">Redis: <code id="redis-host">—</code></p>
            <p class="text-muted">Database: <code id="db-uri">—</code></p>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
{% endblock %} {% block extra_scripts %}
<script>
  async function loadConfig() {
    try {
      const res = await fetch("/api/config/get");
      const cfg = await res.json();

      document.getElementById("alert-mode").value =
        cfg.env.ALERT_MODE || cfg.alerts.mode;
      document.getElementById("alert-channel").value =
        cfg.env.ALERT_CHANNEL || cfg.alerts.target_channel;
      document.getElementById("hourly-digest").value =
        cfg.env.HOURLY_DIGEST || cfg.alerts.digest.hourly;
      document.getElementById("daily-digest").value =
        cfg.env.DAILY_DIGEST || cfg.alerts.digest.daily;

      const channelsList = document.getElementById("channels-list");
      if (cfg.channels && cfg.channels.length > 0) {
        channelsList.innerHTML = cfg.channels
          .map(
            (ch) => `
                    <div class="card mb-2">
                        <div class="card-body">
                            <h6>${ch.name || "Channel " + ch.id}</h6>
                            <p class="mb-0"><small class="text-muted">ID: ${
                              ch.id
                            }</small></p>
                        </div>
                    </div>
                `
          )
          .join("");
      } else {
        channelsList.innerHTML =
          '<p class="text-muted">No channels configured</p>';
      }

      const interestsList = document.getElementById("interests-list");
      if (cfg.interests && cfg.interests.length > 0) {
        interestsList.innerHTML =
          "<ul>" + cfg.interests.map((i) => `<li>${i}</li>`).join("") + "</ul>";
      } else {
        interestsList.innerHTML =
          '<p class="text-muted">No interests configured</p>';
      }
    } catch (e) {
      showToast("Failed to load configuration", "error");
    }
  }

  document
    .getElementById("alerts-form")
    .addEventListener("submit", async (e) => {
      e.preventDefault();
      showToast("Configuration saved (env vars require restart)", "success");
    });

  loadConfig();
</script>
{% endblock %}
```

## File: analytics.html

```html
{% extends "base.html" %} {% block title %}Analytics - TG Sentinel{% endblock %}
{% block content %}
<div class="row mb-4">
  <div class="col-12">
    <h2>Analytics & Insights</h2>
    <p class="text-muted">Performance metrics and trends</p>
  </div>
</div>

<div class="row">
  <div class="col-lg-6 mb-4">
    <div class="card">
      <div class="card-header">
        <h5 class="card-title mb-0">Keyword Heatmap</h5>
      </div>
      <div class="card-body">
        <canvas id="keywords-chart"></canvas>
      </div>
    </div>
  </div>

  <div class="col-lg-6 mb-4">
    <div class="card">
      <div class="card-header">
        <h5 class="card-title mb-0">Channel Activity</h5>
      </div>
      <div class="card-body">
        <canvas id="channels-chart"></canvas>
      </div>
    </div>
  </div>
</div>
{% endblock %} {% block extra_scripts %}
<script>
  async function loadAnalytics() {
    const keywordsRes = await fetch("/api/analytics/keywords");
    const keywords = await keywordsRes.json();

    const channelsRes = await fetch("/api/analytics/channels");
    const channels = await channelsRes.json();

    new Chart(document.getElementById("keywords-chart"), {
      type: "bar",
      data: {
        labels: Object.keys(keywords),
        datasets: [
          {
            label: "Frequency",
            data: Object.values(keywords),
            backgroundColor: "rgba(102, 126, 234, 0.5)",
            borderColor: "rgb(102, 126, 234)",
            borderWidth: 1,
          },
        ],
      },
      options: { responsive: true, maintainAspectRatio: true },
    });

    new Chart(document.getElementById("channels-chart"), {
      type: "doughnut",
      data: {
        labels: channels.map((c) => `Chat ${c.chat_id}`),
        datasets: [
          {
            data: channels.map((c) => c.count),
            backgroundColor: [
              "#667eea",
              "#764ba2",
              "#4facfe",
              "#00f2fe",
              "#f5576c",
            ],
          },
        ],
      },
      options: { responsive: true, maintainAspectRatio: true },
    });
  }

  loadAnalytics();
</script>
{% endblock %}
```

## File: profiles.html

```html
{% extends "base.html" %} {% block title %}Profiles - TG Sentinel{% endblock %}
{% block content %}
<div class="row mb-4">
  <div class="col-12">
    <h2>Interest Profiles</h2>
    <p class="text-muted">Manage semantic interests for scoring</p>
  </div>
</div>

<div class="row">
  <div class="col-12">
    <div class="card">
      <div class="card-header">
        <h5 class="card-title mb-0">Configured Interests</h5>
      </div>
      <div class="card-body">
        <div id="interests-display">Loading...</div>
        <hr />
        <h6>Add New Interest</h6>
        <form id="add-interest-form">
          <div class="mb-3">
            <input
              type="text"
              class="form-control"
              id="new-interest"
              placeholder="E.g., blockchain technology and cryptocurrencies"
            />
          </div>
          <button type="submit" class="btn btn-primary">Add Interest</button>
        </form>
      </div>
    </div>
  </div>
</div>
{% endblock %} {% block extra_scripts %}
<script>
  async function loadInterests() {
    try {
      const res = await fetch("/api/config/get");
      const cfg = await res.json();
      const display = document.getElementById("interests-display");

      if (cfg.interests && cfg.interests.length > 0) {
        display.innerHTML =
          '<ul class="list-group">' +
          cfg.interests
            .map(
              (i, idx) => `
                    <li class="list-group-item d-flex justify-content-between align-items-center">
                        ${i}
                        <button class="btn btn-sm btn-outline-danger" onclick="removeInterest(${idx})">Remove</button>
                    </li>
                `
            )
            .join("") +
          "</ul>";
      } else {
        display.innerHTML = '<p class="text-muted">No interests configured</p>';
      }
    } catch (e) {
      showToast("Failed to load interests", "error");
    }
  }

  document
    .getElementById("add-interest-form")
    .addEventListener("submit", (e) => {
      e.preventDefault();
      const interest = document.getElementById("new-interest").value;
      if (interest) {
        showToast(
          `Interest "${interest}" added (save config to persist)`,
          "success"
        );
        document.getElementById("new-interest").value = "";
        loadInterests();
      }
    });

  function removeInterest(idx) {
    showToast(`Interest removed (save config to persist)`, "success");
    loadInterests();
  }

  loadInterests();
</script>
{% endblock %}
```

## File: console.html

```html
{% extends "base.html" %} {% block title %}Console - TG Sentinel{% endblock %}
{% block content %}
<div class="row mb-4">
  <div class="col-12">
    <h2>System Console</h2>
    <p class="text-muted">Logs and maintenance commands</p>
  </div>
</div>

<div class="row">
  <div class="col-12">
    <div class="card">
      <div class="card-header">
        <h5 class="card-title mb-0">Live Logs</h5>
      </div>
      <div class="card-body">
        <pre
          id="log-output"
          style="height: 500px; overflow-y: auto; background: #0f0f23; color: #00f2fe; padding: 1rem; border-radius: 8px;"
        >
Connecting to log stream...</pre
        >
      </div>
    </div>
  </div>
</div>
{% endblock %} {% block extra_scripts %}
<script>
  const logOutput = document.getElementById("log-output");
  let logLines = [];

  function addLog(message) {
    const timestamp = new Date().toLocaleTimeString();
    logLines.push(`[${timestamp}] ${message}`);
    if (logLines.length > 100) logLines.shift();
    logOutput.textContent = logLines.join("\n");
    logOutput.scrollTop = logOutput.scrollHeight;
  }

  socket.on("log", (data) => {
    addLog(data.message);
  });

  addLog("System console initialized");
  addLog("Waiting for log events...");
</script>
{% endblock %}
```

## Installation Instructions

1. Copy each template section above to its corresponding file in `/ui/templates/`
2. Ensure `/ui/static/css/style.css` exists (already copied from ledger-keys-extractor)
3. Install Flask dependencies: `pip install flask flask-socketio flask-cors python-socketio`
4. Test locally: `cd ui && python app.py`
5. Build Docker: `docker compose build`
6. Start UI: `docker compose up ui`

Access at <http://localhost:5000>
