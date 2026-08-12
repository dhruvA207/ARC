# Dependencies

Running ledger of every dependency, its licence, and why it earns its place. Required by
`docs/BRIEF.md` §0.1, started at commit #1 and kept current.

## The rule

**Application code is 100% mine. Every dependency is permissively licensed.** No GPL, AGPL,
SSPL, research-only, non-commercial, or custom-community licences — ever. If the best library
for a job is copyleft, the second-best permissive one gets used and the tradeoff is recorded
here.

Before adding anything, the question is whether fifty lines of our own code would do (§7:
"treat dependencies as a liability"). Several times so far the answer has been yes — see
[Deliberately not used](#deliberately-not-used).

## Runtime dependencies

| Package | Version | Licence | Why it is here |
|---|---|---|---|
| PyYAML | 6.0.3 | MIT | Config files. Writing a YAML parser is not a good use of anyone's time, and `json`-only config would make `config/*.yaml` unpleasant to hand-edit. |

That is still the entire *required* dependency list after Phase 2. Everything else — CLI
parsing, JSON, paths, process control, threading, hardware probing — is standard library.

## Optional dependencies — inference backends

Deliberately optional, installed as extras. Two of the three cannot even exist on a given
machine (MLX is Apple-Silicon-only, vLLM needs CUDA), and `arc model list` must work on a
machine with no backend at all. `arc/model/router.py` imports these lazily and reports a clear
error when one is missing.

```bash
pip install 'arc[mlx]'       # Apple Silicon
pip install 'arc[llamacpp]'  # anywhere
```

| Package | Version | Licence | Verified | Why it is here |
|---|---|---|---|---|
| mlx-lm | 0.31.3 | MIT | 2026-07-28 | Apple Silicon fast path. Unified memory means no host-to-device copy; measurably faster than llama.cpp's Metal path on this machine. |
| llama-cpp-python | — | MIT | 2026-07-28 | Portable GGUF backend: CPU, Metal, and CUDA. The backend that survives the Windows move unchanged. |
| huggingface-hub | 1.25.1 | Apache-2.0 | 2026-07-29 | Weight downloads for `arc model pull`. Pulled in transitively by mlx-lm anyway. |

## Optional dependencies — the application extras

Everything ARC needs to remember, see, hear, speak, and drive the machine. Optional for
the same reason the backends are: the core must import, and `arc doctor` and `arc-kill`
must run, on a machine with none of them installed. Every import below is lazy.

**`pip install -e '.[all]'` composes all of these.** Adding a package to one of these
tables without adding it to `pyproject.toml` is how the two Macs silently diverge — the
`all` extra is the single source of truth, and this ledger explains it.

### `memory` — vector search and the embedder

| Package | Version | Licence | Verified | Why it is here |
|---|---|---|---|---|
| sqlite-vec | 0.1.9 | MIT / Apache-2.0 | 2026-08-12 | Vector search inside SQLite, no server process. Promoted here from "planned" once Phase 3 landed. |
| onnxruntime | 1.28.0 | MIT | 2026-08-12 | Runs bge-small without dragging in PyTorch — ~19 MB against ~2.5 GB (ADR-014). |
| tokenizers | 0.22.2 | Apache-2.0 | 2026-08-12 | The embedder's tokenizer. Arrives via mlx-lm too, but `memory/embedder.py` imports it directly, so it is declared directly. |

### `screen` — screen reading, OCR, and input control

Split from `camera` because this is how ARC sees and drives *this* machine, with no
camera involved. `camera` is the hand-tracking pipeline and is genuinely separable.

| Package | Version | Licence | Verified | Why it is here |
|---|---|---|---|---|
| pyobjc-core | 12.2.2 | MIT | 2026-08-12 | The objc runtime bridge. `control/overlay.py` imports it directly to define an NSView subclass. |
| pyobjc-framework-Quartz | 12.2.2 | MIT | 2026-08-12 | Screen capture, window lists, and event taps. The most-used framework in the tree. |
| pyobjc-framework-Cocoa | 12.2.2 | MIT | 2026-08-12 | AppKit and Foundation — the overlay window, app activation, string conversion. |
| pyobjc-framework-ApplicationServices | 12.2.2 | MIT | 2026-08-12 | The accessibility tree, which is how ARC reads the screen structurally rather than by pixels. |
| pyobjc-framework-Vision | 12.2.2 | MIT | 2026-08-12 | OCR. Apple's on-device recogniser rather than Tesseract: no extra binary, no model download. |

### `voice` — speech in and out

| Package | Version | Licence | Verified | Why it is here |
|---|---|---|---|---|
| pyobjc-framework-Speech | 12.2.2 | MIT | 2026-08-12 | `SFSpeechRecognizer`, on-device for en-US. |
| pyobjc-framework-AVFoundation | 12.2.2 | MIT | 2026-08-12 | `AVAudioEngine` input tap; also names cameras by identity rather than index. |
| google-genai | 2.17.0 | Apache-2.0 | 2026-08-12 | The Gemini live session (`voice/live.py`). **This is the one part of ARC that leaves the machine** — `arc doctor` says so at every run. The REST TTS path in `voice/gemini.py` deliberately uses `urllib` instead, so the SDK is only needed for the live socket. |
| sounddevice | 0.5.5 | MIT | 2026-08-12 | Audio in and out for that session. Was arriving only as a mediapipe transitive, which meant voice quietly depended on the camera extra. |

### `app` — the window

| Package | Version | Licence | Verified | Why it is here |
|---|---|---|---|---|
| pyobjc-framework-WebKit | 12.2.2 | MIT | 2026-08-12 | `WKWebView` in an ARC-owned `NSWindow`, rather than a browser tab or an embedding library with its own dependency tree (§7). |

### `camera` — hand tracking

| Package | Version | Licence | Verified | Why it is here |
|---|---|---|---|---|
| mediapipe | 1.0.0 | Apache-2.0 | 2026-08-12 | Hand landmarks and the gesture model. |
| opencv-python | 5.0.0.93 | Apache-2.0 | 2026-08-12 | Capture and the preview window. |

### Transitive arrivals that are not Apache-2.0 or MIT

Recorded rather than waved through, in the same spirit as the PyTorch exception below.

| Package | Licence | Arrives via | Status |
|---|---|---|---|
| numpy | BSD-3-Clause AND 0BSD AND MIT AND Zlib | onnxruntime (`numpy>=1.21.6`), mediapipe | **Not declared, deliberately.** `memory/embedder.py` imports numpy directly, so declaring it would be the tidy thing to do — but onnxruntime hard-requires it, so the import is satisfied whenever `[memory]` is. Declaring it would promote a BSD-3-Clause package to a first-class dependency and force a §0.1 exception for no practical gain. |
| certifi | MPL-2.0 | the MLX stack | Recorded at Phase 2. File-level copyleft only; we do not modify it. |
| pathspec | MPL-2.0 | mypy | Dev-time only; never shipped. |

## Development dependencies

| Package | Version | Licence | Why it is here |
|---|---|---|---|
| pytest | 9.1.1 | MIT | Test runner (§7: test as we go). |
| ruff | 0.16.0 | MIT | Lint and format. One tool instead of flake8 + black + isort. |
| mypy | 2.3.0 | MIT | `--strict` type checking (§7). |
| types-PyYAML | 6.0.12 | Apache-2.0 | Type stubs so `--strict` passes across the YAML boundary. |
| mypy_extensions | 1.1.0 | MIT | Transitive dependency of mypy. |

## Planned, not yet added

Recorded now so the licence question is settled before the code depends on it.

| Package | Licence | Verified | For |
|---|---|---|---|
| mlx / mlx-lm | MIT | 2026-07-28 | Phase 2 Apple Silicon inference fast path. Apple Silicon only — see [DECISIONS](DECISIONS.md). |
| llama.cpp (via bindings) | MIT | not yet | Phase 2 portable GGUF backend (CPU / Metal / CUDA). |
| PyTorch | **BSD-3-Clause** | 2026-07-28 | Track B training. **See the exception below.** |
| peft / trl / transformers | Apache-2.0 | not yet | Track B QLoRA fine-tuning on the Windows box. |

### Exception: PyTorch is BSD-3-Clause, not Apache-2.0 or MIT

§0.1 says "Apache-2.0 or MIT only." PyTorch is BSD-3-Clause, which is neither. It is recorded
here as a **conscious, brief-sanctioned exception** rather than quietly waved through:

- §1.7 of the brief names PyTorch explicitly as an acceptable library.
- BSD-3-Clause is functionally equivalent to MIT for our purposes: permissive, no copyleft, no
  royalties, no field-of-use restriction. It requires attribution and adds a
  no-endorsement clause. It lacks Apache-2.0's explicit patent grant.
- There is no permissive alternative at PyTorch's capability level.

## Model weights

Every model's licence is verified against its **live Hugging Face model card** before being
written into `config/models.yaml` — never from memory, ours or anyone's (§3).

`arc/model/registry.py` enforces this in code: an entry whose licence is not Apache-2.0 or MIT
raises `ConfigError` at load time. It cannot drift silently.

| Model | Licence | Verified | Notes |
|---|---|---|---|
| [mlx-community/Qwen3-4B-Instruct-2507-4bit](https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-4bit) | Apache-2.0 | 2026-07-29 | **In the registry — default chat model.** 2.1 GB on disk, native tool calling. |
| [mlx-community/Qwen3-8B-4bit](https://huggingface.co/mlx-community/Qwen3-8B-4bit) | Apache-2.0 | 2026-07-29 | **In the registry.** 4.6 GB, adds thinking mode. |
| [Qwen/Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) | Apache-2.0 | 2026-07-29 | Upstream of the 4-bit conversion above. 262K native context. |
| [Qwen/Qwen3-8B](https://huggingface.co/Qwen/Qwen3-8B) | Apache-2.0 | 2026-07-29 | Upstream of the 8B conversion. 32K native, 131K with YaRN. |
| [Qwen3-0.6B-Base](https://huggingface.co/Qwen/Qwen3-0.6B-Base) | Apache-2.0 | 2026-07-28 | Track B candidate, not in the registry. |
| [Qwen3-1.7B-Base](https://huggingface.co/Qwen/Qwen3-1.7B-Base) | Apache-2.0 | 2026-07-28 | Track B candidate, not in the registry. |

Not used, and why: **Llama** (Meta community licence — not Apache or MIT, carries conditions),
anything tagged research-only or non-commercial.

A fine-tune of Apache-2.0 weights is fully ours to run, modify, sell, or close-source. The base
model's NOTICE and attribution travel with the derivative; that is the whole cost.

## Datasets

Data licences are a separate question from code licences, and the distinction matters.

| Dataset | Licence | Verified | Notes |
|---|---|---|---|
| [TinyStories](https://huggingface.co/datasets/roneneldan/TinyStories) | CDLA-Sharing-1.0 | 2026-07-28 | See below. |

**CDLA-Sharing-1.0 and why it is acceptable.** The name suggests share-alike, but
[§3.5 of the licence](https://cdla.dev/sharing-1-0/) states: *"This Agreement imposes no
obligations or restrictions on Your Use or Publication of Results."* Results are defined as the
outputs of computational use of the data — **a trained model is a Result and is completely
unencumbered.** The sharing obligation attaches only to redistributing the *data itself*.

**Operational rule that follows:** corpora live in `~/.arc/`, never in git. As long as we do not
republish the dataset, nothing propagates to our model or our code.

## Deliberately not used

| Rejected | Licence | Why not | What we do instead |
|---|---|---|---|
| psutil | BSD-3-Clause | Would be convenient for the hardware probe, but it is a dependency for ~50 lines of parsing, and macOS ships `sysctl`/`system_profiler` anyway. | `subprocess` + stdlib in `arc/platform/macos.py` |
| click / typer | BSD-3-Clause / MIT | The CLI is four subcommands and four flags. | `argparse` |
| A server-based vector DB | varies | §4.2 requires one portable SQLite file, not a service. | SQLite + sqlite-vec (Phase 3) |
| Any third-party agent framework | varies | §1.7: zero copied agent-framework code. The architecture is ours. | `arc/agent/` (Phase 4) |
