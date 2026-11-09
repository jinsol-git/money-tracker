/* 검토, 반복 거래, 기록 화면의 분류 폼. 적용을 누르면 화면을 다시 불러오지 않고 그 자리에서 저장한다.
   기다리는 동안 버튼에 도는 표시가 나오고, 저장되면 완료로 바뀐 뒤 화면을 새 상태로 바꾼다. 보던 자리는 그대로다.
   스크립트가 없거나 실패하면 보통 폼처럼 화면을 다시 불러와 저장한다. */
"use strict";

(() => {
  const DONE_MS = 700; // 완료 표시를 보여 주는 시간
  const LEAVE_MS = 260; // 처리되어 목록에서 빠지는 항목이 흐려지는 시간
  const TOAST_MS = 6000;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

  const wait = (ms) => new Promise((resolve) => window.setTimeout(resolve, reduced.matches ? 0 : ms));
  const selectorOf = (id) => `[data-item="${CSS.escape(id)}"]`;

  function showBusy(button) {
    const label = button.textContent;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.classList.add("is-busy");
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    spinner.setAttribute("aria-hidden", "true");
    button.replaceChildren(spinner, "적용 중");
    return label;
  }

  function restore(button, label) {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.classList.remove("is-busy", "is-done");
    button.textContent = label;
  }

  function showDone(button) {
    button.removeAttribute("aria-busy");
    button.classList.remove("is-busy");
    button.classList.add("is-done");
    button.textContent = "✓ 완료";
  }

  function showError(form, text) {
    let note = form.querySelector(".inline-error");
    if (!note) {
      note = document.createElement("p");
      note.className = "inline-error";
      note.setAttribute("role", "alert");
      form.append(note);
    }
    note.textContent = text;
  }

  // 화면 맨 위의 안내는 보던 자리에서 보이지 않으므로 아래에 잠깐 띄운다. 되돌리기 버튼도 함께다.
  let toastBox = null;
  let toastTimer = 0;
  function toast(notices) {
    toastBox?.remove();
    window.clearTimeout(toastTimer);
    if (!notices.length) return;
    toastBox = document.createElement("div");
    toastBox.className = "toast";
    toastBox.setAttribute("role", "status");
    for (const notice of notices) toastBox.append(document.importNode(notice, true));
    document.body.append(toastBox);
    toastTimer = window.setTimeout(() => toastBox?.remove(), TOAST_MS);
  }

  /* 화면을 바꾼 뒤 같은 높이에 둘 항목. 처리된 항목이 빠지면 그 다음 항목이 그 자리에 온다. */
  function anchorFor(item, fresh) {
    const exists = (node) => node.dataset.item && fresh.querySelector(selectorOf(node.dataset.item));
    const top = item.getBoundingClientRect().top;
    if (exists(item)) return { id: item.dataset.item, top };
    for (let node = item.nextElementSibling; node; node = node.nextElementSibling) {
      if (exists(node)) return { id: node.dataset.item, top };
    }
    for (let node = item.previousElementSibling; node; node = node.previousElementSibling) {
      if (exists(node)) return { id: node.dataset.item, top: node.getBoundingClientRect().top };
    }
    return null;
  }

  function swapPage(fresh) {
    const main = document.querySelector("main");
    const next = fresh.querySelector("main");
    if (!main || !next) return false;
    for (const script of next.querySelectorAll("script")) script.remove(); // 이미 실행된 스크립트다
    main.replaceChildren(...[...next.childNodes].map((node) => document.importNode(node, true)));
    // 메뉴의 확인할 거래 수
    const counts = [...fresh.querySelectorAll("sup.cnt")];
    for (const [index, sup] of [...document.querySelectorAll("sup.cnt")].entries()) {
      const same = counts[index] || counts[0];
      if (!same) {
        sup.remove();
        continue;
      }
      sup.textContent = same.textContent;
      sup.setAttribute("aria-label", same.getAttribute("aria-label") || "");
    }
    if (typeof initCategoryChoices === "function") initCategoryChoices(main);
    return true;
  }

  async function send(form, submitter) {
    const button = submitter || form.querySelector('button[type="submit"]');
    const body = new URLSearchParams(new FormData(form, submitter || undefined));
    form.querySelector(".inline-error")?.remove();
    const label = button ? showBusy(button) : "";
    const fail = (text) => {
      delete form.dataset.sending;
      if (button) restore(button, label);
      showError(form, text);
    };
    let fresh;
    try {
      const response = await fetch(form.action, { method: "POST", body, credentials: "same-origin", headers: { Accept: "text/html" } });
      if (response.status === 401) {
        window.location.assign("/auth/login");
        return;
      }
      if (!response.ok) throw new Error(String(response.status));
      fresh = new DOMParser().parseFromString(await response.text(), "text/html");
    } catch {
      fail("저장하지 못했습니다. 다시 눌러 주세요.");
      return;
    }
    const error = fresh.querySelector("main .notice.error");
    if (error) {
      fail(error.textContent.trim());
      return;
    }
    if (button) showDone(button);
    const item = form.closest("[data-item]");
    await wait(DONE_MS);
    const anchor = item && anchorFor(item, fresh);
    if (item && !fresh.querySelector(selectorOf(item.dataset.item))) {
      item.classList.add("is-leaving");
      await wait(LEAVE_MS);
    }
    if (!swapPage(fresh)) {
      window.location.reload(); // 받은 화면이 예상과 다르면 저장은 끝났으니 새로 불러온다
      return;
    }
    if (anchor) {
      const node = document.querySelector(selectorOf(anchor.id));
      if (node) window.scrollBy(0, node.getBoundingClientRect().top - anchor.top);
    }
    toast([...fresh.querySelectorAll("main .notice.ok")]);
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-inline")) return;
    event.preventDefault();
    if (form.dataset.sending) return; // 두 번 눌러도 한 번만 보낸다
    form.dataset.sending = "1";
    send(form, event.submitter);
  });
})();
