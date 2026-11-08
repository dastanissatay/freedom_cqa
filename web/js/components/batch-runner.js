// Пакетная оценка набора (контрольный group_id) с прогрессом и экспортом.
import { api } from "../api.js";
import { state, allVersions } from "../state.js";
import { h, add, clear, toast, fmtScore, fmtMoney, scoreClass } from "../util.js";

export function mount(root, ctx) {
  let timer = null;
  let job = null;
  const page = h("section", { class: "page" });
  const progress = h("div", { class: "card" });
  root.append(page);

  function render() {
    const ver = allVersions().find((v) => v.id === state.versionId);
    const onlyMissing = h("input", { type: "checkbox" });
    const loadBtn = h("button", { class: "btn" }, "1. Загрузить набор из API");
    loadBtn.addEventListener("click", async () => {
      loadBtn.disabled = true;
      try {
        const r = await api.conversations(state.groupId, true);
        toast(`Набор ${state.groupId}: ${r.conversations.length} диалогов`);
      } catch (e) { toast(e.message, "error"); }
      loadBtn.disabled = false;
    });
    const startBtn = h("button", { class: "btn primary", disabled: !state.versionId }, "2. Оценить все диалоги");
    startBtn.addEventListener("click", async () => {
      try {
        job = await api.startBatch({ group_id: state.groupId, checklist_version_id: state.versionId, only_missing: onlyMissing.checked });
        poll();
      } catch (e) { toast(e.message, "error"); }
    });
    add(clear(page),
      h("div", { class: "page-head" }, h("h1", {}, "Пакетная оценка")),
      h("div", { class: "card" },
        h("p", {}, "Набор: ", h("b", { class: "mono" }, state.groupId), " (меняется в шапке) · чек-лист: ",
          h("b", {}, ver ? `${ver.name} v${ver.version}` : "не выбран")),
        h("div", { class: "row" }, loadBtn, startBtn,
          h("label", { class: "check" }, onlyMissing, " только неоценённые этой версией")),
        h("div", { class: "row" },
          h("span", { class: "muted" }, "3. Экспорт результатов: "),
          h("a", { class: "btn small ghost", href: api.exportUrl(state.groupId, state.versionId, "csv") }, "CSV"),
          h("a", { class: "btn small ghost", href: api.exportUrl(state.groupId, state.versionId, "json") }, "JSON (с обоснованиями)"))),
      progress);
    renderProgress();
  }

  function renderProgress() {
    clear(progress);
    if (!job) { progress.append(h("div", { class: "muted" }, "Задача не запущена.")); return; }
    const pct = Math.round(((job.done + job.failed) / job.total) * 100);
    const results = Object.entries(job.results).sort(([a], [b]) => a.localeCompare(b));
    add(progress,
      h("div", { class: "row between" },
        h("b", {}, `${job.status === "running" ? "Выполняется" : job.status === "cancelled" ? "Остановлено" : "Готово"}: ${job.done + job.failed} / ${job.total}`),
        h("span", {}, `ошибок: ${job.failed} · стоимость: ${fmtMoney(job.cost_usd)}`),
        job.status === "running" ? h("button", { class: "btn small danger", onclick: async () => { job = await api.cancelBatch(job.id); } }, "Остановить") : null),
      h("div", { class: "bar big" }, h("div", { class: "bar-fill good", style: { width: `${pct}%` } })),
      job.in_progress.length ? h("div", { class: "small muted" }, `В работе: ${job.in_progress.join(", ")}`) : null,
      Object.keys(job.errors).length
        ? h("div", { class: "errors" }, h("b", {}, "Ошибки:"), Object.entries(job.errors).map(([cid, e]) => h("div", { class: "small" }, h("span", { class: "mono" }, cid), `: ${e}`)))
        : null,
      results.length
        ? h("table", { class: "tbl clickable" },
            h("thead", {}, h("tr", {}, h("th", {}, "Диалог"), h("th", {}, "Итог"), h("th", {}, "Грубая ошибка"))),
            h("tbody", {}, results.map(([cid, r]) => h("tr", { onclick: () => ctx.navigate(`#/dialog/${encodeURIComponent(job.group_id)}/${encodeURIComponent(cid)}?eval=${r.evaluation_id}`) },
              h("td", { class: "mono" }, cid),
              h("td", {}, h("span", { class: `score ${scoreClass(r.total)}` }, fmtScore(r.total))),
              h("td", {}, r.critical_flag ? h("span", { class: "chip bad" }, "⚠ да") : "—")))))
        : null);
  }

  async function poll() {
    clearTimeout(timer);
    try { job = await api.batch(job.id); } catch (e) { toast(e.message, "error"); return; }
    renderProgress();
    if (job.status === "running") timer = setTimeout(poll, 1000);
    else toast(`Пакетная оценка завершена: ${job.done} успешно, ${job.failed} с ошибкой`);
  }

  render();
  // если задача уже идёт (вернулись на страницу) — подхватываем
  fetch(api.url("/api/batch")).then((r) => r.json()).then((jobs) => {
    const running = jobs.find((j) => j.status === "running") || jobs[0];
    if (running && !job) { job = running; renderProgress(); if (job.status === "running") poll(); }
  }).catch(() => {});

  return { update: () => render(), destroy: () => clearTimeout(timer) };
}
