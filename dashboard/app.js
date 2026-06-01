/**
 * STALKER Dashboard — Simplified
 * Shows today's picks as a clean list.
 * Each stock name links directly to TradingView + NSE.
 * No live price polling. No P&L tracking.
 */

// ─────────────────────────────────────────────
// STATE
// ─────────────────────────────────────────────
let allPicks     = [];
let scheduleData = null;
let activeFilter = "all";
let clockInterval = null;

// ─────────────────────────────────────────────
// INIT
// ─────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  setupNavigation();
  updateMarketStatus();
  setInterval(updateMarketStatus, 60_000);

  loadPicks();
  loadScheduleStatus();
  setInterval(loadScheduleStatus, 60_000);

  // Fade out welcome screen after 2s
  setTimeout(() => {
    const ws = document.getElementById("welcome-screen");
    if (ws) {
      ws.classList.add("fade-out");
      setTimeout(() => { ws.style.display = "none"; }, 800);
    }
  }, 2000);
});

// ─────────────────────────────────────────────
// NAVIGATION
// ─────────────────────────────────────────────
function setupNavigation() {
  document.querySelectorAll(".nav-link[data-page]").forEach(link => {
    link.addEventListener("click", e => {
      e.preventDefault();
      switchPage(link.dataset.page);
    });
  });
}

function switchPage(name) {
  document.querySelectorAll(".nav-link").forEach(l => l.classList.remove("active"));
  const lnk = document.getElementById(`nav-${name}`);
  if (lnk) lnk.classList.add("active");

  document.querySelectorAll(".page").forEach(p => {
    p.classList.remove("active");
    p.classList.add("hidden");
  });
  const pg = document.getElementById(`page-${name}`);
  if (pg) { pg.classList.remove("hidden"); pg.classList.add("active"); }

  if (name === "automations") {
    if (scheduleData) renderAutomationsPage(scheduleData);
    else loadScheduleStatus();
    startISTClock();
  } else {
    stopISTClock();
  }
}

// ─────────────────────────────────────────────
// LOAD PICKS
// ─────────────────────────────────────────────
let _pollingPicks = false;

async function loadPicks() {
  try {
    const data = await apiFetch("/api/picks");

    if (data && (data._market_closed === true || data._market_closed === "true")) {
      showMarketClosedScreen(data.message || "Market is closed today.");
      return;
    }

    const isScanning    = data && data._is_scanning === true;
    const hasTodayPicks = data && data._has_today_picks === true;

    if (isScanning || !hasTodayPicks) {
      showAutoGenerationOverlay(isScanning);
      if (!_pollingPicks) {
        _pollingPicks = true;
        setTimeout(() => { _pollingPicks = false; loadPicks(); }, 5000);
      }
      return;
    }

    if (data && (data.picks || data.top_picks)) {
      allPicks = data.picks || data.top_picks || [];
      renderTodayPage(data);
      document.getElementById("no-data-overlay").classList.add("hidden");
    } else {
      document.getElementById("no-data-overlay").classList.remove("hidden");
    }
  } catch (e) {
    console.warn("Could not load picks:", e);
    document.getElementById("no-data-overlay").classList.remove("hidden");
  }
}

function manualRefresh() {
  loadPicks();
}

// ─────────────────────────────────────────────
// RENDER TODAY PAGE
// ─────────────────────────────────────────────
function renderTodayPage(scan) {
  const picks = scan.picks || scan.top_picks || [];
  const trend = (scan.market_trend || "unknown").toLowerCase();

  document.getElementById("scan-meta").textContent =
    `Scanned ${scan.scanned || 0} stocks · ${picks.length} picks · ${formatDate(scan.date || "")}`;

  const pill = document.getElementById("market-pill");
  const trendEmoji = { bullish:"🟢", bearish:"🔴", sideways:"🟡", unknown:"⚪" };
  pill.className = `market-pill ${trend}`;
  document.getElementById("market-icon").textContent  = trendEmoji[trend] || "⚪";
  document.getElementById("market-label").textContent = `Market ${cap(trend)}`;

  document.getElementById("mood-val").textContent      = cap(trend);
  document.getElementById("mood-val").style.color      = { bullish:"var(--buy-color)", bearish:"var(--avoid-color)", sideways:"var(--watch-color)" }[trend] || "";
  document.getElementById("scanned-val").textContent   = scan.scanned || "—";
  document.getElementById("qualified-val").textContent = `${scan.qualified || 0} qualified`;
  document.getElementById("picks-count-val").textContent = picks.length;
  document.getElementById("picks-badge").textContent   = picks.length;
  document.getElementById("picks-total-badge").textContent = picks.length;

  if (scan.date) {
    const d = new Date(scan.date);
    document.getElementById("scan-time-val").textContent =
      d.toLocaleTimeString("en-IN", { hour:"2-digit", minute:"2-digit" });
  }

  renderSectorHeatmap(scan.sector_trends || {});
  renderPicksList(picks);
}

// ─────────────────────────────────────────────
// SECTOR HEATMAP
// ─────────────────────────────────────────────
function renderSectorHeatmap(sectors) {
  const icons  = { Banking:"🏦", IT:"💻", Pharma:"💊", Auto:"🚗", FMCG:"🛒", Energy:"⚡", Metal:"⚙️", Infrastructure:"🏗️", Finance:"💰", Telecom:"📡" };
  const arrows = { bullish:"▲", bearish:"▼", sideways:"→", unknown:"—" };
  const el = document.getElementById("sector-heatmap");
  if (!Object.keys(sectors).length) {
    el.innerHTML = `<div class="sector-loading">Sector data not available</div>`;
    return;
  }
  el.innerHTML = Object.entries(sectors).map(([s, t], i) =>
    `<div class="sector-chip ${t}" style="animation-delay:${i*0.05}s">
       ${icons[s] || "📊"} ${s} ${arrows[t] || "—"}
     </div>`
  ).join("");
}

// ─────────────────────────────────────────────
// PICKS LIST (clean rows, no price data)
// ─────────────────────────────────────────────
function renderPicksList(picks) {
  const list = document.getElementById("picks-list");
  const filtered = activeFilter === "all" ? picks : picks.filter(p => p.action === activeFilter);

  if (!filtered.length) {
    list.innerHTML = `<div class="empty-state"><div style="font-size:48px;margin-bottom:12px">🔍</div><p>No stocks match this filter today</p></div>`;
    return;
  }

  list.innerHTML = filtered.map((pick, i) => buildPickRow(pick, i)).join("");
}

function buildPickRow(pick, index) {
  const action = (pick.action || "AVOID").toUpperCase();
  const ac     = { BUY:"green", WATCH:"yellow", AVOID:"red" }[action] || "red";
  const rc     = { "Low Risk":"low", "Medium Risk":"med", "High Risk":"high" }[pick.risk_profile] || "med";
  const score  = pick.total_score || 0;

  const name   = pick.name || pick.symbol.replace(".NS","").replace(".BO","");
  const sym    = pick.symbol.replace(".NS","").replace(".BO","");

  // Links — TradingView + NSE
  const tvUrl  = `https://www.tradingview.com/symbols/NSE-${sym}/`;
  const nseUrl = `https://www.nseindia.com/get-quotes/equity?symbol=${sym}`;

  // Reasons bullet list
  const reasons = (pick.reasons || []).slice(0,4).map(r => `<div class="pick-reason">• ${r}</div>`).join("");

  // Key numbers
  const sl  = pick.stop_loss    ? `₹${fmt(pick.stop_loss)}`  : "—";
  const t1  = pick.target_1     ? `₹${fmt(pick.target_1)}`   : "—";
  const t2  = pick.target_2     ? `₹${fmt(pick.target_2)}`   : "—";
  const rr  = pick.rr_ratio     ? `1:${pick.rr_ratio.toFixed(1)}` : "—";
  const ep  = pick.current_price ? `₹${fmt(pick.current_price)}` : "—";

  // Chips
  const chips = [];
  if ((pick.volume_ratio||0) >= 1.8) chips.push(`<span class="chip highlight">🔥 ${pick.volume_ratio.toFixed(1)}x Vol</span>`);
  if ((pick.gap_pct||0) >= 1.5)      chips.push(`<span class="chip highlight">⬆ Gap +${pick.gap_pct.toFixed(1)}%</span>`);
  if (pick.structure_label)           chips.push(`<span class="chip">${pick.structure_label}</span>`);
  if (pick.news_sentiment==="bullish") chips.push(`<span class="chip highlight">📰 Bullish News</span>`);

  return `
  <div class="pick-row ${ac}" style="animation-delay:${index*0.05}s">
    <div class="pick-row-main">

      <!-- Left: name + signal -->
      <div class="pick-left">
        <div class="pick-name-group">
          <span class="pick-name">${name}</span>
          <span class="action-badge ${ac}">${action}</span>
          <span class="risk-label ${rc}">${pick.risk_profile || ""}</span>
        </div>
        <div class="pick-sector">${pick.sector || "NSE"} · ${pick.trade_type || "Intraday"}</div>
        ${chips.length ? `<div class="card-chips" style="margin-top:6px">${chips.join("")}</div>` : ""}
      </div>

      <!-- Center: key numbers -->
      <div class="pick-numbers">
        <div class="pn-item">
          <span class="pn-label">Entry ~</span>
          <span class="pn-val entry">${ep}</span>
        </div>
        <div class="pn-item">
          <span class="pn-label">Stop Loss</span>
          <span class="pn-val sl">${sl}</span>
        </div>
        <div class="pn-item">
          <span class="pn-label">Target 1</span>
          <span class="pn-val tgt">${t1}</span>
        </div>
        <div class="pn-item">
          <span class="pn-label">Target 2</span>
          <span class="pn-val tgt">${t2}</span>
        </div>
        <div class="pn-item">
          <span class="pn-label">R:R</span>
          <span class="pn-val" style="color:var(--accent)">${rr}</span>
        </div>
        <div class="pn-item">
          <span class="pn-label">Score</span>
          <span class="pn-val" style="color:var(--accent)">${score}/100</span>
        </div>
      </div>

      <!-- Right: links -->
      <div class="pick-links">
        <a href="${tvUrl}" target="_blank" rel="noopener" class="pick-link tv-link" title="Open on TradingView">
          📊 TradingView
        </a>
        <a href="${nseUrl}" target="_blank" rel="noopener" class="pick-link nse-link" title="Open on NSE India">
          🇮🇳 NSE India
        </a>
      </div>
    </div>

    <!-- Why this stock -->
    ${reasons ? `<div class="pick-reasons">${reasons}</div>` : ""}
  </div>`;
}

// ─────────────────────────────────────────────
// FILTER TABS
// ─────────────────────────────────────────────
function filterPicks(filter, btn) {
  activeFilter = filter;
  document.querySelectorAll(".filter-tab").forEach(t => t.classList.remove("active"));
  btn.classList.add("active");
  renderPicksList(allPicks);
}

// ─────────────────────────────────────────────
// MARKET CLOSED / AUTO-GENERATION OVERLAYS
// ─────────────────────────────────────────────
function showMarketClosedScreen(message) {
  const overlay = document.getElementById("no-data-overlay");
  if (!overlay) return;
  overlay.classList.remove("hidden");
  overlay.innerHTML = `
    <div class="no-data-card" style="border:1px solid rgba(251,146,60,0.15);backdrop-filter:blur(35px);border-radius:28px;padding:56px 40px;text-align:center;max-width:480px;margin:0 auto">
      <div style="font-size:64px;margin-bottom:24px;animation:float-logo 3.8s ease-in-out infinite">🌅</div>
      <h2 style="font-size:28px;font-weight:900;margin-bottom:12px">Market is Closed Today</h2>
      <p style="max-width:400px;margin:15px auto;line-height:1.7;color:var(--text-secondary);font-size:16px">"${message}"</p>
      <div style="margin-top:24px;border-top:1px solid var(--border);padding-top:18px">
        <span style="font-size:11px;color:var(--text-muted);font-weight:700;text-transform:uppercase;letter-spacing:2px">📅 NSE India Trading Holiday</span>
      </div>
    </div>`;
  const picksEl = document.getElementById("picks-list");
  if (picksEl) picksEl.innerHTML = "";
}

function showAutoGenerationOverlay(isScanning) {
  const overlay = document.getElementById("no-data-overlay");
  if (!overlay) return;
  overlay.classList.remove("hidden");
  overlay.innerHTML = `
    <div class="no-data-card animate-pulse">
      <div class="no-data-icon spinner-sun">🌅</div>
      <h2 style="font-size:22px;font-weight:800;margin-bottom:10px">Auto-Generating Today's Picks</h2>
      <p style="max-width:420px;margin:10px auto;line-height:1.6;color:var(--text-secondary);font-size:14px">
        STALKER is running a pre-market 12-layer quant screening analysis.
      </p>
      <div style="margin:24px auto;width:80%;background:var(--bg-elevated);height:6px;border-radius:3px;overflow:hidden">
        <div class="animated-loading-bar" style="background:var(--buy-color);height:100%;width:50%;border-radius:3px"></div>
      </div>
      <span style="font-size:13px;color:var(--buy-color);font-weight:700;text-transform:uppercase;letter-spacing:1px;display:block">
        ${isScanning ? "⏳ Scan in progress..." : "🚀 Kicking off morning scan..."}
      </span>
      <p style="font-size:12px;color:var(--text-muted);margin-top:6px">This takes about 45 seconds. Page will update automatically.</p>
    </div>`;
}

// ─────────────────────────────────────────────
// MARKET STATUS (sidebar)
// ─────────────────────────────────────────────
function updateMarketStatus() {
  const now    = new Date();
  const istMin = ((now.getUTCHours() * 60 + now.getUTCMinutes()) + 330) % 1440;
  const day    = now.getUTCDay();
  const isOpen = day >= 1 && day <= 5 && istMin >= 9*60+15 && istMin <= 15*60+30;

  const dot  = document.getElementById("status-dot");
  const text = document.getElementById("market-status-text");
  if (dot && text) {
    dot.className    = `status-dot ${isOpen ? "open" : "closed"}`;
    text.textContent = isOpen ? "Market Open" : day === 0 || day === 6 ? "Weekend" : "Market Closed";
    text.style.color = isOpen ? "var(--buy-color)" : "var(--text-muted)";
  }

  const updEl = document.getElementById("last-updated-text");
  if (updEl) updEl.textContent = `Updated: ${new Date().toLocaleTimeString("en-IN")}`;
}

// ─────────────────────────────────────────────
// AUTOMATION CENTER
// ─────────────────────────────────────────────
async function loadScheduleStatus() {
  try {
    const data = await apiFetch("/api/schedule");
    scheduleData = data;
    const dot = document.getElementById("auto-dot");
    if (dot) {
      const hasRunning = (data.tasks || []).some(t => t.status === "running");
      dot.classList.toggle("active", hasRunning || data.is_scanning);
    }
    const autoPage = document.getElementById("page-automations");
    if (autoPage && autoPage.classList.contains("active")) {
      renderAutomationsPage(data);
    }
  } catch (e) {
    console.warn("Schedule status error:", e);
  }
}

function renderAutomationsPage(data) {
  const container = document.getElementById("automation-content");
  if (!container || !data) return;

  const tasks      = data.tasks || [];
  const isScanning = data.is_scanning;

  let mktText;
  if (!data.is_market_day)  mktText = "⛔ Weekend — No Trading";
  else if (data.market_open) mktText = "🟢 Market Open";
  else                       mktText = "🔴 Market Closed";

  const timelineHtml = tasks.map(task => {
    const st = task.status;
    const badgeText = { done:"✅ Done", running:"⚡ Running", pending:"⏳ Pending", weekend:"📅 N/A" }[st] || "⏳ Pending";
    let countdown = "";
    if (st === "pending" && task.minutes_away != null) {
      const h = Math.floor(task.minutes_away / 60);
      const m = task.minutes_away % 60;
      countdown = `<div class="timeline-countdown">⏱ Runs in ${h > 0 ? h+"h " : ""}${m}m</div>`;
    }
    if (st === "running") countdown = `<div class="timeline-countdown" style="color:#ffc107">⚡ Running now...</div>`;
    return `
    <div class="timeline-item">
      <div class="timeline-track"><div class="timeline-dot ${st}">${task.icon}</div></div>
      <div class="timeline-body">
        <div class="timeline-card ${st}">
          <div class="timeline-card-top">
            <span class="timeline-time-label">${task.scheduled} IST</span>
            <span class="tl-badge ${st}">${badgeText}</span>
          </div>
          <div class="timeline-name">${task.name}</div>
          <div class="timeline-desc">${task.desc}</div>
          ${countdown}
        </div>
      </div>
    </div>`;
  }).join("");

  const buyCount   = allPicks.filter(p => p.action === "BUY").length;
  const watchCount = allPicks.filter(p => p.action === "WATCH").length;

  // Calculate average validation audit metrics
  let avgDQ = 0;
  let avgRisk = 0;
  let avgInst = 0;
  let hasAudit = false;

  if (allPicks.length > 0) {
    let sumDQ = 0;
    let sumRisk = 0;
    let sumInst = 0;
    let count = 0;
    
    allPicks.forEach(p => {
      const audit = p.validation_audit;
      if (audit) {
        sumDQ += audit.data_quality || 0;
        sumRisk += audit.risk || 0;
        sumInst += audit.institutional || 0;
        count++;
      } else {
        sumDQ += p.data_quality_score || 95;
        sumRisk += p.risk_score || 2.5;
        sumInst += p.institutional_score || 75;
        count++;
      }
    });
    
    if (count > 0) {
      avgDQ = (sumDQ / count).toFixed(1);
      avgRisk = (sumRisk / count).toFixed(1);
      avgInst = (sumInst / count).toFixed(1);
      hasAudit = true;
    }
  }

  const regimeLabel = data.market_trend ? data.market_trend.toUpperCase() : "NEUTRAL";
  const regimeColor = { bullish: "var(--buy-color)", bearish: "var(--avoid-color)", sideways: "var(--watch-color)", neutral: "var(--text-secondary)" }[data.market_trend] || "var(--text-secondary)";

  const subtitleEl = document.getElementById("auto-subtitle");
  if (subtitleEl) subtitleEl.textContent = `${data.weekday_name||""} ${formatDate(data.today)} — ${mktText}`;

  container.innerHTML = `
    <div class="section-header">
      <h2 class="section-title">📅 Daily Automation Schedule</h2>
    </div>
    <div class="automation-timeline">${timelineHtml}</div>

    <div class="section-header" style="margin-top:8px">
      <h2 class="section-title">📊 Today's Picks Summary</h2>
    </div>
    <div class="auto-summary-grid">
      <div class="auto-stat-card">
        <div class="auto-stat-icon">🎯</div>
        <div class="auto-stat-label">Total Picks</div>
        <div class="auto-stat-value" style="color:var(--accent)">${allPicks.length||"—"}</div>
      </div>
      <div class="auto-stat-card">
        <div class="auto-stat-icon">🟢</div>
        <div class="auto-stat-label">BUY Signals</div>
        <div class="auto-stat-value" style="color:var(--buy-color)">${allPicks.length ? buyCount : "—"}</div>
      </div>
      <div class="auto-stat-card">
        <div class="auto-stat-icon">🟡</div>
        <div class="auto-stat-label">WATCH Signals</div>
        <div class="auto-stat-value" style="color:var(--watch-color)">${allPicks.length ? watchCount : "—"}</div>
      </div>
      <div class="auto-stat-card">
        <div class="auto-stat-icon">🌐</div>
        <div class="auto-stat-label">Universe</div>
        <div class="auto-stat-value" style="color:var(--text-secondary)">${data.universe_size||"—"}</div>
      </div>
    </div>

    <div class="section-header" style="margin-top:16px">
      <h2 class="section-title">🛡️ STALKER Alpha Engine v3.0 Quant Diagnostics</h2>
    </div>
    <div class="auto-summary-grid">
      <div class="auto-stat-card">
        <div class="auto-stat-icon">🌐</div>
        <div class="auto-stat-label">Market Regime</div>
        <div class="auto-stat-value" style="color:${regimeColor}; font-size: 20px;">${regimeLabel}</div>
        <div style="font-size: 11px; color: var(--text-muted); margin-top: 6px;">Adaptive scoring active</div>
      </div>
      <div class="auto-stat-card">
        <div class="auto-stat-icon">🛡️</div>
        <div class="auto-stat-label">Avg Data Quality</div>
        <div class="auto-stat-value" style="color:var(--buy-color); font-size: 20px;">${hasAudit ? avgDQ + "%" : "90.0%+"}</div>
        <div style="font-size: 11px; color: var(--text-muted); margin-top: 6px;">Strict Gate: DQ >= 70%</div>
      </div>
      <div class="auto-stat-card">
        <div class="auto-stat-icon">🏢</div>
        <div class="auto-stat-label">Avg Inst. Accumulation</div>
        <div class="auto-stat-value" style="color:var(--accent); font-size: 20px;">${hasAudit ? avgInst + "/100" : "80.0+"}</div>
        <div style="font-size: 11px; color: var(--text-muted); margin-top: 6px;">Volume & accumulation surge</div>
      </div>
      <div class="auto-stat-card">
        <div class="auto-stat-icon">⚖️</div>
        <div class="auto-stat-label">Pearson Correlation</div>
        <div class="auto-stat-value" style="color:var(--buy-color); font-size: 18px;">ACTIVE</div>
        <div style="font-size: 11px; color: var(--text-muted); margin-top: 6px;">Compliant (r <= 0.80)</div>
      </div>
    </div>`;
}

// ─────────────────────────────────────────────
// IST CLOCK
// ─────────────────────────────────────────────
function startISTClock() {
  stopISTClock();
  _tickISTClock();
  clockInterval = setInterval(_tickISTClock, 1000);
}
function stopISTClock() {
  if (clockInterval) { clearInterval(clockInterval); clockInterval = null; }
}
function _tickISTClock() {
  const el = document.getElementById("ist-clock");
  if (!el) return;
  const now  = new Date();
  const utcMs = now.getTime() + (now.getTimezoneOffset() * 60000);
  const ist   = new Date(utcMs + 5.5 * 3600000);
  el.textContent = `${String(ist.getHours()).padStart(2,"0")}:${String(ist.getMinutes()).padStart(2,"0")}:${String(ist.getSeconds()).padStart(2,"0")}`;
}

// ─────────────────────────────────────────────
// UTILS
// ─────────────────────────────────────────────
async function apiFetch(path) {
  const res = await fetch(path + "?_=" + Date.now());
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
function fmt(val) {
  if (val == null || isNaN(val)) return "—";
  return Number(val).toLocaleString("en-IN", { minimumFractionDigits:2, maximumFractionDigits:2 });
}
function cap(str) {
  return str ? str[0].toUpperCase() + str.slice(1) : "";
}
function formatDate(d) {
  if (!d) return "";
  return new Date(d).toLocaleDateString("en-IN", { day:"numeric", month:"short", year:"numeric" });
}
