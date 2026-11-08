// Редактор чек-листа: секции, критерии (формулировка, вес, тип, N/A, шкала). Сохранение = новая версия.
import { api } from "../api.js";
import { state, setState, reloadChecklists } from "../state.js";
import { h, add, clear, toast, spinner, fmtDate } from "../util.js";

const TYPES = [["binary", "Да / Нет"], ["scale", "Числовая шкала"], ["flag", "Флаг грубой ошибки (без баллов)"]];

export function mount(root, ctx) {
  let versionId = Number(ctx.route.parts[1]) || state.versionId;
  let loaded = null; // версия с сервера
  let draft = null;  // редактируемая копия config
  let rawMode = false;
  const page = h("section", { class: "page" });
  const side = h("div", { class: "col-side card" });
  const editor = h("div", { class: "col-main" });
  page.append(h("div", { class: "page-head" }, h("h1", {}, "Чек-листы")), h("div", { class: "split side" }, side, editor));
  root.append(page);

  function renderSide() {
    clear(side).append(h("h3", {}, "Чек-листы и версии"));
    for (const c of state.checklists) {
      side.append(h("div", { class: "cl-group" },
        h("div", { class: "cl-name" }, c.name),
        c.versions.map((v) => h("a", {
          class: `cl-ver ${v.id === versionId ? "sel" : ""}`, href: `#/checklists/${v.id}`,
        }, `v${v.version}`, v.id === state.versionId ? h("span", { class: "chip good" }, "активный") : null,
           h("div", { class: "small muted" }, `${v.comment || ""} · ${fmtDate(v.created_at)}`)))));
    }
  }

  async function load() {
    clear(editor).append(spinner());
    renderSide();
    if (!versionId) { clear(editor).append(h("div", { class: "empty" }, "Нет чек-листов")); return; }
    loaded = await api.version(versionId);
    draft = structuredClone(loaded.config);
    render();
  }

  const weightSum = () => draft.criteria.filter((c) => c.type !== "flag").reduce((s, c) => s + (Number(c.weight) || 0), 0);
  let sumEl;
  const updateSum = () => {
    if (!sumEl) return;
    const s = weightSum();
    sumEl.textContent = `Сумма весов: ${s}`;
    sumEl.className = `chip ${s === 100 ? "good" : "warn"}`;
    sumEl.title = s === 100 ? "" : "Сумма весов не равна 100 — итог всё равно нормируется к 100 по применимым критериям";
  };

  function nextCritId() {
    const nums = draft.criteria.map((c) => parseInt(c.id, 10)).filter((n) => !isNaN(n));
    let n = (nums.length ? Math.max(...nums) : 0) + 1;
    let id = String(n).padStart(2, "0");
    while (draft.criteria.some((c) => c.id === id)) id = String(++n).padStart(2, "0");
    return id;
  }

  // поле, которое пишет в объект без полной перерисовки (чтобы не терять фокус)
  function field(obj, key, { type = "text", rows, placeholder, cls = "", onInput } = {}) {
    const el = rows
      ? h("textarea", { class: `inp ${cls}`, rows, placeholder }, obj[key] ?? "")
      : h("input", { class: `inp ${cls}`, type, placeholder, value: obj[key] ?? "" });
    el.addEventListener("input", () => {
      obj[key] = type === "number" ? (el.value === "" ? 0 : Number(el.value)) : el.value;
      onInput?.();
    });
    return el;
  }

  function scaleEditor(c) {
    const box = h("div", { class: "scale-ed" }, h("div", { class: "small muted" }, "Ступени шкалы (баллы = ступень / максимум × вес):"));
    c.scale.forEach((lvl, i) => box.append(h("div", { class: "row" },
      field(lvl, "value", { type: "number", cls: "w80" }),
      field(lvl, "description", { placeholder: "Описание ступени", cls: "grow" }),
      h("button", { class: "btn small ghost", title: "Удалить ступень", onclick: () => { c.scale.splice(i, 1); render(); } }, "✕"))));
    box.append(h("button", { class: "btn small ghost", onclick: () => { c.scale.push({ value: 0, description: "" }); render(); } }, "+ ступень"));
    return box;
  }

  function criterionEditor(c) {
    const typeSel = h("select", { class: "inp" }, TYPES.map(([v, t]) => h("option", { value: v, selected: c.type === v }, t)));
    typeSel.addEventListener("change", () => {
      c.type = typeSel.value;
      if (c.type === "scale" && (!c.scale || c.scale.length < 2)) c.scale = [{ value: 10, description: "" }, { value: 0, description: "" }];
      if (c.type === "flag") { c.weight = 0; c.na_allowed = false; }
      render();
    });
    const secSel = h("select", { class: "inp" }, draft.sections.map((s) => h("option", { value: s.id, selected: s.id === c.section_id }, s.title)));
    secSel.addEventListener("change", () => { c.section_id = secSel.value; render(); });
    const na = h("input", { type: "checkbox", checked: !!c.na_allowed, disabled: c.type === "flag" });
    na.addEventListener("change", () => { c.na_allowed = na.checked; render(); });

    return h("details", { class: "crit-ed", open: c._open || false, ontoggle: (e) => (c._open = e.target.open) },
      h("summary", {},
        h("span", { class: "crit-id" }, c.id),
        h("span", { class: "crit-title" }, c.title || "(без названия)"),
        h("span", { class: "muted small" }, `${TYPES.find((t) => t[0] === c.type)[1]}${c.type !== "flag" ? ` · вес ${c.weight}` : ""}${c.na_allowed ? " · N/A" : ""}`)),
      h("div", { class: "grid2" },
        h("label", {}, "ID", field(c, "id", { onInput: () => {} })),
        h("label", {}, "Секция", secSel),
        h("label", { class: "span2" }, "Формулировка", field(c, "title")),
        h("label", { class: "span2" }, "Описание / когда «Да»", field(c, "description", { rows: 2 })),
        h("label", {}, "Тип ответа", typeSel),
        c.type !== "flag" ? h("label", {}, "Вес", field(c, "weight", { type: "number", onInput: updateSum })) : h("div", {}),
        h("label", { class: "check span2" }, na, " N/A допустим"),
        c.na_allowed ? h("label", { class: "span2" }, "Условие N/A", field(c, "na_condition", { placeholder: "Когда критерий неприменим" })) : null,
        c.type === "scale" ? h("div", { class: "span2" }, scaleEditor(c)) : null,
        h("label", { class: "span2" }, "Подсказка для LLM (опционально)", field(c, "guidance", { rows: 2 }))),
      h("div", { class: "row end" },
        h("button", { class: "btn small danger", onclick: () => {
          if (!confirm(`Удалить критерий ${c.id}?`)) return;
          draft.criteria = draft.criteria.filter((x) => x !== c); render();
        } }, "Удалить критерий")));
  }

  function render() {
    clear(editor);
    if (rawMode) return renderRaw();
    sumEl = h("span", {});
    const comment = h("input", { class: "inp grow", placeholder: "Комментарий к версии (что изменено)" });
    add(editor,
      h("div", { class: "card" },
        h("div", { class: "row between" },
          h("h2", {}, `${loaded.checklist_name} · v${loaded.version}`),
          h("div", { class: "row" }, sumEl,
            h("button", { class: "btn small ghost", onclick: () => { rawMode = true; render(); } }, "{ } JSON"),
            loaded.id !== state.versionId
              ? h("button", { class: "btn small", onclick: () => { setState({ versionId: loaded.id }); renderSide(); render(); toast("Чек-лист активирован"); } }, "Сделать активным")
              : h("span", { class: "chip good" }, "активный"))),
        h("label", {}, "Название", field(draft, "name")),
        h("details", {}, h("summary", {}, "Общие пояснения для LLM (входят в промпт)"), field(draft, "instructions", { rows: 10 }))),
    );
    for (const s of draft.sections) {
      const crits = draft.criteria.filter((c) => c.section_id === s.id);
      editor.append(h("div", { class: "card section-ed" },
        h("div", { class: "row" },
          field(s, "title", { cls: "grow sec-title" }),
          h("span", { class: "muted small" }, `id: ${s.id}`),
          h("button", { class: "btn small", onclick: () => {
            const id = nextCritId();
            draft.criteria.push({ id, section_id: s.id, title: "Новый критерий", description: "", type: "binary", weight: 10, na_allowed: false, na_condition: "", scale: [], guidance: "", _open: true });
            render();
          } }, "+ критерий"),
          h("button", { class: "btn small ghost", title: "Удалить секцию", disabled: crits.length > 0, onclick: () => { draft.sections = draft.sections.filter((x) => x !== s); render(); } }, "✕")),
        crits.map(criterionEditor)));
    }
    add(editor,
      h("button", { class: "btn ghost", onclick: () => {
        let n = draft.sections.length + 1, id = `s${n}`;
        while (draft.sections.some((x) => x.id === id)) id = `s${++n}`;
        draft.sections.push({ id, title: "Новая секция" }); render();
      } }, "+ секция"),
      h("div", { class: "card save-bar" },
        comment,
        h("button", { class: "btn primary", onclick: () => save("version", comment.value) }, "Сохранить как новую версию"),
        h("button", { class: "btn", onclick: () => save("new", comment.value) }, "Сохранить как новый чек-лист")));
    updateSum();
  }

  function renderRaw() {
    const ta = h("textarea", { class: "inp mono", rows: 30 }, JSON.stringify(clean(draft), null, 2));
    editor.append(h("div", { class: "card" },
      h("div", { class: "row between" }, h("h2", {}, "Конфигурация (JSON)"),
        h("button", { class: "btn small", onclick: () => {
          try { draft = JSON.parse(ta.value); rawMode = false; render(); } catch (e) { toast(`JSON: ${e.message}`, "error"); }
        } }, "Применить и вернуться в форму")),
      ta));
  }

  const clean = (cfg) => ({ ...cfg, criteria: cfg.criteria.map(({ _open, ...c }) => c) });

  async function save(mode, comment) {
    const cfg = clean(draft);
    try {
      if (mode === "new" && state.checklists.some((c) => c.name === cfg.name)) cfg.name = `${cfg.name} (копия)`;
      const saved = mode === "new"
        ? await api.createChecklist(cfg, comment || "Новый чек-лист")
        : await api.addVersion(loaded.checklist_id, cfg, comment || "Изменения");
      await reloadChecklists(saved.id);
      toast(`Сохранено: ${saved.checklist_name} v${saved.version} — активирован для оценки`);
      ctx.navigate(`#/checklists/${saved.id}`);
    } catch (e) { toast(`Ошибка сохранения: ${e.message}`, "error"); }
  }

  load();
  return { update(patch) { if ("versionId" in patch) renderSide(); } };
}
