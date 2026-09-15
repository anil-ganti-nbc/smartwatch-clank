# Production-source policy alignment — 2026-09-15

The production roster is exactly the 13 collectors in
`config/config.yaml`. `coros_updates`, `garmin_catalogue`, and
`garmin_official_news` remain registered and retain all historical evidence,
but are EXPERIMENTAL and excluded from production selection.

This is a qualification correction, not a collector deletion or evidence
rewrite. The three sources have not completed the required current bounded
soak. `coros_updates` also retains its documented firmware-novelty limitation;
the Garmin pair require a verified safe relay path before any new soak.

Read-only observation on 2026-09-15 found deployed commit
`9d85f926474e55310f3b565782c1c5cc4b7a20ea` still selecting all 16 sources in
the production cron, while the separate experimental systemd timer had no
eligible sources. This PR changes repository policy only. It does not deploy,
mutate either scheduler, alter runtime state, or manufacture qualification
history.
