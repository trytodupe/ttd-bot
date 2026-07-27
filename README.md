# ttd-bot

There is no documentations just yet.
More info in `src/plugins/ttd_help/registry.py`

The disabled-by-default isolated QQ development subsystem is documented in
[`dev-agent/README.md`](dev-agent/README.md). It may create and update draft PRs,
but it has no production deployment or merge path.

Release tags must be created through the deploy/release script as annotated tags with a short Chinese message.
Do not create deploy tags with raw `git tag` by hand.
