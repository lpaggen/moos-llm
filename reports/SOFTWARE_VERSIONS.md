# Software and execution-environment versions

Audit date: 2026-11-15 (Europe/Brussels).

This inventory distinguishes three kinds of evidence. **Run-frozen** means that the value is present in the relevant request, response, configuration snapshot, manifest, or run audit. **Currently observed** means that the value was measured in the working environment on the audit date; it must not be presented as the historical experiment version unless a run artifact also records it. **Not frozen** means that the historical value cannot be recovered from the preserved artifacts.

## Versions attributable to the reported experiments

| Component | Recorded identity | Version or revision attributable to the run | Evidence and limitation |
|---|---|---|---|
| Navigation language model | `Qwen/Qwen3-8B` | Model repository name is run-frozen; revision is **not frozen** | Runtime INI snapshots and `audit.jsonl` record the model name. They do not record a Hugging Face commit. The current cache commit, `b968826d9c46dd6066d109eabc6255188de91218`, is therefore not retroactive evidence for an earlier run. |
| Local inference server | vLLM | **Not frozen** for the reported campaigns | The campaign supervisor log records the service endpoint but not the vLLM version. The current installation and the 2026-11-15 `logs/vllm.log` report vLLM 0.25.0; this cannot be assigned to the earlier campaigns without a contemporaneous log. |
| Model precision and context | Qwen3 through vLLM | `bfloat16`, 8,192-token maximum are currently observed; only the 8,192-token setting is frozen in the campaign INI | The current server log records `revision=None`, `dtype=torch.bfloat16`, no quantisation, tensor parallel size 1, and seed 0. The historical INI fixes `max_model_len=8192`, but the other current server defaults were not copied into the run manifest. |
| Extraction and curation model | `gpt-6.1-sol` | Model name is run-frozen; provider snapshot is **not frozen** | Frozen Batch requests and outputs record the model name. Responses contain `system_fingerprint: null`; temperature, top-p, and seed were omitted. |
| Dense retriever | `BAAI/bge-small-en-v1.5` | Model name is frozen; revision is **not frozen** | Retrieval provenance records the repository name only. Current cache commit: `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`. |
| Cross-encoder diagnostic | `cross-encoder/ms-marco-MiniLM-L6-v2` | Model name is frozen; revision is **not frozen** | Diagnostic provenance records the repository name only. Current cache commit: `233902d25c440f23af6f7d6e94d2946bac0bee0a`. This reranker was not enabled in the 255-card runtime campaign. |
| PDF extraction | `pypdf.PdfReader` and `page.extract_text()` | API is frozen; package version is **not frozen** | The index does not contain an environment lock or package version. The currently installed version is 6.18.0. |
| Simulator | MOOS-IvP | Exact historical release/build is **not frozen** | Campaign provenance hashes project scripts and RAG artifacts but not the MOOS-IvP binaries or a release identifier. The current binaries identify MOOS-IvP 24.8; `MOOSDB` identifies its core as V10. These are current-installation facts, not a historical proof. |
| Project source | `moos-llm` | Per-file SHA-256 values are run-frozen; a Git commit is **not recorded in the campaign manifest** | `provenance.sha256` freezes `run_imazu_evaluation.sh`, `run_simulator.sh`, `main.py`, `agents/agent_captain.py`, `rag/maib.py`, prompts, index, and embeddings. Current checkout: `521b0703c9aaaef00de856b5ff280bf2ee26b4ed` with uncommitted changes. |

The principal 255-card campaign manifest was created at `2026-11-02T23:07:15Z`. It freezes the configuration hash `6e0aa4ef855815ef0768100dc48f9f3c64cfb1e0fa01d8668094e5eab3efe969`, card-index hash `de33fdd9a801917bd9765fe5a63e49851fa36ebc568c8293c01112e008ff8d14`, and embedding-array hash `1c344bb6c47acfb21cbe22dbd108e286dedee18c1223005b84b5fc8c9a8226cc`, but it does not freeze the OS, Python environment, CUDA stack, vLLM version, model revision, MOOS-IvP binary, or GPU driver.

## Currently observed host and simulator environment

| Layer | Currently observed version |
|---|---|
| Host operating system | Microsoft Windows 10, build 10.0.19045.6466 |
| WSL | 3.0.5.0 |
| WSL kernel | WSL reports 6.18.58.1-1; Linux reports `6.18.58.1-microsoft-standard-WSL2` |
| WSLg | 1.0.79 |
| MSRDC | 1.2.7391 |
| Direct3D integration | 1.611.1-81528511 |
| DXCore | 10.0.26100.1-240331-1435.ge-release |
| Linux distribution | Ubuntu 26.04 LTS (Resolute Raccoon) |
| C library | GNU libc 2.43 (`2.43-2ubuntu2.7`) |
| CPU | Intel Core i5-14600K; 10 physical cores, 20 logical CPUs; 24 MiB L3 cache |
| GPU | NVIDIA RTX A5000, 24,564 MiB reported memory |
| NVIDIA Windows/WSL driver | 621.14 |
| CUDA toolkit (`nvcc`) | 13.3, build 13.3.73 |
| MOOS-IvP binaries | Release Bundle 24.8 (`uSimMarine --version`, `pMarineViewer --version`) |
| MOOS core | `MOOSDB V10` |
| MOOS-IvP source checkout | `825ecff76ac2c14dfbc30b898cc0ec737fe09891`, dirty working tree |
| MOOS-IvP binary build times | 2026-06-30 local time for `MOOSDB`, `uSimMarine`, and `pMarineViewer` |

The current MOOS-IvP checkout is modified and contains untracked mission material. Its Git commit therefore does not uniquely identify the installed binaries or the experiment configuration.

## Currently observed Python and inference environment

| Component | Version |
|---|---:|
| CPython | 3.11.15 |
| uv | 0.11.25 |
| `moos-llm` project metadata | 0.1.0 |
| vLLM | 0.25.0 |
| PyTorch | 2.11.0+cu130 |
| PyTorch CUDA build | 13.0 |
| cuDNN visible through PyTorch | 9.19.0 (`torch.backends.cudnn.version() = 91900`) |
| Triton | 3.6.0 |
| FlashInfer | 0.6.13 |
| FlashAttention backend | Version 2, as reported by the current vLLM log |
| xgrammar | 0.2.3 |
| llguidance | 1.7.6 |
| transformers | 5.13.1 |
| tokenizers | 0.22.2 |
| safetensors | 0.8.0 |
| huggingface-hub | 1.23.0 |
| sentence-transformers | 6.0.1 |
| NumPy | 2.3.5 |
| SciPy | 1.17.1 |
| scikit-learn | 1.9.0 |
| pypdf | 6.18.0 |
| OpenAI Python SDK | 2.45.0 |
| Pydantic | 2.13.4 |
| FastAPI | 0.136.3 |
| Starlette | 1.3.1 |
| Uvicorn | 0.51.0 |
| HTTPX | 0.28.1 |
| Requests | 2.34.2 |
| tiktoken | 0.13.0 |
| Jinja2 | 3.1.6 |
| PyYAML | 6.0.3 |
| pandas | 3.0.6 |
| PyArrow | 25.0.1 |
| Matplotlib | 3.11.2 |

The exhaustive current dependency resolution is retained in `uv.lock` (SHA-256 `7c7a122512903e92fe5b49ab3b1c8ba14238f6837aacf3d913cb6092baca7462`). The corresponding `pyproject.toml` has SHA-256 `518da478bdc73e67a8d1a32e5096387b31fc47c337987e9feecb7767c0f457c5`. These hashes identify the audit-date files; neither file was copied into the 2026-11-02 campaign manifest.

The current CUDA Python environment includes CUDA toolkit 13.0.2, `cuda-python` 13.3.1, cuBLAS 13.1.0.3, cuFFT 12.0.0.61, cuRAND 10.4.0.35, cuSOLVER 12.0.4.66, cuSPARSE 12.6.3.3, NCCL 2.28.9, NVSHMEM 3.4.5, and NVJITLINK 13.0.88. The project also contains CUDA-12 compatibility wheels (`nvidia-cublas-cu12` 12.9.2.10 and `nvidia-cudnn-cu12` 9.24.0.43). The active PyTorch build is the CUDA-13.0 build; listing installed compatibility wheels does not imply that every wheel participated in a reported run.

The BM25 implementation used for MAIB retrieval is project code, not the `rank-bm25` package. Consequently there is no external BM25-library version to report.

## Other direct project dependencies currently installed

These are declared project capabilities but are peripheral to the Chapter 4 text-retrieval and simulation results: `faster-whisper` 1.2.1, `openai-whisper` 20250625, `sounddevice` 0.5.5, `torchcodec` 0.14.0, `python-dotenv` 1.2.2, and `fonttools` 4.64.0. The optional training environment currently contains `accelerate` 1.15.0, `bitsandbytes` 0.50.2, `datasets` 5.0.1, `peft` 0.21.0, and `trl` 1.13.0. Their installation does not establish that they were used in the reported experiments.

## Current native build tools and graphical libraries

| Component | Version |
|---|---:|
| GCC / G++ | 15.2.0 (`15.2.0-16ubuntu1`) |
| CMake | 4.2.3 |
| GNU Make | 4.4.1 |
| Git | 2.53.0 |
| FLTK runtime/development line used by the installation | 1.3.11-3 |
| Mesa OpenGL runtime | 26.0.3-1ubuntu1 |
| X11 client library | 1.8.13-1ubuntu0.1 |

These tool versions are currently installed. The MOOS-IvP build cache identifies `/usr/bin/c++` and Release mode for MOOSCore, MOOSEssentials, and MOOSToolsUI, but does not preserve the compiler version string used at build time. Current compiler versions must therefore not be described as proven build-time versions.

## Values still missing for exact historical reproduction

The preserved evidence does not identify the historical WSL/Windows build, Linux distribution, kernel, GPU model and driver, CUDA stack, Python patch release, complete Python lock, vLLM version, Qwen snapshot, BGE snapshot, cross-encoder snapshot, `pypdf` build-time version, MOOS-IvP release or binary hashes, or native compiler versions. Those omissions should be stated as limitations; the current values above must not be used to fill them silently.
