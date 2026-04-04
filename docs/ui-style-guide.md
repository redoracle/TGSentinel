# TG Sentinel UI Style Guide

Codified reference to reproduce the current UI look-and-feel across other apps. Built on Bootstrap 5.3, Bootstrap Icons, and Google Fonts (Inter + JetBrains Mono). Default theme is dark with glassmorphism, gradients, and subtle motion. CSS is modular via `ui/static/css/style.css`, which imports the module stack in order: `core.css`, `navbar.css`, `layout-forms.css`, `data-activity.css`, `alerts-profiles.css`, `overlays-errors.css`, `history-filters.css`, `extraction-flows.css`, `console-docs.css`, `docs.css`, `analytics-health.css`, `message-formats.css`, `feedback-learning.css`.

## Theme Tokens (core.css)
- Backgrounds: `--bg-primary #0f0f23`, `--bg-secondary #1a1a2e`, `--bg-card #16213e`, `--bg-elevated #1f2937`.
- Text: `--text-primary #fff`, `--text-secondary #a0aec0`, `--text-muted #718096`.
- Accents: `--primary-color #667eea` (dark `#5568d3`), `--secondary-color #764ba2`, `--success-color #00f2fe`, `--danger-color #f5576c`, `--warning-color #c27e17`, `--info-color #4facfe`; paired opacities (`--primary-color-01/02/04/06`, `--danger-color-12/35/75/80`, `--focus-ring-color`).
- Gradients: `--primary-gradient`, `--secondary-gradient`, `--success-gradient`, `--dark-gradient`.
- Layout: `--navbar-height 70px`, `--border-color rgba(102,126,234,0.2)`, shadows (`--shadow-sm/md/lg/glow`), radii (`--radius-sm 8px`, `--radius-md 12px`, `--radius-lg 16px`, `--radius-xl 24px`), transitions (`--transition-fast`, `--transition-smooth`).
- Code: `--code-bg #0d1117`, `--code-border #30363d`, `--code-text #cfe2ff`.

## Global Styling
- Fonts: Inter for UI, JetBrains Mono for code. Base font-size 16px; headings bold with tight letter-spacing.
- Body: dark background with animated radial gradients (`body::before`), line-height 1.6, overflow-x hidden.
- Effects: glassmorphism (`glass-effect`), glowing shadows, gradient text for stat numbers, ripple on buttons; prefers-reduced-motion guards.
- Utilities: `min-w-0`, `word-break-all`, `link-underline-pointer`, `lock-overlay`, `collapse-icon` rotation helper, sticky navbar, sticky docs nav, styled scrollbars.

## Layout & Containers
- Primary wrapper: `main.container-fluid` max-width 1400–1600px, padding 1.5–2.5rem.
- Navbar: sticky, blurred, two-row structure (brand/auth row + user/menu row). Mobile: collapses at 991px, logo shrinks, auth buttons move into `.ts-mobile-auth-actions`.
- Cards: dark panels with border and hover lift; headers tinted (`card-header`); stat cards (`.stat-card`) use gradient top bar and gradient text.

## Core Components
- **Buttons**: uppercase, bold, ripple highlight. `btn-primary` uses `--primary-gradient`; `btn-outline-primary` fills to gradient on hover; `btn-reset` is danger tinted; `btn-ghost` for subtle lock actions.
- **Forms**: dark `form-control/form-select`, blue focus ring, muted placeholders; enlarged checkboxes/radios; labels in `--text-secondary`.
- **Tables**: transparent dark tables with sticky headers, padded cells, hover tint; small text forced white; headers uppercase.
- **Badges/Chips**: uppercase, rounded; tag chips via `.tag-badge-stacked`; status badges (`status-online/offline/processing`) with pulse glow.
- **Alerts**: left-accented, translucent backgrounds for success/danger/warning/info.
- **Modals**: glass/gradiated content, tinted headers/footers; blurred backdrops enhanced via JS (`applyGlassBackdrop` in `base.html`).
- **Toasts/Overlays**: top-right toasts, loading overlays, lock overlays.
- **Progress**: gradient bars with shine animation; resource bars show inline text.
- **Toggles**: custom live toggle (`.live-toggle-switch`) with sliding knob; list switches style `form-check-input`.

## Page Patterns
- **Dashboard** (`ui/templates/dashboard.html`): stat cards row; collapsible System Health card with score/indicators; Live Activity list with filter + live toggle. Activity items show avatars (image + initials), metadata badges (replies/reactions/media/pinned/forwarded), tags, and hover motion.
- **Alerts/Profiles**: scrollable side lists with hover lift, active bar, reveal-on-hover actions; metadata chips and activity dots; detail/editor cards on the right.
- **History/Extraction** (`history-filters.css`, `extraction-flows.css`, `console-docs.css`): filter grids inside cards, action button group, sticky table headers, validation feedback banners, select-all bars, extraction logs console.
- **Analytics** (`analytics-health.css`): health score gradient card, container/API status grids, metric gauges, endpoint status badges, Prometheus metric tiles.
- **Docs/Developer** (`console-docs.css`, `docs.css`, `message-formats.css`): sticky side nav, API method cards colored by HTTP method, code/JSON blocks on dark panels, Monaco editor containers with fixed heights, preview panels and variable legends.

## Motion & Accessibility
- Hover lifts on cards and list items; translate on activity items; gradient/ripple on buttons; animated background.
- Pulse glows for online status and activity dots; prefers-reduced-motion fallbacks disable animation.
- Sticky nav, sticky docs sidebar; collapsible headers with rotating chevrons for clarity.
- Forms and controls meet focus-visible outlines; helper text muted for hierarchy.

## Responsive Rules
- Breakpoint at 991px: navbar collapses, mobile auth buttons appear, logo shrinks.
- <768px: tighter gutters, smaller stat typography, single-column grids for health/cards/blockchain lists.
- Lists/tables remain scrollable with slim scrollbars; action buttons stack where needed.

## Assets & Imports
- Include in `<head>` (see `ui/templates/base.html`): Google Fonts (Inter, JetBrains Mono), Bootstrap 5.3 CSS/JS, Bootstrap Icons, optional Chart.js and Socket.IO. Then load `static/css/style.css` to pull all modules.
- Images: logo at `ui/static/images/logo.png` (glowing treatment in navbar and modals).

## Implementation Steps (for other apps)
1) Add head imports (fonts, Bootstrap, Icons, optional Chart.js/Socket.IO) and reference a single stylesheet that mirrors `style.css` import order.  
2) Copy `:root` tokens from `core.css` to preserve colors, radii, shadows, transitions.  
3) Structure layout like `base.html`: sticky glass navbar (brand + status + menus), main container, toasts container, modals with `glass-backdrop`.  
4) Use component classes above for cards, forms, tables, badges, toasts, overlays, toggles, activity items, list items.  
5) Keep motion helpers and reduced-motion guards; retain sticky headers/sidebars for docs and tables.  
6) Reuse utilities (`glass-effect`, `min-w-0`, `word-break-all`, `collapse-icon`, `tag-badge-stacked`, `live-toggle-switch`) to match behaviors.  
7) Maintain responsive tweaks at 991px/768px/576px to mirror layout shifts.
