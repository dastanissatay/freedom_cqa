// Конфигурация фронтенда. Для веб-ресурса Dynamics 365 укажите полный URL бэкенда,
// например: apiBase: "https://cqa.example.com". Пусто = тот же origin, что и страница.
window.CQA_CONFIG = Object.assign(
  {
    apiBase: "",
    defaultGroupId: "DEV",
  },
  window.CQA_CONFIG || {}
);
