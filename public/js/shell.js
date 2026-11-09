/* 모든 화면 공통: 낮·밤 전환과 바의 유리 렌즈.
   head에서 바로 실행해 첫 화면부터 고른 밝기로 그린다. 저장값은 이 기기의 화면 설정뿐이다. */
"use strict";

(() => {
  const KEY = "money-tracker-theme";
  const THEME_COLORS = { light: "#f3f4f6", dark: "#0f1218" };
  const root = document.documentElement;
  const system = window.matchMedia("(prefers-color-scheme: dark)");

  function saved() {
    try {
      return window.localStorage.getItem(KEY);
    } catch {
      return null; // 사생활 보호 창처럼 저장소를 막은 경우
    }
  }

  function remember(theme) {
    try {
      window.localStorage.setItem(KEY, theme);
    } catch {
      /* 저장하지 못해도 지금 화면은 바뀐다. */
    }
  }

  function chosen() {
    const value = saved();
    if (value === "light" || value === "dark") return value;
    return system.matches ? "dark" : "light";
  }

  function apply(theme) {
    root.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLORS[theme]);
    for (const button of document.querySelectorAll("[data-theme-toggle]")) {
      button.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    }
  }

  apply(chosen());
  // 직접 고른 적이 없으면 기기 설정을 따라간다. 다른 탭에서 바꾼 값도 따라간다.
  system.addEventListener("change", () => apply(chosen()));
  window.addEventListener("storage", (event) => {
    if (event.key === KEY) apply(chosen());
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-theme-toggle]")) return;
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    remember(next);
    apply(next);
  });

  /* 지금 위치를 덮는 렌즈. 차트 탭은 다시 불러오지 않으므로 표시가 바뀌면 미끄러져 간다. */
  function moveLens(nav) {
    const lens = nav.querySelector(".lens");
    const current = nav.querySelector("a[aria-current]");
    if (!current || !current.offsetWidth) {
      lens.style.opacity = "0";
      return;
    }
    lens.style.opacity = "1";
    lens.style.transform = `translateX(${current.offsetLeft}px)`;
    lens.style.width = `${current.offsetWidth}px`;
  }

  document.addEventListener("DOMContentLoaded", () => {
    apply(root.dataset.theme);
    for (const nav of document.querySelectorAll("[data-lens]")) {
      nav.classList.add("has-lens");
      moveLens(nav);
      new ResizeObserver(() => moveLens(nav)).observe(nav);
      new MutationObserver(() => moveLens(nav)).observe(nav, { attributes: true, attributeFilter: ["aria-current"], subtree: true });
      document.fonts?.ready.then(() => moveLens(nav));
      // 처음 자리를 잡을 때는 미끄러지지 않는다.
      window.requestAnimationFrame(() => window.requestAnimationFrame(() => nav.classList.add("lens-ready")));
    }
  });
})();
