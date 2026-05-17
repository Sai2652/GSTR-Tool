/* Customers cache page — search, edit, delete */

// ===== Search filter =======================================================
const searchInput = document.getElementById('cache-search');
const listEl = document.getElementById('cache-list');

if (searchInput && listEl) {
  searchInput.addEventListener('input', () => {
    const q = searchInput.value.trim().toLowerCase();
    const rows = listEl.querySelectorAll('.cache-row');
    rows.forEach(row => {
      const data = row.dataset.search || '';
      row.style.display = (!q || data.includes(q)) ? '' : 'none';
    });
  });
}

// ===== Edit name ===========================================================
document.querySelectorAll('[data-action="edit"]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const row = btn.closest('.cache-row');
    const gstin = row.dataset.gstin;
    const nameEl = row.querySelector('[data-name]');
    const currentName = nameEl.textContent.trim();
    const newName = prompt(`Edit customer name for ${gstin}:`, currentName);
    if (newName === null) return;
    const trimmed = newName.trim();
    if (!trimmed || trimmed === currentName) return;
    try {
      const res = await fetch(`/api/customers/${encodeURIComponent(gstin)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: trimmed }),
      });
      const data = await res.json();
      if (data.ok) {
        nameEl.textContent = trimmed;
        // update search index
        row.dataset.search = (gstin + ' ' + trimmed).toLowerCase();
      } else {
        alert(data.error || 'Could not update.');
      }
    } catch (e) {
      alert('Network error: ' + e.message);
    }
  });
});

// ===== Delete one ==========================================================
document.querySelectorAll('[data-action="delete"]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const row = btn.closest('.cache-row');
    const gstin = row.dataset.gstin;
    const name = row.querySelector('[data-name]').textContent.trim();
    if (!confirm(`Remove "${name}" (${gstin}) from the cache?\n\nThis will be re-added next time the GSTIN appears in a processed sheet.`)) return;
    try {
      const res = await fetch(`/api/customers/${encodeURIComponent(gstin)}`, { method: 'DELETE' });
      const data = await res.json();
      if (data.ok) row.remove();
      else alert('Could not remove.');
    } catch (e) {
      alert('Network error: ' + e.message);
    }
  });
});

// ===== Clear all ===========================================================
const clearAllBtn = document.getElementById('clear-all-customers');
if (clearAllBtn) {
  clearAllBtn.addEventListener('click', async () => {
    if (!confirm('Clear the entire customer cache?\n\nAll cached GSTIN → name mappings will be deleted. The cache will rebuild next time you process a sheet.')) return;
    try {
      const res = await fetch('/api/customers/clear', { method: 'POST' });
      const data = await res.json();
      if (data.ok) location.reload();
      else alert('Could not clear cache.');
    } catch (e) {
      alert('Network error: ' + e.message);
    }
  });
}
