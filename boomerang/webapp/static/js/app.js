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

// Scrollspy do índice da documentação (destaca a seção visível).
(function () {
  const links = document.querySelectorAll(".docs-side a");
  if (!links.length) return;
  const byId = {};
  links.forEach((a) => { byId[a.getAttribute("href").slice(1)] = a; });
  const sections = [...links]
    .map((a) => document.querySelector(a.getAttribute("href")))
    .filter(Boolean);
  const obs = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) {
        links.forEach((l) => l.classList.remove("active"));
        const a = byId[e.target.id];
        if (a) a.classList.add("active");
      }
    });
  }, { rootMargin: "-15% 0px -75% 0px" });
  sections.forEach((s) => obs.observe(s));
})();

// The nav "Connect Wallet" is a link to /console, where the real login lives
// (wallet via SIWE, or guest mode without a wallet). Nothing to do here.
