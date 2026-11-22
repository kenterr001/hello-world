// Smart Cloud portal – small progressive enhancements (no external libraries).
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const cur = (document.body.dataset.currency || "€");

  // Range inputs with live output
  $$("input[type=range][data-out]").forEach((el) => {
    const out = document.getElementById(el.dataset.out);
    const upd = () => { out.textContent = el.value; calc(); };
    el.addEventListener("input", upd);
  });

  // Deal value calculator
  function calc() {
    const tcvOut = $("#tcvOut");
    if (!tcvOut) return;
    const n = (name) => parseFloat(($(`[name=${name}]`) || {}).value || 0) || 0;
    const tcv = n("value_mrc") * n("term_months") + n("value_otc");
    const p = n("probability");
    const fmt = (v) => cur + Math.round(v).toLocaleString("en-GB");
    tcvOut.textContent = fmt(tcv);
    $("#wOut").textContent = fmt((tcv * p) / 100) + " weighted at " + p + "%";
  }
  $$("[data-calc]").forEach((el) => el.addEventListener("input", calc));
  calc();

  // Print buttons
  $$("[data-print]").forEach((b) => b.addEventListener("click", () => window.print()));

  // Copy buttons
  $$("[data-copy]").forEach((b) => b.addEventListener("click", () => {
    const inp = document.getElementById(b.dataset.copy);
    inp.select();
    (navigator.clipboard ? navigator.clipboard.writeText(inp.value) : Promise.reject()).catch(() => document.execCommand("copy"));
    b.textContent = "Copied";
  }));

  // Auto-submit selects
  $$("select[data-autosubmit]").forEach((s) => s.addEventListener("change", () => s.form.submit()));

  // Confirm buttons
  $$("[data-confirm]").forEach((b) => b.addEventListener("click", (e) => {
    if (!window.confirm(b.dataset.confirm)) e.preventDefault();
  }));

  // ---------- onboarding sheet ----------
  const form = $("#sheetForm");
  if (form) {
    // show/hide VPS & backup sections according to selected services
    const toggles = $$("input[data-toggle]", form);
    const sync = () => {
      const on = toggles.filter((t) => t.checked).map((t) => t.dataset.toggle);
      $$("[data-section]", form).forEach((sec) => sec.classList.toggle("hidden", !on.includes(sec.dataset.section)));
      $$(".sheet-nav a[data-svc]").forEach((a) => a.classList.toggle("off", !on.includes(a.dataset.svc)));
    };
    toggles.forEach((t) => t.addEventListener("change", sync));
    sync();

    // VPS rows
    const table = $("#vpsTable");
    const packages = JSON.parse(table.dataset.packages || "[]");
    const tbody = $("tbody", table);
    const template = tbody.rows[0].cloneNode(true);
    $$("input", template).forEach((i) => { i.value = i.name === "vps_qty" ? "1" : ""; });
    $$("select", template).forEach((s) => { s.selectedIndex = 0; });

    function wire(row) {
      $(".pkg", row).addEventListener("change", (e) => {
        const p = packages.find((x) => x.name === e.target.value);
        if (!p) return;
        if (p.vcpu) $("[name=vps_vcpu]", row).value = p.vcpu;
        if (p.ram_gb) $("[name=vps_ram_gb]", row).value = p.ram_gb;
        if (p.storage_gb) $("[name=vps_storage_gb]", row).value = p.storage_gb;
      });
      $(".del-row", row).addEventListener("click", () => {
        if (tbody.rows.length > 1) row.remove();
        else $$("input,select", row).forEach((i) => { if (i.tagName === "SELECT") i.selectedIndex = 0; else i.value = ""; });
      });
    }
    Array.from(tbody.rows).forEach(wire);
    $("#addVps").addEventListener("click", () => {
      const r = template.cloneNode(true);
      tbody.appendChild(r);
      wire(r);
      $("input", r).focus();
    });

    // Warn on unsaved changes
    let dirty = false;
    form.addEventListener("input", () => { dirty = true; });
    form.addEventListener("submit", () => { dirty = false; });
    window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
  }
})();
