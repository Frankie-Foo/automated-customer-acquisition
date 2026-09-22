const selectedContactKey = "salesbot:selected-contact-id";
const selectedBatchKey = "salesbot:selected-outreach-batch";

export function selectedWorkspaceBatch(contactId) {
  try {
    const value = JSON.parse(window.sessionStorage.getItem(selectedBatchKey) || "null");
    return value?.contactId === Number(contactId) && Number.isInteger(value.batchId) && value.batchId > 0 ? value.batchId : null;
  } catch {
    return null;
  }
}

export function workspaceDraftBatch(contactId, draft) {
  if (Number(draft?.contact_id) === Number(contactId) && Number.isInteger(draft?.campaign_id) && draft.campaign_id > 0) {
    return draft.campaign_id;
  }
  return selectedWorkspaceBatch(contactId);
}

export function rememberWorkspaceContact(contactId) {
  const parsed = Number(contactId);
  if (!Number.isInteger(parsed) || parsed <= 0) return null;
  try {
    window.sessionStorage.setItem(selectedContactKey, String(parsed));
  } catch {
    // The custom event still supports browsers where storage is unavailable.
  }
  return parsed;
}

export function selectedWorkspaceContact() {
  try {
    return rememberWorkspaceContact(window.sessionStorage.getItem(selectedContactKey));
  } catch {
    return null;
  }
}

export function openContactWorkspace(contactId, delay = 0, batchId = null) {
  const parsed = rememberWorkspaceContact(contactId);
  if (!parsed) return;
  try {
    window.sessionStorage.setItem(selectedBatchKey, JSON.stringify({ contactId: parsed, batchId: Number(batchId) || null }));
  } catch {
    // Server membership checks remain authoritative when storage is unavailable.
  }
  window.location.hash = "outreach";
  window.setTimeout(() => {
    window.dispatchEvent(new CustomEvent("salesbot:open-contact", { detail: { contactId: parsed } }));
  }, delay);
}
