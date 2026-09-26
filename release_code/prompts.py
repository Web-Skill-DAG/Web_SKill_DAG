"""Central model-facing prompts for all three WebViSE workflows.

Prompt text lives here; workflow scripts import the constants they use.
"""

# Workflow 01

DERIVE_GOAL_QUERIES_PROMPT = r"""
Construct natural-language website requests from one provided website implementation and its observable behavior.

Working directory: {cwd}

Use only the supplied implementation and evidence. Identify the active behavior that establishes page purpose, content objects, user actions, maintained state, animation, transitions, rendering, and visible outcomes. Do not infer behavior from titles, tags, dependency lists, comments, or other metadata alone. If the supplied evidence does not establish a mechanism, omit it.

Do not describe the supplied evidence or extract a Web Skill. Use the evidence internally, then synthesize three incremental request segments: G, E, and I. Store them separately without duplicated earlier text so they can be concatenated as G, G+E, or G+E+I.

Except for G, a segment may contain multiple sentences when needed for adequate coverage. G is deliberately a terse, low-information user request. Every segment must add only the information assigned to its level and must not repeat, paraphrase, summarize, or contradict earlier segments.

Before writing, identify one dominant user task and its shortest complete experience/interaction path. Use that path as the backbone of all three segments. Omit secondary modes, alternate layouts/routes, autoplay, settings, optional ambience controls, and decorative micro-interactions unless they are indispensable to completing that dominant task or materially define the exhibit's central identity.

Critical separation rules:
- Write requests in natural user/product language, not source-analysis language.
- Do not copy the exhibit title or mention its author, brand, source identity, URLs, implementation symbols, CSS properties, shader variable names, equations, or source constants.
- G, E, and I must not name libraries, frameworks, APIs, rendering technologies, or other implementation labels.
- Do not name an inferred Skill, Skill, or taxonomy label.
- Do not prescribe low-level implementation.
- A request may describe an observable interaction or state change when appropriate.
- Preserve the page goal and core experience, not every decorative effect.
- Do not invent a business purpose, target audience, or domain that the source cannot support. When the source is only an effect/demo rather than a complete product page, formulate a generic request around the actual content object and demonstrated experience; do not turn it into an agency, portfolio, commerce, editorial, or campaign site without evidence.
- The concatenated queries must read naturally as standalone user requests. Avoid references such as "same as above", "as previously described", "this page", or "the source website".

Use the following coverage rules internally. They are selective checks, not output labels and not fields that must be populated without evidence. Include an item only when it is supported by the source and relevant to the dominant path.

1. G — goal:
   - Internally check: user, use context, core content objects, page goal, primary task, and intended outcome. These checks prevent misunderstanding; they are not a list to spell out in the output. State a user/domain only when indispensable and supported; otherwise remain generic rather than inventing one.
   - Use suitability for the user's tasks to decide what is central.
   - Output exactly one short, plain sentence or natural request fragment, normally about 5–25 English words. It should sound like the least-effort request a real person might type: directly name the wanted page/product and its single central purpose, then stop.
   - Prefer ordinary forms such as a bare noun phrase, "make/build/create/design ...", or a brief first-person request. Vary the surface form naturally across records. Do not make every request polished, explanatory, or complete like a product brief.
   - Do not enumerate all content fields, controls, menu destinations, secondary tasks, reasons, or expected benefits. Avoid explanatory padding such as "its only purpose is", "the aim is", "so visitors can", "coming away with", or contrasts describing what the page is not. Include a second clause only when the core task would otherwise be ambiguous.
   - Do not include or contrast layouts, spatial arrangements, visual effects, navigation behavior, movement, animation, or implementation details. Those belong only in later deltas.
   - Write G as a plain functional request from someone who has stated only the minimum desired artifact or task and has not chosen the experience design. Words such as immersive, interactive, full-screen, 3D, animated, spatial, floating, scrolling, dragging, hovering, looping, grid, or route normally indicate that information has leaked from a later level and must be removed.
   - G may draw only from the supported user/context, core objects, central task, and functional result, but it need not state all of them. A brief, broad quality word such as playful, calm, or atmospheric is allowed when it is a natural part of the user's minimal request and is supported by the source. Do not expand that word into a detailed experience description or stack several promotional adjectives; richer experience progression belongs in E.
   - A valid G for a gallery says that visitors should browse and appreciate a collection; it does not yet say that the collection floats in 3D, follows a route, or replaces a flat grid.

2. E — experience delta:
   - Check: dominant user path; changes in presented information and user attention; intended experience qualities; and the completion, exit, or return state when one exists.
   - Add stages such as entering, orienting, exploring, focusing, comparing, completing, or returning, but only when supported by the implementation.
   - Where evidenced, cover self-descriptiveness, conformity with user expectations, learnability, or user engagement.
   - Describe what the experience should feel like or make understandable, without describing input-response mechanics.
   - Do not mention device inputs, named controls, selectable modes, alternative layouts/routes, autoplay, sound toggles, settings, or other optional branches. Experience is the perceived progression of the dominant path, not a feature inventory.

3. I — interaction delta:
   - Check: primary input; affected object; state change; visible feedback; controllability; and reversal, interruption, or recovery when supported and relevant.
   - Express the complete causal chain from user action to visible response without naming the underlying algorithm or technology.
   - Where important, cover controllability and robustness to use error, including reversal, interruption, boundaries, or recovery.
   - Include only actions and responses forming the shortest complete dominant interaction path. Multiple related actions are allowed when the task inherently requires navigation, selection, inspection, and return, but omit autoplay, settings panels, layout/style/route variants, secondary controls, and decorative responses.

For G, write exactly one terse sentence or natural fragment, normally about 5–25 English words. For E and I, normally write two or three concise sentences and roughly 40–70 English words per segment; this is a soft target, not a reason to omit essential information.

Write exactly one UTF-8 JSON object to ./query_segments.json. Do not wrap it in Markdown. Use this schema:
{{
  "segments": [
    {{"level": "goal", "text": "..."}},
    {{"level": "experience", "text": "..."}},
    {{"level": "interaction", "text": "..."}}
  ]
}}

Validate that the file parses as JSON, has all three levels exactly once, and contains nonempty text. Write no other result files. After validation, reply with exactly DONE.

Before finishing, reopen and revise the file until all checks pass:
- G expresses only the minimum central artifact/task in one lazy, plain human utterance; the applicable Goal categories were checked internally rather than enumerated. A concise source-supported quality word is acceptable, but no detailed interaction, spatial-layout, motion, or mechanism information may leak into it.
- E adds only path, information/attention progression, experience qualities, and completion/return information; it contains no input-response description.
- I adds the primary input-to-visible-response chain and applicable control/reversal/recovery behavior, without optional controls.
- No segment repeats or paraphrases information already stated by an earlier segment.
- Concatenating the segments in order produces natural standalone requests.
""".strip()


CONSTRUCT_VISUAL_SKILLS_PROMPT = r"""
Create reusable Web Skills for one already-selected website Skill by inspecting its implementation source.

Working directory: {cwd}

Use the supplied selected Skill as the experience boundary: extract only mechanisms that materially implement it. Inspect the supplied website source project directly. Treat metadata and dependency names only as navigation hints, never as evidence that a mechanism is active.

Write all final artifacts under ./output.

## Objective

Turn source-confirmed implementation knowledge into one or more portable Skills applicable to an unrelated website Query. Preserve the causal mechanisms needed to realize the Skill while removing the original subject, brand, assets, copy, routes, repository structure and application shell.

Skill granularity is not fixed. Prefer one coherent Skill when mechanisms share authoritative state, coordinate systems, timing, update order, rendering output or a transition lifecycle. Split only when each resulting mechanism is independently usable, independently triggered and still meaningful after the others are removed. Do not atomize one coordinated experience into small effects merely because its implementation has multiple modules. Do not bundle unrelated decoration that does not materially implement the selected Skill.

Do not promote generic collection routing, back navigation, loading and unsupported-environment notices, error handling, cleanup, ordinary controls or application-shell plumbing into standalone Skills. They may appear only as constraints inside a defining visual mechanism when required for that mechanism to remain usable. A fallback or ordinary interface behavior is a Skill only when the Skill's name and description make that behavior itself the distinctive designed experience; its presence in secondary requirement fields is insufficient.

Do not force compatibility with an existing Skill library. Generate the representation warranted by this Skill and source.

## Source analysis

Recursively inspect the active source. Trace entry points and relevant imports. Establish, from active code rather than filenames or comments:

- the mechanism goal and affected visual/content objects;
- inputs, autonomous drivers and initialization state;
- authoritative state and derived state;
- update, transition, simulation or rendering rules;
- coordinate conversions and synchronization boundaries;
- meaningful update order and lifecycle ownership;
- visible outputs and feedback;
- responsive behavior, performance constraints and cleanup;
- constants, equations, thresholds and timing relationships that materially define behavior.

Runtime inspection is optional. Screenshots are not required. Do not claim behavior that source relationships cannot establish.

## Generalization boundary

Write each Skill for future unrelated content. The Skill must explain how to implement the transferable mechanism, not how to reproduce this website.

- Generalize subject matter, copy, branding, route names, asset filenames, exact item content and incidental styling.
- Preserve a content type only when the mechanism structurally depends on it.
- Preserve specialized runtime libraries or low-level systems only when their active role materially determines the mechanism. Explain the role; never emit a dependency list.
- Omit application frameworks, component organization, rendering-delivery modes, build tools and source module structure. Do not prescribe React, Vue, Svelte, Angular, Astro, native-DOM architecture, JSX/TSX, SSR/CSR/SSG, Vite, Webpack or Parcel.
- Retain implementation-critical systems such as Three.js, GSAP, Lenis, WebGL/WebGPU, GLSL/WGSL, GPU instancing, shaders, physics, raycasting, animation mixers, video textures or post-processing only when verified and causally useful.
- Prefer parameter relationships and observable invariants over blindly copying source constants. Preserve exact values only when changing them would materially alter identity, correctness or synchronization.
- Do not mention the source, exhibit, repository, article, author, data ID, Skill ID or inspection process inside SKILL.md.

## SKILL.md contract

For every Skill, write `./output/skills/<name>/SKILL.md`.

The YAML frontmatter must contain exactly:

---
name: <lowercase kebab-case, at most 64 characters>
description: <one concise sentence stating when to use the Skill and the distinctive observable result>
---

Use imperative instructions. Keep the body focused on non-obvious implementation knowledge and below 500 lines. Use exactly these top-level sections after one H1 title:

- `## Use this skill when`
- `## Core mechanism`
- `## Implementation workflow`
- `## State and update model`
- `## Constraints and invariants`
- `## Adaptation points`
- `## Validation`

The body must be sufficiently operational without access to the source. Include equations, pseudocode, state transitions, coordinate relationships and update order only when they improve faithful implementation. State visible validation criteria, common causal failure modes and what may vary without changing the Skill.

Do not include provenance, source paths, original names, evaluation commentary, generic web advice, installation steps, README material or a reconstruction narrative.

## Manifest and evidence

Write `./output/skill_manifest.json` using exactly:

{{
  "decomposition_rationale": "why these Skills are the smallest coherent transferable units",
  "skills": [
    {{
      "name": "same frontmatter name",
      "description": "same frontmatter description",
      "relative_path": "skills/<name>/SKILL.md",
      "skill_contribution": "which defining part of the supplied Skill candidate this generated Skill implements",
      "mechanism_scope": ["coherent responsibilities kept together in this Skill"],
      "source_evidence": [
        {{"path": "relative source path", "evidence": "active code relationship establishing the mechanism"}}
      ]
    }}
  ]
}}

The manifest is provenance and may cite source paths. SKILL.md must remain source-neutral. Emit at least one Skill because the supplied Skill candidate was selected as an implemented experience, but do not target any particular Skill count.

Before finishing, validate every path, frontmatter pair and required section; confirm each Skill is source-grounded, jointly covers the selected Skill candidate's defining mechanism, and remains useful for unrelated website content. Reply with exactly DONE after the files are valid.
""".strip()


DISTILL_PROMPT = r"""
Extract query-friendly Experience Category candidates and concrete website Skill candidates.

The input included below contains one website's Goal, Experience and Interaction Queries. Source identity, source code and Skill library are intentionally unavailable.

Use all three Query levels with different responsibilities. Goal supplies the user's communication purpose, foreground content and plausible website-request contexts for Category retrieval and `Fits ...`. Experience supplies scope, composition, attention, progression and experiential qualities. Interaction supplies inputs, affected objects, state changes, visible feedback, boundaries and recovery. Goal context must not become a Category or Skill identity merely because it names a domain or page purpose. Abstract source-specific nouns after using them to understand legitimate applicability.

Two abstraction levels are required:

1. Experience Category answers: "What broad kind of impressive website effect is this useful for?" It is a coarse retrieval class a user with a short request could plausibly understand. Categories group by the primary content/use role, not by exact input, geometry, library or algorithm. A Category must be broad enough to contain many visibly different Skills across unrelated websites. Infer Category names from the supplied case rather than from a presumed taxonomy.

2. Skill answers: "What specific recognizable visual experience is offered inside that category?" A Skill is concrete and vivid enough to choose as a design direction, but it is not an implementation specification. Its complete document must preserve the visible composition, content roles, attention flow, meaningful experience progression when one exists, input-state-feedback chains, completion/return behavior, experience-defining constraints and portability boundary supported by Experience/Interaction. Infer its identity from the supplied case. Omit algorithms, libraries and ordinary components.

Visual-design eligibility is a hard gate. Emit a Skill only when the input states a concrete transferable visible treatment, transformation, spatial presentation, animation, reveal or interaction-to-visible-feedback relationship. Ordinary page structure, content organization, navigation, business flow, standard control behavior, loading or fallback behavior, implementation plumbing, and preservation of input, focus or other functional state are not visual Skills. An unnamed or unspecified visual-treatment slot is also insufficient: configurability and continuity may constrain a concrete effect but cannot replace the missing effect. Never invent an effect to ensure non-empty output.

If the input contains no eligible visual experience, use exactly this exclusion marker and emit no Category or Skill content:
{{
  "case_experience_summary": "NO_ELIGIBLE_VISUAL_SKILL",
  "category_candidates": [],
  "skill_candidates": []
}}

Use the smallest sufficient Category set. A cohesive experience normally needs one primary Category; add another only when the same complete Skill is genuinely useful under two distinct broad requests. Properties of mood, depth, duration, input, rendering, scale or inspection are normally Skill attributes rather than Categories. A foreground experience is not a background category unless it can actually function as a visual substrate behind foreground content. Domain and page-purpose labels are Goal types, not Experience Categories.

Do not use internal engineering, control-state or architecture abstractions as user-facing Category or Skill identities. Do not elevate ordinary interface support, readiness/status behavior or isolated implementation mechanisms into Skills. Keep coordinated content, visual layers, navigation, motion and feedback together when their relationship creates one recognizable experience. One cohesive experience should normally yield one complete Skill rather than fragments for each mechanism. Emit multiple Skills only for independently deployable experience regions or modes that remain recognizable when separated.

Remove brands, names, industries, source identity and incidental copy. Preserve content type only when it determines reuse. A single Skill may belong to multiple Categories. Do not pad Categories or split a complete experience into feature fragments. Every Category description must contain both a concise definition and a positive applicability sentence beginning with `Fits ...`. That sentence must name concrete, transferable website-request contexts in terms of communication purpose, foreground-content role or density, and placement in the experience. Do not satisfy it by merely repeating the Category's media object, visual material or mechanism. Do not add negative `Not for` wording. Keep Skill name and description concise for retrieval, but make the remaining Skill fields complete enough to guide implementation after selection. Do not replace missing evidence with generic web-design advice.

Each Category candidate must contain exactly two fields: `name` and `description`. Put every useful Category-level definition, applicability qualification, retrieval cue, clarification or other explanation into `description`; never create an additional field. Category–Skill membership is represented only from the Skill side through `skill_candidates[*].category_candidate_names`. Every emitted Category name must occur in at least one such list.

Use exactly:
{{
  "case_experience_summary": "short user-facing summary",
  "category_candidates": [
    {{"name": "lowercase_snake_case", "description": "broad use-oriented definition. Fits concrete transferable website-request contexts described by communication purpose, foreground content and placement."}}
  ],
  "skill_candidates": [
    {{
      "name": "lowercase_snake_case",
      "description": "specific recognizable visual experience in user-facing language",
      "experience_scope": "where this experience begins and ends and whether it is a region or whole-view experience",
      "content_and_composition": ["content roles, cardinality, spatial/layer relationships and persistent elements"],
      "experience_progression": ["meaningful ordered stages, continuous advance, looping or return; empty when the experience has no real progression"],
      "attention_and_information_flow": ["how prominence and revealed information change"],
      "interaction_model": [
        {{
          "input": "user input or autonomous driver",
          "affected_object": "content or visual role being changed",
          "state_change": "observable state transition",
          "visible_feedback": "what the user sees",
          "control_and_recovery": "reversal, interruption, resumption, recovery or boundary behavior"
        }}
      ],
      "experience_qualities": ["observable experiential qualities"],
      "completion_and_return": ["completion, looping, reset, return or persistent-state behavior"],
      "locked_observable_requirements": ["requirements that define this Skill and must survive transfer"],
      "adaptable_details": ["details that may change without changing Skill identity"],
      "portability_boundary": "what kinds of content/context fit and what change would create a different Skill",
      "category_candidate_names": ["category_name"]
    }}
  ]
}}

When candidates are emitted, every emitted Category name must be referenced by at least one Skill through `skill_candidates[*].category_candidate_names`, and every Skill must reference at least one emitted Category. The exclusion marker must be used only with both candidate lists empty. Return only the JSON object, without Markdown fences or commentary.
""".strip()


# Workflow 02

INITIAL_SKILL_DAG_CONSTRUCTION_PROMPT = r"""
Integrate one incoming Visual Skill into the current root Category index and update the one-level Initial Skill DAG.

The input contains one complete Visual Skill and the complete active Category index built from earlier Skills. Read the complete Skill, identify its independently central visual roles, and route it by broad query-facing purpose. A Category is a stable first-hop routing concept shared by many visibly different Skills; it is not an exact mechanism, implementation technology, source, page-industry label, mood, layout, input device, or isolated effect property.

Assign the Skill to every existing Category whose description genuinely covers an independently central role. Multiple memberships are valid when removing any selected role would materially change the kinds of user Queries the Skill can serve. Do not select multiple Categories for incidental objects, phases, or different properties of one coherent role.

Prefer existing Categories over index growth. If an existing Category has the correct identity but its current description is accidentally narrow, revise only its description into a broader positive query-facing definition without changing its name or identity. Do not broaden a Category across distinct affected-content families or visible functions, and do not merge, split, or rename existing Categories during Initial DAG construction.

If the most salient central role is not covered by any existing Category, add at most one new Category for that unmatched role. The new Category must express a broad affected-content family and visible function that will remain useful for routing substantially different future Skills. A new mechanism, medium, geometry, interaction, animation, subject, or visual style alone does not justify a new Category. When the index is empty, create the first Category from the incoming Skill's most salient broad visual role.

Every added or revised Category description must contain a concise definition followed by a positive applicability sentence beginning with `Fits ...`. Remove source identity, provenance, brands, implementation details, exact assets, and incidental subject matter. Keep concrete implementation knowledge in the Skill rather than copying it into Category identity.

Return exactly one JSON object:
{{
  "routes": [
    {{
      "operation": "select|revise",
      "category_name": "exact existing Category name",
      "revised_category": null
    }}
  ],
  "new_category": null,
  "rationale": "brief explanation of the central roles, matched Categories, and any unmatched salient role"
}}

For `select`, preserve the existing Category and set `revised_category` to null.
For `revise`, preserve `category_name` and provide:
{{"name":"the exact same existing Category name","description":"broadened definition. Fits transferable website-request contexts."}}

When one unmatched salient role requires a new Category, replace `new_category` with:
{{"name":"lowercase_snake_case","description":"broad definition. Fits transferable website-request contexts."}}

`routes` may be empty when no existing Category matches, but `new_category` must then create the Skill's membership. When the index is empty, `new_category` must create the first Category. Every result must assign the Skill through at least one existing or new Category. Use only supplied existing Category names, do not repeat a Category, and add no more than one new Category. Return only the JSON object without Markdown fences or commentary.
""".strip()


GENERATE_POTENTIAL_QUERIES_PROMPT = """Generate exactly 50 diverse Goal-level English Potential Queries for one visual Web Skill.

These queries will be used only to build an offline retrieval index. Infer plausible user goals from the supplied Skill name and description. Do not consult any held-out query, retrieval result, relevance label, or external library.

Requirements:
- Return exactly one JSON object: {{"potential_queries":["...", "...", ...]}}.
- The array must contain exactly 50 unique non-empty English strings.
- Each string must sound like a natural request from someone asking for a website or web experience, normally 4-35 words and never more than 50 words.
- Express the desired outcome, experience, content presentation, or user task. Do not explain the Skill and do not mention that a Skill is being retrieved.
- Do not copy the Skill name verbatim. Avoid library/framework names, implementation instructions, source filenames, numeric parameters, and internal mechanism terminology unless the user-facing concept is indispensable.
- Keep each query focused on one primary goal; avoid feature inventories and mini-PRDs.
- Make the set genuinely diverse, not 50 superficial synonym substitutions. Cover a balanced mixture of terse requests, outcome-oriented requests, audience/context variants, experience-oriented requests, and plausible page/product contexts.
- Do not number the strings and do not include Markdown or commentary.

Skill name: {name}
Skill description: {description}
"""


DAG_ROUTER_PROMPT = r"""
Route one website Query through the supplied candidates at one level of a Skill DAG.

Select every node whose query-facing definition can materially support the requested visible experience, interaction, content presentation, or task. Preserve multiple plausible paths when the Query leaves the concrete visual mechanism open. Exclude nodes supported only by topic, mood, implementation stack, generic page type, or as insurance. Do not impose a selection quota. An empty ranking is valid when no candidate applies.

Use exactly:
{"ranking":[{"name":"exact supplied node name","reason":"brief Query-grounded reason","confidence":"high|medium|low"}]}

Use supplied names exactly and at most once. Rank selected nodes by applicability and return only the JSON object.
""".strip()


SKILL_APPLICABILITY_REVIEW_PROMPT = r"""
Review every supplied complete Visual Skill document for one website Query.

Judge the reusable visible mechanism in the complete document against the Query's requested content, object, state, task, visual outcome, and interaction outcome. A Skill is applicable when at least one non-trivial, recognizable mechanism can operate on the Query's existing subject or task without inventing unrelated business meaning. Ignore incidental source branding, copy, assets, frameworks, and surrounding workflow. Reject shared topic, mood, page type, routine polish, or mere technical insertability.

Decide every supplied Skill exactly once. Give an integer applicability score from 0 to 100; scores compare direct usefulness across candidates, not visual quality in isolation.

Use exactly:
{"decisions":[{"name":"exact supplied Skill name","applicable":true,"score":0,"reason":"brief Query-grounded applicability reason"}]}

Preserve supplied names and return only the JSON object.
""".strip()


QUERY_EXPANSION_PROMPT = r"""
Write the visual-effects requirements to append to one website-development Query using only the supplied, already-reviewed Visual Skill documents.

The original Query remains authoritative. Transfer only compatible observable composition, motion, interaction, state change, and feedback from the supplied Skills. Adapt mechanisms to the Query's existing subject and task; omit source branding, copy, assets, frameworks, and unrelated workflow. Combine complementary mechanisms coherently and avoid redundant demonstrations or one-section-per-Skill assembly. Do not mention Skills, retrieval, sources, or this review.

Return exactly one JSON object:
{"query":"visual-effects requirements section only"}

Use the original Query's natural language. Return only the JSON object.
""".strip()


DIAGNOSE_ROUTING_PROMPT = """Diagnose one observed retrieval trajectory from a current Skill-DAG checkpoint.

The source Skill is an observed positive because the Goal Query was generated from that Skill,
but source identity is not proof that it is equivalent to another Skill. Make only sparse,
future-query-valid edits.

Miss attribution:
- none: source already ranks within the supplied top-K retention boundary and no retrieval repair is needed.
- rewrite_skill: the source mechanism genuinely supports the Query, but its current short
  retrieval description lacks the ordinary query-facing bridge. Preserve its mechanism.
- rewrite_node: the source is already attached to the right node, but that node wording caused
  the router to miss it. Keep the node broad and coherent.
- add_edge: the source independently belongs to an existing node but lacks that membership.
  Never add an edge merely to rescue this one Query.

Respect the observed pipeline boundary. If the source is absent from the routed candidate set,
the failure is `root_route` and only rewrite_node/add_edge/none can repair it because Skill text
was never read. If the source is in the candidate set but outside the supplied top-K retention
boundary, the failure is `candidate_rank` and only rewrite_skill/none can repair it. A retained
source requires none/none.

Within the supplied co-retrieved batch emit only strong relations:
- same: the two Skills express the same reusable visible mechanism; adaptable details are the
  only differences. This can support merge after full-document verification.
- alternative_same_position: materially different mechanisms that can independently occupy the
  same Query-grounded visual/function position. Keep the Skills distinct. When they coexist
  under an over-broad parent, provide that parent and a concise routing distinction; repeated
  evidence can trigger split = remove old parent-to-Skill edges + add narrower edges.
- omit weak/topic/style/coexistence relationships.

Return exactly JSON:
{
  "miss_attribution": {
    "stage": "none|root_route|candidate_rank",
    "operation": "none|rewrite_skill|rewrite_node|add_edge",
    "target_name": "exact supplied Skill or node name, or empty string",
    "replacement_description": "complete replacement only for rewrite operations, else empty string",
    "reason": "brief evidence-grounded reason"
  },
  "relations": [
    {
      "a": "exact supplied Skill name",
      "b": "exact supplied Skill name",
      "relation": "same|alternative_same_position",
      "reason": "mechanism-level reason",
      "split_parent": "exact supplied common parent for alternative relation, else empty string",
      "routing_distinction": "query-facing distinction for alternative relation, else empty string"
    }
  ]
}
Use exact supplied names. JSON only."""


SKILL_DAG_REVISION_PROMPT = """Produce a sparse query-induced revision for one current Skill-DAG parent node.
The input contains repeated evidence collected only from representative Potential Queries after running the
current checkpoint. Do not invent evidence and do not edit merely to rescue one Query.

Allowed decisions:
- Rewrite a Skill retrieval description only when repeated miss evidence shows that its concrete
  mechanism supports ordinary Goal wording that the current description fails to expose.
- Rewrite the parent description only when repeated root-route misses expose one coherent missing
  fit cue. Do not turn it into a catch-all.
- Add a parent-to-Skill membership only when repeated evidence shows the complete Skill
  independently belongs under this parent for future Queries. Concretely, an `add_edges` item is
  allowed only when `repeated_miss_evidence` contains `operation="add_edge"` for that exact Skill
  and current parent. A `same` relation alone never authorizes a new membership.
- Merge only a `same` pair supported by repeated independent Queries from both source directions.
  Same page type, mood, medium or rough purpose is insufficient.
- Split when repeated `alternative_same_position` evidence shows the parent mixes stable,
  query-distinguishable mechanisms. The alternative relation is evidence that the CURRENT PARENT
  is too broad; it is not a pairwise cannot-link constraint. Repartition the whole supplied set into
  small reusable mechanism families. Skills belong in the same family when they share the same
  causal kernel: trigger, controlled state, affected object, visible feedback, temporal progression,
  interruption/recovery rule, and portability boundary. Differences in item count, copy, imagery,
  page scale, optional flourish, or list-versus-single-item presentation do not by themselves justify
  separate families. Use 2-8 coherent children; never create one child per Skill.
- For a Skill already directly attached to this parent, a split removes that broad membership and
  adds the narrower child membership. A supplied evidence-linked Skill currently attached elsewhere
  may also enter a child family; keep its other valid memberships because the DAG is multi-parent.

The input includes `split_required`, `minimum_split_move_count`, and
`split_candidate_skill_names`. When `split_required` is true, return exactly one split, move at
least `minimum_split_move_count` listed candidates, and partition them into 2-8 coherent children.
When `splits_allowed` is false, return an empty `splits` list. This means the structural partition
has already been staged from the same diagnosis evidence and this call must only complete the
non-structural edits; it does not mean that the evidence is from a later evolution round.
This requirement is activated only after dense repeated alternative evidence; it is not permission
to invent a taxonomy. Use only the supplied Skill names/descriptions and the query-induced relation
signals. Do not infer families from source site, provenance, code, author, or implementation stack.
Before emitting children, compare all candidates jointly. Repeated `same` links should normally land
in one child even when a conservative full-document merge would retain separate Skill identities.

Prefer no edit over weak edit. Return exactly JSON:
{
  "parent_rewrite": null or {
    "replacement_description": "complete replacement",
    "evidence_query_ids": ["supplied id"]
  },
  "skill_rewrites": [{
    "skill_name": "supplied Skill",
    "replacement_description": "complete replacement",
    "evidence_query_ids": ["supplied id"]
  }],
  "add_edges": [{
    "skill_name": "supplied Skill",
    "target_node": "the current parent name",
    "evidence_query_ids": ["supplied id"]
  }],
  "merges": [{
    "keep_skill": "supplied Skill",
    "remove_skill": "supplied Skill",
    "evidence_query_ids": ["supplied id"]
  }],
  "splits": [{
    "parent_node": "the current parent name",
    "children": [{
      "name": "new unique snake_case node name",
      "description": "broad query-facing definition with a Fits sentence",
      "move_skill_names": ["supplied direct member Skill"]
    }],
    "evidence_query_ids": ["supplied id"]
  }]
}
JSON only."""


MERGE_VERIFY_PROMPT = """Verify whether two complete canonical Skills express the same reusable
visible mechanism. `same` requires mechanism-level equivalence: merging must not remove a material
behavior, progression, interaction, recovery rule, or portability boundary. Shared page type,
medium, style, mood, purpose, or being alternatives for one Query is not equivalence. Prefer false
when uncertain. If equivalent, choose the clearer/more complete canonical Skill to keep.
Return exactly JSON: {"equivalent":true|false,"keep_skill":"exact supplied name or empty string","reason":"brief mechanism-level reason"}. JSON only."""


PARTITION_ASSIGNMENT_PROMPT = """Complete one learned Skill-DAG routing partition.
The child nodes were induced from repeated representative Potential-Query evidence. For every supplied
remaining Skill, select the child node or nodes whose retrieval boundary materially covers the
Skill's complete reusable visible mechanism.

Rules:
- This is mechanism-family routing, not topical or page-form tagging. Match the causal kernel:
  trigger, controlled state, affected object, visible feedback, temporal progression,
  interruption/recovery, and portability boundary.
- Prefer a shared causal kernel over superficial form differences. Item count, copy, imagery,
  page scale, optional flourish, or list-versus-single-item presentation may be adaptable variants
  inside one family when the controlling behavior and feedback loop are the same.
- Prefer one best child. Select two only when the complete Skill independently satisfies both
  routing boundaries; shared mood, subject, page type, or implementation is insufficient.
- Use `keep_at_parent=true` only when none of the discovered children can retrieve the Skill
  without distorting its meaning. Do not use the parent as an uncertainty fallback.
- Return every supplied Skill exactly once. Do not invent or rewrite names.

Return exactly JSON:
{"assignments":[{"skill_name":"exact supplied name","child_names":["exact supplied child"],"keep_at_parent":false,"reason":"brief mechanism-level reason"}]}
JSON only."""


DEFAULT_QUERY_INSTRUCTION = (
    "Given a natural-language website request, retrieve relevant visual Web Skills "
    "that help implement the requested page or interaction"
)


# Workflow 03

CONSISTENCY_PROMPT = r"""
Review whether an appended visual-effects section is compatible with its original website-development Query.

The original Query is authoritative and remains unchanged outside this review. The appended section may add concrete visual composition, motion, interaction, state changes and feedback, but it must not restate, change or erase the original meaning.

An explicitly open creative brief is a deliberate exception to the usual no-new-subject rule only when it positively delegates the subject, concept and design direction to the creator (for example, “build whatever you want” or “express yourself without restrictions”). For that case, an invented coherent premise, content structure and many compatible spectacular effects are consistent with the original intent and should not be removed merely because the original supplied no subject. Check instead that the additions form one buildable creative work rather than unrelated demonstrations. Ordinary underspecified Queries do not receive this exception.

Treat an explicit source-fidelity constraint such as “1:1 replicate”, “exact clone”, “pixel-perfect reproduction”, or genuinely equivalent wording as authoritative. A bare request for a clone, counterpart or site in the style of a named product is not by itself an exact-fidelity constraint: it establishes the product domain, recognizable information architecture and core task pattern, while still allowing compatible authored visual mechanisms that serve those same subjects and tasks. Do not claim that an added mechanism exists in the named source, and do not change the referenced product into a different kind of experience. Reserve the strict no-invention rule for language that positively demands exact reproduction. Under that strict rule, remove unsupported additions even when they seem stylistically plausible; if no appended requirement can be retained without risking divergence from the explicitly exact source, return an empty final_query.

Check the subject or entity being presented, who owns the page, the speaker and addressee, pronoun and deictic references, intended audience, page goal, user tasks, required content and functionality, and output language. Interpret second-person references in the original as addressed to the model receiving the website-building request unless the original explicitly establishes another referent.

Do not audit or enforce programming languages, frameworks, styling systems, libraries, file formats or delivery modes in this visual-consistency pass. They belong to the unchanged original Query and must not be copied into, used to weaken, or added as qualifiers to the visual-effects section. Judge whether the visual intent is semantically compatible with the requested subject and task, not whether a particular stack can implement it. Keep `final_query` technology-agnostic and never prepend, quote or repeat the original Query.

Also check how the effects are composed. Distinct experience regions are reasonable when the Query is organised around presentation, exhibition, editorial progression or exploration. For a simulation, tool, generator or control-oriented Query, prefer effects that coherently support the core subject, shared state and task where sensible, while still allowing useful inspection, explanation, diagnostic or supporting regions. A visually sparse or compact layout does not imply few accepted effects: several Skills may be fused into one stage as complementary layers, for example subject material, motion, direct manipulation, impact feedback and atmosphere. Reject unnecessary vertical chaptering and unrelated demonstrations, not a well-integrated multi-Skill composition. Do not enforce one universal arrangement. Repair an effect when it replaces a concrete requested subject with an abstract visual object, obscures the subject's recognisable material or causal behaviour, or creates an unrelated demonstration instead of serving the Query.

This is also a completeness and usability audit. Check whether the section visibly covers an explicitly requested action or causal process; whether a dashboard, tool, editor or simulation exposes meaningful existing variables rather than freezing them; whether current/default time remains distinct from an explorable date or range; whether long overlays remain scrollable with reachable exit controls and restored focus/scroll; whether a concrete subject has been replaced by a generic metaphor; and whether an unsupported dark or low-luminance default has been introduced. These are repairable issues even when the added effect does not literally contradict a sentence in the original Query. Do not add arbitrary new product functions, but controls needed to inspect the Query's existing data, state or causal behavior are not arbitrary.

Audit colour by visual role rather than forcing one palette over the whole page. Avoiding an unsupported black, low-luminance or neon default does not require a light-neutral, achromatic, pastel or low-saturation result. Never use beige, cream, ivory, off-white, paper, earthy, warm-neutral or another named "safe" palette merely as the replacement for an unsupported dark treatment. If the original Query neither specifies a palette nor makes one necessary to the subject, do not prescribe a fixed palette at all: state only the required colour roles, contrast, legibility and distinction, and leave the concrete art direction unspecified. Preserve a named palette only when the original explicitly requests it or the subject intrinsically requires faithful colours. Treat any unsupported named palette introduced by the appended section as an inconsistency and remove or repair it, including unsupported light and warm-neutral palettes, not only dark ones. When patterns, generated artifacts, illustrations, media, data identities, materials or other visual subjects are central to the original Query, a blanket neutral-palette instruction that flattens their variety or expressive role is an unsupported restriction and a visual-completeness failure. Keep structural ground, typography and UI chrome coherent and accessible, while allowing or requiring the subject-defining visuals to use purposeful colour diversity, contrast and saturation when the original Query does not forbid it. Conversely, do not inject arbitrary colour variety when the original explicitly fixes a restrained palette. Repair an over-broad palette clause at its scope rather than replacing it with another house style.

Also audit the visual ambition of an underspecified creative surface. When a generator, creator or customizer produces an artifact whose appearance is central and the original Query does not lock one style, a single fixed treatment plus file-format choices is incomplete: require several materially different visual presets or meaningful appearance parameters, a live preview, a persistent selected state, and an exported artifact that matches the preview while preserving the artifact's validity. These are visual treatments of the requested output, not new business functions. For simulations and animation-led experiences whose art direction is not fixed, retain or minimally repair a coherent distinctive visual language through compatible material response, trajectory or trail, collision/reaction feedback, spatial depth or atmosphere; controls alone do not satisfy this audit. Do not require every possible effect and do not add a generic dark neon theme. Prefer one recognisable, coherent visual signature over a pile of unrelated decoration.

If the section is both consistent and complete, preserve it verbatim. Otherwise repair only the conflicting, missing-core, frozen-control, accessibility or usability requirement. Do not rewrite or repeat the original Query. Retain useful compatible visual mechanisms, and prefer deleting or narrowing a conflicting clause over discarding an otherwise compatible section. Lack of evidence that a compatible authored mechanism appears in a named reference is not, by itself, a reason to erase it unless the Query explicitly requires exact fidelity. When the section still contains any visual effects after repair, keep at least one suitable effect on the home view or first visible screen. Do not force an effect to survive: final_query may be the empty string only when every addition conflicts with the original intent or an explicit exact-fidelity constraint.

Use exactly:
{
  "intent_consistent": true,
  "issues": [],
  "final_query": "the unchanged or minimally repaired expanded Query, or an empty string when no addition is safe"
}

Return only the JSON object.
""".strip()


__all__ = [
    "DERIVE_GOAL_QUERIES_PROMPT",
    "CONSTRUCT_VISUAL_SKILLS_PROMPT",
    "DISTILL_PROMPT",
    "INITIAL_SKILL_DAG_CONSTRUCTION_PROMPT",
    "GENERATE_POTENTIAL_QUERIES_PROMPT",
    "DAG_ROUTER_PROMPT",
    "SKILL_APPLICABILITY_REVIEW_PROMPT",
    "QUERY_EXPANSION_PROMPT",
    "DIAGNOSE_ROUTING_PROMPT",
    "SKILL_DAG_REVISION_PROMPT",
    "MERGE_VERIFY_PROMPT",
    "PARTITION_ASSIGNMENT_PROMPT",
    "DEFAULT_QUERY_INSTRUCTION",
    "CONSISTENCY_PROMPT",
]
