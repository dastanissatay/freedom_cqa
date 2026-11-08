// DOM-хелперы без зависимостей.
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "html") el.innerHTML = v;
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const clear = (el) => { while (el.firstChild) el.firstChild.remove(); return el; };

// append без null/false (нативный Element.append превращает null в текст "null")
export const add = (el, ...kids) => { el.append(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false)); return el; };

export function answerLabel(answer, crit) {
  if (answer === "yes") return "Да";
  if (answer === "no") return "Нет";
  if (answer === "na") return "N/A";
  if (typeof answer === "number") {
    const max = crit?.scale?.length ? Math.max(...crit.scale.map((l) => l.value)) : null;
    return max !== null ? `${answer} из ${max}` : String(answer);
  }
  return String(answer ?? "—");
}

export function answerClass(answer, crit) {
  if (answer === "na") return "na";
  if (crit?.type === "flag") return answer === "yes" ? "bad" : "good";
  if (answer === "yes") return "good";
  if (answer === "no") return "bad";
  if (typeof answer === "number" && crit?.scale?.length) {
    const max = Math.max(...crit.scale.map((l) => l.value));
    return answer >= max ? "good" : answer === 0 ? "bad" : "mid";
  }
  return "";
}

export function allowedAnswers(crit) {
  const vals = crit.type === "scale" ? [...crit.scale].sort((a, b) => b.value - a.value).map((l) => l.value) : ["yes", "no"];
  if (crit.na_allowed) vals.push("na");
  return vals;
}

export const fmtScore = (x) => (x === null || x === undefined ? "—" : Number(x).toFixed(2).replace(/\.00$/, ""));
export const fmtMoney = (x) => (x === null || x === undefined ? "—" : `$${Number(x).toFixed(4)}`);

export function scoreClass(x) {
  if (x === null || x === undefined) return "na";
  return x >= 85 ? "good" : x >= 60 ? "mid" : "bad";
}

export const SPEAKER = { client: "Клиент", operator: "Оператор", bot: "Бот", system: "Система" };

export function fmtOffset(sec) {
  if (sec === null || sec === undefined) return "";
  const s = Math.floor(sec);
  const m = Math.floor(s / 60);
  const hh = Math.floor(m / 60);
  const pad = (n) => String(n).padStart(2, "0");
  return hh ? `${hh}:${pad(m % 60)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`;
}

export function fmtTimestamp(msg, channel) {
  // У чатов MM:SS как есть; у звонков ISO → локальное время + смещение от начала
  if (channel === "voice call" && msg.offset_sec !== null && msg.offset_sec !== undefined) {
    return `+${fmtOffset(msg.offset_sec)}`;
  }
  return msg.timestamp ?? "";
}

let toastTimer;
export function toast(msg, kind = "info") {
  let el = document.querySelector(".cqa-toast");
  if (!el) { el = h("div", { class: "cqa-toast" }); document.body.append(el); }
  el.textContent = msg;
  el.className = `cqa-toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), kind === "error" ? 7000 : 3500);
}

export function spinner(text = "Загрузка…") {
  return h("div", { class: "loading" }, h("span", { class: "spin" }), text);
}

export function fmtDate(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleString("ru-RU"); } catch { return iso; }
}
