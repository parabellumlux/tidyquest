# TidyQuest

**A Meta Global AI Developer Hackathon entry** (October 26 – November 4, 2026).

Photograph a messy room, and TidyQuest turns the chaos into a game: Meta's AI models identify and segment what's in the room, group the mess into zones, and hand back an itemized, step-by-step tidy plan — with the inevitable center pile staged as the Boss Battle.

## How it works

- **SAM 3.1** segments the objects in a room photo.
- **Muse Spark** reasons over the segments to label items, group them into zones, and sequence the work.
- **Muse Image / Muse Voice** support the presentation and demo layer.
- All model calls run on the **Meta Model API** — the app is built against the hackathon's own stack.

The demo differ works at zone-aggregate level: a before photo and an after photo of the same room show what changed, zone by zone.

## Team

- Diana Walker — product, build
- Damon Cory Pettigrew — product, build
- Built with Remy and Baymax, the team's AI collaborators

## Status

In active development for the hackathon window. This repository is private during development.

Test photographs are family photos used as internal test assets only and are deliberately kept out of this repository.
