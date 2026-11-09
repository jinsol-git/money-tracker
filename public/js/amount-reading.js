/* 금액을 입력하면 옆에 읽기 쉬운 표기를 보여 준다. 1000원, 3만 1000원, 1240만처럼 적는다. */
"use strict";

function amountReading(text) {
  const digits = String(text).replace(/[,\s원]/g, "");
  if (!/^\d+$/.test(digits)) return "";
  const value = Number(digits);
  const parts = [];
  const eok = Math.floor(value / 100000000);
  const man = Math.floor((value % 100000000) / 10000);
  const rest = value % 10000;
  if (eok) parts.push(`${eok}억`);
  if (man) parts.push(`${man}만`);
  if (rest || !parts.length) parts.push(`${rest}원`);
  return parts.join(" ");
}

if (typeof module !== "undefined") {
  module.exports = { amountReading };
} else {
  for (const input of document.querySelectorAll("input[data-amount]")) {
    const label = input.labels?.[0];
    if (label) {
      const output = document.createElement("output");
      output.className = "amount-reading";
      if (label.contains(input)) label.insertBefore(output, input);
      else label.append(output);
      const show = () => {
        output.textContent = amountReading(input.value);
      };
      input.addEventListener("input", show);
      show();
    }
  }
}
