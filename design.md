---
name: Proton Syntax
colors:
  surface: '#fbf8ff'
  surface-dim: '#dad9e3'
  surface-bright: '#fbf8ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f4f2fd'
  surface-container: '#eeedf7'
  surface-container-high: '#e8e7f1'
  surface-container-highest: '#e3e1ec'
  on-surface: '#1a1b22'
  on-surface-variant: '#3c4a42'
  inverse-surface: '#2f3038'
  inverse-on-surface: '#f1effa'
  outline: '#6c7a71'
  outline-variant: '#bbcabf'
  surface-tint: '#006c49'
  primary: '#006c49'
  on-primary: '#ffffff'
  primary-container: '#10b981'
  on-primary-container: '#00422b'
  inverse-primary: '#4edea3'
  secondary: '#565e74'
  on-secondary: '#ffffff'
  secondary-container: '#dae2fd'
  on-secondary-container: '#5c647a'
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
  secondary-fixed: '#dae2fd'
  secondary-fixed-dim: '#bec6e0'
  on-secondary-fixed: '#131b2e'
  on-secondary-fixed-variant: '#3f465c'
  tertiary-fixed: '#e1e0ff'
  tertiary-fixed-dim: '#c0c1ff'
  on-tertiary-fixed: '#07006c'
  on-tertiary-fixed-variant: '#2f2ebe'
  background: '#fbf8ff'
  on-background: '#1a1b22'
  surface-variant: '#e3e1ec'
typography:
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.01em
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
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.02em
  code-block:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 20px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  container-max: 1440px
  gutter: 1.5rem
  margin-mobile: 1rem
  stack-xs: 0.25rem
  stack-sm: 0.5rem
  stack-md: 1rem
  stack-lg: 2rem
---

## Brand & Style

The design system is engineered for technical precision and developer confidence. It targets engineers and infrastructure operators who require a high-density, low-friction interface for managing LLM traffic. 

The aesthetic is **Corporate / Modern** with a strong leaning toward **Minimalism** to ensure data density doesn't result in cognitive overload. It mimics the reliability of established infrastructure providers like Stripe or Vercel, utilizing a "Zinc" light mode as the foundation. High-fidelity details—such as subtle 1px borders, precise monospaced accents, and a constrained color palette—convey a sense of robust, production-grade tooling. The emotional response is one of control, speed, and analytical clarity.

## Colors

The palette is centered on a "Zinc" and "Slate" foundation to maintain a professional, neutral workspace. 

- **Primary (Emerald):** Reserved for "Success" states, positive metrics, and core action triggers (e.g., "Run Generate").
- **Secondary (Deep Navy):** Used for high-contrast elements such as sidebars, headers, and primary navigation to anchor the layout.
- **Tertiary (Indigo):** Utilized for secondary actions, interactive links, and informational badges.
- **Neutrals:** A scale of Zinc grays provides soft backgrounds for containers and subtle borders that define the grid without creating visual noise.
- **Code Background:** A specific deep slate (#0f172a) is used for code blocks to provide a "dark mode" syntax highlighting experience even within the light mode interface.

## Typography

This design system uses a dual-font strategy. **Inter** handles all UI chrome, headings, and body copy, providing exceptional legibility at small sizes. **JetBrains Mono** is utilized for all data-heavy segments, including labels, status pills, and JSON response blocks, reinforcing the developer-centric nature of the tool.

Hierarchy is established through weight and color rather than drastic size changes. Labels and metadata should use `label-sm` in a medium-gray tint, while primary headers use `headline-lg` in the secondary navy color.

## Layout & Spacing

The system employs a **Fixed Grid** philosophy for desktop screens, centering the dashboard content within a 1440px maximum width container. This ensures that data visualizations and tables remain readable without excessive horizontal scanning.

- **Desktop:** 12-column grid with 24px (1.5rem) gutters. Components should generally span 3, 4, 6, or 12 columns.
- **Spacing Rhythm:** An 8px base unit (0.5rem) governs all padding and margins. 
- **Density:** High density is encouraged. Information is grouped in cards with 16px internal padding. 
- **Reflow:** On mobile, the grid collapses to a single column with 16px side margins. Cards stack vertically with `stack-md` spacing.

## Elevation & Depth

Visual hierarchy is achieved primarily through **Tonal Layers** and **Low-Contrast Outlines**. 

- **Surface Tiers:** The main background is a very light gray (#fafafa). Main content cards sit on top in pure white (#ffffff).
- **Outlines:** All cards and input fields use a 1px solid border (#e4e4e7).
- **Shadows:** A single "Ambient Shadow" style is used for elevated cards: `0 1px 3px 0 rgba(0, 0, 0, 0.05), 0 1px 2px -1px rgba(0, 0, 0, 0.05)`. This creates a subtle lift without feeling heavy or skeuomorphic.
- **Code Depth:** Code blocks are inset, using a dark background to create a "well" effect, indicating a different functional context.

## Shapes

The shape language is "Soft" yet structured. 
- **Default (4px):** Used for input fields, small buttons, and inner card elements.
- **Rounded-LG (8px):** The standard for all primary dashboard cards and containers.
- **Pill (Full):** Exclusively reserved for status indicators, chips, and tags to differentiate them from actionable buttons.

## Components

### Buttons
- **Primary:** Solid Emerald (#10b981) with white text. No gradient. High-contrast hover state (slightly darker).
- **Secondary:** Solid Navy (#0f172a) for high-importance infrastructure actions.
- **Ghost:** Transparent background with Zinc-700 text; used for low-priority actions like "Clear Filters".

### Input Fields
- White background, Zinc-200 border, and `label-sm` titles positioned strictly above the field. Focused states should use a 1px Emerald ring.

### Cards
- White background, 8px corner radius, and the standard ambient shadow. Headers within cards should have a subtle bottom border if they contain sub-navigation or actions.

### Status Indicators (Pills)
- Small, uppercase `label-sm` text. 
- **Success:** Soft emerald background (10% opacity) with emerald text.
- **Error:** Soft red background with red text.
- **Neutral:** Soft zinc background with zinc text.

### Code Blocks
- Dark slate background, `code-block` typography, and 4px padding. Syntax highlighting should use a vibrant, high-contrast palette (Cyan for keys, Lime for strings, Orange for numbers).
