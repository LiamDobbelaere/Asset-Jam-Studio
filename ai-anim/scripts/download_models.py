"""Downloads the Make-It-Animatable weights (mia/output/best/new, ~2.2 GB) and warms the Kimodo cache.

    python download_models.py            everything that is downloadable without a Hugging Face login
    python download_models.py --kimodo   also the Kimodo-SOMA-RP weights and the Llama-3 text encoder (needs
                                         `hf auth login` with access to meta-llama/Meta-Llama-3-8B-Instruct)

Kimodo pulls its weights and encoder into the Hugging Face cache on first use anyway; this only avoids the wait
during the first animation. MIA's example meshes (mia/data/examples) are fetched too, they are handy tests.
"""
import os
import sys

from huggingface_hub import snapshot_download

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIA = os.path.join(ROOT, "mia")

print("Make-It-Animatable weights + examples -> mia/")
snapshot_download("jasongzy/Make-It-Animatable", allow_patterns=["output/best/new/**", "data/examples/**"], local_dir=MIA)

if "--kimodo" in sys.argv:
    print("Kimodo-SOMA-RP weights")
    snapshot_download("nvidia/Kimodo-SOMA-RP-v1.2")
    print("LLM2Vec Llama-3-8B text encoder (gated: needs Llama access on your Hugging Face account)")
    snapshot_download("McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp")
    snapshot_download("McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp-supervised")
    snapshot_download("meta-llama/Meta-Llama-3-8B-Instruct", allow_patterns=["*.json", "*.safetensors", "tokenizer*"])
print("done")
