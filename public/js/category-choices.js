/* 분류 고치기 폼에서 고른 종류에 맞는 카테고리만 보여 준다.
   지출과 환불·정산이면 지출 카테고리, 수입이면 수입 카테고리이고 다른 종류에는 카테고리가 없다.
   첫 칸은 카테고리를 고르지 않으면 들어갈 이름을 적는다. */
"use strict";

const CATEGORY_KINDS = { expense: "expense", refund: "expense", income: "income" };

function categoryLabel(select, kind) {
  const { current, currentKind, autoExpense, autoIncome } = select.dataset;
  if (!kind) return "카테고리 없음";
  if (current !== undefined && currentKind === kind) return current ? `그대로 두기 (${current})` : "그대로 두기";
  const auto = kind === "income" ? autoIncome : autoExpense;
  return auto || "자동으로 찾기"; // 고르지 않으면 들어갈 카테고리의 이름 그대로다
}

function syncCategories(nature, select, parts) {
  const kind = CATEGORY_KINDS[nature.value] || null;
  const chosen = select.value;
  const [first, ...rest] = parts;
  // 숨기기만 하면 아이폰 목록에는 그대로 보여서, 맞지 않는 묶음은 목록에서 뺐다가 다시 넣는다.
  const shown = rest.filter((part) => (part.dataset.kind ? part.dataset.kind === kind : Boolean(kind)));
  for (const part of shown) {
    part.hidden = false;
    part.disabled = false;
  }
  select.replaceChildren(first, ...shown);
  first.textContent = categoryLabel(select, kind);
  select.value = [...select.options].some((option) => option.value === chosen) ? chosen : "";
  select.disabled = !kind;
}

/* 검토 화면은 적용한 뒤 목록을 새로 그리므로 새로 들어온 폼에도 다시 붙인다. */
function initCategoryChoices(root) {
  for (const select of root.querySelectorAll("select[data-category-choices]")) {
    const nature = select.form && select.form.querySelector('select[name="nature"]');
    if (!nature) continue;
    const parts = [...select.children];
    nature.addEventListener("change", () => syncCategories(nature, select, parts));
    syncCategories(nature, select, parts);
  }
}

initCategoryChoices(document);
