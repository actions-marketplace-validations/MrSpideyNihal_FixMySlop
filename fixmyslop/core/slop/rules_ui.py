"""
UI slop rules — the visual and copywriting tells of AI-generated frontends,
plus the accessibility bugs AI agents reliably forget.
"""
import re
from typing import Iterable

from fixmyslop.core.slop.rule import (
    FileContext, Hit, rule, line_rule, iter_tags,
    KIND_MARKUP, KIND_STYLE, KIND_SCRIPT,
)
from fixmyslop.core.slop.theme import colors_in, describe
from fixmyslop.macros import (
    SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW,
    CATEGORY_UI_SLOP, CATEGORY_A11Y,
)

UI = (KIND_MARKUP, KIND_STYLE)

# ─── Visual tells ────────────────────────────────────────────────────────────

_PURPLE_FAMILY = r"(?:purple|violet|indigo|fuchsia)"
_TW_GRADIENT_STOP = re.compile(r"\b(?:from|via|to)-(\w+)-\d{2,3}\b")
_TW_AI_FAMILIES = {"purple", "violet", "indigo", "fuchsia"}
_TW_PARTNER_FAMILIES = {"pink", "purple", "violet", "indigo", "blue", "fuchsia", "cyan", "rose", "sky"}
_CSS_GRADIENT = re.compile(r"(?:linear|radial|conic)-gradient\(", re.IGNORECASE)


def _theme_vars(ctx: FileContext) -> dict:
    """CSS variables from the project's theme (empty when unavailable)."""
    theme = getattr(getattr(ctx, "project", None), "theme", None)
    return theme.variables if theme is not None else {}


@rule(
    "ui/ai-gradient", "Purple/violet \"AI gradient\"", SEVERITY_MEDIUM, CATEGORY_UI_SLOP, UI,
    why="Violet/purple gradients and glows (from-purple-600 to-pink-500, #667eea→#764ba2, "
        "oklch(… 292) radial glows) are the most recognisable tell of an AI-generated UI. "
        "They make the product look like every other template.",
    fix="Replace with a solid surface from the theme (background/card/muted) and put the brand color on "
        "ONE element (the primary button). If depth is needed, use a subtle same-hue tint or a border — "
        "not a multi-hue gradient or glow.",
    skip_tests=True,
)
def check_ai_gradient(ctx: FileContext) -> Iterable[Hit]:
    """Flag gradients whose colors fall in the violet/purple/magenta band (any color syntax)."""
    variables = _theme_vars(ctx)
    for n, line in enumerate(ctx.lines, 1):
        if ctx.is_comment(n):
            continue
        stops = set(_TW_GRADIENT_STOP.findall(line))
        if stops & _TW_AI_FAMILIES and len(stops & _TW_PARTNER_FAMILIES) >= 2:
            yield Hit(n, line.strip(), "Tailwind " + " → ".join(sorted(stops)))
            continue
        if _CSS_GRADIENT.search(line):
            colors = colors_in(line[line.lower().find("gradient("):], variables)
            purple = [c for c in colors if c.is_ai_purple]
            if purple:
                words = sorted({describe(c) for c in colors if not c.is_neutral})
                yield Hit(n, line.strip(), "gradient of " + " + ".join(words))


_RADIAL_GLOW = re.compile(r"radial-gradient\((?:[^()]|\([^()]*\))*\btransparent\b(?:[^()]|\([^()]*\))*\)", re.I)
_GLOW = re.compile(
    r"\b(?:shadow|drop-shadow)-\[([^\]]+)\]"
    r"|\bshadow-(?:purple|violet|indigo|fuchsia|pink|blue|cyan|sky)-\d{3}(?:/\d+)?"
    r"|box-shadow\s*:\s*([^;]+)|boxShadow\s*:\s*['\"`]([^'\"`]+)",
)


@rule(
    "ui/neon-glow", "Colored neon glow shadows", SEVERITY_LOW, CATEGORY_UI_SLOP, UI,
    why="Big colored glows and glowing orbs (shadow-[0_0_40px_rgba(…)], radial-gradient(color, transparent)) "
        "are the dark-mode AI template look in any hue — mint, cyan or violet. They blur edges and compete "
        "with content.",
    fix="Use neutral elevation from the theme (shadow-sm/shadow-md or a 1px border). Reserve color for "
        "the one primary action.",
    skip_tests=True, project_threshold=2, aggregate_per_file=True,
)
def check_neon_glow(ctx: FileContext) -> Iterable[Hit]:
    """Flag colored (non-neutral) blurred shadows and radial glow orbs."""
    variables = _theme_vars(ctx)
    for n, line in enumerate(ctx.lines, 1):
        if ctx.is_comment(n):
            continue
        glow = _RADIAL_GLOW.search(line)
        if glow:
            colored = [c for c in colors_in(glow.group(0), variables) if not c.is_neutral and c.sat > 0.35]
            if colored and not any(c.is_ai_purple for c in colored):   # purple is reported by ui/ai-gradient
                yield Hit(n, line.strip(), f"{describe(colored[0])} radial glow")
                continue
        for match in _GLOW.finditer(line):
            body = next((g for g in match.groups() if g), None)
            if body is None:  # shadow-purple-500/50 style
                yield Hit(n, line.strip(), match.group(0))
                break
            blur = [float(x) for x in re.findall(r"(?:^|[\s_,])(\d{2,3})px", body)]
            colored = [c for c in colors_in(body, variables) if not c.is_neutral and c.sat > 0.35]
            if colored and blur and max(blur) >= 16:
                yield Hit(n, line.strip(), match.group(0)[:60])
                break


_GRADIENT_TEXT = re.compile(r"\bbg-clip-text\b|background-clip:\s*text|backgroundClip:\s*['\"]text", re.IGNORECASE)


@rule(
    "ui/gradient-text", "Gradient-filled text", SEVERITY_MEDIUM, CATEGORY_UI_SLOP, UI,
    why="Gradient text (bg-clip-text + text-transparent) on headlines is a signature AI landing-page "
        "move. It also hurts contrast and readability.",
    fix="Use a solid text color from the theme. Emphasise with weight, size or a single accent color "
        "on one word instead of a gradient.",
    skip_tests=True,
)
def check_gradient_text(ctx: FileContext) -> Iterable[Hit]:
    """Flag gradient-clipped text."""
    for n, line in enumerate(ctx.lines, 1):
        if not ctx.is_comment(n) and _GRADIENT_TEXT.search(line):
            yield Hit(n, line.strip())


EMOJI_RE = re.compile(
    "[\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF\u2600-\u26FF\u2705\u274C\u2728\u2B50"
    "\u2726\u2727\u273F\u2740\u2302\u2315\u2316]"   # incl. ✦ ✧ ✿ ❀ ⌂ ⌕ ⌖ used as icons; not ⌘ ⌥ ⏎
)


@rule(
    "ui/emoji-icons", "Emoji used as UI icons", SEVERITY_MEDIUM, CATEGORY_UI_SLOP, (KIND_MARKUP,),
    why="Emoji as feature icons, button labels or heading decorations (🚀 ⚡ ✨ 🔒) render "
        "differently on every OS, can't be themed, and scream 'generated'.",
    fix="Replace with icons from the icon library the project already uses (lucide-react, heroicons, "
        "@tabler/icons, etc.) sized and colored via the theme — or simply remove them.",
    skip_tests=True, project_threshold=3,
)
def check_emoji_icons(ctx: FileContext) -> Iterable[Hit]:
    """Flag emoji in rendered markup (not comments)."""
    for n, line in enumerate(ctx.lines, 1):
        if ctx.is_comment(n):
            continue
        if re.search(r"\b(console|print|logger|log)\s*[.(]", line):
            continue  # handled by slop/emoji-log
        match = EMOJI_RE.search(line)
        if match:
            yield Hit(n, line.strip(), match.group(0))


line_rule(
    "ui/glassmorphism", "Glassmorphism card stack", SEVERITY_LOW, CATEGORY_UI_SLOP, UI,
    why="Frosted-glass panels (backdrop-blur over translucent white) everywhere is a default AI "
        "aesthetic; it costs GPU and usually lowers contrast.",
    fix="Use opaque surfaces from the theme for cards and panels. Keep blur for at most one overlay "
        "(e.g. a sticky header) if the design calls for it.",
    pattern=r"^(?![^\n]*\bfixed\b[^\n]*\binset-0\b)[^\n]*(?:backdrop-blur[\w-]*[^\n]*\b(?:bg|border)-(?:white|black|slate-\d+|gray-\d+)/\d+"
            r"|\b(?:bg|border)-(?:white|black)/\d+[^\n]*backdrop-blur|backdrop-filter:\s*blur)",
    aggregate_per_file=True, skip_tests=True, project_threshold=3,
)

line_rule(
    "ui/blur-blobs", "Decorative blurred gradient blobs", SEVERITY_LOW, CATEGORY_UI_SLOP, UI,
    why="Absolutely-positioned, heavily blurred colored circles behind the hero are pure decoration "
        "copied from AI templates.",
    fix="Delete the decorative blob elements. If the section needs depth, use spacing, a border, or a "
        "single subtle background tint.",
    pattern=r"^(?=[^\n]*\bblur-(?:2xl|3xl|\[\d{2,}px\]))(?=[^\n]*\b(?:rounded-full|absolute)\b)"
            r"|filter:\s*blur\(\s*[4-9]\d+px",
    skip_tests=True,
)

line_rule(
    "ui/hover-scale", "Everything scales on hover", SEVERITY_LOW, CATEGORY_UI_SLOP, UI,
    why="hover:scale-105 on every card and button is a reflexive AI flourish; motion stops "
        "meaning anything when everything moves.",
    fix="Remove the scale transforms. Use a subtle color/border/shadow change for hover, and reserve "
        "motion for elements where it communicates something.",
    pattern=r"\bhover:scale-1[01]\d\b|whileHover=\{\{\s*scale",
    aggregate_per_file=True, skip_tests=True, project_threshold=4,
)

line_rule(
    "ui/gimmick-animation", "Attention-seeking animation", SEVERITY_LOW, CATEGORY_UI_SLOP, UI,
    why="Bouncing arrows, pinging and forever-pulsing status dots on static content are distracting filler.",
    fix="Remove animate-bounce / animate-ping from decorative elements. Respect prefers-reduced-motion "
        "for any animation you keep.",
    pattern=r"\banimate-(?:bounce|ping)\b"
            r"|\banimation\s*:[^;]*\b(?:pulse|glow|breathe|float|shimmer|blink)\b[^;]*\binfinite\b"
            r"|\banimation\s*:[^;]*\binfinite\b[^;]*\b(?:pulse|glow|breathe|float|shimmer|blink)\b"
            r"|^(?=[^\n]*\banimate-pulse\b)(?=[^\n]*\brounded-full\b)(?=[^\n]*\b(?:h|size)-(?:1|1\.5|2)\b)",
    skip_tests=True,
)

line_rule(
    "ui/heavy-shadows", "Heavy drop shadows everywhere", SEVERITY_LOW, CATEGORY_UI_SLOP, UI,
    why="shadow-xl/2xl on every card flattens the visual hierarchy — nothing stands out when "
        "everything floats.",
    fix="Use one elevation scale from the theme. Most cards need a 1px border or shadow-sm; reserve "
        "large shadows for overlays (dialogs, popovers).",
    pattern=r"^(?![^\n]*\bfixed\b)[^\n]*\bshadow-(?:xl|2xl)\b",
    aggregate_per_file=True, skip_tests=True, project_threshold=8,
)

line_rule(
    "ui/default-font", "\"Inter everywhere\" typography", SEVERITY_LOW, CATEGORY_UI_SLOP, UI,
    why="Inter as the only typeface is the default of every AI scaffold, so the product has no "
        "typographic identity.",
    fix="Choose a deliberate type pairing (e.g. a distinctive display face for headings + a readable "
        "text face) and define it once as theme tokens.",
    pattern=r"font-family:[^;\n]*\bInter\b|\bInter\(\s*\{|fontFamily[^\n]*['\"]Inter['\"]"
            r"|fonts\.googleapis\.com/css2?\?family=Inter\b|from\s+['\"]@fontsource/inter",
    skip_tests=True, once_per_project=True,
)

line_rule(
    "ui/arbitrary-color", "Hardcoded hex colors in classes", SEVERITY_LOW, CATEGORY_UI_SLOP, (KIND_MARKUP,),
    why="bg-[#1a1a2e]-style one-off colors bypass the theme, so the palette drifts with every "
        "generated component.",
    fix="Map each hex to an existing theme token (or add ONE token to tailwind config / CSS variables) "
        "and use the token class instead.",
    pattern=r"(?<![\w-])(?:bg|text|border|from|to|via|fill|stroke|ring|outline|shadow|decoration)"
            r"-\[#[0-9a-fA-F]{3,8}\]",
    aggregate_per_file=True, skip_tests=True, project_threshold=5,
)

line_rule(
    "ui/z-index-war", "z-index arms race", SEVERITY_LOW, CATEGORY_UI_SLOP, UI,
    why="z-index: 9999 is a symptom of stacking-context bugs being papered over.",
    fix="Define a small z-index scale (e.g. base/dropdown/sticky/overlay/modal/toast) and use it.",
    pattern=r"z-index:\s*\d{4,}|(?<![\w-])z-\[\d{4,}\]|zIndex:\s*\d{4,}",
    skip_tests=True,
)

line_rule(
    "ui/important-abuse", "!important overrides", SEVERITY_LOW, CATEGORY_UI_SLOP, (KIND_STYLE,),
    why="Piles of !important mean styles are fighting each other instead of being structured.",
    fix="Remove !important and fix specificity/order at the source.",
    pattern=r"!important\b",
    aggregate_per_file=True, skip_tests=True, project_threshold=8,
)

# ─── Copy tells ──────────────────────────────────────────────────────────────

line_rule(
    "ui/marketing-slop", "Generic AI marketing copy", SEVERITY_MEDIUM, CATEGORY_UI_SLOP, (KIND_MARKUP,),
    why="Phrases like 'Revolutionize your workflow' or 'Unlock the power of' say nothing; readers "
        "now instantly recognise them as machine-written.",
    fix="Rewrite in plain, specific language: what the product does, for whom, with concrete nouns "
        "and numbers that are real. If you don't know the specifics, ask the user instead of inventing them.",
    pattern=r"\b(?:revolutioni[sz](?:e|es|ing)|unleash|unlock (?:the|your) (?:full )?(?:power|potential)"
            r"|supercharg(?:e|es|ing)|elevate your|seamless(?:ly)?|next[- ]gen(?:eration)?"
            r"|cutting[- ]edge|state[- ]of[- ]the[- ]art|game[- ]chang(?:er|ing)"
            r"|empower(?:s|ing)? (?:you|your|teams|developers|businesses|creators)"
            r"|effortless(?:ly)?|harness the power|to the next level|lightning[- ]fast"
            r"|blazing(?:ly)?[- ]fast|world[- ]class|best[- ]in[- ]class"
            r"|all[- ]in[- ]one (?:platform|solution|workspace)|transform (?:the way|how) you"
            r"|say goodbye to|reimagin(?:e|ed|ing)|unparalleled|skyrocket"
            r"|built for the (?:future|modern)|in today'?s (?:fast[- ]paced|digital))\b",
    skip_tests=True,
)

line_rule(
    "ui/fake-social-proof", "Invented stats or testimonials", SEVERITY_MEDIUM, CATEGORY_UI_SLOP, (KIND_MARKUP,),
    why="'10,000+ happy users', '99.9% uptime', five-star reviews from 'Sarah Johnson' — AI invents "
        "these. Shipping fabricated social proof is misleading and often legally risky.",
    fix="Remove invented numbers, ratings and testimonials. Only show metrics and quotes the user "
        "has provided; otherwise drop the section.",
    pattern=r"\b(?:\d{3,}|\d{1,3}(?:[,.]\d{3})+|\d+(?:\.\d)?[kKmM])\+\s*(?:happy |active |satisfied |trusted )?"
            r"(?:users|customers|developers|companies|teams|clients|downloads|businesses|creators|travell?ers"
            r"|guests|members|readers|students|learners|people|families|hosts|makers|shoppers|subscribers"
            r"|founders|designers|freelancers|patients|investors|merchants|sellers|buyers|writers|marketers)\b"
            r"|\b99\.9+%|\b(?:john|jane) (?:doe|smith)\b|\bsarah (?:johnson|chen|williams|miller)\b"
            r"|\bacme (?:inc|corp|co)\b|★★★★★|\b4\.9\s*/\s*5\b|\btrusted by (?:thousands|millions|over)\b",
    unless_inside=r"placeholder\s*=\s*(?:\"[^\"]*\"|'[^']*'|\{[^}]*\})",
    skip_tests=True,
)

line_rule(
    "ui/placeholder-copy", "Placeholder copy shipped to users", SEVERITY_HIGH, CATEGORY_UI_SLOP, (KIND_MARKUP,),
    why="Lorem ipsum and 'Your Company Name' left in production UI.",
    fix="Replace with real copy from the user/product context, or remove the element.",
    pattern=r"\blorem ipsum\b|\bdolor sit amet\b|\byour (?:company|brand|product|app|startup) name\b"
            r"|\bcompany name here\b|\byour logo here\b|\byour tagline\b",
    skip_tests=True,
)

# ─── Palette sprawl (project-level, see engine) ──────────────────────────────

TW_COLOR_FAMILY = re.compile(
    r"(?<![\w-])(?:bg|text|border|from|via|to|ring|fill|stroke|outline|decoration|divide|accent|caret)"
    r"-(red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)"
    r"-(?:50|[1-9]00|950)\b"
)
HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")

# ─── Accessibility ───────────────────────────────────────────────────────────


@rule(
    "a11y/img-alt", "Image without alt text", SEVERITY_MEDIUM, CATEGORY_A11Y, (KIND_MARKUP,),
    why="Screen readers can't describe images without alt text; it's also an SEO and WCAG failure.",
    fix="Add a meaningful alt describing the image's purpose, or alt=\"\" if it is purely decorative.",
    skip_tests=True,
)
def check_img_alt(ctx: FileContext) -> Iterable[Hit]:
    """Flag <img>/<Image> without an alt attribute."""
    for _name, attrs, start, _ in iter_tags(ctx, ("img", "Image")):
        if re.search(r"(?:^|\s|:)alt\s*=|\{\s*\.\.\.", attrs):
            continue
        line = ctx.line_of(start)
        yield Hit(line, ctx.line_text(line).strip())


@rule(
    "a11y/clickable-div", "Clickable div/span", SEVERITY_MEDIUM, CATEGORY_A11Y, (KIND_MARKUP,),
    why="A div with onClick can't be focused or activated by keyboard and isn't announced as a "
        "control. AI generates these constantly.",
    fix="Use a <button type=\"button\"> (or <a href> for navigation). If a div is unavoidable, add "
        "role=\"button\", tabIndex={0} and Enter/Space key handling.",
    skip_tests=True,
)
def check_clickable_div(ctx: FileContext) -> Iterable[Hit]:
    """Flag div/span click handlers without a role."""
    for name, attrs, start, _ in iter_tags(ctx, ("div", "span", "li", "td")):
        if not re.search(r"(?:\bonClick|@click|v-on:click|on:click|\bonclick)\s*=", attrs):
            continue
        if re.search(r"\brole\s*=", attrs):
            continue
        if re.search(r"(?:\bonClick|@click)\s*=\s*\{?\s*\(?\s*\w*\s*\)?\s*=>\s*\w+\.stopPropagation\(\)\s*\}?", attrs):
            continue  # only stops propagation (dialog body inside a backdrop)
        if re.search(r"\bfixed\b", attrs) and re.search(r"\binset-0\b", attrs):
            continue  # click-outside-to-close backdrop; keyboard users close with Escape
        line = ctx.line_of(start)
        yield Hit(line, ctx.line_text(line).strip(), f"<{name}> with a click handler")


@rule(
    "a11y/icon-button", "Icon-only button without a label", SEVERITY_MEDIUM, CATEGORY_A11Y, (KIND_MARKUP,),
    why="A button containing only an icon is announced as just 'button' — users of assistive tech "
        "can't tell what it does.",
    fix="Add aria-label=\"<action>\" to the button (or visually-hidden text).",
    skip_tests=True,
)
def check_icon_button(ctx: FileContext) -> Iterable[Hit]:
    """Flag <button> elements whose only content is markup (icons)."""
    for name, attrs, start, end in iter_tags(ctx, ("button", "Button")):
        if attrs.rstrip().endswith("/"):
            continue
        if re.search(r"\b(?:aria-label|aria-labelledby|title|sr-only|asChild)\b|\{\s*\.\.\.", attrs):
            continue
        close = ctx.text.find(f"</{name}>", end)
        if close == -1 or close - end > 1500:
            continue
        inner = ctx.text[end + 1:close]
        if "sr-only" in inner or "visually-hidden" in inner:
            continue
        inner = re.sub(r"\{/\*.*?\*/\}", "", inner, flags=re.DOTALL)
        if "<" not in inner:
            continue
        stripped = _strip_tags(inner)
        if "{" in stripped or re.search(r"[^\W_]", stripped.replace("&nbsp;", "")):  # any letter/digit, any script
            continue
        line = ctx.line_of(start)
        yield Hit(line, ctx.line_text(line).strip())


def _strip_tags(markup: str) -> str:
    """Remove all tags (JSX-aware) from a markup fragment."""
    from fixmyslop.core.slop.rule import find_tag_end
    out = []
    i = 0
    while i < len(markup):
        if markup[i] == "<":
            end = find_tag_end(markup, i)
            if end == -1:
                break
            i = end + 1
            continue
        out.append(markup[i])
        i += 1
    return "".join(out).strip()


@rule(
    "a11y/dead-link", "Link that goes nowhere", SEVERITY_LOW, CATEGORY_A11Y, (KIND_MARKUP,),
    why="href=\"#\" and javascript:void(0) links are placeholders that break navigation, history and "
        "keyboard users.",
    fix="Point the link at a real route, or use a <button> if it performs an action.",
    skip_tests=True,
)
def check_dead_link(ctx: FileContext) -> Iterable[Hit]:
    """Flag anchors with placeholder hrefs."""
    for _name, attrs, start, _ in iter_tags(ctx, ("a", "Link")):
        if re.search(r"\bhref\s*=\s*(?:\{\s*)?[\"'](?:#|javascript:[^\"']*|)[\"']", attrs):
            line = ctx.line_of(start)
            yield Hit(line, ctx.line_text(line).strip())


@rule(
    "a11y/focus-removed", "Focus outline removed", SEVERITY_MEDIUM, CATEGORY_A11Y, UI,
    why="outline: none / outline-none hides where keyboard focus is. AI adds it to make buttons "
        "'look clean' and forgets a visible focus style.",
    fix="Keep outline-none only together with a visible focus style, e.g. focus-visible:ring-2 "
        "focus-visible:ring-offset-2 (or a :focus-visible rule in CSS).",
    skip_tests=True,
)
def check_focus_removed(ctx: FileContext) -> Iterable[Hit]:
    """Flag outline removal that isn't paired with a focus style in the same class string."""
    file_has_focus_visible = "focus-visible" in ctx.text or ":focus" in ctx.text
    focus_style = re.compile(r"\b(?:focus|focus-visible|focus-within|group-focus|peer-focus|aria-selected"
                             r"|data-\[(?:selected|highlighted|focus)[^\]]*\]|data-highlighted)[\w-]*:"
                             r"(?:ring|outline|border|shadow|bg|underline|text)")
    for n, line in enumerate(ctx.lines, 1):
        if ctx.is_comment(n):
            continue
        if re.search(r"(?<![\w:-])(?:focus:)?outline-none\b", line):
            window = "\n".join(ctx.lines[max(0, n - 4):n + 3])
            if not focus_style.search(window):
                yield Hit(n, line.strip())
        elif re.search(r"\boutline:\s*(?:none|0)\b", line) and not file_has_focus_visible:
            yield Hit(n, line.strip())


ARBITRARY_SIZE = re.compile(
    r"(?<![\w-])-?(?:p|m|px|py|pt|pb|pl|pr|mx|my|mt|mb|ml|mr|w|h|size|min-w|max-w|min-h|max-h|gap|"
    r"gap-x|gap-y|top|left|right|bottom|inset|text|leading|tracking|rounded|space-x|space-y)"
    r"-\[(\d+(?:\.\d+)?(?:px|rem|em))\]"
)


# ─── 2025–26 AI look: editorial dark-mode templates ─────────────────────────

_TW_EYEBROW = re.compile(r"^(?=[^\n]*\buppercase\b)(?=[^\n]*\btracking-(?:widest|wider|\[[0-9.]+(?:em|px|rem)\]))")


@rule(
    "ui/eyebrow-labels", "Uppercase \"eyebrow\" labels everywhere", SEVERITY_LOW, CATEGORY_UI_SLOP, (KIND_MARKUP,),
    why="Tiny uppercase, widely letter-spaced labels above every section and card (\"A QUIETER KIND OF "
        "PRODUCTIVITY\", \"SCREEN TIME TODAY\") are the signature of current AI-generated editorial UIs. "
        "Used once they add hierarchy; used everywhere they're noise.",
    fix="Keep at most one or two eyebrow labels on a page. Replace the rest with normal-case section headings "
        "or remove them; let size and weight carry the hierarchy.",
    skip_tests=True, aggregate_per_file=True,
)
def check_eyebrow_labels(ctx: FileContext) -> Iterable[Hit]:
    """Flag files that use eyebrow labels 4+ times (Tailwind utilities or a CSS eyebrow class)."""
    theme = getattr(getattr(ctx, "project", None), "theme", None)
    classes = theme.eyebrow_classes if theme is not None else set()
    class_rx = re.compile(r"class(?:Name)?\s*=\s*[\"'{`][^\"'}`]*\b(?:" + "|".join(map(re.escape, classes)) + r")\b") \
        if classes else None
    hits = []
    for n, line in enumerate(ctx.lines, 1):
        if ctx.is_comment(n):
            continue
        if _TW_EYEBROW.search(line) or (class_rx is not None and class_rx.search(line)):
            hits.append(Hit(n, line.strip()))
    if len(hits) >= 4:
        yield from hits


_ACCENT_SPAN = re.compile(
    r"<(?:span|em|strong|mark)\b[^>]*\bclass(?:Name)?\s*=\s*[\"'{`][^\"'}`]*"
    r"(?:\baccent|\bhighlight|\bgradient|\bbrand|\bprimary|text-transparent|"
    r"\btext-(?!white\b|black\b|foreground\b|inherit\b|current\b|left\b|right\b|center\b|xs\b|sm\b|base\b|lg\b|[2-9]?xl\b)"
    r"[a-z]+-\d{2,3})", re.IGNORECASE)


@rule(
    "ui/two-tone-headline", "Two-tone accent headline", SEVERITY_LOW, CATEGORY_UI_SLOP, (KIND_MARKUP,),
    why="Headlines with the last words in the accent color (\"Make space for <span class=accent>what "
        "matters.</span>\") are the default hero of AI-generated landing pages.",
    fix="Set the headline in one color. If one idea needs emphasis, use weight or a single underline — "
        "and write a headline specific enough not to need it.",
    skip_tests=True,
)
def check_two_tone_headline(ctx: FileContext) -> Iterable[Hit]:
    """Flag h1/h2 elements containing an accent-colored span (by class, or styled via `h1 em {...}` CSS)."""
    css_styled = set(re.findall(r"\b(h[12])\s+(em|i|span|strong|mark)\b[^{}]*\{[^}]*\b(?:color|font-family)\s*:",
                                ctx.text, re.IGNORECASE))
    for name, _attrs, start, end in iter_tags(ctx, ("h1", "h2")):
        close = ctx.text.find(f"</{name}>", end)
        if close == -1 or close - end > 600:
            continue
        inner = ctx.text[end:close]
        styled_child = any(heading == name and re.search(rf"<{child}[\s>]", inner) for heading, child in css_styled)
        if _ACCENT_SPAN.search(inner) or styled_child:
            line = ctx.line_of(start)
            yield Hit(line, ctx.line_text(line).strip())


line_rule(
    "ui/aphorism-copy", "Generic aphorism copy", SEVERITY_MEDIUM, CATEGORY_UI_SLOP, (KIND_MARKUP,),
    why="The newer AI copy voice — \"Make space for what matters\", \"Less autopilot. More intention.\", "
        "\"Intentional by design\", \"Your data stays yours\", \"Focus, on purpose\" — sounds calm and says "
        "nothing. Readers now recognise it as instantly as \"Revolutionize your workflow\".",
    fix="Say concretely what the product does and the specific benefit (\"Blocks the sites you choose during "
        "the hours you set\"). Cut taglines that could appear on any product.",
    pattern=r"\b(?:make|making|create|find|clear)\s+(?:space|room|time)\s+for\s+(?:<[^>]+>\s*)?what\s+(?:truly\s+|really\s+)?matters\b"
            r"|\bwhat\s+(?:truly|really)\s+matters\b"
            r"|\b(?:intentional|private|calm|thoughtful|quiet|honest|human)\s+by\s+design\b"
            r"|\bless\s+[a-z-]+\.\s*more\s+[a-z-]+\b"
            r"|\ba\s+(?:quieter|calmer|kinder|gentler|smarter|better|new)\s+(?:kind|way)\s+(?:of|to)\b"
            r"|\bstays?\s+yours\b"
            r"|\b(?:focus|work|build|live|ship|browse|create)(?:,)?\s+on\s+purpose\b"
            r"|\bwithout\s+the\s+(?:noise|clutter|chaos|bloat|overwhelm)\b"
            r"|\bget\s+back\s+to\s+(?:your\s+life|what\s+matters)\b"
            r"|\b(?:thoughtfully|beautifully)\s+(?:designed|crafted|built)\b"
            r"|\bcrafted\s+with\s+(?:care|intention)\b"
            r"|\b(?:made|built|designed)\s+for\s+the\s+way\s+you\b"
            r"|\bwith\s+(?:soul|heart|character)\b"
            r"|\bworth\s+slowing\s+down\s+for\b"
            r"|\bdeserves?\s+(?:better|more)\s+than\b"
            r"|,\s*not\s+just\s+(?:looks|a\s+\w+|another\s+\w+)\b"
            r"|\ba\s+little\s+more\s+you\b"
            r"|\bfeel\s+(?:at\s+home|like\s+home)\b"
            r"|\b([A-Z][a-z]+)\s+[a-z]+\.\s+\1\s+[a-z]+\.",
    skip_tests=True, project_threshold=2,
)

_INLINE_HEIGHT = re.compile(r"style\s*=\s*(?:\"|\{\{)[^\"}]*\bheight\s*:\s*['\"]?\d{1,3}%|\bh-\[\d{1,3}%\]")
_HARDCODED_SERIES = re.compile(
    r"\b(?:spark|bars?|chart|trend|heights?|graph|series|week|daily|history)\w*\s*[:=]\s*"
    r"\[\s*\d{1,3}(?:\s*,\s*\d{1,3}){5,}\s*\]", re.IGNORECASE)


@rule(
    "ui/fake-chart", "Chart drawn from hard-coded data", SEVERITY_MEDIUM, CATEGORY_UI_SLOP, (KIND_MARKUP, KIND_SCRIPT),
    why="A sparkline or bar chart whose heights are hard-coded (style=\"height: 40%\" ×7, sparkData = "
        "[12, 18, 30, …]) shows a trend that doesn't exist — often right next to a \"0\" value. It's "
        "decoration pretending to be data.",
    fix="Render the chart from the real data, or show an honest empty state (\"No data yet\") until there is some.",
    skip_tests=True, aggregate_per_file=True,
)
def check_fake_chart(ctx: FileContext) -> Iterable[Hit]:
    """Flag 5+ hard-coded percentage bar heights in a file, or a literal numeric series named like chart data."""
    inline = [n for n, line in enumerate(ctx.lines, 1) if not ctx.is_comment(n)
              for _ in _INLINE_HEIGHT.finditer(line)]
    if len(inline) >= 5:
        yield Hit(inline[0], ctx.line_text(inline[0]).strip(), f"{len(inline)} hard-coded bar heights")
    for match in _HARDCODED_SERIES.finditer(ctx.text):
        line = ctx.line_of(match.start())
        if not ctx.is_comment(line):
            yield Hit(line, ctx.line_text(line).strip(), "hard-coded series")


line_rule(
    "ui/fake-feature", "Button that only pretends to work", SEVERITY_MEDIUM, CATEGORY_UI_SLOP, (KIND_MARKUP, KIND_SCRIPT),
    why="Sign-in, checkout or booking buttons that just alert('...preview: connect your provider here') or say "
        "\"connect a booking backend to accept real bookings\" ship a feature that doesn't exist. Users click it "
        "and nothing real happens.",
    fix="Implement the feature, or remove/disable the control and say plainly that it's not available yet.",
    pattern=r"\balert\(\s*['\"`][^'\"`]*\b(?:preview|coming soon|not (?:yet )?(?:implemented|available)|todo|"
            r"placeholder|demo)\b"
            r"|['\"`>][^'\"`<]*\bconnect\s+(?:your|a|an)\s+[\w -]{3,40}?\s+(?:here|to\s+(?:accept|enable|make|send|start))\b"
            r"|\bdemo\s+(?:request|mode|only|booking|checkout|payment)\b",
    skip_tests=True,
)


line_rule(
    "ui/fake-ratings", "Hard-coded ratings and review counts", SEVERITY_MEDIUM, CATEGORY_UI_SLOP, (KIND_MARKUP, KIND_SCRIPT),
    why="Listings shipped with literal ratings and review counts (rating: '4.96', reviews: 84) show reviews "
        "nobody wrote. It looks like social proof and it's invented.",
    fix="Load ratings and reviews from real data, or hide them until there are real reviews.",
    pattern=r"\brating\s*:\s*['\"]?[3-5]\.\d{1,2}['\"]?\s*,\s*(?:reviews?|reviewCount|review_count)\s*:\s*\d+",
    skip_tests=True, aggregate_per_file=True,
)
