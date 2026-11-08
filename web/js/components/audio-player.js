// Аудиоплеер звонка с переходом к моменту записи.
// Источник — бэкенд (/api/audio/{group}/{id}): он раскрывает страницу-плеер до mp3,
// обновляет истёкшую подпись ссылки и отдаёт поток с поддержкой перемотки (Range).
import { api } from "../api.js";
import { h, fmtOffset } from "../util.js";

export function mount(root, { groupId, conversationId, originalUrl }) {
  let retried = false;
  const src = api.audioUrl(groupId, conversationId);
  const audio = h("audio", { controls: true, preload: "metadata", src });
  const info = h("span", { class: "muted small" }, "");
  const speed = h("select", { class: "inp small-select", title: "Скорость воспроизведения" },
    [0.75, 1, 1.25, 1.5, 2].map((v) => h("option", { value: v, selected: v === 1 }, `${v}x`)));
  speed.addEventListener("change", () => { audio.playbackRate = Number(speed.value); });

  audio.addEventListener("error", () => {
    if (!retried) {
      // одна повторная попытка (например, истекла подписанная ссылка на хранилище)
      retried = true;
      const t = audio.currentTime;
      audio.src = `${src}?r=${Date.now()}`;
      audio.currentTime = t;
      info.textContent = "повторная загрузка записи…";
      return;
    }
    info.textContent = "";
    info.append("запись недоступна",
      originalUrl ? h("a", { href: originalUrl, target: "_blank", rel: "noopener", style: { marginLeft: "6px" } }, "открыть исходную ссылку ↗") : "");
  });
  audio.addEventListener("loadedmetadata", () => { info.textContent = `длительность ${fmtOffset(audio.duration)}`; });

  const box = h("div", { class: "player" }, h("span", { class: "player-label" }, "☎ Запись звонка"), audio, speed, info);
  root.append(box);

  function seek(sec) {
    if (sec === null || sec === undefined) return;
    const go = () => {
      audio.currentTime = sec;
      audio.play().catch(() => {});
      info.textContent = `переход к ${fmtOffset(sec)}`;
    };
    if (audio.readyState >= 1) go();
    else audio.addEventListener("loadedmetadata", go, { once: true });
  }

  return { seek, destroy: () => { audio.pause(); audio.removeAttribute("src"); audio.load(); box.remove(); } };
}
