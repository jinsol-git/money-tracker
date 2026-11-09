/* 하단 서명: 스크롤을 내렸을 때만 나타나고, 화살표를 누르면 링크가 옆으로 밀려 나온다.
   스크롤할 내용이 없는 화면은 나타날 기회가 없으므로 처음부터 보여 준다. */
"use strict";

(() => {
  const SHOW_AFTER = 120;
  const signature = document.querySelector("[data-signature]");
  if (!signature) return;
  const button = signature.querySelector("button");

  function setOpen(open) {
    signature.classList.toggle("is-open", open);
    button.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function refresh() {
    const page = document.documentElement;
    const scrollable = page.scrollHeight - page.clientHeight > SHOW_AFTER;
    const shown = !scrollable || window.scrollY > SHOW_AFTER;
    signature.classList.toggle("is-shown", shown);
    if (!shown) setOpen(false);
  }

  button.addEventListener("click", () => setOpen(!signature.classList.contains("is-open")));
  document.addEventListener("click", (event) => {
    if (!signature.contains(event.target)) setOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && signature.classList.contains("is-open")) {
      setOpen(false);
      button.focus();
    }
  });
  window.addEventListener("scroll", refresh, { passive: true });
  new ResizeObserver(refresh).observe(document.body);
  refresh();
})();
