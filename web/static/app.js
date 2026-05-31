/* GSTR-1 Generator — Main page logic */

const monthNames = ['', 'January', 'February', 'March', 'April', 'May', 'June',
                    'July', 'August', 'September', 'October', 'November', 'December'];

const fileBindings = new Map(); // firm_id -> File

document.addEventListener('DOMContentLoaded', () => {
  const ov = document.getElementById('overlay');
  if (ov) ov.style.display = 'none';
});

// ===== Period dropdowns ====================================================
const periodHidden = document.getElementById('period');
const monthSelect = document.getElementById('period-month');
const yearSelect = document.getElementById('period-year');
const periodFeedback = document.getElementById('period-feedback');

function buildPeriodSelects() {
  // Months
  for (let i = 1; i <= 12; i++) {
    const opt = document.createElement('option');
    opt.value = String(i).padStart(2, '0');
    opt.textContent = monthNames[i];
    monthSelect.appendChild(opt);
  }
  // Years: previous 3, current, next 1
  const now = new Date();
  const currentYear = now.getFullYear();
  for (let y = currentYear - 3; y <= currentYear + 1; y++) {
    const opt = document.createElement('option');
    opt.value = String(y);
    opt.textContent = String(y);
    yearSelect.appendChild(opt);
  }

  // Pre-select from hidden default (MMYYYY)
  const def = (periodHidden.value || '').trim();
  if (/^\d{6}$/.test(def)) {
    monthSelect.value = def.slice(0, 2);
    yearSelect.value = def.slice(2);
  } else {
    // Default to previous month
    let mm = now.getMonth(); // 0-indexed = previous month in 1-indexed
    let yy = currentYear;
    if (mm === 0) { mm = 12; yy -= 1; }
    monthSelect.value = String(mm).padStart(2, '0');
    yearSelect.value = String(yy);
  }
  syncPeriod();
}

function syncPeriod() {
  const mm = monthSelect.value;
  const yyyy = yearSelect.value;
  if (mm && yyyy) {
    periodHidden.value = mm + yyyy;
    periodFeedback.textContent = `→ ${monthNames[parseInt(mm)]} ${yyyy}`;
    periodFeedback.className = 'period-feedback valid';
  }
}

if (monthSelect && yearSelect) {
  buildPeriodSelects();
  monthSelect.addEventListener('change', syncPeriod);
  yearSelect.addEventListener('change', syncPeriod);
}

// ===== Dropzone wiring =====================================================
function setupDropzones() {
  document.querySelectorAll('.dropzone').forEach((dz) => {
    const targetId = dz.dataset.target;
    const input = document.getElementById(targetId);
    const card = dz.closest('.firm-card');
    const firmId = input.dataset.firmId;
    const statusText = card.querySelector('.status-text');
    const preview = dz.querySelector('.file-preview');
    const previewName = preview.querySelector('.file-name');
    const clearBtn = preview.querySelector('.file-clear');
    const allMain = dz.querySelectorAll('.dropzone-content > svg, .dropzone-content > .dropzone-text');

    function setFile(file) {
      fileBindings.set(firmId, file);
      previewName.textContent = file.name;
      preview.hidden = false;
      allMain.forEach(el => el.style.display = 'none');
      dz.classList.add('has-file');
      card.classList.add('has-file');
      card.classList.remove('error', 'success', 'processing');
      statusText.textContent = 'Ready · ' + (file.size > 1024 ? Math.round(file.size / 1024) + ' KB' : file.size + ' B');
      refreshProcessButton();
    }
    function clearFile() {
      fileBindings.delete(firmId);
      preview.hidden = true;
      allMain.forEach(el => el.style.display = '');
      dz.classList.remove('has-file');
      card.classList.remove('has-file', 'error', 'success', 'processing');
      statusText.textContent = 'Awaiting file';
      input.value = '';
      refreshProcessButton();
    }

    dz.addEventListener('click', (e) => {
      if (e.target === clearBtn) return;
      input.click();
    });
    input.addEventListener('change', () => {
      if (input.files.length) setFile(input.files[0]);
    });
    clearBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      clearFile();
    });

    ['dragenter', 'dragover'].forEach(ev => {
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dz.classList.add('drag-over');
      });
    });
    ['dragleave', 'drop'].forEach(ev => {
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dz.classList.remove('drag-over');
      });
    });
    dz.addEventListener('drop', (e) => {
      const f = e.dataTransfer.files[0];
      if (f) {
        const ext = f.name.split('.').pop().toLowerCase();
        if (['xlsx', 'xls', 'csv', 'tsv'].includes(ext)) setFile(f);
        else alert(`Unsupported file type: .${ext}`);
      }
    });
  });
}
setupDropzones();

// ===== Process button (now: preview) =======================================
const processBtn = document.getElementById('process-btn');
function refreshProcessButton() {
  if (!processBtn) return;
  processBtn.disabled = fileBindings.size === 0;
}
refreshProcessButton();

// State carried between preview and generate
let currentBatchId = null;
let currentPreviews = [];      // array of preview objects from server
let currentExclusions = {};    // firm_id -> Set of doc keys
let currentOverrides = {};     // firm_id -> { doc_key: {supply_type, reverse_charge} }
let currentPeriodLabel = '';

if (processBtn) {
  processBtn.addEventListener('click', async () => {
    const period = periodHidden.value.trim();
    if (!/^\d{6}$/.test(period)) {
      alert('Please pick a month and year.');
      return;
    }
    if (fileBindings.size === 0) return;

    const fd = new FormData();
    fd.append('period', period);

    const jobs = [];
    let i = 0;
    fileBindings.forEach((file, firmId) => {
      const fieldName = `file_${i}`;
      jobs.push({ firm_id: firmId, file_field: fieldName });
      fd.append(fieldName, file);
      const card = document.querySelector(`.firm-card[data-firm-id="${firmId}"]`);
      if (card) {
        card.classList.remove('success', 'error', 'has-file');
        card.classList.add('processing');
        card.querySelector('.status-text').textContent = 'Reading…';
      }
      i++;
    });
    fd.append('jobs', JSON.stringify(jobs));

    showOverlay(true);
    processBtn.disabled = true;

    try {
      const res = await fetch('/api/preview', { method: 'POST', body: fd });
      const data = await res.json();
      showOverlay(false);
      if (!data.ok) {
        alert(data.error || 'Preview failed.');
        processBtn.disabled = false;
        return;
      }
      currentBatchId = data.batch_id;
      currentPreviews = data.previews;
      currentExclusions = {};
      currentOverrides = {};
      currentPeriodLabel = data.period_label;
      renderReview(data);
    } catch (err) {
      showOverlay(false);
      alert('Network error: ' + err.message);
      processBtn.disabled = false;
    }
  });
}

function showOverlay(on, text) {
  const overlay = document.getElementById('overlay');
  if (overlay) overlay.style.display = on ? 'flex' : 'none';
  if (text) {
    const sub = document.getElementById('overlay-sub');
    if (sub) sub.textContent = text;
  }
}

// ===== Render review screen ================================================
function renderReview(data) {
  const section = document.getElementById('review-section');
  const summaryEl = document.getElementById('review-summary');
  const list = document.getElementById('review-firms');
  list.innerHTML = '';

  const totalFirms = data.previews.length;
  const okFirms = data.previews.filter(p => p.ok).length;
  summaryEl.textContent = `${okFirms} of ${totalFirms} ready · ${data.period_label}`;

  data.previews.forEach((p) => {
    const card = document.querySelector(`.firm-card[data-firm-id="${p.firm_id}"]`);
    if (card) {
      card.classList.remove('processing');
      card.classList.add(p.ok ? 'success' : 'error');
      card.querySelector('.status-text').textContent = p.ok ? 'Ready' : 'Failed';
    }

    const div = document.createElement('div');
    div.className = 'review-firm-card' + (p.ok ? '' : ' error');

    if (!p.ok) {
      div.innerHTML = `
        <div class="review-firm-head">
          <div class="result-name">${escapeHtml(p.firm_name || 'Unknown firm')}</div>
          <div class="error-msg">${escapeHtml(p.error || 'Unknown error')}</div>
        </div>
      `;
      list.appendChild(div);
      return;
    }

    const s = p.stats;
    const pf = p.preflight ? p.preflight.totals : { errors: 0, warnings: 0, info: 0 };

    // Preflight banner
    let preflightBanner = '';
    if (pf.errors > 0 || pf.warnings > 0) {
      const errorIssues = (p.preflight.errors || []).slice(0, 5);
      const warnIssues = (p.preflight.warnings || []).slice(0, 5);
      let issuesHtml = '';
      if (errorIssues.length > 0) {
        issuesHtml += `<div class="preflight-block error"><strong>${pf.errors} error${pf.errors !== 1 ? 's' : ''}</strong><ul>` +
          errorIssues.map(i => `<li>${escapeHtml(i.invoice_no)}: ${escapeHtml(i.message)}</li>`).join('') +
          (pf.errors > 5 ? `<li class="more">+${pf.errors - 5} more</li>` : '') +
          `</ul></div>`;
      }
      if (warnIssues.length > 0) {
        issuesHtml += `<div class="preflight-block warn"><strong>${pf.warnings} warning${pf.warnings !== 1 ? 's' : ''}</strong><ul>` +
          warnIssues.map(i => `<li>${escapeHtml(i.invoice_no)}: ${escapeHtml(i.message)}</li>`).join('') +
          (pf.warnings > 5 ? `<li class="more">+${pf.warnings - 5} more</li>` : '') +
          `</ul></div>`;
      }
      preflightBanner = `<details class="preflight-details" ${pf.errors > 0 ? 'open' : ''}>
        <summary>Pre-flight checks: ${pf.errors} error${pf.errors !== 1 ? 's' : ''}, ${pf.warnings} warning${pf.warnings !== 1 ? 's' : ''}</summary>
        ${issuesHtml}
      </details>`;
    } else {
      preflightBanner = `<div class="preflight-clean">✓ Pre-flight checks passed</div>`;
    }

    // Build prominent warnings banner (shown above the invoice table)
    let warningsBanner = '';
    if (p.warnings && p.warnings.length) {
      const isCritical = p.warnings.some(w => w.startsWith('⚠️ CRITICAL'));
      warningsBanner = `
        <div class="period-warnings ${isCritical ? 'critical' : ''}">
          ${p.warnings.map(w => `<div class="period-warning-item">${escapeHtml(w)}</div>`).join('')}
        </div>
      `;
    }

    div.innerHTML = `
      <div class="review-firm-head">
        <div>
          <div class="result-name">${escapeHtml(p.firm_name)}</div>
          <div class="result-gstin">${escapeHtml(p.firm_gstin)}</div>
        </div>
        <div class="review-firm-stats">
          <span class="rstat"><b>${s.invoices}</b> invoices</span>
          <span class="rstat"><b>${s.b2b}</b> B2B</span>
          ${s.credit_notes > 0 ? `<span class="rstat cn"><b>${s.credit_notes}</b> credit notes</span>` : ''}
          ${s.debit_notes > 0 ? `<span class="rstat cn"><b>${s.debit_notes}</b> debit notes</span>` : ''}
          ${s.exceptions > 0 ? `<span class="rstat warn"><b>${s.exceptions}</b> exceptions</span>` : ''}
        </div>
      </div>
      ${warningsBanner}
      ${preflightBanner}
      ${renderCarryForwardBanner(p)}
      <div class="invoice-table-wrap">
        <table class="invoice-table" data-firm-id="${escapeHtml(p.firm_id)}">
          <thead>
            <tr>
              <th class="col-include"><input type="checkbox" class="include-all" checked title="Include/exclude all"></th>
              <th>Type</th>
              <th>Invoice / Note</th>
              <th>Date</th>
              <th>Customer</th>
              <th class="col-num">Taxable</th>
              <th class="col-num">Tax</th>
              <th class="col-num">Total</th>
              <th title="Supply Type — override per invoice if not tagged in Excel">Supply Type</th>
              <th title="Reverse Charge applicable?">RCM</th>
              <th>Bucket</th>
              <th class="col-actions"></th>
            </tr>
          </thead>
          <tbody>
            ${p.invoices.map(inv => renderInvoiceRow(inv)).join('')}
          </tbody>
          <tfoot class="invoice-totals">
            <tr>
              <td colspan="5" class="totals-label">Totals (included only)</td>
              <td class="num" data-totals="taxable">0.00</td>
              <td class="num" data-totals="tax">0.00</td>
              <td class="num" data-totals="value"><b>0.00</b></td>
              <td colspan="4" class="totals-count">
                <span data-totals="included-count">0</span> included ·
                <span data-totals="excluded-count">0</span> excluded
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    `;
    list.appendChild(div);

    // Wire up checkboxes
    const table = div.querySelector('.invoice-table');
    const allBox = table.querySelector('.include-all');

    function recompute() {
      let inclTax = 0, inclTaxable = 0, inclTotal = 0;
      let inclCount = 0, exclCount = 0;
      table.querySelectorAll('.row-include').forEach(cb => {
        const tr = cb.closest('tr');
        const taxable = parseFloat(tr.dataset.taxable || 0);
        const tax = parseFloat(tr.dataset.tax || 0);
        const total = parseFloat(tr.dataset.total || 0);
        if (cb.checked) {
          inclCount++;
          inclTaxable += taxable;
          inclTax += tax;
          inclTotal += total;
        } else {
          exclCount++;
        }
      });
      table.querySelector('[data-totals="taxable"]').textContent = inr(inclTaxable);
      table.querySelector('[data-totals="tax"]').textContent = inr(inclTax);
      table.querySelector('[data-totals="value"]').innerHTML = '<b>' + inr(inclTotal) + '</b>';
      table.querySelector('[data-totals="included-count"]').textContent = inclCount;
      table.querySelector('[data-totals="excluded-count"]').textContent = exclCount;
    }
    // Make recompute accessible to row-replacement code (cell editing)
    table.addEventListener('totals-recompute', recompute);

    allBox.addEventListener('change', (e) => {
      const checked = e.target.checked;
      table.querySelectorAll('.row-include').forEach(cb => {
        cb.checked = checked;
        toggleExclusion(p.firm_id, cb.dataset.key, !checked);
      });
      recompute();
      updateReviewSummary();
    });
    table.querySelectorAll('.row-include').forEach(cb => {
      cb.addEventListener('change', (e) => {
        toggleExclusion(p.firm_id, cb.dataset.key, !e.target.checked);
        const allChecked = Array.from(table.querySelectorAll('.row-include')).every(c => c.checked);
        allBox.checked = allChecked;
        recompute();
        updateReviewSummary();
      });
    });
    // Trash icon clicks
    table.querySelectorAll('.row-trash').forEach(btn => {
      btn.addEventListener('click', () => {
        const cb = btn.closest('tr').querySelector('.row-include');
        cb.checked = !cb.checked;
        cb.dispatchEvent(new Event('change'));
      });
    });
    // Carry-forward Apply handler
    const cfApplyBtn = div.querySelector('.cf-apply-btn');
    if (cfApplyBtn) {
      cfApplyBtn.addEventListener('click', async () => {
        const banner = div.querySelector('.carry-forward-banner');
        const cfStatus = banner.querySelector('.cf-status');
        const picked = [];
        banner.querySelectorAll('.cf-include').forEach(cb => {
          if (cb.checked) {
            const idx = parseInt(cb.dataset.idx, 10);
            const item = (p.carried_forward || [])[idx];
            if (item) picked.push(item);
          }
        });
        if (!picked.length) {
          cfStatus.textContent = 'Pick at least one invoice first.';
          return;
        }
        cfApplyBtn.disabled = true;
        cfStatus.textContent = 'Adding…';
        try {
          const res = await fetch('/api/carry_forward/apply', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              batch_id: currentBatchId,
              firm_id: p.firm_id,
              items: picked,
            }),
          });
          const j = await res.json();
          if (!j.ok) {
            cfStatus.textContent = 'Failed: ' + (j.error || 'unknown');
            cfApplyBtn.disabled = false;
            return;
          }
          cfStatus.innerHTML = '<span style="color:#15803d;">✓ Added ' + j.added + ' invoice(s). Refresh preview to see them in the table.</span>';
          banner.querySelectorAll('.cf-include').forEach(cb => { cb.checked = false; cb.disabled = true; });
          // Append new rows to the invoice table directly
          const tbody = table.querySelector('tbody');
          (j.invoices || []).forEach(inv => {
            if (tbody.querySelector(`tr[data-key="${inv.key}"]`)) return;
            tbody.insertAdjacentHTML('beforeend', renderInvoiceRow(inv));
          });
          recompute();
        } catch (err) {
          cfStatus.textContent = 'Network error: ' + err.message;
          cfApplyBtn.disabled = false;
        }
      });
    }

    // Supply Type dropdowns
    table.querySelectorAll('.row-supply-type').forEach(sel => {
      sel.addEventListener('change', (e) => {
        setOverride(p.firm_id, e.target.dataset.key,
          { supply_type: e.target.value });
      });
    });
    // RCM checkboxes
    table.querySelectorAll('.row-rcm').forEach(cb => {
      cb.addEventListener('change', (e) => {
        setOverride(p.firm_id, e.target.dataset.key,
          { reverse_charge: e.target.checked ? 'Y' : 'N' });
      });
    });

    // Inline editing for invoice_no, invoice_date, gstin (delegated)
    table.addEventListener('click', (e) => {
      const cell = e.target.closest('.editable');
      if (!cell || cell.querySelector('input')) return;
      startEditCell(cell, p.firm_id);
    });

    // Initial compute
    recompute();
  });

  section.hidden = false;
  section.scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (processBtn) processBtn.disabled = false;
  updateReviewSummary();
}

function renderInvoiceRow(inv) {
  const typeLabel = inv.doc_type === 'C' ? 'Credit Note' :
                    inv.doc_type === 'D' ? 'Debit Note' : 'Invoice';
  const typeClass = inv.doc_type === 'C' ? 'type-cn' :
                    inv.doc_type === 'D' ? 'type-dn' : 'type-inv';
  const bucket = inv.is_b2b ? 'B2B' : (inv.is_interstate ? 'B2CL' : 'B2CS');
  const gstinDisplay = inv.gstin || 'unregistered';
  const gstinClass = inv.gstin ? 'cust-gstin mono' : 'cust-gstin muted';

  return `
    <tr data-key="${escapeHtml(inv.key)}"
        data-taxable="${inv.taxable_value || 0}"
        data-tax="${inv.total_tax || 0}"
        data-total="${inv.invoice_value || 0}">
      <td><input type="checkbox" class="row-include" data-key="${escapeHtml(inv.key)}" checked></td>
      <td><span class="type-pill ${typeClass}">${typeLabel}</span></td>
      <td class="mono editable" data-field="invoice_no" title="Click to edit">${escapeHtml(inv.invoice_no)}</td>
      <td class="mono editable" data-field="invoice_date" title="Click to edit">${escapeHtml(inv.invoice_date)}</td>
      <td>
        <div class="cust-name">${escapeHtml(inv.customer_name || '—')}</div>
        <div class="${gstinClass} editable" data-field="gstin" title="Click to edit">${escapeHtml(gstinDisplay)}</div>
      </td>
      <td class="num">${inr(inv.taxable_value)}</td>
      <td class="num">${inr(inv.total_tax)}</td>
      <td class="num"><b>${inr(inv.invoice_value)}</b></td>
      <td>${renderSupplyTypeSelect(inv)}</td>
      <td style="text-align:center;">${renderRcmCheckbox(inv)}</td>
      <td><span class="bucket-pill bucket-${bucket.toLowerCase()}">${bucket}</span></td>
      <td class="col-actions"><button type="button" class="row-trash" title="Toggle exclude" aria-label="Exclude this invoice"></button></td>
    </tr>
  `;
}

function renderCarryForwardBanner(p) {
  const cf = p.carried_forward || [];
  if (!cf.length) return '';
  const prev = p.carried_period || 'previous month';
  const rows = cf.map((inv, idx) => `
    <tr data-cf-idx="${idx}">
      <td style="padding:6px 8px;">
        <input type="checkbox" class="cf-include" data-firm-id="${escapeHtml(p.firm_id)}" data-idx="${idx}">
      </td>
      <td style="padding:6px 8px;">${escapeHtml(inv.invoice_no || '')}</td>
      <td style="padding:6px 8px;">${escapeHtml(inv.invoice_date || '')}</td>
      <td style="padding:6px 8px;">${escapeHtml(inv.customer_name || '—')}</td>
      <td style="padding:6px 8px; font-family: 'JetBrains Mono', monospace; font-size: 11px;">${escapeHtml(inv.gstin || '')}</td>
      <td style="padding:6px 8px; text-align:right;">${inr(inv.taxable_value)}</td>
      <td style="padding:6px 8px; text-align:right;">${inr(inv.total_tax)}</td>
      <td style="padding:6px 8px; text-align:right;"><b>${inr(inv.invoice_value)}</b></td>
    </tr>
  `).join('');
  return `
    <details class="carry-forward-banner" style="background:#fef3c7;border:1px solid #fcd34d;border-radius:6px;padding:12px 14px;margin:10px 0;">
      <summary style="cursor:pointer;font-weight:600;color:#92400e;">
        ⚠ ${cf.length} invoice${cf.length !== 1 ? 's' : ''} excluded from ${prev} — review for this month
      </summary>
      <div style="margin-top:10px;font-size:12px;color:#78350f;">
        Tick the invoices you want to <b>include in the current month's GSTR-1</b>. These will be added to your current preview when you click "Apply".
      </div>
      <table style="margin-top:8px;width:100%;border-collapse:collapse;font-size:12px;background:#fffbeb;">
        <thead>
          <tr style="background:#fcd34d;color:#78350f;">
            <th style="padding:6px 8px;text-align:left;width:30px;">Include?</th>
            <th style="padding:6px 8px;text-align:left;">Invoice</th>
            <th style="padding:6px 8px;text-align:left;">Date</th>
            <th style="padding:6px 8px;text-align:left;">Customer</th>
            <th style="padding:6px 8px;text-align:left;">GSTIN</th>
            <th style="padding:6px 8px;text-align:right;">Taxable</th>
            <th style="padding:6px 8px;text-align:right;">Tax</th>
            <th style="padding:6px 8px;text-align:right;">Total</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <div style="margin-top:10px;display:flex;gap:8px;align-items:center;">
        <button type="button" class="cf-apply-btn" data-firm-id="${escapeHtml(p.firm_id)}"
                style="padding:6px 14px;background:#92400e;color:#fff;border:none;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer;">
          Apply Selected to Current Month
        </button>
        <span class="cf-status" style="font-size:11.5px;color:#78350f;"></span>
      </div>
    </details>
  `;
}

const SUPPLY_TYPE_OPTIONS = [
  ['REGULAR',      'Regular (taxable)'],
  ['SEZ_WPAY',     'SEZ — with payment'],
  ['SEZ_WOPAY',    'SEZ — without payment (LUT)'],
  ['EXPORT_WPAY',  'Export — with payment'],
  ['EXPORT_WOPAY', 'Export — without payment (LUT)'],
  ['DEEMED',       'Deemed export'],
  ['NIL',          'Nil rated'],
  ['EXEMPT',       'Exempt'],
  ['NON_GST',      'Non-GST'],
];

function renderSupplyTypeSelect(inv) {
  const cur = (inv.supply_type || 'REGULAR').toUpperCase();
  const auto = (inv.supply_type_auto || 'REGULAR').toUpperCase();
  // Highlight when server inferred a non-regular type (so user notices)
  const inferred = auto !== 'REGULAR';
  const borderColor = inferred ? '#d97706' : '#d0d0d8';
  const bgColor = inferred ? '#fffbeb' : '#fff';
  const title = inferred ? `Auto-detected: ${auto.replace('_',' ')} (change if wrong)`
                         : 'Choose supply type';
  const opts = SUPPLY_TYPE_OPTIONS.map(([v, l]) =>
    `<option value="${v}" ${v === cur ? 'selected' : ''}>${l}</option>`
  ).join('');
  return `<select class="row-supply-type" data-key="${escapeHtml(inv.key)}"
            title="${escapeHtml(title)}"
            style="font-size:11px;padding:3px 5px;border:1px solid ${borderColor};border-radius:4px;background:${bgColor};max-width:170px;">${opts}</select>`;
}

function renderRcmCheckbox(inv) {
  const checked = (inv.reverse_charge || 'N').toUpperCase() === 'Y';
  return `<input type="checkbox" class="row-rcm" data-key="${escapeHtml(inv.key)}" ${checked ? 'checked' : ''} title="Mark this invoice as Reverse Charge applicable">`;
}

function inr(n) {
  return Number(n || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ----- Inline cell editing for invoice_no, invoice_date, gstin --------
function startEditCell(cell, firmId) {
  const tr = cell.closest('tr');
  const docKey = tr.dataset.key;
  const field = cell.dataset.field;
  const originalText = cell.textContent.trim();
  const currentValue = (originalText === 'unregistered' || originalText === '—') ? '' : originalText;

  // Replace cell content with input
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'cell-edit-input';
  input.value = currentValue;
  if (field === 'invoice_date') {
    input.placeholder = 'DD-MM-YYYY';
  } else if (field === 'gstin') {
    input.placeholder = '15-char GSTIN or empty';
    input.maxLength = 15;
    input.style.textTransform = 'uppercase';
  }

  cell.dataset.original = originalText;
  cell.innerHTML = '';
  cell.appendChild(input);
  input.focus();
  input.select();

  let cancelled = false;
  const cleanup = () => {
    cell.removeEventListener('keydown', onKey);
    input.removeEventListener('blur', onBlur);
  };
  const restore = () => {
    cleanup();
    cell.textContent = cell.dataset.original;
    delete cell.dataset.original;
  };

  const commit = async () => {
    cleanup();
    const newValue = input.value.trim();
    if (newValue === currentValue) {
      // No change — just restore display
      cell.textContent = cell.dataset.original;
      delete cell.dataset.original;
      return;
    }
    // Show "saving" state
    cell.innerHTML = '<span class="cell-saving">saving…</span>';
    try {
      const res = await fetch('/api/edit_invoice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          batch_id: currentBatchId,
          firm_id: firmId,
          doc_key: docKey,
          field: field,
          value: newValue,
        }),
      });
      const data = await res.json();
      if (!data.ok) {
        alert(data.error || 'Could not update.');
        cell.textContent = cell.dataset.original;
        delete cell.dataset.original;
        return;
      }
      // Server returned updated invoice; re-render the entire row
      const updatedInv = data.invoice;
      const oldKey = data.old_key;
      const newRowHtml = renderInvoiceRow(updatedInv);
      const tmp = document.createElement('tbody');
      tmp.innerHTML = newRowHtml;
      const newTr = tmp.firstElementChild;
      // Mark as edited
      newTr.classList.add('row-edited');
      // If the old row was excluded, transfer that state
      const wasExcluded = currentExclusions[firmId] && currentExclusions[firmId].has(oldKey);
      if (wasExcluded) {
        currentExclusions[firmId].delete(oldKey);
        currentExclusions[firmId].add(updatedInv.key);
        const cb = newTr.querySelector('.row-include');
        if (cb) cb.checked = false;
      }
      tr.replaceWith(newTr);
      // Re-bind handlers on new row
      const cb = newTr.querySelector('.row-include');
      cb.addEventListener('change', (e) => {
        toggleExclusion(firmId, cb.dataset.key, !e.target.checked);
        const allBox = newTr.closest('table').querySelector('.include-all');
        const allChecked = Array.from(newTr.closest('tbody').querySelectorAll('.row-include')).every(c => c.checked);
        if (allBox) allBox.checked = allChecked;
        // Trigger recompute
        const evt = new Event('totals-recompute', { bubbles: true });
        newTr.dispatchEvent(evt);
        updateReviewSummary();
      });
      const trash = newTr.querySelector('.row-trash');
      if (trash) {
        trash.addEventListener('click', () => {
          cb.checked = !cb.checked;
          cb.dispatchEvent(new Event('change'));
        });
      }
      // Recompute totals
      const evt = new Event('totals-recompute', { bubbles: true });
      newTr.dispatchEvent(evt);
    } catch (err) {
      alert('Network error: ' + err.message);
      cell.textContent = cell.dataset.original;
      delete cell.dataset.original;
    }
  };

  const onBlur = () => {
    if (cancelled) return;
    commit();
  };
  const onKey = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      input.blur();
    } else if (e.key === 'Escape') {
      cancelled = true;
      restore();
    }
  };
  input.addEventListener('blur', onBlur);
  cell.addEventListener('keydown', onKey);
}

function toggleExclusion(firmId, docKey, exclude) {
  if (!currentExclusions[firmId]) currentExclusions[firmId] = new Set();
  if (exclude) currentExclusions[firmId].add(docKey);
  else currentExclusions[firmId].delete(docKey);
}

function setOverride(firmId, docKey, patch) {
  if (!currentOverrides[firmId]) currentOverrides[firmId] = {};
  currentOverrides[firmId][docKey] = Object.assign(
    currentOverrides[firmId][docKey] || {}, patch);
  // Update review-hint with count
  const cnt = Object.values(currentOverrides)
    .reduce((n, m) => n + Object.keys(m).length, 0);
  const hint = document.getElementById('review-hint');
  if (hint && cnt > 0) {
    hint.textContent = `${cnt} invoice${cnt !== 1 ? 's' : ''} customised`;
  }
}

function updateReviewSummary() {
  const hint = document.getElementById('review-hint');
  let totalExcluded = 0;
  Object.values(currentExclusions).forEach(s => { totalExcluded += s.size; });
  if (hint) {
    hint.textContent = totalExcluded > 0 ?
      `${totalExcluded} invoice${totalExcluded !== 1 ? 's' : ''} excluded` : '';
  }
}

// Back button
document.addEventListener('click', (e) => {
  if (e.target && e.target.id === 'back-btn') {
    document.getElementById('review-section').hidden = true;
    document.getElementById('results-section').hidden = true;
    currentBatchId = null;
  }
});

// Generate button
document.addEventListener('click', async (e) => {
  if (!e.target || e.target.id !== 'generate-btn') return;
  if (!currentBatchId) return;

  const exclusions = {};
  Object.entries(currentExclusions).forEach(([fid, set]) => {
    if (set.size > 0) exclusions[fid] = Array.from(set);
  });

  showOverlay(true, 'Building JSON & Excel reports…');
  e.target.disabled = true;
  try {
    const overrides = {};
    Object.entries(currentOverrides).forEach(([firmId, m]) => {
      if (m && Object.keys(m).length) overrides[firmId] = m;
    });
    const res = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ batch_id: currentBatchId, exclusions, overrides }),
    });
    const data = await res.json();
    showOverlay(false);
    e.target.disabled = false;
    if (!data.ok) {
      alert(data.error || 'Generation failed.');
      return;
    }
    document.getElementById('review-section').hidden = true;
    renderResults(data);
  } catch (err) {
    showOverlay(false);
    e.target.disabled = false;
    alert('Network error: ' + err.message);
  }
});

// ===== Render results ======================================================
function renderResults(data) {
  const section = document.getElementById('results-section');
  const list = document.getElementById('results');
  const zipLink = document.getElementById('download-zip');
  list.innerHTML = '';

  data.results.forEach((r) => {
    const card = document.querySelector(`.firm-card[data-firm-id="${r.firm_id}"]`);
    if (card) {
      card.classList.remove('processing');
      card.classList.add(r.ok ? 'success' : 'error');
      card.querySelector('.status-text').textContent = r.ok ? 'Done' : 'Failed';
    }

    const div = document.createElement('div');
    div.className = 'result-card' + (r.ok ? '' : ' error');

    if (!r.ok) {
      div.innerHTML = `
        <div class="result-head">
          <div class="result-name">${escapeHtml(r.firm_name || 'Unknown firm')}</div>
        </div>
        <div class="error-msg">${escapeHtml(r.error)}</div>
      `;
      list.appendChild(div);
      return;
    }

    const s = r.stats;
    const warningsHtml = (r.warnings && r.warnings.length)
      ? `<div class="warnings-list"><ul>${r.warnings.map(w => `<li>${escapeHtml(w)}</li>`).join('')}</ul></div>`
      : '';

    const excludedHtml = s.excluded ? `
      <div class="stat"><span class="stat-value warn">${s.excluded}</span><span class="stat-label">Excluded</span></div>` : '';

    div.innerHTML = `
      <div class="result-head">
        <div>
          <div class="result-name">${escapeHtml(r.firm_name)}</div>
          <div class="result-gstin">${escapeHtml(r.firm_gstin)} · ${escapeHtml(data.period_label)}</div>
        </div>
      </div>
      <div class="stats-grid">
        <div class="stat"><span class="stat-value">${s.invoices ?? 0}</span><span class="stat-label">Invoices</span></div>
        <div class="stat"><span class="stat-value">${s.b2b ?? 0}</span><span class="stat-label">B2B</span></div>
        <div class="stat"><span class="stat-value">${s.b2cl ?? 0}</span><span class="stat-label">B2CL</span></div>
        <div class="stat"><span class="stat-value">${s.b2cs ?? 0}</span><span class="stat-label">B2CS</span></div>
        ${(s.cdnr ?? 0) > 0 ? `<div class="stat"><span class="stat-value">${s.cdnr}</span><span class="stat-label">CDNR</span></div>` : ''}
        ${(s.cdnur ?? 0) > 0 ? `<div class="stat"><span class="stat-value">${s.cdnur}</span><span class="stat-label">CDNUR</span></div>` : ''}
        ${excludedHtml}
      </div>
      ${warningsHtml}
      <div class="result-actions">
        <a href="${r.json_url}" class="btn-primary">Download JSON</a>
        <a href="${r.report_url}" class="btn-ghost">Download Report (xlsx)</a>
      </div>
    `;
    list.appendChild(div);
  });

  if (data.zip_url) {
    zipLink.href = data.zip_url;
    zipLink.hidden = false;
  }
  section.hidden = false;

  // Show "Continue to GSTR-3B" / "Back to edit" row if at least one firm succeeded
  const anyOk = (data.results || []).some(r => r.ok);
  const nextRow = document.getElementById('next-step-row');
  if (nextRow && anyOk) {
    nextRow.style.display = 'flex';
    const first = (data.results || []).find(r => r.ok);
    const period = (first && first.period) || (window._currentPeriod || '');
    const firmId = (first && (first.firm_id || first.firm_gstin)) || '';
    const url = '/gstr3b'
      + (firmId ? '?firm=' + encodeURIComponent(firmId) : '')
      + (period ? (firmId ? '&' : '?') + 'period=' + encodeURIComponent(period) : '');
    const link = document.getElementById('continue-to-3b-btn');
    if (link) link.href = url;
  }
  const backBtn = document.getElementById('back-to-upload-btn');
  if (backBtn && !backBtn._wired) {
    backBtn._wired = true;
    backBtn.addEventListener('click', () => {
      document.getElementById('results-section').hidden = true;
      if (nextRow) nextRow.style.display = 'none';
      // Re-enable review section
      const review = document.getElementById('review-section');
      if (review) {
        review.hidden = false;
        review.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  }

  section.scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (processBtn) processBtn.disabled = false;
}

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
