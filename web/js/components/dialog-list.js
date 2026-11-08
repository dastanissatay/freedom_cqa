// Список диалогов набора: фильтры, статус оценки, переход в карточку, импорт файла.
import { api } from "../api.js";
import { state } from "../state.js";
import { h, add, clear, toast, spinner, fmtScore, scoreClass } from "../util.js";

export function mount(root, ctx) {
  const filters = { q: "", channel: "", language: "", status: "" };
  let data = [];
  const wrap = h("section", { class: "page" });
  const tableBox = h("div", { class: "card" });
  root.append(wrap);

  async function load(refresh = false) {
    clear(tableBox).append(spinner(refresh ? "Загружаю набор из API…" : "Загрузка диалогов…"));
    try {
      const res = await api.conversations(state.groupId, refresh);
      data = res.conversations;
      if (res.fetched !== null && res.fetched !== undefined) toast(`Загружено из API: ${res.fetched} диалогов`);
      renderTable();
    } catch (e) {
      clear(tableBox).append(h("div", { class: "empty" },
        h("p", {}, `Не удалось загрузить набор «${state.groupId}»: ${e.message}`),
        h("p", { class: "muted" }, "Проверьте DATASET_API_URL в .env или загрузите JSON-файл с транскриптами.")));
    }
  }

  function importFile() {
    const inp = h("input", { type: "file", accept: ".json,application/json" });
    inp.addEventListener("change", async () => {
      const file = inp.files[0];
      if (!file) return;
      try {
        const payload = JSON.parse(await file.text());
        const r = await api.importRecords(state.groupId, payload);
        toast(`Импортировано: ${r.imported}`);
        load();
      } catch (e) { toast(`Ошибка импорта: ${e.message}`, "error"); }
    });
    inp.click();
  }

  function filtered() {
    const q = filters.q.toLowerCase();
    return data.filter((c) =>
      (!q || c.conversation_id.toLowerCase().includes(q) || (c.preview || "").toLowerCase().includes(q)) &&
      (!filters.channel || c.channel === filters.channel) &&
      (!filters.language || c.language === filters.language) &&
      (!filters.status ||
        (filters.status === "none" && !c.evaluation) ||
        (filters.status === "done" && c.evaluation) ||
        (filters.status === "flag" && c.evaluation?.critical_flag) ||
        (filters.status === "appeal" && c.evaluation?.open_appeals > 0)));
  }

  function renderTable() {
    const rows = filtered();
    const evaluated = data.filter((c) => c.evaluation);
    const avg = evaluated.filter((c) => c.evaluation.total !== null);
    const mean = avg.length ? avg.reduce((s, c) => s + c.evaluation.total, 0) / avg.length : null;
    add(clear(tableBox),
      h("div", { class: "stats" },
        stat("Диалогов", data.length),
        stat("Оценено", `${evaluated.length} / ${data.length}`),
        stat("Средний балл", fmtScore(mean)),
        stat("Грубые ошибки", evaluated.filter((c) => c.evaluation.critical_flag).length),
        stat("Открытые апелляции", evaluated.reduce((s, c) => s + (c.evaluation.open_appeals || 0), 0))),
      rows.length
        ? h("table", { class: "tbl clickable" },
            h("thead", {}, h("tr", {}, ["ID", "Канал", "Язык", "Реплик", "Первая реплика клиента", "Итог", "Флаги"].map((t) => h("th", {}, t)))),
            h("tbody", {}, rows.map((c) => {
              const ev = c.evaluation;
              return h("tr", { onclick: () => ctx.navigate(`#/dialog/${encodeURIComponent(state.groupId)}/${encodeURIComponent(c.conversation_id)}`) },
                h("td", { class: "mono" }, c.conversation_id),
                h("td", {}, h("span", { class: `chip ${c.channel === "voice call" ? "call" : "chat"}` }, c.channel === "voice call" ? "☎ звонок" : "💬 чат")),
                h("td", {}, h("span", { class: "chip lang" }, c.language || "?")),
                h("td", { class: "num" }, c.messages),
                h("td", { class: "preview" }, c.preview),
                h("td", { class: "num" }, ev
                  ? h("span", { class: `score ${scoreClass(ev.total)}` }, fmtScore(ev.total),
                      ev.original_total !== ev.total ? h("small", { title: "Исходный балл до апелляций" }, ` (было ${fmtScore(ev.original_total)})`) : null)
                  : h("span", { class: "muted" }, "не оценён")),
                h("td", {},
                  c.reference_versions?.includes(state.versionId) ? h("span", { class: "chip info", title: "Есть эталонная разметка для активного чек-листа" }, "✎ эталон") : null,
                  ev?.critical_flag ? h("span", { class: "chip bad" }, "⚠ грубая ошибка") : null,
                  ev?.open_appeals ? h("span", { class: "chip warn" }, `апелляций: ${ev.open_appeals}`) : null));
            })))
        : h("div", { class: "empty" }, data.length ? "Нет диалогов под фильтр" : "Набор пуст"));
  }

  const stat = (label, value) => h("div", { class: "stat" }, h("div", { class: "stat-v" }, value), h("div", { class: "stat-l" }, label));

  const sel = (key, opts) => {
    const s = h("select", { class: "inp" }, opts.map(([v, t]) => h("option", { value: v }, t)));
    s.addEventListener("change", () => { filters[key] = s.value; renderTable(); });
    return s;
  };
  const search = h("input", { class: "inp", placeholder: "Поиск по ID или тексту…" });
  search.addEventListener("input", () => { filters.q = search.value; renderTable(); });

  add(wrap,
    h("div", { class: "page-head" },
      h("h1", {}, `Диалоги · набор ${state.groupId}`),
      h("div", { class: "actions" },
        h("button", { class: "btn", onclick: () => load(true) }, "⟳ Обновить из API"),
        h("button", { class: "btn ghost", onclick: importFile }, "⇪ Импорт JSON"))),
    h("div", { class: "filters" },
      search,
      sel("channel", [["", "Все каналы"], ["agent chat", "Чаты"], ["voice call", "Звонки"]]),
      sel("language", [["", "Все языки"], ["RU", "RU"], ["KZ", "KZ"], ["EN", "EN"]]),
      sel("status", [["", "Все статусы"], ["none", "Не оценены"], ["done", "Оценены"], ["flag", "С грубой ошибкой"], ["appeal", "С апелляцией"]])),
    tableBox);
  load();

  return {
    update(patch) { if ("groupId" in patch) { wrap.querySelector("h1").textContent = `Диалоги · набор ${state.groupId}`; load(); } },
  };
}
