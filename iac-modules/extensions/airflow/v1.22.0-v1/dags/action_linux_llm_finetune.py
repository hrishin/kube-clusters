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
  2. train_on_slurm — generate a self-contained sbatch script (creates a
                       venv, pip-installs training deps, writes and runs the
                       actual training script) and submit/poll it via
                       `kubectl exec` into slurm-controller-0. No LoginSet or
                       REST API JWT flow — this cluster has neither deployed,
                       and exec into the controller pod (already used
                       manually all through this session) is simpler and
                       just as reliable for one node/one job at a time.

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
        import os

        from kubernetes import client, config
        from kubernetes.stream import stream

        hf_token = os.environ["HF_TOKEN"]

        train_script = f'''#!/usr/bin/env python3
import os

from datasets import load_dataset
from trl import SFTConfig, SFTTrainer
from unsloth import FastLanguageModel
from unsloth.chat_templates import get_chat_template, train_on_responses_only

MAX_SEQ_LENGTH = 2048

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="{base_model}",
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

dataset = load_dataset("{dataset_repo}", split="train")


def formatting_func(example):
    messages = [
        {{"role": "user", "content": example["instruction"]}},
        {{"role": "assistant", "content": example["response"]}},
    ]
    return {{"text": tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)}}


dataset = dataset.map(formatting_func)

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=MAX_SEQ_LENGTH,
    args=SFTConfig(
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

model.push_to_hub_merged(
    "{model_repo}",
    tokenizer,
    save_method="merged_16bit",
    token=os.environ["HF_TOKEN"],
    private=True,
)
print("DONE: pushed to {model_repo}")
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

export HF_TOKEN="{hf_token}"
python3 /tmp/train_lora.py
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

        # Written over stdin rather than as an exec argv string — this
        # heredoc-of-a-heredoc is multiple KB and would be a shell-quoting
        # nightmare (and likely exceed argv limits) passed as `-c "..."`.
        pod_exec("cat > /tmp/train.sbatch", stdin_data=sbatch_script)

        submit_out = pod_exec("sbatch /tmp/train.sbatch")
        job_id = next((tok for tok in submit_out.split() if tok.isdigit()), None)
        if not job_id:
            raise AirflowException(f"couldn't parse job id from sbatch output: {submit_out!r}")

        # scontrol, not sacct: accounting.enabled is false on this cluster
        # (no slurmdbd deployed), so `sacct` fails outright ("Slurm
        # accounting storage is disabled") — confirmed live. scontrol/squeue
        # query slurmctld's live state directly, no accounting needed.
        # scontrol keeps a finished job's record around for a while
        # (MinJobAge, default 300s) before purging it, which is plenty given
        # the 30s poll interval below.
        state = "PENDING"
        while state not in SLURM_TERMINAL_STATES:
            time.sleep(30)
            show = pod_exec(f"scontrol show job {job_id}")
            match = next((tok for tok in show.split() if tok.startswith("JobState=")), None)
            state = match.split("=", 1)[1] if match else "PENDING"

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
