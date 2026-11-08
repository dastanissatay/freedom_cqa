// Экран апелляций: оператор видит свои обращения, проверяющий — очередь и форму решения.
import { api } from "../api.js";
import { state } from "../state.js";
import { h, add, clear, toast, spinner, answerLabel, allowedAnswers, fmtScore, fmtDate, SPEAKER, fmtTimestamp, scoreClass } from "../util.js";

export function mount(root, ctx) {
  let status = state.role === "reviewer" ? "open" : "";
  let selectedId = Number(ctx.route.params.get("id")) || null;
  const page = h("section", { class: "page" });
  const listBox = h("div", { class: "col-left card" });
  const detail = h("div", { class: "col-right card" });
  root.append(page);

  function renderShell() {
    const sel = h("select", { class: "inp" },
      [["open", "На рассмотрении"], ["resolved", "Рассмотренные"], ["", "Все"]].map(([v, t]) => h("option", { value: v, selected: v === status }, t)));
    sel.addEventListener("change", () => { status = sel.value; loadList(); });
    add(clear(page),
      h("div", { class: "page-head" },
        h("h1", {}, state.role === "reviewer" ? "Очередь апелляций · Проверяющий" : "Мои апелляции · Оператор/Супервайзер"),
        h("div", { class: "actions" }, sel)),
      state.role === "operator"
        ? h("p", { class: "muted" }, "Апелляция подаётся из карточки диалога кнопкой «Обжаловать» у критерия. Решение принимает Проверяющий (переключите роль в шапке).")
        : null,
      h("div", { class: "split" }, listBox, detail));
  }

  async function loadList() {
    clear(listBox).append(spinner());
    try {
      const params = { group_id: state.groupId };
      if (status) params.status = status;
      const items = await api.appeals(params);
      clear(listBox);
      if (!items.length) { listBox.append(h("div", { class: "empty" }, "Апелляций нет")); clear(detail); return; }
      listBox.append(h("div", { class: "appeal-list" }, items.map((a) =>
        h("div", { class: `appeal-item ${a.id === selectedId ? "sel" : ""} ${a.status}`, onclick: () => { selectedId = a.id; loadList(); } },
          h("div", { class: "row between" },
            h("b", { class: "mono" }, a.conversation_id),
            h("span", { class: `chip ${a.status === "open" ? "warn" : a.decision === "change" ? "info" : "good"}` },
              a.status === "open" ? "на рассмотрении" : a.decision === "change" ? "изменено" : "подтверждено")),
          h("div", { class: "small" }, `Критерий ${a.criterion_id} · ${fmtDate(a.created_at)}`),
          h("div", { class: "small muted ellipsis" }, a.comment)))));
      const sel = items.find((a) => a.id === selectedId) || items[0];
      selectedId = sel.id;
      listBox.querySelectorAll(".appeal-item")[items.indexOf(sel)]?.classList.add("sel");
      loadDetail(sel);
    } catch (e) { clear(listBox).append(h("div", { class: "empty" }, e.message)); }
  }

  async function loadDetail(a) {
    clear(detail).append(spinner());
    const [ev, conv] = await Promise.all([api.evaluation(a.evaluation_id), api.conversation(a.group_id || state.groupId, a.conversation_id)]);
    const cfg = ev.checklist.config;
    const crit = cfg.criteria.find((c) => c.id === a.criterion_id);
    const v = ev.verdicts.find((x) => x.criterion_id === a.criterion_id) || {};
    const appeal = ev.appeals.find((x) => x.id === a.id);

    // фрагмент диалога: доказательства и проверенный участок ± 2 реплики
    const idx = new Set();
    const mark = new Set(v.evidence || []);
    (v.evidence || []).forEach((i) => { for (let k = i - 2; k <= i + 2; k++) idx.add(k); });
    if (v.checked_range) for (let k = v.checked_range[0] - 1; k <= v.checked_range[1] + 1; k++) idx.add(k);
    if (!idx.size) for (let k = 0; k < Math.min(6, conv.transcript.length); k++) idx.add(k);
    const fragment = [...idx].filter((i) => i >= 0 && i < conv.transcript.length).sort((x, y) => x - y);
    const inRange = (i) => v.checked_range && i >= v.checked_range[0] && i <= v.checked_range[1];

    const fragEl = h("div", { class: "transcript fragment" });
    let prev = null;
    for (const i of fragment) {
      if (prev !== null && i > prev + 1) fragEl.append(h("div", { class: "gap" }, "…"));
      const m = conv.transcript[i];
      fragEl.append(h("div", { class: `msg ${m.speaker} ${mark.has(i) ? "hl" : ""} ${inRange(i) ? "in-range" : ""}` },
        h("div", { class: "msg-meta" }, h("span", { class: "msg-idx" }, `[${i}]`), h("span", { class: "msg-who" }, SPEAKER[m.speaker] || m.speaker),
          h("span", { class: "msg-ts" }, fmtTimestamp(m, conv.channel))),
        h("div", { class: "msg-text" }, m.text)));
      prev = i;
    }

    const totals = h("div", { class: "totals" },
      h("div", { class: `score-box ${scoreClass(ev.original.total)}` }, h("small", {}, "Исходный итог"), h("b", {}, fmtScore(ev.original.total))),
      h("div", { class: "arrow" }, "→"),
      h("div", { class: `score-box ${scoreClass(ev.final.total)}` }, h("small", {}, "Финальный итог"), h("b", {}, fmtScore(ev.final.total))));

    add(clear(detail),
      h("div", { class: "row between" },
        h("h2", {}, `Критерий ${crit.id}. ${crit.title}`),
        h("a", { class: "btn small ghost", href: `#/dialog/${encodeURIComponent(conv.group_id)}/${encodeURIComponent(conv.conversation_id)}?eval=${ev.id}` }, "Открыть карточку ↗")),
      h("div", { class: "kv" },
        h("div", {}, h("span", { class: "muted" }, "Спорная оценка: "), h("b", {}, answerLabel(appeal.original_answer, crit)),
          crit.type !== "flag" ? h("span", { class: "muted" }, ` · вес ${crit.weight}`) : null),
        h("div", {}, h("span", { class: "muted" }, "Обоснование LLM: "), v.explanation || "—"),
        h("div", {}, h("span", { class: "muted" }, "Комментарий оператора: "), h("i", {}, appeal.comment))),
      h("h3", {}, "Фрагмент диалога"),
      fragEl,
      appeal.status === "resolved"
        ? h("div", { class: "appeal resolved" },
            h("b", {}, appeal.decision === "change" ? `Изменено: ${answerLabel(appeal.original_answer, crit)} → ${answerLabel(appeal.new_answer, crit)}` : "Оценка подтверждена"),
            h("div", {}, h("span", { class: "muted" }, "Причина: "), appeal.reviewer_reason),
            h("div", { class: "muted small" }, fmtDate(appeal.resolved_at)))
        : state.role === "reviewer" ? decisionForm(appeal, crit) : h("div", { class: "muted" }, "Ожидает решения проверяющего."),
      totals);
  }

  function decisionForm(appeal, crit) {
    const current = appeal.original_answer;
    const options = allowedAnswers(crit).filter((x) => x !== current);
    let decision = "confirm";
    const sel = h("select", { class: "inp", disabled: true }, options.map((x) => h("option", { value: JSON.stringify(x) }, answerLabel(x, crit))));
    const reason = h("textarea", { class: "inp", rows: 3, placeholder: "Причина решения (обязательно)" });
    const radio = (val, text) => {
      const r = h("input", { type: "radio", name: "decision", value: val, checked: val === "confirm" });
      r.addEventListener("change", () => { decision = val; sel.disabled = val !== "change"; });
      return h("label", { class: "radio" }, r, text);
    };
    return h("div", { class: "decision-form big" },
      h("h3", {}, "Решение проверяющего"),
      h("div", { class: "row" }, radio("confirm", `Подтвердить оценку (${answerLabel(current, crit)})`)),
      h("div", { class: "row" }, radio("change", "Изменить оценку на:"), sel),
      reason,
      h("button", { class: "btn primary", onclick: async () => {
        if (!reason.value.trim()) return toast("Укажите причину решения", "error");
        try {
          const res = await api.decide(appeal.id, decision, decision === "change" ? JSON.parse(sel.value) : null, reason.value.trim());
          toast(`Итог пересчитан: ${fmtScore(res.original.total)} → ${fmtScore(res.final.total)}`);
          status = ""; // показываем и рассмотренную апелляцию с новым итогом
          renderShell();
          loadList();
        } catch (e) { toast(e.message, "error"); }
      } }, "Сохранить решение и пересчитать итог"));
  }

  renderShell();
  loadList();
  return { update() { status = state.role === "reviewer" ? "open" : status; renderShell(); loadList(); } };
}
