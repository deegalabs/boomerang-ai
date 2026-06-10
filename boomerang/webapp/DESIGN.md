# Boomerang AI — Design System

> Aesthetic direction: **"luxury financial terminal + kinetic boomerang motion".**
> Deep-space background with atmosphere, **BNB gold** as a sharp accent, characterful
> typography, and monospaced data. **Dark** theme.

## Principles
1. **Clarity** above all — a layperson understands in seconds.
2. **Visible trust** — on-chain proof, real numbers, security explained.
3. **Progressive disclosure** — simple by default, advanced on demand.
4. **Safety in control** — money-moving actions require confirmation; the key never reaches the browser.
5. **Beauty with purpose** — atmosphere (mesh + grain), intentional motion, no empty decoration.
6. **Bilingual** EN/PT from the skeleton up.

## Color (tokens in `static/css/app.css`)
| Token | Hex | Use |
|---|---|---|
| `--bg` | `#06080F` | Background (deep space) |
| `--surface` | `#131A2A` | Cards/panels |
| `--line` | `#232C40` | Borders |
| `--gold` | `#F3BA2F` | **Primary accent** (BNB) |
| `--gold-hi` | `#FBD66B` | Gold glow/gradient |
| `--up` | `#34E5A4` | Profit / positive |
| `--down` | `#FF6B6B` | Loss / negative |
| `--info` | `#5AA9FF` | Information |
| `--text` / `--muted` / `--faint` | `#EAEEF8` / `#93A0B8` / `#5C6880` | Text |

Atmosphere: `body::before` = radial-gradient mesh (gold + blue + green) with slow drift; `body::after` = fine grain (SVG noise, 3.5% opacity).

## Typography
- **Display** — `Bricolage Grotesque` (Fontshare): headings, hero numbers. Confident, characterful.
- **Body** — `Hanken Grotesk` (Google): running text, clean and warm.
- **Data/Mono** — `JetBrains Mono` (Google): prices, %, hashes, technical labels (`tnum`).

Scale: `.display` (clamp 2.7–5.4rem), `h2`, `h3`, `.lead`, `.eyebrow` (mono, wide tracking, gold).

## Components (utility classes)
- **Buttons**: `.btn` + `.btn--gold` (CTA), `.btn--ghost`, `.btn--danger`, `.btn--sm`.
- **Cards**: `.card`, `.card--glow` (gold hairline on top), `.card__lbl` (mono label).
- **Stat tile**: `.stat` + `.v` (large value); combine with `.mono` / `.grad-gold`.
- **Pills**: `.pill` + `.pill--up/--down/--gold`; `.dot` (glowing dot).
- **Table**: `.tbl` (mono header, row hover).
- **Controls**: `.field` + `.input` / `.select`; `.seg` (segmented control, `.on` option).
- **Brand**: `partials/mark.html` (boomerang SVG, gold gradient, slight tilt).

## Motion
- Entrance: `.reveal` + `.d1…d6` (stagger on load).
- Hero: the boomerang spins in an arc (`heroSpin`), a trail that "draws" (`drawTrail`), orbits (`spin`).
- Hover: buttons/cards lift (translateY) with a gold glow.
- Respects `prefers-reduced-motion`.

## File structure
```
boomerang/webapp/
  site.py                 ← the production app (factory + `app`); serves all routes
  static/css/app.css      ← design system (tokens, components, motion)
  static/js/app.js        ← light interactions (nav scroll, language, wallet connect)
  templates/
    base.html             ← shell (nav + language + footer)
    partials/mark.html     ← boomerang brand mark
    landing.html           ← landing page
    docs/, guides/         ← documentation + step-by-step guide (EN/PT)
    live.html              ← live on-chain proof (reads /api/live)
    console.html           ← demo console (simulated agent, wallet sign-in)
    foundation.html        ← design-system showcase
  i18n.py                 ← EN/PT translations (server-side)
scripts/preview_web.py    ← thin local runner (imports site.py) on port 8090
```

## How to view
- Local dev: `.venv\Scripts\python scripts\preview_web.py` → http://localhost:8090
- Production: `uvicorn boomerang.webapp.site:app` (the same app, behind TLS).
