import ctypes
import sys

from lvnotes.cli import _cuda_bootstrap


def test_preload_cuda_libs_loads_existing_nvidia_wheel_libs(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "nvidia"
    cublas_dir = root / "cublas" / "lib"
    cublas_dir.mkdir(parents=True)
    cublas_lt = cublas_dir / "libcublasLt.so.12"
    cublas = cublas_dir / "libcublas.so.12"
    cublas_lt.touch()
    cublas.touch()
    loaded: list[tuple[str, int]] = []

    def fake_cdll(path: str, mode: int) -> object:
        loaded.append((path, mode))
        return object()

    monkeypatch.setattr(_cuda_bootstrap, "_preload_attempted", False)
    monkeypatch.setattr(_cuda_bootstrap, "_nvidia_roots", lambda: (root,))
    monkeypatch.setattr(_cuda_bootstrap.ctypes, "CDLL", fake_cdll)

    _cuda_bootstrap.preload_cuda_libs()

    assert loaded == [
        (str(cublas_lt), ctypes.RTLD_GLOBAL),
        (str(cublas), ctypes.RTLD_GLOBAL),
    ]


def test_preload_cuda_libs_is_best_effort_and_idempotent(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "nvidia"
    lib_dir = root / "cublas" / "lib"
    lib_dir.mkdir(parents=True)
    (lib_dir / "libcublasLt.so.12").touch()
    calls = 0

    def fake_cdll(path: str, mode: int) -> object:
        nonlocal calls
        calls += 1
        raise OSError("missing dependency")

    monkeypatch.setattr(_cuda_bootstrap, "_preload_attempted", False)
    monkeypatch.setattr(_cuda_bootstrap, "_nvidia_roots", lambda: (root,))
    monkeypatch.setattr(_cuda_bootstrap.ctypes, "CDLL", fake_cdll)

    _cuda_bootstrap.preload_cuda_libs()
    _cuda_bootstrap.preload_cuda_libs()

    assert calls == 1


def test_preload_cuda_libs_skips_cpu_only_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fail_cdll(path: str, mode: int) -> object:
        raise AssertionError("should not load")

    monkeypatch.setattr(_cuda_bootstrap, "_preload_attempted", False)
    monkeypatch.setattr(_cuda_bootstrap, "_nvidia_roots", lambda: ())
    monkeypatch.setattr(_cuda_bootstrap.ctypes, "CDLL", fail_cdll)

    _cuda_bootstrap.preload_cuda_libs()


def test_nvidia_roots_supports_namespace_package(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    nvidia_dir = tmp_path / "nvidia"
    nvidia_dir.mkdir()
    monkeypatch.delitem(sys.modules, "nvidia", raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))

    assert nvidia_dir in _cuda_bootstrap._nvidia_roots()


def test_preload_cuda_libs_checks_all_nvidia_namespace_roots(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    first_root = tmp_path / "first" / "nvidia"
    second_root = tmp_path / "second" / "nvidia"
    first_cublas_dir = first_root / "cublas" / "lib"
    second_cudnn_dir = second_root / "cudnn" / "lib"
    first_cublas_dir.mkdir(parents=True)
    second_cudnn_dir.mkdir(parents=True)
    cublas = first_cublas_dir / "libcublas.so.12"
    cudnn = second_cudnn_dir / "libcudnn.so.9"
    cublas.touch()
    cudnn.touch()
    loaded: list[str] = []

    def fake_cdll(path: str, mode: int) -> object:
        loaded.append(path)
        return object()

    monkeypatch.setattr(_cuda_bootstrap, "_preload_attempted", False)
    monkeypatch.setattr(_cuda_bootstrap, "_nvidia_roots", lambda: (first_root, second_root))
    monkeypatch.setattr(_cuda_bootstrap.ctypes, "CDLL", fake_cdll)

    _cuda_bootstrap.preload_cuda_libs()

    assert loaded == [str(cublas), str(cudnn)]
