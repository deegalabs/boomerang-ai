// Boomerang AI — interações leves (sem framework).

// Sutil: sombra/realce no nav ao rolar.
(function () {
  const nav = document.querySelector(".nav");
  if (!nav) return;
  const onScroll = () => nav.classList.toggle("nav--scrolled", window.scrollY > 8);
  onScroll();
  window.addEventListener("scroll", onScroll, { passive: true });
})();

// Persistir idioma escolhido (o link ?lang= também grava cookie no servidor).
(function () {
  document.querySelectorAll(".lang a").forEach((a) => {
    a.addEventListener("click", () => {
      const lang = a.textContent.trim().toLowerCase();
      document.cookie = `lang=${lang};path=/;max-age=31536000`;
    });
  });
})();

// Contador animado (elementos com data-count) — sobe do 0 até o alvo.
(function () {
  const els = document.querySelectorAll("[data-count]");
  els.forEach((el) => {
    const target = parseFloat(el.dataset.count);
    const prefix = el.dataset.prefix || "";
    const dec = (el.dataset.count.split(".")[1] || "").length;
    const dur = 1400, t0 = performance.now();
    const ease = (x) => 1 - Math.pow(1 - x, 3);
    function frame(now) {
      const p = Math.min((now - t0) / dur, 1);
      el.textContent = prefix + (target * ease(p)).toFixed(dec);
      if (p < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  });
})();

// Parallax sutil do boomerang seguindo o cursor (profundidade).
(function () {
  const stage = document.querySelector(".hero-stage");
  const wing = document.querySelector(".hero-bmrg");
  if (!stage || !wing || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  stage.addEventListener("mousemove", (e) => {
    const r = stage.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5;
    const y = (e.clientY - r.top) / r.height - 0.5;
    wing.style.transform = `translate3d(${x * 16}px, ${y * 16}px, 0)`;
  });
  stage.addEventListener("mouseleave", () => { wing.style.transform = ""; });
})();

// Stub: conexão de carteira (Sign-In with Ethereum) — implementado na Fase 4.
window.boomerangConnect = async function () {
  alert("Conexão de carteira (Sign-In with Ethereum) chega na Fase 4 — Console privado.");
};
