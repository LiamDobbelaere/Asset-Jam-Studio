"""Drop-in replacement for torch_cluster.fps (farthest point sampling) in plain PyTorch.

Make-It-Animatable only uses `fps(pos, batch, ratio=...)`; the compiled torch_cluster wheels are not
available for every torch/CUDA pair on Windows, and 32k points x 512 samples is cheap on the GPU.
Returns indices into `pos`, grouped by batch like the original.
"""
import torch


@torch.no_grad()
def fps(pos, batch=None, ratio=0.5, random_start=True, batch_size=None, ptr=None):
    pos = pos if pos.dim() == 2 else pos.view(-1, pos.shape[-1])
    if batch is None:
        batch = torch.zeros(pos.shape[0], dtype=torch.long, device=pos.device)
    out = []
    for b in torch.unique(batch, sorted=True):
        idx = torch.nonzero(batch == b, as_tuple=False).squeeze(1)
        p = pos[idx]
        n = p.shape[0]
        k = max(1, int(round(n * ratio))) if ratio <= 1 else int(ratio)
        k = min(k, n)
        start = torch.randint(0, n, (1,), device=p.device).item() if random_start else 0
        chosen = torch.empty(k, dtype=torch.long, device=p.device)
        dist = torch.full((n,), float("inf"), device=p.device, dtype=p.dtype)
        last = start
        for i in range(k):
            chosen[i] = last
            d = ((p - p[last]) ** 2).sum(-1)
            dist = torch.minimum(dist, d)
            last = int(torch.argmax(dist))
        out.append(idx[chosen])
    return torch.cat(out)
