# External DCO sign-offs on `main`

The Developer Certificate of Origin is not a separate file. Each sign-off is a `Signed-off-by` line on a commit (DCO 1.1). This list is the sign-offs on `origin/main` whose email is not an IBM address and not a known teammate.

Punleuk Oum is not listed. He signs as `Punleuk Oum <illeatmyhat@users.noreply.github.com>` and also appears as `Punleuk.Oum@ibm.com`.

## Sign-offs on `main`

| Signed-off-by | Commits on `main` |
| --- | --- |
| Alejandro Florez \<soyeladice@gmail.com\> | [#798](https://github.com/cuga-project/cuga-agent/commit/282633f4905fcef586dbb4f3e36949f946b833fa) docs: fix broken README and example-guide links; [#893](https://github.com/cuga-project/cuga-agent/commit/cac2a6f9f24ce51534aaa7ac56b06d7fd0cb329d) docs(registry): fix test paths and dead links |
| Alexis \<292251307+bernalalexis-try@users.noreply.github.com\> (bernalalexis030@gmail.com) | [#874](https://github.com/cuga-project/cuga-agent/commit/15360d8b78c9c704db2de2da7a949212e98eb17b) fix(events): keep 24-hour times when parsing "every day at HH:MM"; [#881](https://github.com/cuga-project/cuga-agent/commit/4355509cddbb775ee1769f22b1fb409c9eb73828) fix(events): preserve cron Sunday aliases |
| Bharat Prajapat \<bharat.xnj@gmail.com\> | [#947](https://github.com/cuga-project/cuga-agent/commit/53177865cdf2734014cd87b2c07cd4b51e201e73) test(agent_spawn): read source files as UTF-8 so tests pass on Windows |
| kragent66-glitch \<kragent66@gmail.com\> (Utkarsh Bhangale) | [#865](https://github.com/cuga-project/cuga-agent/commit/01b6139d400a27635d1466fd634430be11e259ea) docs: remove duplicate stability test command from README |
| Subhasish Padhi \<subhasish.padhi@nxtgen.com\> and Subhasish1802 \<subhasish1802@gmail.com\> (NxtGen Cloud Technologies) | [#492](https://github.com/cuga-project/cuga-agent/commit/8067b3d6080ba8efe9dcf2c7117d9e32ada1429d) feat(policy): expose decisions in invoke results |
| octo-patch \<266937838+octo-patch@users.noreply.github.com\> | [#435](https://github.com/cuga-project/cuga-agent/commit/0d0d6c48ffff39ac41d5bcc8e2854820d5a1aafc) feat: add MiniMax M3 provider |
| Daniel Weller \<daniel.weller@softwarepark.cc\> (TIMETOACT Austria) | [#5](https://github.com/cuga-project/cuga-agent/commit/ec33c1fbc96052bf66c634d3bd01e6fe663e5903) fix: support empty services config; [#11](https://github.com/cuga-project/cuga-agent/commit/2756419def4c313419f3808467b0245c19de5349) bugfix: fixes issue with cuga as mcp |

## Sign-offs that did not land on `main`

- **#890** Alejandro Florez, `fix(supervisor): honor the provider key in supervisor YAML model config`. The squash commit on `main` is [ce4d7f5466bf](https://github.com/cuga-project/cuga-agent/commit/ce4d7f5466bfcd9b545db39e94b03b51975d9711), signed only by `Sami Marreed <sami.marreed@ibm.com>`. His own line, `Signed-off-by: Alejandro Florez <soyeladice@gmail.com>`, is on the six pull-request commits, starting at `bae7d08a5be8`.
- **#495** octo-patch, `feat(llm): Support MiniMax China endpoint selection`. The pull-request commit `04d3bd2e0cb2` and the commit on `main` ([edaf3f23af28](https://github.com/cuga-project/cuga-agent/commit/edaf3f23af28)) have no `Signed-off-by` line. His other change, #435, does.
