// GSTR-3B page — client-side flow
// Step 1: firm + period
// Step 2: upload GSTR-2B
// Step 3: review ITC + per-category eligibility toggles
// Step 4: output liability (auto-pull from saved GSTR-1 or manual)
// Step 5: opening credit ledger
// Step 6: compute + download

(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const $$ = (sel, root = document) => root.querySelectorAll(sel);

  // ---- State ------------------------------------------------------------
  const state = {
    firmId: '',
    firmName: '',
    firmGstin: '',
    period: '',
    gstr2bRaw: null,        // full parsed object
    gstr2bData: null,       // user-edited eligibility view
    eligibility: {},        // {category: {igst: bool, cgst: bool, sgst: bool, cess: bool}}
    computation: null,
  };

  const TAX_HEADS = ['igst', 'cgst', 'sgst', 'cess'];
  const CATEGORIES = [
    { key: 'all_other_itc',  label: 'All other ITC (B2B)' },
    { key: 'reverse_charge', label: 'Reverse charge' },
    { key: 'isd',            label: 'Input Service Distributor (ISD)' },
    { key: 'imports',        label: 'Import of goods' },
    { key: 'credit_notes',   label: 'Credit notes (reduces ITC)' },
  ];

  // ---- Helpers ---------------------------------------------------------
  const fmtCurrency = (n) => '₹' + Number(n || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmt = (n) => Number(n || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const enableStep = (n) => $(`step-${n}`).classList.remove('disabled');
  const disableStep = (n) => $(`step-${n}`).classList.add('disabled');

  // ---- Step 1: firm + period change -------------------------------------
  $('firm-select').addEventListener('change', (e) => {
    const opt = e.target.selectedOptions[0];
    state.firmId = e.target.value;
    state.firmName = opt ? opt.dataset.name : '';
    state.firmGstin = opt ? opt.dataset.gstin : '';
    checkStep1();
  });
  $('period-input').addEventListener('input', () => {
    state.period = $('period-input').value.trim();
    checkStep1();
  });

  function checkStep1() {
    if (state.firmId && /^\d{6}$/.test(state.period)) {
      enableStep(2);
      // Auto-pull GSTR-1 output liability if available
      pullGstr1();
    } else {
      [2, 3, 4, 5, 6].forEach(disableStep);
    }
  }

  // initial — period prefilled from server
  state.period = $('period-input').value.trim();

  // ---- Step 2: file upload ---------------------------------------------
  const drop = $('file-drop');
  drop.addEventListener('click', () => $('file-input').click());
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('drag'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('drag'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('drag');
    if (e.dataTransfer.files.length) {
      $('file-input').files = e.dataTransfer.files;
      handleFile();
    }
  });
  $('file-input').addEventListener('change', handleFile);

  async function handleFile() {
    const f = $('file-input').files[0];
    if (!f) return;
    $('file-name-box').innerHTML = '<span class="g3-file-name">' + f.name + '</span>';
    $('parse-status').innerHTML = '<div class="g3-info"><span class="g3-spinner" style="border-color:#0891b2;border-top-color:transparent;"></span> Parsing GSTR-2B file…</div>';

    const fd = new FormData();
    fd.append('file', f);

    try {
      const res = await fetch('/api/gstr3b/parse', { method: 'POST', body: fd });
      const json = await res.json();
      if (!res.ok || !json.ok) {
        $('parse-status').innerHTML = '<div class="g3-error">Parse failed: ' + (json.error || res.statusText) + '</div>';
        return;
      }
      state.gstr2bRaw = json.data;
      $('parse-status').innerHTML = '<div class="g3-info">✓ Parsed successfully. Review ITC below.</div>';
      renderItcReview();
      enableStep(3);
      enableStep(4);
      enableStep(5);
      enableStep(6);
      // Try auto-pull GSTR-1 now that we have firm + period
      pullGstr1();
    } catch (err) {
      $('parse-status').innerHTML = '<div class="g3-error">Network error: ' + err.message + '</div>';
    }
  }

  // ---- Step 3: ITC review with eligibility toggles ---------------------
  function renderItcReview() {
    const avail = state.gstr2bRaw.itc_available;
    const notavail = state.gstr2bRaw.itc_not_available;
    const reversal = state.gstr2bRaw.itc_reversal;

    // Default all categories as eligible
    state.eligibility = {};
    CATEGORIES.forEach(c => {
      state.eligibility[c.key] = { igst: true, cgst: true, sgst: true, cess: true };
    });

    let html = `
      <table class="g3-table">
        <thead>
          <tr>
            <th style="width:34%">Category (from GSTR-2B 'ITC Available')</th>
            <th>IGST</th><th>CGST</th><th>SGST</th><th>Cess</th>
            <th style="text-align:center;width:140px;">Claim?</th>
          </tr>
        </thead>
        <tbody>
    `;
    CATEGORIES.forEach(c => {
      const t = avail[c.key] || {igst:0, cgst:0, sgst:0, cess:0};
      const anyVal = TAX_HEADS.some(h => t[h] > 0);
      html += `
        <tr data-cat="${c.key}">
          <td>${c.label}</td>
          <td>${fmt(t.igst)}</td>
          <td>${fmt(t.cgst)}</td>
          <td>${fmt(t.sgst)}</td>
          <td>${fmt(t.cess)}</td>
          <td style="text-align:center;">
            <label class="g3-toggle">
              <input type="checkbox" class="elig-toggle" data-cat="${c.key}" ${anyVal ? 'checked' : 'disabled'}>
              <span>${anyVal ? 'Yes' : '—'}</span>
            </label>
          </td>
        </tr>
      `;
    });
    const totA = avail.total || {igst:0,cgst:0,sgst:0,cess:0};
    html += `
        <tr class="total">
          <td>Net ITC Available (per GSTR-2B)</td>
          <td>${fmt(totA.igst)}</td>
          <td>${fmt(totA.cgst)}</td>
          <td>${fmt(totA.sgst)}</td>
          <td>${fmt(totA.cess)}</td>
          <td></td>
        </tr>
      </tbody>
    </table>
    <div style="margin-top:14px;display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:#555;">
      <div><span class="g3-pill g3-pill-warn">Reversal</span> &nbsp; ${TAX_HEADS.map(h => h.toUpperCase()+': '+fmt((reversal.total||{})[h]||0)).join(' &nbsp; ')}</div>
      <div><span class="g3-pill g3-pill-bad">Not available</span> &nbsp; ${TAX_HEADS.map(h => h.toUpperCase()+': '+fmt((notavail.total||{})[h]||0)).join(' &nbsp; ')}</div>
    </div>
    `;
    $('itc-review').innerHTML = html;

    // Toggle handlers
    $$('.elig-toggle').forEach(cb => {
      cb.addEventListener('change', (e) => {
        const cat = e.target.dataset.cat;
        const checked = e.target.checked;
        TAX_HEADS.forEach(h => state.eligibility[cat][h] = checked);
        e.target.parentElement.querySelector('span').textContent = checked ? 'Yes' : 'No';
      });
    });
  }

  // ---- Step 4: pull GSTR-1 output --------------------------------------
  $('pull-gstr1-btn').addEventListener('click', pullGstr1);

  async function pullGstr1() {
    if (!state.firmId || !state.period) return;
    $('pull-status').textContent = 'Looking up saved GSTR-1...';
    try {
      const url = '/api/gstr3b/output-from-gstr1?firm=' + encodeURIComponent(state.firmId)
                + '&period=' + encodeURIComponent(state.period);
      const res = await fetch(url);
      const json = await res.json();
      if (json.ok && json.found) {
        $('out-igst').value = json.totals.igst || 0;
        $('out-cgst').value = json.totals.cgst || 0;
        $('out-sgst').value = json.totals.sgst || 0;
        $('out-cess').value = json.totals.cess || 0;
        $('pull-status').innerHTML = '<span style="color:#15803d;">✓ Auto-filled from ' + json.source_file + '. Edit if needed.</span>';
      } else {
        $('pull-status').innerHTML = '<span style="color:#92400e;">No saved GSTR-1 found for this firm + period. Enter manually below.</span>';
      }
    } catch (err) {
      $('pull-status').innerHTML = '<span style="color:#dc2626;">Lookup failed: ' + err.message + '</span>';
    }
  }

  // ---- Step 6: compute + download --------------------------------------
  $('compute-btn').addEventListener('click', compute);

  function getInputs() {
    // Build eligible ITC totals from user toggles
    const eligible = { igst: 0, cgst: 0, sgst: 0, cess: 0 };
    const avail = state.gstr2bRaw.itc_available;
    CATEGORIES.forEach(c => {
      const t = avail[c.key] || {};
      TAX_HEADS.forEach(h => {
        if (state.eligibility[c.key] && state.eligibility[c.key][h]) {
          if (c.key === 'credit_notes') {
            eligible[h] -= (t[h] || 0);  // credit notes reduce
          } else {
            eligible[h] += (t[h] || 0);
          }
        }
      });
    });
    TAX_HEADS.forEach(h => eligible[h] = Math.max(0, Math.round(eligible[h] * 100) / 100));

    return {
      output_tax: {
        igst: +$('out-igst').value || 0,
        cgst: +$('out-cgst').value || 0,
        sgst: +$('out-sgst').value || 0,
        cess: +$('out-cess').value || 0,
      },
      itc_available: eligible,
      itc_reversal: state.gstr2bRaw.itc_reversal.total || {igst:0,cgst:0,sgst:0,cess:0},
      opening_balance: {
        igst: +$('open-igst').value || 0,
        cgst: +$('open-cgst').value || 0,
        sgst: +$('open-sgst').value || 0,
        cess: +$('open-cess').value || 0,
      },
      cross_order: $('cross-order').value,
    };
  }

  async function compute() {
    const btn = $('compute-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="g3-spinner"></span> Computing…';
    try {
      const res = await fetch('/api/gstr3b/compute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(getInputs()),
      });
      const json = await res.json();
      if (!json.ok) {
        alert('Compute failed: ' + json.error);
        return;
      }
      state.computation = json.result;
      renderResult();
    } catch (err) {
      alert('Network error: ' + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Compute Set-off';
    }
  }

  function renderResult() {
    const c = state.computation;
    $('compute-result').style.display = 'block';
    $('kpi-output').textContent = fmtCurrency(c.total_output);
    $('kpi-credit').textContent = fmtCurrency(c.total_credit_used);
    $('kpi-cash').textContent = fmtCurrency(c.total_cash_payable);
    $('kpi-closing').textContent = fmtCurrency(
      (c.closing_balance.igst || 0) + (c.closing_balance.cgst || 0) +
      (c.closing_balance.sgst || 0) + (c.closing_balance.cess || 0)
    );

    // Aggregate setoff steps by from/to pair
    const trailMap = {};
    c.setoff_steps.forEach(s => {
      const key = s.from + '|' + s.to;
      if (!trailMap[key]) {
        trailMap[key] = { from: s.from, to: s.to, igst: 0, cgst: 0, sgst: 0, cess: 0 };
      }
      trailMap[key][s.to.toLowerCase()] += s.amount;
    });
    let html = '';
    Object.values(trailMap).forEach(row => {
      html += `<tr><td>${row.from} → ${row.to}</td><td>${fmt(row.igst)}</td><td>${fmt(row.cgst)}</td><td>${fmt(row.sgst)}</td><td>${fmt(row.cess)}</td></tr>`;
    });
    if (!html) {
      html = '<tr><td colspan="5" style="text-align:center;color:#777;">No set-off (no credit or no liability)</td></tr>';
    }
    // Cash row
    html += `<tr class="total"><td>Cash payable</td><td>${fmt(c.cash_payable.igst)}</td><td>${fmt(c.cash_payable.cgst)}</td><td>${fmt(c.cash_payable.sgst)}</td><td>${fmt(c.cash_payable.cess)}</td></tr>`;
    $('setoff-trail').innerHTML = html;
  }

  $('download-btn').addEventListener('click', download);
  $('download-pdf-btn').addEventListener('click', downloadPdf);

  async function downloadPdf() {
    if (!state.computation) return;
    const btn = $('download-pdf-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="g3-spinner"></span> Generating…';
    try {
      const res = await fetch('/api/gstr3b/download-pdf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          firm: { name: state.firmName, gstin: state.firmGstin, id: state.firmId },
          period: state.period,
          inputs: getInputs(),
          gstr2b: state.gstr2bRaw,
        }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        alert('PDF download failed: ' + (j.error || res.statusText));
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const safe = (state.firmName || 'firm').replace(/[^a-zA-Z0-9]+/g, '_');
      a.download = `GSTR3B_${safe}_${state.period}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert('PDF download error: ' + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = '⬇ Download GSTR-3B PDF (Portal Format)';
    }
  }

  async function download() {
    if (!state.computation) return;
    const btn = $('download-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="g3-spinner"></span> Generating…';
    try {
      const res = await fetch('/api/gstr3b/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          firm: { name: state.firmName, gstin: state.firmGstin, id: state.firmId },
          period: state.period,
          inputs: getInputs(),
          gstr2b: state.gstr2bRaw,
        }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        alert('Download failed: ' + (j.error || res.statusText));
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const safe = (state.firmName || 'firm').replace(/[^a-zA-Z0-9]+/g, '_');
      a.download = `GSTR3B_${safe}_${state.period}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert('Download error: ' + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = '⬇ Download GSTR-3B Excel';
    }
  }
})();
