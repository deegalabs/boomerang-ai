# Boomerang AI — Design System

> Direção estética: **"Terminal financeiro de luxo + movimento cinético de boomerang"**.
> Fundo de espaço profundo com atmosfera, **dourado da BNB** como acento afiado,
> tipografia de caráter e dados em monoespaçada. Tema **dark**.

## Princípios
1. **Clareza** acima de tudo — o leigo entende em segundos.
2. **Confiança visível** — prova on-chain, números reais, segurança explicada.
3. **Revelação progressiva** — simples por padrão, avançado sob demanda.
4. **Segurança no controle** — ações que mexem em dinheiro pedem confirmação; chave nunca no browser.
5. **Beleza com propósito** — atmosfera (mesh + grão), motion intencional, nada de enfeite vazio.
6. **Bilíngue** EN/PT desde o esqueleto.

## Cor (tokens em `static/css/app.css`)
| Token | Hex | Uso |
|---|---|---|
| `--bg` | `#06080F` | Fundo (espaço profundo) |
| `--surface` | `#131A2A` | Cards/painéis |
| `--line` | `#232C40` | Bordas |
| `--gold` | `#F3BA2F` | **Acento principal** (BNB) |
| `--gold-hi` | `#FBD66B` | Brilho/gradiente do dourado |
| `--up` | `#34E5A4` | Lucro / positivo |
| `--down` | `#FF6B6B` | Perda / negativo |
| `--info` | `#5AA9FF` | Informação |
| `--text` / `--muted` / `--faint` | `#EAEEF8` / `#93A0B8` / `#5C6880` | Texto |

Atmosfera: `body::before` = mesh de gradientes radiais (gold + azul + verde) com deriva lenta; `body::after` = grão fino (SVG noise, opacidade 3,5%).

## Tipografia
- **Display** — `Clash Display` (Fontshare): títulos, números de destaque. Caráter geométrico confiante.
- **Body** — `Hanken Grotesk` (Google): texto corrido, limpo e quente.
- **Data/Mono** — `JetBrains Mono` (Google): preços, %, hashes, rótulos técnicos (`tnum`).

Escala: `.display` (clamp 2.6–5.2rem), `h2`, `h3`, `.lead`, `.eyebrow` (mono, tracking largo, dourado).

## Componentes (classes utilitárias)
- **Botões**: `.btn` + `.btn--gold` (CTA), `.btn--ghost`, `.btn--danger`, `.btn--sm`.
- **Cards**: `.card`, `.card--glow` (fio dourado no topo), `.card__lbl` (rótulo mono).
- **Stat tile**: `.stat` + `.v` (valor grande); combine com `.mono` / `.grad-gold`.
- **Pills**: `.pill` + `.pill--up/--down/--gold`; `.dot` (ponto com glow).
- **Tabela**: `.tbl` (cabeçalho mono, hover de linha).
- **Controles**: `.field` + `.input` / `.select`; `.seg` (segmented control, opção `.on`).
- **Marca**: `partials/mark.html` (boomerang SVG, gradiente dourado, leve inclinação).

## Motion
- Entrada: `.reveal` + `.d1…d6` (stagger no load).
- Hero: boomerang gira em arco (`heroSpin`), trilha que "desenha" (`drawTrail`), órbitas (`spin`).
- Hover: botões/cards sobem (translateY) com glow dourado.
- Respeita `prefers-reduced-motion`.

## Estrutura de arquivos
```
boomerang/webapp/
  static/css/app.css      ← design system (tokens, componentes, motion)
  static/js/app.js        ← interações leves (nav scroll, idioma, stub connect)
  static/img/favicon.svg
  templates/
    base.html             ← shell (nav + idioma + rodapé)
    partials/mark.html     ← marca boomerang
    foundation.html        ← vitrine do design system (Fase 0)
    placeholder.html       ← páginas "em breve"
  i18n.py                 ← traduções EN/PT (server-side)
scripts/preview_web.py    ← preview local na porta 8090
```

## Como ver
`.venv\Scripts\python scripts\preview_web.py` → http://localhost:8090
(Preview separado; não interfere no agente da porta 8080.)
