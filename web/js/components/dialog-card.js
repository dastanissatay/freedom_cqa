// Карточка диалога: транскрипт (+ плеер для звонков) и результат оценки.
import { api } from "../api.js";
import { state, allVersions } from "../state.js";
import { h, add, clear, toast, spinner, fmtScore, fmtDate } from "../util.js";
import * as transcriptC from "./transcript.js";
import * as playerC from "./audio-player.js";
import * as resultC from "./result-panel.js";
import * as refEditorC from "./reference-editor.js";

export function mount(root, ctx) {
  const [, gid, cid] = ctx.route.parts;
  const wantEval = Number(ctx.route.params.get("eval")) || null;
  const wantMsg = ctx.route.params.get("msg");
  let conv = null, evaluation = null, history = [], transcript = null, player = null, result = null;
  // эталонная разметка: для версии чек-листа текущей оценки (или активной, если оценки нет)
  let reference = null, mode = ctx.route.params.get("mode") === "ref" ? "ref" : "result";
  const refVersionId = () => evaluation?.checklist_version_id ?? state.versionId;

  const page = h("section", { class: "page" });
  const head = h("div", { class: "page-head" });
  const left = h("div", { class: "col-left card" });
  const right = h("div", { class: "col-right card" });
  page.append(head, h("div", { class: "split" }, left, right));
  root.append(page);
  left.append(spinner());

  function onRef(evidence, range) { transcript?.highlight(evidence, range); }
  function onSeek(sec) { player?.seek(sec); }

  function renderHead() {
    const ver = allVersions().find((v) => v.id === state.versionId);
    const histSel = history.length > 1
      ? h("select", { class: "inp", title: "История оценок" }, history.map((e) =>
          h("option", { value: e.id, selected: e.id === evaluation?.id }, `#${e.id} · ${e.checklist_name} v${e.version} · ${fmtDate(e.created_at)}`)))
      : null;
    histSel?.addEventListener("change", () => loadEvaluation(Number(histSel.value)));
    const evalBtn = h("button", { class: "btn primary", disabled: !state.versionId }, evaluation ? "↻ Переоценить" : "▶ Оценить");
    const refBtn = h("button", {
      class: `btn ${mode === "ref" ? "on-ref" : "ghost"}`, disabled: !refVersionId(),
      title: "Ручная эталонная разметка для проверки качества LLM",
      onclick: () => { mode = mode === "ref" ? "result" : "ref"; renderHead(); renderResult(); },
    }, mode === "ref" ? "← к результату" : reference ? "✎ Эталон ✓" : "✎ Эталон");
    evalBtn.addEventListener("click", () => runEvaluation(evalBtn));
    add(clear(head),
      h("div", {},
        h("a", { href: "#/dialogs", class: "back" }, "← к списку"),
        h("h1", {}, conv?.conversation_id || cid,
          conv ? h("span", { class: `chip ${conv.channel === "voice call" ? "call" : "chat"}` }, conv.channel === "voice call" ? "☎ звонок" : "💬 чат") : null,
          conv ? h("span", { class: "chip lang" }, conv.language) : null)),
      h("div", { class: "actions" },
        histSel,
        h("span", { class: "muted small" }, ver ? `чек-лист: ${ver.name} v${ver.version}` : "чек-лист не выбран"),
        refBtn,
        evalBtn));
  }

  async function runEvaluation(btn) {
    btn.disabled = true;
    btn.textContent = "Оцениваю…";
    clear(right).append(spinner("LLM оценивает диалог по чек-листу…"));
    try {
      evaluation = await api.evaluate(gid, cid, state.versionId);
      history = await api.evaluations(gid, cid);
      await loadReference();
      toast(`Оценка готова: ${fmtScore(evaluation.final.total)}`);
      renderHead();
      renderResult();
    } catch (e) {
      toast(`Ошибка оценки: ${e.message}`, "error");
      renderHead();
      renderResult();
    }
  }

  async function loadEvaluation(id) {
    clear(right).append(spinner());
    evaluation = id ? await api.evaluation(id) : null;
    await loadReference();
    renderHead();
    renderResult();
  }

  async function loadReference() {
    const vid = refVersionId();
    reference = vid ? await api.reference(gid, cid, vid).catch(() => null) : null;
  }

  async function renderReferenceEditor() {
    const vid = refVersionId();
    clear(right).append(spinner());
    const v = await api.version(vid);
    clear(right);
    result = refEditorC.mount(right, {
      groupId: gid, conversationId: cid,
      version: { id: v.id, name: v.checklist_name, version: v.version, config: v.config },
      reference, onRef,
      llmVerdicts: evaluation && evaluation.checklist_version_id === vid ? evaluation.verdicts : null,
      onSaved: (saved) => { reference = saved; mode = "result"; renderHead(); renderResult(); },
      onClose: () => { mode = "result"; renderHead(); renderResult(); },
    });
  }

  function renderResult() {
    result?.destroy?.();
    clear(right);
    if (mode === "ref") { renderReferenceEditor(); return; }
    if (!evaluation) {
      right.append(h("div", { class: "empty" },
        h("p", {}, "Диалог ещё не оценён."),
        h("p", { class: "muted" }, "Выберите чек-лист в шапке и нажмите «Оценить».")));
      return;
    }
    result = resultC.mount(right, {
      evaluation, conversation: conv, role: state.role, onRef, reference,
      onChanged: async (opts) => { if (opts?.soft) renderResult(); else await loadEvaluation(evaluation.id); },
    });
  }

  (async () => {
    try {
      conv = await api.conversation(gid, cid);
      history = await api.evaluations(gid, cid);
    } catch (e) {
      clear(left).append(h("div", { class: "empty" }, e.message));
      return;
    }
    clear(left);
    if (conv.channel === "voice call" && conv.audio_url) player = playerC.mount(left, { groupId: gid, conversationId: cid, originalUrl: conv.audio_url });
    const tBox = h("div", { class: "transcript-box" });
    left.append(tBox);
    transcript = transcriptC.mount(tBox, { conversation: conv, onSeek });
    const id = wantEval && history.some((e) => e.id === wantEval) ? wantEval : history[0]?.id;
    await loadEvaluation(id || null);
    if (wantMsg !== null) transcript.highlight([Number(wantMsg)], null, { seek: false });
  })();

  return {
    update(patch) {
      if ("role" in patch) renderResult();
      if ("versionId" in patch) {
        if (evaluation) renderHead();
        else loadReference().then(() => { renderHead(); renderResult(); });
      }
      if ("groupId" in patch) ctx.navigate("#/dialogs");
    },
    destroy() { player?.destroy?.(); },
  };
}
