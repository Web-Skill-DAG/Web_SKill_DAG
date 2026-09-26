# ✨ WebViSE: Release Code


## 🗂️ Repository tree

```text
release_code/
├── 01_website_derived_visual_skill_construction/  # Source websites -> Goal Queries -> Visual Skills
├── 02_query_induced_joint_skill_dag_evolution/    # Initial Skill DAG -> representative queries -> evolved DAG
├── 03_plug_and_play_website_query_production/     # Final DAG + raw queries -> expanded queries
├── dag_retrieval.py                              # Shared recursive Router and full-document Reviewer
├── prompts.py                                    # Shared model-facing prompts used across all workflows
├── requirements.txt                               # Python dependencies
├── api_config.sh                                  # Blank API configuration file to fill directly
├── play.sh                                        # Expands one raw website Query from the command line
├── .gitignore                                     # Ignores secrets, caches, and run artifacts
└── README.md                                      # Documentation
```

## ⚙️ 0. Setup

### Step 0.1: Install dependencies

Use an isolated Python 3.10+ environment:

```bash
cd /path/to/Web_SKill_DAG/release_code
python -m pip install -r requirements.txt
```

To use a particular Python interpreter, write its path in `api_config.sh` as described next:

```text
PYTHON="/path/to/python"
```

### Step 0.2: Configure the APIs

Open the provided blank `api_config.sh` directly and fill the endpoint address and key. The minimal shared configuration is:

```text
BASE_URL="https://your-api"
API_KEY="your-key"
```

Every `run.sh` automatically loads `release_code/api_config.sh`; no shell exports or file copies are required. The file also provides optional workflow-specific key fields:

| Workflow | Preferred variable | Fallback variables |
| --- | --- | --- |
| 01 | `QUERY_API_KEY` | `API_KEY` |
| 02 | `QUERY_API_KEY` | `API_KEY` |
| 03 | `SKILL_API_KEY` | `API_KEY` |



## 🎨 1. Website-Derived Visual Skill Construction

This stage derives Goal Queries from source websites, converts observable behavior into Experience and Interaction Descriptions, and constructs transferable Visual Skills as `SKILL.md` documents. Query derivation and Skill construction run Claude Agent SDK in `acceptEdits` permission mode so the model can inspect the source tree inside an isolated runtime directory.

### File tree and responsibilities

```text
01_website_derived_visual_skill_construction/
├── run.sh                       # Runs steps 01 -> 02 -> 03
├── 01_derive_goal_queries.py                 # Derives multi-level Goal Queries from each source website
├── 02_synthesize_visual_skill_candidates.py  # Derives Experience/Interaction Descriptions and Skill candidates
├── 03_construct_visual_skills.py             # Re-inspects source evidence and writes transferable SKILL.md files
├── observable_behavior_contract.py           # Defines the observable-behavior schema
├── common_model_api.py                       # Shared retry, response parsing, and model-call utilities
├── common_messages_client.py                 # Minimal Messages-compatible HTTP client
└── common_io.py                              # JSONL, atomic-write, and filesystem utilities
```


### Step 1: Configure the API and Claude CLI

Fill these fields in the root `api_config.sh` created in Step 0.2.


### Step 2: Prepare source projects

Place each visual website source project in its own child directory. A stable numeric prefix is recommended:

```text
/path/to/visual_sources/
├── 0001-example-a/
│   ├── package.json
│   └── src/
├── 0002-example-b/
│   ├── index.html
│   └── assets/
└── ...
```
### Step 3: Run the complete construction pipeline

```bash
bash 01_website_derived_visual_skill_construction/run.sh \
  --source-dir /path/to/visual_sources \
  --output-dir /path/to/run_01 \
  --query-concurrency 8 \
  --distill-concurrency 8 \
  --skill-concurrency 4
```

The pipeline executes:

1. `01_derive_goal_queries.py`: source inspection and Goal Query derivation.
2. `02_synthesize_visual_skill_candidates.py`: observable-behavior synthesis into Experience and Interaction Descriptions.
3. `03_construct_visual_skills.py`: source-grounded Visual Skill construction.

Use `--limit N` to process a subset of the source projects.


### Main outputs

```text
/path/to/run_01/
├── queries/
│   └── queries.jsonl
├── distillation/
│   └── distillation/
│       └── distillations.jsonl
├── skills/
│   ├── skill_index.jsonl
│   └── .../SKILL.md
└── sdk/                         # Isolated Claude SDK/CLI runtime homes
```

- `queries.jsonl` contains source-grounded, multi-level website requirements.
- `distillations.jsonl` contains normalized Goals, visual Elements, and Interactions.
- `skill_index.jsonl` indexes generated Skills and their source provenance.
- Each generated `SKILL.md` is the reusable artifact consumed when building a Skill library.

## 🧬 2. Query-Induced Joint Skill DAG Evolution

This stage accepts the Initial Visual Skill Library, constructs the Initial Skill DAG, generates Skill-conditioned Potential Queries, selects representative queries with per-Skill GMMs, and performs one routing-diagnosis-edit evolution round.

### File tree and responsibilities

```text
02_query_induced_joint_skill_dag_evolution/
├── run.sh                         # Orchestrates one complete query-induced DAG evolution round
├── 01_construct_initial_skill_dag.py                 # Serially integrates Skills into the Initial Skill DAG
├── 02_generate_skill_conditioned_potential_queries.py# Generates Potential Queries conditioned on each Skill
├── 03_cluster_potential_queries.py                   # Embeds queries and fits per-Skill GMM components
├── 04_select_representative_queries.py               # Selects representative queries near component means
├── 05_multi_path_route_skills.py                     # Applies the shared Router and Reviewer to representative queries
├── 06_diagnose_routing_and_applicability.py          # Diagnoses routing and Skill-applicability failures
├── 07_edit_skill_dag_structure.py                    # Splits routing nodes and rewires DAG structure
├── 08_complete_routing_partitions.py                 # Completes newly created routing partitions
├── 09_edit_visual_skills.py                          # Applies evidence-backed Visual Skill edits
├── embedding_encoder.py                              # Local embedding encoder used by step 03
├── potential_query_transport.py                      # OpenAI-compatible transport for Potential Query generation
├── common_checkpoint_io.py                           # Graph/Skill checkpoint and atomic I/O
└── common_openai_api.py                              # Shared OpenAI-compatible request, retry, and parsing utilities
```

### Step 1: Configure the API

Fill these fields in the root `api_config.sh`:

```text
BASE_URL="https://your-api/v1/chat/completions"
QUERY_API_KEY="your-key"
```

### Step 2: Prepare the initial 864-Skill JSON

The initial library is already included in the repository:

```text
../initial_visual_skill_library/initial_visual_skill_library.json
```
The constructor accepts either a flat `skills` collection or the included
Category-grouped library. Category-grouped input is flattened to unique Skills
before the Initial DAG is reconstructed. Flat input has this conceptual shape:

```json
{
  "skills": [
    {
      "name": "visual_skill_name",
      "description": "Reusable visual implementation knowledge"
    }
  ]
}
```


### Step 3: Run DAG evolution

```bash
bash 02_query_induced_joint_skill_dag_evolution/run.sh \
  --library ../initial_visual_skill_library/initial_visual_skill_library.json \
  --output-dir /path/to/run_02 \
  --embedding-encoder /path/to/embedding-encoder \
  --concurrency 8
```

| Parameter | Meaning |
| --- | --- |
| `--library FILE` | Initial Skill library JSON. |
| `--output-dir DIR` | Directory for checkpoints and intermediate outputs. |
| `--base-url URL` | API endpoint; defaults to `BASE_URL` in `api_config.sh`. |
| `--embedding-encoder PATH` | Local encoder used to embed Potential Queries for GMM clustering and medoid selection; defaults to `EMBEDDING_ENCODER` in `api_config.sh`. |
| `--embedding-device DEVICE` | Encoder device, default `cuda:0`. |
| `--potential-queries FILE` | Reuse an existing representative Potential-Query JSONL and skip Potential Query generation, embedding, and medoid selection. |
| `--query-limit N` | Process only the first `N` representative Potential Queries in routing and later stages; `0` means all. |
| `--concurrency N` | Number of concurrent API requests after Initial DAG construction. Initial construction remains serial because each Skill consumes the Category index produced by the preceding Skills. |
| `--top-k N` | Number of reviewed Skills retained; default `10`. Use the same value in stage 03 for controlled comparisons. |
| `--review-batch-size N` | Number of complete Skill documents per Reviewer call; default `8`. Use the same value in stage 03. |
| `--stop-after STAGE` | Stop after `c0`, `queries`, `retrieval`, `diagnosis`, `structural`, `completion`, or `final`; default `final`. |

The pipeline first constructs C0 by integrating one Visual Skill at a time into the evolving root Category index. Each decision may select multiple matching Categories, broaden an existing Category description without changing its identity, and add at most one Category for an unmatched salient role. It then selects representative Potential Queries and runs routing, diagnosis, structural editing, partition completion, and Skill editing in order. Retrieval uses the repository-level `dag_retrieval.py`: recursive multi-path routing first, then applicability review over every routed candidate's complete Skill document. The same implementation and prompts are reused in stage 03; the Initial and evolved DAGs do not have separate retrieval mechanisms.


### Main outputs

```text
/path/to/run_02/
├── c0/                          # Serially constructed Initial checkpoint
│   ├── construction_records/    # Per-Skill inputs, decisions, and trajectories
│   ├── construction_history.jsonl
│   ├── graph.json
│   └── skills.jsonl
├── potential_queries/
│   └── potential_queries.jsonl
├── pqr/                         # Embeddings, GMM means, and offsets
├── representative_potential_queries/
│   └── representative_potential_queries.jsonl
├── retrieval/
│   └── retrieval_results.jsonl
├── diagnosis/
│   └── diagnosis_results.jsonl
├── structural/
│   └── checkpoint/
├── completion/
│   └── checkpoint/
└── final/
    └── checkpoint/
        ├── graph.json
        └── skills.jsonl
```

The reusable output of this stage is `final/checkpoint/`. Pass that directory directly to stage 03.

## 🚀 3. Plug-and-Play Website Query Production

This stage applies an evolved recursive Skill DAG to raw website requests. It materializes the checkpoint, uses the same recursive multi-path Router and full-document Reviewer as stage 02, retains the reviewed top-K Skills, fuses them into an expanded query, and checks consistency.

### File tree and responsibilities

```text
03_plug_and_play_website_query_production/
├── run.sh                         # Executes steps 01 -> 02
├── 01_materialize_checkpoint.py  # Converts checkpoint topology and Skills into runtime hierarchy files
├── 02_expand_queries.py          # Shared DAG retrieval, Skill fusion, and consistency checking
├── common_model_api.py           # OpenAI-compatible API transport, retry, and response utilities
└── common_io.py                  # Query-expansion JSONL and hierarchy I/O utilities
```

### Step 1: Configure the API

Fill these fields in the root `api_config.sh`:

```text
BASE_URL="https://your-messages-api"
SKILL_API_KEY="your-key"
```

### Step 2: Prepare the final DAG checkpoint

The final DAG checkpoint used by the released query-production pipeline is already included at:

```text
../final_evolved_skill_dag/
├── graph.json
└── skills.jsonl
```

The checkpoint directory must contain both files below:

```text
/path/to/final_checkpoint/
├── graph.json
└── skills.jsonl
```

You can use stage 02's `/path/to/run_02/final/checkpoint` directly.

### Step 3: Prepare raw query JSONL

Each non-empty line must contain a unique `data_id` and a non-empty `query`:

```json
{"data_id":"example-0001","query":"Design a calm editorial portfolio for an architect."}
{"data_id":"example-0002","query":"Create a playful product landing page for a music app."}
```

Benchmark names and benchmark-specific fields are not required. The stage consumes a generic query JSONL contract.

### Step 4: Expand queries

```bash
bash 03_plug_and_play_website_query_production/run.sh \
  --checkpoint ../final_evolved_skill_dag \
  --input-jsonl /path/to/queries.jsonl \
  --output-dir /path/to/run_03 \
  --concurrency 8 \
  --top-k 10 \
  --review-batch-size 8
```

For sharded or partial runs, use:

```bash
--start 0 --limit 10
```

`--limit 0` means all remaining cases. `--top-k` and `--review-batch-size` have the same defaults and meanings as stage 02. Concurrency is applied consistently to cases, routing Categories, Skill review, expansion, consistency checks, and model-call pools.


### Main outputs

```text
/path/to/run_03/
├── dag_hierarchy/                # Materialized runtime DAG
├── expanded_queries.jsonl        # Primary final artifact
└── ...                           # Routing, selection, review, and resume traces
```

`expanded_queries.jsonl` is the end product of the public release pipeline.
