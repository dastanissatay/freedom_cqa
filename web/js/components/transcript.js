// Транскрипт с якорями #msg-i, подсветкой доказательств и проверенного участка.
import { h, clear, SPEAKER, fmtTimestamp } from "../util.js";

export function mount(root, { conversation, onSeek }) {
  const isCall = conversation.channel === "voice call";
  const list = h("div", { class: "transcript" });
  const items = conversation.transcript.map((m) => {
    const el = h("div", { class: `msg ${m.speaker}`, id: `msg-${m.index}`, "data-index": m.index },
      h("div", { class: "msg-meta" },
        h("span", { class: "msg-idx" }, `[${m.index}]`),
        h("span", { class: "msg-who" }, SPEAKER[m.speaker] || m.speaker),
        h("span", { class: "msg-ts", title: m.timestamp }, fmtTimestamp(m, conversation.channel)),
        isCall && m.offset_sec !== null ? h("button", { class: "play-btn", title: "Воспроизвести с этого места", onclick: (e) => { e.stopPropagation(); onSeek?.(m.offset_sec, m.index); } }, "▶") : null),
      h("div", { class: "msg-text" }, m.text));
    return el;
  });
  list.append(...items);
  root.append(list);

  function clearHighlight() {
    items.forEach((el) => el.classList.remove("hl", "in-range", "range-start", "range-end"));
  }

  function highlight(evidence = [], range = null, { seek = true } = {}) {
    clearHighlight();
    if (range && range.length === 2) {
      for (let i = range[0]; i <= range[1]; i++) items[i]?.classList.add("in-range");
      items[range[0]]?.classList.add("range-start");
      items[range[1]]?.classList.add("range-end");
    }
    evidence.forEach((i) => items[i]?.classList.add("hl"));
    const target = evidence.length ? evidence[0] : range ? range[0] : null;
    if (target !== null && items[target]) {
      items[target].scrollIntoView({ behavior: "smooth", block: "center" });
      items[target].classList.remove("flash");
      void items[target].offsetWidth; // перезапуск анимации
      items[target].classList.add("flash");
      const m = conversation.transcript[target];
      if (seek && isCall && m?.offset_sec !== null && m?.offset_sec !== undefined) onSeek?.(m.offset_sec, target);
    }
  }

  return { highlight, clearHighlight, destroy: () => clear(root) };
}
