# Documentation Index

**Status:** Living Document  
**Last Updated:** February 1, 2026  
**Version:** 6.8.8

---

## Purpose

This index explains the documentation strategy for the M1_DC_V6 Blender add-on. It defines what documentation exists, where to find it, and how to maintain it going forward.

---

## Living Documentation (Always Current)

These files are the **single source of truth** and must be kept up-to-date:

### [README.md](README.md)
**Audience:** Users, new contributors, agents (first contact)  
**Content:**
- What this add-on does (2-sentence summary)
- Installation and setup
- Quick start guide
- Proof markers reference (6 validation markers)
- Development workflow (VSCode, reload procedures, testing)
- Troubleshooting common issues
- Hygiene rules (critical: `__pycache__` removal after import changes)
- Changelog (high-level version history)

**Update frequency:** After user-facing changes, new features, or workflow improvements

---

### [ARCHITECTURE.md](ARCHITECTURE.md)
**Audience:** Developers, maintainers, agents (technical deep-dive)  
**Content:**
- Current folder structure (accurate tree diagram)
- Module responsibilities (what each file does)
- Import patterns (Phase 10 state: `from ...utils.X`)
- Pipeline phases (0–5 architecture)
- Extension lifecycle (register/unregister patterns)
- Recent changes (Phases 7–11 summary)

**Update frequency:** After structural changes, refactors, or architectural decisions

**Note:** Historical details (old experiments, deprecated structures, past decisions) are archived in [`_archive/architecture_history.md`](_archive/architecture_history.md) to keep this file readable and current.

---

### [DOCS_INDEX.md](DOCS_INDEX.md) (this file)
**Audience:** Documentation maintainers, agents  
**Content:**
- Documentation strategy explanation
- Where to find what information
- How to update docs in the future

**Update frequency:** Rarely (only when documentation strategy changes)

---

## Archived Documentation (Reference Only)

### [_archive/HISTORY_SNAPSHOT.md](_archive/HISTORY_SNAPSHOT.md)
**Audience:** Agents, new maintainers (onboarding context)  
**Content:**
- Curated project history (Phases 6–11)
- What changed, why, and what was validated
- Current project state (imports clean, utils stable, addon loads)
- Token-efficient summary for AI agents

**Update frequency:** After major phase milestones (Phase 12, 13, etc.)

---

### [_archive/architecture_history.md](_archive/architecture_history.md)
**Audience:** Developers investigating past decisions  
**Content:**
- Historical architectural reasoning
- Deprecated structures (pipeline/api/, vendor/)
- Old experiments and alternatives
- Why certain approaches were abandoned

**Update frequency:** When archiving sections from ARCHITECTURE.md

---

### [_archive/phases_20260201/](_archive/phases_20260201/)
**Audience:** Auditors, deep-dive investigators  
**Content:**
- Original phase reports (PHASE_6_*.md → PHASE_11_*.md)
- Line-by-line change logs
- Validation reports and proof runs
- Commit-level documentation

**Purpose:** Forensic reference, not daily reading

**Update frequency:** Never (immutable archive)

---

## Documentation Policy (How to Update Docs)

### When to Update Living Docs

| Scenario | Update README.md | Update ARCHITECTURE.md |
|----------|------------------|------------------------------|
| New feature added | ✅ Yes (usage instructions) | ⏳ Maybe (if architecture changes) |
| Import paths changed | ❌ No | ✅ Yes (import patterns section) |
| Bug fix (no structure change) | ⏳ Maybe (if troubleshooting affected) | ❌ No |
| New module added | ⏳ Maybe (if user-facing) | ✅ Yes (folder tree + responsibilities) |
| Refactor (same behavior) | ❌ No | ✅ Yes (if module responsibilities shift) |
| Version bump | ✅ Yes (changelog) | ✅ Yes (version number) |
| Proof markers changed | ✅ Yes (proof markers section) | ⏳ Maybe (if validation architecture changes) |

---

### What NOT to Do

❌ **Do NOT** add phase logs to living docs (README or ARCHITECTURE)  
❌ **Do NOT** keep outdated sections "for reference" (archive instead)  
❌ **Do NOT** duplicate content between README and ARCHITECTURE  
❌ **Do NOT** create new top-level markdown files (use README or ARCHITECTURE sections)  
❌ **Do NOT** delete archived phase documents (they are immutable proof)

---

### How to Archive Old Content

When ARCHITECTURE.md becomes too large:

1. **Identify historical sections:**
   - Old experiments, deprecated structures, superseded designs
   - Detailed reasoning that's no longer relevant daily

2. **Move to archive:**
   - Copy full section to `_archive/architecture_history.md`
   - Add date/phase reference
   - Add note: "Moved from ARCHITECTURE.md during Phase X cleanup"

3. **Replace in ARCHITECTURE.md:**
   - Keep 1-paragraph summary
   - Add link: `_Historical details in [architecture_history.md](_archive/architecture_history.md)_`

4. **Never delete:**
   - Historical content has value for future investigations
   - Archive preserves reasoning without cluttering living docs

---

## For AI Agents: Quick Start

**First time in this codebase?**

1. Read [README.md](README.md) (10 min) → Understand what the add-on does
2. Read [ARCHITECTURE.md](ARCHITECTURE.md) (20 min) → Understand how it's built
3. Skim [_archive/HISTORY_SNAPSHOT.md](_archive/HISTORY_SNAPSHOT.md) (5 min) → Context on past changes

**Need deep historical context?**
- Check `_archive/phases_20260201/` for specific phase details

**Making changes?**
- Update README.md if user-facing behavior changes
- Update ARCHITECTURE.md if structure/imports change
- Do NOT modify archived docs (immutable)

---

## Documentation Health Checklist

Use this before committing major changes:

- [ ] README.md reflects current user workflow
- [ ] ARCHITECTURE.md folder tree matches actual structure
- [ ] No outdated import paths in ARCHITECTURE.md
- [ ] Changelog in README.md updated with version number
- [ ] No duplicate content between README and ARCHITECTURE
- [ ] Historical content archived (not deleted)
- [ ] Phase logs (if new ones created) moved to `_archive/phases_YYYYMMDD/`

---

## Rationale (Why This Structure)

**Problem:** Documentation sprawl (18+ markdown files) makes onboarding overwhelming.

**Solution:** Separate living truth from historical proof.

**Benefits:**
- ✅ New contributors have clear entry points (README → ARCHITECTURE)
- ✅ Agents get token-efficient context (HISTORY_SNAPSHOT)
- ✅ No information loss (everything archived, not deleted)
- ✅ Maintainability (living docs stay current, archive grows linearly)
- ✅ Clear ownership (2 living docs, not 18 competing sources)

---

## Version History

| Version | Date | Change |
|---------|------|--------|
| 1.0.0 | 2026-02-01 | Initial documentation index (Phase 11.4) |
