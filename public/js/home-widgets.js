/* 홈의 위젯 화면. 그림을 누를 때마다 다음 순서로 골라지고, 다시 누르면 빠진다.
   적용을 누르면 고른 순서대로 저장한다. 차트 위젯의 그림은 dashboard.js가 그린다. */
"use strict";

(() => {
  const dialog = document.querySelector("[data-home-widget-page]");
  if (!dialog) return;
  const PREVIEW_WIDTH = 640; // 미리 보기를 그리는 폭. 카드 폭에 맞춰 줄인다
  const form = dialog.querySelector("[data-wp-form]");
  const count = dialog.querySelector("[data-wp-count]");
  const cards = [...dialog.querySelectorAll("[data-wp-card]")];
  const chosen = (dialog.dataset.visible || "").split(",").filter(Boolean);

  const resize = new ResizeObserver((entries) => {
    for (const entry of entries) entry.target.style.setProperty("--wp-scale", entry.contentRect.width / PREVIEW_WIDTH);
  });

  /* 고른 위젯에 순서와 선택됨을 붙인다. */
  function mark() {
    for (const card of cards) {
      const at = chosen.indexOf(card.dataset.wpCard);
      const hit = card.querySelector(".wp-hit");
      const name = card.querySelector(".wp-name").textContent;
      card.classList.toggle("is-on", at >= 0);
      card.querySelector(".wp-mark strong").textContent = at >= 0 ? String(at + 1) : "";
      hit.setAttribute("aria-pressed", at >= 0 ? "true" : "false");
      hit.setAttribute("aria-label", at >= 0 ? `${name}, ${at + 1}번째로 선택됨` : `${name} 고르기`);
    }
    count.textContent = chosen.length ? `${chosen.length}개 선택` : "고른 위젯이 없습니다";
  }

  for (const card of cards) {
    resize.observe(card.querySelector(".wp-preview"));
    card.querySelector(".wp-hit").addEventListener("click", () => {
      const at = chosen.indexOf(card.dataset.wpCard);
      if (at >= 0) chosen.splice(at, 1);
      else chosen.push(card.dataset.wpCard);
      mark();
    });
  }

  // 고른 순서대로 같은 이름의 값을 여러 번 보낸다.
  form.addEventListener("submit", () => {
    for (const old of form.querySelectorAll('input[name="keys"]')) old.remove();
    for (const key of chosen) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "keys";
      input.value = key;
      form.append(input);
    }
  });

  const leave = form.querySelector("[data-wp-cancel]").href;
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault(); // Esc도 취소와 같다
    window.location.assign(leave);
  });

  mark();
  dialog.showModal();
})();
