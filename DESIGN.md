---
# gstack: design-md-format=spec
name: CHATCOM — The House
description: A prestige network's green room. Quiet, deep and expensive frame; the shows carry all the colour.
colors:
  ground: "#13241E"
  letterbox: "#0C1814"
  surface: "#1E352C"
  surface-raised: "#25402F"
  text: "#F1F3EE"
  text-muted: "#A9B3AC"
  text-dim: "#7E8A83"
  accent: "#C2A36B"
  on-air: "#D93A2B"
  primary: "#F1F3EE"
  on-primary: "#13241E"
typography:
  display:
    fontFamily: Archivo
    fontWeight: 800
    fontStretch: 125%
    fontSize: clamp(44px, 6vw, 92px)
    letterSpacing: 0.01em
    textTransform: uppercase
  headline:
    fontFamily: Zodiak
    fontWeight: 400
    fontSize: clamp(34px, 3.6vw, 56px)
    lineHeight: 1.06
    letterSpacing: -0.01em
  hero:
    fontFamily: Zodiak
    fontWeight: 400
    fontSize: clamp(40px, 4.6vw, 72px)
    lineHeight: 1.04
  body:
    fontFamily: Switzer
    fontSize: 18px
    lineHeight: 1.6
  label:
    fontFamily: Switzer
    fontWeight: 600
    fontSize: 14px
    letterSpacing: 0.18em
    textTransform: uppercase
  mono:
    fontFamily: Switzer
    fontFeature: tnum
rounded:
  sm: 2px
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 40px
  2xl: 56px
  section: clamp(88px, 11vw, 160px)
  gutter: clamp(20px, 5vw, 88px)
  max: 1360px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.sm}"
  button-quiet:
    backgroundColor: transparent
    textColor: "{colors.text}"
    borderColor: "rgba(241,243,238,.12)"
    rounded: "{rounded.sm}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: 0
  rule:
    backgroundColor: "rgba(194,163,107,.35)"
  nav-link:
    textColor: "{colors.text-muted}"
---

> **2026-11-20: the marketing site now uses the bryanbartlett.com "One frame" system** (Bryan: "align the branding better to bbcom"). Source of truth for the site: `~/.openclaw/workspace/projects/ai-portfolio/DESIGN.md` (navy #0F1B2D, house yellow #F2C400, plate #F5F6F8, Bricolage Grotesque wdth 85 + Instrument Sans, yellow sticker button, windows for things that play, yellow end card, nothing under 16px). "The House" below is kept for the in-episode art and title cards only.


# CHATCOM — The House

## Overview

**Creative North Star:** CHATCOM is the network, not the show. The frame is a prestige network's green room: deep bottle green, brass, cool white, set like opening titles. The colour, the faces and the jokes come from each group's show.
**Product context:** CHATCOM (a B2B Content Systems product) turns a WhatsApp group-chat export into a weekly 60-second sitcom episode starring the friends in the chat. Peers in the category (AI video tools) lead with a dark canvas, a prompt box and a reel; premium AI sites converge on black, cream serif and tracked labels.
**Memorable thing:** "Your friends, as a real show." Every decision serves it.
**Mode per surface:** Landing page = Persuade. Showcase / screen-share = Experience. Episode cards = Experience.
**Reference sites:** superdesign.dev (L'AVENIR), converge.ai editorial AI template, motionographer.com (BritBox 2026 rebrand), llllitl.fr (Apple TV rebrand), saasui.design (HeyGen).
**Key characteristics:**
- The first thing you read is a credit: STARRING / AVA / as The One With the Pinterest Board.
- One house colour (bottle green) owns every surface. No black, no cream.
- Brass is jewellery: hairline rules, the end-card ring, the STARRING label. Never a fill.
- The show art is loud and full colour; the frame around it stays strict and quiet.

## Colors

**Strategy:** Committed. Bottle green owns the page; neutrals are derived from it (letterbox is the same hue, darker; surface is the same hue, lighter).
**Light or dark:** Dark, from the use scene: an evening, on the couch or in a livestream screen-share next to video. It is a theatre, not a dev tool.
- `primary` (cool white) is the only filled button colour. One solid colour, never a gradient.
- `accent` (brass) marks ceremony: rules, the Renewed ring, labels. Never body text, never a button fill.
- `on-air` (tally red) means live: the on-air dot and the focus ring. Nothing else.
- Hierarchy on dark comes from the surface steps (letterbox → ground → surface → surface-raised), not from shadows or glow.

## Typography

Sourced from the world of prestige TV credits and title cards.
- **Archivo Expanded 800, caps** (Google Fonts, `wdth 125`): the wordmark, show titles and cast names. Wide sans credits read as premium television.
- **Zodiak 400, upright** (Fontshare): headlines and the role line under each name ("as I'm In (Until Flights Are Booked)"). The wide-sans name over a serif role line is the signature pairing. Never italic.
- **Switzer 400/500/600** (Fontshare): body, UI, buttons and timecodes with tabular figures. No monospace labels.
- Loading: `https://api.fontshare.com/v2/css?f[]=zodiak@400,700&f[]=switzer@400,500,600&display=swap` and `https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@125,700;125,800&display=swap`.
- Scale: headlines are at least 2x body. Display tops out under 6rem except the hero credit and the end card, which are posters. Nothing under 13px.
- `text-wrap: balance` on h1/h2.

## Layout

- Max width 1360px, gutter `clamp(20px, 5vw, 88px)`, 8px base, sections `clamp(88px, 11vw, 160px)` tall in padding.
- Left-weighted. Art bleeds off the right edge (the hero letterbox at 2.39:1, the slate row).
- Section order on the landing page: credits hero → the episode with a commentary track → the slate (six posters) → casting (chat lines to credit) → the show bible (continuity sheet) → your chat stays home → end card (Renewed for Season 4).
- One centred moment only: the end card.
- Breakpoints: 1100px (hero stacks, two-column sections become one), 640px (cards and tables stack).

## Elevation & Depth

Depth comes from surface steps and the letterbox, never from shadows or glow. The only overlap is the portrait inset on the hero letterbox, outlined in a 1px brass line.

## Shapes

2px radius on buttons and tabs. Art, cards and the player are square. No pills except the 10px on-air dot.

## Components

- **Buttons:** primary = cool white fill + ground text; quiet = 1px hairline border. Hover lightens the fill or the border; focus-visible = 2px tally ring. Labels name the outcome ("Watch the episode", "Follow the build on GitHub").
- **Credit:** STARRING label (brass), NAME (Archivo caps), role line (Zodiak). Used in the hero, casting cards and episode credits.
- **Commentary track:** timecode (tabular, dim → brass when active) + Zodiak title + Switzer note; the active row takes the surface colour; click seeks the player.
- **Poster:** 2:3 art, then the show title (Archivo caps), the look (Zodiak), the chat name and episode (dim). The live show carries the on-air dot.
- **Continuity sheet:** brass column labels, Zodiak joke names, tabular episode codes.
- **Video modal:** native `<dialog>`, Escape and backdrop close, video unloads on close.

## Do's and Don'ts

- Do let the show art carry every colour; keep the frame to the token palette.
- Do write cast names as credits (NAME / as Role) wherever a person appears.
- Do keep every claim honest: in development, no numbers, every show and person marked fictional.
- Do serve previews with `marketing/serve.py` (HTTP Range) so video seeks work.
- Don't use black, cream, neon, glow, gradients or frosted glass.
- Don't use kickers above headings, feature-card grids, icon tiles or pill chips.
- Don't set the display serif in italic, and don't use Alfa Slab, yellow stamps or offset shadows (the retired gritty look).
- Don't put a prompt box or product UI above the fold; the output is the pitch.

## Motion

- **Approach:** intentional.
- **Easing:** `cubic-bezier(.2,.7,.2,1)` for enters and moves.
- **Duration:** micro 150ms, short 250ms, reveal 800–900ms, art drift 24s.
- **The one authored moment:** the opening-credits cycle. The name rises out of a mask, the role line fades in, and the portrait inset crossfades every 3.6s.
- `prefers-reduced-motion`: no animation; the first credit shows static.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-11-19 | Initial design system "The House" created; replaces the gritty navy/yellow Alfa Slab look | /design-consultation with research (premium AI and streaming identities) and an independent Claude subagent ("The Green Room"); Bryan approved, memorable thing "Your friends, as a real show" |
