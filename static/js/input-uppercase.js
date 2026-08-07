(() => {
  const UPPERCASE_SELECTOR = "[data-uppercase]";
  const LOWERCASE_SELECTOR = "[data-lowercase]";

  const transformValue = (el, transform) => {
    if (!el || el.disabled || el.readOnly) return;
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const next = transform(el.value);
    if (el.value !== next) {
      el.value = next;
      if (start !== null && end !== null) {
        el.setSelectionRange(start, end);
      }
    }
  };

  const toUpperValue = (el) => transformValue(el, (value) => value.toUpperCase());
  const toLowerValue = (el) => transformValue(el, (value) => value.toLowerCase());

  const bindCaseInputs = (root = document, selector, datasetKey, className, transform) => {
    root.querySelectorAll(selector).forEach((el) => {
      if (el.dataset[datasetKey] === "true") return;
      el.dataset[datasetKey] = "true";
      el.classList.add(className);
      el.addEventListener("input", () => transform(el));
      el.addEventListener("blur", () => transform(el));
      if (el.value) transform(el);
    });
  };

  const bindFormSubmit = (root = document) => {
    root.querySelectorAll("form").forEach((form) => {
      if (form.dataset.caseSubmitBound === "true") return;
      if (!form.querySelector(`${UPPERCASE_SELECTOR}, ${LOWERCASE_SELECTOR}`)) return;
      form.dataset.caseSubmitBound = "true";
      form.addEventListener("submit", () => {
        form.querySelectorAll(UPPERCASE_SELECTOR).forEach(toUpperValue);
        form.querySelectorAll(LOWERCASE_SELECTOR).forEach(toLowerValue);
      });
    });
  };

  window.initUppercaseInputs = (root = document) => {
    bindCaseInputs(root, UPPERCASE_SELECTOR, "uppercaseBound", "input-uppercase", toUpperValue);
    bindCaseInputs(root, LOWERCASE_SELECTOR, "lowercaseBound", "input-lowercase", toLowerValue);
    bindFormSubmit(root);
  };

  document.addEventListener("DOMContentLoaded", () => {
    window.initUppercaseInputs();
  });
})();
