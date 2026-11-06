# Contributing to `methods`

Thank you for taking the time to contribute. This repository holds the methods, scripts and example data behind PulseHawk reports. Every number we publish must be reproducible from here, so we hold changes to a strict standard.

This guide follows the [CONTRIBUTING guide of librustzcash](https://github.com/zcash/librustzcash/blob/main/CONTRIBUTING.md) and its style guides for Git usage, merge workflow, branch history, pull request review and commit messages. Where this file is silent, the librustzcash rules apply. Where it differs, the reason is stated.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Questions](#questions)
- [Reporting Bugs](#reporting-bugs)
- [Suggesting Enhancements](#suggesting-enhancements)
- [Data Rules](#data-rules)
- [Self Test Before Every Pull Request](#self-test-before-every-pull-request)
- [Git Usage](#git-usage)
- [Commit Messages](#commit-messages)
- [Method and Format Changes](#method-and-format-changes)
- [Coding Style](#coding-style)

## Code of Conduct

We expect everyone who takes part to follow the [Zcash Code of Conduct](https://github.com/zcash/zcash/blob/master/code_of_conduct.md), as librustzcash does. Please report unacceptable behavior to the maintainers of this repository.

## Questions

Read `README.md`, `METHODS.md`, `FORMATS.md` and `ROUND_ROBIN.md` first, and search the [issues](https://github.com/pulsehawk/methods/issues). If your question is still open, open an issue and give as much context as you can, including the commit of this repository and your Python version.

## Reporting Bugs

> Never report a security problem in a public issue. Use the private vulnerability reporting of this repository (Security tab, "Report a vulnerability").

**Before you report a bug**

- Run `python3 upgrade_report/report_v1.py --selftest` and `python3 tools/heads_from_rpc.py --selftest` on the latest `main`. Both must end with `all tests passed`.
- Check whether the problem comes from your environment, for example a Python version older than 3.9.
- Search the existing issues for the same problem.

**How to report a bug**

- Open an issue. Do not label it, the maintainers will.
- Describe the behavior you expected and the behavior you saw.
- Give the steps that reproduce it, ideally with the smallest input that shows the problem. Name the commit of this repository, the config and the sha256 of every input.
- If a reported number is wrong, name the metric (M1 to M7), the method id (PH-M-001 to PH-M-006) and the row of the output CSV.
- Inputs you attach must follow the [data rules](#data-rules).

If the maintainers cannot reproduce the problem, they will ask you for steps. A bug is fixed once it is shown by a failing test, as described under [Git Usage](#git-usage).

## Suggesting Enhancements

Enhancements are tracked as issues.

- Use a clear title. Prefix it with the area, for example `report_v1:` or `METHODS:`.
- Describe the current behavior, the behavior you want and why it helps people who check our numbers.
- A new metric or a change to a method needs a written rule first. Say how it is counted, which window it uses and how its uncertainty is stated.

As in librustzcash, we do not accept trivial pull requests (spelling, link fixes, small style changes) from unknown contributors. Please report such problems as issues instead.

## Data Rules

The repository is public. Data that you add, attach to an issue or put in a test must never contain

- network addresses of any kind, such as IPv4 or IPv6 addresses, host names, onion or I2P addresses, or ports tied to a host,
- wallet or payment addresses, viewing keys or any other key,
- credentials, tokens or RPC passwords. Tools read these from the environment only, see `RPC_URL` in `tools/heads_from_rpc.py`.

Peers are named `peer_1`, `peer_2` and so on, numbered again for each recording. A 64 character hex string may only be a block hash or a transaction hash.

Check your data before you open a pull request. This command must print nothing.

```
P='([0-9]{1,3}\.){3}[0-9]{1,3}|([0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}|[a-z0-9-]+\.(com|net|org|io|de|xyz|app|dev|onion|i2p)\b|\b(t1|t3|tm|zs1|u1|utest1|ztestsapling1)[a-zA-Z0-9]{20,}'
git grep -nIE "$P" -- examples data
for f in $(git ls-files 'examples/*.gz' 'data/*.gz'); do zcat "$f" | grep -nE "$P" | sed "s#^#$f:#"; done
```

The command is a coarse filter, not a proof. You remain responsible for what you add. If you find an address in the repository, report the file and the line in an issue, never the value.

## Self Test Before Every Pull Request

Before you open a pull request, and again before you mark it ready for review, run

```
python3 upgrade_report/report_v1.py --selftest
python3 tools/heads_from_rpc.py --selftest
```

Both must end with `all tests passed`, and the data check above must print nothing. Every new script has its own `--selftest` that runs without network access and checks the rules it implements. If your change alters any output of the examples, the expected sha256 in `examples/*/expected/` change in the same commit, and the pull request says why.

## Git Usage

We follow the librustzcash [Git usage](https://github.com/zcash/librustzcash/blob/main/CONTRIBUTING.md#git-usage), [merge workflow](https://github.com/zcash/librustzcash/blob/main/CONTRIBUTING.md#merge-workflow), [branch history](https://github.com/zcash/librustzcash/blob/main/CONTRIBUTING.md#branch-history) and [pull request review](https://github.com/zcash/librustzcash/blob/main/CONTRIBUTING.md#pull-request-review) rules. In short

- **Branches.** Branch from the latest `main`. Use one branch per change. For a large change use stacked pull requests, each branching from and targeting the branch of its parent.
- **Merging.** Only a maintainer merges. We prefer merge commits and do not use rebase merge. A pull request without a clean commit history may be squash merged, which librustzcash also allows case by case.
- **History.** Each commit is one discrete semantic change. Fixes and answers to review comments go into the commit they concern, for example with [git revise](https://github.com/mystor/git-revise). There are no work in progress commits.
- **Bug fixes.** A fix starts with a commit that adds a failing test or a failing case in `--selftest`. The fix follows in its own commit.
- **Conflicts.** If `main` moves and causes a conflict, rebase your branch onto `main` before review starts. Once review has started, follow the librustzcash rules on the `S-please-do-not-rebase` label.
- **Drafts.** Open a pull request as a draft and mark it ready for review only once the self tests pass.
- **Review.** Mark each review comment you addressed as resolved. If you rebase during review, post a link to the diff between the old and the new branch tip.

**What a pull request contains**

- A short title in the commit message style below.
- What changes and why, which metrics and methods it touches, and whether outputs or expected sha256 change.
- The result of the self tests and of the data check.

## Commit Messages

As in librustzcash

- a short title, preferably under 120 characters, prefixed with the area, for example `report_v1: count negative intervals in M2`,
- a body that states the motivation for the change, unless the change is trivial,
- a `Co-Authored-By:` line for every co-author.

## Method and Format Changes

This repository has no `CHANGELOG.md`. Its role is played by `METHODS.md` and `FORMATS.md`. In the spirit of the librustzcash [changelog rules](https://github.com/zcash/librustzcash/blob/main/CONTRIBUTING.md#changelog-entries)

- a change to how a metric is counted, to a method or to a file format updates `METHODS.md` or `FORMATS.md` in the same commit,
- a change that alters published numbers gets a new method version, and the old version stays documented, so that every published report can still be reproduced from the commit it names,
- every number keeps its kind. counted is counted from our own data, source is taken from a named outside source, computed comes from a model or an estimate.

## Coding Style

- Python 3.9 or later, standard library only. A new dependency needs a strong reason and a discussion in an issue first.
- Results must be deterministic. The same inputs give the same output bytes and the same sha256.
- Inputs are checked, not trusted. A script stops with a clear message instead of guessing.
- Credentials come from the environment only and are never printed or logged.
- Keep functional changes and style changes in separate commits, as librustzcash asks.

## Attribution

This guide is adapted from the [librustzcash contributing guide](https://github.com/zcash/librustzcash/blob/main/CONTRIBUTING.md).
