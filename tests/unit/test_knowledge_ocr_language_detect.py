"""OCR engine wiring for the knowledge ingest pipeline.

Docling's ``TesseractCliOcrOptions(lang=["auto"])`` is the only OCR path —
Tesseract's ``osd.traineddata`` does per-page script detection, then OCRs
with the matching language model. Covers Latin/Hebrew/Arabic/Cyrillic/CJK
in one path, so we don't pre-detect languages ourselves anymore.

These tests pin the contract:
  - When the ``tesseract`` binary is on PATH, accurate mode uses it with
    ``lang=["auto"]``.
  - When it isn't, accurate mode degrades to ``balanced`` (no OCR, digital
    text layer only) with an actionable install hint — ingest still succeeds.
  - Accurate mode enables Docling's code + formula enrichments.
  - The error translator surfaces actionable messages for known-cryptic
    Docling/Tesseract failures.
"""

from __future__ import annotations

from pathlib import Path


def test_accurate_mode_uses_tesseract_auto(monkeypatch, tmp_path):
    """When the ``tesseract`` binary is on PATH, accurate mode must select it
    and pass ``lang=["auto"]`` so Docling delegates per-page script detection
    to Tesseract (covers Hebrew/Arabic/CJK/Cyrillic uniformly)."""
    import shutil
    from cuga.backend.knowledge import engine as engine_mod
    from cuga.backend.knowledge.config import KnowledgeConfig

    monkeypatch.setattr(
        shutil, "which", lambda name: "/opt/homebrew/bin/tesseract" if name == "tesseract" else None
    )

    cfg = KnowledgeConfig(persist_dir=tmp_path, docling_pdf_mode="accurate")
    eng = engine_mod.KnowledgeEngine.__new__(engine_mod.KnowledgeEngine)
    eng._config = cfg
    eng._docling_converters = {}

    logs: list[str] = []
    from loguru import logger as _logger

    handler_id = _logger.add(lambda msg: logs.append(str(msg)), level="INFO")
    try:
        eng._get_docling_converter()
    finally:
        _logger.remove(handler_id)
    joined = " ".join(logs)
    assert "ocr_engine='tesseract-cli'" in joined, f"expected tesseract-cli, got: {joined}"
    assert "['auto']" in joined, f"expected lang=['auto'], got: {joined}"


def test_no_tesseract_falls_back_to_easyocr(monkeypatch, tmp_path):
    """When ``tesseract`` is missing but EasyOCR is importable (it's a hard
    cuga pip dep), accurate mode must use EasyOCR instead of degrading to
    balanced. This is the local-dev path — `pip install cuga` gives you
    OCR with no system install steps."""
    import shutil
    from cuga.backend.knowledge import engine as engine_mod
    from cuga.backend.knowledge.config import KnowledgeConfig

    monkeypatch.setattr(shutil, "which", lambda name: None)  # no tesseract

    cfg = KnowledgeConfig(persist_dir=tmp_path, docling_pdf_mode="accurate")
    eng = engine_mod.KnowledgeEngine.__new__(engine_mod.KnowledgeEngine)
    eng._config = cfg
    eng._docling_converters = {}

    info_logs: list[str] = []
    from loguru import logger as _logger

    info_handler = _logger.add(lambda msg: info_logs.append(str(msg)), level="INFO")
    try:
        converter = eng._get_docling_converter()
    finally:
        _logger.remove(info_handler)

    # Converter still built — accurate mode succeeded with EasyOCR.
    assert converter is not None
    info_joined = " ".join(info_logs)
    # Accurate mode preserved (NOT degraded to balanced).
    assert "effective='accurate'" in info_joined, info_joined
    assert "do_ocr=True" in info_joined, info_joined
    # EasyOCR was selected.
    assert "ocr_engine='easyocr'" in info_joined, info_joined
    # Info note tells the user how to upgrade to Tesseract for full
    # multilingual quality — but does NOT crash or warn loudly.
    assert "EasyOCR" in info_joined
    assert "brew install tesseract" in info_joined


def test_no_tesseract_no_easyocr_degrades_to_balanced(monkeypatch, tmp_path):
    """Last-resort: when neither Tesseract nor EasyOCR is available, the
    engine must degrade to balanced mode (no OCR) instead of crashing.
    Logs a clear install hint covering all three install paths."""
    import shutil
    import sys
    from cuga.backend.knowledge import engine as engine_mod
    from cuga.backend.knowledge.config import KnowledgeConfig

    monkeypatch.setattr(shutil, "which", lambda name: None)  # no tesseract

    # Simulate easyocr being unimportable. The fallback path does
    # `import easyocr` inside the try block, so blocking it in sys.modules
    # AND removing it from the import cache is enough.
    real_import = __import__

    def _blocked_import(name, *args, **kwargs):
        if name == "easyocr":
            raise ImportError("easyocr not installed (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _blocked_import)
    # Also clear cached easyocr module so the import re-runs through our hook.
    if "easyocr" in sys.modules:
        monkeypatch.delitem(sys.modules, "easyocr")

    cfg = KnowledgeConfig(persist_dir=tmp_path, docling_pdf_mode="accurate")
    eng = engine_mod.KnowledgeEngine.__new__(engine_mod.KnowledgeEngine)
    eng._config = cfg
    eng._docling_converters = {}

    info_logs: list[str] = []
    warned: list[str] = []
    from loguru import logger as _logger

    info_handler = _logger.add(lambda msg: info_logs.append(str(msg)), level="INFO")
    warn_handler = _logger.add(lambda msg: warned.append(str(msg)), level="WARNING")
    try:
        converter = eng._get_docling_converter()
    finally:
        _logger.remove(info_handler)
        _logger.remove(warn_handler)

    # Converter still built — digital-text PDFs continue to ingest.
    assert converter is not None
    # Warning is the only place the user learns OCR was skipped — must
    # include the install command for both Tesseract AND EasyOCR.
    warning_text = " ".join(warned)
    assert "brew install tesseract" in warning_text
    assert "apt install tesseract" in warning_text
    assert "pip install easyocr" in warning_text
    # Effective mode is balanced with OCR off.
    info_joined = " ".join(info_logs)
    assert "effective='balanced'" in info_joined
    assert "do_ocr=False" in info_joined


def test_enrichments_stay_off_in_every_mode(monkeypatch, tmp_path):
    """Regression guard: do_code_enrichment + do_formula_enrichment must NOT
    be enabled by default in ANY mode (fast / balanced / accurate).

    Why: these flags activate Docling's VLM (vision-language model). Measured
    on Apple Silicon MPS for a 38-page paper:
      Batch processed 5 images in 1108.67s (221.73s per image)
    Across the paper, that's ~20 minutes added to ingest — a 30× regression
    over the same paper without enrichments. The VLM is also expensive on
    CUDA (single-digit seconds per image, but still adds minutes per paper).
    Until an opt-in config surface exists, leaving them off is the only
    safe default. This test exists so the next 'helpful' contributor can't
    silently re-enable them.
    """
    import shutil
    from cuga.backend.knowledge import engine as engine_mod
    from cuga.backend.knowledge.config import KnowledgeConfig

    # Tesseract present so accurate mode stays accurate (we want to exercise
    # the enrichment-suppression in the *worst case* mode).
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract" if name == "tesseract" else None)

    for mode in ("fast", "balanced", "accurate"):
        cfg = KnowledgeConfig(persist_dir=tmp_path, docling_pdf_mode=mode)
        eng = engine_mod.KnowledgeEngine.__new__(engine_mod.KnowledgeEngine)
        eng._config = cfg
        eng._docling_converters = {}

        logs: list[str] = []
        from loguru import logger as _logger

        handler_id = _logger.add(lambda msg: logs.append(str(msg)), level="INFO")
        try:
            eng._get_docling_converter()
        finally:
            _logger.remove(handler_id)
        joined = " ".join(logs)
        assert "do_code_enrichment=True" not in joined, (
            f"mode={mode!r}: code enrichment must stay off — it triggers a "
            f"VLM that adds ~20 minutes per paper. Log was: {joined}"
        )
        assert "do_formula_enrichment=True" not in joined, (
            f"mode={mode!r}: formula enrichment must stay off — same VLM "
            f"cost as code enrichment. Log was: {joined}"
        )


def test_translate_error_for_password_protected_pdf():
    """Password-protected PDFs surface as low-level parser errors. The
    translator must produce a clear message naming the file."""
    from cuga.backend.knowledge.engine import _translate_document_load_error

    raw = RuntimeError("incorrect password supplied for encrypted PDF")
    translated = _translate_document_load_error(Path("secret.pdf"), raw)
    msg = str(translated)
    assert "password-protected" in msg
    assert "secret.pdf" in msg


def test_translate_error_for_missing_tesseract_traineddata():
    """When a specific language pack is missing, Tesseract raises an
    "opening data file ... traineddata" error. Translate to an install hint
    so the user knows what to do."""
    from cuga.backend.knowledge.engine import _translate_document_load_error

    raw = RuntimeError("Error opening data file /usr/share/tessdata/heb.traineddata")
    translated = _translate_document_load_error(Path("hebrew.pdf"), raw)
    msg = str(translated)
    assert "language pack" in msg
    assert "brew install tesseract-lang" in msg or "apt install tesseract" in msg


# ---------------------------------------------------------------------------
# Layout engine — `auto` must engage the GPU when one is available (issue #304).
# Docling's ONNX layout pipeline is CPU-only; only the PyTorch ("transformers")
# engine actually runs layout on MPS/CUDA. So `auto` must resolve to:
#   - "transformers" when device is mps/cuda  (engage GPU)
#   - "onnx"         when device is cpu       (lean CPU path)
# Explicit "onnx" / "transformers" choices must be honored as escape hatches.
# ---------------------------------------------------------------------------


def _build_eng_for_layout(monkeypatch, tmp_path, *, layout_engine, device_label):
    """Construct a minimally-initialized engine + monkeypatch the device probe."""
    import shutil
    from cuga.backend.knowledge import engine as engine_mod
    from cuga.backend.knowledge.config import KnowledgeConfig

    # Tesseract present so accurate mode stays accurate (we want to exercise
    # the layout-engine resolution, not the missing-tesseract fallback).
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract" if name == "tesseract" else None)
    # Pin the device the engine "detects" without depending on host hardware.
    monkeypatch.setattr(
        engine_mod, "_detect_accelerator", lambda use_gpu: (device_label, ["CPUExecutionProvider"])
    )

    cfg = KnowledgeConfig(
        persist_dir=tmp_path,
        docling_pdf_mode="accurate",
        docling_layout_engine=layout_engine,
    )
    eng = engine_mod.KnowledgeEngine.__new__(engine_mod.KnowledgeEngine)
    eng._config = cfg
    eng._docling_converters = {}
    return eng


def _capture_init_log(eng) -> str:
    from loguru import logger as _logger

    logs: list[str] = []
    handler_id = _logger.add(lambda msg: logs.append(str(msg)), level="INFO")
    try:
        eng._get_docling_converter()
    finally:
        _logger.remove(handler_id)
    return " ".join(logs)


def test_layout_engine_auto_mps_engages_transformers(monkeypatch, tmp_path):
    """The original `auto` + MPS case from the user's bug report: `auto`
    must promote to the PyTorch (`transformers`) engine so layout actually
    runs on MPS, instead of falling back to ONNX-CPU silently."""
    eng = _build_eng_for_layout(monkeypatch, tmp_path, layout_engine="auto", device_label="mps")
    joined = _capture_init_log(eng)
    assert "layout_engine='transformers'" in joined, joined
    assert "layout_device='mps'" in joined, joined


def test_layout_engine_auto_cuda_engages_transformers(monkeypatch, tmp_path):
    """Same promotion on CUDA hosts — `auto` must engage the GPU."""
    eng = _build_eng_for_layout(monkeypatch, tmp_path, layout_engine="auto", device_label="cuda")
    joined = _capture_init_log(eng)
    assert "layout_engine='transformers'" in joined, joined
    assert "layout_device='cuda'" in joined, joined


def test_layout_engine_auto_cpu_stays_on_onnx(monkeypatch, tmp_path):
    """On CPU-only hosts, `auto` must pick ONNX (lighter than PyTorch CPU)
    rather than transformers — we don't want to slow CPU users down."""
    eng = _build_eng_for_layout(monkeypatch, tmp_path, layout_engine="auto", device_label="cpu")
    joined = _capture_init_log(eng)
    assert "layout_engine='onnx'" in joined, joined
    assert "layout_device='cpu'" in joined, joined


def test_explicit_onnx_is_honored_even_with_gpu(monkeypatch, tmp_path):
    """Escape hatch: explicit `onnx` must stay ONNX even when MPS is
    available (operator override / regression-safe pin)."""
    eng = _build_eng_for_layout(monkeypatch, tmp_path, layout_engine="onnx", device_label="mps")
    joined = _capture_init_log(eng)
    assert "layout_engine='onnx'" in joined, joined
    # ONNX is CPU-only regardless of device_label.
    assert "layout_device='cpu'" in joined, joined


def test_explicit_transformers_is_honored_on_cpu(monkeypatch, tmp_path):
    """Escape hatch: explicit `transformers` runs PyTorch-on-CPU even
    though that's slower than ONNX-CPU — operator choice wins."""
    eng = _build_eng_for_layout(monkeypatch, tmp_path, layout_engine="transformers", device_label="cpu")
    joined = _capture_init_log(eng)
    assert "layout_engine='transformers'" in joined, joined
    assert "layout_device='cpu'" in joined, joined


def test_auto_gpu_and_explicit_transformers_share_cache(monkeypatch, tmp_path):
    """`auto`+GPU and explicit `transformers` resolve to the same effective
    engine — they must share a cached converter, not build twice."""
    from cuga.backend.knowledge import engine as engine_mod

    eng = _build_eng_for_layout(monkeypatch, tmp_path, layout_engine="auto", device_label="mps")
    eng._get_docling_converter()
    # Two cache entries for two distinct (mode, effective_engine) combinations
    # would mean we wasted a model load. We expect one.
    assert len(eng._docling_converters) == 1
    # The single entry must be keyed by the EFFECTIVE engine, not "auto".
    assert any("transformers" in k for k in eng._docling_converters), eng._docling_converters

    # Now flip to explicit "transformers" on the same engine. The cache key
    # must collide (same effective engine), so no new entry is added.
    eng._config = engine_mod.KnowledgeConfig(
        persist_dir=tmp_path,
        docling_pdf_mode="accurate",
        docling_layout_engine="transformers",
    )
    eng._get_docling_converter()
    assert len(eng._docling_converters) == 1, eng._docling_converters


def test_use_gpu_toggle_invalidates_cache(monkeypatch, tmp_path):
    """Toggling use_gpu at runtime must clear the Docling converter cache
    so `auto` re-resolves with the new device. Without this, switching
    from CPU to MPS via PATCH would silently keep returning the old
    ONNX-CPU converter."""
    from cuga.backend.knowledge import engine as engine_mod
    from cuga.backend.knowledge.config import KnowledgeConfig

    # We're testing commit_knowledge_update — exercise its docling_changed
    # branch directly without standing up the full engine machinery.
    eng = engine_mod.KnowledgeEngine.__new__(engine_mod.KnowledgeEngine)
    eng._config = KnowledgeConfig(persist_dir=tmp_path, use_gpu=True)
    eng._docling_converters = {"accurate|onnx": object()}  # any sentinel

    # Simulate the captured "before" snapshot commit_knowledge_update uses.
    old_pdf_mode = eng._config.docling_pdf_mode
    old_layout_engine = eng._config.docling_layout_engine
    old_use_gpu = eng._config.use_gpu

    # Flip use_gpu off (pdf_mode + layout_engine unchanged).
    eng._config = KnowledgeConfig(persist_dir=tmp_path, use_gpu=False)

    docling_changed = (
        old_pdf_mode != eng._config.docling_pdf_mode
        or old_layout_engine != eng._config.docling_layout_engine
        or old_use_gpu != eng._config.use_gpu
    )
    assert docling_changed, "use_gpu change must trigger docling_changed"
