/* Dians – заеднички помошни функции за графиците и интеракциите */
(function () {
  const css = getComputedStyle(document.documentElement);
  const v = name => css.getPropertyValue(name).trim();
  const C = {
    accent: v('--accent'), accent2: v('--accent-2'), green: v('--green'), red: v('--red'),
    amber: v('--amber'), text: v('--text'), text2: v('--text-2'), muted: v('--muted'),
    grid: 'rgba(34,44,65,0.65)', surface: v('--surface-2')
  };
  window.DC = C;

  const fmtPrice = n => {
    if (n === null || n === undefined) return '—';
    if (n >= 1) return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (n >= 0.01) return '$' + n.toFixed(4);
    return '$' + n.toPrecision(4);
  };
  const fmtCompact = n => {
    if (n === null || n === undefined) return '—';
    const a = Math.abs(n);
    if (a >= 1e12) return (n / 1e12).toFixed(2) + 'T';
    if (a >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return n.toFixed(2);
  };
  window.fmtPrice = fmtPrice;
  window.fmtCompact = fmtCompact;

  if (window.Chart) {
    Chart.defaults.color = C.muted;
    Chart.defaults.font.family = "Inter, system-ui, sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.borderColor = C.grid;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.boxWidth = 8;
    Chart.defaults.plugins.tooltip.backgroundColor = '#1c2639';
    Chart.defaults.plugins.tooltip.borderColor = '#2e3a54';
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.padding = 12;
    Chart.defaults.plugins.tooltip.titleColor = C.text2;
    Chart.defaults.plugins.tooltip.bodyColor = C.text;
    Chart.defaults.plugins.tooltip.titleFont = { weight: '500' };
    Chart.defaults.plugins.tooltip.bodyFont = { family: "JetBrains Mono, monospace", weight: '600' };
    Chart.defaults.plugins.tooltip.displayColors = false;
  }

  function gradient(ctx, color, top = 0.28) {
    const area = ctx.chart.chartArea;
    if (!area) return 'transparent';
    const g = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
    g.addColorStop(0, color + Math.round(top * 255).toString(16).padStart(2, '0'));
    g.addColorStop(1, color + '00');
    return g;
  }
  window.dcGradient = gradient;

  // Главен линиски график со градиент
  window.priceChart = function (el, labels, data, opts = {}) {
    const color = opts.color || C.accent;
    return new Chart(el, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: opts.label || 'Close', data, borderColor: color, borderWidth: 2,
          pointRadius: 0, pointHoverRadius: 5, pointHoverBackgroundColor: color,
          pointHoverBorderColor: '#0a0e17', pointHoverBorderWidth: 2,
          tension: 0.25, fill: true, backgroundColor: c => gradient(c, color)
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: c => fmtPrice(c.parsed.y) } }
        },
        scales: {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 7, maxRotation: 0 } },
          y: { position: 'right', grid: { color: C.grid }, border: { display: false },
               ticks: { callback: val => fmtPrice(val), maxTicksLimit: 6 } }
        }
      }
    });
  };

  // Мини-графици во табелата
  window.sparkline = function (el, data) {
    if (!data || data.length < 2) return;
    const up = data[data.length - 1] >= data[0];
    const color = up ? C.green : C.red;
    new Chart(el, {
      type: 'line',
      data: { labels: data.map((_, i) => i), datasets: [{ data, borderColor: color, borderWidth: 1.6, pointRadius: 0, tension: 0.35, fill: true, backgroundColor: c => gradient(c, color, 0.18) }] },
      options: { responsive: false, animation: false, events: [], plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } } }
    });
  };

  // Кликање на цел ред во табела
  document.addEventListener('click', e => {
    const tr = e.target.closest('tr[data-href]');
    if (tr && !e.target.closest('a,button')) window.location = tr.dataset.href;
  });

  // Пребарување во табела
  const search = document.querySelector('[data-filter]');
  if (search) {
    const rows = document.querySelectorAll(search.dataset.filter);
    search.addEventListener('input', () => {
      const q = search.value.trim().toLowerCase();
      rows.forEach(r => { r.style.display = r.dataset.search.includes(q) ? '' : 'none'; });
    });
  }

  // Следење на повлекувањето податоци (pipeline во позадина)
  window.watchFetch = function () {
    const toast = document.getElementById('fetch-toast');
    if (!toast) return;
    const bar = toast.querySelector('.progress > span');
    const text = toast.querySelector('.toast-text');
    toast.classList.add('show');
    let seenRunning = false, idle = 0;
    const tick = async () => {
      try {
        const s = await (await fetch('/fetch-status')).json();
        if (s.status === 'running') {
          seenRunning = true;
          const [done, total] = (s.progress || '0/?').split('/').map(Number);
          if (!total) {
            bar.style.width = '4%';
            text.textContent = 'Ја вчитувам листата на најголеми валути…';
          } else {
            bar.style.width = Math.max(4, done / total * 100) + '%';
            text.textContent = s.note ? `${done}/${total} · ${s.note}`
              : `Повлечени ${done} од ${total} валути${s.current ? ' · ' + s.current : ''}…`;
          }
        } else if (s.status === 'completed' && seenRunning) {
          bar.style.width = '100%';
          text.textContent = 'Готово! Се освежува…';
          setTimeout(() => window.location = '/', 900);
          return;
        } else if (++idle > 4) {
          // извршувањето завршило пред да почнеме да следиме
          window.location = '/';
          return;
        }
      } catch (e) { text.textContent = 'Чекам одговор од pipeline сервисот…'; }
      setTimeout(tick, 2500);
    };
    tick();
  };
})();
