// Эталонная (ручная) разметка диалога: человек ставит ответ по каждому критерию.
// Используется для измерения качества LLM на странице «Качество».
import { api } from "../api.js";
import { h, add, clear, toast, answerLabel, allowedAnswers, fmtScore, scoreClass } from "../util.js";

export function mount(root, { groupId, conversationId, version, reference, llmVerdicts, onRef, onSaved, onClose }) {
  const cfg = version.config;
  const answers = { ...(reference?.answers || {}) };
  const notes = { ...(reference?.notes || {}) };
  const llm = Object.fromEntries((llmVerdicts || []).map((v) => [v.criterion_id, v]));
  const box = h("div", { class: "ref-editor" });
  root.append(box);

  function optionButtons(c) {
    const wrap = h("div", { class: "opt-group" });
    for (const a of allowedAnswers(c)) {
      const lvl = c.type === "scale" ? c.scale.find((l) => l.value === a) : null;
      const b = h("button", {
        class: `opt ${answers[c.id] === a ? "on" : ""}`,
        title: lvl?.description || (a === "na" ? c.na_condition : ""),
        onclick: () => { answers[c.id] = a; render(); },
      }, answerLabel(a, c));
      wrap.append(b);
    }
    return wrap;
  }

  function render() {
    clear(box);
    const done = cfg.criteria.filter((c) => c.id in answers).length;
    const head = h("div", { class: "ref-head" },
      h("div", {},
        h("h2", {}, "✎ Эталонная разметка"),
        h("div", { class: "small muted" }, `${version.name} · v${version.version} · размечено ${done} из ${cfg.criteria.length}`),
        reference ? h("div", { class: "small" }, "Сохранённый эталон: ",
          h("b", { class: `score ${scoreClass(reference.total)}` }, fmtScore(reference.total)),
          reference.critical_flag ? h("span", { class: "chip bad" }, "грубая ошибка") : null) : null),
      h("div", { class: "row" },
        llmVerdicts?.length ? h("button", { class: "btn small ghost", title: "Подставить ответы LLM как черновик (меняет только неразмеченные)", onclick: () => {
          for (const v of llmVerdicts) if (!(v.criterion_id in answers)) answers[v.criterion_id] = v.answer;
          render();
        } }, "Заполнить ответами LLM") : null,
        h("button", { class: "btn small ghost", onclick: onClose }, "Закрыть")));

    add(box, head,
      h("p", { class: "small muted" }, "Отметьте правильный ответ по каждому критерию, не глядя на оценку LLM. Ответ LLM по критерию появляется только после вашей отметки — для сверки."));

    for (const s of cfg.sections) {
      const crits = cfg.criteria.filter((c) => c.section_id === s.id);
      if (!crits.length) continue;
      box.append(h("div", { class: "section-title ref-sec" }, s.title));
      for (const c of crits) {
        const lv = llm[c.id];
        const note = h("input", { class: "inp small-inp", placeholder: "Комментарий (необязательно)", value: notes[c.id] || "" });
        note.addEventListener("input", () => { notes[c.id] = note.value; });
        const mismatch = lv && c.id in answers && JSON.stringify(lv.answer) !== JSON.stringify(answers[c.id]);
        box.append(h("div", { class: `ref-crit ${c.id in answers ? "done" : ""}` },
          h("div", { class: "crit-head" },
            h("span", { class: "crit-id" }, c.id),
            h("span", { class: "crit-title" }, c.title),
            c.type !== "flag" ? h("span", { class: "pts" }, `вес ${c.weight}`) : h("span", { class: "pts" }, "без баллов")),
          h("div", { class: "small muted" }, c.description),
          c.na_allowed ? h("div", { class: "small muted" }, `N/A: ${c.na_condition}`) : null,
          optionButtons(c),
          note,
          lv && c.id in answers ? h("div", { class: `small llm-hint ${mismatch ? "mismatch" : ""}` },
            `LLM: ${answerLabel(lv.answer, c)}${mismatch ? " — не совпадает с эталоном" : ""}`,
            (lv.evidence || []).length ? h("button", { class: "ref", onclick: () => onRef(lv.evidence, lv.checked_range) }, "↗ реплики") : null) : null));
      }
    }

    const missing = cfg.criteria.filter((c) => !(c.id in answers));
    add(box, h("div", { class: "save-bar card" },
      missing.length ? h("span", { class: "muted small" }, `Не размечены: ${missing.map((c) => c.id).join(", ")}`) : h("span", { class: "chip good" }, "все критерии размечены"),
      h("button", { class: "btn primary", disabled: missing.length > 0, onclick: save }, "Сохранить эталон"),
      reference ? h("button", { class: "btn small danger", onclick: remove }, "Удалить эталон") : null));
  }

  async function save() {
    try {
      const saved = await api.saveReference(groupId, conversationId, { checklist_version_id: version.id, answers, notes });
      toast(`Эталон сохранён: итог ${fmtScore(saved.total)}`);
      onSaved?.(saved);
    } catch (e) { toast(e.message, "error"); }
  }

  async function remove() {
    if (!confirm("Удалить эталонную разметку этого диалога?")) return;
    try {
      await api.deleteReference(groupId, conversationId, version.id);
      toast("Эталон удалён");
      onSaved?.(null);
    } catch (e) { toast(e.message, "error"); }
  }

  render();
  return { destroy: () => clear(root) };
}
