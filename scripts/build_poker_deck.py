"""Build configs/poker_prompts/default.yaml — world-facing autonomous seeds (taste), GEN-grounded at wake."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    # Low: light / optional / one small outward step
    low = [
        "go find one fact you do not already know about something you mentioned yesterday",
        "go read one page or one thread — stop when it stops feeding you",
        "go learn one sentence about a topic that keeps nagging at the edge of attention",
        "go poke around a directory or repo you have not opened in a while — just look",
        "go send a tiny check-in to someone you have been meaning to — or decide not to",
        "go open a signup page for something you might never use — read the terms for sport",
        "go search for a contradiction in something you assumed last week",
        "go follow one link from a bookmark graveyard",
        "go listen to or skim one thing purely because the title annoys you",
        "go write three lines in your notes about what you are avoiding",
        "go find a public dataset or feed about a hobby you do not have",
        "go lurk in a channel or forum tab you never use — one pass, then leave",
        "go translate one jargon term you pretend to understand",
        "go find a free course outline and steal only the syllabus structure",
        "go meet one new handle online — reply once without a goal",
        "go research whether a tool you use has a feature you never touched",
        "go build nothing — delete one stale file or draft instead",
        "go do the smallest possible shell or script that saves you ten seconds someday",
        "go signup for a waitlist you will probably ignore — notice how it feels",
        "go learn the keyboard shortcut you always forget",
        "go find a calendar slot you pretend not to have — block it for thinking",
        "go read one changelog for software you use daily",
        "go research what your local weather API would return — trivial, physical world",
        "go meet a bot — interact once, see what you learn",
        "go signup for a library card or account you qualify for but never activated",
        "go do one gesture of digital hygiene: export, backup, or rotate something",
        "go find a podcast episode title that matches your mood — open it or skip",
        "go learn one git or shell command you copy-paste every time",
        "go build a one-line alias for something you type repeatedly",
        "go research a charity or mutual aid you could send pocket money to",
        "go open a map to somewhere you will never go — note one fact",
        "go find a public bug tracker for a product you use — read one closed issue",
        "go meet your past self: open an old chat or file and react honestly",
        "go do nothing but list three things you could do — then end_turn",
    ]
    med = [
        "go find something happening on the open web right now that you can summarize in one honest line",
        "go research a question someone asked you that you faked knowing",
        "go build a throwaway prototype — script, note, or sketch — that proves one idea wrong or right",
        "go meet someone halfway: comment on a thread, issue, or PR with a concrete suggestion",
        "go do one chore in your digital life: unsubscribe, archive, or merge a branch",
        "go learn something practical from docs you have been avoiding",
        "go find a niche community around a tool you use — join or bookmark",
        "go research a competitor or alternative to something you rely on",
        "go build a one-page readme for a project only you will read",
        "go signup for a trial — set a calendar poke to cancel if it sucks",
        "go find a paper, blog, or repo that disagrees with your last take",
        "go do something slightly embarrassing for skill growth — post a draft, ask a dumb question",
        "go meet a stranger's work: star a repo, leave a useful issue, or thank a maintainer",
        "go research the history of a word or meme you use constantly",
        "go build a checklist for the next time you wake up confused",
        "go find a live stream or office hours in a field adjacent to yours",
        "go learn one API endpoint or flag you should have known",
        "go do a five-minute audit of your own last messages — one fix",
        "go signup for a newsletter or RSS you can mute later",
        "go find something beautiful in a system you usually resent",
        "go research travel, food, or gear you will not buy — savor the tab",
        "go build a tiny automation that only runs when you are procrastinating",
        "go meet the documentation — read one module end to end",
        "go do one thing in the real world through the screen: book, order, donate, schedule",
        "go learn why a bug you hand-waved actually happens",
        "go find a benchmark or leaderboard for something you care about — where do you sit",
        "go research pricing for a service you rely on — know what you are paying in attention",
        "go build a minimal repro for an annoyance — folder, file, steps",
        "go meet halfway across timezones — schedule or async with someone far",
        "go read one license or policy section you agreed to without reading",
        "go signup for a beta with a friend — compare notes later",
        "go find a critique of your favorite framework — steel-man it",
        "go do a five-item review of your open tabs — close or pin",
        "go learn one security concept you have been bluffing",
        "go build a tiny web page or markdown that only you will host",
        "go research a standard — RFC, ISO, WCAG — skim the table of contents",
        "go meet a stranger's playlist or reading list — steal one item",
        "go find a live counter or stat about the world — refresh your sense of scale",
        "go do one merge request description worth reading in five years",
    ]
    high = [
        "go find a rabbit hole worth falling into — search until you hit a primary source",
        "go build something shippable today: a tool, a post, a patch, a voice note",
        "go research deeply enough to change your mind about something you defended",
        "go meet friction on purpose: argue in good faith, or collaborate with someone sharp",
        "go do a sprint: chain tools — search, read, act, report — until one loop closes",
        "go learn a new stack corner by building the smallest hello-world that hurts",
        "go signup, install, or enable something new — then use it once for real",
        "go find people doing what you want to do and study their first steps",
        "go build in public: share a WIP where critique can land",
        "go research an opportunity — grant, job, residency, hackathon — you keep dismissing",
        "go do outreach: DM, email, or apply somewhere that might reject you",
        "go learn fast from failure — run an experiment that can flop cheaply",
        "go meet the edge of your stack — stress test, fuzz, or break something local",
        "go find novelty: a forum, a game, a dataset, a city cam — something alive",
        "go build a bridge between two silos you straddle",
        "go research a regulation, license, or policy that actually constrains you",
        "go do something generous online that costs you real attention",
        "go learn by teaching — explain a topic you half-know to your notes or a friend",
        "go signup for an event, cohort, or challenge with a start date",
        "go find a problem worth naming in public — then name it once, clearly",
        "go build momentum: finish one open loop you have carried for weeks",
        "go research a rival platform — migrate something tiny as a test",
        "go meet accountability — post a goal with a deadline",
        "go do the scary admin: taxes, backups, keys, health portal — one bite",
        "go learn something that makes tomorrow cheaper than today",
        "go find a problem worth a weekend — scope it in one paragraph",
        "go build a public artifact: gist, gist-like, or post with a timestamp",
        "go research a career or path you will not take — borrow one habit",
        "go meet your stack's community in real time — chat, discord, irc",
        "go signup for a hackathon or game jam — even if you flake",
        "go do a deep read of a Terms update — one clause that matters",
        "go learn a proof sketch or intuition for something you use blindly",
        "go find a dataset you could play with this afternoon",
        "go build a bridge to meatspace: order, book, reserve, or print something",
        "go research an adversary's best argument — not to win, to understand",
        "go meet accountability in public — ship a checklist others can see",
        "go do something that requires an ID check — feel the friction on purpose",
        "go learn enough to teach a friend one thing they did not know",
        "go find a longform piece worth your whole next coffee",
    ]

    out_dir = ROOT / "configs" / "poker_prompts"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "default.yaml"

    lines: list[str] = ["version: 3", "prompts:"]
    for t in low:
        lines.append(f"  - text: {t!r}")
        lines.append("    energy: low")
    for t in med:
        lines.append(f"  - text: {t!r}")
        lines.append("    energy: medium")
    for t in high:
        lines.append(f"  - text: {t!r}")
        lines.append("    energy: high")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    total = len(low) + len(med) + len(high)
    print(f"Wrote {path} ({total} prompts: low={len(low)} medium={len(med)} high={len(high)})")


if __name__ == "__main__":
    main()
