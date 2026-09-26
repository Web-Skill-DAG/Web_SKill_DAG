# Final Evolved Skill DAG

This directory contains the frozen final Skill DAG used by the released current-DAG query-production pipeline.

## Files

```text
final_evolved_skill_dag/
├── graph.json    # Recursive DAG topology, node descriptions, and direct Skill memberships
├── skills.jsonl  # Full canonical documents for all Skill identities referenced by the DAG
└── README.md     # Documentation
```

The released checkpoint contains:

- 11 root nodes;
- 163 total DAG nodes;
- 864 Skill identities, including 858 active Skills after 6 verified merges;
- 1,088 direct node-to-Skill memberships.

The graph is acyclic and every child-node and Skill reference resolves within this directory.

## Use with the release code

From `release_code/`, validate and materialize the DAG without model calls:

```bash
bash 03_plug_and_play_website_query_production/run.sh \
  --checkpoint ../final_evolved_skill_dag \
  --input-jsonl /path/to/queries.jsonl \
  --output-dir /path/to/query_production_run \
  --materialize-only
```

Remove `--materialize-only` after configuring `api_config.sh` to run recursive routing, Full Review, and query expansion.
