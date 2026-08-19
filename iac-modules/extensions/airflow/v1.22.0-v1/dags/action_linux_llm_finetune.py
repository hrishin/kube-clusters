"""
Fine-tune a small instruction-following model to turn natural-language Linux
tasks into a runnable shell command (LoRA/QLoRA via Unsloth) — "option (a)"
from the design discussion: plain task -> command completion, not a full
reasoning/tool-call agent.

Source data (merged into one instruction/response dataset by `preprocess`):
  - missvector/linux-commands        (eng / completion)              — direct
  - KonradSzafer/stackoverflow_linux (title / question / answer)     — extracted
  - Rohithqwerty/linux_data          (question_title / question_description / accepted_answer) — extracted

Pipeline:
  1. preprocess     — pull all three HF datasets, normalize into
                       {instruction, response} pairs, push the merged dataset
                       to a private HF Hub dataset repo. This is the hand-off
                       point to the Slurm job: the airflow and slurm
                       namespaces don't share a filesystem, so HF Hub is the
                       exchange bus rather than new shared-storage infra.
  2. train_on_slurm — generate a self-contained sbatch script (bootstraps
                       pip, installs training deps into user site-packages,
                       writes and runs the actual training script — no venv,
                       this image has no ensurepip), then submit/poll it via
                       slurmrestd's REST API (job/submit, then poll
                       job/{job_id}) instead of `kubectl exec`-ing `sbatch`/
                       `scontrol show job` CLI output. slurmrestd is already
                       deployed by chart default (restapi.* in
                       slurm-cluster/v1.2.1-v1/release.yaml is untouched —
                       there's no `enabled` gate, it just always renders),
                       and jwtKey.create defaults to true, so auth/jwt is
                       already wired into the controller. What REST doesn't
                       remove: minting the JWT itself still needs
                       `scontrol token`, which needs a shell with slurm.conf
                       + the jwt key already configured — this cluster has
                       no LoginSet/SSSD identity provisioning, so that one
                       call still goes through `kubectl exec` into
                       slurm-controller-0 (as root, which already has a
                       valid Slurm identity — nothing extra to provision).
                       That's now the *only* kubectl exec in the submit
                       path, replacing what used to be a `kubectl exec` for
                       every sbatch/scontrol call, including the fragile
                       stdin-piped script write. Verified against this
                       cluster's actual slurmrestd build (not guessed): the
                       v0.0.45 API version, job/submit and job/{job_id}
                       request/response schemas, and the job_state enum
                       below were all pulled from
                       `slurmrestd --generate-openapi-spec` run locally
                       against the ghcr.io/slinkyproject/slurmrestd:
                       26.05-ubuntu26.04 image (same image this cluster's
                       restapi.slurmrestd.image defaults to). NOT verified:
                       an actual end-to-end submit against a live
                       slurmrestd + slurmctld pair (this was written while
                       the cluster was down) — the Service name/port below
                       (slurm-restapi.slurm.svc.cluster.local:6820) is
                       derived from slurm-operator's own Go source
                       (RestApi.Key() -> "<name>-restapi", SlurmrestdPort =
                       6820), not observed live either.

REQUIRED before running:
  - Set HF_NAMESPACE below to a real HF Hub username/org you can push to.
  - `sops` a real (write-scope) HF token into
    iac-modules/extensions/airflow/v1.22.0-v1/secret-hf-token.enc.yaml —
    it ships with a placeholder value, so pushes will fail with an auth
    error until that's done.
  - Verify BASE_MODEL still exists in Unsloth's model catalog — my
    knowledge here is not live.

Known gap (not blocking, but worth knowing): Slurm's own gres tracking for
the MIG device isn't working — `scontrol show node gpu-0` shows `Gres=(null)`
despite `gres.conf: AutoDetect=nvml` being set (iac-modules/extensions/
slurm-cluster/v1.2.1-v1/release.yaml). Confirmed live that the MIG slice is
still visible/usable inside the slurmd container regardless (`nvidia-smi -L`
shows it), via Kubernetes' own device-plugin isolation — so training still
works, it's just not tracked as a Slurm gres. Harmless with exactly one
node/one job slot; would need fixing before running multiple concurrent GPU
jobs on this partition.
"""

from __future__ import annotations

import time
from datetime import datetime

from airflow.decorators import dag, task
from airflow.exceptions import AirflowException
from kubernetes.client import models as k8s

# ---------------------------------------------------------------------------
# Config — edit before running
# ---------------------------------------------------------------------------
HF_NAMESPACE = "hriships"  # REQUIRED: real HF Hub namespace
DATASET_REPO = f"{HF_NAMESPACE}/linux-action-sft"
MODEL_REPO = f"{HF_NAMESPACE}/linux-action-qwen2.5-7b-lora"
BASE_MODEL = "unsloth/Qwen2.5-7B-Instruct-bnb-4bit"  # verify against current Unsloth catalog

SLURM_NAMESPACE = "slurm"
SLURM_CONTROLLER_POD = "slurm-controller-0"
SLURM_CONTROLLER_CONTAINER = "slurmctld"
# Job stdout (#SBATCH --output=...) lands on whichever node actually ran the
# job, not the controller — confirmed live. Hardcoded to the one worker pod
# this cluster's gpu nodeset currently runs (replicas: 1, see
# slurm-cluster/v1.2.1-v1/release.yaml); revisit if that nodeset ever scales
# past one replica.
SLURM_WORKER_POD = "slurm-worker-gpu-0"
SLURM_WORKER_CONTAINER = "slurmd"
SLURM_TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY"}

# slurm-restapi.slurm.svc.cluster.local:6820 — derived from slurm-operator's
# RestApi.Key() ("<HelmRelease releaseName>-restapi", and releaseName is
# "slurm" per slurm-cluster/v1.2.1-v1/release.yaml) and the operator's
# hardcoded SlurmrestdPort=6820, not observed against a live Service (see
# module docstring). v0.0.45 confirmed as this cluster's slurmd image's
# newest supported API version via `slurmrestd --generate-openapi-spec`.
SLURMRESTD_BASE_URL = "http://slurm-restapi.slurm.svc.cluster.local:6820/slurm/v0.0.45"
JWT_LIFESPAN_SECONDS = 3600

# Dedicated ServiceAccount + RBAC for pods/exec into slurm-controller-0 — see
# serviceaccount-slurm-exec.yaml (this module) and
# iac-modules/extensions/slurm-cluster/v1.2.1-v1/rbac-airflow-exec.yaml.
_POD_OVERRIDE_EXEC_SA = k8s.V1Pod(
    spec=k8s.V1PodSpec(
        containers=[k8s.V1Container(name="base")],
        service_account_name="airflow-slurm-exec",
    )
)


@dag(
    dag_id="action_linux_llm_finetune",
    description="LoRA fine-tune a task->linux-command model on the in-cluster Slurm GPU node",
    start_date=datetime(2026, 1, 1),
    schedule=None,  # manually triggered
    catchup=False,
    tags=["llm", "slurm", "finetune"],
)
def action_linux_llm_finetune():
    @task.virtualenv(
        requirements=["datasets>=2.20", "huggingface_hub>=0.24"],
        system_site_packages=False,
    )
    def preprocess(dataset_repo: str) -> str:
        import os
        import re

        from datasets import Dataset, load_dataset

        hf_token = os.environ["HF_TOKEN"]
        examples: list[dict[str, str]] = []

        # --- missvector/linux-commands: direct task -> command mapping. ---
        cmds = load_dataset("missvector/linux-commands", split="train")
        for row in cmds:
            instruction = (row.get("eng") or "").strip()
            command = (row.get("completion") or "").strip()
            if instruction and command:
                examples.append({"instruction": instruction, "response": f"```bash\n{command}\n```"})

        # --- SO-shaped datasets: extract a command from the answer's first
        # fenced or indented code block. ---
        fenced_re = re.compile(r"```(?:bash|sh|shell)?\n(.*?)```", re.DOTALL)
        indented_re = re.compile(r"(?:^|\n)((?:[ ]{4}|\t)[^\n]+(?:\n(?:[ ]{4}|\t)[^\n]+)*)")

        def extract_command(answer: str) -> str:
            m = fenced_re.search(answer)
            if m:
                return m.group(1).strip()
            m = indented_re.search(answer)
            if m:
                return re.sub(r"^[ \t]+", "", m.group(1), flags=re.MULTILINE).strip()
            return ""

        def add_so_shaped(dataset_name: str, title_key: str, body_key: str, answer_key: str) -> None:
            ds = load_dataset(dataset_name, split="train")
            for row in ds:
                title = (row.get(title_key) or "").strip()
                body = (row.get(body_key) or "").strip()
                answer = row.get(answer_key) or ""
                if not (title or body) or not answer:
                    continue
                command = extract_command(answer)
                # Keep it action-oriented: skip answers with no extractable
                # command, or multi-line "scripts" too long to be one command.
                if not command or len(command.splitlines()) > 3 or len(command) > 400:
                    continue
                instruction = title if title else body.splitlines()[0][:200]
                examples.append({"instruction": instruction, "response": f"```bash\n{command}\n```"})

        add_so_shaped("KonradSzafer/stackoverflow_linux", "title", "question", "answer")
        add_so_shaped("Rohithqwerty/linux_data", "question_title", "question_description", "accepted_answer")

        if len(examples) < 100:
            raise AirflowException(
                f"only {len(examples)} usable examples extracted — "
                "check dataset schema/extraction regexes before training on this"
            )

        ds = Dataset.from_list(examples)
        ds = ds.train_test_split(test_size=0.05, seed=42)
        ds.push_to_hub(dataset_repo, token=hf_token, private=True)
        return f"pushed {len(examples)} examples to {dataset_repo}"

    @task(executor_config={"pod_override": _POD_OVERRIDE_EXEC_SA})
    def train_on_slurm(dataset_repo: str, model_repo: str, base_model: str) -> str:
        import json
        import os
        import urllib.error
        import urllib.request

        from kubernetes import client, config
        from kubernetes.stream import stream

        hf_token = os.environ["HF_TOKEN"]

        train_script = f'''#!/usr/bin/env python3
import hashlib
import json
import os
import urllib.request

# unsloth first, before trl/transformers/peft — it patches those libraries
# on import, and importing it later only partially applies the patches
# ("Unsloth should be imported before [trl, transformers, peft]...").
from unsloth import FastLanguageModel
from unsloth.chat_templates import get_chat_template, train_on_responses_only

import pyarrow.parquet as pq
from datasets import Dataset
from trl import SFTConfig, SFTTrainer

MAX_SEQ_LENGTH = 2048
HF_TOKEN = os.environ["HF_TOKEN"]


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode()).hexdigest()[:16]


def download_hf_repo(repo_id: str, local_dir: str) -> str:
    # huggingface_hub's own downloader (Xet-preferring, with an in-process
    # fallback to HF_HUB_DISABLE_XET=1) hangs indefinitely on this repo's
    # model.safetensors — confirmed live, including with HF_HUB_DISABLE_XET=1
    # set from process start (not just Unsloth's post-hoc runtime fallback,
    # which doesn't work either: huggingface_hub.constants caches env vars
    # at import time, before Unsloth's fallback code ever runs). Plain
    # stdlib urllib has none of these issues — confirmed live pulling the
    # full 5.5GB file in ~4-5 minutes with zero stalls. Bypass
    # huggingface_hub's downloader entirely and fetch the repo ourselves.
    os.makedirs(local_dir, exist_ok=True)
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{{repo_id}}", timeout=30) as resp:
        info = json.load(resp)
    for sib in info.get("siblings", []):
        fname = sib["rfilename"]
        if fname in ("README.md", ".gitattributes") or fname.endswith(".md"):
            continue
        dest = os.path.join(local_dir, fname)
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with urllib.request.urlopen(f"https://huggingface.co/{{repo_id}}/resolve/main/{{fname}}", timeout=60) as r, open(dest, "wb") as f:
            while True:
                chunk = r.read(1048576)
                if not chunk:
                    break
                f.write(chunk)
    return local_dir


def load_hf_dataset(repo_id: str, filename: str, local_path: str) -> Dataset:
    # datasets.Dataset construction (via load_dataset or any other
    # constructor) unconditionally dill-pickles part of the call graph to
    # compute a cache fingerprint — this crashes outright on this image's
    # Python 3.14 + dill 0.4.0/0.4.1, with no working workaround at the
    # datasets-library level (confirmed live: disable_caching(),
    # keep_in_memory=True, and upgrading dill all still crash the same way;
    # this is an open, unfixed upstream bug —
    # huggingface/datasets#8373, "Pickler._batch_setitems() takes 2
    # positional arguments but 3 were given"). Passing an explicit
    # fingerprint skips that computation entirely — confirmed live this
    # works — so fetch the parquet file ourselves (same proven urllib
    # approach as the model download) and construct the Dataset directly.
    req = urllib.request.Request(
        f"https://huggingface.co/datasets/{{repo_id}}/resolve/main/{{filename}}",
        headers={{"Authorization": f"Bearer {{HF_TOKEN}}"}},
    )
    with urllib.request.urlopen(req, timeout=120) as r, open(local_path, "wb") as f:
        while True:
            chunk = r.read(1048576)
            if not chunk:
                break
            f.write(chunk)
    table = pq.read_table(local_path)
    return Dataset(table, fingerprint=_fingerprint(repo_id, filename))


local_base_model = download_hf_repo("{base_model}", "/tmp/base_model")

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=local_base_model,
    max_seq_length=MAX_SEQ_LENGTH,
    load_in_4bit=True,
)
tokenizer = get_chat_template(tokenizer, chat_template="qwen-2.5")

model = FastLanguageModel.get_peft_model(
    model,
    r=32,
    lora_alpha=64,
    lora_dropout=0,
    bias="none",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

dataset = load_hf_dataset(
    "{dataset_repo}", "data/train-00000-of-00001.parquet", "/tmp/train_dataset.parquet"
)


def formatting_func(example):
    messages = [
        {{"role": "user", "content": example["instruction"]}},
        {{"role": "assistant", "content": example["response"]}},
    ]
    return {{"text": tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)}}


dataset = dataset.map(formatting_func, new_fingerprint=_fingerprint("{dataset_repo}", "formatted"))

# transformers' TrainingArguments.to_dict() redacts every field whose name
# ends in "_token" (meant for secrets like hub_token) into a literal
# "<FIELD_NAME>" placeholder string. SFTTrainer.__init__ round-trips its
# args through to_dict() internally and then validates fields like
# eos_token/pad_token against the tokenizer's vocabulary — so it ends up
# checking whether the literal string "<EOS_TOKEN>"/"<PAD_TOKEN>" is a real
# token, which it never is, and raises. Confirmed live for both eos_token
# and pad_token (hit one after fixing the other), reproduced
# deterministically against this exact script regardless of
# dataset_num_proc/optim — it's unconditional, not something dodged by
# omitting the field or changing unrelated SFTConfig fields. Rather than
# special-case every "*_token" field TRL happens to validate, generically
# redirect any "<..._TOKEN>" placeholder lookup to the tokenizer's real
# attribute of the same name (e.g. "<PAD_TOKEN>" -> tokenizer.pad_token),
# so validation always passes with the correct value.
_orig_convert_tokens_to_ids = tokenizer.convert_tokens_to_ids


def _redacted_token_safe_convert(tokens):
    if isinstance(tokens, str) and tokens.startswith("<") and tokens.endswith("_TOKEN>"):
        real_token = getattr(tokenizer, tokens[1:-1].lower(), None)
        if real_token is not None:
            return _orig_convert_tokens_to_ids(real_token)
    return _orig_convert_tokens_to_ids(tokens)


tokenizer.convert_tokens_to_ids = _redacted_token_safe_convert

trainer = SFTTrainer(
    model=model,
    # processing_class, not tokenizer=; dataset_text_field/max_length live
    # on SFTConfig now, not here — all renamed/moved upstream in this
    # transformers/trl version, confirmed live one at a time:
    # "SFTTrainer.__init__() got an unexpected keyword argument 'tokenizer'"
    # then "... 'dataset_text_field'" (max_seq_length was never a valid
    # SFTTrainer kwarg in this version either, just didn't error until the
    # first two were fixed).
    processing_class=tokenizer,
    train_dataset=dataset,
    args=SFTConfig(
        dataset_text_field="text",
        max_length=MAX_SEQ_LENGTH,
        per_device_train_batch_size=2,
        gradient_accumulation_steps=16,
        warmup_ratio=0.05,
        num_train_epochs=3,
        learning_rate=2e-4,
        lr_scheduler_type="cosine",
        optim="adamw_8bit",
        weight_decay=0.01,
        logging_steps=10,
        seed=3407,
        output_dir="/tmp/outputs",
        report_to="none",
    ),
)

# Only train on the assistant's completion, not the prompt — the whole point
# of "action-oriented": don't spend capacity relearning the instruction.
trainer = train_on_responses_only(
    trainer,
    instruction_part="<|im_start|>user\\n",
    response_part="<|im_start|>assistant\\n",
)

trainer.train()

# push_to_hub_merged tried and ruled out, in order:
#   - save_method="merged_16bit": needs a 16-bit base to merge the LoRA
#     delta into — this base model was loaded with load_in_4bit=True
#     (nf4), so it fails outright ("base model should be a 16bit or mxfp4
#     base model for a 16bit merge... Use save_method='forced_merged_4bit'
#     instead" — confirmed live, Unsloth's own error message).
#   - save_method="forced_merged_4bit": the LoRA merge itself succeeds,
#     but writing the merged result out then hits an unrelated bug in this
#     transformers version's newer weight-serialization path — confirmed
#     live: NotImplementedError from transformers/core_model_loading.py's
#     revert_weight_conversion, raised while save_pretrained() tries to
#     reverse a quantization weight-conversion op that this transformers
#     version doesn't support reversing. Not something to work around at
#     the config level; it's a real gap in that code path.
# Pushing just the LoRA adapter sidesteps both: it's PEFT's own well-worn
# save/upload path (no merge, no quantization-reversal), the artifact is
# ~300MB instead of several GB, and it's how LoRA fine-tunes are normally
# distributed anyway — downstream consumers load the base model plus this
# adapter with PEFT rather than a single merged checkpoint. Confirmed live
# end-to-end (adapter_model.safetensors + tokenizer files uploaded,
# repo created successfully).
model.push_to_hub("{model_repo}", token=os.environ["HF_TOKEN"], private=True)
tokenizer.push_to_hub("{model_repo}", token=os.environ["HF_TOKEN"], private=True)
print("DONE: pushed LoRA adapter to {model_repo}")
'''

        # No --gres= here — see module docstring: Slurm's AutoDetect=nvml
        # isn't registering the MIG device as a gres on this node
        # (Gres=(null) live), but the device is still visible/usable inside
        # the slurmd container via Kubernetes' own device-plugin isolation.
        # Fine with one node/one job slot; revisit before running concurrent
        # GPU jobs on this partition.
        sbatch_script = f'''#!/bin/bash
#SBATCH --job-name=linux-action-llm
#SBATCH --partition=all
#SBATCH --output=/tmp/linux-action-llm-%j.log
set -euo pipefail

# /home/slurm doesn't exist on this image at all, and /home itself is only
# root-writable — confirmed live: "Permission denied: '/home/slurm'" from
# pip trying to create it for --user installs. /tmp is the one confirmed
# world-writable location (drwxrwxrwt), so point HOME there for the whole
# job; pip's --user site-packages, its cache dir, and anything else that
# keys off $HOME all follow along automatically.
export HOME=/tmp/home
mkdir -p "$HOME"

# Unsloth's custom Triton kernels (e.g. RMSNorm) are JIT-compiled at
# training time and need a C compiler plus the Python C headers to build
# their launcher stub — this image ships neither (confirmed live: "Failed
# to find C compiler", then after installing gcc alone, "fatal error:
# Python.h: No such file or directory"). The container runs as root with
# apt available, so install both directly; no separate download step
# needed the way pip's bootstrap required.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq gcc python3-dev

cat > /tmp/train_lora.py <<'PYEOF'
{train_script}
PYEOF

# No venv: `python3 -m venv` fails on this image — ensurepip isn't
# installed (confirmed live: "The virtual environment was not created
# successfully because ensurepip is not available").
#
# No pip either, not even as a module ("No module named pip") — this is a
# genuinely bare Python 3.14 install, confirmed live. No curl/wget and no
# CA bundle on the image either (no /etc/ssl/certs, ssl.get_default_verify_paths()
# all None), so bootstrapping pip needs Python's own stdlib urllib with TLS
# verification disabled for this one download only — get-pip.py itself is
# fetched insecurely, but pip vendors its own CA bundle (certifi) for every
# install it does afterwards, so real package installs below still verify
# TLS properly. --user + --break-system-packages installs into user
# site-packages, overriding this image's PEP 668
# externally-managed-environment guard, without needing apt/root access the
# job doesn't have anyway.
python3 -c "
import ssl, urllib.request
ssl._create_default_https_context = ssl._create_unverified_context
urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', '/tmp/get-pip.py')
"
python3 /tmp/get-pip.py --quiet --user --break-system-packages
# Pinned loosely on purpose — first run, not yet locked. Once this has
# actually trained successfully once, bake a real container image instead
# of pip-installing several GB of deps on every run.
python3 -m pip install --quiet --user --break-system-packages unsloth "trl>=0.9" peft bitsandbytes accelerate datasets huggingface_hub

# huggingface_hub's fast-download path (hf_xet) is a separate Rust HTTP
# client that does its own TLS setup — it does NOT pick up Python's
# certifi bundle the way pip/requests do, and fails outright on this
# image's missing CA bundle: "RuntimeError: ... Reqwest error: builder
# error" (confirmed live, from FastLanguageModel.from_pretrained trying to
# download model.safetensors). SSL_CERT_FILE is a standard env var reqwest
# (and most native/Rust TLS stacks) respect — point it at the same certifi
# bundle pip just installed. Verified live: a hf_hub_download() call that
# failed without this succeeds with it.
export SSL_CERT_FILE="$(python3 -c 'import certifi; print(certifi.where())')"

export HF_TOKEN="{hf_token}"
# Retry once — generic resilience for a long-running job pulling from the
# network (model/dataset downloads, PyPI installs above), not tied to any
# specific known failure mode.
for attempt in 1 2; do
    if python3 /tmp/train_lora.py; then
        break
    fi
    if [ "$attempt" = "2" ]; then
        echo "train_lora.py failed twice, giving up" >&2
        exit 1
    fi
    echo "train_lora.py failed (attempt $attempt), retrying once..." >&2
done
'''

        config.load_incluster_config()

        def pod_exec(
            command: str,
            stdin_data: str | None = None,
            pod: str = SLURM_CONTROLLER_POD,
            container: str = SLURM_CONTROLLER_CONTAINER,
        ) -> str:
            # A fresh CoreV1Api/ApiClient per call, not shared — reusing one
            # `client.CoreV1Api()` across multiple stream() calls was
            # observed live to hang indefinitely on the second call (writing
            # the sbatch script over stdin worked fine; the very next call,
            # `sbatch /tmp/train.sbatch` on the same api object, never
            # returned — despite that exact command completing in under a
            # second run by hand). Root cause not fully isolated (likely
            # connection/websocket-pool state left over from the manual
            # stdin/_preload_content=False call), but a fresh client per
            # call is cheap and reliably sidesteps it.
            api = client.CoreV1Api()
            if stdin_data is not None:
                w = stream(
                    api.connect_get_namespaced_pod_exec,
                    pod,
                    SLURM_NAMESPACE,
                    container=container,
                    command=["bash", "-c", command],
                    stdin=True,
                    stdout=True,
                    stderr=True,
                    tty=False,
                    _preload_content=False,
                )
                w.write_stdin(stdin_data)
                w.close()
                return ""
            return stream(
                api.connect_get_namespaced_pod_exec,
                pod,
                SLURM_NAMESPACE,
                container=container,
                command=["bash", "-c", command],
                stdin=False,
                stdout=True,
                stderr=True,
                tty=False,
            )

        # Mint a short-lived JWT as the controller container's own identity
        # (root — already a valid Slurm identity, nothing to provision).
        # This is the one kubectl exec left in the submit path; every actual
        # sbatch/scontrol call below goes through slurmrestd instead.
        token_out = pod_exec(f"scontrol token lifespan={JWT_LIFESPAN_SECONDS}")
        jwt = next(
            (tok.split("=", 1)[1] for tok in token_out.split() if tok.startswith("SLURM_JWT=")), None
        )
        if not jwt:
            raise AirflowException(f"couldn't parse JWT from `scontrol token` output: {token_out!r}")

        def slurmrestd_request(method: str, path: str, body: dict | None = None) -> dict:
            req = urllib.request.Request(
                f"{SLURMRESTD_BASE_URL}{path}",
                data=json.dumps(body).encode() if body is not None else None,
                method=method,
                headers={"X-SLURM-USER-TOKEN": jwt, "Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                raise AirflowException(
                    f"slurmrestd {method} {path} failed ({e.code}): {e.read().decode(errors='replace')}"
                ) from e

        # `script` is literally the sbatch script text (same content that
        # used to go over kubectl-exec stdin to `sbatch`) — the #SBATCH
        # lines inside it are redundant with name/partition/standard_output
        # below, kept as belt-and-suspenders rather than relying on whether
        # slurmrestd parses embedded #SBATCH pragmas from `script` the same
        # way the sbatch CLI does (plausible but not verified live).
        submit_resp = slurmrestd_request(
            "POST",
            "/job/submit",
            {
                "script": sbatch_script,
                "job": {
                    "name": "linux-action-llm",
                    "partition": "all",
                    "current_working_directory": "/tmp",
                    "standard_output": "/tmp/linux-action-llm-%j.log",
                    "environment": [
                        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                    ],
                },
            },
        )
        if submit_resp.get("errors"):
            raise AirflowException(f"job/submit returned errors: {submit_resp['errors']}")
        job_id = submit_resp.get("job_id")
        if not job_id:
            raise AirflowException(f"job/submit response had no job_id: {submit_resp!r}")

        # accounting.enabled is false on this cluster (no slurmdbd
        # deployed), so a job's record only lives in slurmctld's live state
        # until MinJobAge (default 300s) purges it — plenty given the 30s
        # poll interval below. job_state is an array (a job can carry
        # compound states); take the first entry, matching how squeue/
        # scontrol display the "primary" state.
        state = "PENDING"
        while state not in SLURM_TERMINAL_STATES:
            time.sleep(30)
            status_resp = slurmrestd_request("GET", f"/job/{job_id}")
            jobs = status_resp.get("jobs") or []
            job_state = jobs[0].get("job_state") if jobs else None
            state = job_state[0] if job_state else "PENDING"

        if state != "COMPLETED":
            log = pod_exec(
                f"tail -n 200 /tmp/linux-action-llm-{job_id}.log 2>/dev/null || true",
                pod=SLURM_WORKER_POD,
                container=SLURM_WORKER_CONTAINER,
            )
            raise AirflowException(f"Slurm job {job_id} ended in state {state}:\n{log}")

        return f"Slurm job {job_id} COMPLETED — model pushed to {model_repo}"

    preprocess_result = preprocess(DATASET_REPO)
    train_result = train_on_slurm(DATASET_REPO, MODEL_REPO, BASE_MODEL)
    preprocess_result >> train_result


action_linux_llm_finetune()
