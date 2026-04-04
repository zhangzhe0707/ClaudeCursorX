# Frontend Design — 前端设计美学技能

> 移植自 Claude Code 官方 frontend-design 插件

Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks to build web components, pages, or applications. Generates creative, polished code that avoids generic AI aesthetics.

本技能指导创建独特的、生产级的前端界面，避免千篇一律的"AI 味"设计。所有实现都要有真实可运行的代码，注重美学细节和创意选择。

## Design Thinking / 设计思考

Before coding, understand the context and commit to a **BOLD** aesthetic direction:

1. **Purpose / 目的**: What problem does this interface solve? Who uses it?
2. **Tone / 调性**: Pick a strong direction — brutally minimal, maximalist chaos, retro-futuristic, organic/natural, luxury/refined, playful/toy-like, editorial/magazine, brutalist/raw, art deco/geometric, soft/pastel, industrial/utilitarian...
3. **Constraints / 约束**: Technical requirements (framework, performance, accessibility).
4. **Differentiation / 差异化**: What makes this UNFORGETTABLE? What's the one thing someone will remember?

**CRITICAL**: Choose a clear conceptual direction and execute it with precision. Bold maximalism and refined minimalism both work — the key is intentionality, not intensity.

## Aesthetics Guidelines / 美学指南

### Typography / 字体

Choose fonts that are **beautiful, unique, and interesting**. Avoid generic fonts (Arial, Inter, Roboto, system fonts). Pair a distinctive display font with a refined body font. Each project should use different fonts.

### Color & Theme / 色彩与主题

Commit to a cohesive aesthetic. Use CSS variables for consistency. Dominant colors with sharp accents outperform timid, evenly-distributed palettes.

### Motion / 动效

Use animations for effects and micro-interactions. Prioritize CSS-only solutions for HTML. Use Motion library for React when available. Focus on high-impact moments: one well-orchestrated page load with staggered reveals creates more delight than scattered micro-interactions.

### Spatial Composition / 空间构图

Unexpected layouts. Asymmetry. Overlap. Diagonal flow. Grid-breaking elements. Generous negative space OR controlled density.

### Backgrounds & Visual Details / 背景与视觉细节

Create atmosphere and depth rather than defaulting to solid colors. Apply creative forms like gradient meshes, noise textures, geometric patterns, layered transparencies, dramatic shadows, decorative borders, custom cursors, and grain overlays.

## Anti-Patterns / 反面模式

**NEVER** use generic AI-generated aesthetics:
- Overused font families (Inter, Roboto, Arial, system fonts)
- Cliched color schemes (purple gradients on white backgrounds)
- Predictable layouts and component patterns
- Cookie-cutter design that lacks context-specific character

Vary between light and dark themes, different fonts, different aesthetics. NEVER converge on common choices across generations.

## Implementation / 实现原则

Match implementation complexity to the aesthetic vision:
- **Maximalist designs** need elaborate code with extensive animations and effects
- **Minimalist designs** need restraint, precision, and careful attention to spacing, typography, and subtle details
- Elegance comes from executing the vision well
