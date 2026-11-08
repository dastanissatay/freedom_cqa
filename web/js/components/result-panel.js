// Результат оценки: итог, секции, критерии с обоснованием и ссылками, апелляции и решения.
import { api } from "../api.js";
import { decisionForm } from "./decision-form.js";
import { h, add, clear, toast, answerLabel, answerClass, fmtScore, scoreClass, fmtMoney, fmtDate, SPEAKER, fmtTimestamp } from "../util.js";

export function mount(root, { evaluation, conversation, role, onRef, onChanged, reference }) {
  // эталон показываем, только если он размечен по той же версии чек-листа
  const ref = reference && reference.checklist_version_id === evaluation.checklist_version_id ? reference : null;
  const ev = evaluation;
  const cfg = ev.checklist.config;
  const verdicts = Object.fromEntries(ev.verdicts.map((v) => [v.criterion_id, v]));
  const finalRows = Object.fromEntries(ev.final.criteria.map((c) => [c.criterion_id, c]));
  const origRows = Object.fromEntries(ev.original.criteria.map((c) => [c.criterion_id, c]));
  const box = h("div", { class: "result" });
  const stab = ev.usage?.stability || null;
  const stabRows = Object.fromEntries((stab?.criteria || []).map((c) => [c.criterion_id, c]));

  // проверка стабильности: повторный прогон LLM и сравнение ответов (аналог калибровочной сессии)
  function stabilityBlock() {
    const btn = h("button", { class: "btn small ghost", title: "Повторить оценку LLM и сравнить ответы по критериям (платный вызов модели)" },
      stab ? "↻ Перепроверить стабильность" : "⚖ Проверить стабильность");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Повторный прогон LLM…";
      try {
        const r = await api.stability(ev.id, 1);
        toast(`Стабильность: ${r.stable} из ${r.total} критериев совпали`);
        onChanged?.();
      } catch (e) { toast(e.message, "error"); btn.disabled = false; }
    });
    return h("div", { class: "stability" },
      stab ? h("span", { class: `chip ${stab.stable === stab.total ? "good" : "warn"}` },
        `Стабильность: ${stab.stable}/${stab.total} (${stab.runs} повт.)`) : null,
      btn);
  }
  root.append(box);

  const msgLabel = (i) => {
    const m = conversation.transcript[i];
    return m ? `[${i}] ${SPEAKER[m.speaker] || m.speaker} ${fmtTimestamp(m, conversation.channel)}` : `[${i}]`;
  };

  function refChips(v) {
    const chips = [];
    (v.evidence || []).forEach((i) =>
      chips.push(h("button", { class: "ref", title: conversation.transcript[i]?.text || "", onclick: () => onRef([i], null) }, "↗ ", msgLabel(i))));
    if (v.checked_range) {
      const [a, b] = v.checked_range;
      chips.push(h("button", { class: "ref range", title: "Участок, где ожидалось действие", onclick: () => onRef([], v.checked_range) },
        "⌕ Проверенный участок: ", a === b ? `[${a}]` : `[${a}]–[${b}]`));
    }
    if (chips.length > 1) chips.unshift(h("button", { class: "ref all", onclick: () => onRef(v.evidence || [], v.checked_range) }, "показать всё"));
    return chips.length ? h("div", { class: "refs" }, chips) : null;
  }

  function appealBlock(crit) {
    const list = ev.appeals.filter((a) => a.criterion_id === crit.id);
    const open = list.find((a) => a.status === "open");
    const el = h("div", { class: "appeals" });
    list.forEach((a) => el.append(appealView(a, crit)));
    if (role === "operator" && !open) {
      const btn = h("button", { class: "btn small ghost" }, "⚑ Обжаловать");
      btn.addEventListener("click", () => { btn.replaceWith(appealForm(crit)); });
      el.append(btn);
    }
    if (role === "reviewer" && open) el.append(decisionForm(open, crit, { onDecided: () => onChanged?.() }));
    return el;
  }

  function appealView(a, crit) {
    return h("div", { class: `appeal ${a.status}` },
      h("div", { class: "appeal-head" },
        h("b", {}, a.status === "open" ? "Апелляция на рассмотрении" : a.decision === "change" ? "Оценка изменена проверяющим" : "Оценка подтверждена проверяющим"),
        h("span", { class: "muted small" }, fmtDate(a.created_at))),
      h("div", {}, h("span", { class: "muted" }, "Комментарий: "), a.comment),
      h("div", {}, h("span", { class: "muted" }, "Исходное значение: "), answerLabel(a.original_answer, crit)),
      a.status === "resolved"
        ? h("div", {}, h("span", { class: "muted" }, "Решение: "),
            a.decision === "change" ? `${answerLabel(a.original_answer, crit)} → ${answerLabel(a.new_answer, crit)}` : "подтвердить",
            h("br"), h("span", { class: "muted" }, "Причина: "), a.reviewer_reason,
            h("span", { class: "muted small" }, `  · ${fmtDate(a.resolved_at)}`))
        : null);
  }

  function appealForm(crit) {
    const ta = h("textarea", { class: "inp", rows: 3, placeholder: "Почему вы не согласны с оценкой?" });
    const form = h("div", { class: "appeal-form" },
      h("div", { class: "small muted" }, `Апелляция по критерию ${crit.id} «${crit.title}»`),
      ta,
      h("div", { class: "row" },
        h("button", { class: "btn small", onclick: async () => {
          if (!ta.value.trim()) return toast("Добавьте комментарий", "error");
          try {
            await api.createAppeal(ev.id, crit.id, ta.value.trim());
            toast("Апелляция сохранена");
            onChanged?.();
          } catch (e) { toast(e.message, "error"); }
        } }, "Сохранить апелляцию"),
        h("button", { class: "btn small ghost", onclick: () => onChanged?.({ soft: true }) }, "Отмена")));
    setTimeout(() => ta.focus(), 0);
    return form;
  }

  function criterionCard(crit) {
    const v = verdicts[crit.id] || {};
    const fin = finalRows[crit.id];
    const orig = origRows[crit.id];
    const changed = JSON.stringify(fin.answer) !== JSON.stringify(orig.answer);
    const isNa = fin.answer === "na";
    const missed = !isNa && crit.type !== "flag" && v.checked_range;
    return h("div", { class: `crit ${answerClass(fin.answer, crit)} ${crit.type === "flag" ? "flag" : ""}`, id: `crit-${crit.id}` },
      h("div", { class: "crit-head" },
        h("span", { class: "crit-id" }, crit.id),
        h("span", { class: "crit-title" }, crit.title),
        h("span", { class: `badge ${answerClass(fin.answer, crit)}` }, answerLabel(fin.answer, crit)),
        h("span", { class: "pts" },
          crit.type === "flag" ? "без баллов"
            : isNa ? `N/A · вес ${crit.weight} перераспределён`
            : `${fmtScore(fin.points)} / ${crit.weight}`)),
      changed ? h("div", { class: "override" }, `Оценка LLM: ${answerLabel(orig.answer, crit)} → после апелляции: ${answerLabel(fin.answer, crit)}`) : null,
      stab && stabRows[crit.id] && !stabRows[crit.id].stable
        ? h("div", { class: "ref-mark miss", title: "Повторные прогоны LLM дали разные ответы — критерий стоит проверить вручную" },
            `⚠ нестабильно: ${stabRows[crit.id].answers.map((a) => answerLabel(a, crit)).join(" / ")}`)
        : null,
      ref && crit.id in ref.answers
        ? h("div", { class: `ref-mark ${JSON.stringify(ref.answers[crit.id]) === JSON.stringify(orig.answer) ? "match" : "miss"}` },
            JSON.stringify(ref.answers[crit.id]) === JSON.stringify(orig.answer)
              ? `✓ совпадает с эталоном (${answerLabel(ref.answers[crit.id], crit)})`
              : `✗ эталон: ${answerLabel(ref.answers[crit.id], crit)} · LLM: ${answerLabel(orig.answer, crit)}`,
            ref.notes?.[crit.id] ? h("span", { class: "muted" }, ` — ${ref.notes[crit.id]}`) : null)
        : null,
      h("div", { class: "expl" },
        isNa ? h("b", {}, "Причина N/A: ") : missed && (fin.answer === "no" || typeof fin.answer === "number") ? h("b", {}, "Пропущено: ") : null,
        v.explanation || "—"),
      isNa && crit.na_condition ? h("div", { class: "small muted" }, `Условие неприменимости: ${crit.na_condition}`) : null,
      refChips(v),
      appealBlock(crit));
  }

  // ---- итог ----
  const F = ev.final, O = ev.original;
  const flags = cfg.criteria.filter((c) => F.critical_criteria.includes(c.id));
  add(box,
    h("div", { class: "summary" },
      h("div", { class: `total ${scoreClass(F.total)}` },
        h("div", { class: "total-v" }, F.total === null ? "—" : fmtScore(F.total)),
        h("div", { class: "total-l" }, F.total === null ? "итог не рассчитан (нет применимых критериев)" : "итоговый балл из 100")),
      h("div", { class: "summary-meta" },
        F.total !== O.total ? h("div", { class: "override" }, `Исходная оценка: ${fmtScore(O.total)} → финальная после апелляций: ${fmtScore(F.total)}`) : null,
        ref ? h("div", { class: "ref-summary" }, "Эталон: ", h("b", {}, fmtScore(ref.total)),
          ` · совпадений LLM с эталоном: ${cfg.criteria.filter((c) => JSON.stringify(ref.answers[c.id]) === JSON.stringify(origRows[c.id]?.answer)).length} из ${cfg.criteria.length}`,
          ` · разница итога: ${O.total !== null && ref.total !== null ? (O.total - ref.total > 0 ? "+" : "") + fmtScore(O.total - ref.total) : "—"}`) : null,
        h("div", {}, `Набрано ${fmtScore(F.earned)} из ${fmtScore(F.applicable_weight)} применимого веса`),
        h("div", { class: "muted small" }, `${ev.checklist.name} · v${ev.checklist.version} · ${ev.provider}/${ev.model} · ${fmtMoney(ev.cost_usd)} · попыток: ${ev.attempts} · ${fmtDate(ev.created_at)}`),
        ev.usage?.warnings?.length ? h("div", { class: "muted small" }, `Предупреждения валидации: ${ev.usage.warnings.join("; ")}`) : null,
        stabilityBlock())),
    F.critical_flag
      ? h("div", { class: "critical" }, h("b", {}, "⚠ Грубая ошибка"),
          flags.map((c) => h("div", {}, `${c.id} ${c.title}: ${verdicts[c.id]?.explanation || ""}`, refChips(verdicts[c.id] || {}))))
      : null,
  );

  for (const s of cfg.sections) {
    const crits = cfg.criteria.filter((c) => c.section_id === s.id);
    if (!crits.length) continue;
    const sr = F.sections.find((x) => x.section_id === s.id);
    const hasScore = sr && sr.score !== null;
    box.append(h("div", { class: "section" },
      h("div", { class: "section-head" },
        h("span", { class: "section-title" }, s.title),
        hasScore
          ? h("span", { class: `section-score ${scoreClass(sr.score)}` }, `${fmtScore(sr.score)}%`, h("small", {}, ` (${fmtScore(sr.earned)}/${fmtScore(sr.applicable_weight)})`))
          : h("span", { class: "muted small" }, sr?.flags?.length ? "выявлено нарушение" : sr?.max_weight ? "все критерии N/A" : "без баллов")),
      hasScore ? h("div", { class: "bar" }, h("div", { class: `bar-fill ${scoreClass(sr.score)}`, style: { width: `${sr.score}%` } })) : null,
      crits.map(criterionCard)));
  }

  return { destroy: () => clear(root) };
}
