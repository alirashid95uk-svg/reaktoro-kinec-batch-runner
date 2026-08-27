# Codex Cloud environment

Use these values in Codex Cloud environment settings for this repository.

- Environment name: `reaktoro-batch-cloud`
- Repository: `alirashid95uk-svg/reaktoro-kinec-batch-runner`
- Base branch: `main`
- Agent internet access: off
- Secrets: none
- Environment variables: none
- Setup script: `bash .codex/cloud/setup.sh`
- Maintenance script: `bash .codex/cloud/maintenance.sh`

The scripts create/update the repository's authoritative `fypr-reaktoro` Conda environment from `environment.yml` and verify that Python and Reaktoro import correctly. The root `AGENTS.md` remains the authority for scientific and verification rules.

## First cloud smoke test

Submit this task after creating the environment:

> Environment smoke test only. Do not change any files. Read AGENTS.md first. Confirm that the `fypr-reaktoro` environment contains Python 3.11 and Reaktoro 2.13.0. Then perform the smallest fast verification necessary to prove that the batch-runner repository imports correctly and its test tooling works. Do not run long geochemical simulations. Report the commands executed and their results.

Official Codex Cloud environment documentation:
https://learn.chatgpt.com/docs/environments/cloud-environment
