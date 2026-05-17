/* Firms page — add/delete/live-validate + auto-fill name from cache */

const form = document.getElementById('add-firm-form');
const gstinInput = form.querySelector('input[name="gstin"]');
const nameInput = form.querySelector('input[name="name"]');
const legalNameInput = form.querySelector('input[name="legal_name"]');
const hint = document.getElementById('gstin-hint');

let checkTimer;
let lastAutofilledName = "";   // track to know when we may overwrite
let lastAutofilledLegal = "";

gstinInput.addEventListener('input', (e) => {
  e.target.value = e.target.value.toUpperCase().replace(/\s/g, '');
  clearTimeout(checkTimer);
  const v = e.target.value;
  if (v.length === 0) {
    hint.textContent = '';
    hint.className = 'gstin-hint';
    return;
  }
  if (v.length < 15) {
    hint.textContent = `${v.length}/15 characters`;
    hint.className = 'gstin-hint';
    return;
  }
  hint.textContent = 'Verifying…';
  hint.className = 'gstin-hint';
  checkTimer = setTimeout(async () => {
    try {
      const res = await fetch('/api/check_gstin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gstin: v }),
      });
      const data = await res.json();
      if (data.valid) {
        if (data.cached_name) {
          hint.textContent = `✓ Valid · auto-filled from cache: ${data.cached_name}`;
          hint.className = 'gstin-hint valid';
          // Auto-fill name fields if empty or if previous value was our last autofill
          if (!nameInput.value.trim() || nameInput.value.trim() === lastAutofilledName) {
            nameInput.value = data.cached_name;
            lastAutofilledName = data.cached_name;
          }
          if (!legalNameInput.value.trim() || legalNameInput.value.trim() === lastAutofilledLegal) {
            legalNameInput.value = data.cached_name;
            lastAutofilledLegal = data.cached_name;
          }
        } else {
          hint.textContent = '✓ Valid GSTIN — checksum verified (not in cache yet)';
          hint.className = 'gstin-hint valid';
        }
      } else {
        hint.textContent = `✗ Invalid: ${data.reason}`;
        hint.className = 'gstin-hint invalid';
      }
    } catch (err) {
      hint.textContent = 'Could not verify';
      hint.className = 'gstin-hint invalid';
    }
  }, 200);
});

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(form);
  const payload = {
    name: fd.get('name'),
    gstin: fd.get('gstin'),
    legal_name: fd.get('legal_name'),
  };
  const res = await fetch('/api/firms', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (data.ok) {
    window.location.reload();
  } else {
    alert(data.error || 'Could not save firm.');
  }
});

// Delete firm
document.querySelectorAll('[data-action="delete"]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const row = btn.closest('.firm-row');
    const firmId = row.dataset.firmId;
    if (!confirm(`Remove firm "${row.querySelector('.firm-row-name').textContent}"?\n\nThis only removes the saved profile, not any data files.`)) return;
    const res = await fetch(`/api/firms/${encodeURIComponent(firmId)}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.ok) row.remove();
    else alert('Could not delete firm.');
  });
});
