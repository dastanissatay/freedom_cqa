// «Качество»: согласие оценок LLM с эталонной (ручной) разметкой.
import { api } from "../api.js";
import { state, allVersions } from "../state.js";
import { h, add, clear, toast, spinner, answerLabel, fmtScore } from "../util.js";

const pctClass = (x) => (x === null || x === undefined ? "na" : x >= 85 ? "good" : x >= 70 ? "mid" : "bad");
const fmtPct = (x) => (x === null || x === undefined ? "—" : `${fmtScore(x)}%`);

export function mount(root, ctx) {
  const page = h("section", { class: "page" });
  root.append(page);
  let timer = null;

  async function load() {
    const ver = allVersions().find((v) => v.id === state.versionId);
    add(clear(page),
      h("div", { class: "page-head" },
        h("div", {},
          h("h1", {}, "Качество оценки LLM"),
          h("div", { class: "muted small" }, `Набор ${state.groupId} · чек-лист ${ver ? `${ver.name} v${ver.version}` : "не выбран"} · сравнивается исходный ответ LLM (до апелляций) из последней оценки этой версией`)),
        h("div", { class: "actions" },
          h("button", { class: "btn ghost", onclick: importFile }, "⇪ Импорт эталона (JSON)"),
          h("button", { class: "btn", onclick: load }, "⟳ Обновить"))),
      spinner());
    if (!state.versionId) return;
    let q, cfg, convs;
    try {
      [q, cfg, convs] = await Promise.all([
        api.quality(state.groupId, state.versionId),
        api.version(state.versionId).then((v) => v.config),
        api.conversations(state.groupId).then((r) => r.conversations).catch(() => []),
      ]);
    } catch (e) {
      page.lastChild.replaceWith(h("div", { class: "empty" }, e.message));
      return;
    }
    page.lastChild.remove();
    const crit = Object.fromEntries(cfg.criteria.map((c) => [c.id, c]));
    const unlabeled = convs.filter((c) => !c.reference_versions.includes(state.versionId));
    const S = q.summary;
    const tile = (label, value, cls = "") => h("div", { class: `stat ${cls}` }, h("div", { class: "stat-v" }, value), h("div", { class: "stat-l" }, label));
    const fl = S.critical_flag || {};

    add(page,
      h("div", { class: "card howto" },
        h("b", {}, "Как пользоваться. "),
        "Откройте диалог → «✎ Эталон» → отметьте правильный ответ по каждому критерию (как оценил бы эксперт заказчика) → сохраните. ",
        "Разметьте 10–15 разных диалогов (RU/KZ/EN, звонки, с нарушениями и без), оцените их LLM — здесь появится процент совпадения, каппа Коэна и список расхождений. ",
        "Расхождения подсказывают, какую формулировку критерия уточнить в редакторе чек-листа. Готовый эталон можно загрузить из JSON: ",
        h("code", {}, '{"items":[{"conversation_id":"p1-ru-001","answers":{"01":"yes","04":15,…}}]}')),
      h("div", { class: "stats" },
        tile("Размечено диалогов", q.references),
        tile("Сравнено с LLM", q.compared),
        tile("Совпадение по критериям", fmtPct(S.criteria_agreement_pct), pctClass(S.criteria_agreement_pct)),
        tile("Ср. отклонение итога (MAE)", S.total_mae === null ? "—" : `${fmtScore(S.total_mae)} б.`),
        tile("Итог в пределах ±5 / ±10", `${fmtPct(S.total_within_5_pct)} / ${fmtPct(S.total_within_10_pct)}`),
        tile("Грубые ошибки: найдено / ложных / пропущено", `${fl.tp || 0} / ${fl.fp || 0} / ${fl.fn || 0}`, (fl.fn || fl.fp) ? "bad" : "")),
    );

    if (q.missing_evaluation.length) {
      const btn = h("button", { class: "btn primary small" }, `Оценить их LLM (${q.missing_evaluation.length})`);
      btn.addEventListener("click", () => evaluateMissing(btn, q.missing_evaluation));
      page.append(h("div", { class: "card notice" },
        `Размечены, но ещё не оценены этой версией чек-листа: ${q.missing_evaluation.join(", ")} `, btn));
    }

    if (q.compared) {
      page.append(h("div", { class: "card" },
        h("h2", {}, "По критериям"),
        h("table", { class: "tbl" },
          h("thead", {}, h("tr", {}, ["", "Критерий", "Пар", "Совпадение", "Каппа", "N/A-расхожд.", "Ошибка шкалы", "Типичные расхождения (LLM → эталон)"].map((t) => h("th", {}, t)))),
          h("tbody", {}, q.criteria.map((c) => h("tr", {},
            h("td", { class: "mono" }, c.criterion_id),
            h("td", {}, c.title),
            h("td", { class: "num" }, c.n),
            h("td", { class: "agree-cell" },
              h("div", { class: "bar" }, h("div", { class: `bar-fill ${pctClass(c.agreement_pct)}`, style: { width: `${c.agreement_pct || 0}%` } })),
              h("span", { class: `score ${pctClass(c.agreement_pct)}` }, fmtPct(c.agreement_pct)), h("span", { class: "muted small" }, ` ${c.agree}/${c.n}`)),
            h("td", { class: "num", title: "Каппа Коэна: 1 — полное согласие, 0 — на уровне случайности" }, c.kappa ?? "—"),
            h("td", { class: "num" }, c.na_mismatch || "—"),
            h("td", { class: "num", title: "Средняя разница ступеней, % от максимума шкалы" }, c.scale_mae_pct === null ? "—" : `${c.scale_mae_pct}%`),
            h("td", { class: "small" }, Object.entries(c.confusion).map(([k, n]) => {
              const [a, b] = k.split("→");
              const parse = (x) => (/^-?\d+$/.test(x) ? Number(x) : x);
              return h("span", { class: "chip warn" }, `${answerLabel(parse(a), crit[c.criterion_id])} → ${answerLabel(parse(b), crit[c.criterion_id])} ×${n}`);
            }))))))));

      page.append(h("div", { class: "card" },
        h("h2", {}, "По диалогам"),
        h("table", { class: "tbl clickable" },
          h("thead", {}, h("tr", {}, ["Диалог", "Эталон", "LLM", "Разница", "Совпадений критериев"].map((t) => h("th", {}, t)))),
          h("tbody", {}, q.dialogs.filter((d) => d.evaluation_id).map((d) => h("tr", {
            onclick: () => ctx.navigate(`#/dialog/${encodeURIComponent(state.groupId)}/${encodeURIComponent(d.conversation_id)}?eval=${d.evaluation_id}`),
          },
            h("td", { class: "mono" }, d.conversation_id),
            h("td", { class: "num" }, fmtScore(d.reference_total), d.reference_flag ? h("span", { class: "chip bad" }, "⚠") : null),
            h("td", { class: "num" }, fmtScore(d.llm_total), d.llm_flag ? h("span", { class: "chip bad" }, "⚠") : null),
            h("td", { class: `num ${d.diff !== null && Math.abs(d.diff) > 10 ? "score bad" : ""}` }, d.diff === null ? "—" : `${d.diff > 0 ? "+" : ""}${fmtScore(d.diff)}`),
            h("td", { class: "num" }, `${d.agree} / ${d.criteria}`)))))));

      page.append(h("div", { class: "card" },
        h("h2", {}, `Расхождения (${q.disagreements.length})`),
        q.disagreements.length
          ? h("div", { class: "dis-list" }, q.disagreements.map((d) => h("a", {
              class: "dis", href: `#/dialog/${encodeURIComponent(state.groupId)}/${encodeURIComponent(d.conversation_id)}?eval=${d.evaluation_id}`,
            },
              h("div", { class: "row between" },
                h("b", {}, h("span", { class: "mono" }, d.conversation_id), ` · ${d.criterion_id} ${d.criterion_title}`),
                h("span", {}, h("span", { class: "chip bad" }, `LLM: ${answerLabel(d.llm, crit[d.criterion_id])}`),
                  h("span", { class: "chip good" }, `эталон: ${answerLabel(d.reference, crit[d.criterion_id])}`))),
              h("div", { class: "small" }, h("span", { class: "muted" }, "Обоснование LLM: "), d.llm_explanation),
              d.note ? h("div", { class: "small" }, h("span", { class: "muted" }, "Комментарий разметчика: "), d.note) : null)))
          : h("div", { class: "muted" }, "Расхождений нет")));
    }

    if (unlabeled.length) {
      page.append(h("div", { class: "card" },
        h("h2", {}, `Ещё не размечены (${unlabeled.length})`),
        h("div", { class: "chips-wrap" }, unlabeled.map((c) => h("a", {
          class: `chip ${c.channel === "voice call" ? "call" : "chat"}`,
          href: `#/dialog/${encodeURIComponent(state.groupId)}/${encodeURIComponent(c.conversation_id)}?mode=ref`,
          title: c.preview,
        }, `${c.conversation_id} · ${c.language}`)))));
    }
  }

  function importFile() {
    const inp = h("input", { type: "file", accept: ".json,application/json" });
    inp.addEventListener("change", async () => {
      const file = inp.files[0];
      if (!file) return;
      try {
        const data = JSON.parse(await file.text());
        const items = Array.isArray(data) ? data : data.items;
        if (!Array.isArray(items)) throw new Error("ожидается массив или {items: [...]}");
        const r = await api.importReferences({ group_id: state.groupId, checklist_version_id: state.versionId, items });
        const errs = Object.entries(r.errors);
        toast(`Импортировано эталонов: ${r.saved}${errs.length ? `, ошибок: ${errs.length} (${errs.slice(0, 3).map(([k, v]) => `${k}: ${v}`).join("; ")})` : ""}`, errs.length ? "error" : "info");
        load();
      } catch (e) { toast(`Ошибка импорта: ${e.message}`, "error"); }
    });
    inp.click();
  }

  async function evaluateMissing(btn, ids) {
    btn.disabled = true;
    try {
      let job = await api.startBatch({ group_id: state.groupId, checklist_version_id: state.versionId, conversation_ids: ids });
      const tick = async () => {
        job = await api.batch(job.id);
        btn.textContent = `Оцениваю… ${job.done + job.failed}/${job.total}`;
        if (job.status === "running") timer = setTimeout(tick, 1000);
        else { toast(`Готово: ${job.done} оценено, ${job.failed} с ошибкой`); load(); }
      };
      tick();
    } catch (e) { toast(e.message, "error"); btn.disabled = false; }
  }

  load();
  return { update: (patch) => { if ("groupId" in patch || "versionId" in patch) load(); }, destroy: () => clearTimeout(timer) };
}
