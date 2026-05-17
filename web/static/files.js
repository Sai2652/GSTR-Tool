/* Files page — clear and delete */

document.querySelectorAll('[data-clear]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const target = btn.dataset.clear;
    const label = target === 'uploads' ? 'all uploaded sales sheets'
                : target === 'outputs' ? 'all generated outputs (JSONs + reports)'
                : 'EVERYTHING (uploads and outputs)';
    if (!confirm(`Delete ${label}?\n\nThis cannot be undone.`)) return;
    btn.disabled = true;
    try {
      const res = await fetch('/api/files/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target }),
      });
      const data = await res.json();
      if (data.ok) {
        location.reload();
      } else {
        alert('Could not clear files.');
        btn.disabled = false;
      }
    } catch (e) {
      alert('Network error: ' + e.message);
      btn.disabled = false;
    }
  });
});

document.querySelectorAll('[data-action="delete-one"]').forEach((btn) => {
  btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    const target = btn.dataset.target;
    const path = btn.dataset.path;
    if (!confirm(`Delete "${path}"?`)) return;
    try {
      const res = await fetch('/api/files/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target, path }),
      });
      const data = await res.json();
      if (data.ok) {
        const row = btn.closest('.storage-row');
        if (row) row.remove();
      } else {
        alert('Could not delete file.');
      }
    } catch (e) {
      alert('Network error: ' + e.message);
    }
  });
});
