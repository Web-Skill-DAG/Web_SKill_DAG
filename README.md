# ✨ WebViSE: Scaling Visual Design Intelligence for LLMs from the Open Web

## ✨ Release code

**[Start with the release-code guide](./release_code/README.md)**

The release code covers the paper-aligned Website-Derived Visual Skill Construction and Query-Induced Joint Skill DAG Evolution pipelines, together with the Plug-and-Play Website Query Production utility. The guide provides runnable entry points, API configuration, execution steps, and input/output contracts.

The repository also provides the frozen artifacts required by the released workflows:

- [`initial_visual_skill_library/`](./initial_visual_skill_library/) contains the initial 864 unique Skills organized into 11 root Categories with 943 Category-to-Skill memberships.
- [`final_evolved_skill_dag/`](./final_evolved_skill_dag/) contains the final recursive DAG (`graph.json`) and its complete Skill documents (`skills.jsonl`). The released graph has 163 nodes and 858 active Skills after verified merges.

## ▶️ Play now!

Export an OpenAI-compatible Chat Completions endpoint, its API key, and the model served by that endpoint. Then pass one website request with `--query`:

```bash
export BASE_URL="https://your-api.example.com/v1/chat/completions"
export API_KEY="sk-your-api-key"
export MODEL="your-model"

bash release_code/play.sh \
  --query "Design a bold, futuristic recruitment poster for a technology company hiring software engineers."
```

The command retrieves Visual Skills from the released evolved Skill DAG and prints the expanded Query directly to standard output.

## 🎨 Website showcase

**[Open the full interactive gallery](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/)**

We present 16 qualitative cases of Visual Skill selection and application. For each case, we report the selected Skills and compare websites generated under three settings: **No Skill**, **General Website Design Skill**, and **Website-Derived Visual Skill**.

These cases show what was selected and how the website generator applied it. They are separate from the distilled-model results presented later in the paper. Use **Last**, **Next**, the case dropdown, or the keyboard arrow keys to move through the comparisons. Every preview is live and can be opened independently.

| Direct comparison | Case | Task | Visual Skill | No Skill | General Skill | Visual Skill Result |
|---|---:|---|---|---|---|---|
| [Open Case 01](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-01) | 01 | World Cup Live Experience | Stock Ticker Dashboard; Orken; Scroll-Synced Video; Fullscreen Clip Animation | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-01/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-01/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-01/ours/) |
| [Open Case 02](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-02) | 02 | Marketing Course Landing Page | Background Image Grid Motion | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-02/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-02/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-02/ours/) |
| [Open Case 03](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-03) | 03 | Premium Auto Parts | Apple-Style 3D Product Explode | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-03/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-03/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-03/ours/) |
| [Open Case 04](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-04) | 04 | Pattern Pinboard | Stack Motion Hover Effects | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-04/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-04/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-04/ours/) |
| [Open Case 05](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-05) | 05 | Windows XP Desktop | Liquid Effect | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-05/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-05/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-05/ours/) |
| [Open Case 06](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-06) | 06 | Aquarium Dashboard | Surf Report Template | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-06/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-06/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-06/ours/) |
| [Open Case 07](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-07) | 07 | Wind Farm Monitor | Interactive 3D Mall Map | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-07/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-07/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-07/ours/) |
| [Open Case 08](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-08) | 08 | Fitness Planner | Music Player Component | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-08/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-08/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-08/ours/) |
| [Open Case 09](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-09) | 09 | Personal Finance Dashboard | Animated Border Cards | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-09/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-09/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-09/ours/) |
| [Open Case 10](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-10) | 10 | Origami Instruction Studio | Three.js Matcap Demo | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-10/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-10/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-10/ours/) |
| [Open Case 11](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-11) | 11 | Bouncing Ball Physics | Theatre.js Dynamic Visuals | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-11/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-11/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-11/ours/) |
| [Open Case 12](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-12) | 12 | 3D Flame Simulation | Waves | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-12/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-12/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-12/ours/) |
| [Open Case 13](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-13) | 13 | Underwater Coral Reef | Aurelia | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-13/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-13/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-13/ours/) |
| [Open Case 14](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-14) | 14 | Intermittent Geyser | X-Ray Visualizer | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-14/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-14/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-14/ours/) |
| [Open Case 15](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-15) | 15 | 5x5x5 Rubik's Cube | Ball of Glass with Attraction | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-15/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-15/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-15/ours/) |
| [Open Case 16](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/#case-16) | 16 | Interactive 3D Earth | Interactive 3D Device Showcase | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-16/no-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-16/general-skill/) | [Open](https://web-skill-dag.github.io/Web_SKill_DAG/showcase/cases/case-16/ours/) |

## 🚀 Case Study 1: Plug-and-Play Website Generation

**[Open the Guoqing Temple comparison](https://web-skill-dag.github.io/Web_SKill_DAG/case_study/)**

This side-by-side case study compares the Base setting with WebViSE under the same website-generation model and original user instruction. WebViSE retrieves applicable Visual Skills through the evolved Skill DAG and combines them with the original instruction as plug-and-play guidance. Both generated websites are live and can also be opened independently.

## 🧠 Case Study 2: Intelligence Internalization

**[Open the Pointer Lab four-way comparison](https://web-skill-dag.github.io/Web_SKill_DAG/internalization-case-study/)**

This case study illustrates intelligence internalization through Skill-augmented training on the same five-experiment Pointer Lab task. It compares four conditions: Qwen3.8-27B with the original Query, Qwen3.8-27B+WebViSE with plug-and-play Skill guidance, Qwen3.8-27B NoSkill-SFT with the original Query at inference time, and Qwen3.8-27B WebViSE-SFT with the original Query at inference time. All four generated websites are interactive and can be opened independently.
