// Глобальное состояние приложения (группа, роль, активная версия чек-листа) с подпиской.
const KEY = "cqa.state.v1";
const listeners = new Set();

function load() {
  try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; }
}

export const state = {
  groupId: window.CQA_CONFIG?.defaultGroupId || "DEV",
  role: "operator", // operator | reviewer
  versionId: null,
  checklists: [],
  config: null,
  ...load(),
};

export function setState(patch) {
  Object.assign(state, patch);
  try {
    localStorage.setItem(KEY, JSON.stringify({ groupId: state.groupId, role: state.role, versionId: state.versionId }));
  } catch { /* приватный режим — не критично */ }
  listeners.forEach((fn) => fn(state, patch));
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export async function reloadChecklists(selectVersionId) {
  const { api } = await import("./api.js");
  const checklists = await api.checklists();
  const versions = checklists.flatMap((c) => c.versions.map((v) => v.id));
  let versionId = selectVersionId ?? state.versionId;
  if (!versions.includes(versionId)) versionId = checklists[0]?.latest_version_id ?? null;
  setState({ checklists, versionId });
}

export function allVersions() {
  return state.checklists.flatMap((c) => c.versions.map((v) => ({ ...v, checklist_id: c.id, name: c.name })));
}
