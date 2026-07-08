# Warden — Market Memo

> **What this is:** an honest, point-in-time competitive/market analysis of the space
> Warden sits in, produced **2026-07-07** via a multi-agent web-research pass (fan-out
> across observability, AI-security, agent-governance, platform-native, and market
> areas → adversarial verification of the load-bearing claims → synthesis). Funding,
> pricing, and product facts move fast — treat this as a snapshot, not gospel. I built
> Warden *first* and researched the market *after*; this is that research, unedited in
> substance.

## Bottom line up front

There is **real, growing money in the category** — but the specific bet Warden is built
on ("cross-vendor coding-agent governance is unclaimed, and local-first is the open
seam") is **no longer true as of mid-2026**. In the last ~4 months the wedge got a name,
funded pure-plays, public pricing, and — critically — a **funded, shipping, local-first
competitor**. Warden is a good OSS project and a credible portfolio/acqui-hire artifact,
but the odds of it becoming a standalone venture-scale company for a solo builder are
**low (~10–15%)**. Play it as OSS + a narrow developer-owned/data-residency niche +
exit-to-platform, not as a category-defining startup.

## Is there real money here?

Yes — the budget and category are real, but the money is already flowing to funded
players, not into an empty field.

- Enterprises spend ~**$150–250/dev/mo** on a single agentic coding tool ([Anthropic Claude Code cost docs](https://code.claude.com/docs/en/costs)); blended $200–600/dev/mo for multi-tool teams. A governance layer is a plausible **10–25% attach**.
- Dev-security pricing band: Snyk Team **$25/dev/mo** ([snyk.io/plans](https://snyk.io/plans/)), GitHub Advanced Security **$49/committer/mo**.
- Direct in-category price anchor: **MintMCP $1,250/mo per 50 seats ≈ $25/seat/mo** ([mintmcp.com/pricing](https://www.mintmcp.com/pricing)).
- Tailwind is large but soft: a Gartner-cited AI-cybersecurity TAM of **~$172B by 2029 (~74% CAGR)** — *single secondary source, treat as unconfirmed* — while the narrow agentic-security slice is *"still measured in hundreds of millions, not billions"* ([softwarestrategiesblog, 2026-03-28](https://softwarestrategiesblog.com/2026/03/28/agentic-ai-security-startups-funding-mna-rsac-2026/)).

## Is the cross-vendor coding-agent governance wedge open?

**No — not anymore, and this is the decisive finding.**

- **Unbound branded the category** — "Agent Access Security Broker (AASB)," cross-vendor, full agent-action audit + block/approve ([PRNewswire, 2026-03-20](https://www.prnewswire.com/news-releases/unbound-ai-announces-agent-access-security-broker-aasb-a-new-market-category-for-governing-ai-coding-agents-302718987.html)).
- **Runlayer preaches Warden's exact thesis, funded at ~$42M** — $30M Series A 2026-06-24 (Felicis + Khosla), up from an $11M seed ~7 months earlier; *"no platform can own this"* ([TechCrunch](https://techcrunch.com/2025/11/17/mcp-ai-agent-security-startup-runlayer-launches-with-8-unicorns-11m-from-khoslas-keith-rabois-and-felicis/)). Caveat: MCP gateway — blind to non-MCP shell/file ops.
- **Local-first is claimed too** — Operant AI's **Endpoint Protector** (2026-05-04) is a native macOS/Windows/Linux app doing cross-vendor coding-agent + MCP monitoring, shell/package-execution guard, and injection defense — essentially Warden's whole design, shipped ([Help Net Security](https://www.helpnetsecurity.com/2026/05/04/operant-ai-endpoint-protector-secures-ai-agents-and-mcp-tools/)).
- **OSS already implements the spec** — Hawkeye, Unworldly (local-first, auto-detects 8 agents, tamper-proof chain), AEGIS (signed audit trails). Not first-mover.
- **General cross-vendor agent governance is funded** — Geordie ($36.5M total, won 2026 RSAC Innovation Sandbox), though for *general* enterprise agents ([Fortune](https://fortune.com/2026/05/28/geordie-security-governance-ai-agents/)).

**The only honest white space:** a local-first, **developer-owned** (not MDM/CISO-managed),
data-residency-friendly, portable "what changed on THIS machine" timeline covering the
**full non-MCP action path** (native shell/file/self-tamper) plus **injection flagging in
tool results**. Operant occupies "local" but as an enterprise/MDM product; Runlayer/MintMCP
occupy "cross-vendor" but as cloud MCP gateways blind to non-MCP local actions. A niche,
not a category — and squeezed from both sides.

## Platform-disintermediation risk: HIGH, from three directions

1. **From below ($0):** every vendor ships free native audit+policy; native OTel + Claude Code hooks commoditize the audit half at $0; **GitHub Agent HQ / Enterprise AI Controls** is a native cross-vendor plane (GitHub-scoped, excludes local trace); **Microsoft** gives away an MIT Agent Governance Toolkit *and* extended Dataverse cross-vendor coding-agent governance (2026-07-06).
2. **From the side:** Datadog/Dynatrace/Elastic own the cross-vendor audit timeline via free OTel; Datadog even shipped an experimental local [`ai-guard-coding-agents`](https://github.com/DataDog/ai-guard-coding-agents) CLI that runs Claude/Codex/Cursor calls through a firewall before execution.
3. **From above:** 6+ acquisitions in ~18 months — Robust Intelligence→Cisco, Protect AI→Palo Alto, Prompt Security→SentinelOne, Lakera→Check Point, Aim→Cato, Invariant→Snyk. Realistic exit is acquisition/acqui-hire (~$250–300M comps), not IPO.

## Pricing benchmarks

| Reference | Price | Anchors |
|---|---|---|
| MintMCP Teams | **$25/seat/mo** ($1,250 / 50 seats) | direct in-category |
| Snyk Team | $25/dev/mo | dev-security low |
| GitHub Advanced Security | $49/committer/mo | dev-security high |
| Microsoft Agent 365 | $15/user/mo | platform floor |
| **Free floor** | **$0** | native OTel + hooks + MS MIT toolkit |
| Budget governed | $150–250/dev/mo per tool | attach math |

Read: price a governance attach at **~$15–40/dev/mo**, sold to platform-eng/DevEx with
security co-signing — but the $0 free floor means the **firewall + injection-in-results +
cross-vendor unified policy** must carry the price, not the audit log alone.

## Recommended path (solo builder)

1. **Don't out-platform the funded players** — you lose a cloud-gateway race vs Runlayer's $42M and an endpoint race vs Operant's shipped app.
2. **Own the niche they ignore:** local-first + developer-owned + data-residency, for regulated/air-gapped teams that won't send agent transcripts + diffs off the box.
3. **Ship OSS first, monetize edges** — do the two things everyone under-serves: full local execution path *beyond* MCP, and *one* cross-vendor policy schema. Treat the interception hook and injection detection as features, not the wedge (both commoditized).
4. **Price a $15–40/dev/mo attach** to platform-eng/DevEx, security co-signing.
5. **Hard timing gate:** Runlayer 6×'d funding in ~7 months. If not in front of ≥2 design partners within a quarter or two, run it as OSS / acqui-hire, not a venture bet.

**One-liner:** *Real market, wrong timing for the original thesis. The wedge — including
local-first — is now claimed and funded. Warden's only daylight is the narrow
developer-owned, data-residency, full-non-MCP niche, best played as OSS with an
acquisition/acqui-hire exit.*

## What would flip this verdict (worth re-checking in a few months)

- **A funded player stumbles or narrows** — e.g. Runlayer/Operant stay MCP/endpoint-only and never cover the native non-MCP local path, leaving the developer-owned niche genuinely open.
- **A platform mandate creates a compliance forcing-function** — a regulation or standard (EU AI Act enforcement, a SOC2-for-agents equivalent) that *requires* cross-vendor, on-device agent audit trails, expanding the serviceable pool fast.
- **The vendors decline to unify** — if GitHub Agent HQ / Microsoft cross-vendor governance stays shallow or GitHub-scoped, the independent cross-vendor position stays valuable.
- **A design-partner pull** — if 2+ regulated/air-gapped teams ask for exactly the local, no-cloud, developer-owned version, that's real signal the niche is a business, not just a feature.

## Confidence & gaps

- **High (re-verified live):** the category is named + funded (Unbound, Runlayer $42M, Geordie $36.5M); Operant's local endpoint app; MintMCP $25/seat; the consolidation wave; platform disintermediation (GitHub, Microsoft, Datadog, native OTel/hooks).
- **Medium / unconfirmed:** the $172B TAM (single-source, Gartner-cited); bottom-up seat counts and TAM range (estimates); whether Operant/Runlayer meaningfully capture the *non-MCP* local path in practice (claimed, not independently verified).
