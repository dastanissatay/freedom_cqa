// «Аналитика»: сводка по набору для руководителя контроля качества —
// типовые ошибки по критериям, разрезы по языку и каналу, грубые нарушения, худшие диалоги.
import { api } from "../api.js";
import { state, allVersions } from "../state.js";
import { h, add, clear, spinner, fmtScore, scoreClass } from "../util.js";

const failClass = (x) => (x === null || x === undefined ? "na" : x >= 40 ? "bad" : x >= 20 ? "mid" : "good");

export function mount(root, ctx) {
  const page = h("section", { class: "page" });
  root.append(page);

  async function load() {
    const ver = allVersions().find((v) => v.id === state.versionId);
    add(clear(page),
      h("div", { class: "page-head" },
        h("div", {},
          h("h1", {}, `Аналитика · набор ${state.groupId}`),
          h("div", { class: "muted small" }, `чек-лист ${ver ? `${ver.name} v${ver.version}` : "не выбран"} · последняя оценка каждого диалога этой версией, с учётом апелляций`)),
        h("button", { class: "btn", onclick: load }, "⟳ Обновить")),
      spinner());
    if (!state.versionId) return;
    let s;
    try { s = await api.summary(state.groupId, state.versionId); } catch (e) {
      page.lastChild.replaceWith(h("div", { class: "empty" }, e.message));
      return;
    }
    page.lastChild.remove();
    if (!s.evaluated) {
      page.append(h("div", { class: "card empty" }, "В этом наборе нет оценок выбранной версией чек-листа. Запустите «Пакетную оценку»."));
      return;
    }
    const link = (cid, eid) => `#/dialog/${encodeURIComponent(state.groupId)}/${encodeURIComponent(cid)}?eval=${eid}`;
    const tile = (label, value, cls = "") => h("div", { class: `stat ${cls}` }, h("div", { class: "stat-v" }, value), h("div", { class: "stat-l" }, label));
    const maxBucket = Math.max(1, ...s.distribution.map((b) => b.n));

    add(page,
      h("div", { class: "stats" },
        tile("Оценено", `${s.evaluated} / ${s.dialogs_total}`),
        tile("Средний балл", fmtScore(s.avg_total), scoreClass(s.avg_total)),
        tile("Медиана", fmtScore(s.median_total), scoreClass(s.median_total)),
        tile("Грубые нарушения", s.critical.length, s.critical.length ? "bad" : "good")),

      s.top_problems.length
        ? h("div", { class: "card notice" }, h("b", {}, "Главные зоны роста: "),
            s.top_problems.map((c) => h("span", { class: `chip ${failClass(c.fail_pct)}` }, `${c.criterion_id} ${c.title} — ${fmtScore(c.fail_pct)}% диалогов`)))
        : null,

      h("div", { class: "grid-2col" },
        h("div", { class: "card" },
          h("h2", {}, "Распределение итогового балла"),
          h("div", { class: "hist" }, s.distribution.map((b) => h("div", { class: "hist-row" },
            h("span", { class: "hist-l" }, b.range),
            h("div", { class: "bar big" }, h("div", { class: "bar-fill good", style: { width: `${(b.n / maxBucket) * 100}%` } })),
            h("span", { class: "num" }, b.n))))),
        h("div", { class: "card" },
          h("h2", {}, "Средний балл по разрезам"),
          h("table", { class: "tbl" },
            h("tbody", {},
              [...s.by_language.map((x) => ["Язык", x]), ...s.by_channel.map((x) => ["Канал", x])].map(([kind, x]) =>
                h("tr", {}, h("td", { class: "muted" }, kind), h("td", {}, x.key), h("td", { class: "num" }, `${x.n} диал.`),
                  h("td", { class: "num" }, h("span", { class: `score ${scoreClass(x.avg)}` }, fmtScore(x.avg))))))))),

      h("div", { class: "card" },
        h("h2", {}, "По критериям"),
        h("table", { class: "tbl" },
          h("thead", {}, h("tr", {}, ["", "Критерий", "Вес", "Применим", "N/A", "Нарушено", "Доля нарушений", "Средний % баллов"].map((t) => h("th", {}, t)))),
          h("tbody", {}, s.criteria.map((c) => h("tr", {},
            h("td", { class: "mono" }, c.criterion_id),
            h("td", {}, c.title),
            h("td", { class: "num" }, c.type === "flag" ? "—" : c.weight),
            h("td", { class: "num" }, c.applicable),
            h("td", { class: "num" }, c.na || "—"),
            h("td", { class: "num" }, c.fail),
            h("td", { class: "agree-cell" },
              h("div", { class: "bar" }, h("div", { class: `bar-fill ${failClass(c.fail_pct)}`, style: { width: `${c.fail_pct || 0}%` } })),
              h("span", { class: `score ${failClass(c.fail_pct)}` }, c.fail_pct === null ? "—" : `${fmtScore(c.fail_pct)}%`)),
            h("td", { class: "num" }, c.score_pct === null ? "—" : `${fmtScore(c.score_pct)}%`))))),
        h("div", { class: "small muted" }, "«Нарушено» — ответ «Нет» или балл шкалы ниже максимума; для флага грубой ошибки — число диалогов с нарушением.")),

      h("div", { class: "grid-2col" },
        h("div", { class: "card" },
          h("h2", {}, `Грубые нарушения (${s.critical.length})`),
          s.critical.length
            ? h("div", { class: "chips-wrap" }, s.critical.map((x) => h("a", { class: "chip bad", href: link(x.conversation_id, x.evaluation_id) }, `⚠ ${x.conversation_id}`)))
            : h("div", { class: "muted" }, "Не выявлено")),
        h("div", { class: "card" },
          h("h2", {}, "Худшие диалоги"),
          h("table", { class: "tbl clickable" }, h("tbody", {}, s.worst.map((d) => h("tr", { onclick: () => ctx.navigate(link(d.conversation_id, d.evaluation_id)) },
            h("td", { class: "mono" }, d.conversation_id), h("td", {}, d.language), h("td", { class: "num" },
              h("span", { class: `score ${scoreClass(d.total)}` }, fmtScore(d.total)), d.critical_flag ? h("span", { class: "chip bad" }, "⚠") : null))))))));
  }

  load();
  return { update: (patch) => { if ("groupId" in patch || "versionId" in patch) load(); } };
}
