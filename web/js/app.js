import { api } from "./api.js";
import { state, setState, subscribe, allVersions, reloadChecklists } from "./state.js";
import { h, add, clear, toast } from "./util.js";
import * as dialogList from "./components/dialog-list.js";
import * as dialogCard from "./components/dialog-card.js";
import * as appealsPage from "./components/reviewer-queue.js";
import * as checklistEditor from "./components/checklist-editor.js";
import * as batchRunner from "./components/batch-runner.js";
import * as qualityPage from "./components/quality.js";
import * as summaryPage from "./components/summary.js";

const main = document.getElementById("cqa-main");
const controls = document.getElementById("cqa-controls");
let current = null;

function renderControls() {
  clear(controls);
  const gid = h("input", { class: "inp gid", value: state.groupId, title: "group_id набора данных", placeholder: "group_id" });
  gid.addEventListener("change", () => setState({ groupId: gid.value.trim() || "DEV" }));
  gid.addEventListener("keydown", (e) => { if (e.key === "Enter") gid.blur(); });

  const sel = h("select", { class: "inp", title: "Активный чек-лист (версия)" },
    allVersions().map((v) => h("option", { value: v.id, selected: v.id === state.versionId }, `#${v.checklist_id} ${v.name} · v${v.version}`)));
  sel.addEventListener("change", () => setState({ versionId: Number(sel.value) }));

  const role = h("div", { class: "role-toggle", title: "Режим интерфейса" },
    h("button", { class: state.role === "operator" ? "on" : "", onclick: () => setState({ role: "operator" }) }, "Оператор / Супервайзер"),
    h("button", { class: state.role === "reviewer" ? "on" : "", onclick: () => setState({ role: "reviewer" }) }, "Проверяющий"));

  add(controls,
    h("label", { class: "ctl" }, h("span", {}, "group_id"), gid),
    h("label", { class: "ctl" }, h("span", {}, "Чек-лист"), sel),
    role,
    state.config ? h("span", { class: "model-chip", title: "LLM_PROVIDER / LLM_MODEL" }, `${state.config.provider}: ${state.config.model}${state.config.pii_masking ? " · ПДн скрыты" : ""}`) : null,
  );
}

function parseRoute() {
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  return { name: parts[0] || "dialogs", parts, params: new URLSearchParams(query) };
}

function route() {
  const r = parseRoute();
  current?.destroy?.();
  clear(main);
  document.querySelectorAll("#cqa-nav a").forEach((a) => {
    const n = a.dataset.route;
    a.classList.toggle("active", n === r.name || (n === "dialogs" && r.name === "dialog"));
  });
  const ctx = { route: r, navigate: (hash) => (location.hash = hash) };
  const views = { dialogs: dialogList, dialog: dialogCard, appeals: appealsPage, checklists: checklistEditor, batch: batchRunner, summary: summaryPage, quality: qualityPage };
  const view = views[r.name] || dialogList;
  current = view.mount(main, ctx);
}

subscribe((s, patch) => {
  renderControls();
  // перерисовываем экран при смене группы/роли/чек-листа
  if ("groupId" in patch || "role" in patch || "versionId" in patch) {
    if (current?.update) current.update(patch);
    else if (current) route();
  }
});

window.addEventListener("hashchange", route);

(async function init() {
  renderControls();
  try {
    const cfg = await api.config();
    const patch = { config: cfg };
    if (!localStorage.getItem("cqa.state.v1") && cfg.default_group_id) patch.groupId = cfg.default_group_id;
    setState(patch);
    await reloadChecklists();
  } catch (e) {
    toast(`Бэкенд недоступен: ${e.message}`, "error");
  }
  route();
})();
