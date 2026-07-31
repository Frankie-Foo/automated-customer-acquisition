const selectedContactKey = "salesbot:selected-contact-id";

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

export function openContactWorkspace(contactId, delay = 0) {
  const parsed = rememberWorkspaceContact(contactId);
  if (!parsed) return;
  window.location.hash = "outreach";
  window.setTimeout(() => {
    window.dispatchEvent(new CustomEvent("salesbot:open-contact", { detail: { contactId: parsed } }));
  }, delay);
}
