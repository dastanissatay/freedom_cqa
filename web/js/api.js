// Тонкий клиент API. Базовый URL берётся из window.CQA_CONFIG.apiBase.
const base = () => (window.CQA_CONFIG?.apiBase || "").replace(/\/$/, "");

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(base() + path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    let msg = data?.detail ?? data ?? res.statusText;
    if (Array.isArray(msg)) msg = msg.map((e) => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("; ");
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

const enc = encodeURIComponent;

export const api = {
  url: (path) => base() + path,
  config: () => request("GET", "/api/config"),
  conversations: (gid, refresh = false) => request("GET", `/api/datasets/${enc(gid)}/conversations${refresh ? "?refresh=true" : ""}`),
  importRecords: (gid, payload) => request("POST", `/api/datasets/${enc(gid)}/import`, payload),
  conversation: (gid, cid) => request("GET", `/api/conversations/${enc(gid)}/${enc(cid)}`),
  checklists: () => request("GET", "/api/checklists"),
  version: (vid) => request("GET", `/api/checklists/versions/${vid}`),
  createChecklist: (config, comment) => request("POST", "/api/checklists", { config, comment }),
  addVersion: (cid, config, comment) => request("POST", `/api/checklists/${cid}/versions`, { config, comment }),
  evaluate: (gid, cid, vid) => request("POST", "/api/evaluations", { group_id: gid, conversation_id: cid, checklist_version_id: vid }),
  evaluations: (gid, cid) => request("GET", `/api/evaluations?group_id=${enc(gid)}&conversation_id=${enc(cid)}`),
  evaluation: (id) => request("GET", `/api/evaluations/${id}`),
  createAppeal: (evaluation_id, criterion_id, comment) => request("POST", "/api/appeals", { evaluation_id, criterion_id, comment }),
  appeals: (params = {}) => request("GET", `/api/appeals?${new URLSearchParams(params)}`),
  decide: (id, decision, new_answer, reason) => request("POST", `/api/appeals/${id}/decision`, { decision, new_answer, reason }),
  startBatch: (body) => request("POST", "/api/batch", body),
  batch: (id) => request("GET", `/api/batch/${id}`),
  cancelBatch: (id) => request("POST", `/api/batch/${id}/cancel`),
  reference: (gid, cid, vid) => request("GET", `/api/references/${enc(gid)}/${enc(cid)}?checklist_version_id=${vid}`),
  saveReference: (gid, cid, body) => request("PUT", `/api/references/${enc(gid)}/${enc(cid)}`, body),
  deleteReference: (gid, cid, vid) => request("DELETE", `/api/references/${enc(gid)}/${enc(cid)}?checklist_version_id=${vid}`),
  importReferences: (body) => request("POST", "/api/references/import", body),
  quality: (gid, vid) => request("GET", `/api/quality?group_id=${enc(gid)}&checklist_version_id=${vid}`),
  exportUrl: (gid, vid, fmt = "csv") => base() + `/api/export/${enc(gid)}?fmt=${fmt}${vid ? `&checklist_version_id=${vid}` : ""}`,
  // запись звонка всегда через бэкенд: audio_url ведёт на HTML-страницу с плеером, а не на файл
  audioUrl: (gid, cid) => base() + `/api/audio/${enc(gid)}/${enc(cid)}`,
};
