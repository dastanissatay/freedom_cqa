// Форма решения проверяющего по апелляции: подтвердить или изменить оценку + причина.
// Используется в карточке диалога и на экране «Апелляции».
import { api } from "../api.js";
import { h, toast, answerLabel, allowedAnswers, fmtScore } from "../util.js";

export function decisionForm(appeal, crit, { big = false, onDecided } = {}) {
  const current = appeal.original_answer;
  let decision = "confirm";
  const sel = h("select", { class: "inp", disabled: true },
    allowedAnswers(crit).filter((x) => x !== current).map((x) => h("option", { value: JSON.stringify(x) }, answerLabel(x, crit))));
  const reason = h("textarea", { class: "inp", rows: big ? 3 : 2, placeholder: "Причина решения (обязательно)" });
  const radio = (val, text) => {
    const r = h("input", { type: "radio", name: `dec-${appeal.id}`, value: val, checked: val === "confirm" });
    r.addEventListener("change", () => { decision = val; sel.disabled = val !== "change"; });
    return h("label", { class: "radio" }, r, text);
  };
  const save = h("button", { class: big ? "btn primary" : "btn small" }, big ? "Сохранить решение и пересчитать итог" : "Сохранить решение");
  save.addEventListener("click", async () => {
    if (!reason.value.trim()) return toast("Укажите причину решения", "error");
    save.disabled = true;
    try {
      const res = await api.decide(appeal.id, decision, decision === "change" ? JSON.parse(sel.value) : null, reason.value.trim());
      toast(`Итог пересчитан: ${fmtScore(res.original.total)} → ${fmtScore(res.final.total)}`);
      onDecided?.(res);
    } catch (e) {
      toast(e.message, "error");
      save.disabled = false;
    }
  });
  return h("div", { class: `decision-form ${big ? "big" : ""}` },
    big ? h("h3", {}, "Решение проверяющего") : h("div", { class: "small" }, h("b", {}, "Решение проверяющего")),
    h("div", { class: "row" }, radio("confirm", `Подтвердить оценку (${answerLabel(current, crit)})`)),
    h("div", { class: "row" }, radio("change", "Изменить оценку на:"), sel),
    reason,
    save);
}
