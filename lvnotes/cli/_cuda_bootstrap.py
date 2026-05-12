"""Preload NVIDIA CUDA libs from pip-installed wheels before CTranslate2 dlopens them."""

import ctypes
import importlib.util
from pathlib import Path

_CUDA_LIBS = (
    "cublas/lib/libcublasLt.so.12",
    "cublas/lib/libcublas.so.12",
    "cudnn/lib/libcudnn.so.9",
    "cudnn/lib/libcudnn_ops.so.9",
    "cudnn/lib/libcudnn_cnn.so.9",
    "cudnn/lib/libcudnn_graph.so.9",
)
_preload_attempted = False


def preload_cuda_libs() -> None:
    """Best-effort preload of CUDA shared libs bundled by nvidia-* wheels."""
    global _preload_attempted
    if _preload_attempted:
        return
    _preload_attempted = True

    roots = _nvidia_roots()
    if not roots:
        return

    for root in roots:
        for rel_path in _CUDA_LIBS:
            lib_path = root / rel_path
            if not lib_path.exists():
                continue
            try:
                ctypes.CDLL(str(lib_path), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                continue


def _nvidia_roots() -> tuple[Path, ...]:
    spec = importlib.util.find_spec("nvidia")
    if spec is None:
        return ()
    if spec.submodule_search_locations:
        return tuple(Path(location) for location in spec.submodule_search_locations)
    if spec.origin is None:
        return ()
    return (Path(spec.origin).parent,)
