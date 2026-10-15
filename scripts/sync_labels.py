# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Create/update GitHub labels from .github/labels.yml: uv run scripts/sync_labels.py [owner/name]"""

import pathlib
import subprocess
import sys

import yaml

repo = sys.argv[1] if len(sys.argv) > 1 else "Adityashandilya555/hackhive"
labels = yaml.safe_load((pathlib.Path(__file__).parents[1] / ".github/labels.yml").read_text())
for label in labels:
    subprocess.run(
        ["gh", "label", "create", label["name"], "--repo", repo, "--color", label["color"],
         "--description", label.get("description", ""), "--force"],
        check=True,
    )
print(f"synced {len(labels)} labels to {repo}")
