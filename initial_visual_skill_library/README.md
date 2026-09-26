# Initial Visual Skill Library

This directory contains the frozen initial Skill library used to initialize the C0 graph in the released experiments.

## Files

```text
initial_visual_skill_library/
├── initial_visual_skill_library.json  # Initial Category-to-Skill library
└── README.md                 # Documentation
```

The library contains:

- 11 initial root Categories;
- 864 unique Skills;
- 943 Category-to-Skill memberships.

A Skill may belong to more than one initial Category, so the membership count is larger than the unique-Skill count.

## Use with the release code

From `release_code/`:

```bash
bash 02_query_induced_joint_skill_dag_evolution/run.sh \
  --library ../initial_visual_skill_library/initial_visual_skill_library.json \
  --output-dir /path/to/dag_evolution_run
```

To validate the library and construct only the C0 checkpoint without model calls:

```bash
bash 02_query_induced_joint_skill_dag_evolution/run.sh \
  --library ../initial_visual_skill_library/initial_visual_skill_library.json \
  --output-dir /path/to/c0_validation \
  --stop-after c0
```
