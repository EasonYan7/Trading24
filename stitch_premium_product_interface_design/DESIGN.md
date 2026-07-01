---
name: Trading24 Professional
colors:
  surface: '#fcf8f9'
  surface-dim: '#dcd9da'
  surface-bright: '#fcf8f9'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f6f3f4'
  surface-container: '#f0edee'
  surface-container-high: '#ebe7e8'
  surface-container-highest: '#e5e2e3'
  on-surface: '#1c1b1c'
  on-surface-variant: '#3c4a42'
  inverse-surface: '#313031'
  inverse-on-surface: '#f3f0f1'
  outline: '#6c7a71'
  outline-variant: '#bbcabf'
  surface-tint: '#006c49'
  primary: '#006c49'
  on-primary: '#ffffff'
  primary-container: '#10b981'
  on-primary-container: '#00422b'
  inverse-primary: '#4edea3'
  secondary: '#b61722'
  on-secondary: '#ffffff'
  secondary-container: '#da3437'
  on-secondary-container: '#fffbff'
  tertiary: '#494bd6'
  on-tertiary: '#ffffff'
  tertiary-container: '#9699ff'
  on-tertiary-container: '#1d17b2'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#6ffbbe'
  primary-fixed-dim: '#4edea3'
  on-primary-fixed: '#002113'
  on-primary-fixed-variant: '#005236'
  secondary-fixed: '#ffdad7'
  secondary-fixed-dim: '#ffb3ad'
  on-secondary-fixed: '#410004'
  on-secondary-fixed-variant: '#930013'
  tertiary-fixed: '#e1e0ff'
  tertiary-fixed-dim: '#c0c1ff'
  on-tertiary-fixed: '#07006c'
  on-tertiary-fixed-variant: '#2f2ebe'
  background: '#fcf8f9'
  on-background: '#1c1b1c'
  surface-variant: '#e5e2e3'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  headline-sm:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  data-mono:
    fontFamily: Geist Mono
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
    letterSpacing: -0.01em
  label-caps:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '700'
    lineHeight: 16px
    letterSpacing: 0.05em
  caption:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  base: 4px
  gap-xs: 4px
  gap-sm: 8px
  gap-md: 16px
  gap-lg: 24px
  container-padding: 32px
---

## Brand & Style

The brand identity for this design system is rooted in **Precision, Authority, and Analytical Depth**. It targets a high-stakes, professional user who requires institutional-grade clarity and high-density data visualization without the friction of decorative "AI fluff."

The design style is **"Bloomberg-meets-Modern-SaaS"**:
- **Modular (Bento) Architecture:** Content is organized into discrete, logical modules that facilitate rapid scanning and information hierarchy.
- **Data-First Minimalism:** Prioritizes technical utility over aesthetic ornamentation. Heavy use of whitespace and hairline borders replaces traditional shadows to maintain a "sharp" professional feel.
- **Tactile Professionalism:** High-contrast interactions and micro-refinements (such as 1px borders) evoke the feel of a high-end financial terminal.
- **Action-Oriented Narrative:** The UI should feel like a living document—streaming agent thoughts, live status indicators, and precision markers create a sense of active intelligence.

## Colors

The palette is strictly functional, utilizing high-contrast chromatic accents against a clean, light professional background to ensure maximum legibility during intense analytical sessions.

- **Backgrounds:** The interface utilizes a Light Mode foundation. The primary background is a clean, neutral surface (#F9FAFB). Cards use white surfaces to create subtle definition.
- **Accents (Semantic):**
    - **Emerald Green (#10B981):** Reserved exclusively for 'BUY' signals, growth indicators, and positive price delta.
    - **Crimson Red (#EF4444):** Reserved for 'SELL' signals, declines, and risk warnings.
    - **Slate Blue (#6366F1):** Used for system actions, navigational focus, and secondary data points.
- **Borders:** All container separation is handled by 1px hairline borders (#E5E7EB). Avoid high-contrast black borders; keep the structure architectural and subtle.

## Typography

The typography system prioritizes legibility and data density. 

- **Inter:** Used for all prose, headers, and UI controls. It provides a neutral, professional tone that doesn't distract from the data.
- **Geist Mono:** Used for all numerical data, stock tickers, timestamps, and terminal output. This ensures that columns of numbers align perfectly (tabular figures) for easier comparison.
- **Hierarchy:** Use `label-caps` for section headers in the bento grid to maintain a "Terminal" aesthetic. 
- **Scale:** Keep mobile headlines constrained to 32px or less to ensure stock tickers don't wrap awkwardly.

## Layout & Spacing

This design system uses a **Modular Bento Grid** layout. Components are treated as distinct "tiles" within a fixed-width dashboard or a fluid multi-column grid.

- **The 4px Rule:** All spacing units are multiples of 4px. This creates a tight, precise layout characteristic of financial software.
- **Grid System:**
    - **Desktop:** 12-column grid with a 16px gutter. Use "Bento Box" groupings where related agents share a parent container.
    - **Tablet:** 6-column grid; containers reflow vertically.
    - **Mobile:** 1-column stack. Sparklines should maintain a fixed height to preserve chart readability.
- **Density:** High information density is preferred. Use `gap-sm` for internal component elements and `gap-lg` for separating major dashboard sections.

## Elevation & Depth

Depth is conveyed through **Subtle Tonal Layering** rather than traditional drop shadows.

- **Base Layer:** The primary light background (#F9FAFB).
- **Surface Layer:** Card containers use white backgrounds with a 1px border (#E5E7EB).
- **Interactive Overlays:** Use light-themed glassmorphism for tooltips, dropdowns, and modal windows. Apply a `backdrop-filter: blur(12px)` with a semi-transparent white fill (80% opacity).
- **Z-Index Strategy:** Only modals and floating precision markers (on charts) should feel "detached" from the grid. All other elements should appear integrated into the modular surface.

## Shapes

The shape language is **Technical and Rigid**. 

- **Corners:** Use a consistent 4px radius for small components (buttons, tags) and an 8px radius for larger containers (bento cards, terminal panels). 
- **Buttons:** Sharp corners convey authority. Avoid fully rounded pill shapes unless used for specific "Active" status indicators.
- **Charts:** Line charts should use straight-line segments or very minimal smoothing to emphasize data accuracy over aesthetic "curves."

## Components

- **Status Indicators:** Use a "pulsing" dot for `Active Running` (Slate Blue) and a solid, dim dot for `Idle`.
- **Buttons:** Primary buttons use a ghost style with 1px borders or a solid Slate Blue fill. 'BUY' and 'SELL' actions are high-contrast solid blocks of Emerald and Crimson respectively.
- **Agent Terminal Cards:** Background should be slightly off-white. Text streams in Geist Mono. Use a vertical "progress line" on the left to indicate which agent is currently active.
- **Data Tables:** No vertical borders. Use subtle horizontal dividers. Hover states should highlight the entire row in a faint Slate Blue tint.
- **Sparklines:** Compact, monochromatic (or semantic Green/Red) charts without axes, embedded within cards to show 24h or 7d trends.
- **Interactive Charts:** Precision markers (crosshairs) should trigger a light glassmorphic tooltip showing the exact price and AI decision at that specific timestamp.
- **Input Fields:** Minimalist design with only a bottom border that illuminates Slate Blue on focus.