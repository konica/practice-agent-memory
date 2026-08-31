# practice-agent-memory

## Prerequisites

Host-level tools (not covered by any Dockerfile/venv/package.json in this repo)
are provisioned via Ansible:

```bash
ansible-playbook ansible/playbook.yml --ask-become-pass
```

See `ansible/playbook.yml` for the full list.