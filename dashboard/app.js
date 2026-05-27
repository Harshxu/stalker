/**
 * STALKER Dashboard — Live Edition
 * ─────────────────────────────────
 * Polls /api/live every 20 seconds for real-time NSE prices.
 * Animates price changes (green flash up, red flash down).
 * Shows live P&L on every stock card vs morning entry price.
 * Updates scrolling ticker bar at top with all picked stocks.
 */

// ─────────────────────────────────────────────
// STATE
// ─────────────────────────────────────────────
let allPicks      = [];         // Today's picks from /api/picks (fixed for the day)
let livePrices    = {};         // Current live prices from /api/live
let prevPrices    = {};         // Previous prices (for flash detection)
let scheduleData  = null;       // /api/schedule response
let activeFilter  = "all";
let countdown     = 20;
let countdownTimer = null;
let liveInterval  = null;
let clockInterval  = null;      // IST live clock on automations page
const LIVE_INTERVAL = 20;      // seconds between live price fetches

// ─────────────────────────────────────────────
// INIT
// ─────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  setupNavigation();
  updateMarketStatus();
  setInterval(updateMarketStatus, 60_000);

  // Load picks first, then start live price loop
  loadPicks().then(() => {
    fetchLivePrices();               // Immediate first fetch
    startLiveLoop();                 // Then every 20s
  });

  // Load automation schedule status (refreshes every 60s)
  loadScheduleStatus();
  setInterval(loadScheduleStatus, 60_000);
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

  document.querySelectorAll(".page").forEach(p => { p.classList.remove("active"); p.classList.add("hidden"); });
  const pg = document.getElementById(`page-${name}`);
  if (pg) { pg.classList.remove("hidden"); pg.classList.add("active"); }

  if (name === "live") {
    renderLiveTable();
  }

  if (name === "automations") {
    if (scheduleData) renderAutomationsPage(scheduleData);
    else loadScheduleStatus();
    startISTClock();
  } else {
    stopISTClock();
  }
}

// ─────────────────────────────────────────────
// LOAD TODAY'S PICKS
// ─────────────────────────────────────────────
let isAutoPollingPicks = false;

async function loadPicks() {
  try {
    const data = await apiFetch("/api/picks");
    
    const isScanning = data && data._is_scanning === true;
    const hasTodayPicks = data && data._has_today_picks === true;
    
    // If today's picks don't exist or a background scan is active, show the premium auto-generation overlay
    if (isScanning || !hasTodayPicks) {
      showAutoGenerationOverlay(isScanning);
      
      // Auto-poll picks endpoint every 5 seconds until today's picks are populated
      if (!isAutoPollingPicks) {
        isAutoPollingPicks = true;
        setTimeout(() => {
          isAutoPollingPicks = false;
          loadPicks();
        }, 5000);
      }
      return;
    }
    
    if (data && (data.picks || data.top_picks)) {
      const picks = data.picks || data.top_picks || [];
      allPicks = picks;
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

function showAutoGenerationOverlay(isScanning) {
  const overlay = document.getElementById("no-data-overlay");
  if (!overlay) return;
  
  overlay.classList.remove("hidden");
  overlay.innerHTML = `
    <div class="no-data-card animate-pulse">
      <div class="no-data-icon spinner-sun">🌅</div>
      <h2 style="font-size: 22px; font-weight: 800; margin-bottom: 10px;">Auto-Generating Today's Picks</h2>
      <p style="max-width: 420px; margin: 10px auto; line-height: 1.6; color: var(--text-secondary); font-size: 14px;">
        STALKER is running a pre-market 6-layer AI screening analysis across 75 high-liquidity NSE stocks to identify today's high-probability opportunities.
      </p>
      <div style="margin: 24px auto; width: 80%; background: var(--bg-elevated); height: 6px; border-radius: 3px; overflow: hidden; border: 1px solid var(--border);">
        <div class="animated-loading-bar" style="background: var(--buy-color); height: 100%; width: 50%; border-radius: 3px;"></div>
      </div>
      <div class="no-data-steps" style="border-top: 1px solid var(--border); padding-top: 15px; text-align: center; display: block;">
        <span style="font-size: 13px; color: var(--buy-color); font-weight: 700; text-transform: uppercase; letter-spacing: 1px; display: block;">
          ${isScanning ? "⏳ Scan in progress..." : "🚀 Kicking off morning scan..."}
        </span>
        <p style="font-size: 12px; color: var(--text-muted); margin-top: 6px;">This takes about 45 seconds. Page will update automatically.</p>
      </div>
    </div>
  `;
}

// ─────────────────────────────────────────────
// LIVE PRICE LOOP
// ─────────────────────────────────────────────
function startLiveLoop() {
  // Countdown timer
  countdown = LIVE_INTERVAL;
  if (countdownTimer) clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    countdown--;
    const el = document.getElementById("refresh-countdown");
    if (el) el.textContent = countdown > 0 ? `Next update: ${countdown}s` : "Updating...";
    if (countdown <= 0) countdown = LIVE_INTERVAL;
  }, 1000);

  // Price fetch loop
  if (liveInterval) clearInterval(liveInterval);
  liveInterval = setInterval(() => {
    fetchLivePrices();
  }, LIVE_INTERVAL * 1000);
}

async function fetchLivePrices() {
  try {
    const data = await apiFetch("/api/live");
    if (data && data.prices) {
      prevPrices = { ...livePrices };
      livePrices = data.prices;

      // Update all live UI elements
      updateCardLivePnl();
      renderTickerBar();
      updateAvgPnlBar();
      renderLiveTable();   // Updates if live tab is open

      document.getElementById("last-updated-text").textContent =
        `Live as of ${new Date().toLocaleTimeString("en-IN")}`;
    }
  } catch (e) {
    console.warn("Live price fetch error:", e);
  }
}

function manualRefresh() {
  countdown = LIVE_INTERVAL;
  fetchLivePrices();
  loadPicks();
}

// ─────────────────────────────────────────────
// RENDER TODAY PAGE
// ─────────────────────────────────────────────
function renderTodayPage(scan) {
  const picks = scan.picks || scan.top_picks || [];
  const trend = (scan.market_trend || "unknown").toLowerCase();

  // Subtitle
  document.getElementById("scan-meta").textContent =
    `Scanned ${scan.scanned || 0} stocks · ${picks.length} picks · ${formatDate(scan.date || "")}`;

  // Market pill
  const pill = document.getElementById("market-pill");
  const trendEmoji = { bullish:"🟢", bearish:"🔴", sideways:"🟡", unknown:"⚪" };
  pill.className = `market-pill ${trend}`;
  document.getElementById("market-icon").textContent  = trendEmoji[trend] || "⚪";
  document.getElementById("market-label").textContent = `Market ${cap(trend)}`;

  // Market bar
  document.getElementById("mood-val").textContent     = cap(trend);
  document.getElementById("mood-val").style.color     = { bullish:"var(--buy-color)", bearish:"var(--avoid-color)", sideways:"var(--watch-color)" }[trend] || "";
  document.getElementById("scanned-val").textContent  = scan.scanned || "—";
  document.getElementById("qualified-val").textContent = `${scan.qualified || 0} qualified`;
  document.getElementById("picks-count-val").textContent = picks.length;
  document.getElementById("picks-badge").textContent  = picks.length;
  document.getElementById("picks-total-badge").textContent = picks.length;

  // Sector heatmap
  renderSectorHeatmap(scan.sector_trends || {});

  // Picks grid
  renderPicksGrid(picks);
}

// ─────────────────────────────────────────────
// SECTOR HEATMAP
// ─────────────────────────────────────────────
function renderSectorHeatmap(sectors) {
  const icons = { Banking:"🏦", IT:"💻", Pharma:"💊", Auto:"🚗", FMCG:"🛒", Energy:"⚡", Metal:"⚙️", Infrastructure:"🏗️", Finance:"💰", Telecom:"📡" };
  const arrows = { bullish:"▲", bearish:"▼", sideways:"→", unknown:"—" };
  const el = document.getElementById("sector-heatmap");
  if (!Object.keys(sectors).length) {
    el.innerHTML = `<div class="sector-loading">Sector data not available yet</div>`;
    return;
  }
  el.innerHTML = Object.entries(sectors).map(([s, t], i) =>
    `<div class="sector-chip ${t}" style="animation-delay:${i*0.05}s">
       ${icons[s] || "📊"} ${s} ${arrows[t] || "—"}
     </div>`
  ).join("");
}

// ─────────────────────────────────────────────
// PICKS GRID
// ─────────────────────────────────────────────
function renderPicksGrid(picks) {
  const grid = document.getElementById("picks-grid");
  const list = activeFilter === "all" ? picks : picks.filter(p => p.action === activeFilter);

  if (!list.length) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;padding:60px;text-align:center;color:var(--text-muted)"><div style="font-size:48px;margin-bottom:12px">🔍</div><p>No stocks match this filter today</p></div>`;
    return;
  }
  grid.innerHTML = list.map((pick, i) => buildStockCard(pick, i)).join("");
  setTimeout(() => document.querySelectorAll(".score-fill").forEach(b => b.style.width = b.dataset.score + "%"), 100);

  // Inject live P&L if prices already loaded
  updateCardLivePnl();
}

function buildStockCard(pick, index) {
  const action = (pick.action || "AVOID").toUpperCase();
  const ac = { BUY:"green", WATCH:"yellow", AVOID:"red" }[action] || "red";
  const cc = { BUY:"buy", WATCH:"watch", AVOID:"avoid" }[action] || "avoid";
  const score = pick.total_score || 0;
  const fc = score >= 65 ? "" : score >= 45 ? "medium" : "low";
  const rc = { "Low Risk":"low", "Medium Risk":"med", "High Risk":"high" }[pick.risk_profile] || "med";

  const chips = [];
  if ((pick.volume_ratio || 0) >= 1.8) chips.push(`<span class="chip highlight">🔥 ${pick.volume_ratio.toFixed(1)}x Vol</span>`);
  if ((pick.gap_pct || 0) >= 1.5)      chips.push(`<span class="chip highlight">⬆ Gap +${pick.gap_pct.toFixed(1)}%</span>`);
  if (pick.structure_label)             chips.push(`<span class="chip">${pick.structure_label}</span>`);
  if (pick.news_sentiment === "bullish") chips.push(`<span class="chip highlight">📰 Bullish News</span>`);

  const reasons = (pick.reasons || []).slice(0, 3).map(r => `<div class="reason-item">• ${r}</div>`).join("");

  return `
  <div class="stock-card ${cc}" style="animation-delay:${index*0.06}s"
       data-symbol="${pick.symbol}"
       onclick="openModal(${index})">

    <div class="card-header">
      <div>
        <div class="card-name">${pick.name || pick.symbol.replace(".NS","")}</div>
        <div class="card-sector">${pick.sector || "NSE"}</div>
      </div>
      <div class="card-badges">
        <span class="action-badge ${ac}">${action}</span>
        <span class="risk-label ${rc}">${pick.risk_profile || "—"}</span>
      </div>
    </div>

    <div class="score-bar">
      <div class="score-bar-header">
        <span class="score-label">Confidence</span>
        <span class="score-number">${score}/100</span>
      </div>
      <div class="score-track"><div class="score-fill ${fc}" data-score="${score}" style="width:0"></div></div>
    </div>

    <!-- Live P&L strip (updated by updateCardLivePnl) -->
    <div class="card-live-pnl" id="lpnl-${pick.symbol.replace(/\W/g,"_")}">
      <div>
        <div class="live-pnl-label">LIVE P&L vs Entry</div>
        <div class="live-price-display" id="lprice-${pick.symbol.replace(/\W/g,"_")}">Loading...</div>
      </div>
      <div class="live-pnl-value neutral" id="lval-${pick.symbol.replace(/\W/g,"_")}">—</div>
    </div>

    <div class="price-grid">
      <div class="price-item">
        <div class="price-item-label">Entry ~</div>
        <div class="price-item-value entry">₹${fmt(pick.current_price)}</div>
      </div>
      <div class="price-item">
        <div class="price-item-label">Stop Loss</div>
        <div class="price-item-value sl">₹${fmt(pick.stop_loss)}</div>
      </div>
      <div class="price-item">
        <div class="price-item-label">Target</div>
        <div class="price-item-value tgt">₹${fmt(pick.target_2)}</div>
      </div>
    </div>

    <!-- EOD Performance Result Strip -->
    <div class="performance-strip">
      <div class="perf-item">
        <div class="perf-label">Open Price</div>
        <div class="perf-val" id="open-${pick.symbol.replace(/\W/g,"_")}">₹${fmt(pick.open_price)}</div>
      </div>
      <div class="perf-item">
        <div class="perf-label">EOD Close</div>
        <div class="perf-val" id="close-${pick.symbol.replace(/\W/g,"_")}">Pending</div>
      </div>
      <div class="perf-item">
        <div class="perf-label">Total P&L</div>
        <div class="perf-val neutral" id="pnl-${pick.symbol.replace(/\W/g,"_")}">—</div>
      </div>
      <div class="perf-item result-item">
        <div class="perf-label">Result</div>
        <div class="perf-val neutral" id="result-${pick.symbol.replace(/\W/g,"_")}">—</div>
      </div>
    </div>

    ${chips.length ? `<div class="card-chips">${chips.join("")}</div>` : ""}
    <div class="trade-type-tag">🎯 ${pick.trade_type || "Watchlist"}</div>
    ${reasons ? `<div class="card-reasons">${reasons}</div>` : ""}
  </div>`;
}

// ─────────────────────────────────────────────
// UPDATE LIVE P&L ON CARDS (called every 20s)
// ─────────────────────────────────────────────
function updateCardLivePnl() {
  if (!Object.keys(livePrices).length) return;

  allPicks.forEach(pick => {
    const sym = pick.symbol;
    const safe = sym.replace(/\W/g, "_");
    const live = livePrices[sym];

    const pnlStrip = document.getElementById(`lpnl-${safe}`);
    const priceEl  = document.getElementById(`lprice-${safe}`);
    const valEl    = document.getElementById(`lval-${safe}`);
    if (!pnlStrip || !valEl || !priceEl) return;

    if (!live) {
      priceEl.textContent = "Price unavailable";
      valEl.textContent   = "—";
      return;
    }

    const prevLive = prevPrices[sym];

    // Flash the card if price changed
    if (prevLive && prevLive.price !== live.price) {
      const cls = live.price > prevLive.price ? "flash-up" : "flash-down";
      pnlStrip.classList.remove("flash-up", "flash-down");
      void pnlStrip.offsetWidth; // reflow
      pnlStrip.classList.add(cls);
    }

    // Price display
    const dir = live.change >= 0 ? "▲" : "▼";
    const chgColor = live.change >= 0 ? "var(--buy-color)" : "var(--avoid-color)";
    priceEl.innerHTML =
      `₹${fmt(live.price)} &nbsp;<span style="color:${chgColor}">${dir} ${fmt(Math.abs(live.change))} (${live.change_pct > 0 ? "+" : ""}${live.change_pct}%)</span>`;

    // Live P&L vs entry
    const pnl    = live.live_pnl_pct;
    const pnlRs  = live.live_pnl_rs;
    if (pnl != null) {
      const cl = pnl > 0 ? "profit" : pnl < 0 ? "loss" : "neutral";
      const sign = pnl > 0 ? "+" : "";
      valEl.className = `live-pnl-value ${cl}`;
      valEl.textContent = `${sign}${pnl.toFixed(2)}%`;
    } else {
      valEl.className   = "live-pnl-value neutral";
      valEl.textContent = `${live.change_pct > 0 ? "+" : ""}${live.change_pct}%`;
    }

    // Dynamic EOD Performance Panel updates
    const openValEl  = document.getElementById(`open-${safe}`);
    const closeValEl = document.getElementById(`close-${safe}`);
    const pnlValEl   = document.getElementById(`pnl-${safe}`);

    if (openValEl) {
      if (live.open_price) {
        openValEl.textContent = `₹${fmt(live.open_price)}`;
      } else {
        openValEl.textContent = `₹${fmt(pick.current_price)}`;
      }
    }

    if (closeValEl) {
      if (live.close_price) {
        closeValEl.innerHTML = `₹${fmt(live.close_price)} <span style="font-size: 8px; color: var(--text-muted); font-weight:800; text-transform:uppercase; letter-spacing:0.5px;">EOD</span>`;
      } else {
        // Market is open, show current price with Live indicator
        closeValEl.innerHTML = `₹${fmt(live.price)} <span style="font-size: 8px; color: var(--accent); font-weight:800; text-transform:uppercase; letter-spacing:0.5px; animation: pulse-live 1.5s infinite;">Live</span>`;
      }
    }

    if (pnlValEl) {
      if (pnl != null) {
        const cl = pnl > 0 ? "profit" : pnl < 0 ? "loss" : "neutral";
        const sign = pnl > 0 ? "+" : "";
        pnlValEl.className = `perf-val ${cl}`;
        pnlValEl.textContent = `${sign}${pnl.toFixed(2)}%`;
      } else {
        pnlValEl.className = "perf-val neutral";
        pnlValEl.textContent = "—";
      }
    }

    const resultValEl = document.getElementById(`result-${safe}`);
    if (resultValEl) {
      if (pnl != null) {
        const action = (pick.action || "").toUpperCase();
        let isWin = false;
        let isLoss = false;
        
        if (action === "BUY") {
          isWin = pnl > 0;
          isLoss = pnl < 0;
        } else if (action === "WATCH" || action === "AVOID") {
          // If we suggested AVOID and it went down, that's a good suggestion
          isWin = pnl < 0;
          isLoss = pnl > 0;
        }

        if (isWin) {
          resultValEl.className = "perf-val profit";
          resultValEl.innerHTML = "✅ Helped";
        } else if (isLoss) {
          resultValEl.className = "perf-val loss";
          resultValEl.innerHTML = "❌ Failed";
        } else {
          resultValEl.className = "perf-val neutral";
          resultValEl.textContent = "Neutral";
        }
      } else {
        resultValEl.className = "perf-val neutral";
        resultValEl.textContent = "—";
      }
    }
  });
}

// ─────────────────────────────────────────────
// AVG LIVE P&L IN MARKET BAR
// ─────────────────────────────────────────────
function updateAvgPnlBar() {
  const el = document.getElementById("avg-live-pnl");
  if (!el || !allPicks.length) return;

  const pnls = allPicks
    .map(p => livePrices[p.symbol]?.live_pnl_pct ?? livePrices[p.symbol]?.change_pct)
    .filter(v => v != null);

  if (!pnls.length) { el.textContent = "—"; return; }

  const avg = pnls.reduce((a, b) => a + b, 0) / pnls.length;
  el.textContent  = `${avg > 0 ? "+" : ""}${avg.toFixed(2)}%`;
  el.style.color  = avg >= 0 ? "var(--buy-color)" : "var(--avoid-color)";
}

// ─────────────────────────────────────────────
// TICKER BAR (top scrolling strip)
// ─────────────────────────────────────────────
function renderTickerBar() {
  if (!allPicks.length && !Object.keys(livePrices).length) return;

  const inner = document.getElementById("ticker-inner");
  if (!inner) return;

  const items = allPicks.map(pick => {
    const sym  = pick.symbol;
    const live = livePrices[sym];
    if (!live) return "";

    const dir     = live.change >= 0 ? "▲" : "▼";
    const chgCls  = live.change >= 0 ? "up" : "down";
    const sign    = live.change >= 0 ? "+" : "";

    return `<span class="ticker-item">
      <span class="t-name">${pick.name || sym.replace(".NS","")}</span>
      <span class="t-price">₹${fmt(live.price)}</span>
      <span class="t-chg ${chgCls}">${dir} ${sign}${live.change_pct}%</span>
    </span>`;
  }).filter(Boolean);

  if (!items.length) return;

  // Duplicate for seamless infinite scroll
  const content = items.join("") + items.join("");
  inner.innerHTML = content;

  // Adjust animation duration based on number of items
  const dur = Math.max(40, items.length * 5);
  inner.style.animation = `ticker-scroll ${dur}s linear infinite`;
}

// ─────────────────────────────────────────────
// LIVE TABLE (Live Prices tab)
// ─────────────────────────────────────────────
function renderLiveTable() {
  const tbody = document.getElementById("live-table-body");
  if (!tbody) return;

  const activePage = document.getElementById("page-live");
  if (!activePage || !activePage.classList.contains("active")) return;

  if (!allPicks.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="table-empty">No picks loaded — run morning scan first</td></tr>`;
    return;
  }

  const ts = document.getElementById("live-updated-at");
  if (ts) ts.textContent = `Last updated: ${new Date().toLocaleTimeString("en-IN")} — refreshes every 20s`;

  const rows = allPicks.map(pick => {
    const sym    = pick.symbol;
    const live   = livePrices[sym];
    const action = (pick.action || "").toUpperCase();
    const ac     = { BUY:"green", WATCH:"yellow", AVOID:"red" }[action] || "red";

    const tvSymbol  = sym.replace(".NS", "").replace(".BO", "");
    const tvUrl     = `https://www.tradingview.com/symbols/NSE-${tvSymbol}/`;
    const tvLink    = `<a href="${tvUrl}" target="_blank" rel="noopener" onclick="event.stopPropagation()" title="View on TradingView" style="color:inherit;text-decoration:none;cursor:pointer;border-bottom:1px dashed var(--accent);">${pick.name || sym.replace(".NS","")}</a>`;

    if (!live) {
      return `<tr>
        <td class="stock-name-cell"><strong>${tvLink}</strong><span>${pick.sector || ""}</span></td>
        <td><span class="action-badge ${ac}">${action}</span></td>
        <td class="right mono" colspan="6" style="color:var(--text-muted)">Loading...</td>
      </tr>`;
    }

    const prev    = prevPrices[sym];
    const changed = prev && prev.price !== live.price;
    const flashCls = changed ? (live.price > prev.price ? "chg-up" : "chg-down") : "";

    const chgCls  = live.change >= 0 ? "chg-up" : "chg-down";
    const pnl     = live.live_pnl_pct;
    const pnlCls  = pnl == null ? "pnl-pending" : pnl > 0 ? "pnl-profit" : "pnl-loss";
    const pnlStr  = pnl != null ? `${pnl > 0 ? "+" : ""}${pnl.toFixed(2)}%` : "Pending";

    return `<tr>
      <td class="stock-name-cell"><strong>${tvLink}</strong><span>${pick.sector || ""}</span></td>
      <td><span class="action-badge ${ac}">${action}</span></td>
      <td class="right mono ${flashCls}">₹${fmt(live.price)}</td>
      <td class="right ${chgCls}">${live.change >= 0 ? "▲ +" : "▼ "}${live.change_pct}%</td>
      <td class="right mono">₹${fmt(pick.current_price)}</td>
      <td class="right ${pnlCls}">${pnlStr}</td>
      <td class="right mono" style="color:var(--text-secondary)">₹${fmt(live.day_high)}</td>
      <td class="right mono" style="color:var(--text-secondary)">₹${fmt(live.day_low)}</td>
    </tr>`;
  }).join("");

  tbody.innerHTML = rows;
}

// ─────────────────────────────────────────────
// FILTER TABS
// ─────────────────────────────────────────────
function filterPicks(filter, btn) {
  activeFilter = filter;
  document.querySelectorAll(".filter-tab").forEach(t => t.classList.remove("active"));
  btn.classList.add("active");
  renderPicksGrid(allPicks);
}

// ─────────────────────────────────────────────
// STOCK DETAIL MODAL
// ─────────────────────────────────────────────
function openModal(index) {
  const list = activeFilter === "all" ? allPicks : allPicks.filter(p => p.action === activeFilter);
  const pick = list[index];
  if (!pick) return;

  const live   = livePrices[pick.symbol] || {};
  const action = (pick.action || "AVOID").toUpperCase();
  const ac     = { BUY:"green", WATCH:"yellow", AVOID:"red" }[action] || "red";
  const pnl    = live.live_pnl_pct;
  const pnlStr = pnl != null ? `${pnl > 0 ? "+" : ""}${pnl.toFixed(2)}%` : "—";
  const pnlColor = pnl == null ? "var(--text-muted)" : pnl > 0 ? "var(--buy-color)" : "var(--avoid-color)";

  const reasonsList = (pick.reasons || []).map(r => `<li>• ${r}</li>`).join("");
  const fundFlags   = (pick.fund_flags || []).map(f => `<span class="modal-fund-flag">✅ ${f}</span>`).join("");
  const headlines   = (pick.headlines || []).map(h => `<li>📰 ${h}</li>`).join("");

  const modalTvSym = pick.symbol.replace(".NS", "").replace(".BO", "");
  const modalTvUrl = `https://www.tradingview.com/symbols/NSE-${modalTvSym}/`;

  document.getElementById("modal-content").innerHTML = `
    <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:6px">
      <div>
        <div class="modal-stock-name">
          <a href="${modalTvUrl}" target="_blank" rel="noopener" title="Open on TradingView"
             style="color:inherit;text-decoration:none;border-bottom:2px solid var(--accent)">
            ${pick.name || pick.symbol.replace(".NS","")}
          </a>
          <a href="${modalTvUrl}" target="_blank" rel="noopener"
             style="margin-left:8px;font-size:13px;color:var(--accent);text-decoration:none;vertical-align:middle"
             title="View full chart on TradingView">📊 TradingView ↗</a>
        </div>
        <div class="modal-sector">${pick.sector || "NSE"}</div>
      </div>
      <span class="action-badge ${ac}" style="font-size:14px;padding:6px 14px">${action}</span>
    </div>

    ${live.price ? `<div style="background:var(--bg-elevated);border-radius:8px;padding:12px;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between">
      <div>
        <div style="font-size:11px;color:var(--text-muted);font-weight:700;text-transform:uppercase;letter-spacing:0.5px">Live Price</div>
        <div style="font-size:24px;font-weight:800;font-family:'JetBrains Mono',monospace">₹${fmt(live.price)}</div>
        <div style="font-size:12px;color:${live.change>=0?"var(--buy-color)":"var(--avoid-color)"}">${live.change>=0?"▲":"▼"} ₹${fmt(Math.abs(live.change))} (${live.change_pct > 0 ? "+" : ""}${live.change_pct}%) today</div>
      </div>
      <div style="text-align:right">
        <div style="font-size:11px;color:var(--text-muted);font-weight:700;text-transform:uppercase;letter-spacing:0.5px">Live P&L</div>
        <div style="font-size:22px;font-weight:800;font-family:'JetBrains Mono',monospace;color:${pnlColor}">${pnlStr}</div>
      </div>
    </div>` : ""}

    <div class="modal-prices">
      <div class="modal-price-box"><div class="modal-price-label">Entry Price</div><div class="modal-price-val">₹${fmt(pick.current_price)}</div></div>
      <div class="modal-price-box"><div class="modal-price-label">Stop Loss</div><div class="modal-price-val" style="color:var(--avoid-color)">₹${fmt(pick.stop_loss)}</div></div>
      <div class="modal-price-box"><div class="modal-price-label">Target 1</div><div class="modal-price-val" style="color:var(--buy-color)">₹${fmt(pick.target_1)}</div></div>
      <div class="modal-price-box"><div class="modal-price-label">Target 2</div><div class="modal-price-val" style="color:var(--buy-color)">₹${fmt(pick.target_2)}</div></div>
      <div class="modal-price-box"><div class="modal-price-label">Risk:Reward</div><div class="modal-price-val" style="color:var(--accent)">1:${pick.rr_ratio?.toFixed(1) || "—"}</div></div>
      <div class="modal-price-box"><div class="modal-price-label">Score</div><div class="modal-price-val" style="color:var(--accent)">${pick.total_score}/100</div></div>
    </div>

    ${reasonsList ? `<div class="modal-section"><h4>Why this stock?</h4><ul class="modal-reasons">${reasonsList}</ul></div>` : ""}
    ${fundFlags ? `<div class="modal-section"><h4>Company Health</h4><div class="modal-fund-flags">${fundFlags}</div></div>` : ""}
    ${headlines ? `<div class="modal-section"><h4>Recent News</h4><ul class="modal-headlines">${headlines}</ul></div>` : ""}

    <div class="modal-section" style="background:var(--bg-elevated);border-radius:8px;padding:12px">
      <div style="font-size:12px;color:var(--text-muted);line-height:1.6">
        ⚠️ <strong>Disclaimer:</strong> STALKER provides analysis for informational purposes only.
        Past performance doesn't guarantee future results. Never invest money you can't afford to lose.
      </div>
    </div>`;

  document.getElementById("modal-overlay").classList.remove("hidden");
}

function closeModal() {
  document.getElementById("modal-overlay").classList.add("hidden");
}
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });



// ─────────────────────────────────────────────
// MARKET STATUS
// ─────────────────────────────────────────────
function updateMarketStatus() {
  const now    = new Date();
  const istMin = ((now.getUTCHours() * 60 + now.getUTCMinutes()) + 330) % 1440;
  const day    = now.getUTCDay();
  const isOpen = day >= 1 && day <= 5 && istMin >= 9*60+15 && istMin <= 15*60+30;

  const dot  = document.getElementById("status-dot");
  const text = document.getElementById("market-status-text");
  if (dot && text) {
    dot.className   = `status-dot ${isOpen ? "open" : "closed"}`;
    text.textContent = isOpen ? "Market Open" : day === 0 || day === 6 ? "Weekend" : "Market Closed";
    text.style.color = isOpen ? "var(--buy-color)" : "var(--text-muted)";
  }
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
  return Number(val).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function cap(str) {
  return str ? str[0].toUpperCase() + str.slice(1) : "";
}

function formatDate(d) {
  if (!d) return "";
  return new Date(d).toLocaleDateString("en-IN", { day:"numeric", month:"short", year:"numeric" });
}


// ─────────────────────────────────────────────
// AUTOMATION CENTER — Schedule Status
// ─────────────────────────────────────────────

async function loadScheduleStatus() {
  try {
    const data = await apiFetch("/api/schedule");
    scheduleData = data;

    // Update sidebar auto-dot indicator
    const dot = document.getElementById("auto-dot");
    if (dot) {
      // Active (glowing) if any task is currently running
      const hasRunning = (data.tasks || []).some(t => t.status === "running");
      dot.classList.toggle("active", hasRunning || data.is_scanning);
    }

    // Re-render if page is open
    const autoPage = document.getElementById("page-automations");
    if (autoPage && autoPage.classList.contains("active")) {
      renderAutomationsPage(data);
    }
  } catch (e) {
    console.warn("Schedule status fetch error:", e);
  }
}


function renderAutomationsPage(data) {
  const container = document.getElementById("automation-content");
  if (!container || !data) return;

  const tasks       = data.tasks || [];
  const isMarketDay = data.is_market_day;
  const marketOpen  = data.market_open;
  const isScanning  = data.is_scanning;

  // Market status label + color
  let mktText, mktClass;
  if (!isMarketDay)      { mktText = "⛔ Weekend — No Trading";         mktClass = "weekend"; }
  else if (marketOpen)   { mktText = "🟢 Market Open (9:15 AM – 3:30 PM IST)"; mktClass = "open"; }
  else                   { mktText = "🔴 Market Closed";                mktClass = "closed"; }

  // Build timeline HTML
  const timelineHtml = tasks.map(task => {
    const st = task.status;

    // Badge text
    const badgeText = {
      done:    "✅ Done",
      running: "⚡ Running",
      pending: "⏳ Pending",
      weekend: "📅 N/A",
    }[st] || "⏳ Pending";

    // Countdown for pending tasks
    let countdown = "";
    if (st === "pending" && task.minutes_away != null) {
      const h = Math.floor(task.minutes_away / 60);
      const m = task.minutes_away % 60;
      countdown = `<div class="timeline-countdown">⏱ Runs in ${h > 0 ? h + "h " : ""}${m}m</div>`;
    }
    if (st === "running") {
      countdown = `<div class="timeline-countdown" style="color:#ffc107">⚡ Running now...</div>`;
    }

    return `
    <div class="timeline-item">
      <div class="timeline-track">
        <div class="timeline-dot ${st}">${task.icon}</div>
      </div>
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

  // Pick counts
  const buyCount   = allPicks.filter(p => p.action === "BUY").length;
  const watchCount = allPicks.filter(p => p.action === "WATCH").length;
  const totalPicks = allPicks.length;

  // Scan info bar (shown only when picks exist)
  const scanInfoHtml = (data.picks_count > 0 || data.scanned_count > 0) ? `
  <div class="scan-info-bar">
    <div class="scan-info-item">📡 <strong>${data.universe_size}</strong> stocks in universe</div>
    <span class="scan-info-sep">|</span>
    <div class="scan-info-item">🔍 <strong>${data.scanned_count || "—"}</strong> scanned today</div>
    <span class="scan-info-sep">|</span>
    <div class="scan-info-item">🎯 <strong>${data.picks_count}</strong> picks selected</div>
    ${data.market_trend ? `<span class="scan-info-sep">|</span>
    <div class="scan-info-item">🌐 Market: <strong style="color:${
      data.market_trend === 'bullish' ? 'var(--buy-color)' :
      data.market_trend === 'bearish' ? 'var(--avoid-color)' : 'var(--watch-color)'
    }">${cap(data.market_trend)}</strong></div>` : ""}
    ${data.scan_time ? `<span class="scan-info-sep">|</span>
    <div class="scan-info-item">🕐 Scanned at <strong>${data.scan_time}</strong></div>` : ""}
    ${isScanning ? `<span class="scan-info-sep">|</span>
    <div class="scan-info-item" style="color:var(--accent);font-weight:700">
      <span class="spinner" style="width:12px;height:12px;border-width:2px;margin-bottom:0;margin-right:6px"></span> Scanning now...
    </div>` : ""}
  </div>` : "";

  // Update subtitle
  const subtitleEl = document.getElementById("auto-subtitle");
  if (subtitleEl) {
    subtitleEl.textContent = `${data.weekday_name || ""} ${formatDate(data.today)} — ${mktText}`;
  }

  container.innerHTML = `
    ${scanInfoHtml}

    <div class="section-header">
      <h2 class="section-title">📅 Daily Automation Schedule</h2>
    </div>

    <div class="automation-timeline">
      ${timelineHtml}
    </div>

    <div class="section-header" style="margin-top:8px">
      <h2 class="section-title">📊 Today's Picks Summary</h2>
    </div>

    <div class="auto-summary-grid">
      <div class="auto-stat-card">
        <div class="auto-stat-icon">🎯</div>
        <div class="auto-stat-label">Total Picks Today</div>
        <div class="auto-stat-value" style="color:var(--accent)">${totalPicks || "—"}</div>
      </div>
      <div class="auto-stat-card">
        <div class="auto-stat-icon">🟢</div>
        <div class="auto-stat-label">BUY Signals</div>
        <div class="auto-stat-value" style="color:var(--buy-color)">${totalPicks ? buyCount : "—"}</div>
      </div>
      <div class="auto-stat-card">
        <div class="auto-stat-icon">🟡</div>
        <div class="auto-stat-label">WATCH Signals</div>
        <div class="auto-stat-value" style="color:var(--watch-color)">${totalPicks ? watchCount : "—"}</div>
      </div>
      <div class="auto-stat-card">
        <div class="auto-stat-icon">🌐</div>
        <div class="auto-stat-label">Stocks Universe</div>
        <div class="auto-stat-value" style="color:var(--text-secondary)">${data.universe_size || "—"}</div>
      </div>
    </div>`;
}


// ─────────────────────────────────────────────
// IST LIVE CLOCK (Automation Center only)
// ─────────────────────────────────────────────

function startISTClock() {
  stopISTClock();
  // Tick immediately, then every second
  _tickISTClock();
  clockInterval = setInterval(_tickISTClock, 1000);
}

function stopISTClock() {
  if (clockInterval) { clearInterval(clockInterval); clockInterval = null; }
}

function _tickISTClock() {
  const el = document.getElementById("ist-clock");
  if (!el) return;
  // Calculate IST = UTC + 5:30
  const now = new Date();
  const utcMs = now.getTime() + (now.getTimezoneOffset() * 60000);
  const istMs = utcMs + (5.5 * 3600000);
  const ist   = new Date(istMs);
  const hh    = String(ist.getHours()).padStart(2, "0");
  const mm    = String(ist.getMinutes()).padStart(2, "0");
  const ss    = String(ist.getSeconds()).padStart(2, "0");
  el.textContent = `${hh}:${mm}:${ss}`;
}
