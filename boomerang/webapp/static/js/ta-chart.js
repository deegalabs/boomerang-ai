/* Reusable TA chart + confluence panel (lightweight-charts).
   mountTAChart(opts) wires a candle chart with EMA/VWAP/Fibonacci overlays and a
   confluence panel, polling /api/ta?symbol=<symbolFn()>. Element ids are passed in,
   so the same code drives /live and the demo Console. */
(function () {
  window.mountTAChart = function (o) {
    var chart = null, candle = null, e9 = null, e21 = null, lines = [], full = false;
    var el = function (id) { return id ? document.getElementById(id) : null; };
    var H = o.height || 380;

    function resize() {
      if (!chart) return;
      var c = el(o.chart);
      var h = full ? Math.max(window.innerHeight - 200, 360) : H;
      c.style.height = h + "px";
      chart.applyOptions({ width: c.clientWidth, height: h });
      chart.timeScale().fitContent();
    }
    function ensure() {
      if (chart || !window.LightweightCharts) return;
      var c = el(o.chart);
      chart = LightweightCharts.createChart(c, {
        width: c.clientWidth, height: H,
        layout: { background: { color: "transparent" }, textColor: "#A7ADBE", fontFamily: "inherit" },
        grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.04)" } },
        timeScale: { visible: false, borderColor: "rgba(255,255,255,0.08)" },
        rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
        crosshair: { mode: 0 }
      });
      candle = chart.addCandlestickSeries({ upColor: "#34E5A4", downColor: "#FF5470", borderVisible: false, wickUpColor: "#34E5A4", wickDownColor: "#FF5470" });
      e9 = chart.addLineSeries({ color: "#F3BA2F", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      e21 = chart.addLineSeries({ color: "#5AA9FF", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      window.addEventListener("resize", resize);
    }
    function renderChart(d) {
      ensure(); if (!chart) return;
      candle.setData(d.candles || []);
      e9.setData(d.ema9 || []); e21.setData(d.ema21 || []);
      lines.forEach(function (l) { try { candle.removePriceLine(l); } catch (e) {} }); lines = [];
      var lv = (d.fib && d.fib.levels) || {};
      Object.keys(lv).forEach(function (k) {
        var gold = (k === "0.618");
        lines.push(candle.createPriceLine({ price: lv[k], color: gold ? "#F3BA2F" : "rgba(255,255,255,0.22)", lineWidth: gold ? 2 : 1, lineStyle: gold ? 0 : 2, axisLabelVisible: true, title: "fib " + k }));
      });
      if (d.vwap) lines.push(candle.createPriceLine({ price: d.vwap, color: "#BF5AF2", lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: "VWAP" }));
      resize();
    }
    var DEC = { ENTER: ["#34E5A4", "ENTER"], WAIT: ["#F3BA2F", "WAIT"], AVOID: ["#FF5470", "AVOID"] };
    function renderPanel(d) {
      var cf = d.confluence || {}, dec = DEC[cf.decision] || ["#6E7388", cf.decision || "·"];
      if (el(o.symbol)) el(o.symbol).textContent = d.symbol || "";
      var b = el(o.decision); if (b) { b.textContent = dec[1]; b.style.background = dec[0]; b.style.color = "#05060B"; }
      if (el(o.scoreBar)) { el(o.scoreBar).style.width = (cf.score || 0) + "%"; el(o.scoreBar).style.background = dec[0]; }
      if (el(o.scoreVal)) el(o.scoreVal).textContent = (cf.score != null ? cf.score : "·") + "/100 · " + (cf.mode || "");
      var sig = cf.signals || [];
      if (el(o.signals)) el(o.signals).innerHTML = sig.length
        ? sig.map(function (s) { var col = s.vote > 0 ? "#34E5A4" : s.vote < 0 ? "#FF5470" : "#6E7388";
            return '<div style="display:flex;align-items:center;gap:8px;padding:4px 0"><span style="width:8px;height:8px;border-radius:50%;background:' + col + ';flex-shrink:0"></span><span class="mono" style="font-size:.66rem;color:#6E7388;width:74px;text-transform:uppercase;flex-shrink:0">' + s.pillar + '</span><span style="font-size:.84rem;color:#F5F5F7">' + s.reason + '</span></div>'; }).join("")
        : '<span class="mut">' + (cf.veto || "no long signals") + "</span>";
      var st = d.stats || {}, f = function (v, suf) { suf = suf || ""; return v == null ? "·" : (Math.round(v * 10) / 10) + suf; };
      if (el(o.stats)) el(o.stats).innerHTML = [["RSI", f(st.rsi)], ["MACD", f(st.macd_hist)], ["ADX", f(st.adx)], ["%B", st.bb_pct_b != null ? (Math.round(st.bb_pct_b * 100) / 100) : "·"], ["ATR%", f(st.atr_pct)], ["VWAP Δ", f(st.vwap_dist_pct, "%")]]
        .map(function (kv) { return '<div style="text-align:center;flex:1"><div class="mono" style="font-size:.66rem;color:#6E7388">' + kv[0] + '</div><div class="mono" style="font-size:1rem;color:#F5F5F7">' + kv[1] + "</div></div>"; }).join("");
    }
    function toggleFull() {
      if (!o.card) return;
      full = !full;
      el(o.card).classList.toggle("ta-full", full);
      document.body.style.overflow = full ? "hidden" : "";
      if (el(o.expandLbl)) el(o.expandLbl).textContent = full ? o.closeText : o.expandText;
      requestAnimationFrame(function () { requestAnimationFrame(resize); });
    }
    function refresh() {
      var sym = o.symbolFn ? o.symbolFn() : o.symbolValue;
      var box = el(o.box), un = el(o.unavail);
      if (!sym) { if (box) box.style.display = "none"; if (un) { un.style.display = "block"; un.textContent = o.unavailText || "·"; } return; }
      fetch("/api/ta?symbol=" + encodeURIComponent(sym)).then(function (r) { return r.json(); }).then(function (d) {
        if (!d.available) { if (box) box.style.display = "none"; if (un) { un.style.display = "block"; un.textContent = (o.unavailText || "·") + " · " + (d.symbol || ""); } return; }
        if (box) box.style.display = o.boxDisplay || "";
        if (un) un.style.display = "none";
        renderChart(d); renderPanel(d);
      }).catch(function () {});
    }
    if (el(o.expand)) el(o.expand).addEventListener("click", toggleFull);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && full) toggleFull(); });
    refresh();
    setInterval(refresh, o.intervalMs || 15000);
    return { refresh: refresh, toggleFull: toggleFull };
  };
})();
